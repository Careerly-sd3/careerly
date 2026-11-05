import os

from db import DatabaseError, init_app

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user

from services.auth_service import (
    authenticate,
    create_user,
    get_user_by_id,
    validate_login,
    validate_registration,
    current_user_id
)
from tracker_data import (
    PRACTICE_QUESTIONS,
    STATUS_THEMES,
    VISIBLE_INTERVIEWS,
    load_tracker_page,
)

load_dotenv()


app = Flask(__name__)
init_app(app)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "danger"


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
    status_code = 200
    status = request.args.get("status", "")
    company = request.args.get("company", "")
    role = request.args.get("role", "")
    try:
        page = load_tracker_page(current_user_id(), status=status, company=company, role=role)
    except DatabaseError as error:
        flash(str(error), "danger")
        page = {"applications": [], "interviews": [], "practice_meta": "",
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
        visible_interviews=VISIBLE_INTERVIEWS,
    ), status_code


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
