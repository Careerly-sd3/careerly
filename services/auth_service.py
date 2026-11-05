"""Registration and login logic for Careerly (CAR-20 and CAR-30).

Kept separate from the routes so it can be unit tested in CAR-75.
Users are stored in the `User` table (CAR-2) through Flask-SQLAlchemy.
"""
import re

from flask_login import UserMixin
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8

# column sizes to match User table
MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 255


class EmailAlreadyExists(Exception):
    """Raised by create_user() when the unique email constraint is hit,
    e.g. two people registering the same email at the same moment."""


class User(UserMixin, db.Model):
    """Maps to the existing `User` table. The table already exists, so do
    not call db.create_all() for it."""

    __tablename__ = "User"

    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    phone_number = db.Column(db.String(30), nullable=True)
    # The database has no default for created_at, so the app supplies it on insert.
    created_at = db.Column(db.DateTime, nullable=False, default=func.now())
    cv_text = db.Column(db.Text, nullable=True)

    @property
    def id(self):
        """Alias so code written against the old in-memory User (user.id) still works."""
        return self.user_id

    def get_id(self):
        """Flask-Login looks for `id` by default; our primary key is user_id."""
        return str(self.user_id)


_DUMMY_HASH = generate_password_hash("not-a-real-password")

def current_user_id():
    """The logged-in user's id, or None when nobody is logged in."""
    return getattr(current_user, "id", None)


def normalise_email(email):
    """Trim spaces and lowercase, so 'A@x.com ' and 'a@x.com' are the same (CAR-24)."""
    return (email or "").strip().lower()


# find user
def email_exists(email):
    return get_user_by_email(email) is not None


def get_user_by_email(email):
    return db.session.execute(
        db.select(User).where(User.email == normalise_email(email))
    ).scalar_one_or_none()


def get_user_by_id(user_id):
    """Flask-Login gives the id back as text, so convert it before the lookup."""
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


# registration

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
    """Save a new user. Call only after validate_registration() returned no errors.

    Raises EmailAlreadyExists if the email was taken between validation and
    the insert (the unique index on User.email is the final safeguard).
    """
    user = User(
        name=form.get("name").strip(),
        email=normalise_email(form.get("email")),
        password_hash=generate_password_hash(form.get("password")),
    )
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise EmailAlreadyExists(user.email)
    return user


# login

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
