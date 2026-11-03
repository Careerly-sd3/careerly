"""Local regression checks for main features retained in the database merge."""

import importlib
import os
import unittest
from unittest.mock import patch

from werkzeug.security import check_password_hash


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
        users = patch.dict(self.auth._users, {}, clear=True)
        users.start()
        self.addCleanup(users.stop)

    def tearDown(self):
        self.connect.assert_not_called()

    def test_tracker_requires_login_and_retains_user_filtering(self):
        response = self.client.get("/tracker")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/login"))
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        own = self.client.get("/tracker")
        self.assertEqual(own.status_code, 200)
        self.assertIn(b"Qualcomm", own.data)
        with self.client.session_transaction() as session:
            session["user_id"] = 2
        other = self.client.get("/tracker")
        self.assertEqual(other.status_code, 200)
        self.assertNotIn(b"Qualcomm", other.data)

    def test_registration_and_login_pages_render(self):
        for route in ("/register", "/login"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)

    def test_registration_validation_and_in_memory_storage_preserved(self):
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
        user = self.auth._users[form["email"]]
        self.assertTrue(check_password_hash(user["password_hash"], form["password"]))
        self.assertEqual(self.client.post("/register", data=form).status_code, 400)

    def test_db_check_remains_registered(self):
        self.assertIn("db-check", self.module.app.cli.commands)


if __name__ == "__main__":
    unittest.main()
