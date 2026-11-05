"""Tests for the booking model, loader and the ``get_booking`` tool."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from citywheels.bookings import BookingDataError, BookingStore
from citywheels.tools.bookings import get_booking

WARSAW_SUMMER = timezone(timedelta(hours=2))


def make_raw(**overrides):
    raw = {
        "id": "CW-2001",
        "status": "confirmed",
        "bike_type": "city",
        "bikes": 1,
        "start": "2026-06-13T10:00:00+02:00",
        "end": "2026-06-15T10:00:00+02:00",
        "add_ons": [],
        "rental_price_eur": 40.0,
        "booking_fee_eur": 2.0,
        "deposit_eur": 50.0,
        "paid_with": "card",
    }
    raw.update(overrides)
    return raw


def write_bookings(path: Path, bookings: list[dict]) -> Path:
    path.write_text(json.dumps(bookings), encoding="utf-8")
    return path


def test_store_loads_all_delivered_bookings(store):
    assert len(store) == 10
    assert {booking.id for booking in store.all()} == {
        f"CW-{number}" for number in range(1001, 1011)
    }


def test_get_is_case_insensitive_and_trims_spaces(store):
    assert store.get("cw-1002").id == "CW-1002"
    assert store.get("  CW-1001  ").id == "CW-1001"


def test_get_unknown_or_empty_returns_none(store):
    assert store.get("CW-9999") is None
    assert store.get("") is None


def test_booking_fields_are_parsed_exactly(store):
    booking = store.get("CW-1002")
    assert booking.status == "confirmed"
    assert booking.bike_type == "e-bike"
    assert booking.bikes == 2
    assert booking.start == datetime(2026, 6, 11, 18, 0, tzinfo=WARSAW_SUMMER)
    assert booking.end == datetime(2026, 6, 12, 18, 0, tzinfo=WARSAW_SUMMER)
    assert booking.rental_price_eur == Decimal("80.0")
    assert booking.booking_fee_eur == Decimal("2.0")
    assert booking.deposit_eur == Decimal("300.0")
    assert booking.paid_with == "card"


def test_booking_add_ons_are_kept(store):
    assert store.get("CW-1004").add_ons == ("child_seat",)


def test_to_dict_uses_strings_for_money(store):
    payload = store.get("CW-1002").to_dict()
    assert payload["rental_price_eur"] == "80.00"
    assert payload["booking_fee_eur"] == "2.00"
    assert payload["deposit_eur"] == "300.00"
    assert payload["start"] == "2026-06-11T18:00:00+02:00"
    assert payload["add_ons"] == []


def test_tool_returns_full_booking(store, fixed_now):
    result = get_booking("cw-1002", store=store, now=fixed_now)
    assert result["found"] is True
    assert result["id"] == "CW-1002"
    assert result["bike_type"] == "e-bike"
    assert result["rental_price_eur"] == "80.00"
    assert result["deposit_eur"] == "300.00"
    assert result["hours_until_start"] == 33.0


def test_tool_reports_hours_for_boundary_booking(store, fixed_now):
    assert get_booking("CW-1007", store=store, now=fixed_now)["hours_until_start"] == 48.0
    assert get_booking("CW-1010", store=store, now=fixed_now)["hours_until_start"] == 24.0


def test_tool_unknown_booking_is_not_found_not_an_error(store, fixed_now):
    result = get_booking("CW-9999", store=store, now=fixed_now)
    assert result["found"] is False
    assert result["booking_id"] == "CW-9999"
    assert "not found" in result["message"].lower()
    assert "hint" in result
    assert "rental_price_eur" not in result


def test_tool_blank_booking_id(store, fixed_now):
    result = get_booking("   ", store=store, now=fixed_now)
    assert result["found"] is False
    assert result["booking_id"] is None


@pytest.mark.parametrize("weird_id", ["../../etc/passwd", "'; DROP TABLE", "not-a-booking", "0"])
def test_tool_never_raises_for_weird_ids(store, fixed_now, weird_id):
    result = get_booking(weird_id, store=store, now=fixed_now)
    assert result["found"] is False


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(BookingDataError, match="not found"):
        BookingStore.from_data_dir(tmp_path)


def test_invalid_json_raises_clear_error(tmp_path):
    path = tmp_path / "bookings.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(BookingDataError, match="invalid JSON"):
        BookingStore.from_file(path)


def test_missing_field_is_reported_with_index(tmp_path):
    path = tmp_path / "bookings.json"
    path.write_text(json.dumps([{"id": "CW-1"}]), encoding="utf-8")
    with pytest.raises(BookingDataError, match="index 0"):
        BookingStore.from_file(path)


def test_duplicate_ids_are_rejected(tmp_path):
    path = write_bookings(tmp_path / "bookings.json", [make_raw(), make_raw()])
    with pytest.raises(BookingDataError, match="duplicate"):
        BookingStore.from_file(path)


def test_naive_datetime_is_rejected(tmp_path):
    path = write_bookings(
        tmp_path / "bookings.json", [make_raw(start="2026-06-13T10:00:00")]
    )
    with pytest.raises(BookingDataError, match="time zone"):
        BookingStore.from_file(path)


def test_end_before_start_is_rejected(tmp_path):
    path = write_bookings(
        tmp_path / "bookings.json",
        [make_raw(start="2026-06-15T10:00:00+02:00", end="2026-06-13T10:00:00+02:00")],
    )
    with pytest.raises(BookingDataError, match="end must be after start"):
        BookingStore.from_file(path)


def test_non_array_json_is_rejected(tmp_path):
    path = tmp_path / "bookings.json"
    path.write_text(json.dumps({"id": "CW-1"}), encoding="utf-8")
    with pytest.raises(BookingDataError, match="JSON array"):
        BookingStore.from_file(path)


def test_non_object_entry_is_rejected(tmp_path):
    path = tmp_path / "bookings.json"
    path.write_text(json.dumps(["CW-1"]), encoding="utf-8")
    with pytest.raises(BookingDataError, match="expected an object"):
        BookingStore.from_file(path)


def test_contains_supports_normalized_ids(store):
    assert "cw-1003" in store
    assert "CW-9999" not in store
    assert 1003 not in store
