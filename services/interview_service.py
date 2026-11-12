"""Owner-scoped ORM interview operations returning fully materialised dictionaries.

Public operations own explicit sessions; Tracker shares its read session through
_list_interviews. Writes lock/recheck ownership and commit atomically. Legacy or
nested ORM caller work is refused untouched. Naive UTC/Dublin rules are unchanged.
"""
from datetime import datetime, timezone

from sqlalchemy import select, update, delete, case, bindparam

from db import DatabaseError
from models import JobApplication, Company, Interview
from orm_db import operation_session, TransactionConflict


INTERVIEW_TYPES = ("online", "phone", "technical", "onsite")
OUTCOMES = ("Pending", "Passed", "Failed")
DEFAULT_OUTCOME = "Pending"


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


def _validate_notes(notes):
    if notes is not None:
        try:
            if not isinstance(notes, str) or len(notes.encode("utf-8")) > 65535:
                raise InterviewValidationError({"notes": "Use text of at most 65535 UTF-8 bytes, or leave it empty."})
        except UnicodeEncodeError:
            raise InterviewValidationError({"notes": "Use valid UTF-8 text."}) from None


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
    try:
        _validate_notes(notes)
    except InterviewValidationError as error:
        errors.update(error.errors)
    if errors:
        raise InterviewValidationError(errors)
    return scheduled_at, interview_type, venue_or_link, notes, outcome


def _present(row):
    result = dict(row)
    if result["outcome"] is None:
        result["outcome"] = DEFAULT_OUTCOME
    return result


def _application_statement(user_id, application_id):
    return (select(JobApplication.application_id, JobApplication.user_id,
                   JobApplication.company_id, JobApplication.job_title,
                   JobApplication.status, Company.name.label("company"))
            .select_from(JobApplication)
            .join(Company, Company.company_id == JobApplication.company_id)
            .where(JobApplication.application_id == application_id,
                   JobApplication.user_id == user_id))


def _interview_statement():
    return (select(*Interview.__table__.columns, JobApplication.job_title,
                   Company.name.label("company"))
            .select_from(Interview)
            .join(JobApplication, JobApplication.application_id == Interview.application_id)
            .join(Company, Company.company_id == JobApplication.company_id))


def _owned_interview(session, user_id, interview_id, *, lock=False):
    query = _interview_statement().where(
        Interview.interview_id == interview_id, JobApplication.user_id == user_id)
    if lock:
        query = query.with_for_update()
    row = session.execute(query).mappings().one_or_none()
    if row is None:
        raise InterviewNotFound()
    return _present(row)


def _write(operation):
    try:
        with operation_session(
            error_message="Could not save the interview change; check its outcome before retrying.",
        ) as session:
            result = operation(session)
        return result
    except TransactionConflict:
        raise InterviewTransactionError("Finish the caller's transaction before saving interviews.") from None


def get_owned_application(user_id, application_id):
    """Return application context for the entry point, or InterviewNotFound."""
    _validate_id(user_id, "user_id")
    _validate_id(application_id, "application_id")
    with operation_session(
        read_only=True, error_message="Could not load the application for this interview.",
    ) as session:
        row = session.execute(_application_statement(user_id, application_id)).mappings().one_or_none()
        if row is None:
            raise InterviewNotFound()
        result = dict(row)
    return result


def _list_statement(user_id, cutoff):
    cutoff = bindparam("cutoff", cutoff)
    upcoming = Interview.scheduled_at >= cutoff
    return (_interview_statement().where(JobApplication.user_id == user_id)
            .order_by(case((upcoming, 0), else_=1).asc(),
                      case((upcoming, Interview.scheduled_at)).asc(),
                      case((Interview.scheduled_at < cutoff, Interview.scheduled_at)).desc(),
                      Interview.interview_id.asc()))


def _list_interviews(session, user_id, *, cutoff):
    """Borrow Tracker's explicitly owned read unit; never begin/end it here."""
    _validate_id(user_id, "user_id")
    cutoff = _utc(cutoff, "cutoff")
    return [_present(row) for row in session.execute(_list_statement(user_id, cutoff)).mappings().all()]


def list_interviews(user_id, *, cutoff=None):
    """UTC upcoming ascending, past descending, then interview_id ascending."""
    _validate_id(user_id, "user_id")
    cutoff = _utc(datetime.now(timezone.utc) if cutoff is None else cutoff, "cutoff")
    with operation_session(read_only=True, error_message="Could not load your interviews.") as session:
        result = _list_interviews(session, user_id, cutoff=cutoff)
    return result


def get_interview(user_id, interview_id):
    """Return all interview fields and display context, or InterviewNotFound."""
    _validate_id(user_id, "user_id")
    _validate_id(interview_id, "interview_id")
    with operation_session(read_only=True, error_message="Could not load this interview.") as session:
        result = _owned_interview(session, user_id, interview_id)
    return result


def _mutation_predicates(user_id, interview_id):
    return (Interview.interview_id == interview_id,
            Interview.application_id.in_(select(JobApplication.application_id)
                                         .where(JobApplication.user_id == user_id)))


def create_interview(user_id, application_id, *, scheduled_at, interview_type,
                     venue_or_link=None, notes=None, outcome=DEFAULT_OUTCOME):
    """Create under a locked owned application; return the committed saved row."""
    _validate_id(user_id, "user_id")
    _validate_id(application_id, "application_id")
    values = _values(scheduled_at, interview_type, venue_or_link, notes, outcome)

    def operation(session):
        parent = session.execute(_application_statement(user_id, application_id).with_for_update()).mappings().one_or_none()
        if parent is None:
            raise InterviewNotFound()
        record = Interview(application_id=parent["application_id"],
                           **dict(zip(("scheduled_at", "interview_type", "venue_or_link", "notes", "outcome"), values)))
        session.add(record)
        session.flush()
        if type(record.interview_id) is not int or not 1 <= record.interview_id <= 2147483647:
            raise DatabaseError("Interview creation did not return a valid persisted ID.")
        return _owned_interview(session, user_id, record.interview_id)
    return _write(operation)


def update_interview(user_id, interview_id, *, scheduled_at, interview_type,
                     outcome, venue_or_link=None, notes=None):
    """Full replacement of editable fields; application_id stays immutable."""
    _validate_id(user_id, "user_id")
    _validate_id(interview_id, "interview_id")
    values = _values(scheduled_at, interview_type, venue_or_link, notes, outcome)

    def operation(session):
        _owned_interview(session, user_id, interview_id, lock=True)
        session.execute(update(Interview).where(*_mutation_predicates(user_id, interview_id))
                        .values(**dict(zip(("scheduled_at", "interview_type", "venue_or_link", "notes", "outcome"), values))),
                        execution_options={"synchronize_session": False})
        return _owned_interview(session, user_id, interview_id)
    return _write(operation)


def delete_interview(user_id, interview_id):
    """Delete an owned interview and commit, or InterviewNotFound."""
    _validate_id(user_id, "user_id")
    _validate_id(interview_id, "interview_id")

    def operation(session):
        _owned_interview(session, user_id, interview_id, lock=True)
        result = session.execute(delete(Interview).where(*_mutation_predicates(user_id, interview_id)),
                                 execution_options={"synchronize_session": False})
        if result.rowcount != 1:
            raise InterviewNotFound()
    return _write(operation)


def update_interview_notes(user_id, interview_id, *, notes):
    """Update only notes, preserving exact whitespace and other stored fields."""
    _validate_id(user_id, "user_id")
    _validate_id(interview_id, "interview_id")
    _validate_notes(notes)

    def operation(session):
        _owned_interview(session, user_id, interview_id, lock=True)
        session.execute(update(Interview).where(*_mutation_predicates(user_id, interview_id)).values(notes=notes),
                        execution_options={"synchronize_session": False})
        return _owned_interview(session, user_id, interview_id)
    return _write(operation)
