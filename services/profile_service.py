"""The user's CV text (User.cv_text) and skills (UserSkill).

- A skill name is unique per user, ignoring case and accents, as in the database.
- A new CV replaces only the skills found in the old CV; skills the user added stay.
"""
from datetime import datetime
import unicodedata

from sqlalchemy import delete, select, update

from models import User, UserSkill
from orm_db import operation_session
from services.common import is_valid_id, now_utc as _now_utc

SKILL_MAX_CHARS = 100
CV_TEXT_MAX_BYTES = 65535  # MySQL TEXT


class ProfileValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Invalid profile input.")


class SkillNotFound(LookupError):
    pass


class ProfileNotFound(LookupError):
    pass


def _check_id(value, error):
    if not is_valid_id(value):
        raise error()


def skill_key(name):
    """Comparison key matching utf8mb4_0900_ai_ci closely enough: no case, no accents."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def clean_skill_name(value):
    if not isinstance(value, str):
        raise ProfileValidationError({"skill_name": "Enter a skill."})
    name = " ".join(value.split())
    if not name:
        raise ProfileValidationError({"skill_name": "Enter a skill."})
    if len(name) > SKILL_MAX_CHARS:
        raise ProfileValidationError({"skill_name": f"Use at most {SKILL_MAX_CHARS} characters."})
    if not any(ch.isalnum() for ch in name):
        raise ProfileValidationError({"skill_name": "Include at least one letter or number."})
    return name


def fit_text(text, max_bytes=CV_TEXT_MAX_BYTES):
    """Trim text to fit a TEXT column without splitting a character; returns (text, was_trimmed)."""
    text = (text or "").strip()
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip(), True


def _skills(session, user_id):
    rows = session.execute(
        select(UserSkill.user_skill_id, UserSkill.skill_name, UserSkill.source, UserSkill.created_at)
        .where(UserSkill.user_id == user_id)
    ).mappings().all()
    return sorted((dict(row) for row in rows), key=lambda row: row["skill_name"].casefold())


def _lock_user(session, user_id):
    found = session.execute(select(User.user_id).where(User.user_id == user_id).with_for_update())
    if found.scalar_one_or_none() is None:
        raise ProfileNotFound()


def get_profile(user_id):
    """{"cv_text": str or None, "skills": [{user_skill_id, skill_name, source, created_at}]}."""
    _check_id(user_id, ProfileNotFound)
    with operation_session(read_only=True, error_message="Could not load your profile.") as session:
        cv_text = session.execute(select(User.cv_text).where(User.user_id == user_id)).one_or_none()
        if cv_text is None:
            raise ProfileNotFound()
        skills = _skills(session, user_id)
    return {"cv_text": cv_text[0], "skills": skills}


def replace_cv(user_id, cv_text, skills, *, now=None):
    """Save new CV text and AI-extracted skills; keep manual skills. Returns a summary."""
    _check_id(user_id, ProfileNotFound)
    text, trimmed = fit_text(cv_text)
    if not text:
        raise ProfileValidationError({"cv": "The CV had no readable text."})
    names = []
    for value in skills:
        try:
            names.append(clean_skill_name(value))
        except ProfileValidationError:
            continue  # skip unusable AI output rather than failing the whole upload
    stamp = now or _now_utc()
    with operation_session(error_message="Could not save your CV. Please try again.") as session:
        _lock_user(session, user_id)
        session.execute(
            delete(UserSkill).where(UserSkill.user_id == user_id, UserSkill.source == "ai_extracted"),
            execution_options={"synchronize_session": False})
        kept = {skill_key(row["skill_name"]) for row in _skills(session, user_id)}
        added = 0
        for name in names:
            key = skill_key(name)
            if key in kept:
                continue
            kept.add(key)
            session.add(UserSkill(user_id=user_id, skill_name=name, source="ai_extracted", created_at=stamp))
            added += 1
        session.execute(update(User).where(User.user_id == user_id).values(cv_text=text),
                        execution_options={"synchronize_session": False})
    return {"skills_added": added, "trimmed": trimmed}


def add_manual_skill(user_id, name, *, now=None):
    """Add a skill and return its saved name.

    If the CV already gave the same skill, that record becomes manual instead,
    so it survives the next CV upload.
    """
    _check_id(user_id, ProfileNotFound)
    name = clean_skill_name(name)
    key = skill_key(name)
    stamp = now or _now_utc()
    with operation_session(error_message="Could not save the skill. Please try again.") as session:
        _lock_user(session, user_id)
        for row in _skills(session, user_id):
            if skill_key(row["skill_name"]) != key:
                continue
            if row["source"] == "manual":
                raise ProfileValidationError({"skill_name": "That skill is already on your list."})
            session.execute(
                update(UserSkill).where(UserSkill.user_skill_id == row["user_skill_id"]).values(source="manual"),
                execution_options={"synchronize_session": False})
            return row["skill_name"]
        session.add(UserSkill(user_id=user_id, skill_name=name, source="manual", created_at=stamp))
    return name


def delete_skill(user_id, user_skill_id):
    _check_id(user_id, ProfileNotFound)
    _check_id(user_skill_id, SkillNotFound)
    with operation_session(error_message="Could not delete the skill. Please try again.") as session:
        result = session.execute(
            delete(UserSkill).where(UserSkill.user_skill_id == user_skill_id, UserSkill.user_id == user_id),
            execution_options={"synchronize_session": False})
        if result.rowcount != 1:
            raise SkillNotFound()
