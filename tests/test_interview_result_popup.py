""""How did it go?" popup: Offer / Rejected / Withdrawn set the application status."""
from datetime import datetime
import unittest
from unittest.mock import patch

from services import interview_service
import test_add_interview
import tracker_data


def _card(interview_id, application_id, outcome="Pending", is_past=True):
    return dict(interview_id=interview_id, application_id=application_id, outcome=outcome, is_past=is_past)


class ResultValidationTests(unittest.TestCase):
    def test_only_offer_rejected_or_withdrawn_are_accepted(self):
        for answer in ("Passed", "Failed", "Applied", "", None):
            with self.subTest(answer=answer), \
                    patch("services.interview_service.operation_session") as session:
                with self.assertRaises(interview_service.InterviewValidationError) as raised:
                    interview_service.record_result(101, 901, answer)
                self.assertIn("status", raised.exception.errors)
                session.assert_not_called()

    def test_each_answer_maps_to_an_interview_outcome(self):
        self.assertEqual(interview_service.RESULT_STATUSES,
                         {"Offer": "Passed", "Rejected": "Failed", "Withdrawn": None})


class AwaitingResultsTests(unittest.TestCase):
    def test_finished_applications_are_not_asked_about(self):
        applications = [dict(application_id=1, status="Interview"), dict(application_id=2, status="Offer"),
                        dict(application_id=3, status="Rejected"), dict(application_id=4, status="Withdrawn")]
        cards = [_card(11, 1), _card(12, 2), _card(13, 3), _card(14, 4),
                 _card(15, 1, outcome="Passed"), _card(16, 1, is_past=False)]
        awaiting = tracker_data._awaiting_results(cards, applications)
        self.assertEqual([card["interview_id"] for card in awaiting], [11])


class ResultRouteTests(unittest.TestCase):
    setUp = test_add_interview.AddInterviewTests.setUp
    tearDown = test_add_interview.AddInterviewTests.tearDown

    def test_saves_the_chosen_status_and_returns_to_the_page(self):
        for status in ("Offer", "Rejected", "Withdrawn"):
            with self.subTest(status=status), patch("services.interview_service.record_result") as record:
                response = self.client.post("/interviews/901/result", data=dict(
                    csrf_token="synthetic-token", status=status, next="/dashboard"))
            record.assert_called_once_with(101, 901, status)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/dashboard"))

    def test_unknown_answer_is_a_bad_request(self):
        response = self.client.post("/interviews/901/result",
                                    data=dict(csrf_token="synthetic-token", status="Passed"))
        self.assertEqual(response.status_code, 400)

    def test_popup_offers_the_three_final_statuses(self):
        self.database.applications[501]["status"] = "Interview"
        with patch("tracker_data._awaiting_results", return_value=[dict(
                interview_id=901, company="Synthetic company", job_title="Synthetic role",
                scheduled_at=datetime(2026, 7, 1, 10))]):
            html = self.client.get("/tracker").get_data(as_text=True)
        popup = html[html.index("interview-result-popup"):]
        for status in ("Offer", "Rejected", "Withdrawn"):
            self.assertIn(f'name="status" value="{status}"', popup)
        self.assertNotIn('value="Passed"', popup)


if __name__ == "__main__":
    unittest.main()
