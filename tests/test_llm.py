"""Tests for the Gemini adapter: conversion, parsing and retry, no network."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.genai import errors, types

from citywheels.llm import (
    GeminiClient,
    LLMResponse,
    QuotaExhausted,
    ToolCall,
    parse_response,
    retry_delay_seconds,
    to_contents,
)


def test_to_contents_maps_user_and_assistant_text():
    contents = to_contents(
        [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "hi there", "tool_calls": [], "raw": None},
        ]
    )

    assert [content.role for content in contents] == ["user", "model"]
    assert contents[0].parts[0].text == "hello"
    assert contents[1].parts[0].text == "hi there"


def test_to_contents_builds_function_call_parts():
    contents = to_contents(
        [
            {
                "role": "assistant",
                "text": None,
                "tool_calls": [{"name": "get_booking", "args": {"booking_id": "CW-1001"}}],
                "raw": None,
            }
        ]
    )

    function_call = contents[0].parts[0].function_call
    assert function_call.name == "get_booking"
    assert function_call.args == {"booking_id": "CW-1001"}


def test_to_contents_groups_consecutive_tool_results():
    contents = to_contents(
        [
            {"role": "user", "text": "check two things"},
            {
                "role": "assistant",
                "text": None,
                "tool_calls": [
                    {"name": "get_booking", "args": {"booking_id": "CW-1001"}},
                    {"name": "search_policies", "args": {"query": "deposit"}},
                ],
                "raw": None,
            },
            {"role": "tool", "name": "get_booking", "result": {"found": True}},
            {"role": "tool", "name": "search_policies", "result": {"found": False}},
        ]
    )

    assert [content.role for content in contents] == ["user", "model", "user"]
    responses = contents[-1].parts
    assert [part.function_response.name for part in responses] == [
        "get_booking",
        "search_policies",
    ]
    assert responses[0].function_response.response == {"found": True}


def test_to_contents_reuses_raw_provider_content():
    raw = types.Content(role="model", parts=[types.Part(text="kept")])

    contents = to_contents(
        [{"role": "assistant", "text": "kept", "tool_calls": [], "raw": raw}]
    )

    assert contents[0] is raw


def test_to_contents_wraps_non_dict_tool_results():
    contents = to_contents(
        [{"role": "tool", "name": "odd", "result": ["a", "b"]}]
    )

    assert contents[0].parts[0].function_response.response == {"result": ["a", "b"]}


def test_parse_response_extracts_text_calls_and_raw_content():
    raw = types.Content(role="model", parts=[types.Part(text="  hello  ")])
    response = SimpleNamespace(
        text="  hello  ",
        function_calls=[
            SimpleNamespace(name="get_booking", args={"booking_id": "CW-1001"})
        ],
        candidates=[SimpleNamespace(content=raw)],
    )

    parsed = parse_response(response)

    assert parsed.text == "hello"
    assert parsed.tool_calls == (ToolCall("get_booking", {"booking_id": "CW-1001"}),)
    assert parsed.raw is raw


def test_parse_response_without_text_or_calls():
    class NoText:
        @property
        def text(self):
            raise ValueError("no text part")

        function_calls = None
        candidates = []

    parsed = parse_response(NoText())

    assert parsed.text is None
    assert parsed.tool_calls == ()
    assert parsed.raw is None


def test_parse_response_keeps_malformed_args_for_the_dispatcher():
    response = SimpleNamespace(
        text=None,
        function_calls=[SimpleNamespace(name="get_booking", args="CW-1001")],
        candidates=[],
    )

    parsed = parse_response(response)

    assert parsed.tool_calls[0].args == "CW-1001"


def test_parse_response_reads_parts_without_touching_sdk_text_property():
    class Exploding:
        @property
        def text(self):
            raise AssertionError("must not read response.text when parts exist")

        function_calls = None
        candidates = [
            SimpleNamespace(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="from parts")],
                )
            )
        ]

    parsed = parse_response(Exploding())

    assert parsed.text == "from parts"


class _FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, object]] = []

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeSDKClient:
    def __init__(self, outcomes):
        self.models = _FakeModels(outcomes)


def _ok_response(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(text=text, function_calls=None, candidates=[])


def _client(outcomes, **kwargs) -> tuple[GeminiClient, _FakeSDKClient, list[float]]:
    sdk = _FakeSDKClient(outcomes)
    slept: list[float] = []
    client = GeminiClient(
        api_key="test-key",
        model="gemini-test",
        sleep=slept.append,
        client=sdk,
        **kwargs,
    )
    return client, sdk, slept


def test_client_retries_rate_limit_with_exponential_backoff():
    client, sdk, slept = _client(
        [
            errors.APIError(429, {"error": {"message": "rate"}}),
            errors.APIError(503, {"error": {"message": "unavailable"}}),
            _ok_response("recovered"),
        ],
        base_delay=0.5,
    )

    result = client.complete([{"role": "user", "text": "hi"}], [])

    assert result.text == "recovered"
    assert len(sdk.models.requests) == 3
    assert slept == [0.5, 1.0]


def test_client_does_not_retry_client_errors():
    client, sdk, slept = _client(
        [errors.APIError(400, {"error": {"message": "bad request"}})]
    )

    with pytest.raises(errors.APIError):
        client.complete([{"role": "user", "text": "hi"}], [])

    assert len(sdk.models.requests) == 1
    assert slept == []


def test_client_gives_up_after_max_attempts():
    client, sdk, slept = _client(
        [errors.APIError(429, {}) for _ in range(3)],
        max_attempts=3,
        base_delay=1.0,
    )

    with pytest.raises(errors.APIError):
        client.complete([{"role": "user", "text": "hi"}], [])

    assert len(sdk.models.requests) == 3
    assert slept == [1.0, 2.0]


def _quota_error(retry_delay: str) -> errors.APIError:
    return errors.APIError(
        429,
        {
            "error": {
                "code": 429,
                "message": "quota exceeded",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": retry_delay,
                    }
                ],
            }
        },
    )


def test_retry_delay_seconds_reads_the_provider_hint():
    assert retry_delay_seconds(_quota_error("42s")) == 42.0
    assert retry_delay_seconds(errors.APIError(429, {})) is None


def test_daily_quota_is_not_retried_and_is_reported_as_quota_exhausted():
    client, sdk, slept = _client(
        [_quota_error("41998s")],
        max_attempts=5,
        base_delay=2.0,
    )

    with pytest.raises(QuotaExhausted) as caught:
        client.complete([{"role": "user", "text": "hi"}], [])

    assert caught.value.retry_delay_s == 41998.0
    assert len(sdk.models.requests) == 1
    assert slept == []


def test_short_rate_limit_waits_for_the_provider_hint():
    client, sdk, slept = _client(
        [_quota_error("5s"), _ok_response("recovered")],
        base_delay=0.5,
    )

    result = client.complete([{"role": "user", "text": "hi"}], [])

    assert result.text == "recovered"
    assert len(sdk.models.requests) == 2
    assert slept == [5.5]


def test_client_sends_model_temperature_and_declarations():
    schema = {
        "name": "get_booking",
        "description": "look up a booking",
        "parameters": {
            "type": "OBJECT",
            "properties": {"booking_id": {"type": "STRING"}},
            "required": ["booking_id"],
        },
    }
    client, sdk, _ = _client([_ok_response()])

    client.complete(
        [{"role": "user", "text": "hi"}],
        [schema],
        system_instruction="be nice",
    )

    request = sdk.models.requests[0]
    assert request["model"] == "gemini-test"
    config = request["config"]
    assert config.temperature == 0
    assert config.system_instruction == "be nice"
    declaration = config.tools[0].function_declarations[0]
    assert declaration.name == "get_booking"


def test_client_omits_tools_when_none_are_given():
    client, sdk, _ = _client([_ok_response()])

    client.complete([{"role": "user", "text": "hi"}], [])

    assert sdk.models.requests[0]["config"].tools is None


def test_client_requires_an_api_key():
    with pytest.raises(ValueError):
        GeminiClient(api_key="", model="gemini-test")


def test_llm_response_helpers():
    assert LLMResponse().has_tool_calls is False
    response = LLMResponse(tool_calls=(ToolCall("get_booking", {"booking_id": "CW-1"}),))
    assert response.has_tool_calls is True
    assert response.tool_calls[0].to_dict() == {
        "name": "get_booking",
        "args": {"booking_id": "CW-1"},
    }
