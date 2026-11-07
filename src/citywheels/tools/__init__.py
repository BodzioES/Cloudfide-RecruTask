"""Tool registry: JSON schemas, argument validation and a safe dispatcher.

The language model sees :data:`TOOL_SCHEMAS` and calls :meth:`Toolbox.dispatch`
with a tool name and a JSON object of arguments. Everything that can go wrong
with those arguments is turned into a structured result instead of an exception,
so one bad tool call cannot kill the conversation.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..bookings import BookingStore
from ..policies import PolicyIndex
from . import bookings as booking_tools
from . import policies as policy_tools
from . import refunds as refund_tools

logger = logging.getLogger(__name__)

TOOL_SCHEMAS: list[dict[str, object]] = [
    {
        "name": "search_policies",
        "description": (
            "Search the CityWheels policy documents for rules about prices, "
            "opening hours, deposits, cancellations and refunds. Use this before "
            "answering any question about the shop's rules."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "The customer's question or keywords, in English.",
                },
                "top_k": {
                    "type": "INTEGER",
                    "description": (
                        "How many matching sections to return. Default 3, "
                        "allowed range 1-10."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_booking",
        "description": (
            "Look up one booking by its booking number, for example CW-1001. "
            "Returns the booking details, or a not-found result when the number "
            "does not exist."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "booking_id": {
                    "type": "STRING",
                    "description": "Booking number such as CW-1001.",
                },
            },
            "required": ["booking_id"],
        },
    },
    {
        "name": "calculate_refund",
        "description": (
            "Calculate exactly how much the customer gets back if they cancel "
            "this booking. The amount is computed by code from the cancellation "
            "rules; never calculate or estimate it yourself. Use delay_days=0 "
            "for a cancellation right now and delay_days=1 for tomorrow."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "booking_id": {
                    "type": "STRING",
                    "description": "Booking number such as CW-1001.",
                },
                "delay_days": {
                    "type": "INTEGER",
                    "description": (
                        "Whole days from now to the simulated cancellation. "
                        "0 means now (the default), 1 means tomorrow. "
                        f"Allowed range 0-{refund_tools.MAX_DELAY_DAYS}."
                    ),
                },
            },
            "required": ["booking_id"],
        },
    },
]

_REQUIRED: dict[str, dict[str, type]] = {
    "search_policies": {"query": str},
    "get_booking": {"booking_id": str},
    "calculate_refund": {"booking_id": str},
}

_OPTIONAL: dict[str, dict[str, type]] = {
    "search_policies": {"top_k": int},
    "get_booking": {},
    "calculate_refund": {"delay_days": int},
}


def _error(code: str, tool: object, message: str, **extra: object) -> dict[str, object]:
    return {"error": code, "tool": tool, "message": message, **extra}


def _validate(name: str, args: dict[str, object]) -> dict[str, object] | None:
    required = _REQUIRED[name]
    optional = _OPTIONAL[name]
    allowed = sorted({*required, *optional})

    unexpected = sorted(key for key in args if key not in required and key not in optional)
    if unexpected:
        return _error(
            "invalid_arguments",
            name,
            f"Unexpected argument(s): {', '.join(unexpected)}. "
            f"Allowed arguments: {', '.join(allowed)}.",
        )

    for argument in required:
        if argument not in args:
            return _error(
                "invalid_arguments",
                name,
                f"Missing required argument: '{argument}'.",
            )

    for argument, expected in {**required, **optional}.items():
        if argument not in args:
            continue
        value = args[argument]
        if expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                return _error(
                    "invalid_arguments",
                    name,
                    f"Argument '{argument}' must be an integer, "
                    f"got {type(value).__name__}.",
                )
        elif not isinstance(value, expected):
            return _error(
                "invalid_arguments",
                name,
                f"Argument '{argument}' must be a string, "
                f"got {type(value).__name__}.",
            )
        if isinstance(value, str) and not value.strip():
            return _error(
                "invalid_arguments",
                name,
                f"Argument '{argument}' must not be empty.",
            )

    if name == "calculate_refund" and "delay_days" in args:
        delay = args["delay_days"]
        if isinstance(delay, int) and not isinstance(delay, bool):
            if not 0 <= delay <= refund_tools.MAX_DELAY_DAYS:
                return _error(
                    "invalid_arguments",
                    name,
                    f"Argument 'delay_days' must be between 0 and "
                    f"{refund_tools.MAX_DELAY_DAYS}.",
                )

    return None


class Toolbox:
    """Binds the tool implementations to the data they need."""

    def __init__(
        self,
        *,
        store: BookingStore,
        index: PolicyIndex,
        now: datetime,
    ) -> None:
        self._store = store
        self._index = index
        self._now = now
        self._handlers: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
            "search_policies": lambda args: policy_tools.search_policies(
                args["query"],  # type: ignore[arg-type]
                index=self._index,
                top_k=args.get("top_k", 3),  # type: ignore[arg-type]
            ),
            "get_booking": lambda args: booking_tools.get_booking(
                args["booking_id"],  # type: ignore[arg-type]
                store=self._store,
                now=self._now,
            ),
            "calculate_refund": lambda args: refund_tools.calculate_refund(
                args["booking_id"],  # type: ignore[arg-type]
                store=self._store,
                now=self._now,
                delay_days=args.get("delay_days", 0),  # type: ignore[arg-type]
            ),
        }

    @classmethod
    def from_data_dir(cls, data_dir: Path, *, now: datetime) -> "Toolbox":
        return cls(
            store=BookingStore.from_data_dir(data_dir),
            index=PolicyIndex.from_data_dir(data_dir),
            now=now,
        )

    def schemas(self) -> list[dict[str, object]]:
        """A copy, so callers cannot accidentally mutate the declarations."""

        return copy.deepcopy(TOOL_SCHEMAS)

    def dispatch(self, name: object, args: object = None) -> dict[str, object]:
        """Run one tool call. Never raises for bad input or a failing tool."""

        if not isinstance(name, str) or name not in self._handlers:
            return _error(
                "unknown_tool",
                name,
                f"Unknown tool: {name!r}.",
                available_tools=sorted(self._handlers),
            )

        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(
                "invalid_arguments",
                name,
                "Tool arguments must be a JSON object, "
                f"got {type(args).__name__}.",
            )

        problem = _validate(name, args)
        if problem is not None:
            return problem

        try:
            return self._handlers[name](args)
        except Exception:  # noqa: BLE001 - one broken tool must not kill the loop
            logger.exception("Tool %s failed", name)
            return _error(
                "tool_failed",
                name,
                "The tool failed unexpectedly. Tell the customer you cannot "
                "answer right now.",
            )
