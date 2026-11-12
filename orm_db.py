"""Operation-owned ORM transactions; no request-wide session or implicit commit."""
from contextlib import contextmanager

from flask import current_app, g
import mysql.connector
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from db import DatabaseError, create_verified_connection


class TransactionConflict(DatabaseError):
    """Refuse entry without touching caller-owned work."""


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
def operation_session():
    """Own one fresh session; refuse active legacy/nested work before cleanup."""
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
            session.commit()
        except BaseException:
            try:
                session.rollback()
            except Exception:
                raise DatabaseError("Rollback failed; check the saved outcome before retrying.") from None
            raise
    except SQLAlchemyError:
        raise DatabaseError("Could not save your notes. Check your tracker before retrying.") from None
    finally:
        try:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    raise DatabaseError("ORM cleanup failed; check the saved outcome before retrying.") from None
        finally:
            g.pop("orm_operation_active", None)
