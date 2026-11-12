"""Mocked outages, diagnostics and responsive merge contract; no live database."""
from datetime import datetime, timedelta
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import mysql.connector
from sqlalchemy.exc import OperationalError
import orm_db

from db import DatabaseError
import test_add_interview as create_tests
import tracker_data


class MainIntegrationTests(unittest.TestCase):
    setUp = create_tests.AddInterviewTests.setUp
    tearDown = create_tests.AddInterviewTests.tearDown

    def test_startup_requires_a_configured_nonempty_secret(self):
        for environment in ({}, {'SECRET_KEY': ''}, {'SECRET_KEY': '   '}):
            with self.subTest(environment_keys=list(environment)), patch.dict(os.environ, environment, clear=True):
                spec = importlib.util.spec_from_file_location('synthetic_startup', Path(self.module.__file__))
                module = importlib.util.module_from_spec(spec)
                with self.assertRaisesRegex(RuntimeError, 'SECRET_KEY must be configured'):
                    spec.loader.exec_module(module)

    def test_session_outage_keeps_public_pages_renderable_and_protected_pages_styled(self):
        with patch('app.get_user_by_id', side_effect=DatabaseError('private connection detail')):
            for url in ('/', '/login'):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'Your session has been preserved', response.data)
                self.assertNotIn(b'private connection detail', response.data)
                with self.client.session_transaction() as session:
                    self.assertEqual(session['_user_id'], '101')
            for url in ('/tracker', '/interviews', self.url, '/interviews/901/edit', '/interviews/901/delete'):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 503)
                self.assertIn(b'/static/css/style.css', response.data)
                self.assertIn(b'Service temporarily unavailable', response.data)
                self.assertNotIn(b'private connection detail', response.data)
            response = self.client.post('/login', data={'email': 'synthetic@example.invalid', 'password': 'synthetic'})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b'Email or password is incorrect', response.data)
        # No new login/session is needed after the same database recovers.
        response = self.client.get('/tracker')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Synthetic 101', response.data)
        self.assertIn(b'/applications/501/interviews/new', response.data)
        self.database.connection.commit.assert_not_called()

    def test_anonymous_public_pages_do_not_require_database(self):
        client = self.module.app.test_client()
        with patch('app.get_user_by_id') as loader:
            for url in ('/', '/login'):
                self.assertEqual(client.get(url).status_code, 200)
            loader.assert_not_called()

    def test_tracker_failure_logs_stack_without_driver_message_or_parameters(self):
        # Exercise the production translation path, not a fixture that has
        # already stripped the sensitive driver/SQL parameter values.
        with patch('tracker_data.operation_session', orm_db.operation_session), patch('orm_db.Session') as factory:
            factory.return_value.execute.side_effect = OperationalError(
                'SELECT private_user_101', {'MYSQL_PASSWORD': 'private-value'},
                mysql.connector.Error('mysql://name:password@host private-value'))
            with self.module.app.app_context(), self.assertLogs('tracker_data', level='ERROR') as captured:
                with self.assertRaises(DatabaseError):
                    tracker_data.load_tracker_page(101)
        factory.return_value.rollback.assert_called_once()
        factory.return_value.close.assert_called_once()
        output = '\n'.join(captured.output)
        self.assertIn('Tracker query failed', output)
        self.assertIn('Traceback (most recent call last)', output)
        self.assertIn('Database read failed.', output)
        for value in ('private-value', 'mysql://', 'private_user_101', 'MYSQL_PASSWORD'):
            self.assertNotIn(value, output)
        record = captured.records[0]
        self.assertEqual(record.args, ())
        self.assertIsNotNone(record.exc_info[2])

    def test_practice_uses_only_upcoming_at_shared_utc_cutoff(self):
        cutoff = datetime(2026, 7, 9, 12)
        past = dict(scheduled_at_utc=cutoff-timedelta(seconds=1), scheduled_at=cutoff,
                    company='Past company', job_title='Past role')
        future = dict(scheduled_at_utc=cutoff, scheduled_at=cutoff+timedelta(hours=1),
                      company='Upcoming company', job_title='Upcoming role')
        self.assertEqual(tracker_data._practice_meta([past], cutoff), '')
        self.assertIn('Upcoming role', tracker_data._practice_meta([future, past], cutoff))
        response = self.client.get('/tracker')
        self.assertNotIn(b'id="practice-meta"', response.data)
        self.assertIn(b'class="card practice-card g-col-12 md:g-col-6"', response.data)

    def test_responsive_template_keeps_semantic_actions_and_external_link_rel(self):
        self.client.post(self.url, data=self.form | {'venue_or_link': 'https://example.invalid/meeting'})
        response = self.client.get('/tracker')
        self.assertIn(b'/static/css/tracker.css', response.data)
        self.assertIn(b'class="tracker-header"', response.data)
        self.assertIn(b'data-col="actions"', response.data)
        self.assertNotIn(b'id="interviews-table"', response.data)
        response = self.client.get('/interviews')
        self.assertIn(b'data-col="scheduled"', response.data)
        self.assertIn(b'data-label="Date / time (Europe/Dublin)"', response.data)
        self.assertIn(b'id="interviews"', response.data)
        self.assertIn(b'/interviews/901/edit', response.data)
        self.assertIn(b'/interviews/901/delete', response.data)
        self.assertIn(b'href="https://example.invalid/meeting" rel="noopener noreferrer"', response.data)
        self.assertIn(b'action="/logout"', response.data)
        self.assertNotIn(b'sm:d-inline', response.data)
        self.assertNotIn(b'<style>', response.data)
