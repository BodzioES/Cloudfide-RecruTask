"""Tests for the cancellation rules and the ``calculate_refund`` tool."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from citywheels.bookings import Booking
from citywheels.clock import delta_until
from citywheels.refunds import compute_refund
from citywheels.tools.refunds import calculate_refund

WARSAW = ZoneInfo("Europe/Warsaw")


def make_booking(**overrides) -> Booking:
    start = overrides.pop("start", datetime(2026, 6, 20, 10, 0, tzinfo=WARSAW))
    data = {
        "id": "CW-9001",
        "status": "confirmed",
        "bike_type": "city",
        "bikes": 1,
        "start": start,
        "end": start + timedelta(hours=24),
        "add_ons": (),
        "rental_price_eur": Decimal("40.0"),
        "booking_fee_eur": Decimal("2.0"),
        "deposit_eur": Decimal("50.0"),
        "paid_with": "card",
    }
    data.update(overrides)
    return Booking(**data)


@pytest.mark.parametrize(
    ("booking_id", "percent", "rental", "deposit", "total"),
    [
        ("CW-1001", 100, "40.00", "50.00", "90.00"),
        ("CW-1002", 50, "40.00", "300.00", "340.00"),
        ("CW-1003", 0, "0.00", "50.00", "50.00"),
        ("CW-1004", 100, "23.00", "50.00", "73.00"),
        ("CW-1007", 100, "36.00", "150.00", "186.00"),
        ("CW-1009", 100, "119.00", "50.00", "169.00"),
        ("CW-1010", 50, "20.00", "150.00", "170.00"),
    ],
)
def test_delivered_bookings(store, fixed_now, booking_id, percent, rental, deposit, total):
    result = calculate_refund(booking_id, store=store, now=fixed_now)
    assert result["found"] is True
    assert result["cancellable"] is True
    assert result["refund_percent_of_rental"] == percent
    assert result["rental_refund_eur"] == rental
    assert result["deposit_refund_eur"] == deposit
    assert result["booking_fee_refunded_eur"] == "0.00"
    assert result["total_refund_eur"] == total


@pytest.mark.parametrize(
    ("delta", "percent"),
    [
        (timedelta(hours=73), 100),
        (timedelta(hours=48), 100),
        (timedelta(hours=47, minutes=59), 50),
        (timedelta(hours=24), 50),
        (timedelta(hours=23, minutes=59), 0),
        (timedelta(minutes=1), 0),
        (timedelta(0), 0),
        (timedelta(minutes=-1), 0),
    ],
)
def test_percent_boundaries_are_inclusive(delta, percent, fixed_now):
    booking = make_booking(start=fixed_now + delta)
    assert compute_refund(booking, fixed_now).refund_percent_of_rental == percent


def test_start_in_the_past_still_refunds_the_deposit(fixed_now):
    booking = make_booking(start=fixed_now - timedelta(hours=1))
    result = compute_refund(booking, fixed_now)
    assert result.refund_percent_of_rental == 0
    assert result.deposit_refund_eur == Decimal("50.00")
    assert "past" in result.reason


def test_booking_fee_is_never_refunded(store, fixed_now):
    for booking in store.all():
        result = calculate_refund(booking.id, store=store, now=fixed_now)
        assert result["booking_fee_refunded_eur"] == "0.00"


@pytest.mark.parametrize(
    ("booking_id", "status"),
    [("CW-1005", "cancelled"), ("CW-1006", "completed"), ("CW-1008", "in_progress")],
)
def test_non_cancellable_statuses_refund_nothing(store, fixed_now, booking_id, status):
    result = calculate_refund(booking_id, store=store, now=fixed_now)
    assert result["cancellable"] is False
    assert result["status"] == status
    assert result["refund_percent_of_rental"] == 0
    assert result["rental_refund_eur"] == "0.00"
    assert result["deposit_refund_eur"] == "0.00"
    assert result["total_refund_eur"] == "0.00"
    assert status in result["reason"]


def test_voucher_booking_is_refunded_as_a_voucher(store, fixed_now):
    result = calculate_refund("CW-1004", store=store, now=fixed_now)
    assert result["refund_method"] == "voucher"
    assert result["total_refund_eur"] == "73.00"
    assert "voucher" in result["refund_note"]
    assert "never as money" in result["refund_note"]


def test_card_booking_is_refunded_to_the_card(store, fixed_now):
    result = calculate_refund("CW-1002", store=store, now=fixed_now)
    assert result["refund_method"] == "card"
    assert "card" in result["refund_note"]


def test_zero_percent_reason_mentions_the_deposit(store, fixed_now):
    result = calculate_refund("CW-1003", store=store, now=fixed_now)
    assert result["total_refund_eur"] == "50.00"
    assert "0%" in result["reason"]
    assert "deposit" in result["reason"].lower()


def test_fifty_percent_reason_mentions_hours(store, fixed_now):
    result = calculate_refund("CW-1002", store=store, now=fixed_now)
    assert "33 hours" in result["reason"]
    assert "50%" in result["reason"]


def test_unknown_booking_is_not_found(store, fixed_now):
    result = calculate_refund("CW-9999", store=store, now=fixed_now)
    assert result["found"] is False
    assert "not found" in result["message"].lower()
    assert "total_refund_eur" not in result


def test_blank_booking_id_is_not_found(store, fixed_now):
    result = calculate_refund("   ", store=store, now=fixed_now)
    assert result["found"] is False
    assert result["booking_id"] is None


def test_refund_result_is_json_serialisable(store, fixed_now):
    result = calculate_refund("CW-1002", store=store, now=fixed_now)
    payload = json.dumps(result)
    assert "340.00" in payload


def test_computing_a_refund_does_not_mutate_the_booking(store, fixed_now):
    booking = store.get("CW-1002")
    before = booking.to_dict()
    compute_refund(booking, fixed_now)
    assert booking.to_dict() == before


def test_invariants_hold_for_every_delivered_booking(store, fixed_now):
    for booking in store.all():
        result = calculate_refund(booking.id, store=store, now=fixed_now)
        total = Decimal(result["total_refund_eur"])
        rental = Decimal(result["rental_refund_eur"])
        deposit = Decimal(result["deposit_refund_eur"])
        fee = Decimal(result["booking_fee_refunded_eur"])

        assert total == rental + deposit + fee
        assert fee == Decimal("0.00")
        assert total >= Decimal("0.00")
        assert total <= booking.rental_price_eur + booking.deposit_eur

        if booking.status == "confirmed":
            assert result["cancellable"] is True
            assert result["refund_percent_of_rental"] in {0, 50, 100}
        else:
            assert result["cancellable"] is False
            assert total == Decimal("0.00")


def test_dst_change_uses_absolute_time_not_wall_clock():
    # On 25 October 2026 the clocks go back. 13:00 on the 23rd and 12:00 on
    # the 25th are 48 absolute hours apart, although the wall clock shows 47.
    now = datetime(2026, 10, 23, 13, 0, tzinfo=WARSAW)
    start = datetime(2026, 10, 25, 12, 0, tzinfo=WARSAW)
    assert delta_until(start, now) == timedelta(hours=48)

    booking = make_booking(start=start)
    assert compute_refund(booking, now).refund_percent_of_rental == 100
