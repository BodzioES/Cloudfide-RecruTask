"""Model client abstraction: real Gemini, plus a scripted fake for tests.

The agent loop only knows :class:`LLMClient`: it sends a neutral message list
and receives text and/or tool calls. That keeps the loop testable without a
network, an API key or a model.
"""

from __future__ import annotations

import copy
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol, Sequence

logger = logging.getLogger(__name__)

RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})

# Gemini attaches a ``retryDelay`` to quota errors. A daily limit reports hours,
# which is far beyond a sensible backoff, so such errors are not retried.
RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?([0-9.]+)s")


class QuotaExhausted(RuntimeError):
    """The provider quota is gone for so long that retrying makes no sense."""

    def __init__(self, message: str, retry_delay_s: float) -> None:
        super().__init__(message)
        self.retry_delay_s = retry_delay_s


def retry_delay_seconds(error: Exception) -> float | None:
    """Read Gemini's suggested retry delay from an error, if it is present."""

    match = RETRY_DELAY_RE.search(str(error))
    return float(match.group(1)) if match else None


RETRY_AFTER_HEADER_RE = re.compile(r"retry[- ]after['\"]?\s*[:=]\s*['\"]?([0-9.]+)", re.I)
TRY_AGAIN_RE = re.compile(r"try again in\s+([0-9.]+)\s*s", re.I)


def retry_after_seconds(error: Exception) -> float | None:
    """Read a retry hint from OpenAI-style headers or error text.

    The OpenAI SDK exposes the HTTP response on most API errors. Local servers
    rarely send it, but cloud endpoints often do, and respecting it is cheaper
    than guessing with exponential backoff.
    """

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for name in ("retry-after", "Retry-After"):
            try:
                value = headers.get(name)
            except Exception:  # noqa: BLE001 - a broken header object is not fatal
                value = None
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass

    text = str(error)
    for pattern in (RETRY_AFTER_HEADER_RE, TRY_AGAIN_RE):
        match = pattern.search(text)
        if match:
            return float(match.group(1))
    return None


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
    # Token counts, when the provider reports them. Used by the benchmark.
    usage: dict[str, int] | None = None

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

    return LLMResponse(text=text, tool_calls=tuple(calls), raw=raw, usage=_gemini_usage(response))


def _gemini_usage(response: object) -> dict[str, int] | None:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return None
    values = {
        "prompt_tokens": getattr(metadata, "prompt_token_count", None),
        "completion_tokens": getattr(metadata, "candidates_token_count", None),
        "total_tokens": getattr(metadata, "total_token_count", None),
    }
    usage = {
        key: int(value)
        for key, value in values.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    return usage or None


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
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    # The OpenAI SDK exception hierarchy is not imported at module level:
    # matching the class name keeps this module importable without the SDK.
    return type(error).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
    }


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
        max_retry_delay_s: float = 300.0,
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
        self._max_retry_delay_s = max_retry_delay_s
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
    def from_settings(cls, settings: object, **kwargs: object) -> "GeminiClient":
        return cls(
            api_key=settings.require_api_key(),  # type: ignore[attr-defined]
            model=settings.model,  # type: ignore[attr-defined]
            **kwargs,  # type: ignore[arg-type]
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
                provider_delay = retry_delay_seconds(error)
                if (
                    provider_delay is not None
                    and provider_delay > self._max_retry_delay_s
                ):
                    logger.error(
                        "Gemini quota is exhausted for about %.1f hours; "
                        "not retrying",
                        provider_delay / 3600,
                    )
                    raise QuotaExhausted(
                        f"quota exhausted; the provider asks to retry in "
                        f"{provider_delay:.0f}s",
                        retry_delay_s=provider_delay,
                    ) from error
                delay = self._base_delay * (2 ** (attempt - 1))
                if provider_delay is not None:
                    # The server knows when the window resets; wait at least
                    # that long instead of guessing with exponential backoff.
                    delay = max(delay, provider_delay + 0.5)
                logger.warning(
                    "Gemini call failed (attempt %s/%s): %s; retrying in %.1fs",
                    attempt,
                    self._max_attempts,
                    error,
                    delay,
                )
                self._sleep(delay)


# ---------------------------------------------------------------------------
# OpenAI-compatible adapter
#
# One adapter covers the official OpenAI API and local servers that implement
# the same chat-completions contract: LM Studio, Ollama, vLLM, llama.cpp,
# Groq, OpenRouter and similar gateways.
# ---------------------------------------------------------------------------


def _lowercase_schema(value: object) -> object:
    """Convert Gemini's uppercase JSON-schema types to standard JSON Schema."""

    if isinstance(value, dict):
        converted: dict[str, object] = {}
        for key, item in value.items():
            if key == "type" and isinstance(item, str):
                converted[key] = item.lower()
            else:
                converted[key] = _lowercase_schema(item)
        return converted
    if isinstance(value, list):
        return [_lowercase_schema(item) for item in value]
    return value


def to_openai_tools(tools: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Convert the neutral tool schemas into OpenAI ``function`` tools."""

    converted: list[dict[str, object]] = []
    for schema in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "parameters": _lowercase_schema(schema.get("parameters", {})),
                },
            }
        )
    return converted


def to_openai_messages(
    messages: Sequence[dict[str, object]],
    system_instruction: str | None = None,
) -> list[dict[str, object]]:
    """Convert the neutral message list into chat-completions messages.

    The neutral format has no provider-specific call ids, so ids are generated
    here and paired with the tool results in the order the agent produced them.
    """

    converted: list[dict[str, object]] = []
    if system_instruction:
        converted.append({"role": "system", "content": system_instruction})

    pending_ids: list[str] = []
    call_counter = 0

    for message in messages:
        role = message.get("role")

        if role == "user":
            converted.append(
                {"role": "user", "content": str(message.get("text") or "")}
            )
            continue

        if role == "assistant":
            text = message.get("text")
            calls = message.get("tool_calls") or []
            if not calls:
                converted.append(
                    {"role": "assistant", "content": str(text or "")}
                )
                continue

            openai_calls: list[dict[str, object]] = []
            pending_ids = []
            for call in calls:
                call_counter += 1
                call_id = f"call_{call_counter}"
                pending_ids.append(call_id)
                openai_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": str(call.get("name")),
                            "arguments": _json_arguments(call.get("args")),
                        },
                    }
                )
            converted.append(
                {
                    "role": "assistant",
                    "content": str(text) if text else None,
                    "tool_calls": openai_calls,
                }
            )
            continue

        if role == "tool":
            call_id = pending_ids.pop(0) if pending_ids else f"call_{call_counter}"
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _json_arguments(message.get("result")),
                }
            )

    return converted


def _json_arguments(value: object) -> str:
    import json

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=True)
    except (TypeError, ValueError):
        return "{}"


def parse_openai_response(response: object) -> LLMResponse:
    """Turn an OpenAI chat-completion response into the neutral form."""

    import json

    choices = getattr(response, "choices", None) or []
    if not choices:
        return LLMResponse(usage=_openai_usage(response))

    message = getattr(choices[0], "message", None)
    text = getattr(message, "content", None)
    if not isinstance(text, str) or not text.strip():
        text = None
    else:
        text = text.strip()

    calls: list[ToolCall] = []
    for call in getattr(message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        name = getattr(function, "name", None)
        if not name:
            continue
        arguments = getattr(function, "arguments", None)
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            # Leave malformed arguments untouched: the dispatcher turns them
            # into a structured invalid_arguments result.
            args = arguments
        calls.append(ToolCall(str(name), {} if args is None else args))

    return LLMResponse(
        text=text,
        tool_calls=tuple(calls),
        raw=None,
        usage=_openai_usage(response),
    )


def _openai_usage(response: object) -> dict[str, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    values = {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
    counts = {
        key: int(value)
        for key, value in values.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    return counts or None


class OpenAICompatibleClient:
    """Adapter for any endpoint that speaks the OpenAI chat-completions API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str | None = None,
        timeout_ms: int = 60_000,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_retry_delay_s: float = 300.0,
        sleep: Callable[[float], None] = time.sleep,
        client: object | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not model:
            raise ValueError("a model name is required")
        if not api_key and not base_url and client is None:
            raise ValueError("an API key is required for a remote OpenAI endpoint")

        self._model = model
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_retry_delay_s = max_retry_delay_s
        self._sleep = sleep

        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key or "not-needed",
                base_url=base_url,
                timeout=timeout_ms / 1000,
                max_retries=0,
            )
        self._client = client

    @classmethod
    def from_settings(
        cls, settings: object, **kwargs: object
    ) -> "OpenAICompatibleClient":
        return cls(
            model=settings.model,  # type: ignore[attr-defined]
            api_key=settings.require_api_key(),  # type: ignore[attr-defined]
            base_url=getattr(settings, "base_url", None),
            **kwargs,  # type: ignore[arg-type]
        )

    def complete(
        self,
        messages: Sequence[dict[str, object]],
        tools: Sequence[dict[str, object]],
        system_instruction: str | None = None,
    ) -> LLMResponse:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": to_openai_messages(messages, system_instruction),
            "temperature": 0,
        }
        if tools:
            payload["tools"] = to_openai_tools(tools)

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.chat.completions.create(  # type: ignore[attr-defined]
                    **payload
                )
                return parse_openai_response(response)
            except Exception as error:  # noqa: BLE001 - classified below
                if attempt >= self._max_attempts or not _is_retryable(error):
                    raise
                provider_delay = retry_after_seconds(error)
                if (
                    provider_delay is not None
                    and provider_delay > self._max_retry_delay_s
                ):
                    logger.error(
                        "Provider quota is exhausted for about %.1f hours; "
                        "not retrying",
                        provider_delay / 3600,
                    )
                    raise QuotaExhausted(
                        f"quota exhausted; the provider asks to retry in "
                        f"{provider_delay:.0f}s",
                        retry_delay_s=provider_delay,
                    ) from error
                delay = self._base_delay * (2 ** (attempt - 1))
                if provider_delay is not None:
                    delay = max(delay, provider_delay + 0.5)
                logger.warning(
                    "Model call failed (attempt %s/%s): %s; retrying in %.1fs",
                    attempt,
                    self._max_attempts,
                    error,
                    delay,
                )
                self._sleep(delay)


def create_client(
    settings: object,
    *,
    timeout_ms: int = 60_000,
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> LLMClient:
    """Build the client selected by ``Settings.provider``.

    This is the single place to switch models and providers: change ``.env``,
    not Python code.
    """

    from .config import PROVIDER_GEMINI, PROVIDER_OPENAI

    provider = getattr(settings, "provider", PROVIDER_GEMINI)
    kwargs = {
        "timeout_ms": timeout_ms,
        "max_attempts": max_attempts,
        "base_delay": base_delay,
    }
    if provider == PROVIDER_GEMINI:
        return GeminiClient.from_settings(settings, **kwargs)
    if provider == PROVIDER_OPENAI:
        return OpenAICompatibleClient.from_settings(settings, **kwargs)
    raise ValueError(f"unsupported provider: {provider!r}")
