"""Dublin form wall-clock parsing and UTC-to-display conversion, without SQL."""
from datetime import date, datetime, time, timezone
import re
from zoneinfo import ZoneInfo

from services.interview_service import InterviewValidationError


DUBLIN = ZoneInfo("Europe/Dublin")


def parse_dublin_datetime(date_value, time_value):
    """Return naive UTC; reject both DST gaps and repeated wall-clock times."""
    errors = {}
    parsed_date = parsed_time = None
    date_value = date_value.strip() if isinstance(date_value, str) else ""
    time_value = time_value.strip() if isinstance(time_value, str) else ""
    if not date_value:
        errors["date"] = "Enter an interview date."
    else:
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_value):
                raise ValueError
            parsed_date = date.fromisoformat(date_value)
            if parsed_date.year < 1000:
                raise ValueError
        except ValueError:
            errors["date"] = "Enter a valid date in YYYY-MM-DD format (year 1000 or later)."
    if not time_value:
        errors["time"] = "Enter an interview time."
    else:
        try:
            if not re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", time_value):
                raise ValueError
            parsed_time = time.fromisoformat(time_value)
        except ValueError:
            errors["time"] = "Enter a valid 24-hour time, such as 14:30."
    if errors:
        raise InterviewValidationError(errors)

    wall_clock = datetime.combine(parsed_date, parsed_time)
    candidates = set()
    try:
        for fold in (0, 1):
            candidate = wall_clock.replace(tzinfo=DUBLIN, fold=fold).astimezone(timezone.utc)
            if candidate.astimezone(DUBLIN).replace(tzinfo=None) == wall_clock:
                candidates.add(candidate)
    except (OverflowError, ValueError):
        raise InterviewValidationError({"scheduled_at": "Choose a date and time within the supported range."}) from None
    if not candidates:
        raise InterviewValidationError({"scheduled_at": (
            "This time does not exist in Europe/Dublin because clocks go forward. Choose another time."
        )})
    if len(candidates) > 1:
        raise InterviewValidationError({"scheduled_at": (
            "This time occurs twice in Europe/Dublin because clocks go back. Choose a time outside the repeated hour."
        )})
    return candidates.pop().replace(tzinfo=None)


def utc_to_dublin(value):
    """MySQL DATETIME is naive UTC; display it as an aware Dublin datetime."""
    if value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DUBLIN)
