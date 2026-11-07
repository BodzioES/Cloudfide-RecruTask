"""The agent loop: model -> tool calls -> model -> answer.

The loop owns no business rules. It sends the question and optional earlier
turns, executes whatever the model asks for through :meth:`Toolbox.dispatch`,
feeds the results back, and stops on a final text answer or after a fixed
number of model turns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

from .llm import LLMClient, QuotaExhausted
from .prompts import SYSTEM_PROMPT
from .tools import Toolbox

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 6

# Conversation memory is deliberately small: enough for follow-up questions,
# not enough to blow up the prompt or the bill.
MAX_HISTORY_TURNS = 10
MAX_HISTORY_CHARS = 6000

MODEL_ERROR_ANSWER = (
    "Sorry, I cannot check that right now. Please try again in a moment or "
    "contact the CityWheels staff at help@citywheels.example or +48 000 000 000."
)
MAX_ITERATIONS_ANSWER = (
    "Sorry, I could not finish checking that. Please contact the CityWheels "
    "staff at help@citywheels.example or +48 000 000 000."
)


@dataclass(frozen=True)
class ToolCallRecord:
    """One executed tool call, complete enough for the evaluation harness."""

    name: str
    args: object
    result: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "args": self.args, "result": self.result}


@dataclass(frozen=True)
class AgentResult:
    answer: str
    tool_calls: tuple[ToolCallRecord, ...] = ()
    iterations: int = 0
    error: str | None = None
    usage: dict[str, int] | None = None

    @property
    def used_tools(self) -> tuple[str, ...]:
        return tuple(record.name for record in self.tool_calls)

    def to_dict(self) -> dict[str, object]:
        return {
            "answer": self.answer,
            "tool_calls": [record.to_dict() for record in self.tool_calls],
            "iterations": self.iterations,
            "error": self.error,
            "usage": self.usage,
        }


def normalize_history(
    history: Iterable[object] | None,
) -> list[dict[str, str]]:
    """Keep only well-formed text turns; ignore anything else.

    Conversation memory is a convenience, never a source of truth. A malformed
    entry must not crash the CLI or reach the provider.
    """

    normalized: list[dict[str, str]] = []
    for entry in history or ():
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        text = entry.get("text")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        normalized.append({"role": role, "text": text.strip()})
    return normalized


def trim_history(
    history: Iterable[object] | None,
    *,
    max_turns: int = MAX_HISTORY_TURNS,
    max_chars: int = MAX_HISTORY_CHARS,
) -> list[dict[str, str]]:
    """Keep the most recent complete turns and stay under the character budget."""

    items = normalize_history(history)
    if max_turns <= 0:
        items = []
    else:
        items = items[-max_turns * 2 :]

    total = sum(len(item["text"]) for item in items)
    while len(items) > 1 and total > max_chars:
        total -= len(items.pop(0)["text"])

    # A history that starts with an assistant reply would confuse a chat model.
    while items and items[0]["role"] == "assistant":
        items.pop(0)
    return items


def _add_usage(
    totals: dict[str, int], usage: dict[str, int] | None
) -> None:
    if not usage:
        return
    for key, value in usage.items():
        if isinstance(value, int) and not isinstance(value, bool):
            totals[key] = totals.get(key, 0) + value


def run_agent(
    question: str,
    *,
    client: LLMClient,
    tools: Toolbox,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    system_instruction: str | None = SYSTEM_PROMPT,
    history: Sequence[dict[str, str]] | None = None,
) -> AgentResult:
    """Answer one question. Never raises for model or tool failures.

    ``history`` holds earlier ``{"role": ..., "text": ...}`` turns. Tool calls
    are not replayed; the model gets the conversation, and calls fresh tools for
    the current question.
    """

    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    messages: list[dict[str, object]] = [
        *normalize_history(history),
        {"role": "user", "text": question},
    ]
    records: list[ToolCallRecord] = []
    usage_totals: dict[str, int] = {}
    schemas = tools.schemas()

    for iteration in range(1, max_iterations + 1):
        try:
            response = client.complete(messages, schemas, system_instruction)
        except Exception as error:  # noqa: BLE001 - the loop must stay alive
            if isinstance(error, QuotaExhausted):
                logger.error("Model quota is exhausted; stopping this question")
                prefix = "quota_error"
            else:
                logger.exception("Model call failed on iteration %s", iteration)
                prefix = "model_error"
            return AgentResult(
                answer=MODEL_ERROR_ANSWER,
                tool_calls=tuple(records),
                iterations=iteration,
                error=f"{prefix}: {type(error).__name__}: {error}",
                usage=usage_totals or None,
            )

        _add_usage(usage_totals, response.usage)

        if response.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "text": response.text,
                    "tool_calls": [call.to_dict() for call in response.tool_calls],
                    "raw": response.raw,
                }
            )
            for call in response.tool_calls:
                result = tools.dispatch(call.name, call.args)
                records.append(ToolCallRecord(call.name, call.args, result))
                messages.append(
                    {"role": "tool", "name": call.name, "result": result}
                )
            continue

        answer = (response.text or "").strip()
        if answer:
            return AgentResult(
                answer=answer,
                tool_calls=tuple(records),
                iterations=iteration,
                error=None,
                usage=usage_totals or None,
            )

        logger.warning("Model returned neither text nor tool calls")
        return AgentResult(
            answer=MODEL_ERROR_ANSWER,
            tool_calls=tuple(records),
            iterations=iteration,
            error="empty_response",
            usage=usage_totals or None,
        )

    logger.warning("Agent stopped after %s model turns", max_iterations)
    return AgentResult(
        answer=MAX_ITERATIONS_ANSWER,
        tool_calls=tuple(records),
        iterations=max_iterations,
        error="max_iterations",
        usage=usage_totals or None,
    )
