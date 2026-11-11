"""Mocked connection-layer tests: no environment files or network access."""

import importlib
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import call, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from flask import Flask
import mysql.connector

import db


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ca = Path(self.temp.name) / "ca.pem"
        self.ca.write_text("test fixture; CA parsing is mocked")
        self.environment = {
            "MYSQL_HOST": "mysql.example.invalid",
            "MYSQL_PORT": "12345",
            "MYSQL_DATABASE": "careerly",
            "MYSQL_USER": "test_user",
            "MYSQL_PASSWORD": "test-secret-never-print",
            "MYSQL_SSL_CA": str(self.ca),
        }
        env = patch.dict(os.environ, self.environment, clear=True)
        env.start()
        self.addCleanup(env.stop)
        ca_parser = patch("db.ssl.create_default_context")
        self.parse_ca = ca_parser.start()
        self.addCleanup(ca_parser.stop)
        connector = patch("db.mysql.connector.connect")
        self.connect = connector.start()
        self.addCleanup(connector.stop)
        self.connection = self.connect.return_value
        self.cursor = self.connection.cursor.return_value
        self.cursor.fetchone.side_effect = lambda: next(self.results)
        self.results = iter([("careerly",), ("Ssl_cipher", "TLS_AES_256_GCM_SHA384")])
        self.app = Flask(__name__)
        self.app.testing = True
        db.init_app(self.app)

    def test_missing_configuration_names_only(self):
        for name in self.environment:
            with self.subTest(name=name), patch.dict(os.environ, {name: ""}):
                with self.app.app_context(), self.assertRaises(db.DatabaseError) as error:
                    db.get_db()
                self.assertIn(name, str(error.exception))
                self.assertNotIn(self.environment["MYSQL_PASSWORD"], str(error.exception))
        self.connect.assert_not_called()

    def test_invalid_port_never_connects(self):
        for port in ["abc", "0", "65536"]:
            with self.subTest(port=port), patch.dict(os.environ, MYSQL_PORT=port):
                with self.app.app_context(), self.assertRaisesRegex(db.DatabaseError, "MYSQL_PORT"):
                    db.get_db()
        self.connect.assert_not_called()

    def test_missing_ca_never_connects(self):
        self.ca.unlink()
        with self.app.app_context(), self.assertRaisesRegex(db.DatabaseError, "MYSQL_SSL_CA"):
            db.get_db()
        self.connect.assert_not_called()

    def test_invalid_ca_error_is_sanitised(self):
        self.parse_ca.side_effect = ssl.SSLError("sensitive certificate path")
        with self.app.app_context(), self.assertRaises(db.DatabaseError) as error:
            db.get_db()
        self.assertNotIn("sensitive", str(error.exception))
        self.connect.assert_not_called()

    def test_tls_options_and_context_reuse(self):
        with self.app.app_context():
            self.assertIs(db.get_db(), self.connection)
            self.assertIs(db.get_db(), self.connection)
        self.connect.assert_called_once()
        options = self.connect.call_args.kwargs
        self.assertIs(options["ssl_verify_cert"], True)
        self.assertIs(options["ssl_verify_identity"], True)
        self.assertIs(options["ssl_disabled"], False)
        self.assertIs(options["autocommit"], False)
        self.assertEqual(options["tls_versions"], ["TLSv1.2", "TLSv1.3"])
        self.assertEqual(options["ssl_ca"], str(self.ca.resolve()))
        for key in ["host", "database", "user", "password"]:
            self.assertEqual(options[key], self.environment["MYSQL_" + key.upper()])
        self.assertEqual(options["port"], 12345)
        self.parse_ca.assert_called_once_with(cafile=str(self.ca.resolve()))
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_exception_still_rolls_back_and_closes(self):
        with self.assertRaisesRegex(RuntimeError, "request failed"):
            with self.app.app_context():
                db.get_db()
                raise RuntimeError("request failed")
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_close_runs_even_if_rollback_fails_without_logging_details(self):
        self.connection.rollback.side_effect = mysql.connector.Error("test-secret-never-print")
        with self.assertLogs(self.app.logger, level="WARNING") as logs:
            with self.app.app_context():
                db.get_db()
        self.connection.close.assert_called_once()
        self.assertNotIn("test-secret-never-print", " ".join(logs.output))

    def test_no_connection_means_no_cleanup_work(self):
        with self.app.app_context():
            pass
        self.connect.assert_not_called()
        self.connection.rollback.assert_not_called()

    def test_new_context_gets_new_connection(self):
        for _ in range(2):
            self.results = iter([("careerly",), ("Ssl_cipher", "cipher")])
            with self.app.app_context():
                db.get_db()
        self.assertEqual(self.connect.call_count, 2)
        self.assertEqual(self.connection.close.call_count, 2)

    def test_empty_cipher_rejected_and_connection_closed(self):
        self.results = iter([("careerly",), ("Ssl_cipher", "")])
        with self.app.app_context(), self.assertRaisesRegex(db.DatabaseError, "SSL cipher"):
            db.get_db()
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()

    def test_wrong_database_rejected(self):
        self.results = iter([("wrong_database",)])
        with self.app.app_context(), self.assertRaisesRegex(db.DatabaseError, "MYSQL_DATABASE"):
            db.get_db()
        self.connection.close.assert_called_once()

    def test_connector_errors_hide_credentials_and_do_not_retry(self):
        self.connect.side_effect = mysql.connector.Error(self.environment["MYSQL_PASSWORD"])
        result = self.app.test_cli_runner().invoke(args=["db-check"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Check the environment", result.output)
        self.assertNotIn(self.environment["MYSQL_PASSWORD"], result.output)
        self.connect.assert_called_once()

    def test_cli_read_only_success_and_cleanup(self):
        result = self.app.test_cli_runner().invoke(args=["db-check"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("session SSL cipher is non-empty", result.output)
        self.assertEqual(self.cursor.execute.call_args_list, [
            call("SELECT DATABASE()"), call("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        ])
        self.cursor.close.assert_called_once()
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_cli_rejects_missing_config(self):
        with patch.dict(os.environ, MYSQL_PASSWORD=""):
            result = self.app.test_cli_runner().invoke(args=["db-check"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("MYSQL_PASSWORD", result.output)
        self.connect.assert_not_called()

    def test_application_import_and_existing_routes_do_not_connect(self):
        # Main loads dotenv on import; never read private files in mocked tests.
        with patch("dotenv.load_dotenv", return_value=False), patch.dict(
            os.environ, SECRET_KEY="unit-test-only-session-key"
        ):
            module = importlib.import_module("app")
        auth = importlib.import_module("services.auth_service")
        user = auth.User(
            id=101, name="Synthetic User", email="synthetic@example.invalid",
            password_hash="synthetic-unusable-login-marker",
        )
        page = {
            "applications": [], "interviews": [], "practice_meta": "",
            "status_counts": {}, "total_applications": 0,
            "company_options": [], "role_options": [],
        }
        with patch.dict(os.environ, {}, clear=True), patch.dict(
            module.app.config, TESTING=True, SECRET_KEY="unit-test-only-session-key"
        ), patch.object(module, "get_user_by_id", return_value=user), patch.object(
            module, "load_tracker_page", return_value=page
        ) as tracker:
            client = module.app.test_client()
            self.assertEqual(client.get("/").status_code, 200)
            response = client.get("/tracker")
            self.assertEqual(response.status_code, 302)
            destination = urlsplit(response.location)
            self.assertEqual(destination.path, "/login")
            self.assertEqual(parse_qs(destination.query), {"next": ["/tracker"]})
            self.assertEqual(destination.query, urlencode({"next": "/tracker"}))
            tracker.assert_not_called()
            with client.session_transaction() as session:
                session["_user_id"] = user.get_id()
                session["_fresh"] = True
            self.assertEqual(client.get("/tracker").status_code, 200)
            tracker.assert_called_once_with(user.id, status="", company="", role="")
        self.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
