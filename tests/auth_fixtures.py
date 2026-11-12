"""Synthetic MySQL-boundary fixture for route tests; never opens a database."""
from copy import deepcopy
from contextlib import contextmanager
from sqlalchemy.dialects import mysql
from models import User as UserRecord
from orm_db import ORMDatabaseError
from unittest.mock import MagicMock


class AuthDatabaseFixture:
    def __init__(self):
        self.rows = {}
        self.next_id = 101
        self.snapshot = None
        self.connection = MagicMock()
        self.connection.autocommit = False
        self.connection.in_transaction = False
        self.connection.cursor.side_effect = self.cursor
        self.connection.start_transaction.side_effect = self.start
        self.connection.commit.side_effect = self.commit
        self.connection.rollback.side_effect = self.rollback

    def start(self):
        if self.connection.in_transaction:
            raise AssertionError("Test found an unexpected active transaction")
        self.snapshot = deepcopy(self.rows)
        self.connection.in_transaction = True

    def commit(self):
        self.snapshot = None
        self.connection.in_transaction = False

    def rollback(self):
        if self.snapshot is not None:
            self.rows = self.snapshot
        self.snapshot = None
        self.connection.in_transaction = False

    def cursor(self, **options):
        return MagicMock()

    @contextmanager
    def auth_operation(self, *, read_only=False, error_message=None):
        """Synthetic ORM boundary independent of the legacy interview connection.

        Infrastructure exclusion/rollback is tested with the real context manager
        in test_auth_service; this fixture retains route behaviour assertions.
        """
        snapshot = deepcopy(self.rows)
        session = MagicMock()
        pending = []
        session.add.side_effect = pending.append

        def execute(statement):
            parameters = statement.compile(dialect=mysql.dialect()).params
            if 'email_1' in parameters:
                matches = [r for r in self.rows.values() if r['email'] == parameters['email_1']]
            else:
                matches = [r for r in self.rows.values() if r['user_id'] == parameters['user_id_1']]
            result = MagicMock()
            result.scalar_one_or_none.return_value = UserRecord(**deepcopy(matches[0])) if matches else None
            return result

        def flush():
            for record in pending:
                if any(r['email'] == record.email for r in self.rows.values()):
                    raise ORMDatabaseError(error_message, connector_errno=1062)
                record.user_id = self.next_id
                self.next_id += 1
                self.rows[record.user_id] = {c.name: getattr(record, c.name) for c in UserRecord.__table__.columns}
            pending.clear()

        session.execute.side_effect = execute
        session.flush.side_effect = flush
        try:
            yield session
            if read_only:
                session.rollback()
            else:
                session.commit()
        except BaseException:
            self.rows = snapshot
            session.rollback()
            raise
        finally:
            session.close()
