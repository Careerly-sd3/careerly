"""AI results in the AIAnalysis table, and the match score.

- Every analysis is a new row; older rows stay as history.
- match_score is 0-100 for cv_match and NULL for every other type.
- result_text is the result as JSON, including a fingerprint of the inputs it was
  made from, so the page can tell when a result is out of date.
"""
from datetime import datetime, timezone
import hashlib
import json
import logging

from sqlalchemy import select

from models import AIAnalysis, JobApplication
from orm_db import operation_session
from services.application_service import ApplicationNotFound

logger = logging.getLogger(__name__)

ANALYSIS_TYPES = ("interview_prep", "missing_skills", "cv_match")
RESULT_MAX_BYTES = 65535  # MySQL TEXT
MAX_ID = 2147483647
REQUIRED_WEIGHT = 2
NICE_WEIGHT = 1


class AnalysisValidationError(ValueError):
    """A result that breaks a database rule (a programming error, not user input)."""


def compute_match_score(requirements):
    """0-100 from {"importance", "has_skill"} dicts, or None when there is nothing to score.

    Required skills count double: 100 * (2 * required met + nice met) / (2 * required + nice).
    """
    total = matched = 0
    for requirement in requirements:
        weight = REQUIRED_WEIGHT if requirement["importance"] == "required" else NICE_WEIGHT
        total += weight
        matched += weight if requirement["has_skill"] else 0
    if total == 0:
        return None
    return max(0, min(100, round(100 * matched / total)))


def input_fingerprint(*parts):
    """A short hash of the inputs; ignores spacing, case and the order of list items."""
    normalised = []
    for part in parts:
        if isinstance(part, (list, tuple)):
            part = "\x1e".join(sorted(" ".join(str(item).split()).casefold() for item in part))
        normalised.append(" ".join(str(part or "").split()))
    return hashlib.sha256("\x1f".join(normalised).encode("utf-8")).hexdigest()[:16]


def _serialise(analysis_type, result, match_score):
    if analysis_type not in ANALYSIS_TYPES:
        raise AnalysisValidationError("Unknown analysis type.")
    if analysis_type == "cv_match":
        if type(match_score) is not int or not 0 <= match_score <= 100:
            raise AnalysisValidationError("cv_match needs an integer score from 0 to 100.")
    elif match_score is not None:
        raise AnalysisValidationError("Only cv_match analyses have a score.")
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > RESULT_MAX_BYTES:
        raise AnalysisValidationError("The analysis result is too large to store.")
    return text


def _check_owned(session, user_id, application_id, *, lock=False):
    """Raise ApplicationNotFound unless the user owns the application."""
    for value in (user_id, application_id):
        if type(value) is not int or not 1 <= value <= MAX_ID:
            raise ApplicationNotFound()
    query = select(JobApplication.application_id).where(
        JobApplication.application_id == application_id, JobApplication.user_id == user_id)
    if session.execute(query.with_for_update() if lock else query).scalar_one_or_none() is None:
        raise ApplicationNotFound()


def save_analyses(user_id, application_id, entries, *, now=None):
    """Insert one row per entry ({"analysis_type", "result", "match_score"}) in one transaction."""
    rows = [(entry["analysis_type"], _serialise(entry["analysis_type"], entry["result"], entry.get("match_score")),
             entry.get("match_score")) for entry in entries]
    stamp = now or datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    with operation_session(error_message="Could not save the AI result. Please try again.") as session:
        _check_owned(session, user_id, application_id, lock=True)
        for analysis_type, text, score in rows:
            session.add(AIAnalysis(application_id=application_id, analysis_type=analysis_type,
                                   result_text=text, match_score=score, created_at=stamp))


def latest_analyses(user_id, application_id):
    """The newest analysis of each type: {type: {"result", "match_score", "created_at"}}."""
    with operation_session(read_only=True, error_message="Could not load AI results.") as session:
        _check_owned(session, user_id, application_id)
        rows = session.execute(
            select(AIAnalysis.analysis_id, AIAnalysis.analysis_type, AIAnalysis.result_text,
                   AIAnalysis.match_score, AIAnalysis.created_at)
            .where(AIAnalysis.application_id == application_id)
            .order_by(AIAnalysis.created_at, AIAnalysis.analysis_id)
        ).mappings().all()
    latest = {}
    for row in rows:  # oldest first, so newer rows replace older ones
        try:
            result = json.loads(row["result_text"])
        except ValueError:
            logger.warning("Skipping unreadable AIAnalysis %s", row["analysis_id"])
            continue
        latest[row["analysis_type"]] = {"result": result, "match_score": row["match_score"],
                                        "created_at": row["created_at"]}
    return latest
