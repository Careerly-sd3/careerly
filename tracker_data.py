"""Sample data for the tracker page, shaped like docs/database/database-design.md.
"""

from collections import Counter
from datetime import date, datetime

# JobApplication.status values, each with a Bootstrap theme colour for its badge.
STATUS_THEMES = {
    "Saved": "secondary",
    "Applied": "info",
    "Interview": "warning",
    "Offer": "success",
    "Rejected": "danger",
    "Withdrawn": "secondary",
}

# Statuses shown as count tiles at the top of the page.
SUMMARY_STATUSES = ("Applied", "Interview", "Offer", "Rejected")

# How many interview cards show before the "Show more" button.
VISIBLE_INTERVIEWS = 2

# Company table: company_id -> name.
COMPANY_NAMES = {
    1: "Qualcomm",
    2: "Motorola Solutions",
    3: "Apple",
    4: "Trend Micro",
    5: "Quest Software",
    6: "F5",
    7: "Amazon Web Services",
    8: "Dell Technologies",
    9: "Boston Scientific",
    10: "Logitech",
    11: "Workhuman",
}


def _application(application_id, company_id, job_title, status, applied_at=None, closing_date=None, user_id=1):
    """One JobApplication row (only the columns the page uses). Sample user 1 is the demo account."""
    return {
        "application_id": application_id,
        "user_id": user_id,
        "company_id": company_id,
        "job_title": job_title,
        "status": status,
        "applied_at": applied_at,
        "closing_date": closing_date,
    }


JOB_APPLICATIONS = (
    _application(1, 1, "Software Engineering Intern", "Interview", date(2026, 10, 14)),
    _application(2, 2, "Software Engineering Intern", "Interview", date(2026, 10, 16)),
    _application(3, 3, "Software Automation Engineering Intern", "Applied", date(2026, 10, 20)),
    _application(4, 4, "Applied AI Engineer Intern", "Offer", date(2026, 10, 8)),
    _application(5, 5, "Software Engineering Intern", "Rejected", date(2026, 10, 5)),
    _application(6, 6, "Software Engineering Intern", "Applied", date(2026, 10, 22)),
    _application(7, 7, "Software Development Intern", "Applied", date(2026, 10, 25)),
    _application(8, 8, "Software Engineering Intern", "Interview", date(2026, 10, 18)),
    _application(9, 9, "Software Engineering Intern", "Saved", closing_date=date(2026, 11, 20)),
    _application(10, 10, "Software UX Design Intern", "Rejected", date(2026, 10, 3)),
    _application(11, 11, "Software Engineer Intern", "Withdrawn", date(2026, 10, 1)),
)

INTERVIEWS = (
    {"interview_id": 1, "application_id": 1,
     "scheduled_at": datetime(2026, 11, 12, 10, 0), "venue_or_link": "Online (Teams)"},
    {"interview_id": 2, "application_id": 2,
     "scheduled_at": datetime(2026, 11, 16, 14, 30), "venue_or_link": "Cork office"},
    {"interview_id": 3, "application_id": 8,
     "scheduled_at": datetime(2026, 11, 19, 11, 0), "venue_or_link": "Online (Zoom)"},
)

# Generic practice questions, the same for everyone. Personalised ones are planned
# to come from AIAnalysis (interview_prep) later.
PRACTICE_QUESTIONS = (
    "Tell me about a project you built and a technical decision you would change.",
    "How do you debug a problem when you don't know where to start?",
    "Explain the difference between a process and a thread.",
    "What is the difference between a stack and a queue, and when would you use each?",
    "What happens when you type a URL into a browser and press Enter?",
    "Walk me through how you would design a URL shortener.",
    "Tell me about a bug that took you a long time to fix. What did you learn?",
    "Describe a time you disagreed with a teammate about a technical approach.",
    "How do you approach reviewing someone else's code?",
    "Why do you want this internship, and what do you hope to learn from it?",
)

_APPLICATIONS_BY_ID = {application["application_id"]: application for application in JOB_APPLICATIONS}


def _next_step(application):
    """Return (label, date or None) for the Next Step column, worked out from the record."""
    status = application["status"]
    if status == "Saved":
        closing_date = application["closing_date"]
        return ("Apply by", closing_date) if closing_date else ("Not applied yet", None)
    if status == "Applied":
        return ("Awaiting reply", None)
    if status == "Interview":
        times = [
            interview["scheduled_at"]
            for interview in INTERVIEWS
            if interview["application_id"] == application["application_id"]
        ]
        return ("Interview", min(times).date() if times else None)
    if status == "Offer":
        return ("Decision pending", None)
    return ("Closed", None)


def _applications_of(user_id):
    """Only this user's applications: the one place that keeps users' data apart."""
    return [application for application in JOB_APPLICATIONS if application["user_id"] == user_id]


def build_application_rows(user_id):
    """Rows for the applications table."""
    rows = []
    for application in _applications_of(user_id):
        next_step, next_date = _next_step(application)
        rows.append({
            "company": COMPANY_NAMES[application["company_id"]],
            "job_title": application["job_title"],
            "applied_at": application["applied_at"],
            "status": application["status"],
            "next_step": next_step,
            "next_date": next_date,
        })
    return rows


def build_interview_cards(user_id):
    """Interview cards for this user's applications, soonest first."""
    own_application_ids = {application["application_id"] for application in _applications_of(user_id)}
    own_interviews = [interview for interview in INTERVIEWS if interview["application_id"] in own_application_ids]
    cards = []
    for interview in sorted(own_interviews, key=lambda item: item["scheduled_at"]):
        application = _APPLICATIONS_BY_ID[interview["application_id"]]
        cards.append({
            "company": COMPANY_NAMES[application["company_id"]],
            "job_title": application["job_title"],
            "scheduled_at": interview["scheduled_at"],
            "location": interview["venue_or_link"],
        })
    return cards


def build_practice_meta(user_id):
    """Subtitle for the practice card, from this user's soonest interview ('' if they have none)."""
    cards = build_interview_cards(user_id)
    if not cards:
        return ""
    soonest = cards[0]
    return f"{soonest['job_title']} · {soonest['company']} · {soonest['scheduled_at'].strftime('%A')}"


def build_status_counts(user_id):
    """Number of this user's applications per status, for the count tiles."""
    counts = Counter(application["status"] for application in _applications_of(user_id))
    return {status: counts[status] for status in SUMMARY_STATUSES}
