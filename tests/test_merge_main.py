"""Local regression checks for main features retained in the database merge."""

import importlib
import os
import unittest
from unittest.mock import ANY, call, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from werkzeug.security import check_password_hash

from auth_fixtures import AuthDatabaseFixture


class MainMergeTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"SECRET_KEY": "unit-test-only-session-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        dotenv = patch("dotenv.load_dotenv", return_value=False)
        dotenv.start()
        self.addCleanup(dotenv.stop)
        connector = patch("db.mysql.connector.connect")
        self.connect = connector.start()
        self.addCleanup(connector.stop)
        self.module = importlib.import_module("app")
        config = patch.dict(self.module.app.config, TESTING=True, SECRET_KEY="unit-test-only-session-key")
        config.start()
        self.addCleanup(config.stop)
        self.client = self.module.app.test_client()
        self.auth = importlib.import_module("services.auth_service")
        self.database = AuthDatabaseFixture()
        provider = patch("services.auth_service.get_db", return_value=self.database.connection)
        provider.start()
        self.addCleanup(provider.stop)

    def tearDown(self):
        self.connect.assert_not_called()

    def test_tracker_requires_login_and_retains_user_filtering(self):
        response = self.client.get("/tracker")
        self.assertEqual(response.status_code, 302)
        destination = urlsplit(response.location)
        self.assertEqual(destination.path, "/login")
        self.assertEqual(parse_qs(destination.query), {"next": ["/tracker"]})
        self.assertEqual(destination.query, urlencode({"next": "/tracker"}))

        # Use the real current login flow and distinct synthetic identities.
        # Tracker now reads MySQL rather than sample data, so mock the data
        # boundary while retaining the actual owner-filtered SQL and rendering.
        first_form = {
            "name": "First Synthetic User", "email": "first@example.invalid",
            "password": "synthetic-test-password",
        }
        second_form = first_form | {"name": "Second Synthetic User", "email": "second@example.invalid"}
        for form in (first_form, second_form):
            self.auth.create_user(form)
        first = self.auth.get_user_by_email(first_form["email"])
        second = self.auth.get_user_by_email(second_form["email"])
        self.assertNotEqual(first.id, second.id)
        tracker_data = importlib.import_module("tracker_data")
        row = {
            "application_id": 501, "company": "Qualcomm", "job_title": "Synthetic role",
            "status": "Applied", "applied_at": None, "closing_date": None,
        }
        from services import interview_service
        with patch("tracker_data.get_db") as database, patch("services.interview_service.get_db", return_value=database.return_value):
            database.return_value.in_transaction = False
            cursor = database.return_value.cursor.return_value

            def execute(query, parameters):
                self.assertIn("WHERE a.user_id = %s", query)
                self.assertIn(parameters[0], [first.id, second.id])
                self.assertIn(len(parameters), [1, 4])
                self.assertIn(query, [tracker_data.APPLICATIONS_QUERY, interview_service.LIST_QUERY])
                cursor.fetchall.return_value = (
                    [row] if query == tracker_data.APPLICATIONS_QUERY and parameters == (first.id,) else []
                )

            cursor.execute.side_effect = execute
            other_client = self.module.app.test_client()
            for client, form in [(self.client, first_form), (other_client, second_form)]:
                login = client.post("/login", data=form | {"user_id": "999999"})
                self.assertEqual(login.status_code, 302)
                self.assertEqual(urlsplit(login.location).path, "/tracker")
            own = self.client.get("/tracker")
            self.assertEqual(own.status_code, 200)
            self.assertIn(b"Qualcomm", own.data)
            other = other_client.get("/tracker")
            self.assertEqual(other.status_code, 200)
            self.assertNotIn(b"Qualcomm", other.data)
            self.assertEqual(cursor.execute.call_args_list, [
                call(tracker_data.APPLICATIONS_QUERY, (first.id,)),
                call(interview_service.LIST_QUERY, (first.id, ANY, ANY, ANY)),
                call(tracker_data.APPLICATIONS_QUERY, (second.id,)),
                call(interview_service.LIST_QUERY, (second.id, ANY, ANY, ANY)),
            ])

    def test_registration_and_login_pages_render(self):
        for route in ("/register", "/login"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)

    def test_registration_validation_and_persistent_storage(self):
        self.assertEqual(self.client.post("/register", data={}).status_code, 400)
        form = {
            "name": "Synthetic Merge User",
            "email": "merge-check@example.invalid",
            "password": "synthetic-test-password",
            "confirm_password": "synthetic-test-password",
        }
        response = self.client.post("/register", data=form)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/login"))
        user = self.auth.get_user_by_email(form["email"])
        self.assertIsNotNone(user)
        self.assertEqual(user.name, form["name"])
        self.assertEqual(user.email, form["email"])
        self.assertNotEqual(user.password_hash, form["password"])
        self.assertTrue(check_password_hash(user.password_hash, form["password"]))
        self.assertEqual(self.client.post("/register", data=form).status_code, 400)
        self.assertEqual(self.auth.get_user_by_email(form["email"]).user_id, user.user_id)
        self.assertEqual(len(self.database.rows), 1)

    def test_db_check_remains_registered(self):
        self.assertIn("db-check", self.module.app.cli.commands)


if __name__ == "__main__":
    unittest.main()
