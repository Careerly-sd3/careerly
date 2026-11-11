"""Relocation regressions: real requests/services, synthetic database only."""
from datetime import datetime, timezone
import re
import unittest
from unittest.mock import patch

from db import DatabaseError
from services import interview_service as service
import test_edit_delete_interview as edit_tests
import tracker_data


class InterviewsPageTests(unittest.TestCase):
    setUp = edit_tests.EditDeleteInterviewTests.setUp
    tearDown = edit_tests.EditDeleteInterviewTests.tearDown

    def test_listing_reads_only_owner_interviews_once_without_application_query(self):
        response = self.client.get('/interviews')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Location', response.headers)
        self.assertIn(b'data-interview-id="901"', response.data)
        self.assertNotIn(b'data-interview-id="902"', response.data)
        queries = [query for query, _ in self.database.queries]
        self.assertEqual(queries.count(service.LIST_QUERY), 1)
        self.assertNotIn(tracker_data.APPLICATIONS_QUERY, queries)
        self.assertNotIn(b'id="practice"', response.data)

    def test_tracker_retains_application_entry_point_next_step_and_practice(self):
        self.database.applications[501]['status'] = 'Interview'
        with patch('tracker_data.datetime') as clock:
            clock.now.return_value = datetime(2026, 7, 1, tzinfo=timezone.utc)
            response = self.client.get('/tracker')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'/applications/501/interviews/new', response.data)
        self.assertIn(b'id="practice"', response.data)
        self.assertIn(b'id="practice-meta"', response.data)
        self.assertIn(b'Jul 09, 2026', response.data)
        self.assertNotIn(b'id="interviews-table"', response.data)
        self.assertIn(b'class="card interview-card"', response.data)
        self.assertIn(b'data-interview-id="901"', response.data)
        self.assertIn(b'href="/interviews/901/edit"', response.data)
        self.assertIn(b'href="/interviews/901/delete"', response.data)
        self.assertNotIn(b'/interviews/902/', response.data)
        self.assertIn(b'details-501-history', response.data)
        self.assertIn(b'details-501-notes', response.data)

    def test_cancel_destinations_and_no_return_url_override(self):
        cases = ((self.url, b'href="/tracker"'), (self.edit, b'href="/interviews"'),
                 (self.delete, b'href="/interviews"'))
        for url, destination in cases:
            with self.subTest(url=url):
                response = self.client.get(url + '?next=https://example.invalid&return_url=/other')
                self.assertEqual(response.status_code, 200)
                self.assertIn(destination + b'>Cancel</a>', response.data)
        self.database.connection.commit.assert_not_called()

    def test_navigation_and_active_state(self):
        for url, active in (('/tracker', '/tracker'), ('/interviews', '/interviews'),
                            (self.edit, '/interviews'), (self.delete, '/interviews'), (self.url, '/tracker')):
            with self.subTest(url=url):
                response = self.client.get(url)
                html = response.get_data(as_text=True)
                links = re.findall(r'<a\b[^>]*aria-current="page"[^>]*>', html)
                self.assertEqual(len(links), 1)
                self.assertIn(f'href="{active}"', links[0])
                self.assertIn('btn-solid', links[0])
                self.assertIn('href="/interviews"', html)
                self.assertNotIn('/tracker#interviews', html)

    def test_listing_failure_is_styled_and_generic(self):
        with patch('app.load_interviews_page', side_effect=DatabaseError('private internal details')):
            response = self.client.get('/interviews')
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'Service temporarily unavailable', response.data)
        self.assertNotIn(b'private internal details', response.data)

    def test_notes_use_native_disclosure_with_decorative_document_icon_and_escaped_text(self):
        response = self.client.get('/interviews')
        html = response.get_data(as_text=True)
        disclosure = re.search(r'<details class="interview-notes">(.*?)</details>', html, re.S).group(1)
        self.assertIn('<summary class="btn btn-subtle btn-sm theme-secondary interview-notes-toggle" aria-controls="notes-panel-901" aria-expanded="false">', disclosure)
        self.assertIn('bi-file-earmark-text" aria-hidden="true"', disclosure)
        self.assertIn('notes-closed-label">View notes</span>', disclosure)
        self.assertIn('notes-open-label">Hide notes</span>', disclosure)
        self.assertIn('&lt;script&gt;synthetic&lt;/script&gt;', disclosure)
        self.assertNotIn('<script>', disclosure)
        self.assertNotIn('tabindex="-1"', disclosure)
        self.assertNotIn('onclick=', disclosure)

    def test_standalone_listing_requires_login(self):
        response = self.module.app.test_client().get('/interviews')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login?next=%2Finterviews'))
