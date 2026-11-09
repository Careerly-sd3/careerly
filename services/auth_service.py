"""Persistent registration/login via the shared verified MySQL connection.

No environment loading, schema creation, ORM or in-memory user fallback.
Reads finish only read transactions they opened. Registration requires an idle
connection and owns its explicit transaction, preserving unrelated caller work.
"""
from contextlib import closing
from datetime import datetime, timezone
import re

from flask_login import UserMixin, current_user
import mysql.connector
from werkzeug.security import check_password_hash, generate_password_hash

from db import DatabaseError, get_db

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 255

USER_COLUMNS = "`user_id`, `name`, `email`, `password_hash`, `phone_number`, `created_at`, `cv_text`"
USER_BY_EMAIL = "SELECT " + USER_COLUMNS + " FROM `User` WHERE `email` = %s"
USER_BY_ID = "SELECT " + USER_COLUMNS + " FROM `User` WHERE `user_id` = %s"
CREATE_USER = """
    INSERT INTO `User` (`name`, `email`, `password_hash`, `created_at`)
    VALUES (%s, %s, %s, %s)
"""


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


def _read_user(query, parameters):
    connection = get_db()
    try:
        owns_read = not connection.in_transaction
    except mysql.connector.Error:
        raise DatabaseError("Could not inspect the authentication transaction.") from None
    try:
        with closing(connection.cursor(dictionary=True, buffered=True)) as cursor:
            cursor.execute(query, parameters)
            row = cursor.fetchone()
        if row is None:
            return None
        return User(id=row["user_id"], name=row["name"], email=row["email"],
                    password_hash=row["password_hash"], phone_number=row.get("phone_number"),
                    created_at=row.get("created_at"), cv_text=row.get("cv_text"))
    except mysql.connector.Error:
        raise DatabaseError("Could not load the user account. Please try again later.") from None
    finally:
        if owns_read:
            try:
                connection.rollback()
            except mysql.connector.Error:
                raise DatabaseError("Could not finish the authentication read transaction.") from None


def get_user_by_email(email):
    return _read_user(USER_BY_EMAIL, (normalise_email(email),))


def get_user_by_id(user_id):
    """Invalid IDs resolve without SQL; absent persisted users also return None."""
    identity = _persisted_id(user_id)
    return None if identity is None else _read_user(USER_BY_ID, (identity,))


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
    connection = get_db()
    try:
        if connection.autocommit is not False or connection.in_transaction:
            raise DatabaseError("Finish the caller's transaction before registering a user.")
    except mysql.connector.Error:
        raise DatabaseError("Could not inspect the registration transaction.") from None
    try:
        connection.start_transaction()
        with closing(connection.cursor(dictionary=True, buffered=True)) as cursor:
            cursor.execute(CREATE_USER, (name, email, password_hash, created_at))
            identity = _persisted_id(cursor.lastrowid)
            if identity is None:
                raise DatabaseError("Registration did not return a valid database user ID.")
        connection.commit()
        return User(id=identity, name=name, email=email, password_hash=password_hash, created_at=created_at)
    except BaseException as error:
        try:
            connection.rollback()
        except mysql.connector.Error:
            raise DatabaseError("Registration rollback failed; check the outcome before retrying.") from None
        if isinstance(error, mysql.connector.Error):
            if error.errno == 1062:
                raise EmailAlreadyExists() from None
            raise DatabaseError("Could not register the account; check the outcome before retrying.") from None
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
