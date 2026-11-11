"""Mocked listing requests; SQL ordering is asserted, not executed on MySQL."""
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_add_interview as create_tests
from services import interview_service as service
import tracker_data


class InterviewRows(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr' and 'data-interview-id' in attrs:
            self.ids.append(int(attrs['data-interview-id']))


class InterviewListingTests(unittest.TestCase):
    setUp = create_tests.AddInterviewTests.setUp
    tearDown = create_tests.AddInterviewTests.tearDown

    def seed(self):
        self.cutoff = datetime(2026, 7, 9, 12)
        # Deliberately unordered: cutoff-inclusive, future ties, past ties and a foreign row.
        for identity, seconds, application in [(9, -60, 501), (4, 60, 501), (8, -60, 501),
                                               (3, 0, 501), (2, 60, 501), (7, -120, 501),
                                               (1, 0, 502)]:
            self.database.interviews[identity] = dict(interview_id=identity, application_id=application,
                scheduled_at=self.cutoff + timedelta(seconds=seconds), interview_type='online',
                venue_or_link='https://example.invalid/meeting?a=1&b=2', notes='<script>notes</script>',
                outcome=None, job_title=f'Role {identity}', company='Synthetic company')

    def test_order_cutoff_single_query_and_rendering(self):
        self.seed()
        with patch('tracker_data.datetime') as clock, patch('tracker_data.list_interviews', wraps=service.list_interviews) as listing:
            clock.now.return_value = self.cutoff.replace(tzinfo=timezone.utc)
            response = self.client.get('/interviews')
            clock.now.assert_called_once_with(timezone.utc)
            listing.assert_called_once_with(101, cutoff=self.cutoff)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(InterviewRows(response.get_data(as_text=True)).ids, [3, 2, 4, 8, 9, 7])
        queries = [(query, parameters) for query, parameters in self.database.queries if query == service.LIST_QUERY]
        self.assertEqual(queries, [(service.LIST_QUERY, (101, self.cutoff, self.cutoff, self.cutoff))])
        for clause in ('WHERE a.user_id = %s',
                       'CASE WHEN i.scheduled_at >= %s THEN 0 ELSE 1 END ASC',
                       'CASE WHEN i.scheduled_at >= %s THEN i.scheduled_at END ASC',
                       'CASE WHEN i.scheduled_at < %s THEN i.scheduled_at END DESC', 'i.interview_id ASC'):
            self.assertIn(clause, service.LIST_QUERY)
        for value in (b'<table id="interviews-table"', b'Europe/Dublin', b'1:00 PM', b'<details class="interview-notes">',
                      b'notes-closed-label">View notes</span>', b'&lt;script&gt;notes&lt;/script&gt;',
                      b'href="https://example.invalid/meeting?a=1&amp;b=2"', b'Pending',
                      b'href="/interviews/3/edit"', b'href="/interviews/3/delete"', b'tabindex="0"'):
            self.assertIn(value, response.data)
        self.assertNotIn(b'<script>notes</script>', response.data)
        self.assertNotIn(b'/interviews/1/edit', response.data)
        self.assertIsNone(self.database.interviews[3]['outcome'])

    def test_transform_preserves_input_order_and_ids(self):
        self.seed()
        supplied = [self.database.interviews[identity] for identity in (9, 3, 2)]
        rows = tracker_data._interview_rows(supplied)
        self.assertEqual([row['interview_id'] for row in rows], [9, 3, 2])
        self.assertEqual([row['scheduled_at_utc'] for row in rows], [row['scheduled_at'] for row in supplied])
        self.assertEqual(rows[1]['scheduled_at'].hour, 13)

    def test_application_filters_do_not_filter_interviews_or_counts(self):
        self.seed()
        self.database.applications[503] = self.database.applications[501] | dict(
            application_id=503, company='Another company', job_title='Another role', status='Saved')
        for query in ('company=Missing', 'role=Missing', 'status=Rejected',
                      'company=Synthetic+company&role=Synthetic+role&status=Applied'):
            response = self.client.get('/tracker?' + query)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(InterviewRows(response.get_data(as_text=True)).ids, [])
            listing = self.client.get('/interviews?' + query)
            self.assertEqual(set(InterviewRows(listing.get_data(as_text=True)).ids), {2, 3, 4, 7, 8, 9})
            if 'Missing' in query or 'Rejected' in query:
                self.assertIn(b'No applications match these filters', response.data)
            else:
                self.assertIn(b'/applications/501/interviews/new', response.data)
                self.assertNotIn(b'/applications/503/interviews/new', response.data)
        page = tracker_data.load_tracker_page
        with patch('tracker_data._fetch_all', return_value=list(self.database.applications.values())[:1]), patch('tracker_data.list_interviews', return_value=[]):
            data = page(101, status='Rejected')
        self.assertEqual(data['applications'], [])
        self.assertEqual(data['total_applications'], 1)
        self.assertEqual(data['status_counts']['Applied'], 1)

    def test_authenticated_navigation_and_empty_state(self):
        response = self.client.get('/interviews')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.location)
        response = self.client.get('/interviews', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'No interviews scheduled yet.', response.data)
        self.assertIn(b'<table id="interviews-table"', response.data)
        self.assertEqual(InterviewRows(response.get_data(as_text=True)).ids, [])
        anonymous = self.module.app.test_client().get('/interviews')
        self.assertEqual(urlsplit(anonymous.location).path, '/login')
        self.assertEqual(parse_qs(urlsplit(anonymous.location).query), {'next': ['/interviews']})

    def test_second_persisted_user_sees_only_their_interviews(self):
        self.seed()
        with self.client.session_transaction() as session:
            session['_user_id'] = '102'
        response = self.client.get('/interviews', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(InterviewRows(response.get_data(as_text=True)).ids, [1])
        self.assertIn(b'/interviews/1/edit', response.data)
        self.assertNotIn(b'/interviews/3/edit', response.data)

    def test_only_absolute_http_https_links_clickable(self):
        for value in ('javascript:alert(1)', 'data:text/html,synthetic', '//example.invalid',
                      'ftp://example.invalid', 'https://', 'https://example.invalid:bad',
                      'https://example.invalid\\evil', 'https://example.invalid/\nnext', 'Office room'):
            self.assertIsNone(tracker_data._location_url(value), value)
        for value in ('http://example.invalid', 'https://example.invalid/meeting', 'HTTPS://example.invalid'):
            self.assertEqual(tracker_data._location_url(value), value)
