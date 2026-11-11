"""Tracker page data, read from the JobApplication, Company and Interview tables."""

from collections import Counter
from datetime import datetime, timezone
import logging
from urllib.parse import urlsplit

import mysql.connector

from db import DatabaseError, get_db
from services.interview_service import list_interviews
from services.interview_time import utc_to_dublin

logger = logging.getLogger(__name__)

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

# How many upcoming interview cards the tracker shows; "Show more" opens the Interviews page.
VISIBLE_INTERVIEWS = 2

# Shown where an interview has no venue or link.
NO_LOCATION = "—"

# Every query filters by the user's id, which is passed as a parameter, never joined into the text.
APPLICATIONS_QUERY = """
    SELECT a.application_id, c.name AS company, a.job_title, a.status, a.applied_at, a.closing_date,
           a.created_at
    FROM `JobApplication` a
    JOIN `Company` c ON c.company_id = a.company_id
    WHERE a.user_id = %s
    ORDER BY a.application_id
"""

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


def _fetch_all(query, user_id):
    """Run one read-only query for this user and return its rows as dictionaries."""
    try:
        cursor = get_db().cursor(dictionary=True)
        try:
            cursor.execute(query, (user_id,))
            return cursor.fetchall()
        finally:
            cursor.close()
    except (mysql.connector.Error, DatabaseError) as error:
        # Connector messages can include usernames, SQL or server-provided text.
        # Retain the original call stack, but never log that exception's value,
        # chained exceptions, query parameters or frame locals.
        safe_error = DatabaseError("Database read failed.")
        logger.exception("Tracker query failed", exc_info=(DatabaseError, safe_error, error.__traceback__))
        raise DatabaseError("Could not load your tracker data. Please try again later.") from None


def _next_step(application, interviews):
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
            for interview in interviews
            if interview["application_id"] == application["application_id"]
        ]
        return ("Interview", utc_to_dublin(min(times)).date() if times else None)
    if status == "Offer":
        return ("Decision pending", None)
    return ("Closed", None)


def _history_event(day, label, detail=None, *, is_future=False, is_upcoming=False):
    return {"date": day, "label": label, "detail": detail, "is_future": is_future, "is_upcoming": is_upcoming}


def _interview_event(interview, cutoff):
    """One interview: '<Type> interview', its venue label, and the outcome once it has happened."""
    is_upcoming = interview["scheduled_at"] >= cutoff
    venue = interview.get("venue_or_link")
    parts = [_location_label(venue)] if venue else []
    if not is_upcoming and interview.get("outcome") not in (None, "Pending"):
        parts.append(interview["outcome"])
    return _history_event(utc_to_dublin(interview["scheduled_at"]).date(),
                          f"{interview['interview_type'].capitalize()} interview",
                          " · ".join(parts) or None, is_future=is_upcoming, is_upcoming=is_upcoming)


def _history(application, interviews, cutoff):
    """What happened with one application and when, oldest first, from dates already stored.

    created_at and scheduled_at are naive UTC (shown as Europe/Dublin dates); applied_at and
    closing_date are plain dates. Same-day events keep this order: saved, applied, interviews, closing.
    """
    today = utc_to_dublin(cutoff).date()
    events = []
    if application.get("created_at"):
        events.append(_history_event(utc_to_dublin(application["created_at"]).date(), "Saved to tracker"))
    if application.get("applied_at"):
        events.append(_history_event(application["applied_at"], "Applied",
                                     is_future=application["applied_at"] > today))
    events += [_interview_event(interview, cutoff) for interview in interviews
               if interview["application_id"] == application["application_id"]]
    if application.get("closing_date"):
        events.append(_history_event(application["closing_date"], "Closing date",
                                     is_future=application["closing_date"] > today))
    return sorted(events, key=lambda event: event["date"])  # stable: keeps same-day order


def _now_utc():
    """The page's single "now": naive UTC to the second, matching the stored DATETIMEs."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _application_rows(applications, interviews, cutoff=None):
    cutoff = cutoff or _now_utc()
    rows = []
    for application in applications:
        next_step, next_date = _next_step(application, interviews)
        rows.append({
            "application_id": application["application_id"],
            "company": application["company"],
            "job_title": application["job_title"],
            "applied_at": application["applied_at"],
            "status": application["status"],
            "next_step": next_step,
            "next_date": next_date,
            "history": _history(application, interviews, cutoff),
        })
    return rows


def _location_url(value):
    """Allow only absolute HTTP(S) URLs; malformed/other venues stay text."""
    if not value or any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in ("http", "https") and parsed.hostname and parsed.port != 0:
            return value
    except ValueError:
        pass
    return None


# Meeting services named on the interview cards, matched on the link's host
# (the host itself or any subdomain of it, never a lookalike such as zoom.us.evil.example).
MEETING_SERVICES = (
    ("meet.google.com", "Google Meet"),
    ("zoom.us", "Zoom"),
    ("teams.microsoft.com", "Microsoft Teams"),
    ("teams.live.com", "Microsoft Teams"),
    ("skype.com", "Skype"),
    ("webex.com", "Webex"),
    ("whereby.com", "Whereby"),
    ("meet.jit.si", "Jitsi Meet"),
)


def _location_label(value):
    """Readable text for a venue: a service name or site name for a link, else the venue as typed."""
    if not _location_url(value):
        return value
    host = urlsplit(value).hostname.lower()
    for domain, label in MEETING_SERVICES:
        if host == domain or host.endswith("." + domain):
            return label
    return host.removeprefix("www.")


def _interview_rows(interviews, cutoff=None):
    """Keep the service's UTC ordering/IDs; convert only display datetimes."""
    return [
        {
            **interview,
            "scheduled_at_utc": interview["scheduled_at"],
            "scheduled_at": utc_to_dublin(interview["scheduled_at"]),
            "location": interview["venue_or_link"] or NO_LOCATION,
            "location_url": _location_url(interview["venue_or_link"]),
            "location_label": _location_label(interview["venue_or_link"] or NO_LOCATION),
            "is_past": cutoff is not None and interview["scheduled_at"] < cutoff,
        }
        for interview in interviews
    ]


def _practice_meta(cards, cutoff):
    """Use only upcoming UTC interviews, keeping the listing's single cutoff."""
    upcoming = [card for card in cards if card["scheduled_at_utc"] >= cutoff]
    if not upcoming:
        return ""
    soonest = upcoming[0]
    return f"{soonest['job_title']} · {soonest['company']} · {soonest['scheduled_at'].strftime('%A')}"


def _status_counts(applications):
    counts = Counter(application["status"] for application in applications)
    return {status: counts[status] for status in SUMMARY_STATUSES}


def _filter_rows(rows, status, company, role):
    """Keep the rows that match every filter that is switched on (a blank filter is off)."""
    return [
        row for row in rows
        if (not status or row["status"] == status)
        and (not company or row["company"] == company)
        and (not role or row["job_title"] == role)
    ]


def _options(rows, key):
    """Each different value of `key`, once, in alphabetical order, for a dropdown."""
    return sorted({row[key] for row in rows}, key=str.casefold)


def load_tracker_page(user_id, status="", company="", role=""):
    """Everything the tracker page shows for one user, from two queries.

    The filters only narrow the applications table. The status tiles, the total and
    the company and role dropdown lists always cover all of the user's applications.
    Raises DatabaseError (safe to show) when the database cannot be read.
    """
    applications = _fetch_all(APPLICATIONS_QUERY, user_id)
    cutoff = _now_utc()
    interviews = list_interviews(user_id, cutoff=cutoff)
    cards = _interview_rows(interviews, cutoff)
    upcoming = [card for card in cards if not card["is_past"]][:VISIBLE_INTERVIEWS]
    rows = _application_rows(applications, interviews, cutoff)
    return {
        "applications": _filter_rows(rows, status, company, role),
        "total_applications": len(applications),
        "company_options": _options(rows, "company"),
        "role_options": _options(rows, "job_title"),
        "interviews": upcoming,
        "has_more_interviews": len(cards) > len(upcoming),
        "practice_meta": _practice_meta(cards, cutoff),
        "status_counts": _status_counts(applications),
    }
