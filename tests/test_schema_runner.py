"""Local runner safety checks only. Never execute the hosted schema suite."""

import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector

from database import verify_schema as runner


class RunnerTests(unittest.TestCase):
    def test_opt_in_required_before_configuration_or_connection(self):
        with patch("dotenv.load_dotenv") as load, patch("db.get_db") as connect:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
                runner.main([])
        self.assertEqual(exit.exception.code, 2)
        load.assert_not_called()
        connect.assert_not_called()

    def connection(self):
        connection = MagicMock()
        connection.autocommit = False
        return connection

    def test_success_always_rolls_back_without_committing(self):
        connection = self.connection()
        output = []
        with patch.object(runner.SchemaChecks, "run"):
            self.assertEqual(runner.run_verification(connection, output.append), 0)
        connection.start_transaction.assert_called_once_with()
        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()
        connection.cursor.return_value.close.assert_called_once_with()

    def test_unexpected_error_rolls_back_and_does_not_expose_details(self):
        for error in [mysql.connector.Error("private connection details", errno=1045),
                      RuntimeError("private connection details"), KeyboardInterrupt()]:
            with self.subTest(error=type(error).__name__):
                connection = self.connection()
                output = []
                with patch.object(runner.SchemaChecks, "run", side_effect=error):
                    self.assertEqual(runner.run_verification(connection, output.append), 1)
                connection.rollback.assert_called_once()
                connection.commit.assert_not_called()
                connection.cursor.return_value.close.assert_called_once()
                self.assertNotIn("private connection details", " ".join(output))

    def test_rollback_failure_cannot_report_success(self):
        connection = self.connection()
        connection.rollback.side_effect = mysql.connector.Error("private failure")
        output = []
        with patch.object(runner.SchemaChecks, "run"):
            self.assertEqual(runner.run_verification(connection, output.append), 1)
        connection.cursor.return_value.close.assert_called_once()
        self.assertIn("rollback could not be confirmed", " ".join(output))
        self.assertNotIn("private failure", " ".join(output))

    def test_autocommit_refused_before_any_checks(self):
        connection = self.connection()
        connection.autocommit = True
        with patch.object(runner.SchemaChecks, "run") as suite:
            self.assertEqual(runner.run_verification(connection, lambda line: None), 1)
        suite.assert_not_called()
        connection.start_transaction.assert_not_called()
        connection.rollback.assert_called_once()

    def test_exact_expected_error_codes_only(self):
        checks = runner.SchemaChecks(MagicMock())
        for code in [1048, 1364, 1062, 3819, 1451, 1452]:
            with self.subTest(code=code):
                operation = MagicMock(side_effect=mysql.connector.Error("not printed", errno=code))
                checks.expect_error(code, operation)
                operation.assert_called_once()
        for error in [mysql.connector.Error("private", errno=1045),
                      mysql.connector.Error("private", errno=1452)]:
            with self.assertRaises(runner.VerificationFailure):
                checks.expect_error(1062, MagicMock(side_effect=error))
        with self.assertRaises(runner.VerificationFailure):
            checks.expect_error(1062, lambda: None)
        with self.assertRaises(RuntimeError):
            checks.expect_error(1062, MagicMock(side_effect=RuntimeError("unexpected")))

    def test_company_name_omission_reaches_mysql(self):
        cursor = MagicMock()
        cursor.execute.side_effect = mysql.connector.Error("name has no default", errno=1364)
        checks = runner.SchemaChecks(cursor)
        row = checks.row("Company")
        del row["name"]

        checks.expect_error(1364, lambda: checks.insert("Company", row))

        cursor.execute.assert_called_once_with("INSERT INTO `Company` () VALUES ()", ())
        self.assertEqual(checks.owned["Company"], set())

    def test_all_required_field_omissions_reach_mysql(self):
        for table, columns in runner.REQUIRED.items():
            for column in columns:
                if (table, column) == ("JobApplication", "status"):
                    continue  # Omission uses the documented Saved default.
                with self.subTest(table=table, column=column):
                    cursor = MagicMock()
                    cursor.execute.side_effect = mysql.connector.Error("missing default", errno=1364)
                    checks = runner.SchemaChecks(cursor)
                    checks.user, checks.company, checks.application = 11, 22, 33
                    row = checks.row(table)
                    del row[column]

                    checks.expect_error(1364, lambda: checks.insert(table, row))

                    cursor.execute.assert_called_once()
                    sql, parameters = cursor.execute.call_args.args
                    self.assertNotIn(f"`{column}`", sql)
                    self.assertEqual(parameters, tuple(row.values()))
                    self.assertEqual(sql.count("%s"), len(row))
                    self.assertEqual(checks.owned[table], set())

    def test_empty_column_inserts_reach_mysql_for_every_table(self):
        for table in runner.PRIMARY_KEYS:
            with self.subTest(table=table):
                cursor = MagicMock()
                cursor.execute.side_effect = mysql.connector.Error("missing default", errno=1364)
                checks = runner.SchemaChecks(cursor)

                checks.expect_error(1364, lambda: checks.insert(table, {}))

                cursor.execute.assert_called_once_with(f"INSERT INTO `{table}` () VALUES ()", ())

    def test_empty_insert_requires_expected_mysql_error(self):
        for actual_code in (None, 1045, 1048):
            with self.subTest(actual_code=actual_code):
                cursor = MagicMock()
                cursor.lastrowid = 123
                if actual_code is not None:
                    cursor.execute.side_effect = mysql.connector.Error("not printed", errno=actual_code)
                checks = runner.SchemaChecks(cursor)

                with self.assertRaises(runner.VerificationFailure) as error:
                    checks.expect_error(1364, lambda: checks.insert("Company", {}))

                cursor.execute.assert_called_once_with("INSERT INTO `Company` () VALUES ()", ())
                self.assertIn("Expected MySQL error 1364", str(error.exception))

    def test_mutations_are_parameterised_and_restricted_to_owned_ids(self):
        cursor = MagicMock()
        cursor.lastrowid = 123
        checks = runner.SchemaChecks(cursor)
        row = checks.row("User")
        identity = checks.insert("User", row)
        statement, values = cursor.execute.call_args.args
        self.assertTrue(statement.startswith("INSERT INTO `User`"))
        self.assertEqual(values, tuple(row.values()))
        self.assertNotIn(row["password_hash"], statement)
        checks.update("User", identity, "name", "synthetic update")
        self.assertEqual(cursor.execute.call_args.args[1], ("synthetic update", identity))
        checks.delete("User", identity)
        self.assertEqual(cursor.execute.call_args.args[1], (identity,))
        for operation in [lambda: checks.delete("User", 456),
                          lambda: checks.update("User", 456, "name", "synthetic"),
                          lambda: checks.insert("User", {"unexpected_column": "synthetic"})]:
            cursor.reset_mock()
            with self.assertRaises(runner.VerificationFailure):
                operation()
            cursor.execute.assert_not_called()

    def test_preflight_rejects_nontransactional_table(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            (table, "MyISAM" if table == "User" else "InnoDB", "utf8mb4_0900_ai_ci")
            for table in runner.PRIMARY_KEYS
        ]
        with self.assertRaises(runner.VerificationFailure):
            runner.SchemaChecks(cursor).preflight()
        self.assertTrue(all(call.args[0].startswith("SELECT ") for call in cursor.execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
