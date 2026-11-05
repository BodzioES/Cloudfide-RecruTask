"""Booking model and loader for ``data/bookings.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .money import format_eur, to_decimal


class BookingDataError(RuntimeError):
    """Raised when the booking file is missing, unreadable or malformed."""


def normalize_booking_id(value: str) -> str:
    """Booking numbers are matched case-insensitively and ignore outer spaces."""

    return value.strip().upper()


@dataclass(frozen=True)
class Booking:
    id: str
    status: str
    bike_type: str
    bikes: int
    start: datetime
    end: datetime
    add_ons: tuple[str, ...]
    rental_price_eur: Decimal
    booking_fee_eur: Decimal
    deposit_eur: Decimal
    paid_with: str

    REQUIRED_FIELDS = (
        "id",
        "status",
        "bike_type",
        "bikes",
        "start",
        "end",
        "add_ons",
        "rental_price_eur",
        "booking_fee_eur",
        "deposit_eur",
        "paid_with",
    )

    @classmethod
    def from_dict(cls, raw: dict) -> "Booking":
        if not isinstance(raw, dict):
            raise ValueError(f"expected an object, got {type(raw).__name__}")

        missing = [field for field in cls.REQUIRED_FIELDS if field not in raw]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")

        start = _parse_datetime(raw["start"], "start")
        end = _parse_datetime(raw["end"], "end")
        if end <= start:
            raise ValueError(f"booking {raw['id']}: end must be after start")

        return cls(
            id=str(raw["id"]).strip().upper(),
            status=str(raw["status"]).strip().lower(),
            bike_type=str(raw["bike_type"]).strip(),
            bikes=int(raw["bikes"]),
            start=start,
            end=end,
            add_ons=tuple(str(item) for item in raw["add_ons"] or ()),
            rental_price_eur=to_decimal(raw["rental_price_eur"]),
            booking_fee_eur=to_decimal(raw["booking_fee_eur"]),
            deposit_eur=to_decimal(raw["deposit_eur"]),
            paid_with=str(raw["paid_with"]).strip().lower(),
        )

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly representation with money as strings."""

        return {
            "id": self.id,
            "status": self.status,
            "bike_type": self.bike_type,
            "bikes": self.bikes,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "add_ons": list(self.add_ons),
            "rental_price_eur": format_eur(self.rental_price_eur),
            "booking_fee_eur": format_eur(self.booking_fee_eur),
            "deposit_eur": format_eur(self.deposit_eur),
            "paid_with": self.paid_with,
        }


def _parse_datetime(value: object, field: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid {field} datetime {value!r}") from exc
    if moment.tzinfo is None:
        raise ValueError(f"{field} must include a time zone offset")
    return moment


class BookingStore:
    """Read-only index of all bookings, keyed by normalized booking number."""

    def __init__(self, bookings: list[Booking]) -> None:
        self._by_id: dict[str, Booking] = {}
        for booking in bookings:
            if booking.id in self._by_id:
                raise BookingDataError(f"duplicate booking id: {booking.id}")
            self._by_id[booking.id] = booking

    @classmethod
    def from_file(cls, path: Path) -> "BookingStore":
        if not path.is_file():
            raise BookingDataError(f"booking file not found: {path}")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BookingDataError(f"cannot read booking file {path}: {exc}") from exc
        try:
            raw = json.loads(text, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise BookingDataError(f"invalid JSON in {path}: {exc}") from exc
        if not isinstance(raw, list):
            raise BookingDataError(f"{path} must contain a JSON array of bookings")

        bookings: list[Booking] = []
        for index, item in enumerate(raw):
            try:
                bookings.append(Booking.from_dict(item))
            except (TypeError, ValueError) as exc:
                raise BookingDataError(f"invalid booking at index {index}: {exc}") from exc
        return cls(bookings)

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> "BookingStore":
        return cls.from_file(Path(data_dir) / "bookings.json")

    def all(self) -> tuple[Booking, ...]:
        return tuple(self._by_id.values())

    def get(self, booking_id: str) -> Booking | None:
        if not booking_id:
            return None
        return self._by_id.get(normalize_booking_id(booking_id))

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, booking_id: object) -> bool:
        return isinstance(booking_id, str) and self.get(booking_id) is not None
