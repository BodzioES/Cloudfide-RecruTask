"""Model client abstraction: real Gemini, plus a scripted fake for tests.

The agent loop only knows :class:`LLMClient`: it sends a neutral message list
and receives text and/or tool calls. That keeps the loop testable without a
network, an API key or a model.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol, Sequence

logger = logging.getLogger(__name__)

RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class ToolCall:
    """One function call requested by the model."""

    name: str
    args: object = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "args": self.args}


@dataclass(frozen=True)
class LLMResponse:
    """What one model turn produced."""

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    # Raw provider content, kept so Gemini thought signatures survive the
    # round trip. Fake responses leave it as None.
    raw: object | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMClient(Protocol):
    """Minimal contract the agent loop depends on."""

    def complete(
        self,
        messages: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
        system_instruction: str | None = None,
    ) -> LLMResponse: ...


class FakeLLM:
    """Scripted client: returns prepared responses and records every call.

    A response may be an :class:`LLMResponse` or an exception instance, which
    is raised on that turn. Running out of script raises ``AssertionError`` so
    a test fails loudly instead of silently looping.
    """

    def __init__(self, responses: Iterable[LLMResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        messages: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
        system_instruction: str | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": copy.deepcopy(list(messages)),
                "tools": copy.deepcopy(list(tools)),
                "system_instruction": system_instruction,
            }
        )
        if not self._responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def remaining(self) -> int:
        return len(self._responses)


def to_contents(messages: Sequence[dict[str, object]]) -> list[object]:
    """Convert the neutral message list into Gemini ``Content`` objects.

    Consecutive tool results are grouped into one user turn, which is how the
    Gemini API expects parallel function responses.
    """

    from google.genai import types

    contents: list[types.Content] = []
    pending_responses: list[types.Part] = []

    def flush_responses() -> None:
        if pending_responses:
            contents.append(types.Content(role="user", parts=list(pending_responses)))
            pending_responses.clear()

    for message in messages:
        role = message.get("role")

        if role == "tool":
            pending_responses.append(
                types.Part.from_function_response(
                    name=str(message.get("name") or ""),
                    response=_response_payload(message.get("result")),
                )
            )
            continue

        flush_responses()

        if role == "user":
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(text=str(message.get("text") or ""))],
                )
            )
            continue

        if role != "assistant":
            continue

        raw = message.get("raw")
        if isinstance(raw, types.Content):
            contents.append(raw)
            continue

        parts: list[types.Part] = []
        text = message.get("text")
        if text:
            parts.append(types.Part(text=str(text)))
        for call in message.get("tool_calls") or []:
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        name=str(call.get("name")),
                        args=call.get("args") or {},
                    )
                )
            )
        if parts:
            contents.append(types.Content(role="model", parts=parts))

    flush_responses()
    return contents


def _response_payload(result: object) -> dict[str, object]:
    if isinstance(result, dict):
        return result
    return {"result": result}


def parse_response(response: object) -> LLMResponse:
    """Turn an SDK response into the neutral form used by the loop."""

    calls: list[ToolCall] = []
    for call in getattr(response, "function_calls", None) or []:
        name = getattr(call, "name", None)
        if not name:
            continue
        args = getattr(call, "args", None)
        calls.append(ToolCall(str(name), {} if args is None else args))

    raw = None
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        raw = getattr(candidates[0], "content", None)

    # Prefer the raw parts: ``response.text`` prints a warning when the reply
    # also contains function calls, and we do not want that noise in the CLI.
    text = _extract_text(raw)
    if raw is None or not getattr(raw, "parts", None):
        try:
            candidate_text = response.text  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - the SDK raises when there is no text part
            candidate_text = None
        if isinstance(candidate_text, str) and candidate_text.strip():
            text = candidate_text.strip()

    return LLMResponse(text=text, tool_calls=tuple(calls), raw=raw)


def _extract_text(content: object | None) -> str | None:
    pieces: list[str] = []
    for part in getattr(content, "parts", None) or []:
        if getattr(part, "thought", False):
            continue
        value = getattr(part, "text", None)
        if isinstance(value, str) and value:
            pieces.append(value)
    joined = "\n".join(pieces).strip()
    return joined or None



def _is_retryable(error: Exception) -> bool:
    code = getattr(error, "code", None)
    if isinstance(code, int) and code in RETRYABLE_CODES:
        return True
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and status in RETRYABLE_CODES:
        return True
    return isinstance(error, (ConnectionError, TimeoutError))


class GeminiClient:
    """Gemini implementation of :class:`LLMClient` with retry and timeout."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_ms: int = 60_000,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        client: object | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("an API key is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        self._model = model
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._sleep = sleep

        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=timeout_ms),
            )
        self._client = client

    @classmethod
    def from_settings(cls, settings: object) -> "GeminiClient":
        return cls(
            api_key=settings.require_api_key(),  # type: ignore[attr-defined]
            model=settings.model,  # type: ignore[attr-defined]
        )

    def complete(
        self,
        messages: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
        system_instruction: str | None = None,
    ) -> LLMResponse:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0,
            tools=[types.Tool(function_declarations=list(tools))] if tools else None,
        )
        contents = to_contents(messages)

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.models.generate_content(  # type: ignore[attr-defined]
                    model=self._model,
                    contents=contents,
                    config=config,
                )
                return parse_response(response)
            except Exception as error:  # noqa: BLE001 - classified below
                if attempt >= self._max_attempts or not _is_retryable(error):
                    raise
                delay = self._base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Gemini call failed (attempt %s/%s): %s; retrying in %.1fs",
                    attempt,
                    self._max_attempts,
                    error,
                    delay,
                )
                self._sleep(delay)
