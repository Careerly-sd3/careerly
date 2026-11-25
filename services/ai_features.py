"""The AI features end to end: read the inputs, call Gemini, save the result.

Gemini is never called inside a database transaction: inputs are read first,
then Gemini is called, then the result is saved in a new transaction.
"""
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
import threading
import time
from urllib.parse import urlsplit

from db import DatabaseError
from services import (ai_analysis_service, ai_jobs, ai_service, application_service, interview_service,
                      profile_service)
from services.interview_time import utc_to_dublin
from services.common import now_utc as _now_utc

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
JOB_AD_MAX_BYTES = 65535  # MySQL TEXT
URL_MAX_CHARS = 500
RATE_LIMIT_CALLS = 20
RATE_LIMIT_WINDOW_SECONDS = 600
DISPLAY_TIME = "%b %d, %Y at %H:%M"

PDF = "application/pdf"
# The file type is read from the file's first bytes; the name and browser-sent type are not trusted.
SIGNATURES = ((b"%PDF-", PDF), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))
CV_TYPES = (PDF,)
JOB_AD_FILE_TYPES = (PDF, "image/png", "image/jpeg", "image/webp")
# Maximum lengths of the application form's text fields.
JOB_FIELD_LIMITS = {"company": 150, "job_title": 150, "location": 150, "salary_text": 100}


class UploadRejected(ValueError):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class MissingInputs(Exception):
    """An analysis needs something the user hasn't added yet."""

    MESSAGES = {
        "job_ad": "Add the job advert text to this application first (Edit application, then Job advert).",
        "cv": "Upload your CV or add skills on your Profile first.",
        "requirements": "The job advert doesn't list clear requirements to score against. Add the full advert text.",
    }

    def __init__(self, missing):
        self.message = " ".join(self.MESSAGES[item] for item in missing)
        super().__init__(self.message)


class RateLimited(Exception):
    message = "You've used the AI a lot in the last few minutes. Wait a little and try again."


# Failures a background job shows to the user; anything else is logged as unexpected.
BACKGROUND_ERRORS = (ai_service.AIServiceError, MissingInputs, RateLimited, DatabaseError)


# ---------- rate limit ----------
# Kept in memory: it resets when the app restarts and each worker process counts separately.

_calls = defaultdict(deque)
_calls_lock = threading.Lock()


def check_rate_limit(user_id, *, clock=time.monotonic):
    """Count one AI action for the user, or raise RateLimited."""
    now = clock()
    with _calls_lock:
        for idle_user in [key for key, times in _calls.items()
                          if times and now - times[-1] > RATE_LIMIT_WINDOW_SECONDS]:
            del _calls[idle_user]
        recent = _calls[user_id]
        while recent and now - recent[0] > RATE_LIMIT_WINDOW_SECONDS:
            recent.popleft()
        if len(recent) >= RATE_LIMIT_CALLS:
            raise RateLimited()
        recent.append(now)


# ---------- uploads ----------

def detect_file_type(data):
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for signature, mime_type in SIGNATURES:
        if data.startswith(signature):
            return mime_type
    return None


def has_file(file_storage):
    return file_storage is not None and bool(file_storage.filename)


def read_upload(file_storage, allowed_types, wrong_type_message):
    """Return (bytes, mime_type), or raise UploadRejected."""
    if not has_file(file_storage):
        raise UploadRejected("Choose a file to upload.")
    data = file_storage.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise UploadRejected("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected("That file is too large. Upload a file up to 5 MB.")
    mime_type = detect_file_type(data)
    if mime_type not in allowed_types:
        raise UploadRejected(wrong_type_message)
    return data, mime_type


# ---------- CAR-83: CV, read in the background ----------

def cv_job_key(user_id):
    return ("cv", user_id)


def read_cv(user_id, data, mime_type):
    """The background job: Gemini reads the CV, then its text and skills are saved."""
    extraction = ai_service.extract_cv(data, mime_type)
    try:
        summary = profile_service.replace_cv(user_id, extraction.cv_text, extraction.skills)
    except profile_service.ProfileValidationError:  # the CV had no readable text
        raise ai_service.AIUnreadable(ai_service.FILE_NOT_CV) from None
    message = f"CV saved. {summary['skills_added']} skills found."
    if summary["trimmed"]:
        message += " Your CV was very long, so only the first part was saved."
    return message


def start_cv_reading(user_id, file_storage):
    """Check the upload now (fast) and read it in the background; the user doesn't wait."""
    key = cv_job_key(user_id)
    if ai_jobs.is_running(key):
        raise UploadRejected("Your last CV is still being read. Wait for it to finish, then upload again.")
    data, mime_type = read_upload(file_storage, CV_TYPES, "Upload your CV as a PDF file.")
    check_rate_limit(user_id)
    ai_jobs.start(key, lambda: read_cv(user_id, data, mime_type),
                  expected_errors=BACKGROUND_ERRORS, force=True)


# ---------- CAR-81: fill the application form from a job ad ----------

def _check_url(url):
    message = "Use a full web address starting with http:// or https://."
    if len(url) > URL_MAX_CHARS or any(ch.isspace() for ch in url):
        raise UploadRejected(message)
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise UploadRejected(message) from None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise UploadRejected(message)


def _recent_date(value, today):
    """A YYYY-MM-DD date from the last year onwards, else None."""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= today - timedelta(days=365) else None


def suggested_fields(fields, today):
    """Keep only the AI suggestions the application form accepts."""
    suggestions = {}
    for name, limit in JOB_FIELD_LIMITS.items():
        value = getattr(fields, name)
        if value and len(value) <= limit and any(ch.isalpha() for ch in value):
            suggestions[name] = value
    work_type = (fields.work_type or "").strip().lower()
    if work_type in application_service.WORK_TYPES:
        suggestions["work_type"] = work_type
    opportunity_type = (fields.opportunity_type or "").strip().lower()
    if opportunity_type in application_service.OPPORTUNITY_TYPES:
        suggestions["opportunity_type"] = opportunity_type
    closing_date = _recent_date(fields.closing_date, today)
    if closing_date:
        suggestions["closing_date"] = closing_date.isoformat()
    return suggestions


def prefill_application(user_id, *, url="", text="", file_storage=None, today=None):
    """Return (form values, names of the AI-filled fields) for the Add application form.

    The advert comes from the uploaded file if there is one, else the pasted text, else the link.
    """
    url = (url or "").strip()
    text = (text or "").replace("\r\n", "\n").strip()
    if url:
        _check_url(url)
    file_data = None
    if has_file(file_storage):
        file_data = read_upload(file_storage, JOB_AD_FILE_TYPES,
                                "Upload the job page as a PDF, or a PNG, JPEG or WebP screenshot.")
    elif not (text or url):
        raise UploadRejected("Add a link, paste the job description, or upload the job page.")

    check_rate_limit(user_id)
    if file_data:
        ad_text = ai_service.read_job_ad_file(*file_data)
    elif text:
        ad_text = text
    else:
        ad_text = ai_service.read_job_ad_url(url)
    ad_text, _ = profile_service.fit_text(ad_text, JOB_AD_MAX_BYTES)
    today = today or _now_utc().date()

    suggestions = suggested_fields(ai_service.extract_job_fields(ad_text, today), today)
    form = dict(suggestions, status=application_service.DEFAULT_STATUS, job_ad_text=ad_text)
    if url and not file_data:
        form["job_url"] = url
    return form, set(suggestions)


# ---------- inputs shared by the analyses ----------

def _skill_names(profile):
    return [skill["skill_name"] for skill in profile["skills"]]


def _job_ad(application):
    return (application.get("job_ad_text") or "").strip()


def fit_fingerprint(job_ad, profile):
    return ai_analysis_service.input_fingerprint(job_ad, profile["cv_text"], _skill_names(profile))


def prep_fingerprint(job_ad, profile, interview_type):
    return ai_analysis_service.input_fingerprint(job_ad, profile["cv_text"], _skill_names(profile), interview_type)


def next_interview(user_id, application_id, now):
    """The application's next upcoming interview, else its latest past one, else None."""
    own = [row for row in interview_service.list_interviews(user_id, cutoff=now)
           if row["application_id"] == application_id]
    upcoming = sorted((row for row in own if row["scheduled_at"] >= now), key=lambda row: row["scheduled_at"])
    past = sorted((row for row in own if row["scheduled_at"] < now), key=lambda row: row["scheduled_at"],
                  reverse=True)
    return (upcoming or past or [None])[0]


# ---------- CAR-84 and CAR-85: missing skills and match score ----------

def analyse_fit(user_id, application_id, *, now=None):
    """One Gemini comparison, saved as a missing_skills row and a cv_match row."""
    application = application_service.get_application(user_id, application_id)
    profile = profile_service.get_profile(user_id)
    job_ad = _job_ad(application)
    missing = [item for item, present in (("job_ad", job_ad), ("cv", profile["cv_text"] or profile["skills"]))
               if not present]
    if missing:
        raise MissingInputs(missing)

    check_rate_limit(user_id)
    comparison = ai_service.compare_skills(job_ad, profile["cv_text"] or "", _skill_names(profile))
    requirements = [requirement.model_dump() for requirement in comparison.requirements]
    score = ai_analysis_service.compute_match_score(requirements)
    fingerprint = fit_fingerprint(job_ad, profile)
    if score is None:
        # Remember this for these inputs, so the same advert isn't sent to Gemini again and again.
        ai_analysis_service.save_analyses(user_id, application_id, [
            {"analysis_type": "missing_skills",
             "result": {"missing": [], "advice": [], "fingerprint": fingerprint, "no_requirements": True}},
        ], now=now)
        raise MissingInputs(["requirements"])

    gaps = [requirement for requirement in requirements if not requirement["has_skill"]]
    ai_analysis_service.save_analyses(user_id, application_id, [
        {"analysis_type": "missing_skills",
         "result": {"missing": gaps, "advice": comparison.advice, "fingerprint": fingerprint}},
        {"analysis_type": "cv_match", "match_score": score,
         "result": {"requirements": requirements, "advice": comparison.advice, "fingerprint": fingerprint}},
    ], now=now)


# ---------- CAR-82: interview prep ----------

def prepare_interview(user_id, application_id, *, now=None):
    now = now or _now_utc()
    application = application_service.get_application(user_id, application_id)
    profile = profile_service.get_profile(user_id)
    interview = next_interview(user_id, application_id, now)
    latest_gaps = ai_analysis_service.latest_analyses(user_id, application_id).get("missing_skills")
    gaps = [item["skill"] for item in latest_gaps["result"]["missing"]] if latest_gaps else []
    job_ad = _job_ad(application)
    interview_type = interview["interview_type"] if interview else None

    check_rate_limit(user_id)
    prep = ai_service.interview_prep(
        job_title=application["job_title"], company=application["company"], job_ad_text=job_ad,
        interview_type=interview_type, interview_notes=interview["notes"] if interview else None,
        cv_text=profile["cv_text"], skills=_skill_names(profile), missing_skills=gaps)
    result = prep.model_dump() | {"interview_type": interview_type,
                                  "fingerprint": prep_fingerprint(job_ad, profile, interview_type)}
    ai_analysis_service.save_analyses(user_id, application_id, [
        {"analysis_type": "interview_prep", "result": result}], now=now)


# ---------- the AI insights page ----------

def _display_time(utc_value):
    return utc_to_dublin(utc_value).strftime(DISPLAY_TIME)


def _with_display_time(analysis):
    return {**analysis, "created_text": _display_time(analysis["created_at"])} if analysis else None


def insights(user_id, application_id, *, now=None):
    """Everything the AI insights page shows for one application."""
    now = now or _now_utc()
    application = application_service.get_application(user_id, application_id)
    profile = profile_service.get_profile(user_id)
    latest = ai_analysis_service.latest_analyses(user_id, application_id)
    interview = next_interview(user_id, application_id, now)
    job_ad = _job_ad(application)
    fit = _with_display_time(latest.get("cv_match"))
    prep = _with_display_time(latest.get("interview_prep"))
    interview_type = interview["interview_type"] if interview else None
    can_analyse = bool(job_ad) and bool(profile["cv_text"] or profile["skills"])
    current_fit = fit_fingerprint(job_ad, profile)
    gaps = latest.get("missing_skills")
    # The advert had no requirements to score, and nothing has changed since.
    no_requirements = bool(gaps) and gaps["result"].get("no_requirements") is True \
        and gaps["result"].get("fingerprint") == current_fit
    fit_outdated = bool(fit) and fit["result"].get("fingerprint") != current_fit
    prep_outdated = bool(prep) and prep["result"].get("fingerprint") != prep_fingerprint(
        job_ad, profile, interview_type)
    return {
        "application": application,
        "has_job_ad": bool(job_ad),
        "has_cv": bool(profile["cv_text"]),
        "skill_count": len(profile["skills"]),
        "interview": interview,
        "interview_text": _display_time(interview["scheduled_at"]) if interview else None,
        "interview_is_upcoming": bool(interview) and interview["scheduled_at"] >= now,
        "can_analyse": can_analyse,
        "no_requirements": no_requirements,
        "fit": None if no_requirements else fit,
        "prep": prep,
        "fit_needed": can_analyse and not no_requirements and (not fit or fit_outdated),
        "prep_needed": not prep or prep_outdated,
        "ai_configured": ai_service.is_configured(),
    }


# ---------- keeping results up to date in the background ----------

def refresh_job_key(user_id, application_id):
    return ("refresh", user_id, application_id)


def refresh_application(user_id, application_id, *, include_fit=True):
    """The background job: redo the match score and interview questions if missing or out of date.

    The score runs first, so the questions can ask about the latest missing skills.
    include_fit=False makes only the questions (the Tracker shows nothing else).
    """
    fit_problem = None
    if include_fit and insights(user_id, application_id)["fit_needed"]:
        try:
            analyse_fit(user_id, application_id)
        except MissingInputs as error:  # e.g. no clear requirements; the questions can still be made
            fit_problem = error
    if insights(user_id, application_id)["prep_needed"]:
        prepare_interview(user_id, application_id)
    if fit_problem:
        raise fit_problem


def keep_up_to_date(user_id, application_id, *, needed, include_fit=True):
    """Start the background refresh if `needed`; return the job status for the page."""
    key = refresh_job_key(user_id, application_id)
    if needed and ai_service.is_configured():
        ai_jobs.start(key, lambda: refresh_application(user_id, application_id, include_fit=include_fit),
                      expected_errors=BACKGROUND_ERRORS)
    return ai_jobs.status(key)
