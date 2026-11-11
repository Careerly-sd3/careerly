"""Synthetic Flask sessions only; no production app, files or database access."""
import unittest

from flask import Flask, render_template_string, session
from werkzeug.exceptions import BadRequest

from services import csrf


class CSRFTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="unit-test-only-csrf-key")
        csrf.init_app(self.app)

        @self.app.get("/synthetic-token")
        def token():
            return render_template_string("{{ csrf_token() }}")

        @self.app.route("/synthetic-action", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        @csrf.csrf_protect
        def action():
            return "synthetic success"

        self.client = self.app.test_client()

    def test_template_hook_is_session_bound_and_stable(self):
        first = self.client.get("/synthetic-token").get_data(as_text=True)
        self.assertEqual(first, self.client.get("/synthetic-token").get_data(as_text=True))
        other = self.app.test_client().get("/synthetic-token").get_data(as_text=True)
        self.assertNotEqual(first, other)
        self.assertTrue(first.isascii())
        self.assertGreaterEqual(len(first), 32)

    def test_mutations_require_matching_token_and_get_is_not_restricted(self):
        self.assertEqual(self.client.get("/synthetic-action").status_code, 200)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(self.client.open("/synthetic-action", method=method).status_code, 400)
                token = self.client.get("/synthetic-token").get_data(as_text=True)
                response = self.client.open("/synthetic-action", method=method, data={"csrf_token": token})
                self.assertEqual(response.status_code, 200)

    def test_failed_validation_does_not_create_a_token_or_accept_wrong_types(self):
        with self.app.test_request_context(method="POST"):
            for value in (None, "", "invalid", [], 123, "\u00e9"):
                with self.subTest(value=value), self.assertRaises(BadRequest):
                    csrf.validate_csrf_token(value)
            self.assertNotIn(csrf.SESSION_KEY, session)

    def test_clear_invalidates_old_token_and_issues_new_one(self):
        with self.app.test_request_context():
            old = csrf.csrf_token()
            csrf.validate_csrf_token(old)
            csrf.clear_csrf_token()
            with self.assertRaises(BadRequest):
                csrf.validate_csrf_token(old)
            new = csrf.csrf_token()
            self.assertNotEqual(new, old)
            csrf.validate_csrf_token(new)

    def test_corrupted_session_value_is_replaced(self):
        with self.client.session_transaction() as session:
            session[csrf.SESSION_KEY] = 123
        token = self.client.get("/synthetic-token").get_data(as_text=True)
        self.assertEqual(self.client.post("/synthetic-action", data={"csrf_token": token}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
