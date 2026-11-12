"""Persistent authentication through operation-scoped SQLAlchemy transactions.

Reads roll back and close before subsequent writes. All operations refuse active
legacy/ORM caller work without touching it. Routes receive the detached User
adapter, never a mapped entity. No environment loading or schema creation.
"""
from datetime import datetime, timezone
import re

from flask_login import UserMixin, current_user
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from db import DatabaseError
from models import User as UserRecord
from orm_db import operation_session, ORMDatabaseError

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 255


class EmailAlreadyExists(Exception):
    """Only MySQL duplicate-key error 1062 is translated to this result."""

    def __init__(self):
        super().__init__("An account with this email already exists.")


class User(UserMixin):
    """Flask-Login adapter for a persisted User row; no second data model."""

    def __init__(self, id, name, email, password_hash, *, phone_number=None,
                 created_at=None, cv_text=None):
        self.user_id = id
        self.name = name
        self.email = email
        self.password_hash = password_hash
        self.phone_number = phone_number
        self.created_at = created_at
        self.cv_text = cv_text

    @property
    def id(self):
        return self.user_id

    def get_id(self):
        return str(self.user_id)


def _persisted_id(value):
    if type(value) is int:
        return value if 1 <= value <= 2147483647 else None
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,9}", value):
        identity = int(value)
        return identity if identity <= 2147483647 else None
    return None


def current_user_id():
    """Canonical ownership ID, from the persisted Flask-Login user only."""
    if not current_user.is_authenticated:
        return None
    return _persisted_id(getattr(current_user, "user_id", None))


def normalise_email(email):
    return (email or "").strip().lower()


def _adapter(record):
    return User(id=record.user_id, name=record.name, email=record.email,
                password_hash=record.password_hash, phone_number=record.phone_number,
                created_at=record.created_at, cv_text=record.cv_text)


def _read_user(statement):
    with operation_session(
        read_only=True,
        error_message="Could not load the user account. Please try again later.",
    ) as session:
        record = session.execute(statement).scalar_one_or_none()
        result = None if record is None else _adapter(record)
    return result


def get_user_by_email(email):
    return _read_user(select(UserRecord).where(UserRecord.email == normalise_email(email)))


def get_user_by_id(user_id):
    """Invalid IDs resolve without SQL; absent persisted users also return None."""
    identity = _persisted_id(user_id)
    return None if identity is None else _read_user(
        select(UserRecord).where(UserRecord.user_id == identity))


def email_exists(email):
    return get_user_by_email(email) is not None


def validate_registration(form):
    """Check the submitted form and return {field_name: message}.

    An empty dict means the form is valid. All problems are returned
    at once so the page can show them together (CAR-28).
    """
    errors = {}
    name = (form.get("name") or "").strip()
    email = normalise_email(form.get("email"))
    password = form.get("password") or ""
    confirm = form.get("confirm_password") or ""

    if not name:
        errors["name"] = "Enter your name."
    elif len(name) > MAX_NAME_LENGTH:
        errors["name"] = f"Name must be {MAX_NAME_LENGTH} characters or fewer."

    if not email:
        errors["email"] = "Enter your email address."
    elif len(email) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(email):
        errors["email"] = "Please enter a valid email address."
    elif email_exists(email):
        errors["email"] = "An account with this email already exists."

    if not password.strip():
        errors["password"] = "Enter a password."
    elif len(password) < MIN_PASSWORD_LENGTH:
        errors["password"] = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."

    if not confirm.strip():
        errors["confirm_password"] = "Confirm your password."
    elif password != confirm:
        errors["confirm_password"] = "Passwords do not match."

    return errors


def create_user(form):
    """After validation, insert/commit and return User with the generated ID.

    A preflight email check is not authority: concurrent duplicates are caught
    by User.email's unique constraint. No caller-provided user_id is inserted.
    """
    name = (form.get("name") or "").strip()
    email = normalise_email(form.get("email"))
    password_hash = generate_password_hash(form.get("password"))
    created_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    try:
        with operation_session(
            error_message="Could not register the account; check the outcome before retrying.",
        ) as session:
            record = UserRecord(name=name, email=email, password_hash=password_hash,
                                created_at=created_at)
            session.add(record)
            session.flush()
            if _persisted_id(record.user_id) is None:
                raise DatabaseError("Registration did not return a valid database user ID.")
            result = _adapter(record)
        # Return only after the operation context has committed and closed.
        return result
    except ORMDatabaseError as error:
        if error.connector_errno == 1062:
            raise EmailAlreadyExists() from None
        raise


def validate_login(form):
    """CAR-33: both fields are required (spaces only counts as empty)."""
    errors = {}
    if not (form.get("email") or "").strip():
        errors["email"] = "Enter your email address."
    if not (form.get("password") or "").strip():
        errors["password"] = "Enter your password."
    return errors


# Preserve the existing Werkzeug hash formats; never rehash/reset stored users
# during login. Unknown emails still perform synthetic hash verification work.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def authenticate(email, password):
    user = get_user_by_email(email)
    if user is None:
        check_password_hash(_DUMMY_HASH, password or "")
        return None
    try:
        return user if check_password_hash(user.password_hash, password or "") else None
    except ValueError:
        # Malformed/unsupported stored hashes fail closed without being logged.
        return None
