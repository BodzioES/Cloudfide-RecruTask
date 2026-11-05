"""Time helpers built around the fixed reference clock from the assignment."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import REFERENCE_NOW, parse_now

HOUR = timedelta(hours=1)


def reference_now() -> datetime:
    """The "current" moment used by the whole application."""

    return parse_now(REFERENCE_NOW)


def delta_until(start: datetime, now: datetime) -> timedelta:
    """How much time is left until ``start``, as absolute time.

    Both values are converted to UTC first. Subtracting two aware datetimes
    that share the same ``ZoneInfo`` object would otherwise ignore the DST
    offset change and produce wall-clock arithmetic instead of real elapsed
    time.
    """

    if start.tzinfo is None or now.tzinfo is None:
        raise ValueError("both datetimes must be timezone-aware")
    return start.astimezone(timezone.utc) - now.astimezone(timezone.utc)


def whole_hours(delta: timedelta) -> int:
    """Number of whole hours in a duration (rounded down)."""

    return int(delta // HOUR)


def describe_delta(delta: timedelta) -> str:
    """Human readable distance to the start, used in explanations."""

    if delta < timedelta(0):
        return "the start is in the past"
    hours = whole_hours(delta)
    unit = "hour" if hours == 1 else "hours"
    return f"{hours} {unit} before the start"
