"""Opt-in synthetic integration checks on a newly initialised local MySQL server.

Never loads .env, connects to a pre-existing server, or uses developer records.
Requires existing mysqld and OpenSSL executables; installs nothing.
"""
import argparse
from datetime import date, datetime
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(args):
    # Never echo commands, credentials, generated keys, or raw subprocess errors.
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=90)


def check_deadline_routes(db_app, check):
    from db import get_db

    with (patch('dotenv.load_dotenv', return_value=False),
          patch.dict(os.environ, SECRET_KEY='synthetic-deadline-route-key')):
        from app import app

    def snapshot():
        with db_app.app_context():
            cursor = get_db().cursor(dictionary=True, buffered=True)
            try:
                cursor.execute('SELECT * FROM JobApplication ORDER BY application_id')
                applications = cursor.fetchall()
                cursor.execute('SELECT * FROM Company ORDER BY company_id')
                return applications, cursor.fetchall()
            finally:
                cursor.close()

    # All database reads and writes use the harness's verified loopback server.
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id='1', _fresh=True, _csrf_token='synthetic-deadline-token')
    add_url = '/applications/new'
    form = dict(csrf_token='synthetic-deadline-token', company='Deadline verification',
                job_title='Synthetic deadline role', opportunity_type='internship',
                status='Applied', work_type='hybrid', location='Dublin',
                job_url='https://example.invalid/role', salary_text='Synthetic salary',
                job_ad_text='Synthetic job advert', applied_at='2026-07-10')
    original = snapshot()
    response = client.post(add_url, data=form | {'closing_date': '2026-07-09'})
    check(response.status_code == 400 and b'Closing date cannot be earlier' in response.data
          and b'name="closing_date" type="date" value="2026-07-09"' in response.data
          and snapshot() == original, 'deadline create rejects reversal and leaves applications and companies intact')

    response = client.post(add_url, data=form | {'closing_date': '2026-07-10'})
    applications, _ = snapshot()
    created = next(row for row in applications if row['job_title'] == form['job_title'])
    edit_url = f"/applications/{created['application_id']}/edit"
    reopened = client.get(edit_url)
    check(response.status_code == 302 and reopened.status_code == 200
          and created['applied_at'] == created['closing_date'] == date(2026, 7, 10)
          and b'name="closing_date" type="date" value="2026-07-10"' in reopened.data
          and b'name="applied_at" type="date" value="2026-07-10"' in reopened.data,
          'deadline equality persists on create and prefills the edit form')

    notes_url = f"/applications/{created['application_id']}/notes"
    response = client.post(notes_url, data=dict(csrf_token=form['csrf_token'], notes='Keep these notes'))
    check(response.status_code == 200, 'deadline fixture saves notes through the existing route')
    before_edit = snapshot()
    response = client.post(edit_url, data=form | {
        'closing_date': '2026-07-09', 'location': 'Do not save this location'})
    check(response.status_code == 400 and b'Closing date cannot be earlier' in response.data
          and b'name="closing_date" type="date" value="2026-07-09"' in response.data
          and b'Do not save this location' in response.data and snapshot() == before_edit,
          'deadline edit rejects reversal, retains form values and leaves persisted rows intact')

    response = client.post(edit_url, data=form | {'closing_date': '2026-07-13'})
    after_edit, companies = snapshot()
    previous = next(row for row in before_edit[0] if row['application_id'] == created['application_id'])
    edited = next(row for row in after_edit if row['application_id'] == created['application_id'])
    reopened = client.get(edit_url)
    check(response.status_code == 302 and reopened.status_code == 200
          and edited == previous | {'closing_date': date(2026, 7, 13), 'updated_at': edited['updated_at']}
          and companies == before_edit[1]
          and b'name="closing_date" type="date" value="2026-07-13"' in reopened.data
          and b'name="applied_at" type="date" value="2026-07-10"' in reopened.data,
          'corrected edit persists and reopens with notes and other fields intact')

    with patch('tracker_data._now_utc', return_value=datetime(2026, 7, 9, 23)):
        default = client.get('/tracker')
        sorted_page = client.get('/tracker?sort=closing_date')
    marker = f'id="details-{created["application_id"]}"'.encode()
    check(default.status_code == sorted_page.status_code == 200
          and default.data.index(b'id="details-1"') < default.data.index(marker)
          and sorted_page.data.index(marker) < sorted_page.data.index(b'id="details-1"')
          and b'id="details-2"' not in sorted_page.data
          and b'theme-warning">3 days left</span>' in sorted_page.data,
          'real Tracker route sorts against NULL dates and renders the Dublin-local badge for the owner')


def check_follow_up_routes(check):
    from app import app
    from mysql.connector import OperationalError
    from orm_db import get_engine, operation_session
    from sqlalchemy import select
    from models import JobApplication
    from services import application_service, dashboard_service

    client = app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id='1', _fresh=True, _csrf_token='follow-up-test')
    form = dict(csrf_token='follow-up-test', company='Follow-up fixture', job_title='Same day follow-up',
                opportunity_type='internship', status='Applied', applied_at='2026-07-10')
    add = '/applications/new'
    for value in ('2026-07-09', '2026-02-30'):
        response = client.post(add, data=form | {'follow_up_date': value})
        check(response.status_code == 400 and f'value="{value}"'.encode() in response.data,
              'invalid follow-up create retains input')
    with app.app_context():
        engine = get_engine()
        with operation_session(read_only=True) as session:
            check(session.execute(select(JobApplication.application_id).where(
                JobApplication.job_title == form['job_title'])).first() is None,
                'invalid creates persist no application')
    check(client.post(add, data=form | {'follow_up_date': '2026-07-10'}).status_code == 302,
          'follow-up equality saves on create')
    with app.app_context():
        with operation_session(read_only=True) as session:
            identity = session.execute(select(JobApplication.application_id).where(
                JobApplication.job_title == form['job_title'])).scalar_one()

    def read():
        with app.app_context():
            return application_service.get_application(1, identity)

    edit = f'/applications/{identity}/edit'
    original = read()
    check(original['follow_up_date'] == date(2026, 7, 10)
          and b'name="follow_up_date" type="date" value="2026-07-10"' in client.get(edit).data,
          'follow-up persists and prefills on reopen')
    check(client.post(edit, data=form | {'follow_up_date': '2026-07-09'}).status_code == 400
          and read() == original, 'invalid follow-up edit leaves the row intact')
    with patch.object(engine.dialect, 'do_commit', side_effect=OperationalError('synthetic commit failure', errno=2013)):
        failed = client.post(edit, data=form | {'follow_up_date': '2026-07-11'})
    check(failed.status_code == 503 and b'value="2026-07-11"' in failed.data and read() == original,
          'failed follow-up commit rolls back the edit and retains the submitted date')
    with patch('services.application_service._now_utc', return_value=datetime(2026, 7, 11)):
        check(client.post(edit, data=form | {'follow_up_date': '2026-07-10'}).status_code == 302
              and read() == original, 'unchanged follow-up edit preserves timestamp and row')
    cleared_response = client.post(edit, data=form | {'follow_up_date': ''})
    cleared = read()
    check(cleared_response.status_code == 302
          and cleared == original | {'follow_up_date': None, 'updated_at': cleared['updated_at']}
          and b'name="follow_up_date" type="date" value=""' in client.get(edit).data,
          'blank follow-up clears only the date and prefills an empty input on reopen')
    check(client.post(edit, data=form | {'follow_up_date': '2026-07-10'}).status_code == 302
          and read()['follow_up_date'] == date(2026, 7, 10), 'edit persists a new follow-up value')
    check(client.get('/applications/2/edit').status_code == 404
          and client.post('/applications/2/edit', data=form).status_code == 404
          and client.post(edit, data={}).status_code == 400
          and client.post(add, data={}).status_code == 400, 'create and edit still enforce CSRF and edit ownership')

    fixtures = (
        ('Yesterday follow-up', '2026-07-09', 'Saved', 1),
        ('Tie follow-up', '2026-07-10', 'Saved', 1),
        ('Offer follow-up', '2026-07-17', 'Offer', 1),
        ('Out of range follow-up', '2026-07-18', 'Saved', 1),
        ('Rejected follow-up', '2026-07-10', 'Rejected', 1),
        ('Withdrawn follow-up', '2026-07-10', 'Withdrawn', 1),
        ('Foreign follow-up', '2026-07-10', 'Saved', 2),
    )
    with app.app_context():
        for title, follow_up, status, user_id in fixtures:
            application_service.create_application(user_id, company='Follow-up fixture', job_title=title,
                                                   opportunity_type='internship', status=status,
                                                   follow_up_date=follow_up)
    interview = dict(csrf_token=form['csrf_token'], date='2026-07-10', time='14:00', interview_type='online')
    check(client.post(f'/applications/{identity}/interviews/new', data=interview).status_code == 302,
          'synthetic follow-up fixture also has an upcoming interview')
    with patch.object(dashboard_service, 'datetime') as clock:
        clock.now.return_value = datetime(2026, 7, 9, 23)
        dashboard = client.get('/dashboard')
    html = dashboard.get_data(as_text=True).split('UPCOMING', 1)[-1].split('RECENT APPLICATIONS', 1)[0]
    names = ['Same day follow-up', 'Tie follow-up', 'Offer follow-up']
    check(dashboard.status_code == 200 and all(name in html for name in names)
          and html.index(names[0]) < html.index(names[1]) < html.index('Interview · Online') < html.index(names[2])
          and html.count(' · Follow-up') == 3
          and all(title not in html for title, _, _, _ in fixtures if title not in names),
          'combined Dashboard includes Dublin today and +7, stable ties, Offer and interviews for the owner')
    link = f'/tracker#details-{identity}'
    tracker = client.get('/tracker')
    check(f'href="{link}"' in html and f'id="details-{identity}"'.encode() in tracker.data
          and b'id="details-2"' not in tracker.data
          and b'<dt>Follow-up</dt><dd>Jul 10, 2026</dd>' in tracker.data
          and '<dt>Follow-up</dt><dd>—</dd>'.encode() in tracker.data,
          'Dashboard links to the owned Tracker details with a formatted follow-up date')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-disposable', action='store_true')
    parser.add_argument('--mysqld', required=True)
    parser.add_argument('--openssl', required=True)
    args = parser.parse_args()
    if not args.run_disposable:
        parser.error('--run-disposable is required; no server started')
    mysqld, openssl = Path(args.mysqld).resolve(), Path(args.openssl).resolve()
    if not mysqld.is_file() or not openssl.is_file():
        parser.error('Existing mysqld and OpenSSL executables are required')

    from flask import Flask, g
    import mysql.connector
    from sqlalchemy import event, update
    from models import JobApplication, User as UserRecord
    from werkzeug.security import generate_password_hash
    import db
    import orm_db
    from services import application_service as service, auth_service as auth, interview_service

    process = None
    connection = None
    stage = 'temporary server initialisation'
    passed = 0

    def check(condition, label):
        nonlocal passed
        if not condition:
            raise AssertionError(label)
        passed += 1
        print('PASS: ' + label, flush=True)

    # The new, uniquely named datadir is positive evidence of disposability.
    with tempfile.TemporaryDirectory(prefix='careerly-orm-disposable-') as directory:
        folder = Path(directory)
        data = folder / 'data'
        run([str(mysqld), '--no-defaults', '--initialize-insecure', '--basedir='+str(mysqld.parent.parent), '--datadir='+str(data)])
        identity = re.search(r'server-uuid=(.+)', (data / 'auto.cnf').read_text()).group(1).strip()
        stage = 'temporary TLS certificate generation'
        run([str(openssl), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=Careerly disposable CA', '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-keyout', str(folder/'ca.key'), '-out', str(folder/'ca.pem')])
        run([str(openssl), 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost', '-keyout', str(folder/'server.key'), '-out', str(folder/'server.csr')])
        (folder/'extensions.cnf').write_text('subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\n')
        run([str(openssl), 'x509', '-req', '-in', str(folder/'server.csr'), '-CA', str(folder/'ca.pem'), '-CAkey', str(folder/'ca.key'), '-CAcreateserial', '-days', '1', '-extfile', str(folder/'extensions.cnf'), '-out', str(folder/'server.pem')])
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        stage = 'temporary server startup'
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        process = subprocess.Popen([str(mysqld), '--no-defaults', '--basedir='+str(mysqld.parent.parent), '--datadir='+str(data), '--bind-address=127.0.0.1', '--port='+str(port), '--mysqlx=OFF', '--skip-log-bin', '--require-secure-transport=ON', '--ssl-ca='+str(folder/'ca.pem'), '--ssl-cert='+str(folder/'server.pem'), '--ssl-key='+str(folder/'server.key'), '--log-error='+str(folder/'server.log')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        try:
            deadline = time.monotonic()+30
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Temporary server stopped')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Temporary server startup')
                    time.sleep(0.25)
            # No dotenv; root exists only in our new isolated datadir. Strict TLS
            # and UUID matching prevent any accidental use of another server.
            stage = 'isolated server identity and schema setup'
            options = dict(host='127.0.0.1', port=port, user='root', password='', use_pure=True,
                           ssl_ca=str(folder/'ca.pem'), ssl_verify_cert=True,
                           ssl_verify_identity=True, ssl_disabled=False, connection_timeout=5)
            connection = mysql.connector.connect(**options)
            cursor = connection.cursor(buffered=True)
            cursor.execute('SELECT @@server_uuid, VERSION()')
            actual_identity, version = cursor.fetchone()
            check(actual_identity == identity, 'new temporary server UUID matches datadir')
            print('Local MySQL version: '+version, flush=True)
            cursor.execute('CREATE DATABASE careerly_orm_disposable CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
            cursor.execute('USE careerly_orm_disposable')
            migration = (ROOT/'database/migrations/001_initial_schema.sql').read_text(encoding='utf-8')
            definitions = re.findall(r'CREATE TABLE .*?;', migration, re.S)
            check(len(definitions) == 6, 'six released table definitions selected; original USE excluded')
            for definition in definitions:
                cursor.execute(definition)
            cursor.execute("CREATE USER 'orm_synthetic'@'localhost' IDENTIFIED BY 'Synthetic-local-only-42!' REQUIRE SSL")
            cursor.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON careerly_orm_disposable.* TO 'orm_synthetic'@'localhost'")
            now = datetime(2026, 11, 12, 12, 0, 0)
            for name in ('Synthetic owner', 'Synthetic other'):
                cursor.execute('INSERT INTO `User` (name,email,password_hash,created_at) VALUES (%s,%s,%s,%s)', (name, name.replace(' ', '')+'@example.invalid', 'unusable-synthetic-hash', now))
            cursor.execute("INSERT INTO Company (name) VALUES ('Synthetic company')")
            for owner in (1, 2):
                cursor.execute('INSERT INTO JobApplication (user_id,company_id,job_title,opportunity_type,notes,created_at,updated_at) VALUES (%s,1,%s,%s,%s,%s,%s)', (owner, 'Synthetic role', 'internship', 'initial', now, now))
            connection.commit()
            cursor.execute('SELECT * FROM JobApplication ORDER BY application_id')
            original_applications = cursor.fetchall()
            cursor.execute('SHOW CREATE TABLE `JobApplication`')
            original_definition = cursor.fetchone()[1]
            migration = (ROOT/'database/migrations/002_application_follow_up_date.sql').read_text(encoding='utf-8')
            cursor.execute(migration)
            cursor.execute('SELECT * FROM JobApplication ORDER BY application_id')
            check(cursor.fetchall() == [row + (None,) for row in original_applications],
                  'migration 002 preserves existing applications and defaults follow-ups to NULL')
            cursor.execute('SHOW CREATE TABLE `JobApplication`')
            migrated_definition = cursor.fetchone()[1]
            check(re.sub(r'^  `follow_up_date` date DEFAULT NULL,\n', '', migrated_definition, flags=re.M)
                  == original_definition, 'migration 002 preserves the existing application table definition and constraints')
            from database.verify_schema import SchemaChecks
            SchemaChecks(cursor).preflight()
            check(True, 'schema preflight confirms 45 columns and nullable follow-up date')
            cursor.close()
            connection.close()
            connection = None
            environment = dict(MYSQL_HOST='127.0.0.1', MYSQL_PORT=str(port), MYSQL_DATABASE='careerly_orm_disposable', MYSQL_USER='orm_synthetic', MYSQL_PASSWORD='Synthetic-local-only-42!', MYSQL_SSL_CA=str(folder/'ca.pem'))
            app = Flask('disposable_mysql_checks')
            db.init_app(app)

            def row(identity):
                with app.app_context():
                    reader = db.get_db().cursor(dictionary=True, buffered=True)
                    try:
                        reader.execute('SELECT * FROM JobApplication WHERE application_id=%s', (identity,))
                        return reader.fetchone()
                    finally:
                        reader.close()

            with patch.dict(os.environ, environment, clear=True):
                stage = 'strict helper and ORM integration'
                before, other = row(1), row(2)
                saved_at = datetime(2026, 11, 12, 12, 1, 0)
                with app.app_context():
                    legacy = db.get_db()
                    saved = service.update_notes(1, 1, 'new notes', now=saved_at)
                    check(saved == dict(notes='new notes', updated_at=saved_at), 'ORM save returns existing service contract')
                    check(not legacy.in_transaction, 'idle legacy connection remains idle')
                after = row(1)
                check(after == before | {'notes': 'new notes', 'updated_at': saved_at}, 'only notes/timestamp fields change')
                check(row(2) == other, 'other owner application remains unchanged')
                for value in ('new notes', '', '', 'new notes'):
                    with app.app_context():
                        service.update_notes(1, 1, value, now=now)
                    check(row(1)['notes'] == (value or None), 'unchanged or blank save succeeds')
                for owner, application in ((2, 1), (1, 999)):
                    with app.app_context():
                        try:
                            service.update_notes(owner, application, 'forbidden')
                        except service.ApplicationNotFound:
                            pass
                        else:
                            raise AssertionError('Ownership refusal missing')
                check(row(1)['notes'] == 'new notes', 'foreign/missing writes leave data unchanged')
                with app.app_context():
                    legacy = db.get_db()
                    writer = legacy.cursor(buffered=True)
                    writer.execute("UPDATE JobApplication SET notes='caller-owned' WHERE application_id=1")
                    try:
                        service.update_notes(1, 1, 'forbidden')
                    except service.ApplicationTransactionError:
                        pass
                    else:
                        raise AssertionError('Active legacy refusal missing')
                    check(legacy.in_transaction, 'ORM refusal preserves active legacy transaction')
                    writer.execute('SELECT notes FROM JobApplication WHERE application_id=1')
                    check(writer.fetchone() == ('caller-owned',), 'caller uncommitted work survives refusal')
                    legacy.rollback()
                    writer.close()
                check(row(1)['notes'] == 'new notes', 'caller rollback remains caller-owned')
                with app.app_context():
                    with orm_db.operation_session() as session:
                        try:
                            db.get_db()
                        except db.DatabaseError:
                            pass
                        else:
                            raise AssertionError('Legacy exclusion missing')
                    check(not g.get('orm_operation_active', False), 'ORM success releases exclusion')
                with app.app_context():
                    with orm_db.operation_session() as session:
                        result = session.execute(update(JobApplication).where(
                            JobApplication.application_id == 1).values(notes='new notes', updated_at=now))
                        check(result.rowcount == 1, 'unchanged MySQL update reports matched rows')
                # Fail the actual DBAPI commit after a real ORM update.
                with app.app_context():
                    engine = orm_db.get_engine()
                    with patch.object(engine.dialect, 'do_commit', side_effect=mysql.connector.OperationalError('synthetic private commit failure', errno=2013)):
                        try:
                            service.update_notes(1, 1, 'must rollback', now=now)
                        except db.DatabaseError as error:
                            check('private' not in str(error), 'DBAPI commit error is safely translated')
                        else:
                            raise AssertionError('Commit failure injection missing')
                    check(not g.get('orm_operation_active', False), 'commit interruption releases exclusion')
                check(row(1)['notes'] == 'new notes', 'real session commit interruption does not persist write')
                # A separate connection has a short lock timeout only inside this
                # disposable server; attempt notes while another transaction locks it.
                with app.app_context():
                    locker = db.get_db()
                    lock_cursor = locker.cursor(buffered=True)
                    lock_cursor.execute('SELECT application_id FROM JobApplication WHERE application_id=1 FOR UPDATE')
                    lock_cursor.fetchall()
                    with app.app_context():
                        engine = orm_db.get_engine()
                        def timeout_setup(connection, record):
                            c = connection.cursor()
                            c.execute('SET SESSION innodb_lock_wait_timeout=1')
                            c.close()
                        event.listen(engine, 'connect', timeout_setup)
                        try:
                            try:
                                service.update_notes(1, 1, 'blocked')
                            except db.DatabaseError:
                                pass
                            else:
                                raise AssertionError('Lock was not respected')
                        finally:
                            event.remove(engine, 'connect', timeout_setup)
                    locker.rollback()
                    lock_cursor.close()
                check(row(1)['notes'] == 'new notes', 'FOR UPDATE contention refuses competing write safely')
                stage = 'synthetic ORM authentication integration'
                form = dict(name=' Synthetic Auth User ', email=' CAFEORM@EXAMPLE.INVALID ',
                            password='Synthetic-auth-only-42!', user_id='2147483647')
                connection_ids = []
                with app.app_context():
                    engine = orm_db.get_engine()
                def record_connection(connection, record):
                    connection_ids.append(connection.connection_id)
                event.listen(engine, 'connect', record_connection)
                try:
                    with app.app_context():
                        check(auth.validate_registration(form | {'confirm_password': form['password']}) == {}, 'registration precheck permits new email')
                        registered = auth.create_user(form)
                        check(registered.user_id > 2 and registered.get_id() == str(registered.id), 'registration uses generated persisted identity')
                        check(registered.email == 'cafeorm@example.invalid' and registered.name == 'Synthetic Auth User', 'registration preserves email/name normalisation')
                        check(registered.created_at.tzinfo is None and registered.created_at.microsecond == 0, 'registration created_at is naive UTC at second precision')
                        check(not g.get('orm_operation_active', False), 'registration commits/closes and releases ownership')
                    with app.app_context():
                        logged_in = auth.authenticate(form['email'], form['password'])
                        check(logged_in is not None and logged_in.user_id == registered.user_id, 'login persists across a separate connection')
                        check(auth.authenticate(form['email'], 'wrong-password') is None, 'incorrect password is rejected')
                        check(auth.authenticate('absent-auth@example.invalid', form['password']) is None, 'unknown user is rejected')
                    with app.app_context():
                        loaded = auth.get_user_by_id(registered.get_id())
                        check(loaded is not None and loaded.user_id == registered.user_id, 'session identity reload persists across a separate connection')
                        check(auth.email_exists('CAF\u00c9ORM@example.invalid'), 'email lookup respects database accent/case collation')
                        check('email' in auth.validate_registration(form | {'confirm_password': form['password']}), 'duplicate precheck reports existing email')
                        try:
                            auth.create_user(form | {'email': 'CAF\u00c9ORM@example.invalid'})
                        except auth.EmailAlreadyExists:
                            pass
                        else:
                            raise AssertionError('Duplicate insert was not translated')
                        check(auth.get_user_by_id(registered.id).password_hash == registered.password_hash, 'duplicate insert rollback preserves existing account/hash')
                    check(len(set(connection_ids)) >= 3, 'authentication operations use distinct physical connections')
                    historic = generate_password_hash(form['password'], method='pbkdf2:sha256:1000')
                    with app.app_context():
                        with orm_db.operation_session() as session:
                            historic_record = UserRecord(name='Synthetic historic hash', email='historic@example.invalid',
                                                         password_hash=historic, created_at=now)
                            session.add(historic_record)
                            session.flush()
                            historic_id = historic_record.user_id
                    with app.app_context():
                        historical = auth.authenticate('historic@example.invalid', form['password'])
                        check(historical is not None and historical.password_hash == historic, 'existing PBKDF2 hash verifies without replacement')
                        check(auth.get_user_by_id(historic_id).password_hash == historic, 'historic stored hash remains unchanged')
                    with app.app_context():
                        user = auth.get_user_by_id(1)
                        service.update_notes(user.user_id, 1, 'after auth', now=now)
                        check(not g.get('orm_operation_active', False), 'authentication read then notes write releases both scopes')
                        interview = interview_service.create_interview(user.user_id, 1, scheduled_at=now, interview_type='online')
                        check(interview['application_id'] == 1, 'authentication read then ORM interview write succeeds')
                    with app.app_context():
                        user = auth.get_user_by_id(1)
                        legacy = db.get_db()
                        cursor = legacy.cursor(buffered=True)
                        cursor.execute("UPDATE JobApplication SET notes='auth-caller-work' WHERE application_id=1")
                        try:
                            auth.get_user_by_id(user.user_id)
                        except db.DatabaseError:
                            pass
                        else:
                            raise AssertionError('Authentication failed to refuse active legacy work')
                        cursor.execute('SELECT notes FROM JobApplication WHERE application_id=1')
                        check(cursor.fetchone() == ('auth-caller-work',) and legacy.in_transaction, 'authentication refusal preserves caller transaction and pending data')
                        legacy.rollback()
                        cursor.close()
                    check(row(1)['notes'] == 'after auth', 'caller rollback after authentication refusal remains independent')
                    with app.app_context():
                        with patch.object(engine.dialect, 'do_commit', side_effect=mysql.connector.OperationalError('synthetic private auth commit failure', errno=2013)):
                            try:
                                auth.create_user(form | {'email': 'rolled-back-auth@example.invalid'})
                            except db.DatabaseError as error:
                                check('private' not in str(error), 'registration DBAPI commit error is safe')
                            else:
                                raise AssertionError('Registration commit failure was ignored')
                        check(not g.get('orm_operation_active', False), 'failed registration releases ownership')
                    with app.app_context():
                        check(auth.get_user_by_email('rolled-back-auth@example.invalid') is None, 'failed registration is not persisted')
                        recovered = auth.create_user(form | {'email': 'rolled-back-auth@example.invalid'})
                        check(recovered.id > 0, 'fresh registration succeeds after commit failure cleanup')
                finally:
                    event.remove(engine, 'connect', record_connection)
                stage = 'synthetic interview and Tracker ORM integration'
                from integration_interviews import run_checks
                run_checks(app, check, now)
                stage = 'synthetic deadline create, edit and Tracker routes'
                check_deadline_routes(app, check)
                stage = 'synthetic follow-up persistence and Dashboard routes'
                check_follow_up_routes(check)
            print('Result: '+str(passed)+' isolated checks passed', flush=True)
        except Exception as error:
            print('FAIL: '+stage+'; '+type(error).__name__+'; errno='+str(getattr(error, 'errno', None))+'; SQLSTATE='+str(getattr(error, 'sqlstate', None))+'; details suppressed', flush=True)
            return 1
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()
            if process is not None and process.poll() is None:
                # Windows mysqld uses a supervising process. Graceful SQL shutdown
                # stops its child as well, unlike terminating only the supervisor.
                try:
                    admin = mysql.connector.connect(**options)
                    shutdown_cursor = admin.cursor()
                    shutdown_cursor.execute('SELECT @@server_uuid')
                    if shutdown_cursor.fetchone() != (identity,):
                        raise RuntimeError('Shutdown target identity mismatch')
                    shutdown_cursor.execute('SHUTDOWN')
                    shutdown_cursor.close()
                    admin.close()
                except mysql.connector.Error:
                    pass
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                    else:
                        process.terminate()
                    process.wait(timeout=10)
                # Allow Windows to release asynchronous file handles.
                time.sleep(1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
