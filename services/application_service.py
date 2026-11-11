"""Owner-scoped writes to one JobApplication (CAR-63: notes edited in place on the tracker).

Callers must supply the trusted, persisted User.user_id from Flask-Login, never a
submitted identity. Absent and foreign-owned applications share one result
(ApplicationNotFound), which routes map to 404.

The write follows the interview service's transaction rules: it needs an idle,
autocommit-disabled db.get_db() connection, refuses a caller's active transaction
without committing or rolling it back, locks and rechecks ownership, then commits.
Failures roll back. Flask supplies updated_at as naive UTC, as the schema requires.
"""

from contextlib import closing
from datetime import datetime, timezone

import mysql.connector

from db import DatabaseError, get_db

# MySQL TEXT holds at most 65,535 bytes; utf8mb4 characters take 1 to 4 bytes each.
NOTES_MAX_BYTES = 65535
MAX_ID = 2147483647  # signed INT primary keys

LOCK_QUERY = """
    SELECT a.application_id
    FROM `JobApplication` a
    WHERE a.application_id = %s AND a.user_id = %s
    FOR UPDATE
"""

UPDATE_NOTES_QUERY = """
    UPDATE `JobApplication`
    SET notes = %s, updated_at = %s
    WHERE application_id = %s AND user_id = %s
"""


class ApplicationNotFound(LookupError):
    """Absent and foreign-owned applications deliberately share one result."""

    def __init__(self):
        super().__init__("Application not found.")


class ApplicationValidationError(ValueError):
    """Fixed field messages; submitted values are never echoed back."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("Invalid application input.")


class ApplicationTransactionError(DatabaseError):
    """The service will not complete or discard someone else's unit of work."""


def _validate_id(value, field):
    if type(value) is not int or not 1 <= value <= MAX_ID:
        raise ApplicationValidationError({field: "A positive persisted integer ID is required."})


def _clean_notes(notes):
    """Plain newlines, surrounding blank space removed; blank notes become None (NULL)."""
    if not isinstance(notes, str):
        raise ApplicationValidationError({"notes": "Notes must be text."})
    notes = notes.replace("\r\n", "\n").replace("\r", "\n")
    if not notes.strip():
        return None
    notes = notes.strip("\n").rstrip()
    try:
        size = len(notes.encode("utf-8"))
    except UnicodeEncodeError:
        raise ApplicationValidationError({"notes": "Use valid UTF-8 text."}) from None
    if size > NOTES_MAX_BYTES:
        raise ApplicationValidationError(
            {"notes": f"Notes can be at most {NOTES_MAX_BYTES} bytes (about 16,000 to 65,000 characters)."})
    return notes


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _write(operation):
    connection = get_db()
    try:
        if connection.autocommit is not False or connection.in_transaction:
            raise ApplicationTransactionError(
                "Application writes require an idle connection with autocommit disabled; "
                "finish the caller's transaction first."
            )
    except mysql.connector.Error:
        raise DatabaseError("Could not inspect the application transaction state.") from None

    try:
        connection.start_transaction()
        with closing(connection.cursor(dictionary=True, buffered=True)) as cursor:
            result = operation(cursor)
        connection.commit()
        return result
    except BaseException as error:
        # The idle guard above ensures this rollback cannot discard caller work.
        try:
            connection.rollback()
        except mysql.connector.Error:
            raise DatabaseError(
                "Rollback failed; check the application in your tracker before retrying."
            ) from None
        if isinstance(error, mysql.connector.Error):
            raise DatabaseError(
                "Could not save your notes. Check your tracker before retrying."
            ) from None
        raise


def update_notes(user_id, application_id, notes, *, now=None):
    """Replace an owned application's notes; return {"notes", "updated_at"} as saved."""
    _validate_id(user_id, "user_id")
    _validate_id(application_id, "application_id")
    notes = _clean_notes(notes)
    updated_at = now or _now_utc()

    def operation(cursor):
        cursor.execute(LOCK_QUERY, (application_id, user_id))
        if cursor.fetchone() is None:
            raise ApplicationNotFound()
        cursor.execute(UPDATE_NOTES_QUERY, (notes, updated_at, application_id, user_id))
        return {"notes": notes, "updated_at": updated_at}

    return _write(operation)
