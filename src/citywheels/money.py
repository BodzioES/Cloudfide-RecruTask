"""Money helpers.

Every amount in this project is a :class:`~decimal.Decimal`. Floats never take
part in the arithmetic, so results are exact and reproducible.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
ONE_HUNDRED = Decimal("100")

# Rounding policy, applied once at the final step of a calculation:
#   * half up matches how a customer reads an amount ("2.005 -> 2.01");
#   * a tie therefore goes in the customer's favour by at most half a cent;
#   * for the delivered data (whole-euro prices, 0/50/100% refunds) rounding
#     never changes a value, so the policy is a safety net, not a hidden fee.
# If a future rule ever charges the customer (late fees, damage), it must pick
# its own policy explicitly instead of reusing this constant silently.
ROUNDING = ROUND_HALF_UP


def to_decimal(value: object) -> Decimal:
    """Convert a number, string or Decimal into a Decimal without float noise."""

    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError(f"Cannot convert {value!r} to Decimal")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Cannot convert {value!r} to Decimal") from exc


def quantize(value: object) -> Decimal:
    """Round to cents using the project rounding policy."""

    return to_decimal(value).quantize(CENT, rounding=ROUNDING)


def format_eur(value: object) -> str:
    """Format an amount as a plain string with two decimals, e.g. ``"40.00"``."""

    return f"{quantize(value):.2f}"


def percent_of(amount: object, percent: object) -> Decimal:
    """Return ``percent`` percent of ``amount``.

    The multiplication and division happen on exact Decimals; rounding is
    applied once, to the final result only.
    """

    result = to_decimal(amount) * to_decimal(percent) / ONE_HUNDRED
    return quantize(result)
