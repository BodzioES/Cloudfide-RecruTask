"""Tests for the Decimal money helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from citywheels.money import format_eur, percent_of, quantize, to_decimal


def test_to_decimal_keeps_float_representation_exact():
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(40.0) == Decimal("40.0")
    assert str(to_decimal(0.1 + 0.2)) == "0.30000000000000004"


def test_to_decimal_accepts_strings_and_decimals():
    assert to_decimal("40.50") == Decimal("40.50")
    value = Decimal("12.34")
    assert to_decimal(value) is value


def test_to_decimal_rejects_booleans_and_garbage():
    with pytest.raises(ValueError):
        to_decimal(True)
    with pytest.raises(ValueError):
        to_decimal("not money")


def test_quantize_rounds_half_up():
    assert quantize(Decimal("2.005")) == Decimal("2.01")
    assert quantize(Decimal("2.004")) == Decimal("2.00")
    assert quantize(40) == Decimal("40.00")


def test_format_eur_always_has_two_decimals():
    assert format_eur(40) == "40.00"
    assert format_eur(Decimal("73")) == "73.00"
    assert format_eur("119.5") == "119.50"


def test_percent_of_uses_decimal_maths():
    assert percent_of(80, 50) == Decimal("40.00")
    assert percent_of(119, 100) == Decimal("119.00")
    assert percent_of(40, 0) == Decimal("0.00")
    assert percent_of("36.00", 50) == Decimal("18.00")


def test_rounding_policy_half_up_is_explicit_and_tested():
    # Ties go up: the customer gains at most half a cent, the shop never
    # silently keeps it. This pins the policy so a future change is a
    # conscious decision, not an accident.
    assert percent_of("12.35", 50) == Decimal("6.18")
    assert percent_of("12.34", 50) == Decimal("6.17")


def test_decimal_addition_has_no_float_noise():
    assert format_eur(Decimal("0.1") + Decimal("0.2")) == "0.30"
    assert format_eur(Decimal("0.1") + Decimal("0.7")) == "0.80"
