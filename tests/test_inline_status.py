"""Inline status change on the tracker: route, validation and table layout."""
from datetime import datetime
import unittest
from unittest.mock import patch

from db import DatabaseError
from services import application_service
import test_add_interview


class SetStatusValidationTests(unittest.TestCase):
    def test_rejects_unknown_status_before_touching_the_database(self):
        with patch("services.application_service.operation_session") as session:
            with self.assertRaises(application_service.ApplicationValidationError) as raised:
                application_service.set_status(101, 501, "Ghosted")
        self.assertIn("status", raised.exception.errors)
        session.assert_not_called()

    def test_rejects_invalid_ids(self):
        with self.assertRaises(application_service.ApplicationValidationError):
            application_service.set_status(101, 0, "Offer")


class InlineStatusRouteTests(unittest.TestCase):
    setUp = test_add_interview.AddInterviewTests.setUp
    tearDown = test_add_interview.AddInterviewTests.tearDown

    def post_status(self, status, application_id=501, **extra):
        form = dict(csrf_token="synthetic-token", status=status, next="/tracker?status=Applied", **extra)
        return self.client.post(f"/applications/{application_id}/status", data=form)

    def test_saves_status_and_returns_to_the_filtered_tracker(self):
        with patch("services.application_service.set_status") as set_status:
            response = self.post_status("Offer")
        set_status.assert_called_once_with(101, 501, "Offer")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/tracker?status=Applied"))

    def test_not_found_and_invalid_status_map_to_404_and_400(self):
        for error, code in ((application_service.ApplicationNotFound(), 404),
                            (application_service.ApplicationValidationError({"status": "x"}), 400)):
            with self.subTest(code=code), patch("services.application_service.set_status", side_effect=error):
                self.assertEqual(self.post_status("Offer").status_code, code)

    def test_background_save_answers_in_json_without_redirect(self):
        headers = {"Accept": "application/json"}
        form = dict(csrf_token="synthetic-token", status="Interview")
        with patch("services.application_service.set_status"):
            saved = self.client.post("/applications/501/status", data=form, headers=headers)
        self.assertEqual((saved.status_code, saved.get_json()), (200, {"status": "Interview"}))
        with patch("services.application_service.set_status", side_effect=DatabaseError("private")):
            failed = self.client.post("/applications/501/status", data=form, headers=headers)
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("private", failed.get_data(as_text=True))

    def test_star_saves_in_the_background_with_json(self):
        headers = {"Accept": "application/json"}
        for value, starred in (("1", True), ("0", False)):
            with self.subTest(value=value), patch("services.application_service.set_starred") as set_starred:
                response = self.client.post("/applications/501/star", headers=headers,
                                            data=dict(csrf_token="synthetic-token", starred=value))
            set_starred.assert_called_once_with(101, 501, starred)
            self.assertEqual((response.status_code, response.get_json()), (200, {"starred": starred}))
        with patch("services.application_service.set_starred", side_effect=DatabaseError("private")):
            failed = self.client.post("/applications/501/star", headers=headers,
                                      data=dict(csrf_token="synthetic-token", starred="1"))
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("private", failed.get_data(as_text=True))

    def test_star_without_javascript_still_redirects(self):
        with patch("services.application_service.set_starred"):
            response = self.client.post("/applications/501/star", data=dict(
                csrf_token="synthetic-token", starred="1", next="/tracker?starred=1"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/tracker?starred=1"))

    def test_database_error_flashes_and_redirects(self):
        with patch("services.application_service.set_status", side_effect=DatabaseError("private")):
            response = self.post_status("Offer")
        self.assertEqual(response.status_code, 302)

    def test_requires_csrf_token(self):
        response = self.client.post("/applications/501/status", data=dict(status="Offer"))
        self.assertEqual(response.status_code, 400)

    def test_table_has_status_dropdown_star_first_and_no_applied_column(self):
        with patch("tracker_data._now_utc", return_value=datetime(2026, 7, 10)):
            html = self.client.get("/tracker").get_data(as_text=True)
        self.assertNotIn('data-col="applied"', html)
        self.assertNotIn(">Applied</th>", html)
        self.assertIn('id="status-form-501"', html)
        self.assertIn('form="status-form-501"', html)
        self.assertIn('<option value="Applied" data-theme="info" selected>Applied</option>', html)
        row = html[html.index('<tbody>'):]
        self.assertLess(row.index('data-col="star"'), row.index('data-col="company"'))
        head = html[html.index('<thead'):html.index('</thead>')]
        self.assertLess(head.index('name="starred"'), head.index('name="company"'))
        self.assertIn('type="checkbox" class="visually-hidden" name="starred" value="1"', head)
        self.assertNotIn("bi-chevron-down", head[:head.index('</th>')])

    def test_starred_filter_shows_a_filled_checked_star(self):
        html = self.client.get("/tracker?starred=1").get_data(as_text=True)
        head = html[html.index('<thead'):html.index('</thead>')]
        self.assertIn('value="1" checked', head)
        self.assertIn("bi-star-fill", head)


if __name__ == "__main__":
    unittest.main()
