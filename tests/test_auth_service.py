"""Persistent-auth tests using synthetic rows and mocked SQL, never hosting."""
from datetime import datetime, timezone
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import mysql.connector
from werkzeug.security import check_password_hash, generate_password_hash

from db import DatabaseError
from services import auth_service as auth


class AuthServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "synthetic-test-password"
        cls.scrypt = generate_password_hash(cls.password)
        cls.pbkdf2 = generate_password_hash(cls.password, method="pbkdf2:sha256:1000")

    def setUp(self):
        provider = patch("services.auth_service.get_db")
        self.get_db = provider.start()
        self.addCleanup(provider.stop)
        network = patch("mysql.connector.connect", side_effect=AssertionError("No hosted access in tests"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.connection = self.get_db.return_value
        self.connection.autocommit = False
        self.connection.in_transaction = False
        self.cursor = self.connection.cursor.return_value
        self.cursor.lastrowid = 873
        self.row = {
            "user_id": 873, "name": "Synthetic User", "email": "synthetic@example.invalid",
            "password_hash": self.scrypt, "created_at": datetime(2026, 11, 9, 12),
        }
        self.cursor.fetchone.return_value = self.row
        self.form = {
            "name": " Synthetic User ", "email": " SYNTHETIC@example.invalid ",
            "password": self.password, "confirm_password": self.password, "user_id": "999999",
        }

    def tearDown(self):
        self.network.assert_not_called()

    def test_registration_uses_generated_id_utc_hash_and_explicit_commit(self):
        before = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        user = auth.create_user(self.form)
        after = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        self.assertEqual((user.user_id, user.id, user.get_id()), (873, 873, "873"))
        query, parameters = self.cursor.execute.call_args.args
        self.assertEqual(query, auth.CREATE_USER)
        self.assertNotIn("user_id", query)
        self.assertEqual(parameters[:2], ("Synthetic User", "synthetic@example.invalid"))
        self.assertNotIn(self.password, query)
        self.assertNotEqual(parameters[2], self.password)
        self.assertTrue(check_password_hash(parameters[2], self.password))
        self.assertTrue(before <= parameters[3] <= after)
        self.assertIsNone(parameters[3].tzinfo)
        self.assertEqual(user.created_at, parameters[3])
        self.connection.start_transaction.assert_called_once()
        self.connection.commit.assert_called_once()
        self.connection.rollback.assert_not_called()
        calls = [call[0] for call in self.connection.mock_calls]
        self.assertLess(calls.index("cursor().close"), calls.index("commit"))

    def test_concurrent_duplicate_after_precheck_is_1062_and_rolls_back(self):
        self.cursor.fetchone.return_value = None
        self.assertEqual(auth.validate_registration(self.form), {})
        self.cursor.execute.side_effect = mysql.connector.IntegrityError("private duplicate details", errno=1062)
        with self.assertRaises(auth.EmailAlreadyExists) as error:
            auth.create_user(self.form)
        self.assertNotIn("private", str(error.exception))
        self.connection.commit.assert_not_called()
        self.assertEqual(self.connection.rollback.call_count, 2)  # precheck read + failed write

    def test_unrelated_integrity_failure_is_not_duplicate_email(self):
        self.cursor.execute.side_effect = mysql.connector.IntegrityError("private write details", errno=1048)
        with self.assertRaises(DatabaseError) as error:
            auth.create_user(self.form)
        self.assertNotIn("private", str(error.exception))
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
        self.cursor.close.assert_called_once()

    def test_commit_failure_returns_no_success_and_attempts_rollback(self):
        self.connection.commit.side_effect = mysql.connector.Error("private commit details")
        with self.assertRaises(DatabaseError) as error:
            auth.create_user(self.form)
        self.assertIn("check the outcome", str(error.exception))
        self.connection.rollback.assert_called_once()
        self.assertNotIn("private", str(error.exception))

    def test_rollback_failure_is_sanitised(self):
        self.cursor.execute.side_effect = mysql.connector.Error("private write details")
        self.connection.rollback.side_effect = mysql.connector.Error("private rollback details")
        with self.assertRaises(DatabaseError) as error:
            auth.create_user(self.form)
        self.assertIn("rollback failed", str(error.exception))
        self.assertNotIn("private", str(error.exception))
        self.connection.commit.assert_not_called()

    def test_registration_never_commits_or_discards_existing_caller_work(self):
        self.connection.in_transaction = True
        with self.assertRaises(DatabaseError):
            auth.create_user(self.form)
        self.connection.start_transaction.assert_not_called()
        self.connection.cursor.assert_not_called()
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_not_called()

    def test_missing_generated_id_is_a_failed_registration(self):
        self.cursor.lastrowid = 0
        with self.assertRaises(DatabaseError):
            auth.create_user(self.form)
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_load_by_email_and_id_use_same_persisted_record(self):
        by_email = auth.get_user_by_email(" SYNTHETIC@example.invalid ")
        self.assertEqual(self.cursor.execute.call_args.args, (auth.USER_BY_EMAIL, ("synthetic@example.invalid",)))
        by_id = auth.get_user_by_id(by_email.get_id())
        self.assertEqual(self.cursor.execute.call_args.args, (auth.USER_BY_ID, (873,)))
        self.assertEqual(by_email.user_id, by_id.user_id)
        self.assertEqual(by_id.id, 873)
        self.assertEqual(self.connection.rollback.call_count, 2)
        self.connection.commit.assert_not_called()

    def test_invalid_session_ids_are_anonymous_without_querying(self):
        for identity in (None, True, False, 0, -1, 1.5, [], {}, "", "1 OR 1=1", "-1", "0", "1.0", "2147483648"):
            with self.subTest(identity=identity):
                self.assertIsNone(auth.get_user_by_id(identity))
        self.get_db.assert_not_called()

    def test_deleted_user_session_is_anonymous(self):
        self.cursor.fetchone.return_value = None
        self.assertIsNone(auth.get_user_by_id("873"))
        self.connection.rollback.assert_called_once()

    def test_read_preserves_an_existing_caller_transaction(self):
        self.connection.in_transaction = True
        self.assertEqual(auth.get_user_by_id(873).user_id, 873)
        self.connection.rollback.assert_not_called()
        self.connection.commit.assert_not_called()

    def test_user_loader_read_does_not_leave_transaction_blocking_interview_write(self):
        from services import interview_service
        state = {"active": False}
        self.connection.in_transaction = False

        def execute(*args):
            state["active"] = True
            self.connection.in_transaction = True

        def rollback():
            state["active"] = False
            self.connection.in_transaction = False

        self.cursor.execute.side_effect = execute
        self.connection.rollback.side_effect = rollback
        user = auth.get_user_by_id("873")
        self.assertFalse(state["active"])
        self.cursor.fetchone.return_value = {"interview_id": 41, "application_id": 11, "outcome": "Pending"}
        self.cursor.rowcount = 1
        with patch.object(interview_service, "get_db", return_value=self.connection):
            interview_service.delete_interview(user.user_id, 41)
        self.connection.start_transaction.assert_called_once()
        self.connection.commit.assert_called_once()

    def test_login_accepts_existing_scrypt_and_pbkdf2_without_resetting_hashes(self):
        for stored_hash in (self.scrypt, self.pbkdf2):
            with self.subTest(method=stored_hash.split(":", 1)[0]):
                self.row["password_hash"] = stored_hash
                user = auth.authenticate(self.form["email"], self.password)
                self.assertEqual(user.user_id, 873)
                self.assertEqual(user.password_hash, stored_hash)
                self.assertIsNone(auth.authenticate(self.form["email"], "synthetic-wrong-password"))
                self.assertTrue(all(call.args[0].startswith("SELECT") for call in self.cursor.execute.call_args_list))
        self.connection.commit.assert_not_called()

    def test_unknown_email_and_malformed_stored_hash_fail_closed(self):
        self.cursor.fetchone.return_value = None
        self.assertIsNone(auth.authenticate("absent@example.invalid", self.password))
        self.cursor.fetchone.return_value = self.row | {"password_hash": "unsupported-method$salt$hash"}
        self.assertIsNone(auth.authenticate("synthetic@example.invalid", self.password))

    def test_query_parameters_and_failures_never_expose_credentials(self):
        payload = "synthetic' OR 1=1 --@example.invalid"
        auth.get_user_by_email(payload)
        query, parameters = self.cursor.execute.call_args.args
        self.assertNotIn(payload, query)
        self.assertEqual(parameters, (auth.normalise_email(payload),))
        self.cursor.execute.side_effect = mysql.connector.Error("private password/hash details")
        with self.assertRaises(DatabaseError) as error:
            auth.get_user_by_email(payload)
        self.assertNotIn("private", str(error.exception))

    def test_canonical_identity_uses_persisted_user_id_not_alias_or_posted_id(self):
        user = SimpleNamespace(is_authenticated=True, user_id=873, id=999999)
        with patch.object(auth, "current_user", user):
            self.assertEqual(auth.current_user_id(), 873)
        for user in (SimpleNamespace(is_authenticated=False),
                     SimpleNamespace(is_authenticated=True, user_id="bad")):
            with patch.object(auth, "current_user", user):
                self.assertIsNone(auth.current_user_id())

    def test_registration_validation_keeps_incoming_length_and_password_rules(self):
        self.cursor.fetchone.return_value = None
        self.assertEqual(auth.validate_registration(self.form), {})
        for overrides, field in [({"name": "x" * 101}, "name"),
                                 ({"email": "x" * 256 + "@example.invalid"}, "email"),
                                 ({"password": "short"}, "password"),
                                 ({"confirm_password": "mismatch"}, "confirm_password")]:
            self.assertIn(field, auth.validate_registration(self.form | overrides))

    def test_app_loader_and_duplicate_race_response_are_safe(self):
        with patch("dotenv.load_dotenv", return_value=False), patch.dict(
            "os.environ", {"SECRET_KEY": "unit-test-only-session-key"}, clear=True
        ):
            module = importlib.import_module("app")
        with patch.dict(module.app.config, TESTING=True, SECRET_KEY="unit-test-only-session-key"):
            with module.app.test_request_context():
                self.assertEqual(module.load_user("873").user_id, 873)
                self.assertIsNone(module.load_user("invalid"))
            self.cursor.fetchone.return_value = None
            self.cursor.execute.side_effect = [None, mysql.connector.IntegrityError("private details", errno=1062)]
            response = module.app.test_client().post("/register", data=self.form)
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"An account with this email already exists.", response.data)
            self.assertNotIn(self.password.encode(), response.data)

    def test_diagnostic_import_is_lazy_and_has_no_second_connection(self):
        with patch("dotenv.load_dotenv") as load:
            module = importlib.import_module("db_check")
        load.assert_not_called()
        self.network.assert_not_called()
        self.assertIs(module.get_db, importlib.import_module("db").get_db)

    def test_login_routes_keep_errors_safe_redirects_and_database_identity(self):
        with patch("dotenv.load_dotenv", return_value=False), patch.dict(
            "os.environ", {"SECRET_KEY": "unit-test-only-session-key"}, clear=True
        ):
            module = importlib.import_module("app")
        with patch.dict(module.app.config, TESTING=True, SECRET_KEY="unit-test-only-session-key"):
            self.assertEqual(module.app.test_client().post("/login", data={}).status_code, 400)
            invalid = module.app.test_client().post("/login", data=self.form | {"password": "synthetic-wrong"})
            self.assertEqual(invalid.status_code, 401)
            self.assertIn(b"Email or password is incorrect.", invalid.data)
            for target, expected in [("/tracker?status=Saved", "/tracker?status=Saved"),
                                     ("https://example.invalid/", "/tracker"), ("//example.invalid/", "/tracker")]:
                client = module.app.test_client()
                response = client.post("/login", query_string={"next": target}, data=self.form)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, expected)
                with client.session_transaction() as session:
                    self.assertEqual(session["_user_id"], "873")
            self.cursor.fetchone.return_value = None
            absent = module.app.test_client().post("/login", data=self.form)
            self.assertEqual(absent.status_code, 401)
            self.assertIn(b"Email or password is incorrect.", absent.data)

    def test_bad_session_is_anonymous_and_database_errors_return_503(self):
        with patch("dotenv.load_dotenv", return_value=False), patch.dict(
            "os.environ", {"SECRET_KEY": "unit-test-only-session-key"}, clear=True
        ):
            module = importlib.import_module("app")
        with patch.dict(module.app.config, TESTING=True, SECRET_KEY="unit-test-only-session-key"):
            client = module.app.test_client()
            with client.session_transaction() as session:
                session["_user_id"] = "not-a-database-id"
            response = client.get("/tracker")
            self.assertEqual(response.status_code, 302)
            destination = urlsplit(response.location)
            self.assertEqual(destination.path, "/login")
            self.assertEqual(parse_qs(destination.query), {"next": ["/tracker"]})
            self.get_db.assert_not_called()
            self.cursor.execute.side_effect = mysql.connector.Error("private credential details")
            response = module.app.test_client().post("/login", data=self.form)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b"private", response.data)

    def test_read_transaction_rollback_failure_cannot_return_a_user(self):
        self.connection.rollback.side_effect = mysql.connector.Error("private read rollback")
        with self.assertRaises(DatabaseError) as error:
            auth.get_user_by_id(873)
        self.assertIn("read transaction", str(error.exception))
        self.assertNotIn("private", str(error.exception))
        self.connection.commit.assert_not_called()

    def test_schema_diagnostic_is_read_only_and_parameterised(self):
        diagnostic = importlib.import_module("db_check")
        self.cursor.fetchone.side_effect = [{"selected_database": "careerly"}, {"Value": "synthetic-cipher"}]
        self.cursor.fetchall.side_effect = [
            [{"TABLE_NAME": "User"}], [{"COLUMN_NAME": "user_id"}],
            [{"CONSTRAINT_NAME": "PRIMARY"}], [{"INDEX_NAME": "PRIMARY"}],
        ]
        output = []
        diagnostic.inspect_schema(self.connection, output.append)
        for operation in self.cursor.execute.call_args_list:
            self.assertTrue(operation.args[0].startswith(("SELECT", "SHOW")))
        for operation in self.cursor.execute.call_args_list[-3:]:
            self.assertEqual(operation.args[1], ("User",))
        self.assertIn("Table: User", output)
        self.cursor.close.assert_called_once()
        self.connection.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
