import os
from datetime import timedelta

from db import DatabaseError, init_app

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user

from services.csrf import clear_csrf_token, csrf_protect, init_app as init_csrf
from services import application_service, interview_service
from services.interview_time import parse_dublin_datetime, utc_to_dublin

from services.auth_service import (
    EmailAlreadyExists,
    authenticate,
    create_user,
    get_user_by_id,
    validate_login,
    validate_registration,
    current_user_id
)
from tracker_data import (
    DISPLAY_DATE,
    PRACTICE_QUESTIONS,
    STATUS_THEMES,
    load_tracker_page,
    load_interviews_page,
)

load_dotenv()


app = Flask(__name__)
init_app(app)

secret_key = os.environ.get("SECRET_KEY")
if not secret_key or not secret_key.strip():
    raise RuntimeError("SECRET_KEY must be configured before starting Careerly.")
app.config["SECRET_KEY"] = secret_key

# IMPORTANT: "SECURE_COOKIES" is for later (HTTPS deployment, car-77)
# what this does:
# 1) _HTTPONLY >> prevent js from reading cookie data, for XSS bugs
# 2) _SAMESITE >> cookie only shared for same site requests
# 3) _SECURE >> only send cookie over HTTPS (127.0.0.1 is considered safe on most broswers)
# 4) REMEMBER_COOKIE_* >> makes the created cookier safer
# 5) _LIFETIME >> dictates max session length (we can change it based on what you guys think)
secure_cookies = os.environ.get("SECURE_COOKIES") == "0"   # ignore this line for now
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=secure_cookies,
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_SECURE=secure_cookies,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

init_csrf(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "danger"


@app.errorhandler(DatabaseError)
def database_unavailable(error):
    return render_template("service-unavailable.html"), 503


@login_manager.user_loader
def load_user(user_id):
    """Flask-Login calls this on every request with the id from the session
    cookie. Returning None means "not logged in"."""
    try:
        return get_user_by_id(user_id)
    except DatabaseError:
        # Preserve the signed session identity for recovery on the next request.
        # This is an unavailable account lookup, not invalid credentials.
        g.auth_database_unavailable = True
        return None


@app.before_request
def account_service_availability():
    if request.endpoint == "static":
        return None
    # Resolve Flask-Login once before a protected view or template can use it.
    current_user.is_authenticated
    if getattr(g, "auth_database_unavailable", False):
        public_read = request.method in ("GET", "HEAD") and request.endpoint in ("index", "login", "register")
        if not public_read and request.endpoint != "logout":
            return database_unavailable(None)


def safe_next_url(target):
    """only allow redirects to pages on our own site."""
    if (
        target
        and target.startswith("/")
        and not target.startswith("//")
        and "\\" not in target
    ):
        return target
    return None


# pages


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/tracker")
@login_required
def tracker():
    status_code = 200
    status = request.args.get("status", "")
    company = request.args.get("company", "")
    role = request.args.get("role", "")
    try:
        page = load_tracker_page(current_user_id(), status=status, company=company, role=role)
    except DatabaseError as error:
        flash(str(error), "danger")
        page = {"applications": [], "interviews": [], "has_more_interviews": False, "practice_meta": "",
                "status_counts": {}, "total_applications": 0}
        status_code = 503
    return render_template(
        "tracker.html",
        **page,
        status=status,
        company=company,
        role=role,
        practice_questions=PRACTICE_QUESTIONS,
        status_themes=STATUS_THEMES,
    ), status_code


NOTES_SAVE_FAILED = "Could not save your notes. Check your tracker before retrying."


@app.route("/applications/<int:application_id>/notes", methods=["POST"])
@login_required
@csrf_protect
def save_application_notes(application_id):
    """Save the notes typed into an application's Notes panel; answers the tracker's editor in JSON."""
    try:
        saved = application_service.update_notes(
            current_user_id(), application_id, request.form.get("notes"))
    except application_service.ApplicationNotFound:
        abort(404)
    except application_service.ApplicationValidationError as error:
        if "notes" not in error.errors:
            abort(404)  # an out-of-range id in the address is simply not one of the user's
        return jsonify(error=error.errors["notes"]), 400
    except DatabaseError:
        return jsonify(error=NOTES_SAVE_FAILED), 503
    return jsonify(notes=saved["notes"] or "",
                   updated_text=utc_to_dublin(saved["updated_at"]).strftime(DISPLAY_DATE))


def interview_form_values():
    """Whitelist editable fields; ownership/association never come from the form."""
    form = {name: request.form.get(name, "") for name in
            ("date", "time", "interview_type", "venue_or_link", "notes")}
    form["outcome"] = request.form.get("outcome", interview_service.DEFAULT_OUTCOME)
    return form


def interview_save_values(form):
    """Shared create/edit wall-clock parsing; the service validates other fields."""
    return dict(scheduled_at=parse_dublin_datetime(form["date"], form["time"]),
                interview_type=form["interview_type"], outcome=form["outcome"],
                venue_or_link=form["venue_or_link"] or None, notes=form["notes"] or None)


@app.route("/applications/<int:application_id>/interviews/new", methods=["GET", "POST"])
@login_required
@csrf_protect
def add_interview(application_id):
    user_id = current_user_id()
    form = interview_form_values()

    def show_form(errors=None, status=200, application=None):
        return render_template(
            "interviews/form.html", mode="create", application=application,
            form=form, errors=errors or {},
            action_url=url_for("add_interview", application_id=application_id),
            cancel_url=url_for("tracker"),
        ), status

    # Do not use any posted user_id/application_id/interview_id for authority.
    try:
        application = interview_service.get_owned_application(user_id, application_id)
    except (interview_service.InterviewNotFound, interview_service.InterviewValidationError):
        abort(404)
    except DatabaseError:
        return show_form({"_form": "Could not load the application. Please try again later."}, 503)

    if request.method == "POST":
        try:
            interview_service.create_interview(
                user_id, application_id, **interview_save_values(form),
            )
        except interview_service.InterviewNotFound:
            abort(404)  # Parent could have disappeared after the preflight read.
        except interview_service.InterviewValidationError as error:
            return show_form(error.errors, 400, application)
        except DatabaseError:
            return show_form({"_form": (
                "Could not save the interview. Check your Interviews page before retrying."
            )}, 503, application)
        flash("Interview added.", "success")
        return redirect(url_for("interviews"))
    return show_form(application=application)


def owned_interview_or_404(interview_id):
    try:
        return interview_service.get_interview(current_user_id(), interview_id)
    except (interview_service.InterviewNotFound, interview_service.InterviewValidationError):
        abort(404)


@app.route("/interviews/<int:interview_id>/edit", methods=["GET", "POST"])
@login_required
@csrf_protect
def edit_interview(interview_id):
    interview = owned_interview_or_404(interview_id)
    local = utc_to_dublin(interview["scheduled_at"])
    form = interview_form_values() if request.method == "POST" else dict(
        date=local.strftime("%Y-%m-%d"), time=local.strftime("%H:%M:%S"),
        interview_type=interview["interview_type"], venue_or_link=interview["venue_or_link"] or "",
        notes=interview["notes"] or "", outcome=interview["outcome"],
    )

    def show_form(errors=None, status=200):
        return render_template("interviews/form.html", mode="edit", application=interview,
                               form=form, errors=errors or {},
                               action_url=url_for("edit_interview", interview_id=interview_id),
                               cancel_url=url_for("interviews")), status

    if request.method == "POST":
        try:
            interview_service.update_interview(
                current_user_id(), interview_id, **interview_save_values(form))
        except interview_service.InterviewNotFound:
            abort(404)
        except interview_service.InterviewValidationError as error:
            return show_form(error.errors, 400)
        except DatabaseError:
            return show_form({"_form": "Could not save the interview. Check your Interviews page before retrying."}, 503)
        flash("Interview updated.", "success")
        return redirect(url_for("interviews"))
    return show_form()


@app.route("/interviews/<int:interview_id>/delete", methods=["GET", "POST"])
@login_required
@csrf_protect
def delete_interview(interview_id):
    interview = owned_interview_or_404(interview_id)
    error = None
    if request.method == "POST":
        try:
            # The service locks and rechecks ownership inside its write transaction.
            interview_service.delete_interview(current_user_id(), interview_id)
        except interview_service.InterviewNotFound:
            abort(404)
        except DatabaseError:
            error = "Could not delete the interview. Check your Interviews page before retrying."
        else:
            flash("Interview deleted.", "success")
            return redirect(url_for("interviews"))
    return render_template("interviews/delete.html", interview=interview,
                           scheduled_local=utc_to_dublin(interview["scheduled_at"]),
                           error=error), 503 if error else 200


@app.post("/interviews/<int:interview_id>/notes")
@login_required
@csrf_protect
def save_interview_notes(interview_id):
    user_id = current_user_id()
    try:
        # Preflight first, so valid-CSRF foreign requests return 404 even if
        # their input is invalid. The service rechecks under its write lock.
        interview_service.get_interview(user_id, interview_id)
    except (interview_service.InterviewNotFound, interview_service.InterviewValidationError):
        return jsonify(ok=False, message="Interview not found."), 404
    except DatabaseError:
        return jsonify(ok=False, message="Could not load the interview. Please try again later."), 503
    if "notes" not in request.form:
        return jsonify(ok=False, errors={"notes": "Supply notes, or an empty value to clear them."}), 400
    try:
        saved = interview_service.update_interview_notes(user_id, interview_id, notes=request.form["notes"])
    except interview_service.InterviewNotFound:
        return jsonify(ok=False, message="Interview not found."), 404
    except interview_service.InterviewValidationError as error:
        return jsonify(ok=False, errors=error.errors), 400
    except DatabaseError:
        return jsonify(ok=False, message="Could not save notes. Check saved notes before retrying."), 503
    return jsonify(ok=True, interview_id=saved["interview_id"], notes=saved["notes"])


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("tracker"))

    if request.method == "POST":
        errors = validate_registration(request.form)
        if errors:
            form = {
                "name": request.form.get("name", ""),
                "email": request.form.get("email", ""),
            }
            return render_template("register.html", errors=errors, form=form), 400

        try:
            create_user(request.form)
        except EmailAlreadyExists:
            errors = {"email": "An account with this email already exists."}
            form = {
                "name": request.form.get("name", ""),
                "email": request.form.get("email", ""),
            }
            return render_template("register.html", errors=errors, form=form), 400
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", errors={}, form={})


# dummy endpoints
@app.route("/browse")
def browse():
    return render_template("browse.html")


@app.route("/interviews")
@login_required
def interviews():
    return render_template("interviews/index.html", **load_interviews_page(current_user_id()))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("tracker"))

    if request.method == "POST":
        form = {"email": request.form.get("email", "")}

        errors = validate_login(request.form)
        if errors:
            return render_template("login.html", errors=errors, form=form), 400

        user = authenticate(request.form.get("email"), request.form.get("password"))
        if user is None:
            return render_template(
                "login.html",
                errors={},
                form=form,
                login_error="Email or password is incorrect.",
            ), 401

        login_user(user)
        return redirect(safe_next_url(request.args.get("next")) or url_for("tracker"))

    return render_template("login.html", errors={}, form={})

# also had to alter one test file to match this (test_logout.py)
@app.route("/logout", methods=["POST"])
@csrf_protect
def logout():
    logout_user()
    session.pop("user_id", None)  # Legacy pre-Flask-Login authentication key.
    session.pop("_remember_seconds", None)
    # Keep this marker until Flask-Login's response hook deletes the cookie.
    # Do not session.clear() after setting it, which would lose that operation.
    session["_remember"] = "clear"
    clear_csrf_token()
    flash("You have been logged out.", "success") # logout message added (car-37)
    return redirect(url_for("index")) # changed url

# essentially what this does is;
# 1. checks if a user is logged in (doest access db incase its down)
# 2. tells the server "don't store cache for this page!"
# its so that after you log out, hitting the back button doesn't show you the logged in page again
# additonal info: gets called everytime a view is requested | the "!= static" is so it doesnt mess with css, js, etc. | displays message from line 44
@app.after_request
def no_store_for_logged_in_pages(response):
    if request.endpoint != "static" and session.get("_user_id"):
        response.headers["Cache-Control"] = "no-store"
    return response
