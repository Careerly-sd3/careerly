"""Synthetic request/database lifecycle coverage; no configuration files or hosting."""
from copy import deepcopy
from datetime import datetime, date
import importlib
import os
import unittest
from unittest.mock import patch

import mysql.connector
from flask import g

from auth_fixtures import AuthDatabaseFixture
from services import auth_service as auth, interview_service as service
from services.interview_time import parse_dublin_datetime, utc_to_dublin
import tracker_data


class InterviewDatabase(AuthDatabaseFixture):
    def __init__(self):
        super().__init__()
        self.applications = {
            501: dict(application_id=501, user_id=101, company_id=1, company='Synthetic company',
                      job_title='Synthetic role', status='Applied', applied_at=None, closing_date=None),
            502: dict(application_id=502, user_id=102, company_id=1, company='Other company',
                      job_title='Other role', status='Saved', applied_at=None, closing_date=None),
        }
        self.interviews = {}
        self.saved = None
        self.fail = False
        self.queries = []

    def start(self):
        super().start()
        self.saved = deepcopy(self.interviews)

    def commit(self):
        super().commit()
        self.saved = None

    def rollback(self):
        super().rollback()
        if self.saved is not None:
            self.interviews = self.saved
        self.saved = None

    def cursor(self, **options):
        cursor = super().cursor(**options)
        auth_execute = cursor.execute.side_effect

        def execute(query, parameters):
            self.queries.append((query, parameters))
            if query in (auth.USER_BY_ID, auth.USER_BY_EMAIL, auth.CREATE_USER):
                return auth_execute(query, parameters)
            self.connection.in_transaction = True
            if query in (service.APPLICATION_QUERY, service.APPLICATION_QUERY + ' FOR UPDATE'):
                row = self.applications.get(parameters[0])
                cursor.fetchone.return_value = deepcopy(row) if row and row['user_id'] == parameters[1] else None
            elif query == service.CREATE_QUERY:
                if self.fail:
                    raise mysql.connector.Error('synthetic internal SQL/password details')
                when, kind, venue, notes, outcome, app_id, owner = parameters
                assert self.applications[app_id]['user_id'] == owner
                identity = len(self.interviews) + 901
                self.interviews[identity] = dict(interview_id=identity, application_id=app_id,
                    scheduled_at=when, interview_type=kind, venue_or_link=venue, notes=notes, outcome=outcome,
                    company=self.applications[app_id]['company'], job_title=self.applications[app_id]['job_title'])
                cursor.rowcount, cursor.lastrowid = 1, identity
            elif query in (service.OWNED_INTERVIEW_QUERY, service.OWNED_INTERVIEW_QUERY + ' FOR UPDATE'):
                row = self.interviews.get(parameters[0])
                cursor.fetchone.return_value = deepcopy(row) if row and self.applications[row['application_id']]['user_id'] == parameters[1] else None
            elif query in (service.UPDATE_QUERY, service.DELETE_QUERY, service.NOTES_QUERY):
                if self.fail:
                    raise mysql.connector.Error('synthetic internal SQL/password details')
                identity, owner = parameters[-2:]
                row = self.interviews.get(identity)
                if not row or self.applications[row['application_id']]['user_id'] != owner:
                    cursor.rowcount = 0
                elif query == service.DELETE_QUERY:
                    del self.interviews[identity]
                    cursor.rowcount = 1
                elif query == service.NOTES_QUERY:
                    cursor.rowcount = int(row['notes'] != parameters[0])
                    row['notes'] = parameters[0]
                else:
                    fields = ('scheduled_at', 'interview_type', 'venue_or_link', 'notes', 'outcome')
                    values = dict(zip(fields, parameters[:-2]))
                    cursor.rowcount = int(any(row[key] != value for key, value in values.items()))
                    row.update(values)
            elif query == service.LIST_QUERY:
                assert parameters[1] == parameters[2] == parameters[3]
                rows = [deepcopy(row) for row in self.interviews.values()
                        if self.applications[row['application_id']]['user_id'] == parameters[0]]
                upcoming = sorted((row for row in rows if row['scheduled_at'] >= parameters[1]),
                                  key=lambda row: (row['scheduled_at'], row['interview_id']))
                past = sorted((row for row in rows if row['scheduled_at'] < parameters[1]),
                              key=lambda row: row['interview_id'])
                past.sort(key=lambda row: row['scheduled_at'], reverse=True)
                cursor.fetchall.return_value = upcoming + past
            elif query == tracker_data.APPLICATIONS_QUERY:
                cursor.fetchall.return_value = [deepcopy(row) for row in self.applications.values() if row['user_id'] == parameters[0]]
            else:
                raise AssertionError('Unexpected SQL')
        cursor.execute.side_effect = execute
        return cursor


class AddInterviewTests(unittest.TestCase):
    def setUp(self):
        for p in (patch.dict(os.environ, {'SECRET_KEY': 'synthetic-test-key'}, clear=True),
                  patch('dotenv.load_dotenv', return_value=False)):
            p.start(); self.addCleanup(p.stop)
        network = patch('mysql.connector.connect', side_effect=AssertionError('No network'))
        self.network = network.start(); self.addCleanup(network.stop)
        self.module = importlib.import_module('app')
        config = patch.dict(self.module.app.config, TESTING=True, SECRET_KEY='synthetic-test-key')
        config.start(); self.addCleanup(config.stop)
        self.database = InterviewDatabase()
        self.database.rows = {identity: dict(user_id=identity, name=f'Synthetic {identity}',
            email=f'{identity}@example.invalid', password_hash='not-a-real-hash', created_at=datetime(2026, 1, 1))
            for identity in (101, 102)}

        def provider():
            # Exercise the actual context cleanup on the same connection used by
            # user_loader, preflight SELECT, guarded write and tracker reads.
            g.mysql_connection = self.database.connection
            return self.database.connection
        for target in ('services.auth_service.get_db', 'services.interview_service.get_db', 'tracker_data.get_db'):
            p = patch(target, side_effect=provider); p.start(); self.addCleanup(p.stop)
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = '101'; session['_fresh'] = True
            session['_csrf_token'] = 'synthetic-token'
        self.url = '/applications/501/interviews/new'
        self.form = dict(csrf_token='synthetic-token', date='2026-07-09', time='14:30',
                         interview_type='online', venue_or_link='', notes='')

    def tearDown(self):
        self.network.assert_not_called()

    def test_get_and_save_real_service_lifecycle_and_tracker(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Synthetic role', response.data)
        self.assertIn(b'Europe/Dublin', response.data)
        response = self.client.post(self.url, data=self.form | {'user_id': '102', 'application_id': '502', 'interview_id': '999'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/interviews'))
        row = self.database.interviews[901]
        self.assertEqual(row['application_id'], 501)
        self.assertEqual(row['scheduled_at'], datetime(2026, 7, 9, 13, 30))
        self.assertEqual(row['outcome'], 'Pending')
        self.database.connection.start_transaction.assert_called_once()
        self.database.connection.commit.assert_called_once()
        self.assertFalse(self.database.connection.in_transaction)
        response = self.client.get('/interviews')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Interview added.', response.data)
        self.assertIn(b'2:30 PM', response.data)
        self.assertIn(b'data-interview-id="901"', response.data)
        self.assertEqual(sum(q == service.LIST_QUERY for q, _ in self.database.queries), 1)
        tracker_response = self.client.get('/tracker')
        self.assertNotIn(b'id="interviews-table"', tracker_response.data)
        self.assertIn(self.url.encode(), tracker_response.data)
        self.assertNotIn(b'Other role', response.data)
        self.assertEqual(sum(q == service.LIST_QUERY for q, _ in self.database.queries), 2)
        self.assertEqual(self.database.applications[501]['status'], 'Applied')

    def test_invalid_fields_preserved_and_no_write(self):
        for field, value in [('date', ''), ('time', ''), ('date', '2026-02-30'), ('date', 'bad'),
                             ('time', '25:99'), ('interview_type', 'Online'), ('outcome', 'Unknown'),
                             ('venue_or_link', 'x' * 501), ('notes', 'x' * 65536)]:
            with self.subTest(field=field):
                response = self.client.post(self.url, data=self.form | {field: value, 'notes': '<script>synthetic</script>'} if field != 'notes' else self.form | {field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(('interview-' + field + '-error').encode(), response.data)
                self.assertNotIn(b'<script>synthetic</script>', response.data)
        self.assertEqual(self.database.interviews, {})
        self.database.connection.commit.assert_not_called()

    def test_csrf_and_owner_checks(self):
        without_token = {key: value for key, value in self.form.items() if key != 'csrf_token'}
        self.assertEqual(self.client.post(self.url, data=without_token).status_code, 400)
        for token in ('', 'wrong'):
            self.assertEqual(self.client.post(self.url, data=self.form | {'csrf_token': token}).status_code, 400)
        for app_id in (0, 502, 999, 999999999999):
            url = f'/applications/{app_id}/interviews/new'
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, data={'csrf_token': 'synthetic-token'}).status_code, 404)
        self.database.connection.commit.assert_not_called()

    def test_anonymous_redirect(self):
        client = self.module.app.test_client()
        self.assertEqual(client.get(self.url).status_code, 302)
        self.assertEqual(client.post(self.url, data=self.form).status_code, 302)

    def test_failure_rolls_back_without_internal_details(self):
        self.database.fail = True
        response = self.client.post(self.url, data=self.form)
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'Could not save', response.data)
        self.assertNotIn(b'internal SQL/password', response.data)
        self.assertEqual(self.database.interviews, {})
        self.database.connection.commit.assert_not_called()
        self.assertFalse(self.database.connection.in_transaction)

    def test_commit_failure_rolls_back(self):
        self.database.connection.commit.side_effect = mysql.connector.Error('synthetic secret')
        response = self.client.post(self.url, data=self.form)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.database.interviews, {})
        self.assertNotIn(b'synthetic secret', response.data)

    def test_ownership_read_failure_is_generic_and_does_not_save(self):
        with patch('services.interview_service.get_owned_application',
                   side_effect=service.DatabaseError('synthetic internal detail')):
            response = self.client.post(self.url, data=self.form)
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'Could not load the application', response.data)
        self.assertNotIn(b'synthetic internal detail', response.data)
        self.database.connection.start_transaction.assert_not_called()

    def test_parent_disappearing_before_locked_recheck_returns_404(self):
        original = service.get_owned_application

        def remove_after_read(*args):
            result = original(*args)
            del self.database.applications[501]
            return result

        with patch('services.interview_service.get_owned_application', side_effect=remove_after_read):
            response = self.client.post(self.url, data=self.form)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.database.interviews, {})
        self.database.connection.commit.assert_not_called()
        self.assertFalse(self.database.connection.in_transaction)

    def test_past_allowed_and_display_escaped(self):
        response = self.client.post(self.url, data=self.form | {'date': '2020-01-01', 'notes': '<script>synthetic</script>',
            'venue_or_link': 'javascript:alert("synthetic")', 'outcome': 'Passed'})
        self.assertEqual(response.status_code, 302)
        response = self.client.get('/interviews')
        self.assertIn(b'&lt;script&gt;synthetic&lt;/script&gt;', response.data)
        self.assertNotIn(b'href="javascript:', response.data)
        self.assertIn(b'Passed', response.data)

    def test_dst_edges_rejected_in_form(self):
        for day in ('2026-03-29', '2026-10-25'):
            response = self.client.post(self.url, data=self.form | {'date': day, 'time': '01:30'})
            self.assertEqual(response.status_code, 400)
            self.assertIn(b'interview-scheduled_at-error', response.data)
        self.assertEqual(self.database.interviews, {})

    def test_empty_application_state(self):
        self.database.applications.clear()
        response = self.client.get('/tracker')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Interviews are added from an existing application.', response.data)


class InterviewTimeTests(unittest.TestCase):
    def test_winter_summer_and_rollover(self):
        for day, clock, expected in [('2026-01-09', '14:30', datetime(2026, 1, 9, 14, 30)),
            ('2026-07-09', '14:30', datetime(2026, 7, 9, 13, 30)),
            ('2026-07-09', '00:30', datetime(2026, 7, 8, 23, 30))]:
            result = parse_dublin_datetime(day, clock)
            self.assertEqual(result, expected)
            self.assertEqual(utc_to_dublin(result).strftime('%Y-%m-%d %H:%M'), f'{day} {clock}')

    def test_gap_and_overlap_explicit(self):
        for day, message in [('2026-03-29', 'does not exist'), ('2026-10-25', 'occurs twice')]:
            with self.assertRaises(service.InterviewValidationError) as error:
                parse_dublin_datetime(day, '01:30')
            self.assertIn(message, error.exception.errors['scheduled_at'])
            for clock in ('00:30', '02:30'):
                self.assertIsInstance(parse_dublin_datetime(day, clock), datetime)

    def test_date_fields_not_timezone_converted(self):
        day = date(2026, 7, 9)
        row = dict(application_id=1, company='Synthetic', job_title='Synthetic', status='Saved',
                   closing_date=day, applied_at=day)
        display = tracker_data._application_rows([row], [])[0]
        self.assertIs(display['applied_at'], day)
        self.assertIs(display['next_date'], day)
        self.assertEqual(display['application_id'], 1)
