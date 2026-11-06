"""The agent loop: model -> tool calls -> model -> answer.

The loop owns no business rules. It sends the question, executes whatever the
model asks for through :meth:`Toolbox.dispatch`, feeds the results back, and
stops on a final text answer or after a fixed number of model turns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .llm import LLMClient
from .prompts import SYSTEM_PROMPT
from .tools import Toolbox

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 6

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

    @property
    def used_tools(self) -> tuple[str, ...]:
        return tuple(record.name for record in self.tool_calls)

    def to_dict(self) -> dict[str, object]:
        return {
            "answer": self.answer,
            "tool_calls": [record.to_dict() for record in self.tool_calls],
            "iterations": self.iterations,
            "error": self.error,
        }


def run_agent(
    question: str,
    *,
    client: LLMClient,
    tools: Toolbox,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    system_instruction: str | None = SYSTEM_PROMPT,
) -> AgentResult:
    """Answer one question. Never raises for model or tool failures."""

    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    messages: list[dict[str, object]] = [{"role": "user", "text": question}]
    records: list[ToolCallRecord] = []
    schemas = tools.schemas()

    for iteration in range(1, max_iterations + 1):
        try:
            response = client.complete(messages, schemas, system_instruction)
        except Exception as error:  # noqa: BLE001 - the loop must stay alive
            logger.exception("Model call failed on iteration %s", iteration)
            return AgentResult(
                answer=MODEL_ERROR_ANSWER,
                tool_calls=tuple(records),
                iterations=iteration,
                error=f"model_error: {type(error).__name__}: {error}",
            )

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
            )

        logger.warning("Model returned neither text nor tool calls")
        return AgentResult(
            answer=MODEL_ERROR_ANSWER,
            tool_calls=tuple(records),
            iterations=iteration,
            error="empty_response",
        )

    logger.warning("Agent stopped after %s model turns", max_iterations)
    return AgentResult(
        answer=MAX_ITERATIONS_ANSWER,
        tool_calls=tuple(records),
        iterations=max_iterations,
        error="max_iterations",
    )
