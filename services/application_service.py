"""Owner-scoped ORM operations on JobApplication rows.

CAR-63: notes edited in place on the tracker (update_notes).
CAR-54: create, read, edit and delete an application (create_application,
get_application, update_application, delete_application).

Callers must supply the trusted, persisted User.user_id from Flask-Login, never a
submitted identity. Absent and foreign-owned applications share one result
(ApplicationNotFound), which routes map to 404.

Every write owns an operation-scoped ORM transaction. Active legacy or ORM work
is refused without touching the caller. Ownership is locked and rechecked before
changing a row, and timestamps are explicit naive UTC values.
"""

from datetime import date, datetime, timezone
import re
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select, update

from db import DatabaseError
from models import Company, Interview, JobApplication
from orm_db import operation_session, TransactionConflict

# MySQL TEXT holds at most 65,535 bytes; utf8mb4 characters take 1 to 4 bytes each.
NOTES_MAX_BYTES = 65535
JOB_AD_MAX_BYTES = 65535
MAX_ID = 2147483647  # signed INT primary keys

# Enforced by the database CHECK constraint on JobApplication.status.
STATUSES = ("Saved", "Applied", "Interview", "Offer", "Rejected", "Withdrawn")
DEFAULT_STATUS = "Saved"
# NOT enforced by the database, so this service is the only gatekeeper.
# TODO: confirm the exact spelling against docs/database/database-design.md
# and what tracker_data.py displays before the form is built.
OPPORTUNITY_TYPES = ("internship", "graduate job")
WORK_TYPES = ("onsite", "hybrid", "remote")

_DATE_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


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


# ---------- field cleaning (CAR-54) ----------
# Each helper records a fixed message in `errors` and returns None on a problem,
# so one submission reports every problem at once.

def _text_field(errors, field, value, max_length, *, missing=None, letters_required=False):
    """One-line text: stray whitespace collapsed, blank becomes None."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        errors[field] = "Use text."
        return None
    value = " ".join(value.split())
    if not value:
        if missing:
            errors[field] = missing
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        errors[field] = "Use valid UTF-8 text."
        return None
    if letters_required and not any(ch.isalpha() for ch in value):
        errors[field] = "Include at least one letter, not just numbers or symbols."
        return None
    if len(value) > max_length:
        errors[field] = f"Use at most {max_length} characters."
        return None
    return value


def _choice_field(errors, field, value, choices, message, *, required=False, default=None):
    if isinstance(value, str):
        value = value.strip()
    if value is None or value == "":
        if default is not None:
            return default
        if required:
            errors[field] = message
        return None
    if value not in choices:
        errors[field] = message
        return None
    return value


def _url_field(errors, field, value):
    """Only absolute http(s) addresses, matching what the tracker will turn into links."""
    text = _text_field(errors, field, value, 500)
    if text is None:
        return None
    message = "Use a full web address starting with http:// or https://."
    if any(ch.isspace() for ch in text):
        errors[field] = message
        return None
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        errors[field] = message
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or port == 0:
        errors[field] = message
        return None
    return text


def _date_field(errors, field, value):
    """A date object or YYYY-MM-DD text; blank becomes None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    parsed = None
    if type(value) is date:
        parsed = value
    elif isinstance(value, str) and _DATE_TEXT.fullmatch(value.strip()):
        try:
            parsed = date.fromisoformat(value.strip())
        except ValueError:
            parsed = None
    if parsed is None or parsed.year < 1000:  # MySQL DATE range starts at year 1000
        errors[field] = "Use a valid date (YYYY-MM-DD)."
        return None
    return parsed


def _long_text_field(errors, field, value):
    if value is None:
        return None
    if not isinstance(value, str):
        errors[field] = "Use text."
        return None
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        return None
    value = value.strip("\n").rstrip()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        errors[field] = "Use valid UTF-8 text."
        return None
    if size > JOB_AD_MAX_BYTES:
        errors[field] = f"Use at most {JOB_AD_MAX_BYTES} bytes of text (about 16,000 to 65,000 characters)."
        return None
    return value


def _clean_fields(*, company, job_title, opportunity_type, work_type, status, location,
                  job_url, closing_date, applied_at, salary_text, job_ad_text):
    """Return (company_name, column_values) or raise ApplicationValidationError.

    Notes are deliberately not part of this form: they keep their own in-place
    editor on the tracker (update_notes), and create/update never touch them.
    """
    errors = {}
    company_name = _text_field(errors, "company", company, 150, missing="Enter the company name.", letters_required=True)
    values = {
        "job_title": _text_field(errors, "job_title", job_title, 150, missing="Enter the job title.", letters_required=True),
        "opportunity_type": _choice_field(
            errors, "opportunity_type", opportunity_type, OPPORTUNITY_TYPES,
            "Choose " + " or ".join(OPPORTUNITY_TYPES) + ".", required=True),
        "work_type": _choice_field(
            errors, "work_type", work_type, WORK_TYPES,
            "Choose " + ", ".join(WORK_TYPES[:-1]) + " or " + WORK_TYPES[-1] + ", or leave it empty."),
        "status": _choice_field(
            errors, "status", status, STATUSES,
            "Choose " + ", ".join(STATUSES[:-1]) + " or " + STATUSES[-1] + ".", default=DEFAULT_STATUS),
        "location": _text_field(errors, "location", location, 150),
        "job_url": _url_field(errors, "job_url", job_url),
        "closing_date": _date_field(errors, "closing_date", closing_date),
        "applied_at": _date_field(errors, "applied_at", applied_at),
        "salary_text": _text_field(errors, "salary_text", salary_text, 100),
        "job_ad_text": _long_text_field(errors, "job_ad_text", job_ad_text),
    }
    # Schema notes describe applied_at as the submission date, empty while the
    # opportunity is only saved, so a Saved application can't carry one.
    if (values["status"] == "Saved" and values["applied_at"] is not None
            and "status" not in errors and "applied_at" not in errors):
        errors["applied_at"] = "Leave the applied date empty while the status is Saved."
    closing_date = values["closing_date"]
    applied_at = values["applied_at"]
    if closing_date is not None and applied_at is not None and closing_date < applied_at:
        errors["closing_date"] = "Closing date cannot be earlier than applied date."
    if errors:
        raise ApplicationValidationError(errors)
    return company_name, values


# ---------- shared queries ----------

def _owned(user_id, application_id):
    return (JobApplication.application_id == application_id,
            JobApplication.user_id == user_id)


def _lock_owned(session, user_id, application_id):
    found = session.execute(
        select(JobApplication.application_id).where(*_owned(user_id, application_id)).with_for_update()
    ).scalar_one_or_none()
    if found is None:
        raise ApplicationNotFound()


def _owned_application(session, user_id, application_id):
    row = session.execute(
        select(*JobApplication.__table__.columns, Company.name.label("company"))
        .select_from(JobApplication)
        .join(Company, Company.company_id == JobApplication.company_id)
        .where(*_owned(user_id, application_id))
    ).mappings().one_or_none()
    if row is None:
        raise ApplicationNotFound()
    return dict(row)


def _interview_count(session, application_id):
    return session.execute(
        select(func.count()).select_from(Interview).where(Interview.application_id == application_id)
    ).scalar_one()


def _company_id(session, user_id, name):
    """Reuse one of THIS user's company rows with exactly this spelling, else create one.

    Company has no owner column and no unique name, so rows are never shared
    between users and an existing row is never renamed or edited. That keeps
    one user's spelling (or a future website_url) from showing up on another
    user's tracker. The database comparison ignores case and accents, so the
    exact-spelling check is done here to keep what the user typed.
    """
    rows = session.execute(
        select(Company.company_id, Company.name)
        .select_from(Company)
        .join(JobApplication, JobApplication.company_id == Company.company_id)
        .where(JobApplication.user_id == user_id, Company.name == name)
        .distinct()
        .order_by(Company.company_id)
    ).all()
    for company_id, existing_name in rows:
        if existing_name == name:
            return company_id
    company = Company(name=name)
    session.add(company)
    session.flush()
    if type(company.company_id) is not int or not 1 <= company.company_id <= MAX_ID:
        raise DatabaseError("Company creation did not return a valid persisted ID.")
    return company.company_id


def _write(operation, error_message):
    try:
        with operation_session(error_message=error_message) as session:
            return operation(session)
    except TransactionConflict:
        raise ApplicationTransactionError(
            "Finish the caller's transaction before saving applications.") from None


# ---------- public operations ----------

def get_application(user_id, application_id):
    """Return every application field, the company name and interview_count, or ApplicationNotFound."""
    _validate_id(user_id, "user_id")
    _validate_id(application_id, "application_id")
    with operation_session(read_only=True, error_message="Could not load this application.") as session:
        result = _owned_application(session, user_id, application_id)
        result["interview_count"] = _interview_count(session, application_id)
    return result


def create_application(user_id, *, company, job_title, opportunity_type, work_type=None,
                       status=DEFAULT_STATUS, location=None, job_url=None, closing_date=None,
                       applied_at=None, salary_text=None, job_ad_text=None, now=None):
    """Create an application owned by user_id; return the committed saved row."""
    _validate_id(user_id, "user_id")
    company_name, values = _clean_fields(
        company=company, job_title=job_title, opportunity_type=opportunity_type,
        work_type=work_type, status=status, location=location, job_url=job_url,
        closing_date=closing_date, applied_at=applied_at, salary_text=salary_text,
        job_ad_text=job_ad_text)
    stamp = now or _now_utc()

    def operation(session):
        record = JobApplication(user_id=user_id, company_id=_company_id(session, user_id, company_name),
                                created_at=stamp, updated_at=stamp, **values)
        session.add(record)
        session.flush()
        if type(record.application_id) is not int or not 1 <= record.application_id <= MAX_ID:
            raise DatabaseError("Application creation did not return a valid persisted ID.")
        return _owned_application(session, user_id, record.application_id)
    return _write(operation, "Could not save the application; check your tracker before retrying.")


def update_application(user_id, application_id, *, company, job_title, opportunity_type,
                       work_type=None, status=DEFAULT_STATUS, location=None, job_url=None,
                       closing_date=None, applied_at=None, salary_text=None, job_ad_text=None,
                       now=None):
    """Full replacement of the editable fields (omitted optional fields are cleared).

    Notes, created_at and the owner never change here. A save that changes
    nothing succeeds and leaves updated_at alone.
    """
    _validate_id(user_id, "user_id")
    _validate_id(application_id, "application_id")
    company_name, values = _clean_fields(
        company=company, job_title=job_title, opportunity_type=opportunity_type,
        work_type=work_type, status=status, location=location, job_url=job_url,
        closing_date=closing_date, applied_at=applied_at, salary_text=salary_text,
        job_ad_text=job_ad_text)
    stamp = now or _now_utc()

    def operation(session):
        _lock_owned(session, user_id, application_id)
        current = _owned_application(session, user_id, application_id)
        new = dict(values, company_id=_company_id(session, user_id, company_name))
        if all(current[key] == value for key, value in new.items()):
            return current
        session.execute(
            update(JobApplication).where(*_owned(user_id, application_id)).values(**new, updated_at=stamp),
            execution_options={"synchronize_session": False})
        return _owned_application(session, user_id, application_id)
    return _write(operation, "Could not save the application; check your tracker before retrying.")


def delete_application(user_id, application_id):
    """Delete an owned application; its interviews and AI analyses go with it (ON DELETE CASCADE).

    Returns {"application_id", "interviews_deleted"}, or raises ApplicationNotFound.
    The Company row is left in place.
    """
    _validate_id(user_id, "user_id")
    _validate_id(application_id, "application_id")

    def operation(session):
        _lock_owned(session, user_id, application_id)
        interviews = _interview_count(session, application_id)
        result = session.execute(
            delete(JobApplication).where(*_owned(user_id, application_id)),
            execution_options={"synchronize_session": False})
        if result.rowcount != 1:
            raise ApplicationNotFound()
        return {"application_id": application_id, "interviews_deleted": interviews}
    return _write(operation, "Could not delete the application; check your tracker before retrying.")


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
