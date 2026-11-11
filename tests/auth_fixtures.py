"""Synthetic MySQL-boundary fixture for route tests; never opens a database."""
from copy import deepcopy
from unittest.mock import MagicMock

import mysql.connector

from services import auth_service as auth


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
        cursor = MagicMock()

        def execute(query, parameters):
            if query == auth.USER_BY_EMAIL:
                matches = [row for row in self.rows.values() if row["email"] == parameters[0]]
            elif query == auth.USER_BY_ID:
                matches = [row for row in self.rows.values() if row["user_id"] == parameters[0]]
            elif query == auth.CREATE_USER:
                name, email, password_hash, created_at = parameters
                if any(row["email"] == email for row in self.rows.values()):
                    raise mysql.connector.IntegrityError("synthetic duplicate", errno=1062)
                identity = self.next_id
                self.next_id += 1
                self.rows[identity] = {
                    "user_id": identity, "name": name, "email": email,
                    "password_hash": password_hash, "created_at": created_at,
                }
                cursor.lastrowid = identity
                self.connection.in_transaction = True
                return
            else:
                raise AssertionError("Unexpected authentication SQL")
            cursor.fetchone.return_value = deepcopy(matches[0]) if matches else None
            self.connection.in_transaction = True

        cursor.execute.side_effect = execute
        return cursor
