"""Who is logged in, and a guard for pages that need a logged-in user (CAR-53)."""

from functools import wraps

from flask import current_app, redirect, session, url_for

# The login feature must store the signed-in user's id in the session under this key:
#     session[SESSION_USER_KEY] = user.user_id
SESSION_USER_KEY = "user_id"


def current_user_id():
    """The logged-in user's id, or None when nobody is logged in."""
    return session.get(SESSION_USER_KEY)


def _login_endpoint():
    # Until the login page exists, visitors are sent to the welcome page instead.
    return "login" if "login" in current_app.view_functions else "index"


def login_required(view):
    """Send visitors who are not logged in away instead of running the page."""

    @wraps(view)
    def guarded_view(*args, **kwargs):
        if current_user_id() is None:
            return redirect(url_for(_login_endpoint()))
        return view(*args, **kwargs)

    return guarded_view
