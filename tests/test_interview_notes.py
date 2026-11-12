"""Notes-only writes via real Flask/service code and a synthetic connection."""
from copy import deepcopy
import re
import unittest
from unittest.mock import patch

import mysql.connector

from db import DatabaseError
from services import interview_service as service
import test_edit_delete_interview as edit_tests


class InterviewNotesTests(unittest.TestCase):
    setUp = edit_tests.EditDeleteInterviewTests.setUp
    tearDown = edit_tests.EditDeleteInterviewTests.tearDown

    def post(self, **fields):
        return self.client.post('/interviews/901/notes', data={'csrf_token': 'synthetic-token'} | fields)

    def test_save_changes_only_notes_and_preserves_exact_text(self):
        self.database.interviews[901]['outcome'] = None
        before = deepcopy(self.database.interviews)
        notes = "\n  literal </textarea><script>synthetic</script>\r\n  trailing spaces  "
        response = self.post(notes=notes, scheduled_at='wrong', interview_type='phone',
                             venue_or_link='changed', outcome='Failed', application_id='502', user_id='102')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'ok': True, 'interview_id': 901, 'notes': notes})
        self.assertNotIn('Location', response.headers)
        expected = before[901] | {'notes': notes}
        self.assertEqual(self.database.interviews[901], expected)
        self.assertEqual(self.database.interviews[902], before[902])
        queries = self.database.queries
        self.assertIn(('notes', (notes, 901, 101)), queries)
        self.database.connection.commit.assert_called_once()
        self.database.connection.start_transaction.assert_called_once()
        page = self.client.get('/interviews')
        self.assertIn(b'&lt;/textarea&gt;&lt;script&gt;synthetic&lt;/script&gt;', page.data)
        self.assertNotIn(b'<script>synthetic</script>', page.data)

    def test_empty_add_clear_and_unchanged_saves(self):
        self.database.interviews[901]['notes'] = None
        for notes in ('', '  new note  ', '  new note  ', ''):
            with self.subTest(notes=notes):
                response = self.post(notes=notes)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['notes'], notes)
                self.assertEqual(self.database.interviews[901]['notes'], notes)
        self.assertEqual(self.database.connection.commit.call_count, 4)

    def test_foreign_and_missing_valid_csrf_reach_ownership_without_writes(self):
        page = self.client.get('/interviews')
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        before = deepcopy(self.database.interviews)
        for identity in (902, 999, 0, 999999999999):
            self.database.queries.clear()
            response = self.client.post(f'/interviews/{identity}/notes',
                data={'csrf_token': token, 'notes': 'x' * 65536, 'user_id': '102'})
            self.assertEqual(response.status_code, 404)
            if identity in (902, 999):
                self.assertIn(('owned', (identity, 101)), self.database.queries)
            self.assertFalse(any(query == 'notes' for query, _ in self.database.queries))
        self.assertEqual(self.database.interviews, before)
        self.database.connection.start_transaction.assert_not_called()
        self.database.connection.commit.assert_not_called()

    def test_logged_out_and_csrf_rejection_do_not_write(self):
        before = deepcopy(self.database.interviews)
        client = self.module.app.test_client()
        response = client.post('/interviews/901/notes', data={'csrf_token': 'synthetic-token', 'notes': 'changed'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login?', response.location)
        for data in ({'notes': 'changed'}, {'notes': 'changed', 'csrf_token': ''},
                     {'notes': 'changed', 'csrf_token': 'wrong'}):
            self.assertEqual(self.client.post('/interviews/901/notes', data=data).status_code, 400)
        self.assertEqual(self.database.interviews, before)
        self.database.connection.commit.assert_not_called()

    def test_missing_invalid_and_boundary_notes(self):
        before = deepcopy(self.database.interviews)
        for data in ({}, {'notes': 'x' * 65536}, {'notes': '\u00e9' * 32768}):
            response = self.post(**data)
            self.assertEqual(response.status_code, 400)
            self.assertIn('notes', response.json['errors'])
            self.assertEqual(self.database.interviews, before)
        self.database.connection.commit.assert_not_called()
        self.assertEqual(self.post(notes='x' * 65535).status_code, 200)

    def test_write_commit_and_read_failures_are_generic_and_rollback(self):
        before = deepcopy(self.database.interviews)
        for stage in ('write', 'commit', 'read'):
            with self.subTest(stage=stage):
                self.database.fail = stage == 'write'
                self.database.connection.commit.side_effect = (
                    mysql.connector.Error('private sql credentials') if stage == 'commit' else self.database.commit)
                if stage == 'read':
                    with patch('services.interview_service.get_interview', side_effect=DatabaseError('private read detail')):
                        response = self.post(notes='changed')
                else:
                    response = self.post(notes='changed')
                self.assertEqual(response.status_code, 503)
                self.assertFalse(response.json['ok'])
                self.assertNotIn(b'private', response.data)
                self.assertEqual(self.database.interviews, before)
                self.assertFalse(self.database.connection.in_transaction)

    def test_disappearing_parent_recheck_and_post_only(self):
        original = service.get_interview
        def disappear(*args):
            row = original(*args)
            del self.database.interviews[901]
            return row
        with patch('services.interview_service.get_interview', side_effect=disappear):
            self.assertEqual(self.post(notes='changed').status_code, 404)
        self.database.connection.commit.assert_not_called()
        self.assertEqual(self.client.get('/interviews/902/notes').status_code, 405)

    def test_service_guard_and_validation_preserve_caller_work(self):
        self.database.connection.in_transaction = True
        with patch('services.interview_service.operation_session', side_effect=self.database.interview_operation), self.assertRaises(service.InterviewTransactionError):
            service.update_interview_notes(101, 901, notes='changed')
        self.database.connection.rollback.assert_not_called()
        self.database.connection.commit.assert_not_called()
        self.database.connection.start_transaction.assert_not_called()
        for notes in (123, '\ud800', 'x' * 65536):
            with self.assertRaises(service.InterviewValidationError):
                service.update_interview_notes(101, 901, notes=notes)

    def test_direct_service_foreign_owner_never_updates(self):
        before = deepcopy(self.database.interviews)
        with patch('services.interview_service.operation_session', side_effect=self.database.interview_operation), self.assertRaises(service.InterviewNotFound):
            service.update_interview_notes(102, 901, notes='changed')
        self.assertEqual(self.database.queries, [('owned' + ' FOR UPDATE', (901, 102))])
        self.assertEqual(self.database.interviews, before)
        self.database.connection.rollback.assert_called_once()
        self.database.connection.commit.assert_not_called()

    def test_editors_exist_for_blank_notes_with_unique_labels_and_status_regions(self):
        # Give the current user a second owned row without touching real records.
        self.database.interviews[903] = self.database.interviews[901] | {'interview_id': 903, 'notes': None}
        html = self.client.get('/interviews').get_data(as_text=True)
        for identity in (901, 903):
            self.assertIn(f'action="/interviews/{identity}/notes"', html)
            self.assertIn(f'for="interview-notes-{identity}"', html)
            self.assertEqual(html.count(f'id="interview-notes-{identity}"'), 1)
            self.assertEqual(html.count(f'id="notes-status-{identity}"'), 1)
        self.assertIn('role="status" aria-live="polite" aria-atomic="true"', html)
        self.assertIn('/static/js/interview-notes.js', html)

    def test_panel_starts_readonly_with_hidden_editor_and_text_actions(self):
        html = self.client.get('/interviews').get_data(as_text=True)
        form = re.search(r'<form class="interview-notes-editor".*?</form>', html, re.S).group(0)
        self.assertIn('class="visually-hidden" for="interview-notes-901"', form)
        self.assertIn('aria-describedby="notes-status-901"', form)
        self.assertNotIn('Optional. Saves notes only.', form)
        self.assertNotIn('notes-help-', form)
        self.assertIn('class="notes-panel-heading"', form)
        self.assertIn('bi-file-earmark-text" aria-hidden="true"', form)
        self.assertIn('data-notes-view>', form)
        self.assertIn('data-notes-editor hidden>', form)
        self.assertIn('bi-pencil" aria-hidden="true"', form)
        self.assertIn('Edit notes</button>', form)
        self.assertIn('aria-label="Save notes" disabled>Save</button>', form)
        self.assertIn('aria-label="Cancel changes">Cancel</button>', form)
        self.assertNotIn('bi-check-lg', form)
        self.assertNotIn('bi-x-lg', form)
        self.assertIn('data-notes-save', form)
        self.assertIn('data-notes-cancel', form)

    def test_inline_panel_has_unique_control_relationship_and_starts_hidden(self):
        html = self.client.get('/interviews').get_data(as_text=True)
        self.assertIn('aria-controls="notes-panel-901" aria-expanded="false"', html)
        self.assertEqual(html.count('id="notes-panel-901"'), 1)
        self.assertIn('id="notes-panel-901" hidden', html)
        self.assertNotIn('popover=', html)
        self.assertIn('class="notes-toggle-label"', html)

    def test_saved_multiline_and_empty_notes_are_rendered_as_text(self):
        notes = "Recruiter: Synthetic person.\nAsk about the team.\n\n- Revise graphs\n<script>literal</script>"
        self.database.interviews[901]['notes'] = notes
        html = self.client.get('/interviews').get_data(as_text=True)
        self.assertIn('data-notes-text>Recruiter: Synthetic person.\nAsk about the team.\n\n- Revise graphs\n&lt;script&gt;literal&lt;/script&gt;</div>', html)
        self.assertNotIn('<script>literal</script>', html)
        self.database.interviews[901]['notes'] = None
        html = self.client.get('/interviews').get_data(as_text=True)
        self.assertIn('data-notes-text>No notes yet.</div>', html)
