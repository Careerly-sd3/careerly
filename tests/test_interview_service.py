"""CAR-86 unit tests only: synthetic rows, mocked db.get_db(), no app imports."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector

from db import DatabaseError
from services import interview_service as service


class InterviewServiceTests(unittest.TestCase):
    def setUp(self):
        provider = patch("services.interview_service.get_db")
        self.get_db = provider.start()
        self.addCleanup(provider.stop)
        network = patch("mysql.connector.connect", side_effect=AssertionError("No database access in unit tests"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.connection = self.get_db.return_value
        self.connection.autocommit = False
        self.connection.in_transaction = False
        self.cursor = self.connection.cursor.return_value
        self.cursor.rowcount = 1
        self.cursor.lastrowid = 41
        self.time = datetime(2026, 11, 9, 12, 0)
        self.application = {
            "application_id": 11, "user_id": 7, "company_id": 3,
            "job_title": "Synthetic role", "status": "Applied", "company": "Synthetic company",
        }
        self.row = {
            "interview_id": 41, "application_id": 11, "scheduled_at": self.time,
            "interview_type": "online", "venue_or_link": None, "notes": "Synthetic notes",
            "outcome": None, "job_title": "Synthetic role", "company": "Synthetic company",
        }
        self.cursor.fetchone.return_value = self.row
        self.input = {
            "scheduled_at": self.time, "interview_type": "online",
            "venue_or_link": "https://example.invalid/meeting", "notes": "Synthetic notes",
        }

    def tearDown(self):
        self.network.assert_not_called()

    def create(self, **overrides):
        self.cursor.fetchone.side_effect = [self.application, self.row]
        return service.create_interview(7, 11, **(self.input | overrides))

    def update(self, **overrides):
        return service.update_interview(7, 41, **(self.input | {"outcome": "Passed"} | overrides))

    def assert_rolled_back(self):
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()
        self.cursor.close.assert_called_once_with()

    def test_get_owned_application_uses_owner_predicate(self):
        self.cursor.fetchone.return_value = self.application
        self.assertEqual(service.get_owned_application(7, 11), self.application)
        self.cursor.execute.assert_called_once_with(service.APPLICATION_QUERY, (11, 7))
        self.assertIn("a.application_id = %s AND a.user_id = %s", service.APPLICATION_QUERY)
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_not_called()
        self.cursor.close.assert_called_once()

    def test_get_interview_has_all_columns_and_presents_null_without_mutation(self):
        result = service.get_interview(7, 41)
        self.assertEqual(set(result), set(self.row))
        self.assertEqual(result["outcome"], "Pending")
        self.assertIsNone(self.row["outcome"])
        self.cursor.execute.assert_called_once_with(service.OWNED_INTERVIEW_QUERY, (41, 7))
        self.assertIn("JOIN `JobApplication`", service.OWNED_INTERVIEW_QUERY)
        self.assertIn("i.interview_id = %s AND a.user_id = %s", service.OWNED_INTERVIEW_QUERY)

    def test_absent_and_foreign_resources_share_not_found(self):
        for operation in [service.get_owned_application, service.get_interview]:
            for resource_id in (41, 999):
                with self.subTest(operation=operation.__name__, resource_id=resource_id):
                    self.cursor.fetchone.return_value = None
                    with self.assertRaises(service.InterviewNotFound) as error:
                        operation(8, resource_id)
                    self.assertEqual(str(error.exception), "Resource not found.")
                    self.assertEqual(self.cursor.execute.call_args.args[1], (resource_id, 8))
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_not_called()

    def test_listing_keeps_database_order_and_one_utc_cutoff(self):
        # Database-supplied order: cutoff inclusive; upcoming ties by ID;
        # future before past; most recent past before older past, with ID ties.
        rows = [self.row | {"interview_id": i, "scheduled_at": time} for i, time in [
            (5, self.time), (2, self.time + timedelta(hours=1)),
            (3, self.time + timedelta(hours=1)), (6, self.time - timedelta(hours=1)),
            (7, self.time - timedelta(hours=1)), (1, self.time - timedelta(days=2)),
        ]]
        self.cursor.fetchall.return_value = rows
        cutoff = self.time.replace(tzinfo=timezone.utc)
        result = service.list_interviews(7, cutoff=cutoff)
        self.assertEqual([row["interview_id"] for row in result], [5, 2, 3, 6, 7, 1])
        self.assertTrue(all(row["outcome"] == "Pending" for row in result))
        self.cursor.execute.assert_called_once_with(service.LIST_QUERY, (7, self.time, self.time, self.time))
        normalised = " ".join(service.LIST_QUERY.split())
        self.assertIn("WHERE a.user_id = %s ORDER BY CASE WHEN i.scheduled_at >= %s THEN 0 ELSE 1 END ASC,", normalised)
        self.assertIn("CASE WHEN i.scheduled_at >= %s THEN i.scheduled_at END ASC,", normalised)
        self.assertIn("CASE WHEN i.scheduled_at < %s THEN i.scheduled_at END DESC, i.interview_id ASC", normalised)

    def test_listing_current_cutoff_is_reused_and_empty_result_is_valid(self):
        before = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        self.cursor.fetchall.return_value = []
        self.assertEqual(service.list_interviews(7), [])
        after = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        owner, first, second, third = self.cursor.execute.call_args.args[1]
        self.assertEqual(owner, 7)
        self.assertTrue(before <= first <= after)
        self.assertEqual(first, second)
        self.assertEqual(first, third)

    def test_create_defaults_pending_and_locks_owner_before_parameterised_insert(self):
        result = self.create()
        calls = self.cursor.execute.call_args_list
        self.assertEqual(calls[0].args, (service.APPLICATION_QUERY + " FOR UPDATE", (11, 7)))
        self.assertEqual(calls[1].args, (service.CREATE_QUERY, (*self.input.values(), "Pending", 11, 7)))
        self.assertIn("WHERE a.application_id = %s AND a.user_id = %s", service.CREATE_QUERY)
        self.assertEqual(calls[2].args, (service.OWNED_INTERVIEW_QUERY, (41, 7)))
        self.assertEqual(result["interview_id"], 41)
        self.connection.start_transaction.assert_called_once()
        self.connection.commit.assert_called_once()
        self.connection.rollback.assert_not_called()
        names = [call[0] for call in self.connection.mock_calls]
        self.assertLess(names.index("cursor().close"), names.index("commit"))
        self.connection.close.assert_not_called()

    def test_aware_time_is_converted_to_utc_and_seconds(self):
        local = datetime(2026, 11, 9, 14, 0, 0, 123, tzinfo=timezone(timedelta(hours=2)))
        self.create(scheduled_at=local)
        self.assertEqual(self.cursor.execute.call_args_list[1].args[1][0], self.time)

    def test_update_scoped_and_cannot_reassign_application(self):
        result = self.update()
        calls = self.cursor.execute.call_args_list
        self.assertEqual(calls[0].args, (service.OWNED_INTERVIEW_QUERY + " FOR UPDATE", (41, 7)))
        self.assertEqual(calls[1].args, (service.UPDATE_QUERY, (*self.input.values(), "Passed", 41, 7)))
        self.assertIn("i.interview_id = %s AND a.user_id = %s", service.UPDATE_QUERY)
        assignments = service.UPDATE_QUERY.split("SET", 1)[1].split("WHERE", 1)[0]
        self.assertNotIn("application_id", assignments)
        self.assertEqual(result["application_id"], 11)
        self.connection.commit.assert_called_once()
        self.connection.rollback.assert_not_called()

    def test_unchanged_owned_edit_is_success_even_with_zero_changed_rows(self):
        self.cursor.rowcount = 0
        self.assertEqual(self.update()["interview_id"], 41)
        self.connection.commit.assert_called_once()

    def test_delete_scopes_the_dml_and_commits(self):
        self.assertIsNone(service.delete_interview(7, 41))
        self.assertEqual(self.cursor.execute.call_args_list[-1].args, (service.DELETE_QUERY, (41, 7)))
        self.assertIn("i.interview_id = %s AND a.user_id = %s", service.DELETE_QUERY)
        self.connection.commit.assert_called_once()
        self.connection.rollback.assert_not_called()

    def test_mutation_denies_unknown_or_foreign_owner_before_dml(self):
        for operation in ("create", "update", "delete"):
            for owner, identity in [(8, 41), (7, 999)]:
                with self.subTest(operation=operation, owner=owner, identity=identity):
                    self.connection.reset_mock()
                    self.cursor.fetchone.side_effect = None
                    self.cursor.fetchone.return_value = None
                    with self.assertRaises(service.InterviewNotFound):
                        if operation == "create":
                            service.create_interview(owner, identity, **self.input)
                        elif operation == "update":
                            service.update_interview(owner, identity, **self.input, outcome="Pending")
                        else:
                            service.delete_interview(owner, identity)
                    self.assertEqual(self.cursor.execute.call_count, 1)
                    self.assertEqual(self.cursor.execute.call_args.args[1], (identity, owner))
                    self.assert_rolled_back()

    def test_guards_survive_zero_insert_or_delete_count(self):
        for operation in (self.create, lambda: service.delete_interview(7, 41)):
            with self.subTest(operation=operation):
                self.connection.reset_mock()
                self.cursor.rowcount = 0
                self.cursor.fetchone.side_effect = None
                self.cursor.fetchone.return_value = self.row
                with self.assertRaises(service.InterviewNotFound):
                    operation()
                self.assert_rolled_back()

    def test_values_are_never_interpolated_into_sql(self):
        payload = "synthetic'); DELETE FROM Interview; --"
        self.create(notes=payload, venue_or_link=payload)
        for call in self.cursor.execute.call_args_list:
            query, parameters = call.args
            self.assertNotIn(payload, query)
            self.assertEqual(query.count("%s"), len(parameters))
        self.assertIn(payload, self.cursor.execute.call_args_list[1].args[1])
        with self.assertRaises(TypeError):
            service.update_interview(7, 41, **self.input, outcome="Pending", application_id=12)
        with self.assertRaises(TypeError):
            service.create_interview(7, 11, **self.input, posted_user_id=8)

    def test_supported_types_and_outcomes_are_exact(self):
        for kind in ("online", "phone", "technical", "onsite"):
            for outcome in ("Pending", "Passed", "Failed"):
                with self.subTest(kind=kind, outcome=outcome):
                    self.create(interview_type=kind, outcome=outcome)
        for field, values in {
            "interview_type": (None, "", "Online", "online ", "remote", 1),
            "outcome": (None, "", "pending", "Passed ", "Offer", 1),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.get_db.reset_mock()
                    with self.assertRaises(service.InterviewValidationError) as error:
                        service.create_interview(7, 11, **(self.input | {field: value}))
                    self.assertIn(field, error.exception.errors)
                    self.get_db.assert_not_called()

    def test_required_datetime_and_bounded_optional_text(self):
        for overrides, field in [
            ({"scheduled_at": None}, "scheduled_at"),
            ({"scheduled_at": "2026-11-09"}, "scheduled_at"),
            ({"scheduled_at": datetime(999, 12, 31)}, "scheduled_at"),
            ({"venue_or_link": "x" * 501}, "venue_or_link"),
            ({"venue_or_link": 1}, "venue_or_link"),
            ({"venue_or_link": "\ud800"}, "venue_or_link"),
            ({"notes": "x" * 65536}, "notes"),
            ({"notes": "\u00e9" * 32768}, "notes"),
            ({"notes": 1}, "notes"),
            ({"notes": "\ud800"}, "notes"),
        ]:
            with self.subTest(field=field):
                with self.assertRaises(service.InterviewValidationError) as error:
                    service.create_interview(7, 11, **(self.input | overrides))
                self.assertIn(field, error.exception.errors)
        self.get_db.assert_not_called()
        self.create(venue_or_link="x" * 500, notes="x" * 65535)

    def test_invalid_identity_never_queries(self):
        for value in (None, True, 0, -1, 2147483648, "7", "7 OR 1=1", 7.0):
            with self.subTest(value=value), self.assertRaises(service.InterviewValidationError):
                service.list_interviews(value)
        self.get_db.assert_not_called()

    def test_active_caller_transaction_or_autocommit_is_never_completed(self):
        for active, autocommit in [(True, False), (False, True)]:
            for operation in (self.create, self.update, lambda: service.delete_interview(7, 41)):
                with self.subTest(active=active, autocommit=autocommit):
                    self.connection.reset_mock()
                    self.connection.in_transaction, self.connection.autocommit = active, autocommit
                    with self.assertRaises(service.InterviewTransactionError):
                        operation()
                    self.connection.start_transaction.assert_not_called()
                    self.connection.cursor.assert_not_called()
                    self.connection.commit.assert_not_called()
                    self.connection.rollback.assert_not_called()

    def test_execute_failure_rolls_back_without_exposing_details(self):
        self.cursor.execute.side_effect = mysql.connector.Error("private driver details", errno=1452)
        with self.assertRaises(DatabaseError) as error:
            self.create()
        self.assertNotIn("private driver details", str(error.exception))
        self.assert_rolled_back()

    def test_commit_failure_attempts_rollback_and_returns_no_success(self):
        self.connection.commit.side_effect = mysql.connector.Error("private commit details")
        with self.assertRaises(DatabaseError) as error:
            self.create()
        self.assertNotIn("private commit details", str(error.exception))
        self.assertIn("check its outcome", str(error.exception))
        self.connection.rollback.assert_called_once()
        self.cursor.close.assert_called_once()

    def test_rollback_failure_is_explicit_and_sanitised(self):
        self.cursor.execute.side_effect = mysql.connector.Error("private write details")
        self.connection.rollback.side_effect = mysql.connector.Error("private rollback details")
        with self.assertRaises(DatabaseError) as error:
            self.create()
        self.assertIn("rollback failed", str(error.exception))
        self.assertNotIn("private", str(error.exception))
        self.cursor.close.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_start_transaction_failure_attempts_rollback(self):
        self.connection.start_transaction.side_effect = mysql.connector.Error("private start details")
        with self.assertRaises(DatabaseError):
            self.create()
        self.connection.rollback.assert_called_once()
        self.connection.cursor.assert_not_called()
        self.connection.commit.assert_not_called()

    def test_cursor_cleanup_failure_prevents_commit_and_rolls_back(self):
        self.cursor.close.side_effect = mysql.connector.Error("private close details")
        with self.assertRaises(DatabaseError):
            self.create()
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_read_failure_preserves_caller_transaction(self):
        self.connection.in_transaction = True
        self.cursor.execute.side_effect = mysql.connector.Error("private read details")
        with self.assertRaises(DatabaseError) as error:
            service.list_interviews(7)
        self.assertNotIn("private", str(error.exception))
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_not_called()
        self.cursor.close.assert_called_once()

    def test_interruption_rolls_back(self):
        self.cursor.execute.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.create()
        self.assert_rolled_back()


if __name__ == "__main__":
    unittest.main()
