"""``calculate_refund`` tool: the model gets amounts, it never computes them."""

from __future__ import annotations

from datetime import datetime

from ..bookings import BookingStore
from ..refunds import compute_refund
from .bookings import not_found_result


def calculate_refund(
    booking_id: str,
    *,
    store: BookingStore,
    now: datetime,
) -> dict[str, object]:
    """Return a full refund breakdown, or a structured not-found result."""

    requested = (booking_id or "").strip()
    if not requested:
        return not_found_result(None)

    booking = store.get(requested)
    if booking is None:
        return not_found_result(requested)

    return {"found": True, **compute_refund(booking, now).to_dict()}
