"""Registration and login logic for Careerly (CAR-20 and CAR-30).

Users are stored in the shared MySQL database through db.get_db().
Kept separate from the routes so it can be unit tested in CAR-75
(patch email_exists / get_db in tests so no real database is needed).
"""
import re

import mysql.connector
from flask_login import UserMixin, current_user
from mysql.connector import errorcode
from werkzeug.security import check_password_hash, generate_password_hash

from db import DatabaseError, get_db

# Table name exactly as it appears in the database. MySQL on Linux treats
# table names as case-sensitive, so check it with SHOW TABLES, or see how
# tracker_data.py writes it, and change this one line if it differs.
USER_TABLE = "User"

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_NAME_LENGTH = 100    # User.name is VARCHAR(100)
MAX_EMAIL_LENGTH = 255   # User.email is VARCHAR(255)

_DUMMY_HASH = generate_password_hash("not-a-real-password")


class EmailAlreadyExists(Exception):
    """The email is already registered (the database's UNIQUE index caught it)."""


class User(UserMixin):
    """What Flask-Login needs to represent a logged-in user.

    UserMixin supplies is_authenticated, is_active, get_id() and so on.
    `id` holds the User.user_id column; `user_id` is an alias for code
    that prefers the column name.
    """

    def __init__(self, id, name, email, password_hash):
        self.id = id
        self.name = name
        self.email = email
        self.password_hash = password_hash

    @property
    def user_id(self):
        return self.id


def current_user_id():
    """The logged-in user's id, or None when nobody is logged in."""
    return getattr(current_user, "id", None)


def normalise_email(email):
    """Trim spaces and lowercase, so 'A@x.com ' and 'a@x.com' are the same (CAR-24)."""
    return (email or "").strip().lower()


# ---------- database helpers ----------

def _db_error():
    return DatabaseError(
        "The user database could not be reached. Please try again in a moment."
    )


def _query_one(sql, params):
    """Run a SELECT and return one row as a dict, or None."""
    connection = get_db()  # raises DatabaseError if it can't connect
    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, params)
        return cursor.fetchone()
    except mysql.connector.Error:
        raise _db_error() from None
    finally:
        if cursor is not None:
            cursor.close()


def _row_to_user(row):
    if row is None:
        return None
    return User(
        id=row["user_id"],
        name=row["name"],
        email=row["email"],
        password_hash=row["password_hash"],
    )


def _rollback(connection):
    try:
        connection.rollback()
    except mysql.connector.Error:
        pass


# ---------- finding users ----------

def email_exists(email):
    row = _query_one(
        f"SELECT user_id FROM `{USER_TABLE}` WHERE email = %s",
        (normalise_email(email),),
    )
    return row is not None


def get_user_by_email(email):
    return _row_to_user(_query_one(
        f"SELECT user_id, name, email, password_hash FROM `{USER_TABLE}` WHERE email = %s",
        (normalise_email(email),),
    ))


def get_user_by_id(user_id):
    """Flask-Login gives the id back as text, so convert it. A bad or
    tampered value just means "not logged in"."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    return _row_to_user(_query_one(
        f"SELECT user_id, name, email, password_hash FROM `{USER_TABLE}` WHERE user_id = %s",
        (user_id,),
    ))


# ---------- registration ----------

# special characters, lower/uppercase addition for passwords maybe.
# feedback: accessibility (speech access, colorblind, etc.)

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
    elif not EMAIL_PATTERN.match(email):
        errors["email"] = "Please enter a valid email address."
    elif len(email) > MAX_EMAIL_LENGTH:
        errors["email"] = "That email address is too long."
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
    """Save a new user and return it. Call only after validate_registration()
    returned no errors.

    Raises EmailAlreadyExists if the email was taken between validation and
    the insert (the UNIQUE index on User.email is the final safeguard).
    """
    email = normalise_email(form.get("email"))
    name = form.get("name").strip()
    password_hash = generate_password_hash(form.get("password"))

    connection = get_db()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"INSERT INTO `{USER_TABLE}` (name, email, password_hash, created_at) "
            "VALUES (%s, %s, %s, NOW())",
            (name, email, password_hash),
        )
        connection.commit()  # db.py never commits for us
        user_id = cursor.lastrowid
    except mysql.connector.IntegrityError as error:
        _rollback(connection)
        if error.errno == errorcode.ER_DUP_ENTRY:
            raise EmailAlreadyExists(email) from None
        raise _db_error() from None
    except mysql.connector.Error:
        _rollback(connection)
        raise _db_error() from None
    finally:
        if cursor is not None:
            cursor.close()

    return User(id=user_id, name=name, email=email, password_hash=password_hash)


# ---------- login ----------

def validate_login(form):
    """CAR-33: both fields are required (spaces only counts as empty)."""
    errors = {}
    if not (form.get("email") or "").strip():
        errors["email"] = "Enter your email address."
    if not (form.get("password") or "").strip():
        errors["password"] = "Enter your password."
    return errors


def authenticate(email, password):
    """CAR-34: return the User if the details are correct, otherwise None.

    The caller shows the same message for "no such email" and "wrong
    password" (CAR-35), so this returns None for both.
    """
    user = get_user_by_email(email)
    if user is None:
        # Hash anyway so a missing email takes about as long as a wrong password.
        check_password_hash(_DUMMY_HASH, password or "")
        return None
    if check_password_hash(user.password_hash, password or ""):
        return user
    return None
