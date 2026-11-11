"""Small session-bound CSRF hook for explicit protected mutation routes.

Uses Flask's existing signed session; no second secret or package is needed.
Register the template provider once and decorate each mutation endpoint that
uses it. A rendered token is not protection without server-side validation.
"""
from functools import wraps
import hmac
import secrets

from flask import abort, request, session


SESSION_KEY = "_csrf_token"


def csrf_token():
    token = session.get(SESSION_KEY)
    if not isinstance(token, str) or not token or not token.isascii():
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def clear_csrf_token():
    session.pop(SESSION_KEY, None)


def validate_csrf_token(token):
    expected = session.get(SESSION_KEY)
    if not (isinstance(expected, str) and expected and expected.isascii()
            and isinstance(token, str) and token and token.isascii()
            and hmac.compare_digest(expected, token)):
        abort(400, description="Missing or invalid CSRF token.")


def csrf_protect(view):
    @wraps(view)
    def protected(*args, **kwargs):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            validate_csrf_token(request.form.get("csrf_token"))
        return view(*args, **kwargs)
    return protected


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
