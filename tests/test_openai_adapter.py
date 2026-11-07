"""Tests for the OpenAI-compatible adapter: conversion and retry, no network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from citywheels.config import Settings
from citywheels.llm import (
    GeminiClient,
    OpenAICompatibleClient,
    QuotaExhausted,
    ToolCall,
    create_client,
    parse_openai_response,
    retry_after_seconds,
    to_openai_messages,
    to_openai_tools,
)

SCHEMA = {
    "name": "get_booking",
    "description": "look up a booking",
    "parameters": {
        "type": "OBJECT",
        "properties": {"booking_id": {"type": "STRING"}},
        "required": ["booking_id"],
    },
}


def test_tool_schemas_are_lowercased_for_openai():
    tools = to_openai_tools([SCHEMA])

    assert tools[0]["type"] == "function"
    function = tools[0]["function"]
    assert function["name"] == "get_booking"
    assert function["parameters"]["type"] == "object"
    assert function["parameters"]["properties"]["booking_id"]["type"] == "string"


def test_messages_include_the_system_prompt_and_pair_tool_results():
    messages = to_openai_messages(
        [
            {"role": "user", "text": "check CW-1002"},
            {
                "role": "assistant",
                "text": None,
                "tool_calls": [
                    {"name": "get_booking", "args": {"booking_id": "CW-1002"}},
                    {"name": "search_policies", "args": {"query": "deposit"}},
                ],
            },
            {"role": "tool", "name": "get_booking", "result": {"found": True}},
            {"role": "tool", "name": "search_policies", "result": {"found": False}},
        ],
        "be nice",
    )

    assert messages[0] == {"role": "system", "content": "be nice"}
    assert messages[1] == {"role": "user", "content": "check CW-1002"}

    assistant = messages[2]
    assert assistant["role"] == "assistant"
    assert assistant["content"] is None
    assert [entry["function"]["name"] for entry in assistant["tool_calls"]] == [
        "get_booking",
        "search_policies",
    ]

    first_id = assistant["tool_calls"][0]["id"]
    second_id = assistant["tool_calls"][1]["id"]
    assert messages[3]["tool_call_id"] == first_id
    assert messages[4]["tool_call_id"] == second_id
    assert json.loads(messages[3]["content"]) == {"found": True}


def test_assistant_text_without_calls_stays_plain():
    messages = to_openai_messages(
        [{"role": "assistant", "text": "hello", "tool_calls": []}]
    )
    assert messages == [{"role": "assistant", "content": "hello"}]


def test_malformed_tool_arguments_are_passed_through_as_text():
    messages = to_openai_messages(
        [
            {
                "role": "assistant",
                "text": None,
                "tool_calls": [{"name": "get_booking", "args": "CW-1002"}],
            }
        ]
    )
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == "CW-1002"


def _response(content=None, tool_calls=None, usage=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
    )


def test_parse_openai_response_reads_text_calls_and_usage():
    response = _response(
        content="  hello  ",
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                function=SimpleNamespace(
                    name="get_booking",
                    arguments='{"booking_id": "CW-1002"}',
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=12, completion_tokens=4, total_tokens=16
        ),
    )

    parsed = parse_openai_response(response)

    assert parsed.text == "hello"
    assert parsed.tool_calls == (ToolCall("get_booking", {"booking_id": "CW-1002"}),)
    assert parsed.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
    }


def test_parse_openai_response_keeps_malformed_arguments_for_the_dispatcher():
    response = _response(
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                function=SimpleNamespace(name="get_booking", arguments="CW-1002"),
            )
        ]
    )

    parsed = parse_openai_response(response)

    assert parsed.tool_calls[0].args == "CW-1002"


def test_parse_openai_response_without_choices():
    parsed = parse_openai_response(SimpleNamespace(choices=[]))
    assert parsed.text is None
    assert parsed.tool_calls == ()


class _APIError(Exception):
    def __init__(self, status_code, message="error", headers=None):
        super().__init__(message)
        self.status_code = status_code
        if headers is not None:
            self.response = SimpleNamespace(headers=headers)


class _FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeChat:
    def __init__(self, outcomes):
        self.completions = _FakeCompletions(outcomes)


class _FakeOpenAI:
    def __init__(self, outcomes):
        self.chat = _FakeChat(outcomes)


def _client(outcomes, **kwargs):
    sdk = _FakeOpenAI(outcomes)
    slept: list[float] = []
    client = OpenAICompatibleClient(
        model="qwen3-8b",
        base_url="http://localhost:1234/v1",
        sleep=slept.append,
        client=sdk,
        **kwargs,
    )
    return client, sdk, slept


def test_client_retries_rate_limit_and_returns_the_answer():
    client, sdk, slept = _client(
        [
            _APIError(429, "rate limited"),
            _response(content="recovered"),
        ],
        base_delay=0.5,
    )

    result = client.complete([{"role": "user", "text": "hi"}], [SCHEMA])

    assert result.text == "recovered"
    assert len(sdk.chat.completions.requests) == 2
    assert slept == [0.5]


def test_client_sends_model_messages_and_tools():
    client, sdk, _ = _client([_response(content="ok")])

    client.complete(
        [{"role": "user", "text": "hi"}],
        [SCHEMA],
        system_instruction="be nice",
    )

    request = sdk.chat.completions.requests[0]
    assert request["model"] == "qwen3-8b"
    assert request["temperature"] == 0
    assert request["messages"][0]["role"] == "system"
    assert request["tools"][0]["function"]["name"] == "get_booking"


def test_client_omits_tools_when_none_are_given():
    client, sdk, _ = _client([_response(content="ok")])

    client.complete([{"role": "user", "text": "hi"}], [])

    assert "tools" not in sdk.chat.completions.requests[0]


def test_client_does_not_retry_client_errors():
    client, sdk, slept = _client([_APIError(400, "bad request")])

    with pytest.raises(_APIError):
        client.complete([{"role": "user", "text": "hi"}], [])

    assert len(sdk.chat.completions.requests) == 1
    assert slept == []


def test_large_retry_after_header_becomes_quota_exhausted():
    client, sdk, slept = _client(
        [_APIError(429, "quota", headers={"retry-after": "41998"})],
        max_attempts=5,
    )

    with pytest.raises(QuotaExhausted) as caught:
        client.complete([{"role": "user", "text": "hi"}], [])

    assert caught.value.retry_delay_s == 41998.0
    assert len(sdk.chat.completions.requests) == 1
    assert slept == []


def test_retry_after_seconds_reads_headers_and_text():
    assert retry_after_seconds(
        _APIError(429, "slow down", headers={"retry-after": "7"})
    ) == 7.0
    assert retry_after_seconds(_APIError(429, "Please try again in 3s")) == 3.0
    assert retry_after_seconds(_APIError(429, "no hint")) is None


def test_remote_endpoint_requires_a_key():
    with pytest.raises(ValueError):
        OpenAICompatibleClient(model="gpt-4.1-mini")


def test_local_endpoint_accepts_a_placeholder_without_a_key():
    client = OpenAICompatibleClient(
        model="qwen3-8b",
        base_url="http://localhost:1234/v1",
        client=SimpleNamespace(),
    )
    assert client is not None


def _settings(provider):
    from datetime import datetime
    from pathlib import Path

    return Settings(
        data_dir=Path("data"),
        model="test-model",
        api_key="test-key",
        now=datetime(2026, 6, 10, 9, 0),
        provider=provider,
        base_url="http://localhost:1234/v1" if provider == "openai" else None,
    )


def test_factory_returns_the_client_for_the_selected_provider():
    assert isinstance(create_client(_settings("gemini")), GeminiClient)
    assert isinstance(create_client(_settings("openai")), OpenAICompatibleClient)


def test_factory_rejects_an_unknown_provider():
    with pytest.raises(ValueError, match="unsupported provider"):
        create_client(_settings("telepathy"))
