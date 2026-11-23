import unittest
from unittest.mock import patch

import test_add_interview


class FollowUpFormTests(unittest.TestCase):
    setUp = test_add_interview.AddInterviewTests.setUp
    tearDown = test_add_interview.AddInterviewTests.tearDown

    def test_follow_up_validation_and_retained_values_on_both_forms(self):
        form = dict(csrf_token='synthetic-token', company='Test company', job_title='Developer',
                    opportunity_type='internship', status='Applied')
        saved = dict(self.database.applications[501], follow_up_date=None)
        for url in ('/applications/new', '/applications/501/edit'):
            for applied, follow_up, error_field, message in (
                ('2026-07-10', '2026-07-09', 'follow_up_date', 'Follow-up date cannot be earlier than applied date.'),
                ('2026-07-10', '2026-02-30', 'follow_up_date', 'Use a valid date (YYYY-MM-DD).'),
                ('2026-02-30', '2026-07-09', 'applied_at', 'Use a valid date (YYYY-MM-DD).'),
                ('2026-07-10', '2026-07-10', None, ''),
                ('2026-07-10', '2026-07-11', None, ''),
                ('2026-07-10', '', None, ''),
                ('', '2026-07-09', None, ''),
            ):
                with (self.subTest(url=url, applied=applied, follow_up=follow_up),
                      patch('services.application_service.get_application', return_value=saved),
                      patch('services.application_service._write') as write):
                    response = self.client.post(url, data=form | dict(applied_at=applied, follow_up_date=follow_up))
                    self.assertEqual(response.status_code, 400 if error_field else 302)
                    if error_field:
                        write.assert_not_called()
                        self.assertIn(f'name="follow_up_date" type="date" value="{follow_up}"'.encode(), response.data)
                        self.assertIn(f'name="applied_at" type="date" value="{applied}"'.encode(), response.data)
                        self.assertIn(f'id="application-{error_field}-error" class="auth-error">{message}'.encode(),
                                      response.data)
                        if error_field == 'applied_at':
                            self.assertNotIn(b'id="application-follow_up_date-error"', response.data)
                    else:
                        write.assert_called_once()
