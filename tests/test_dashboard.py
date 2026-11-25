"""Tests for dashboard route and dashboard service."""
import unittest
from datetime import datetime
from unittest.mock import patch
from db import DatabaseError
import test_add_interview as base_tests
from services import dashboard_service


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
                {"job_title": "Interview role", "company": "Acme Corp",
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
