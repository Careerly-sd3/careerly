"""Edit/delete requests through real services and a synthetic shared connection."""
from copy import deepcopy
from datetime import datetime
import re
import unittest
from unittest.mock import patch

import mysql.connector

import test_add_interview as create_tests
from services import interview_service as service


class EditDeleteInterviewTests(unittest.TestCase):
    tearDown = create_tests.AddInterviewTests.tearDown

    def setUp(self):
        create_tests.AddInterviewTests.setUp(self)
        self.database.interviews = {
            901: dict(interview_id=901, application_id=501, scheduled_at=datetime(2026, 7, 9, 13, 30, 12),
                      interview_type='technical', venue_or_link='Room <one>', notes='<script>synthetic</script>',
                      outcome='Pending', company='Synthetic <company>', job_title='Synthetic <role>'),
            902: dict(interview_id=902, application_id=502, scheduled_at=datetime(2026, 1, 9, 13, 30),
                      interview_type='phone', venue_or_link=None, notes=None, outcome=None,
                      company='Other company', job_title='Other role'),
        }
        self.edit = '/interviews/901/edit'
        self.delete = '/interviews/901/delete'
        self.form = self.form | dict(time='14:30:12', interview_type='technical',
                                    venue_or_link='Room <one>', notes='<script>synthetic</script>', outcome='Pending')

    def test_prefill_every_field_and_escaped_context(self):
        response = self.client.get(self.edit)
        self.assertEqual(response.status_code, 200)
        for value in (b'value="2026-07-09"', b'value="14:30:12"', b'value="Room &lt;one&gt;"',
                      b'&lt;script&gt;synthetic&lt;/script&gt;', b'Synthetic &lt;company&gt;',
                      b'value="technical" selected', b'value="Pending" selected', b'Edit interview'):
            self.assertIn(value, response.data)
        self.assertNotIn(b'<script>synthetic</script>', response.data)
        self.database.connection.commit.assert_not_called()

    def test_unchanged_edit_zero_rowcount_succeeds(self):
        original = deepcopy(self.database.interviews)
        response = self.client.post(self.edit, data=self.form)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/tracker'))
        self.assertEqual(self.database.interviews, original)
        self.database.connection.commit.assert_called_once()
        self.database.connection.start_transaction.assert_called_once()
        self.assertFalse(self.database.connection.in_transaction)
        self.assertIn(b'Interview updated.', self.client.get('/tracker').data)

    def test_changes_outcomes_and_ignores_association_tampering(self):
        foreign_before = deepcopy(self.database.interviews[902])
        for outcome in ('Passed', 'Failed', 'Pending'):
            response = self.client.post(self.edit, data=self.form | dict(
                date='2026-01-09', time='10:15', interview_type='onsite', venue_or_link='New room',
                notes='New notes', outcome=outcome, application_id='502', user_id='102', interview_id='902'))
            self.assertEqual(response.status_code, 302)
            row = self.database.interviews[901]
            self.assertEqual(row['scheduled_at'], datetime(2026, 1, 9, 10, 15))
            self.assertEqual(row['application_id'], 501)
            self.assertEqual((row['interview_type'], row['venue_or_link'], row['notes'], row['outcome']),
                             ('onsite', 'New room', 'New notes', outcome))
            self.assertIn(outcome.encode(), self.client.get('/tracker').data)
        self.assertEqual(self.database.applications[501]['status'], 'Applied')
        self.assertIsNone(self.database.interviews[902]['outcome'])
        self.assertEqual(self.database.interviews[902], foreign_before)

    def test_invalid_fields_and_dst_do_not_change_record(self):
        before = deepcopy(self.database.interviews)
        for changes in ({'date': ''}, {'time': ''}, {'time': 'bad'}, {'date': 'bad'},
                        {'date': '2026-02-30'}, {'interview_type': 'Other'},
                        {'outcome': 'passed'}, {'venue_or_link': 'x' * 501}, {'notes': 'x' * 65536},
                        {'date': '2026-03-29', 'time': '01:30'}, {'date': '2026-10-25', 'time': '01:30'}):
            with self.subTest(changes=list(changes)):
                response = self.client.post(self.edit, data=self.form | changes)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b'Please correct', response.data)
                self.assertNotIn(b'<script>synthetic</script>', response.data)
                self.assertEqual(self.database.interviews, before)
        self.database.connection.commit.assert_not_called()

    def test_missing_foreign_ids_and_anonymous_requests(self):
        for identity in (0, 902, 999, 999999999999):
            for action in ('edit', 'delete'):
                url = f'/interviews/{identity}/{action}'
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, data=self.form).status_code, 404)
        client = self.module.app.test_client()
        for url in (self.edit, self.delete):
            self.assertEqual(client.get(url).status_code, 302)
            self.assertEqual(client.post(url, data=self.form).status_code, 302)
        self.database.connection.commit.assert_not_called()

    def test_csrf_required_for_both_mutations(self):
        before = deepcopy(self.database.interviews)
        for url in (self.edit, self.delete):
            without_token = {key: value for key, value in self.form.items() if key != 'csrf_token'}
            self.assertEqual(self.client.post(url, data=without_token).status_code, 400)
            for token in ('', 'wrong'):
                self.assertEqual(self.client.post(url, data=self.form | {'csrf_token': token}).status_code, 400)
        self.assertEqual(self.database.interviews, before)
        self.database.connection.start_transaction.assert_not_called()

    def test_foreign_posts_with_rendered_valid_csrf_reach_ownership_and_never_write(self):
        response = self.client.get(self.edit)
        self.assertEqual(response.status_code, 200)
        token = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)
        with self.client.session_transaction() as session:
            self.assertEqual(token, session['_csrf_token'])
        before_interviews = deepcopy(self.database.interviews)
        before_applications = deepcopy(self.database.applications)
        for url, ownership_query, parameters in (
            ('/applications/502/interviews/new', service.APPLICATION_QUERY, (502, 101)),
            ('/interviews/902/edit', service.OWNED_INTERVIEW_QUERY, (902, 101)),
            ('/interviews/902/delete', service.OWNED_INTERVIEW_QUERY, (902, 101)),
        ):
            with self.subTest(url=url):
                self.database.queries.clear()
                response = self.client.post(url, data=self.form | {
                    'csrf_token': token, 'user_id': '102', 'application_id': '501', 'interview_id': '901'})
                self.assertEqual(response.status_code, 404)  # CSRF rejection would be 400.
                self.assertIn((ownership_query, parameters), self.database.queries)
                self.assertFalse(any(query in (service.CREATE_QUERY, service.UPDATE_QUERY, service.DELETE_QUERY)
                                     for query, _ in self.database.queries))
                self.database.connection.start_transaction.assert_not_called()
                self.database.connection.commit.assert_not_called()
                self.assertEqual(self.database.interviews, before_interviews)
                self.assertEqual(self.database.applications, before_applications)

    def test_confirmation_cancel_and_post_delete(self):
        before = deepcopy(self.database.interviews)
        # GET parameters cannot turn confirmation into a write.
        response = self.client.get(self.delete + '?confirm=true')
        self.assertEqual(response.status_code, 200)
        for value in (b'Synthetic &lt;company&gt;', b'Synthetic &lt;role&gt;', b'09 July 2026, 14:30:12',
                      b'Europe/Dublin', b'method="post"', b'action="/interviews/901/delete"',
                      b'name="csrf_token"', b'Confirm delete', b'href="/tracker">Cancel'):
            self.assertIn(value, response.data)
        self.client.get('/tracker')  # Follow Cancel, which is a GET link.
        self.assertEqual(self.database.interviews, before)
        self.database.connection.commit.assert_not_called()
        response = self.client.post(self.delete, data={'csrf_token': 'synthetic-token',
                                                     'interview_id': '902', 'application_id': '502'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(901, self.database.interviews)
        self.assertIn(902, self.database.interviews)
        self.database.connection.commit.assert_called_once()
        self.assertIn(b'Interview deleted.', self.client.get('/tracker').data)

    def test_display_actions_target_owned_record(self):
        response = self.client.get('/tracker')
        self.assertIn(b'href="/interviews/901/edit"', response.data)
        self.assertIn(b'href="/interviews/901/delete"', response.data)
        self.assertNotIn(b'/interviews/902/', response.data)
        self.assertIn(b'data-application-id="501"', response.data)

    def test_edit_winter_and_summer_round_trips(self):
        for stored, clock in ((datetime(2026, 1, 9, 13, 30, 12), '13:30:12'),
                              (datetime(2026, 7, 9, 13, 30, 12), '14:30:12')):
            self.database.interviews[901]['scheduled_at'] = stored
            response = self.client.get(self.edit)
            self.assertIn(f'value="{clock}"'.encode(), response.data)
            response = self.client.post(self.edit, data=self.form | {
                'date': stored.strftime('%Y-%m-%d'), 'time': clock})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.database.interviews[901]['scheduled_at'], stored)

    def test_other_http_methods_cannot_delete(self):
        before = deepcopy(self.database.interviews)
        for method in ('DELETE', 'PUT', 'PATCH'):
            response = self.client.open(self.delete, method=method, data=self.form)
            self.assertEqual(response.status_code, 405)
        self.assertEqual(self.database.interviews, before)
        self.database.connection.commit.assert_not_called()

    def test_legacy_null_is_pending_without_writing(self):
        self.database.interviews[901]['outcome'] = None
        self.assertIn(b'value="Pending" selected', self.client.get(self.edit).data)
        self.assertIn(b'Pending', self.client.get('/tracker').data)
        self.assertIsNone(self.database.interviews[901]['outcome'])
        self.database.connection.commit.assert_not_called()

    def test_write_and_commit_failures_rollback_and_never_flash_success(self):
        before = deepcopy(self.database.interviews)
        for url in (self.edit, self.delete):
            for stage in ('write', 'commit'):
                with self.subTest(url=url, stage=stage):
                    self.database.fail = stage == 'write'
                    self.database.connection.commit.side_effect = (
                        mysql.connector.Error('synthetic internal SQL/password details')
                        if stage == 'commit' else self.database.commit)
                    response = self.client.post(url, data=self.form | {'outcome': 'Failed'})
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn(b'internal SQL/password', response.data)
                    self.assertEqual(self.database.interviews, before)
                    self.assertFalse(self.database.connection.in_transaction)
                    with self.client.session_transaction() as session:
                        self.assertFalse(any(category == 'success' for category, _ in session.get('_flashes', [])))

    def test_recheck_missing_after_preflight_rolls_back(self):
        for url in (self.edit, self.delete):
            with self.subTest(url=url):
                original = service.get_interview
                record = deepcopy(self.database.interviews[901])

                def disappear(*args):
                    row = original(*args)
                    del self.database.interviews[901]
                    return row

                with patch('services.interview_service.get_interview', side_effect=disappear):
                    self.assertEqual(self.client.post(url, data=self.form).status_code, 404)
                self.database.connection.commit.assert_not_called()
                self.assertFalse(self.database.connection.in_transaction)
                self.database.interviews[901] = record
