"""Synthetic request/database lifecycle coverage; no configuration files or hosting."""
from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from sqlalchemy.dialects import mysql as mysql_dialect
from orm_db import TransactionConflict
from db import DatabaseError
import mysql.connector as connector
from datetime import datetime, date
import importlib
import os
import unittest

from auth_fixtures import AuthDatabaseFixture
from services import interview_service as service
from services.interview_time import parse_dublin_datetime, utc_to_dublin
import tracker_data


class InterviewDatabase(AuthDatabaseFixture):
    """Synthetic ORM boundary for route regressions; no real database or SQL parser."""
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
        self.fail_read = False
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

    @contextmanager
    def interview_operation(self, *, read_only=False, error_message=None):
        if self.connection.in_transaction or self.connection.autocommit is not False:
            raise TransactionConflict('Finish caller work first')
        session = MagicMock()
        pending = []
        session.add.side_effect = pending.append
        def flush():
            for record in pending:
                if self.fail:
                    raise connector.Error('private write details')
                identity = len(self.interviews) + 901
                record.interview_id = identity
                app = self.applications[record.application_id]
                self.interviews[identity] = {c.name: getattr(record, c.name) for c in record.__table__.columns} | dict(job_title=app['job_title'], company=app['company'])
                self.queries.append(('create', (record.scheduled_at, record.interview_type, record.venue_or_link, record.notes, record.outcome, record.application_id, app['user_id'])))
            pending.clear()
        def execute(statement, **options):
            params = statement.compile(dialect=mysql_dialect.dialect()).params
            result = MagicMock()
            row = None
            if statement.is_select:
                if self.fail_read:
                    raise connector.Error('private read details')
                if 'interview_id' in statement.selected_columns.keys():
                    if 'interview_id_1' in params:
                        kind = 'owned' + (' FOR UPDATE' if statement._for_update_arg is not None else '')
                        parameters = (params['interview_id_1'], params['user_id_1'])
                        candidate = self.interviews.get(parameters[0])
                        row = deepcopy(candidate) if candidate and self.applications[candidate['application_id']]['user_id'] == parameters[1] else None
                    else:
                        kind = 'list'
                        cutoff = params['cutoff']
                        parameters = (params['user_id_1'], cutoff, cutoff, cutoff)
                        values = [deepcopy(r) for r in self.interviews.values() if self.applications[r['application_id']]['user_id'] == parameters[0]]
                        future = sorted((r for r in values if r['scheduled_at'] >= cutoff), key=lambda r:(r['scheduled_at'],r['interview_id']))
                        past = sorted((r for r in values if r['scheduled_at'] < cutoff), key=lambda r:r['interview_id'])
                        past.sort(key=lambda r:r['scheduled_at'], reverse=True)
                        result.mappings.return_value.all.return_value = future+past
                elif 'application_id_1' in params:
                    kind = 'application' + (' FOR UPDATE' if statement._for_update_arg is not None else '')
                    parameters = (params['application_id_1'],params['user_id_1'])
                    candidate = self.applications.get(parameters[0])
                    row = deepcopy(candidate) if candidate and candidate['user_id']==parameters[1] else None
                else:
                    kind, parameters = 'applications', (params['user_id_1'],)
                    result.mappings.return_value.all.return_value = [deepcopy(r) for r in self.applications.values() if r['user_id']==parameters[0]]
                result.mappings.return_value.one_or_none.return_value = row
            else:
                if self.fail:
                    raise connector.Error('private write details')
                identity, owner = params['interview_id_1'], params['user_id_1']
                row = self.interviews.get(identity)
                owned = row is not None and self.applications[row['application_id']]['user_id']==owner
                values = {k:v for k,v in params.items() if k not in ('interview_id_1','user_id_1')}
                kind = 'delete' if statement.is_delete else ('notes' if set(values)=={'notes'} else 'update')
                fields = ('scheduled_at','interview_type','venue_or_link','notes','outcome')
                parameters = ((identity, owner) if kind=='delete' else
                              ((values['notes'], identity,owner) if kind=='notes' else tuple(values[f] for f in fields)+(identity,owner)))
                result.rowcount = int(owned)
                if owned:
                    if kind=='delete': del self.interviews[identity]
                    else:
                        result.rowcount = int(any(row.get(k)!=v for k,v in values.items()))
                        row.update(values)
            self.queries.append((kind, parameters))
            return result
        session.execute.side_effect = execute
        session.flush.side_effect = flush
        try:
            if not read_only:
                self.connection.start_transaction()
            yield session
            if read_only: self.connection.rollback()
            else: self.connection.commit()
        except BaseException as error:
            self.connection.rollback()
            if isinstance(error, connector.Error):
                raise DatabaseError(error_message or 'Synthetic database failure') from None
            raise
        finally:
            session.close()


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

        for target in ('services.interview_service.operation_session', 'tracker_data.operation_session'):
            p = patch(target, side_effect=self.database.interview_operation)
            p.start(); self.addCleanup(p.stop)
        auth_provider = patch('services.auth_service.operation_session', side_effect=self.database.auth_operation)
        auth_provider.start(); self.addCleanup(auth_provider.stop)
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
        self.assertEqual(sum(q == 'list' for q, _ in self.database.queries), 1)
        tracker_response = self.client.get('/tracker')
        self.assertNotIn(b'id="interviews-table"', tracker_response.data)
        self.assertIn(self.url.encode(), tracker_response.data)
        self.assertNotIn(b'Other role', response.data)
        self.assertEqual(sum(q == 'list' for q, _ in self.database.queries), 2)
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
        self.database.connection.commit.side_effect = connector.Error('synthetic secret')
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
