from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

import tracker_data
import test_add_interview


class DeadlineWarningTests(unittest.TestCase):
    def test_signed_days_text_and_badge_boundaries(self):
        today = date(2026, 7, 10)
        for days, text, theme in (
            (-1, "Overdue", "danger"),
            (0, "Due today", "danger"),
            (1, "1 day left", "danger"),
            (2, "2 days left", "danger"),
            (3, "3 days left", "warning"),
            (7, "7 days left", "warning"),
            (8, "8 days left", "success"),
        ):
            with self.subTest(days=days):
                self.assertEqual(tracker_data._deadline_warning(today + timedelta(days=days), today),
                                 dict(days_left=days, text=text, theme=theme))
        self.assertIsNone(tracker_data._deadline_warning(None, today))

    def test_dublin_date_from_tracker_cutoff(self):
        for cutoff, deadline, days in (
            (datetime(2026, 7, 9, 22, 59), date(2026, 7, 10), 1),
            (datetime(2026, 7, 9, 23), date(2026, 7, 10), 0),
            (datetime(2026, 1, 9, 23), date(2026, 1, 10), 1),
            (datetime(2026, 1, 10), date(2026, 1, 10), 0),
        ):
            with self.subTest(cutoff=cutoff):
                application = dict(application_id=1, company="Example", job_title="Developer",
                                   applied_at=None, status="Saved", closing_date=deadline)
                row, = tracker_data._application_rows([application], [], cutoff)
                self.assertEqual(row["deadline_warning"]["days_left"], days)


class DeadlineRouteTests(unittest.TestCase):
    setUp = test_add_interview.AddInterviewTests.setUp
    tearDown = test_add_interview.AddInterviewTests.tearDown

    def seed_deadline_rows(self):
        base = self.database.applications[501]
        for identity, company, role, status, day in (
            (501, "Synthetic company", "Synthetic role", "Applied", None),
            (503, "Synthetic company", "Synthetic role", "Applied", 13),
            (504, "Synthetic company", "Synthetic role", "Applied", 10),
            (505, "Synthetic company", "Synthetic role", "Applied", 10),
            (506, "Synthetic company", "Synthetic role", "Offer", 8),
            (507, "Other owned company", "Synthetic role", "Rejected", 9),
            (508, "Synthetic company", "Other owned role", "Withdrawn", 10),
            (509, "Other owned company", "Synthetic role", "Applied", 18),
            (510, "Synthetic company", "Other owned role", "Applied", 17),
        ):
            self.database.applications[identity] = base | dict(
                application_id=identity, company=company, job_title=role, status=status,
                closing_date=date(2026, 7, day) if day else None)

    def test_warnings_and_filters_preserve_owner_scope_and_selected_controls(self):
        self.seed_deadline_rows()
        with patch("tracker_data._now_utc", return_value=datetime(2026, 7, 10)):
            default = self.client.get("/tracker").get_data(as_text=True)
            filtered = self.client.get("/tracker", query_string=dict(
                company="Synthetic company", role="Synthetic role",
                status="Applied")).get_data(as_text=True)

        def assert_order(html, ids):
            positions = [html.index(f'id="details-{identity}"') for identity in ids]
            self.assertEqual(positions, sorted(positions))
            self.assertNotIn('id="details-502"', html)

        assert_order(default, [501, 503, 504, 505, 506, 507, 508, 509, 510])
        # Only five applications have both a deadline and a warning-eligible status.
        self.assertEqual(default.count('days left</span>'), 3)
        self.assertEqual(default.count('Due today</span>'), 2)
        self.assertNotIn('Overdue</span>', default)
        self.assertIn('theme-warning">3 days left</span>', default)
        self.assertIn('theme-success">8 days left</span>', default)
        self.assertIn('theme-danger">Due today</span>', default)
        assert_order(filtered, [501, 503, 504, 505])
        for identity in (506, 507, 508, 509, 510):
            self.assertNotIn(f'id="details-{identity}"', filtered)
        for value in ("Synthetic company", "Synthetic role", "Applied"):
            self.assertIn(f'value="{value}" selected', filtered)

    def test_create_and_edit_validate_dates_before_writing_and_retain_input(self):
        saved = self.database.applications[501]
        form = dict(csrf_token="synthetic-token", company="Example company",
                    job_title="Test developer", opportunity_type="internship", status="Applied")
        for url in ("/applications/new", "/applications/501/edit"):
            for closing, applied, error_field, message in (
                ("2026-07-09", "2026-07-10", "closing_date",
                 "Closing date cannot be earlier than applied date."),
                ("2026-02-30", "2026-07-10", "closing_date", "Use a valid date (YYYY-MM-DD)."),
                ("2026-07-09", "invalid", "applied_at", "Use a valid date (YYYY-MM-DD)."),
                ("2026-07-10", "2026-07-10", None, None),
                ("", "2026-07-10", None, None),
                ("2026-07-09", "", None, None),
                ("", "", None, None),
            ):
                with (self.subTest(url=url, closing=closing, applied=applied),
                      patch("services.application_service.get_application", return_value=saved),
                      patch("services.application_service._write") as write):
                    response = self.client.post(url, data=form | dict(closing_date=closing, applied_at=applied))
                    if error_field is None:
                        self.assertEqual(response.status_code, 302)
                        write.assert_called_once()
                    else:
                        self.assertEqual(response.status_code, 400)
                        write.assert_not_called()
                        html = response.get_data(as_text=True)
                        self.assertIn(f'id="application-{error_field}-error" class="auth-error">{message}', html)
                        self.assertIn(f'name="closing_date" type="date" value="{closing}"', html)
                        self.assertIn(f'name="applied_at" type="date" value="{applied}"', html)
                        self.assertIn('value="Test developer"', html)

    def test_tracker_shares_cutoff_and_keeps_interview_date_and_legacy_application(self):
        self.database.applications[501].update(
            status="Interview", applied_at=date(2026, 7, 14), closing_date=date(2026, 7, 13),
            notes="Existing notes")
        response = self.client.post(self.url, data=self.form | {"date": "2026-07-10"})
        self.assertEqual(response.status_code, 302)
        with patch("tracker_data._now_utc", return_value=datetime(2026, 7, 9, 23)) as clock:
            response = self.client.get("/tracker")
        clock.assert_called_once_with()
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        next_step = html.split('<td data-col="next">', 1)[1].split("</td>", 1)[0]
        self.assertIn("Interview", next_step)
        self.assertIn("Jul 10, 2026", next_step)
        self.assertIn('theme-warning">3 days left</span>', next_step)
        self.assertIn('id="details-501-history"', html)
        self.assertIn("Existing notes", html)

    def test_create_and_edit_still_require_csrf_and_edit_requires_ownership(self):
        with (patch("services.application_service.operation_session",
                    side_effect=self.database.interview_operation),
              patch("services.application_service._write") as write):
            for url in ("/applications/new", "/applications/501/edit"):
                with self.subTest(url=url):
                    self.assertEqual(self.client.post(url).status_code, 400)
            response = self.client.post("/applications/502/edit", data={"csrf_token": "synthetic-token"})
            self.assertEqual(response.status_code, 404)
            write.assert_not_called()
