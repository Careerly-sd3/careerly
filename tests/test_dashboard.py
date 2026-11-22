"""Tests for dashboard route and dashboard service."""
import unittest
from datetime import date, datetime
from unittest.mock import patch
from db import DatabaseError
import test_add_interview as base_tests
from services import dashboard_service
from services.interview_time import utc_to_dublin


class UpcomingOrderingTests(unittest.TestCase):
    def test_same_day_follow_ups_and_interviews_across_dublin_clock_change(self):
        cutoff = datetime(2026, 10, 24, 23)
        day = date(2026, 10, 25)
        first = utc_to_dublin(datetime(2026, 10, 25, 0, 50))
        second = utc_to_dublin(datetime(2026, 10, 25, 1, 10))
        interviews = [
            dict(interview_id=12, scheduled_at=second),
            dict(interview_id=11, scheduled_at=second),
            dict(interview_id=13, scheduled_at=first),
        ]
        follow_ups = [dict(application_id=8, follow_up_date=day), dict(application_id=7, follow_up_date=day)]
        with (patch.object(dashboard_service, "_upcoming_interviews", return_value=interviews) as scheduled,
              patch.object(dashboard_service, "list_upcoming_follow_ups", return_value=follow_ups) as dated):
            upcoming = dashboard_service._upcoming(None, 101, cutoff)
        scheduled.assert_called_once_with(None, 101, cutoff)
        dated.assert_called_once_with(None, 101, day)
        self.assertEqual([item["kind"] for item in upcoming], ["follow_up"] * 2 + ["interview"] * 3)
        self.assertEqual([item["application_id"] for item in upcoming[:2]], [7, 8])
        self.assertEqual([item["interview_id"] for item in upcoming[2:]], [13, 11, 12])


class DashboardTests(unittest.TestCase):
    setUp = base_tests.AddInterviewTests.setUp
    tearDown = base_tests.AddInterviewTests.tearDown

    def test_dashboard_requires_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get('/dashboard')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers.get('Location', ''))

    def test_dashboard_renders_successfully(self):
        mock_data = {
            "total_applications": 5,
            "total_interviews": 2,
            "offers": 1,
            "offer_rate": 20,
            "status_data": [
                {"status": "Saved", "count": 1},
                {"status": "Applied", "count": 2},
                {"status": "Interview", "count": 1},
                {"status": "Offer", "count": 1},
                {"status": "Rejected", "count": 0},
                {"status": "Withdrawn", "count": 0},
            ],
            "apps_per_week": [],
            "interview_outcomes": [
                {"outcome": "Passed", "count": 1},
                {"outcome": "Failed", "count": 0},
                {"outcome": "Pending", "count": 1},
            ],
            "interview_types": [{"interview_type": "online", "cnt": 2}],
            "top_companies": [{"company": "Acme Corp", "cnt": 3}],
            "response_rate": {"responded": 2, "total": 4, "rate": 50},
            "upcoming": [
                {"kind": "follow_up", "job_title": "Follow-up role", "company": "Acme Corp",
                 "follow_up_date": date(2026, 7, 10), "application_link": "/tracker#details-501"},
                {"kind": "interview", "job_title": "Interview role", "company": "Acme Corp",
                 "scheduled_at": datetime(2026, 7, 10, 14), "interview_type": "online"},
            ],
            "recent_applications": [],
            "work_type_breakdown": [{"work_type": "Remote", "cnt": 5}],
        }
        with patch.object(dashboard_service, "load_dashboard", return_value=mock_data):
            response = self.client.get('/dashboard')
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Dashboard", response.data)
            self.assertIn(b"Acme Corp", response.data)
            self.assertIn(b'href="/tracker#details-501">Follow-up role', response.data)
            self.assertIn(b"Interview role", response.data)
            self.assertIn(b"14:00", response.data)

    def test_dashboard_handles_database_error(self):
        with patch.object(
            dashboard_service,
            "load_dashboard",
            side_effect=DatabaseError("Could not load your dashboard data. Please try again later."),
        ):
            response = self.client.get('/dashboard')
            self.assertEqual(response.status_code, 503)
            self.assertIn(b"Could not load your dashboard data", response.data)
