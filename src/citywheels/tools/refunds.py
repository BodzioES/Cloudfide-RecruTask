"""``calculate_refund`` tool: the model gets amounts, it never computes them."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..bookings import BookingStore
from ..refunds import compute_refund
from .bookings import not_found_result

# A customer may ask "and if I cancel tomorrow?"; a whole-day offset is enough
# to cover realistic follow-ups without turning the tool into a time machine.
MAX_DELAY_DAYS = 30


def calculate_refund(
    booking_id: str,
    *,
    store: BookingStore,
    now: datetime,
    delay_days: int = 0,
) -> dict[str, object]:
    """Return a full refund breakdown, or a structured not-found result.

    ``delay_days`` shifts the simulated cancellation moment by whole days. The
    refund rules are still applied here, in code; the model only picks the
    number of days.
    """

    if (
        isinstance(delay_days, bool)
        or not isinstance(delay_days, int)
        or not 0 <= delay_days <= MAX_DELAY_DAYS
    ):
        return {
            "error": "invalid_arguments",
            "tool": "calculate_refund",
            "message": (
                f"Argument 'delay_days' must be an integer between 0 and "
                f"{MAX_DELAY_DAYS}."
            ),
        }

    requested = (booking_id or "").strip()
    if not requested:
        return not_found_result(None)

    booking = store.get(requested)
    if booking is None:
        return not_found_result(requested)

    cancel_at = now + timedelta(days=delay_days)
    return {
        "found": True,
        "delay_days": delay_days,
        "cancel_at": cancel_at.isoformat(),
        **compute_refund(booking, cancel_at).to_dict(),
    }
