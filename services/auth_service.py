
# functionality is fully operational but the users are stored locally until database is configured.

"""Registration logic for Careerly (CAR-20).

Kept separate from the routes so it can be unit tested in CAR-75.
"""
import re

from werkzeug.security import generate_password_hash

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8

_users = {}


def normalise_email(email):
    return (email or "").strip().lower()


def email_exists(email):
    return normalise_email(email) in _users


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
    email = normalise_email(form.get("email"))
    user = {
        "name": form.get("name").strip(),
        "email": email,

        "password_hash": generate_password_hash(form.get("password")),
    }
    _users[email] = user
    return user
