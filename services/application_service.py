"""Owner-scoped writes to one JobApplication (CAR-63: notes edited in place on the tracker).

Callers must supply the trusted, persisted User.user_id from Flask-Login, never a
submitted identity. Absent and foreign-owned applications share one result
(ApplicationNotFound), which routes map to 404.

The write owns an operation-scoped ORM transaction. Active legacy or ORM work
is refused without touching the caller. Ownership is locked and rechecked before
updating notes and the explicit naive UTC timestamp.
"""

from datetime import datetime, timezone

from sqlalchemy import select, update

from db import DatabaseError
from models import JobApplication
from orm_db import operation_session, TransactionConflict

# MySQL TEXT holds at most 65,535 bytes; utf8mb4 characters take 1 to 4 bytes each.
NOTES_MAX_BYTES = 65535
MAX_ID = 2147483647  # signed INT primary keys


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


def update_notes(user_id, application_id, notes, *, now=None):
    """Replace an owned application's notes; return {"notes", "updated_at"} as saved."""
    _validate_id(user_id, "user_id")
    _validate_id(application_id, "application_id")
    notes = _clean_notes(notes)
    updated_at = now or _now_utc()

    try:
        with operation_session() as session:
            owned = (JobApplication.application_id == application_id,
                     JobApplication.user_id == user_id)
            identity = session.execute(
                select(JobApplication.application_id).where(*owned).with_for_update()
            ).scalar_one_or_none()
            if identity is None:
                raise ApplicationNotFound()
            session.execute(
                update(JobApplication).where(*owned).values(notes=notes, updated_at=updated_at),
                execution_options={"synchronize_session": False},
            )
        return {"notes": notes, "updated_at": updated_at}
    except TransactionConflict:
        raise ApplicationTransactionError("Finish the caller's transaction before saving notes.") from None
