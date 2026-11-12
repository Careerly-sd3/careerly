"""Tracker page data, read from the JobApplication, Company and Interview tables."""

from collections import Counter
from datetime import datetime, timezone
import logging
from urllib.parse import urlsplit

from sqlalchemy import select

from db import DatabaseError
from models import JobApplication, Company
from orm_db import operation_session
from services.interview_service import list_interviews, _list_interviews
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

# How dates read in the details panels; matches the template's date_text macro.
DISPLAY_DATE = "%b %d, %Y"

# Text columns listed in the Details panel, in this order, when they hold something.
FACT_FIELDS = (
    ("location", "Location"),
    ("work_type", "Work type"),
    ("opportunity_type", "Opportunity"),
    ("salary_text", "Salary"),
)

# Every query filters by the user's id, which is passed as a parameter, never joined into the text.
# job_ad_text is left out on purpose: it can be long and the panels do not show it.
def _applications_statement(user_id):
    return (select(
                JobApplication.application_id, Company.name.label("company"),
                JobApplication.job_title, JobApplication.status, JobApplication.applied_at,
                JobApplication.closing_date, JobApplication.created_at, JobApplication.location,
                JobApplication.work_type, JobApplication.opportunity_type,
                JobApplication.salary_text, JobApplication.job_url, JobApplication.notes,
                JobApplication.updated_at, Company.website_url)
            .select_from(JobApplication)
            .join(Company, Company.company_id == JobApplication.company_id)
            .where(JobApplication.user_id == user_id).order_by(JobApplication.application_id))


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


def _fetch_all(session, user_id):
    """Materialise application/company rows within the owning Tracker read unit."""
    return [dict(row) for row in session.execute(_applications_statement(user_id)).mappings().all()]


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


def _facts(application):
    """The Details panel's label/value pairs, only for fields that hold something."""
    values = ((label, (application.get(field) or "").strip()) for field, label in FACT_FIELDS)
    facts = [{"label": label, "value": value} for label, value in values if value]
    if application.get("closing_date"):
        facts.append({"label": "Closing date", "value": application["closing_date"].strftime(DISPLAY_DATE)})
    return facts


def _link(label, value):
    """A saved address: a link only for absolute HTTP(S); anything else stays plain text."""
    url = _location_url(value)
    return {"label": label, "text": _location_label(value) if url else value, "url": url}


def _links(application):
    return [_link(label, application[field])
            for field, label in (("job_url", "Job ad"), ("website_url", "Company site"))
            if application.get(field)]


def _updated_text(application):
    updated_at = application.get("updated_at")
    return utc_to_dublin(updated_at).strftime(DISPLAY_DATE) if updated_at else ""


def _application_rows(applications, interviews, cutoff=None):
    cutoff = cutoff or _now_utc()
    rows = []
    for application in applications:
        own_interviews = [interview for interview in interviews
                          if interview["application_id"] == application["application_id"]]
        next_step, next_date = _next_step(application, own_interviews)
        rows.append({
            "application_id": application["application_id"],
            "company": application["company"],
            "job_title": application["job_title"],
            "applied_at": application["applied_at"],
            "status": application["status"],
            "next_step": next_step,
            "next_date": next_date,
            "history": _history(application, own_interviews, cutoff),
            "facts": _facts(application),
            "links": _links(application),
            "notes": application.get("notes") or "",
            "updated_text": _updated_text(application),
            "interviews": _interview_rows(own_interviews, cutoff),
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
    try:
        with operation_session(
            read_only=True,
            error_message="Could not load your tracker data. Please try again later.",
        ) as session:
            applications = _fetch_all(session, user_id)
            cutoff = _now_utc()
            interviews = _list_interviews(session, user_id, cutoff=cutoff)
    except DatabaseError as error:
        safe_error = DatabaseError("Database read failed.")
        logger.exception("Tracker query failed", exc_info=(DatabaseError, safe_error, error.__traceback__))
        raise DatabaseError("Could not load your tracker data. Please try again later.") from None
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


def load_interviews_page(user_id):
    """Dedicated owner listing; reuse the UTC ordering and display transformation."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    return {"interviews": _interview_rows(list_interviews(user_id, cutoff=cutoff))}
