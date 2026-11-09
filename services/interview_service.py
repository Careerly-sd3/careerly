"""CAR-86 data access only; no routes or authentication are wired here.

Callers must supply a trusted, persisted User.user_id, never a posted identity
or the current temporary in-memory authentication ID. NotFound maps to 404.

Reads return dictionaries (lists for listing), with every Interview column,
job_title and company. Legacy NULL outcomes are presented as Pending only.
scheduled_at is a naive UTC datetime matching MySQL DATETIME (second precision).
Inputs may be naive UTC or aware datetimes; aware values are converted to UTC.
Local wall-clock parsing and DST validation belong to the later form layer.

Writes require an idle, autocommit-disabled db.get_db() connection. An active
caller transaction (including an earlier transactional read) is refused without
commit or rollback. Callers must end their own unit of work first. Each write
owns its transaction, closes its cursor, then commits; failures roll back.
No caller-owned connection is closed here; Flask teardown remains responsible.
"""

from contextlib import closing
from datetime import datetime, timezone

import mysql.connector

from db import DatabaseError, get_db


INTERVIEW_TYPES = ("online", "phone", "technical", "onsite")
OUTCOMES = ("Pending", "Passed", "Failed")
DEFAULT_OUTCOME = "Pending"

APPLICATION_QUERY = """
    SELECT a.application_id, a.user_id, a.company_id, a.job_title, a.status,
           c.name AS company
    FROM `JobApplication` a
    JOIN `Company` c ON c.company_id = a.company_id
    WHERE a.application_id = %s AND a.user_id = %s
"""

INTERVIEW_QUERY = """
    SELECT i.interview_id, i.application_id, i.scheduled_at, i.interview_type,
           i.venue_or_link, i.notes, i.outcome, a.job_title, c.name AS company
    FROM `Interview` i
    JOIN `JobApplication` a ON a.application_id = i.application_id
    JOIN `Company` c ON c.company_id = a.company_id
"""

OWNED_INTERVIEW_QUERY = INTERVIEW_QUERY + """
    WHERE i.interview_id = %s AND a.user_id = %s
"""

LIST_QUERY = INTERVIEW_QUERY + """
    WHERE a.user_id = %s
    ORDER BY CASE WHEN i.scheduled_at >= %s THEN 0 ELSE 1 END ASC,
             CASE WHEN i.scheduled_at >= %s THEN i.scheduled_at END ASC,
             CASE WHEN i.scheduled_at < %s THEN i.scheduled_at END DESC,
             i.interview_id ASC
"""

CREATE_QUERY = """
    INSERT INTO `Interview`
        (application_id, scheduled_at, interview_type, venue_or_link, notes, outcome)
    SELECT a.application_id, %s, %s, %s, %s, %s
    FROM `JobApplication` a
    WHERE a.application_id = %s AND a.user_id = %s
"""

UPDATE_QUERY = """
    UPDATE `Interview` i
    JOIN `JobApplication` a ON a.application_id = i.application_id
    SET i.scheduled_at = %s, i.interview_type = %s, i.venue_or_link = %s,
        i.notes = %s, i.outcome = %s
    WHERE i.interview_id = %s AND a.user_id = %s
"""

DELETE_QUERY = """
    DELETE i FROM `Interview` i
    JOIN `JobApplication` a ON a.application_id = i.application_id
    WHERE i.interview_id = %s AND a.user_id = %s
"""


class InterviewNotFound(LookupError):
    """Absent and foreign-owned resources deliberately share one result."""

    def __init__(self):
        super().__init__("Resource not found.")


class InterviewValidationError(ValueError):
    """Fixed field messages for future forms; submitted values are not echoed."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("Invalid interview input.")


class InterviewTransactionError(DatabaseError):
    """The service will not complete or discard someone else's unit of work."""


def _validate_id(value, field):
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise InterviewValidationError({field: "A positive persisted integer ID is required."})


def _utc(value, field="scheduled_at"):
    if not isinstance(value, datetime):
        raise InterviewValidationError({field: "A valid UTC datetime is required."})
    try:
        if value.utcoffset() is not None:
            value = value.astimezone(timezone.utc)
        value = value.replace(tzinfo=None)
    except (ValueError, OverflowError):
        raise InterviewValidationError({field: "A valid UTC datetime is required."}) from None
    if value.year < 1000:
        raise InterviewValidationError({field: "Use a datetime within MySQL's supported range."})
    return value.replace(microsecond=0)


def _values(scheduled_at, interview_type, venue_or_link, notes, outcome):
    scheduled_at = _utc(scheduled_at)
    errors = {}
    if interview_type not in INTERVIEW_TYPES:
        errors["interview_type"] = "Choose online, phone, technical or onsite."
    if outcome not in OUTCOMES:
        errors["outcome"] = "Choose Pending, Passed or Failed."
    if venue_or_link is not None and (not isinstance(venue_or_link, str) or len(venue_or_link) > 500):
        errors["venue_or_link"] = "Use text of at most 500 characters, or leave it empty."
    elif venue_or_link is not None:
        try:
            venue_or_link.encode("utf-8")
        except UnicodeEncodeError:
            errors["venue_or_link"] = "Use valid UTF-8 text."
    if notes is not None:
        try:
            if not isinstance(notes, str) or len(notes.encode("utf-8")) > 65535:
                errors["notes"] = "Use text of at most 65535 UTF-8 bytes, or leave it empty."
        except UnicodeEncodeError:
            errors["notes"] = "Use valid UTF-8 text."
    if errors:
        raise InterviewValidationError(errors)
    return scheduled_at, interview_type, venue_or_link, notes, outcome


def _present(row):
    result = dict(row)
    if result["outcome"] is None:
        result["outcome"] = DEFAULT_OUTCOME
    return result


def _owned_interview(cursor, user_id, interview_id, *, lock=False):
    query = OWNED_INTERVIEW_QUERY + (" FOR UPDATE" if lock else "")
    cursor.execute(query, (interview_id, user_id))
    row = cursor.fetchone()
    if row is None:
        raise InterviewNotFound()
    return _present(row)


def _write(operation):
    connection = get_db()
    try:
        if connection.autocommit is not False or connection.in_transaction:
            raise InterviewTransactionError(
                "Interview writes require an idle connection with autocommit disabled; "
                "finish the caller's transaction first."
            )
    except mysql.connector.Error:
        raise DatabaseError("Could not inspect the interview transaction state.") from None

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
                "Interview rollback failed; the write outcome must be checked before retrying."
            ) from None
        if isinstance(error, mysql.connector.Error):
            raise DatabaseError(
                "Could not save the interview change; check its outcome before retrying."
            ) from None
        raise


def get_owned_application(user_id, application_id):
    """Return application context for the entry point, or InterviewNotFound."""
    _validate_id(user_id, "user_id")
    _validate_id(application_id, "application_id")
    try:
        with closing(get_db().cursor(dictionary=True, buffered=True)) as cursor:
            cursor.execute(APPLICATION_QUERY, (application_id, user_id))
            row = cursor.fetchone()
            if row is None:
                raise InterviewNotFound()
            return dict(row)
    except mysql.connector.Error:
        raise DatabaseError("Could not load the application for this interview.") from None


def list_interviews(user_id, *, cutoff=None):
    """UTC upcoming ascending, past descending, then interview_id ascending."""
    _validate_id(user_id, "user_id")
    cutoff = _utc(datetime.now(timezone.utc) if cutoff is None else cutoff, "cutoff")
    try:
        with closing(get_db().cursor(dictionary=True, buffered=True)) as cursor:
            cursor.execute(LIST_QUERY, (user_id, cutoff, cutoff, cutoff))
            return [_present(row) for row in cursor.fetchall()]
    except mysql.connector.Error:
        raise DatabaseError("Could not load your interviews.") from None


def get_interview(user_id, interview_id):
    """Return all interview fields and display context, or InterviewNotFound."""
    _validate_id(user_id, "user_id")
    _validate_id(interview_id, "interview_id")
    try:
        with closing(get_db().cursor(dictionary=True, buffered=True)) as cursor:
            return _owned_interview(cursor, user_id, interview_id)
    except mysql.connector.Error:
        raise DatabaseError("Could not load this interview.") from None


def create_interview(user_id, application_id, *, scheduled_at, interview_type,
                     venue_or_link=None, notes=None, outcome=DEFAULT_OUTCOME):
    """Create under an owned application; commit and return the full saved row."""
    _validate_id(user_id, "user_id")
    _validate_id(application_id, "application_id")
    values = _values(scheduled_at, interview_type, venue_or_link, notes, outcome)

    def operation(cursor):
        # Lock the parent so deletion/ownership changes cannot race this write.
        cursor.execute(APPLICATION_QUERY + " FOR UPDATE", (application_id, user_id))
        if cursor.fetchone() is None:
            raise InterviewNotFound()
        cursor.execute(CREATE_QUERY, (*values, application_id, user_id))
        if cursor.rowcount != 1:
            raise InterviewNotFound()
        return _owned_interview(cursor, user_id, cursor.lastrowid)

    return _write(operation)


def update_interview(user_id, interview_id, *, scheduled_at, interview_type,
                     outcome, venue_or_link=None, notes=None):
    """Full form replacement of editable fields; application_id is immutable."""
    _validate_id(user_id, "user_id")
    _validate_id(interview_id, "interview_id")
    values = _values(scheduled_at, interview_type, venue_or_link, notes, outcome)

    def operation(cursor):
        _owned_interview(cursor, user_id, interview_id, lock=True)
        cursor.execute(UPDATE_QUERY, (*values, interview_id, user_id))
        # MySQL may report zero affected rows for an unchanged owned interview.
        return _owned_interview(cursor, user_id, interview_id)

    return _write(operation)


def delete_interview(user_id, interview_id):
    """Delete an owned interview and commit; return None, or InterviewNotFound."""
    _validate_id(user_id, "user_id")
    _validate_id(interview_id, "interview_id")

    def operation(cursor):
        _owned_interview(cursor, user_id, interview_id, lock=True)
        cursor.execute(DELETE_QUERY, (interview_id, user_id))
        if cursor.rowcount != 1:
            raise InterviewNotFound()

    return _write(operation)
