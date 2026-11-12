"""Operation-owned ORM transactions; no request-wide session or implicit commit."""
from contextlib import contextmanager

from flask import current_app, g
import mysql.connector
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError, DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from db import DatabaseError, create_verified_connection


class TransactionConflict(DatabaseError):
    """Refuse entry without touching caller-owned work."""


class ORMDatabaseError(DatabaseError):
    """Safe ORM failure: retain only a genuine connector's numeric error code."""

    def __init__(self, message, *, connector_errno=None):
        super().__init__(message)
        self.connector_errno = connector_errno


def _orm_connection():
    connection = create_verified_connection(orm=True)
    try:
        # End only this new connection's verification reads, never legacy work.
        connection.rollback()
        return connection
    except BaseException as error:
        try:
            connection.close()
        except Exception:
            raise DatabaseError("Could not clean up ORM connection verification.") from None
        if isinstance(error, Exception):
            raise DatabaseError("Could not finish ORM connection verification.") from None
        raise


def get_engine():
    engine = current_app.extensions.get("careerly_orm_engine")
    if engine is None:
        engine = create_engine(
            "mysql+mysqlconnector://", creator=_orm_connection, poolclass=NullPool,
            echo=False, hide_parameters=True,
            logging_name="careerly", pool_logging_name="careerly",
        )
        # Dedicated loggers: pool cleanup errors can otherwise expose raw DBAPI
        # messages even with echo off and parameters hidden. Services report safe errors.
        engine.logger.disabled = True
        engine.pool.logger.disabled = True
        current_app.extensions["careerly_orm_engine"] = engine
    return engine


@contextmanager
def operation_session(
    *, read_only=False,
    error_message="Could not save your notes. Check your tracker before retrying.",
):
    """Own one fresh session; read-only scopes roll back, write scopes commit.

    read_only selects cleanup behaviour; it does not change MySQL settings.
    Refuse active legacy/nested work before acquiring any new resources.
    """
    if g.get("orm_operation_active", False):
        raise TransactionConflict("Finish the active ORM operation first.")
    legacy = g.get("mysql_connection")
    if legacy is not None:
        try:
            if legacy.autocommit is not False or legacy.in_transaction:
                raise TransactionConflict("Finish the caller's legacy transaction first.")
        except mysql.connector.Error:
            raise DatabaseError("Could not inspect the caller's transaction.") from None

    g.orm_operation_active = True
    session = None
    try:
        session = Session(get_engine(), autobegin=False, autoflush=False,
                          expire_on_commit=False)
        try:
            session.begin()
            yield session
            if read_only:
                session.rollback()
            else:
                session.commit()
        except BaseException:
            try:
                session.rollback()
            except Exception:
                raise DatabaseError("Rollback failed; check the saved outcome before retrying.") from None
            raise
    except SQLAlchemyError as error:
        original = error.orig if isinstance(error, DBAPIError) else None
        errno = original.errno if isinstance(original, mysql.connector.Error) else None
        raise ORMDatabaseError(error_message, connector_errno=errno) from None
    finally:
        try:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    raise DatabaseError("ORM cleanup failed; check the saved outcome before retrying.") from None
        finally:
            g.pop("orm_operation_active", None)
