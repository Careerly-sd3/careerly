"""Synthetic authentication regressions through real ORM transaction ownership."""
from datetime import datetime, timezone
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from flask import Flask, g
import mysql.connector as connector
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.security import check_password_hash, generate_password_hash

from db import DatabaseError
from services import auth_service as auth


class AuthServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = 'synthetic-test-password'
        cls.scrypt = generate_password_hash(cls.password)
        cls.pbkdf2 = generate_password_hash(cls.password, method='pbkdf2:sha256:1000')

    def setUp(self):
        self.context = Flask('auth-unit-test').app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        for target, value in [('orm_db.get_engine', MagicMock()), ('orm_db.Session', None)]:
            patcher = patch(target, **({'return_value': value} if value is not None else {}))
            mock = patcher.start(); self.addCleanup(patcher.stop)
            if target == 'orm_db.Session':
                self.factory = mock
        self.session = self.factory.return_value
        network = patch('mysql.connector.connect', side_effect=AssertionError('No hosted access'))
        self.network = network.start(); self.addCleanup(network.stop)
        self.row = dict(user_id=873, name='Synthetic User', email='synthetic@example.invalid',
                        password_hash=self.scrypt, created_at=datetime(2026, 11, 9, 12))
        self.session.execute.side_effect = self.execute
        self.session.flush.side_effect = self.flush
        self.generated_id = 873
        self.form = dict(name=' Synthetic User ', email=' SYNTHETIC@example.invalid ',
                         password=self.password, confirm_password=self.password, user_id='999999')

    def execute(self, statement, *args, **kwargs):
        result = MagicMock()
        result.scalar_one_or_none.return_value = auth.UserRecord(**self.row) if self.row is not None else None
        return result

    def flush(self):
        self.session.add.call_args.args[0].user_id = self.generated_id

    def tearDown(self):
        self.network.assert_not_called()

    def error(self, errno=1048, *, original=None):
        return IntegrityError('private SQL', {'hash': 'private'}, original or connector.IntegrityError('private driver details', errno=errno))

    def web_app(self):
        with patch('dotenv.load_dotenv', return_value=False), patch.dict('os.environ', {'SECRET_KEY': 'synthetic-key'}, clear=True):
            module = importlib.import_module('app')
        config = patch.dict(module.app.config, TESTING=True, SECRET_KEY='synthetic-key')
        config.start(); self.addCleanup(config.stop)
        return module

    def test_registration_uses_generated_id_utc_hash_and_explicit_commit(self):
        before = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        user = auth.create_user(self.form)
        after = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        record = self.session.add.call_args.args[0]
        self.assertEqual((user.user_id, user.id, user.get_id()), (873, 873, '873'))
        self.assertEqual((record.name, record.email), ('Synthetic User', 'synthetic@example.invalid'))
        self.assertTrue(check_password_hash(record.password_hash, self.password))
        self.assertNotEqual(record.password_hash, self.password)
        self.assertTrue(before <= record.created_at <= after)
        self.assertIsNone(record.created_at.tzinfo)
        self.assertEqual(record.created_at.microsecond, 0)
        self.assertEqual(user.created_at, record.created_at)
        self.assertIsNone(inspect(user, raiseerr=False))
        self.session.begin.assert_called_once()
        self.session.flush.assert_called_once()
        self.session.commit.assert_called_once()
        self.session.rollback.assert_not_called()
        self.session.close.assert_called_once()
        self.assertEqual([c[0] for c in self.session.mock_calls], ['begin', 'add', 'flush', 'commit', 'close'])

    def test_concurrent_duplicate_after_precheck_is_1062_and_rolls_back(self):
        self.row = None
        self.assertEqual(auth.validate_registration(self.form), {})
        self.session.flush.side_effect = self.error(1062)
        with self.assertRaises(auth.EmailAlreadyExists) as caught:
            auth.create_user(self.form)
        self.assertNotIn('private', str(caught.exception))
        self.session.commit.assert_not_called()
        self.assertEqual(self.session.rollback.call_count, 2)
        self.assertEqual(self.session.close.call_count, 2)

    def test_existing_duplicate_precheck_preserves_validation(self):
        self.assertEqual(auth.validate_registration(self.form), {'email': 'An account with this email already exists.'})
        self.session.add.assert_not_called()
        self.session.commit.assert_not_called()

    def test_unrelated_integrity_failure_is_not_duplicate_email(self):
        for error in (self.error(1048), self.error(original=SimpleNamespace(errno=1062))):
            self.session.reset_mock()
            self.session.flush.side_effect = error
            with self.assertRaises(DatabaseError) as caught:
                auth.create_user(self.form)
            self.assertNotIn('private', str(caught.exception))
            self.session.rollback.assert_called_once()
            self.session.commit.assert_not_called()
            self.session.close.assert_called_once()

    def test_commit_failure_returns_no_success_and_attempts_rollback(self):
        self.session.commit.side_effect = OperationalError('private', {}, connector.Error('private', errno=2013))
        with self.assertRaises(DatabaseError) as caught:
            auth.create_user(self.form)
        self.assertIn('check the outcome', str(caught.exception))
        self.assertNotIn('private', str(caught.exception))
        self.session.rollback.assert_called_once()
        self.session.close.assert_called_once()

    def test_rollback_failure_is_sanitised(self):
        self.session.flush.side_effect = self.error()
        self.session.rollback.side_effect = RuntimeError('private')
        with self.assertRaises(DatabaseError) as caught:
            auth.create_user(self.form)
        self.assertIn('rollback failed', str(caught.exception).lower())
        self.assertNotIn('private', str(caught.exception))
        self.session.close.assert_called_once()
        self.session.commit.assert_not_called()

    def test_all_auth_operations_refuse_caller_work_untouched(self):
        for active, autocommit in ((True, False), (False, True)):
            legacy = MagicMock(in_transaction=active, autocommit=autocommit)
            g.mysql_connection = legacy
            for operation in (lambda: auth.create_user(self.form), lambda: auth.get_user_by_id(873), lambda: auth.get_user_by_email(self.form['email'])):
                with self.assertRaises(DatabaseError):
                    operation()
            legacy.commit.assert_not_called()
            legacy.rollback.assert_not_called()
            legacy.close.assert_not_called()
            legacy.cursor.assert_not_called()
        self.factory.assert_not_called()

    def test_auth_operations_refuse_nested_orm_work(self):
        g.orm_operation_active = True
        with self.assertRaises(DatabaseError):
            auth.create_user(self.form)
        with self.assertRaises(DatabaseError):
            auth.get_user_by_id(873)
        self.factory.assert_not_called()
        self.assertTrue(g.orm_operation_active)

    def test_missing_generated_id_is_a_failed_registration(self):
        self.generated_id = 0
        with self.assertRaises(DatabaseError):
            auth.create_user(self.form)
        self.session.rollback.assert_called_once()
        self.session.commit.assert_not_called()

    def test_load_by_email_and_id_use_same_persisted_record(self):
        by_email = auth.get_user_by_email(self.form['email'])
        statement = self.session.execute.call_args.args[0].compile(dialect=mysql.dialect())
        self.assertEqual(statement.params, {'email_1': 'synthetic@example.invalid'})
        by_id = auth.get_user_by_id(by_email.get_id())
        statement = self.session.execute.call_args.args[0].compile(dialect=mysql.dialect())
        self.assertEqual(statement.params, {'user_id_1': 873})
        self.assertEqual(by_id.id, by_email.user_id)
        self.assertEqual(self.session.rollback.call_count, 2)
        self.assertEqual(self.session.close.call_count, 2)
        self.session.commit.assert_not_called()
        self.session.add.assert_not_called()

    def test_invalid_session_ids_are_anonymous_without_querying(self):
        for identity in (None, True, False, 0, -1, 1.5, [], {}, '', '1 OR 1=1', '-1', '0', '01', '1.0', '2147483648'):
            with self.subTest(identity=identity):
                self.assertIsNone(auth.get_user_by_id(identity))
        self.factory.assert_not_called()

    def test_deleted_user_session_is_anonymous(self):
        self.row = None
        self.assertIsNone(auth.get_user_by_id('873'))
        self.session.rollback.assert_called_once()
        self.session.close.assert_called_once()

    def test_user_loader_read_then_interview_write(self):
        from services import interview_service
        legacy = MagicMock(in_transaction=False, autocommit=False)
        legacy.cursor.return_value.fetchone.return_value = dict(interview_id=41, application_id=11, outcome='Pending')
        legacy.cursor.return_value.rowcount = 1
        g.mysql_connection = legacy
        user = auth.get_user_by_id('873')
        self.session.rollback.assert_called_once()
        self.session.close.assert_called_once()
        self.assertFalse(g.get('orm_operation_active', False))
        with patch.object(interview_service, 'get_db', return_value=legacy):
            interview_service.delete_interview(user.user_id, 41)
        legacy.start_transaction.assert_called_once()
        legacy.commit.assert_called_once()

    def test_user_loader_read_then_application_notes_write(self):
        from services import application_service
        user = auth.get_user_by_id('873')
        self.session.execute.side_effect = None
        self.session.execute.return_value.scalar_one_or_none.return_value = 501
        application_service.update_notes(user.user_id, 501, 'notes')
        self.assertEqual(self.session.begin.call_count, 2)
        self.session.rollback.assert_called_once()
        self.session.commit.assert_called_once()
        self.assertEqual(self.session.close.call_count, 2)
        self.assertFalse(g.get('orm_operation_active', False))

    def test_login_accepts_existing_scrypt_and_pbkdf2_without_resetting_hashes(self):
        for stored_hash in (self.scrypt, self.pbkdf2):
            self.row['password_hash'] = stored_hash
            user = auth.authenticate(self.form['email'], self.password)
            self.assertEqual(user.user_id, 873)
            self.assertEqual(user.password_hash, stored_hash)
            self.assertIsNone(auth.authenticate(self.form['email'], 'wrong-password'))
        self.session.add.assert_not_called()
        self.session.flush.assert_not_called()
        self.session.commit.assert_not_called()

    def test_unknown_email_and_malformed_stored_hash_fail_closed(self):
        self.row = None
        with patch.object(auth, 'check_password_hash', wraps=check_password_hash) as verify:
            self.assertIsNone(auth.authenticate('absent@example.invalid', self.password))
            verify.assert_called_once_with(auth._DUMMY_HASH, self.password)
        self.row = dict(user_id=873, name='Synthetic', email='synthetic@example.invalid', password_hash='unsupported-method$salt$hash')
        self.assertIsNone(auth.authenticate(self.row['email'], self.password))

    def test_query_parameters_and_failures_never_expose_credentials(self):
        payload = "synthetic' OR 1=1 --@example.invalid"
        auth.get_user_by_email(payload)
        compiled = self.session.execute.call_args.args[0].compile(dialect=mysql.dialect())
        self.assertNotIn(payload, str(compiled))
        self.assertEqual(compiled.params, {'email_1': auth.normalise_email(payload)})
        self.session.execute.side_effect = self.error()
        with self.assertRaises(DatabaseError) as caught:
            auth.get_user_by_email(payload)
        self.assertNotIn('private', str(caught.exception))

    def test_canonical_identity_uses_persisted_user_id_not_alias_or_posted_id(self):
        with patch.object(auth, 'current_user', SimpleNamespace(is_authenticated=True, user_id=873, id=999999)):
            self.assertEqual(auth.current_user_id(), 873)
        for user in (SimpleNamespace(is_authenticated=False), SimpleNamespace(is_authenticated=True, user_id='bad')):
            with patch.object(auth, 'current_user', user):
                self.assertIsNone(auth.current_user_id())

    def test_registration_validation_keeps_incoming_length_and_password_rules(self):
        self.row = None
        self.assertEqual(auth.validate_registration(self.form), {})
        for overrides, field in [({'name': 'x'*101}, 'name'), ({'email': 'x'*256+'@example.invalid'}, 'email'), ({'password': 'short'}, 'password'), ({'confirm_password': 'mismatch'}, 'confirm_password')]:
            self.assertIn(field, auth.validate_registration(self.form | overrides))

    def test_app_loader_and_duplicate_race_response_are_safe(self):
        module = self.web_app()
        with module.app.test_request_context():
            self.assertEqual(module.load_user('873').user_id, 873)
            self.assertIsNone(module.load_user('invalid'))
        self.row = None
        self.session.flush.side_effect = self.error(1062)
        response = module.app.test_client().post('/register', data=self.form)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'An account with this email already exists.', response.data)
        self.assertNotIn(self.password.encode(), response.data)

    def test_diagnostic_import_is_lazy_and_has_no_second_connection(self):
        with patch('dotenv.load_dotenv') as load:
            module = importlib.import_module('db_check')
        load.assert_not_called()
        self.network.assert_not_called()
        self.assertIs(module.get_db, importlib.import_module('db').get_db)

    def test_login_routes_keep_errors_safe_redirects_and_database_identity(self):
        module = self.web_app()
        self.assertEqual(module.app.test_client().post('/login', data={}).status_code, 400)
        response = module.app.test_client().post('/login', data=self.form | {'password': 'wrong'})
        self.assertEqual(response.status_code, 401)
        self.assertIn(b'Email or password is incorrect.', response.data)
        for target, expected in [('/tracker?status=Saved', '/tracker?status=Saved'), ('https://example.invalid/', '/tracker'), ('//example.invalid/', '/tracker')]:
            client = module.app.test_client()
            response = client.post('/login', query_string={'next': target}, data=self.form)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, expected)
            with client.session_transaction() as session:
                self.assertEqual(session['_user_id'], '873')
        self.row = None
        response = module.app.test_client().post('/login', data=self.form)
        self.assertEqual(response.status_code, 401)
        self.assertIn(b'Email or password is incorrect.', response.data)

    def test_bad_session_is_anonymous_and_database_errors_return_503(self):
        module = self.web_app()
        client = module.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = 'not-a-database-id'
        response = client.get('/tracker')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.location).path, '/login')
        self.assertEqual(parse_qs(urlsplit(response.location).query), {'next': ['/tracker']})
        self.factory.assert_not_called()
        self.session.execute.side_effect = self.error()
        response = module.app.test_client().post('/login', data=self.form)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'private', response.data)

    def test_read_transaction_rollback_failure_cannot_return_a_user(self):
        self.session.rollback.side_effect = RuntimeError('private')
        with self.assertRaises(DatabaseError) as caught:
            auth.get_user_by_id(873)
        self.assertNotIn('private', str(caught.exception))
        self.session.close.assert_called_once()
        self.session.commit.assert_not_called()
        self.assertFalse(g.get('orm_operation_active', False))

    def test_outage_preserves_session_then_recovers(self):
        module = self.web_app()
        client = module.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = '873'
        self.session.execute.side_effect = self.error()
        response = client.get('/tracker')
        self.assertEqual(response.status_code, 503)
        with client.session_transaction() as session:
            self.assertEqual(session['_user_id'], '873')
        self.session.execute.side_effect = self.execute
        with patch.object(module, 'load_tracker_page', return_value=dict(applications=[], interviews=[], has_more_interviews=False, practice_meta='', status_counts={}, total_applications=0)) as tracker:
            self.assertEqual(client.get('/tracker').status_code, 200)
            tracker.assert_called_once_with(873, status='', company='', role='')

    def test_schema_diagnostic_is_read_only_and_parameterised(self):
        diagnostic = importlib.import_module('db_check')
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [{'selected_database': 'careerly'}, {'Value': 'synthetic-cipher'}]
        cursor.fetchall.side_effect = [[{'TABLE_NAME': 'User'}], [{'COLUMN_NAME': 'user_id'}], [{'CONSTRAINT_NAME': 'PRIMARY'}], [{'INDEX_NAME': 'PRIMARY'}]]
        output = []
        diagnostic.inspect_schema(connection, output.append)
        for operation in cursor.execute.call_args_list:
            self.assertTrue(operation.args[0].startswith(('SELECT', 'SHOW')))
        for operation in cursor.execute.call_args_list[-3:]:
            self.assertEqual(operation.args[1], ('User',))
        self.assertIn('Table: User', output)
        cursor.close.assert_called_once()
        connection.commit.assert_not_called()

    def test_adapter_is_independent_of_read_rollback_and_session_close(self):
        record = auth.UserRecord(**(self.row | {'phone_number': 'synthetic phone', 'cv_text': 'synthetic CV'}))
        result = MagicMock()
        result.scalar_one_or_none.return_value = record
        self.session.execute.side_effect = None
        self.session.execute.return_value = result
        def expire_record():
            record.password_hash = record.cv_text = record.phone_number = None
        self.session.rollback.side_effect = expire_record
        user = auth.get_user_by_id(873)
        self.assertEqual(user.password_hash, self.scrypt)
        self.assertEqual(user.cv_text, 'synthetic CV')
        self.assertEqual(user.phone_number, 'synthetic phone')
        self.assertIsNone(inspect(user, raiseerr=False))
        self.session.close.assert_called_once()

    def test_auth_cleanup_failure_prevents_a_successful_return(self):
        self.session.close.side_effect = RuntimeError('private cleanup details')
        for operation in (lambda: auth.get_user_by_id(873), lambda: auth.create_user(self.form)):
            with self.assertRaises(DatabaseError) as caught:
                operation()
            self.assertNotIn('private', str(caught.exception))
            self.assertFalse(g.get('orm_operation_active', False))

    def test_raw_hash_and_sql_errors_are_not_logged(self):
        with patch('logging.Logger._log') as log:
            self.session.flush.side_effect = self.error(1062)
            with self.assertRaises(auth.EmailAlreadyExists):
                auth.create_user(self.form)
            self.session.execute.side_effect = self.error()
            with self.assertRaises(DatabaseError):
                auth.get_user_by_email(self.form['email'])
        log.assert_not_called()
