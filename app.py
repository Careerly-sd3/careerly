import os

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user

from services.access import current_user_id
from services.auth_service import (
    authenticate,
    create_user,
    get_user_by_id,
    validate_login,
    validate_registration,
)
from tracker_data import (
    PRACTICE_QUESTIONS,
    STATUS_THEMES,
    VISIBLE_INTERVIEWS,
    build_application_rows,
    build_interview_cards,
    build_practice_meta,
    build_status_counts,
)

load_dotenv()


app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY")

# login
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "error"


@login_manager.user_loader
def load_user(user_id):
    """Flask-Login calls this on every request with the id from the session
    cookie. Returning None means "not logged in"."""
    return get_user_by_id(user_id)


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
    user_id = current_user_id()
    return render_template(
        "tracker.html",
        applications=build_application_rows(user_id),
        interviews=build_interview_cards(user_id),
        practice_meta=build_practice_meta(user_id),
        practice_questions=PRACTICE_QUESTIONS,
        status_counts=build_status_counts(user_id),
        status_themes=STATUS_THEMES,
        visible_interviews=VISIBLE_INTERVIEWS,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        errors = validate_registration(request.form)
        if errors:
            form = {
                "name": request.form.get("name", ""),
                "email": request.form.get("email", ""),
            }
            return render_template("register.html", errors=errors, form=form), 400

        create_user(request.form)
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", errors={}, form={})


# dummy endpoints
@app.route("/browse")
def browse():
    return render_template("browse.html")


@app.route("/interviews")
def interviews():
    return render_template("interviews.html")


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
