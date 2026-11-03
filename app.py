import os

from db import init_app

from services.access import current_user_id, login_required
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for

from services.auth_service import create_user, validate_registration
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
init_app(app)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")


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


# add login redirect, this is temp
@app.route("/login")
def login():
    return render_template("login.html")


# dummy endpoints
@app.route("/browse")
def browse():
    return render_template("browse.html")


@app.route("/interviews")
def interviews():
    return render_template("interviews.html")
