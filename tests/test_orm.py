"""Offline ORM contract tests. MySQL compilation does not prove live semantics."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import re
import runpy
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask, g
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

import db
import models
import orm_db
from services import application_service as service
import test_add_interview as route_fixture


class MappingTests(unittest.TestCase):
    def test_all_columns_match_released_migration(self):
        sql = (Path(__file__).resolve().parents[1] / 'database/migrations/001_initial_schema.sql').read_text()
        tables = re.findall(r'CREATE TABLE `(\w+)` \((.*?)\) ENGINE=', sql, re.S)
        self.assertEqual(len(tables), 6)
        self.assertEqual(sum(len(t.columns) for t in models.Base.metadata.tables.values()), 44)
        self.assertEqual(set(models.Base.metadata.tables), {name for name, _ in tables})
        for name, body in tables:
            table = models.Base.metadata.tables[name]
            expected = re.findall(r'^  `(\w+)` (INT|VARCHAR\(\d+\)|TEXT|DATETIME|DATE)([^,\n]*)', body, re.M)
            self.assertEqual(list(table.columns.keys()), [c[0] for c in expected])
            for column_name, kind, suffix in expected:
                column = table.c[column_name]
                self.assertEqual(str(column.type.compile(dialect=mysql.dialect())).replace('INTEGER', 'INT'), kind)
                self.assertEqual(column.nullable, 'NOT NULL' not in suffix)
                self.assertIsNone(column.default)
                self.assertIsNone(column.onupdate)
                self.assertIsNone(column.server_onupdate)
                self.assertEqual(column.primary_key, 'AUTO_INCREMENT' in suffix)
                if column.primary_key:
                    self.assertIs(column.autoincrement, True)
                if kind == 'DATETIME':
                    self.assertEqual(column.type.fsp, 0)
                expected_default = 'Saved' if (name, column_name) == ('JobApplication', 'status') else None
                self.assertEqual(column.server_default.arg if column.server_default is not None else None, expected_default)
                if kind == 'INT':
                    self.assertNotIn('UNSIGNED', column.type.compile(dialect=mysql.dialect()))
            for option, value in [('engine', 'InnoDB'), ('charset', 'utf8mb4'), ('collate', 'utf8mb4_0900_ai_ci')]:
                self.assertEqual(table.dialect_options['mysql'][option], value)
            unique = {c.name: tuple(c.columns.keys()) for c in table.constraints if isinstance(c, UniqueConstraint)}
            expected_unique = {n: tuple(re.findall(r'`(\w+)`', cols)) for n, cols in re.findall(r'CONSTRAINT `(\w+)` UNIQUE \((.*?)\)', body)}
            self.assertEqual(unique, expected_unique)
            actual_fk = {(c.name, tuple(c.column_keys), tuple(e.target_fullname for e in c.elements), c.ondelete) for c in table.constraints if isinstance(c, ForeignKeyConstraint)}
            expected_fk = {(n, (local,), (parent+'.'+remote,), delete) for n, local, parent, remote, delete in re.findall(r'CONSTRAINT `(\w+)` FOREIGN KEY \(`(\w+)`\)\s+REFERENCES `(\w+)` \(`(\w+)`\) ON DELETE (\w+)', body)}
            self.assertEqual(actual_fk, expected_fk)
            checks = {c.name: re.sub(r'\s+', '', str(c.sqltext)) for c in table.constraints if isinstance(c, CheckConstraint)}
            expected_checks = {n: re.sub(r'\s+', '', expr) for n, expr in re.findall(r'CONSTRAINT `(\w+)` CHECK \((.*?)(?=,\n  CONSTRAINT|\Z)', body, re.S)}
            expected_checks = {n: expr[:-1] for n, expr in expected_checks.items()}
            self.assertEqual(checks, expected_checks)

    def test_imports_and_engine_are_lazy(self):
        with patch('mysql.connector.connect', side_effect=AssertionError('No network')) as connect:
            runpy.run_path(str(Path(models.__file__)))
            with Flask(__name__).app_context():
                engine = orm_db.get_engine()
                self.assertIsInstance(engine.pool, NullPool)
                self.assertTrue(engine.hide_parameters)
                self.assertFalse(engine.echo)
                self.assertTrue(engine.logger.disabled)
                self.assertTrue(engine.pool.logger.disabled)
                self.assertIs(engine, orm_db.get_engine())
            connect.assert_not_called()


class TransactionTests(unittest.TestCase):
    def setUp(self):
        context = Flask(__name__).app_context()
        context.push()
        self.addCleanup(context.pop)
        self.factory = patch('orm_db.Session').start()
        self.addCleanup(patch.stopall)
        self.session = self.factory.return_value
        self.engine = patch('orm_db.get_engine', return_value=MagicMock()).start()

    def test_success_and_explicit_options(self):
        with orm_db.operation_session() as session:
            self.assertIs(session, self.session)
            self.session.commit.assert_not_called()
        self.assertEqual(self.factory.call_args.kwargs, dict(autobegin=False, autoflush=False, expire_on_commit=False))
        self.assertEqual([c[0] for c in self.session.mock_calls], ['begin', 'commit', 'close'])
        self.assertFalse(g.get('orm_operation_active', False))

    def test_failure_commit_failure_and_interruption_cleanup(self):
        for stage in ('body', 'begin', 'commit'):
            self.session.reset_mock()
            self.session.begin.side_effect = None
            self.session.commit.side_effect = None
            error = OperationalError('private SQL', {'password': 'private'}, Exception('private'))
            if stage != 'body':
                getattr(self.session, stage).side_effect = error
            with self.assertRaises(db.DatabaseError) as caught:
                with orm_db.operation_session():
                    if stage == 'body':
                        raise error
            self.assertNotIn('private', str(caught.exception))
            self.session.rollback.assert_called_once()
            self.session.close.assert_called_once()
            self.assertFalse(g.get('orm_operation_active', False))
        self.session.begin.side_effect = self.session.commit.side_effect = None
        with self.assertRaises(KeyboardInterrupt):
            with orm_db.operation_session():
                raise KeyboardInterrupt()
        self.assertFalse(g.get('orm_operation_active', False))

    def test_legacy_refusal_does_not_touch_caller(self):
        for active, autocommit in ((True, False), (False, True)):
            legacy = MagicMock(in_transaction=active, autocommit=autocommit)
            g.mysql_connection = legacy
            with self.assertRaises(orm_db.TransactionConflict):
                with orm_db.operation_session():
                    self.fail('entered')
            legacy.commit.assert_not_called()
            legacy.rollback.assert_not_called()
            legacy.close.assert_not_called()
        self.factory.assert_not_called()
        self.engine.assert_not_called()

    def test_orm_blocks_legacy_and_nested_orm_then_releases(self):
        legacy = MagicMock(in_transaction=False, autocommit=False)
        g.mysql_connection = legacy
        with orm_db.operation_session():
            with self.assertRaises(db.DatabaseError):
                db.get_db()
            with self.assertRaises(orm_db.TransactionConflict):
                with orm_db.operation_session():
                    self.fail('nested entry')
            legacy.rollback.assert_not_called()
            legacy.close.assert_not_called()
        self.assertIs(db.get_db(), legacy)

    def test_rollback_and_close_failures_are_safe_and_release_guard(self):
        self.session.rollback.side_effect = RuntimeError('private')
        with self.assertRaisesRegex(db.DatabaseError, 'Rollback failed'):
            with orm_db.operation_session():
                raise ValueError('private')
        self.session.close.assert_called_once()
        self.assertFalse(g.get('orm_operation_active', False))
        self.session.rollback.side_effect = None
        self.session.close.side_effect = RuntimeError('private')
        with self.assertRaisesRegex(db.DatabaseError, 'cleanup failed'):
            with orm_db.operation_session():
                pass
        self.assertFalse(g.get('orm_operation_active', False))

    def test_verified_creator_finishes_only_its_new_connection(self):
        with patch('orm_db.create_verified_connection') as factory:
            self.assertIs(orm_db._orm_connection(), factory.return_value)
            factory.assert_called_once_with(orm=True)
            factory.return_value.rollback.assert_called_once()
            factory.return_value.close.assert_not_called()
            factory.return_value.rollback.side_effect = RuntimeError('private')
            with self.assertRaises(db.DatabaseError):
                orm_db._orm_connection()
            factory.return_value.close.assert_called_once()

    def test_creator_preserves_interrupt_and_sanitises_close_failure(self):
        with patch('orm_db.create_verified_connection') as factory:
            connection = factory.return_value
            connection.rollback.side_effect = KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                orm_db._orm_connection()
            connection.close.assert_called_once()
            connection.rollback.side_effect = RuntimeError('private')
            connection.close.side_effect = RuntimeError('private close')
            with self.assertRaises(db.DatabaseError) as caught:
                orm_db._orm_connection()
            self.assertNotIn('private', str(caught.exception))



class NotesTests(unittest.TestCase):
    setUp = route_fixture.AddInterviewTests.setUp
    tearDown = route_fixture.AddInterviewTests.tearDown

    def session_patch(self, identity=501):
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = identity
        @contextmanager
        def operation():
            yield session
        patcher = patch('services.application_service.operation_session', operation)
        patcher.start()
        self.addCleanup(patcher.stop)
        return session

    def test_owner_lock_and_only_notes_timestamp_updated(self):
        session = self.session_patch()
        now = datetime(2026, 11, 12, 10, 0, 0)
        saved = service.update_notes(101, 501, '\r\nhello\r\nworld  ', now=now)
        self.assertEqual(saved, dict(notes='hello\nworld', updated_at=now))
        lock = session.execute.call_args_list[0].args[0].compile(dialect=mysql.dialect())
        write = session.execute.call_args_list[1].args[0].compile(dialect=mysql.dialect())
        self.assertIn('FOR UPDATE', str(lock))
        for query in (lock, write):
            self.assertEqual(query.params['application_id_1'], 501)
            self.assertEqual(query.params['user_id_1'], 101)
        set_clause = str(write).split(' SET ')[1].split(' WHERE ')[0]
        self.assertEqual(set_clause, 'notes=%s, updated_at=%s')
        self.assertEqual(write.params['notes'], 'hello\nworld')
        self.assertEqual(write.params['updated_at'], now)

    def test_blank_unchanged_and_default_utc(self):
        session = self.session_patch()
        for notes in ('  ', '', 'same', 'same'):
            saved = service.update_notes(101, 501, notes)
            self.assertEqual(saved['notes'], notes if notes.strip() else None)
            self.assertIsNone(saved['updated_at'].tzinfo)
            self.assertEqual(saved['updated_at'].microsecond, 0)
        self.assertEqual(session.execute.call_count, 8)

    def test_missing_foreign_never_mutates(self):
        session = self.session_patch(None)
        for identity in (501, 999):
            session.reset_mock()
            with self.assertRaises(service.ApplicationNotFound):
                service.update_notes(101, identity, 'changed')
            self.assertEqual(session.execute.call_count, 1)

    def test_validation_never_enters_orm(self):
        with patch('services.application_service.operation_session') as operation:
            for notes in (None, 123, '\ud800', 'x'*65536):
                with self.assertRaises(service.ApplicationValidationError):
                    service.update_notes(101, 501, notes)
            operation.assert_not_called()

    def test_route_contract_csrf_ownership_and_errors(self):
        session = self.session_patch()
        url = '/applications/501/notes'
        response = self.client.post(url, data={'csrf_token': 'synthetic-token', 'notes': 'hello', 'user_id': 102})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['notes'], 'hello')
        lock = session.execute.call_args_list[0].args[0].compile(dialect=mysql.dialect())
        self.assertEqual(lock.params['user_id_1'], 101)
        session.reset_mock()
        self.assertEqual(self.client.post(url, data={'notes': 'x'}).status_code, 400)
        session.execute.assert_not_called()
        self.assertEqual(self.client.post(url, data={'csrf_token': 'synthetic-token', 'notes': 'x'*65536}).status_code, 400)
        session.execute.assert_not_called()
        session.execute.return_value.scalar_one_or_none.return_value = None
        self.assertEqual(self.client.post(url, data={'csrf_token': 'synthetic-token', 'notes': 'x'}).status_code, 404)
        session.execute.side_effect = db.DatabaseError('private')
        response = self.client.post(url, data={'csrf_token': 'synthetic-token', 'notes': 'x'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.get_data(as_text=True))
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.module.app.test_client().post(url, data={'notes': 'x'}).status_code, 302)

    def test_service_transaction_conflict_contract(self):
        with self.module.app.app_context():
            legacy = MagicMock(in_transaction=True, autocommit=False)
            g.mysql_connection = legacy
            with self.assertRaises(service.ApplicationTransactionError):
                service.update_notes(101, 501, 'x')
            legacy.rollback.assert_not_called()
            legacy.commit.assert_not_called()
            legacy.close.assert_not_called()

    def test_real_operation_boundary_route_commit_failure_is_503(self):
        with patch('orm_db.get_engine', return_value=MagicMock()), patch('orm_db.Session') as factory:
            session = factory.return_value
            session.execute.return_value.scalar_one_or_none.return_value = 501
            url = '/applications/501/notes'
            response = self.client.post(url, data={'csrf_token': 'synthetic-token', 'notes': 'hello'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([c[0] for c in session.mock_calls], [
                'begin', 'execute', 'execute().scalar_one_or_none', 'execute', 'commit', 'close'])
            session.reset_mock()
            session.commit.side_effect = OperationalError('private SQL', {'notes': 'private'}, Exception('private'))
            response = self.client.post(url, data={'csrf_token': 'synthetic-token', 'notes': 'hello'})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private', response.get_data(as_text=True))
            session.rollback.assert_called_once()
            session.close.assert_called_once()
