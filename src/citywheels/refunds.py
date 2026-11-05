"""Cancellation and refund rules, taken from ``data/policies/cancellations.md``.

The amount is always computed here, in code. The language model only decides
when to call the tool and how to explain the result to the customer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .bookings import Booking
from .clock import delta_until, describe_delta
from .money import format_eur, percent_of, quantize

FULL_REFUND_WINDOW = timedelta(hours=48)
HALF_REFUND_WINDOW = timedelta(hours=24)
CANCELLABLE_STATUS = "confirmed"
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class RefundBreakdown:
    """Every number a customer might ask about, already computed."""

    booking_id: str
    status: str
    cancellable: bool
    refund_percent_of_rental: int
    rental_refund_eur: Decimal
    deposit_refund_eur: Decimal
    booking_fee_refunded_eur: Decimal
    total_refund_eur: Decimal
    refund_method: str
    refund_note: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly view: money as strings, no floats."""

        return {
            "booking_id": self.booking_id,
            "status": self.status,
            "cancellable": self.cancellable,
            "refund_percent_of_rental": self.refund_percent_of_rental,
            "rental_refund_eur": format_eur(self.rental_refund_eur),
            "deposit_refund_eur": format_eur(self.deposit_refund_eur),
            "booking_fee_refunded_eur": format_eur(self.booking_fee_refunded_eur),
            "total_refund_eur": format_eur(self.total_refund_eur),
            "refund_method": self.refund_method,
            "refund_note": self.refund_note,
            "reason": self.reason,
        }


def compute_refund(booking: Booking, now: datetime) -> RefundBreakdown:
    """Apply the cancellation rules to one booking at a given moment."""

    if booking.status != CANCELLABLE_STATUS:
        return RefundBreakdown(
            booking_id=booking.id,
            status=booking.status,
            cancellable=False,
            refund_percent_of_rental=0,
            rental_refund_eur=ZERO,
            deposit_refund_eur=ZERO,
            booking_fee_refunded_eur=ZERO,
            total_refund_eur=ZERO,
            refund_method=booking.paid_with,
            refund_note="No refund is due for this booking.",
            reason=(
                f"Booking status is '{booking.status}'; only confirmed bookings "
                "can be cancelled, so nothing is refunded."
            ),
        )

    delta = delta_until(booking.start, now)
    percent = _refund_percent(delta)
    rental_refund = percent_of(booking.rental_price_eur, percent)
    deposit_refund = quantize(booking.deposit_eur)
    booking_fee_refunded = ZERO
    total_refund = quantize(rental_refund + deposit_refund + booking_fee_refunded)

    return RefundBreakdown(
        booking_id=booking.id,
        status=booking.status,
        cancellable=True,
        refund_percent_of_rental=percent,
        rental_refund_eur=rental_refund,
        deposit_refund_eur=deposit_refund,
        booking_fee_refunded_eur=booking_fee_refunded,
        total_refund_eur=total_refund,
        refund_method=booking.paid_with,
        refund_note=_refund_note(booking.paid_with),
        reason=_reason(delta, percent),
    )


def _refund_percent(delta: timedelta) -> int:
    """The rule table: boundaries are inclusive, compare exact durations."""

    if delta >= FULL_REFUND_WINDOW:
        return 100
    if delta >= HALF_REFUND_WINDOW:
        return 50
    return 0


def _reason(delta: timedelta, percent: int) -> str:
    if delta < timedelta(0):
        return (
            "The rental start is in the past: 0% of the rental price. "
            "The deposit is refunded in full."
        )
    return (
        f"Cancelled {describe_delta(delta)}: {percent}% of the rental price. "
        "The deposit is refunded in full."
    )


def _refund_note(paid_with: str) -> str:
    if paid_with == "voucher":
        return (
            "The rental refund and the deposit are returned as a new CityWheels "
            "voucher, never as money."
        )
    return (
        "The rental refund and the deposit are returned to the original card "
        "within 5 working days."
    )
