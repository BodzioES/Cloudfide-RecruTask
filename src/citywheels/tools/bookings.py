"""``get_booking`` tool: look up one booking, or say clearly that it is missing."""

from __future__ import annotations

from datetime import datetime

from ..bookings import BookingStore, normalize_booking_id
from ..clock import delta_until

NOT_FOUND_HINT = (
    "Check the booking number with the customer. Do not guess booking details."
)


def not_found_result(booking_id: str | None) -> dict[str, object]:
    """Shared not-found payload, used by every tool that takes a booking id."""

    if booking_id is None:
        return {
            "found": False,
            "booking_id": None,
            "message": "No booking number was provided.",
            "hint": "Ask the customer for their booking number, for example CW-1001.",
        }

    normalized = normalize_booking_id(booking_id)
    return {
        "found": False,
        "booking_id": normalized,
        "message": f"Booking {normalized} was not found.",
        "hint": NOT_FOUND_HINT,
    }


def get_booking(
    booking_id: str,
    *,
    store: BookingStore,
    now: datetime,
) -> dict[str, object]:
    """Return the booking details, or a structured not-found result.

    The function never raises for a wrong or missing booking number: the agent
    loop needs a normal result it can show to the model.
    """

    requested = (booking_id or "").strip()
    if not requested:
        return not_found_result(None)

    booking = store.get(requested)
    if booking is None:
        return not_found_result(requested)

    hours_until_start = delta_until(booking.start, now).total_seconds() / 3600
    return {
        "found": True,
        **booking.to_dict(),
        "hours_until_start": round(hours_until_start, 2),
    }
