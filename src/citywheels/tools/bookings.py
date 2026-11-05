"""``get_booking`` tool: look up one booking, or say clearly that it is missing."""

from __future__ import annotations

from datetime import datetime

from ..bookings import BookingStore, normalize_booking_id

NOT_FOUND_HINT = (
    "Check the booking number with the customer. Do not guess booking details."
)


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
        return {
            "found": False,
            "booking_id": None,
            "message": "No booking number was provided.",
            "hint": "Ask the customer for their booking number, for example CW-1001.",
        }

    booking = store.get(requested)
    if booking is None:
        return {
            "found": False,
            "booking_id": normalize_booking_id(requested),
            "message": f"Booking {normalize_booking_id(requested)} was not found.",
            "hint": NOT_FOUND_HINT,
        }

    hours_until_start = (booking.start - now).total_seconds() / 3600
    return {
        "found": True,
        **booking.to_dict(),
        "hours_until_start": round(hours_until_start, 2),
    }
