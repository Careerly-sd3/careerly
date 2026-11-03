from flask import Flask, render_template

from services.access import current_user_id, login_required
from tracker_data import (
    PRACTICE_QUESTIONS,
    STATUS_THEMES,
    VISIBLE_INTERVIEWS,
    build_application_rows,
    build_interview_cards,
    build_practice_meta,
    build_status_counts,
)

app = Flask(__name__)

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
