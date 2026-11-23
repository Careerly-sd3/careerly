"""The only module that talks to Google Gemini (google-genai SDK, Interactions API).

Every function returns a checked result or raises AIServiceError, whose `message`
is safe to show users. Prompts, CVs and job adverts are never logged.
"""
import base64
import logging
import os

from pydantic import BaseModel, ValidationError

from services import ai_prompts as prompts

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
# Give up on a call after this long, so a stalled connection can't hold a background worker forever.
REQUEST_TIMEOUT_SECONDS = 60
# Same input, same answer: keeps the match score stable. Interview prep leaves this off for variety.
REPEATABLE = {"temperature": 0, "seed": 42}

# Limits applied to Gemini's answers, which can ignore length hints.
CV_MAX_SKILLS = 60
SKILL_MAX_CHARS = 100
FIELD_MAX_CHARS = 500
EVIDENCE_MAX_CHARS = 200
MAX_REQUIREMENTS = 40
MAX_ADVICE = 4
MAX_QUESTIONS = 12
MAX_QUESTIONS_TO_ASK = 3
# Less page text than this almost always means a login wall or an error page.
MIN_JOB_AD_CHARS = 80
UNREADABLE_MARKER = "UNREADABLE"

NOT_CONFIGURED = "AI features are not set up yet. Add GEMINI_API_KEY to the .env file and restart the app."
KEY_REJECTED = "The AI service rejected the API key. Check GEMINI_API_KEY in the .env file."
BUSY = "The AI service is busy right now. Wait a minute and try again."
BAD_REQUEST = "The AI service couldn't process this request. Try shorter text or a different file."
UNAVAILABLE = "The AI service didn't answer. Try again in a moment."
BAD_RESPONSE = "The AI gave an answer we couldn't use. Try again."
LINK_UNREADABLE = ("Couldn't read that page. Sites like LinkedIn need a login. "
                   "Paste the job description, upload the page saved as a PDF, "
                   "or try the company's own careers page.")
FILE_NOT_JOB_AD = "That file doesn't look like a job advert. Upload the job page saved as a PDF or a screenshot."
FILE_NOT_CV = "That file doesn't look like a CV. Upload your CV as a PDF."


class AIServiceError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class AINotConfigured(AIServiceError):
    def __init__(self):
        super().__init__(NOT_CONFIGURED)


class AIUnreadable(AIServiceError):
    """Gemini read the input, but it isn't what was asked for (not a CV, a login page...)."""


# ---------- calling Gemini ----------

_cached_client = (None, None)  # (api key, client)


def is_configured():
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def model_name():
    return os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


def _client():
    global _cached_client
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise AINotConfigured()
    if _cached_client[0] != key:
        from google import genai  # imported on first use, so the app starts without a key
        _cached_client = (key, genai.Client(api_key=key))
    return _cached_client[1]


def _message_for_status(status):
    if status == 429:
        return BUSY
    if status in (401, 403):
        return KEY_REJECTED
    if status == 400:
        return BAD_REQUEST
    return UNAVAILABLE


def _interact(*, input, system_instruction, response_format=None, tools=None, repeatable=False):
    """One Gemini call; returns the reply text."""
    params = {"model": model_name(), "input": input, "system_instruction": system_instruction}
    if response_format:
        params["response_format"] = response_format
    if tools:
        params["tools"] = tools
    if repeatable:
        params["generation_config"] = REPEATABLE
    client = _client()
    try:
        interaction = client.interactions.create(**params, timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as error:  # the SDK raises several error types; all become one safe message
        status = getattr(error, "status_code", None)
        logger.warning("Gemini request failed: %s (HTTP status %s)", type(error).__name__, status)
        raise AIServiceError(_message_for_status(status)) from None
    text = getattr(interaction, "output_text", None)
    if not isinstance(text, str) or not text.strip():
        logger.warning("Gemini returned no text (interaction status %s)", getattr(interaction, "status", None))
        raise AIServiceError(BAD_RESPONSE)
    return text


def _inline_refs(node, definitions):
    """Replace $ref pointers with their definitions; Pydantic puts nested models in $defs."""
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline_refs(definitions[node["$ref"].rsplit("/", 1)[-1]], definitions)
        return {key: _inline_refs(value, definitions) for key, value in node.items() if key != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(item, definitions) for item in node]
    return node


def json_schema(schema: type[BaseModel]):
    raw = schema.model_json_schema()
    return _inline_refs(raw, raw.get("$defs", {}))


def generate_structured(input, schema: type[BaseModel], *, system_instruction, repeatable=True):
    """Ask for JSON in the shape of `schema`; return it checked."""
    text = _interact(
        input=input, system_instruction=system_instruction, repeatable=repeatable,
        response_format={"type": "text", "mime_type": "application/json", "schema": json_schema(schema)},
    )
    try:
        return schema.model_validate_json(text)
    except ValidationError as error:
        logger.warning("Gemini JSON did not match %s (%d errors)", schema.__name__, error.error_count())
        raise AIServiceError(BAD_RESPONSE) from None


# ---------- helpers ----------

def file_part(data: bytes, mime_type: str):
    kind = "image" if mime_type.startswith("image/") else "document"
    return {"type": kind, "data": base64.b64encode(data).decode("ascii"), "mime_type": mime_type}


def _text_part(text):
    return {"type": "text", "text": text}


def _short(value, limit):
    """Collapse whitespace and cut to `limit` characters."""
    return " ".join((value or "").split())[:limit].rstrip()


def _unique(values, *, max_items, max_chars):
    """Shortened, non-empty values without case-insensitive duplicates."""
    seen, result = set(), []
    for value in values:
        text = _short(value, max_chars)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
        if len(result) == max_items:
            break
    return result


# ---------- features ----------

def extract_cv(data: bytes, mime_type: str) -> prompts.CvExtraction:
    result = generate_structured(
        [file_part(data, mime_type), _text_part("Extract the CV text and skills from the attached CV.")],
        prompts.CvExtraction, system_instruction=prompts.CV_SYSTEM)
    if not result.is_cv or not result.cv_text.strip():
        raise AIUnreadable(FILE_NOT_CV)
    return prompts.CvExtraction(
        is_cv=True, cv_text=result.cv_text.strip(),
        skills=_unique(result.skills, max_items=CV_MAX_SKILLS, max_chars=SKILL_MAX_CHARS))


def read_job_ad_url(url: str) -> str:
    """Gemini's URL context tool opens the page (our server never fetches it)."""
    text = _interact(input=prompts.job_ad_url_prompt(url), system_instruction=prompts.JOB_AD_URL_SYSTEM,
                     tools=[{"type": "url_context"}]).strip()
    if text.upper().startswith(UNREADABLE_MARKER) or len(text) < MIN_JOB_AD_CHARS:
        raise AIUnreadable(LINK_UNREADABLE)
    return text


def read_job_ad_file(data: bytes, mime_type: str) -> str:
    result = generate_structured(
        [file_part(data, mime_type), _text_part("Return the job advertisement text from the attached file.")],
        prompts.JobAdText, system_instruction=prompts.JOB_AD_FILE_SYSTEM)
    text = result.text.strip()
    if not result.is_job_ad or len(text) < MIN_JOB_AD_CHARS:
        raise AIUnreadable(FILE_NOT_JOB_AD)
    return text


def extract_job_fields(job_ad_text: str, today) -> prompts.JobFields:
    result = generate_structured(prompts.job_fields_prompt(job_ad_text, today), prompts.JobFields,
                                 system_instruction=prompts.JOB_FIELDS_SYSTEM)
    return prompts.JobFields(**{name: _short(value, FIELD_MAX_CHARS) or None
                                for name, value in result.model_dump().items()})


def compare_skills(job_ad_text: str, cv_text: str, skills: list[str]) -> prompts.SkillComparison:
    result = generate_structured(prompts.compare_prompt(job_ad_text, cv_text, skills), prompts.SkillComparison,
                                 system_instruction=prompts.COMPARE_SYSTEM)
    seen, requirements = set(), []
    for requirement in result.requirements:
        name = _short(requirement.skill, SKILL_MAX_CHARS)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        requirements.append(requirement.model_copy(update={
            "skill": name, "evidence": _short(requirement.evidence, EVIDENCE_MAX_CHARS)}))
        if len(requirements) == MAX_REQUIREMENTS:
            break
    return prompts.SkillComparison(
        requirements=requirements,
        advice=_unique(result.advice, max_items=MAX_ADVICE, max_chars=FIELD_MAX_CHARS))


def interview_prep(**context) -> prompts.InterviewPrep:
    """`context` holds the keyword arguments of ai_prompts.prep_prompt."""
    result = generate_structured(prompts.prep_prompt(**context), prompts.InterviewPrep,
                                 system_instruction=prompts.PREP_SYSTEM, repeatable=False)
    questions = [question.model_copy(update={
                     "question": _short(question.question, FIELD_MAX_CHARS),
                     "why_asked": _short(question.why_asked, FIELD_MAX_CHARS),
                     "answer_tip": _short(question.answer_tip, FIELD_MAX_CHARS)})
                 for question in result.questions if question.question.strip()][:MAX_QUESTIONS]
    if not questions:
        raise AIServiceError(BAD_RESPONSE)
    return prompts.InterviewPrep(
        questions=questions,
        questions_to_ask=_unique(result.questions_to_ask, max_items=MAX_QUESTIONS_TO_ASK,
                                 max_chars=FIELD_MAX_CHARS))
