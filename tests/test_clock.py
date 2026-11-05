"""Tests for the fixed reference clock."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from citywheels.clock import (
    delta_until,
    describe_delta,
    reference_now,
    whole_hours,
)
from citywheels.config import REFERENCE_NOW

WARSAW = ZoneInfo("Europe/Warsaw")


def test_reference_now_is_the_assignment_moment():
    assert reference_now().isoformat() == REFERENCE_NOW
    assert reference_now().tzinfo is not None


def test_delta_until_uses_real_timestamps(store):
    booking = store.get("CW-1007")
    assert delta_until(booking.start, reference_now()) == timedelta(hours=48)


def test_whole_hours_rounds_down():
    assert whole_hours(timedelta(hours=33, minutes=59)) == 33
    assert whole_hours(timedelta(minutes=59)) == 0


def test_describe_delta_is_human_readable():
    assert describe_delta(timedelta(hours=33)) == "33 hours before the start"
    assert describe_delta(timedelta(hours=1)) == "1 hour before the start"
    assert describe_delta(timedelta(hours=-2)) == "the start is in the past"


def test_delta_until_is_absolute_across_dst():
    # Same ZoneInfo on both sides: a naive subtraction would say 47 hours,
    # because the clocks go back on 25 October 2026.
    now = datetime(2026, 10, 23, 13, 0, tzinfo=WARSAW)
    start = datetime(2026, 10, 25, 12, 0, tzinfo=WARSAW)
    assert delta_until(start, now) == timedelta(hours=48)


def test_delta_until_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        delta_until(datetime(2026, 6, 10, 12, 0), reference_now())
