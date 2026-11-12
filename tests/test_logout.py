"""Header identity/logout regressions: synthetic persisted rows, no hosting."""
from html.parser import HTMLParser
import importlib
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from flask_login.utils import encode_cookie

from auth_fixtures import AuthDatabaseFixture


class HeaderHTML(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.in_name = self.in_logout = self.in_button = False
        self.account_name = self.button_text = ""
        self.logout = self.button = None
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "span" and attributes.get("id") == "account-name":
            self.in_name = True
        if tag == "form" and attributes.get("id") == "logout-form":
            self.logout = attributes
            self.in_logout = True
        if self.in_logout and tag == "input":
            self.fields[attributes["name"]] = attributes
        if self.in_logout and tag == "button":
            self.button = attributes
            self.in_button = True

    def handle_data(self, data):
        if self.in_name:
            self.account_name += data
        if self.in_button:
            self.button_text += data

    def handle_endtag(self, tag):
        if tag == "span":
            self.in_name = False
        if tag == "form":
            self.in_logout = False
        if tag == "button":
            self.in_button = False


class LogoutTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"SECRET_KEY": "unit-test-only-session-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        dotenv = patch("dotenv.load_dotenv", return_value=False)
        dotenv.start()
        self.addCleanup(dotenv.stop)
        network = patch("mysql.connector.connect", side_effect=AssertionError("No hosting in logout tests"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.module = importlib.import_module("app")
        config = patch.dict(self.module.app.config, TESTING=True, SECRET_KEY="unit-test-only-session-key",
                            REMEMBER_COOKIE_NAME="synthetic_remember")
        config.start()
        self.addCleanup(config.stop)
        self.client = self.module.app.test_client()
        self.database = AuthDatabaseFixture()
        provider = patch("services.auth_service.operation_session", side_effect=self.database.auth_operation)
        provider.start()
        self.addCleanup(provider.stop)
        self.forms = [
            {"name": 'Alina <script>alert("synthetic")</script>', "email": "alina@example.invalid",
             "password": "synthetic-test-password", "confirm_password": "synthetic-test-password"},
            {"name": "Second Synthetic User", "email": "second@example.invalid",
             "password": "synthetic-test-password", "confirm_password": "synthetic-test-password"},
        ]
        for form in self.forms:
            self.assertEqual(self.client.post("/register", data=form).status_code, 302)
        self.identities = [self.module.get_user_by_id(identity).user_id for identity in sorted(self.database.rows)]
        self.assertEqual(self.identities, [101, 102])
        tracker = patch("tracker_data.get_db")
        self.tracker = tracker.start()
        self.addCleanup(tracker.stop)
        self.tracker.return_value.in_transaction = False
        interviews = patch("services.interview_service.get_db", return_value=self.tracker.return_value)
        interviews.start()
        self.addCleanup(interviews.stop)
        self.owners = []
        cursor = self.tracker.return_value.cursor.return_value

        def execute(query, parameters):
            self.assertIn("WHERE a.user_id = %s", query)
            self.assertIn(parameters[0], [101, 102])
            self.assertIn(len(parameters), [1, 4])
            self.owners.append(parameters[0])
            cursor.fetchall.return_value = []

        cursor.execute.side_effect = execute

    def tearDown(self):
        self.network.assert_not_called()

    def login(self, index=0, client=None):
        client = self.client if client is None else client
        response = client.post("/login", data=self.forms[index] | {"user_id": "999999"})
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            self.assertEqual(session["_user_id"], str(self.identities[index]))
        return client

    def header(self, client=None):
        client = self.client if client is None else client
        response = client.get("/tracker")
        self.assertEqual(response.status_code, 200)
        return response, HeaderHTML(response.get_data(as_text=True))

    def assert_login_required(self):
        response = self.client.get("/tracker")
        self.assertEqual(response.status_code, 302)
        destination = urlsplit(response.location)
        self.assertEqual(destination.path, "/login")
        self.assertEqual(parse_qs(destination.query), {"next": ["/tracker"]})

    def test_each_login_displays_its_persisted_name_and_ownership_id(self):
        for index in (0, 1):
            client = self.module.app.test_client()
            self.login(index, client)
            self.owners.clear()
            response, header = self.header(client)
            self.assertEqual(self.owners, [self.identities[index]] * 2)
            self.assertEqual(header.account_name.strip(), self.forms[index]["name"])
            if index == 0:
                self.assertNotIn(b'<script>alert("synthetic")</script>', response.data)
                self.assertIn(b"&lt;script&gt;", response.data)

    def test_logout_markup_is_visible_post_form_with_csrf_and_native_button(self):
        self.login()
        _, header = self.header()
        self.assertEqual(header.logout["action"], "/logout")
        self.assertEqual(header.logout["method"], "post")
        self.assertEqual(header.button["type"], "submit")
        self.assertEqual(header.button_text.strip(), "Logout")
        self.assertNotIn("hidden", header.logout)
        self.assertNotIn("disabled", header.button)
        self.assertNotEqual(header.button.get("tabindex"), "-1")
        self.assertTrue(header.fields["csrf_token"]["value"])

    def test_valid_logout_clears_framework_and_legacy_auth_then_requires_login(self):
        self.login()
        _, header = self.header()
        with self.client.session_transaction() as session:
            session["user_id"] = 102
            session["_remember"] = "set"
            session["_remember_seconds"] = 3600
        response = self.client.post("/logout", data={"csrf_token": header.fields["csrf_token"]["value"]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.location).path, "/login")
        with self.client.session_transaction() as session:
            for key in ("_user_id", "_fresh", "_id", "user_id", "_remember", "_remember_seconds", "_csrf_token"):
                self.assertNotIn(key, session)
        self.assert_login_required()

    def test_logout_then_other_login_does_not_keep_previous_identity_or_token(self):
        self.login(0)
        _, first = self.header()
        token = first.fields["csrf_token"]["value"]
        self.assertEqual(self.client.post("/logout", data={"csrf_token": token}).status_code, 302)
        self.login(1)
        self.owners.clear()
        _, second = self.header()
        self.assertEqual(second.account_name.strip(), self.forms[1]["name"])
        self.assertEqual(self.owners, [102, 102])
        self.assertNotEqual(second.fields["csrf_token"]["value"], token)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": token}).status_code, 400)
        self.assertEqual(self.header()[1].account_name.strip(), self.forms[1]["name"])

    def test_missing_invalid_and_non_ascii_csrf_do_not_log_out(self):
        self.login()
        self.header()
        for data in ({}, {"csrf_token": "invalid"}, {"csrf_token": "\u00e9"}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post("/logout", data=data).status_code, 400)
                self.assertEqual(self.header()[1].account_name.strip(), self.forms[0]["name"])

    def test_another_sessions_csrf_is_rejected(self):
        self.login(0)
        other = self.module.app.test_client()
        self.login(1, other)
        _, first = self.header()
        self.header(other)
        response = other.post("/logout", data={"csrf_token": first.fields["csrf_token"]["value"]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.header(other)[1].account_name.strip(), self.forms[1]["name"])

    def test_get_logout_does_not_change_authentication(self):
        self.login()
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.header()[1].account_name.strip(), self.forms[0]["name"])

    def test_logout_removes_a_real_library_signed_synthetic_remember_cookie(self):
        cookie = encode_cookie("101", key="unit-test-only-session-key")
        self.client.set_cookie("synthetic_remember", cookie)
        _, header = self.header()  # Flask-Login restores this synthetic persisted user.
        self.assertEqual(header.account_name.strip(), self.forms[0]["name"])
        self.assertEqual(self.client.post("/logout", data={"csrf_token": header.fields["csrf_token"]["value"]}).status_code, 302)
        self.assertIsNone(self.client.get_cookie("synthetic_remember"))
        self.assert_login_required()

    def test_legacy_posted_identity_does_not_change_header_or_query_owner(self):
        self.login()
        with self.client.session_transaction() as session:
            session["user_id"] = 102
        self.owners.clear()
        _, header = self.header()
        self.assertEqual(header.account_name.strip(), self.forms[0]["name"])
        self.assertEqual(self.owners, [101, 101])


if __name__ == "__main__":
    unittest.main()
