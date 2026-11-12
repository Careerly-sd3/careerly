"""Opt-in synthetic integration checks on a newly initialised local MySQL server.

Never loads .env, connects to a pre-existing server, or uses developer records.
Requires existing mysqld and OpenSSL executables; installs nothing.
"""
import argparse
from datetime import datetime
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
    from models import JobApplication
    import db
    import orm_db
    from services import application_service as service

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
