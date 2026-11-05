
# functionality is fully operational but the users are stored locally until database is configured.

"""Registration and login logic for Careerly (CAR-20 and CAR-30).

Kept separate from the routes so it can be unit tested in CAR-75.
"""
import re

from flask_login import UserMixin, current_user
from werkzeug.security import check_password_hash, generate_password_hash

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


class User(UserMixin):
    """What Flask-Login needs to represent a logged-in user.

    UserMixin supplies is_authenticated, is_active, get_id() and so on.
    TEMPORARY: when the database exists, make this the SQLAlchemy User
    model (inheriting UserMixin) instead of a plain class.
    """

    def __init__(self, id, name, email, password_hash):
        self.id = id
        self.name = name
        self.email = email
        self.password_hash = password_hash


# temp in-memory store so the pages work before the database is connected.
# need to replace email_exists(), create_user(), get_user_by_email() and
# get_user_by_id() with queries on the User table (CAR-2).
_users = {}
_next_id = 1

_DUMMY_HASH = generate_password_hash("not-a-real-password")

def current_user_id():
    """The logged-in user's id, or None when nobody is logged in."""
    return getattr(current_user, "id", None)


def normalise_email(email):
    """Trim spaces and lowercase, so 'A@x.com ' and 'a@x.com' are the same (CAR-24)."""
    return (email or "").strip().lower()


# find user
def email_exists(email):
    return normalise_email(email) in _users


def get_user_by_email(email):
    return _users.get(normalise_email(email))


def get_user_by_id(user_id):
    """Flask-Login gives the id back as text, so compare as text."""
    for user in _users.values():
        if str(user.id) == str(user_id):
            return user
    return None


#registration

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

    if not email:
        errors["email"] = "Enter your email address."
    elif not EMAIL_PATTERN.match(email):
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
    """Save a new user. Call only after validate_registration() returned no errors."""
    global _next_id
    email = normalise_email(form.get("email"))
    _next_id += 1
    _users[email] = User(
        id=_next_id,
        name=form.get("name").strip(),
        email=email,
        password_hash=generate_password_hash(form.get("password")),
    )


#login

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
        check_password_hash(_DUMMY_HASH, password or "")
        return None
    if check_password_hash(user.password_hash, password or ""):
        return user
    return None
