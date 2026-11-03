from flask import Flask, render_template

from db import init_app

from tracker_data import (
    PRACTICE_META,
    PRACTICE_QUESTIONS,
    STATUS_THEMES,
    VISIBLE_INTERVIEWS,
    build_application_rows,
    build_interview_cards,
    build_status_counts,
)

app = Flask(__name__)
init_app(app)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/tracker")
def tracker():
    return render_template(
        "tracker.html",
        applications=build_application_rows(),
        interviews=build_interview_cards(),
        practice_meta=PRACTICE_META,
        practice_questions=PRACTICE_QUESTIONS,
        status_counts=build_status_counts(),
        status_themes=STATUS_THEMES,
        visible_interviews=VISIBLE_INTERVIEWS,
    )
