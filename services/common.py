"""Small helpers shared by the services: the stored-ID range and "now" in UTC."""
from datetime import datetime, timezone

MAX_ID = 2147483647  # signed INT primary keys


def now_utc():
    """Naive UTC to the second, matching the stored DATETIME columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def is_valid_id(value):
    """True for a positive int that fits a signed INT primary key."""
    return type(value) is int and 1 <= value <= MAX_ID
