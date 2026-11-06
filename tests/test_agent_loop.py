"""Agent-loop tests: no network, no API key, fully scripted model."""

from __future__ import annotations

import json

import pytest

from citywheels.agent import (
    MAX_ITERATIONS_ANSWER,
    MODEL_ERROR_ANSWER,
    run_agent,
)
from citywheels.llm import FakeLLM, LLMResponse, ToolCall
from citywheels.policies import PolicyIndex
from citywheels.prompts import SYSTEM_PROMPT
from citywheels.tools import Toolbox


@pytest.fixture
def toolbox(store, data_dir, fixed_now):
    return Toolbox(
        store=store,
        index=PolicyIndex.from_data_dir(data_dir),
        now=fixed_now,
    )


def reply(text: str) -> LLMResponse:
    return LLMResponse(text=text)


def call(name: str, **args: object) -> LLMResponse:
    return LLMResponse(tool_calls=(ToolCall(name, args),))


def test_happy_path_get_booking_then_refund(toolbox):
    client = FakeLLM(
        [
            call("get_booking", booking_id="CW-1002"),
            call("calculate_refund", booking_id="CW-1002"),
            reply("You get back 340.00 EUR: 40.00 EUR of the rental and 300.00 EUR deposit."),
        ]
    )

    result = run_agent("Can I cancel CW-1002?", client=client, tools=toolbox)

    assert result.error is None
    assert result.iterations == 3
    assert result.used_tools == ("get_booking", "calculate_refund")
    assert result.tool_calls[1].result["total_refund_eur"] == "340.00"
    assert "340.00" in result.answer


def test_immediate_answer_without_tool_calls(toolbox):
    client = FakeLLM([reply("Hello, how can I help with your CityWheels booking?")])

    result = run_agent("Hi", client=client, tools=toolbox)

    assert result.iterations == 1
    assert result.tool_calls == ()
    assert result.answer.startswith("Hello")


def test_model_receives_schemas_and_system_prompt(toolbox):
    client = FakeLLM([reply("ok")])

    run_agent("Hi", client=client, tools=toolbox)

    sent = client.calls[0]
    assert sent["system_instruction"] == SYSTEM_PROMPT
    assert sorted(schema["name"] for schema in sent["tools"]) == [
        "calculate_refund",
        "get_booking",
        "search_policies",
    ]
    assert "help@citywheels.example" in sent["system_instruction"]


def test_custom_system_prompt_is_passed_through(toolbox):
    client = FakeLLM([reply("ok")])

    run_agent("Hi", client=client, tools=toolbox, system_instruction="custom prompt")

    assert client.calls[0]["system_instruction"] == "custom prompt"


def test_tool_result_is_sent_back_unchanged(toolbox):
    expected = toolbox.dispatch("get_booking", {"booking_id": "CW-1002"})
    client = FakeLLM([call("get_booking", booking_id="CW-1002"), reply("done")])

    run_agent("Check CW-1002", client=client, tools=toolbox)

    messages = client.calls[1]["messages"]
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["name"] == "get_booking"
    assert messages[2] == {
        "role": "tool",
        "name": "get_booking",
        "result": expected,
    }


def test_unknown_booking_is_reported_to_the_model(toolbox):
    client = FakeLLM(
        [call("get_booking", booking_id="CW-9999"), reply("That booking was not found.")]
    )

    result = run_agent("Check CW-9999", client=client, tools=toolbox)

    record = result.tool_calls[0]
    assert record.result["found"] is False
    assert "error" not in record.result
    fed_back = client.calls[1]["messages"][2]["result"]
    assert fed_back["found"] is False
    assert fed_back["message"] == "Booking CW-9999 was not found."


def test_parallel_tool_calls_are_all_executed(toolbox):
    client = FakeLLM(
        [
            LLMResponse(
                tool_calls=(
                    ToolCall("get_booking", {"booking_id": "CW-1002"}),
                    ToolCall("search_policies", {"query": "deposit"}),
                )
            ),
            reply("Here is what I found."),
        ]
    )

    result = run_agent("Check my booking and the deposit rule", client=client, tools=toolbox)

    assert result.used_tools == ("get_booking", "search_policies")
    messages = client.calls[1]["messages"]
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]


def test_unknown_tool_becomes_an_error_result(toolbox):
    client = FakeLLM([call("delete_everything"), reply("I cannot do that.")])

    result = run_agent("Delete everything", client=client, tools=toolbox)

    assert result.tool_calls[0].result["error"] == "unknown_tool"
    assert result.error is None


def test_malformed_arguments_become_an_error_result(toolbox):
    client = FakeLLM(
        [LLMResponse(tool_calls=(ToolCall("get_booking", "CW-1002"),)), reply("ok")]
    )

    result = run_agent("Check CW-1002", client=client, tools=toolbox)

    assert result.tool_calls[0].result["error"] == "invalid_arguments"


def test_missing_required_argument_becomes_an_error_result(toolbox):
    client = FakeLLM([call("calculate_refund"), reply("ok")])

    result = run_agent("Refund please", client=client, tools=toolbox)

    assert result.tool_calls[0].result["error"] == "invalid_arguments"
    assert "booking_id" in result.tool_calls[0].result["message"]


def test_max_iterations_is_enforced(toolbox):
    client = FakeLLM(
        [call("get_booking", booking_id="CW-1002") for _ in range(5)]
    )

    result = run_agent("Loop forever", client=client, tools=toolbox, max_iterations=2)

    assert result.error == "max_iterations"
    assert result.iterations == 2
    assert len(result.tool_calls) == 2
    assert result.answer == MAX_ITERATIONS_ANSWER
    assert client.remaining == 3


def test_model_failure_is_returned_gracefully(toolbox):
    client = FakeLLM([RuntimeError("network down")])

    result = run_agent("Hi", client=client, tools=toolbox)

    assert result.error is not None
    assert result.error.startswith("model_error: RuntimeError")
    assert result.answer == MODEL_ERROR_ANSWER
    assert result.iterations == 1


def test_tool_failure_is_contained_and_reported_to_the_model(toolbox, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("citywheels.tools.bookings.get_booking", boom)
    client = FakeLLM([call("get_booking", booking_id="CW-1001"), reply("Sorry.")])

    result = run_agent("Check CW-1001", client=client, tools=toolbox)

    assert result.tool_calls[0].result["error"] == "tool_failed"
    assert result.error is None
    fed_back = client.calls[1]["messages"][2]["result"]
    assert fed_back["error"] == "tool_failed"


def test_empty_model_response_is_handled(toolbox):
    client = FakeLLM([LLMResponse()])

    result = run_agent("Hi", client=client, tools=toolbox)

    assert result.error == "empty_response"
    assert result.answer == MODEL_ERROR_ANSWER


def test_max_iterations_must_be_positive(toolbox):
    with pytest.raises(ValueError):
        run_agent("Hi", client=FakeLLM([]), tools=toolbox, max_iterations=0)


def test_result_is_json_serialisable(toolbox):
    client = FakeLLM([call("get_booking", booking_id="CW-1002"), reply("done")])

    result = run_agent("Check CW-1002", client=client, tools=toolbox)

    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["answer"] == "done"
    assert payload["tool_calls"][0]["name"] == "get_booking"
    assert payload["iterations"] == 2
    assert payload["error"] is None


def test_fake_llm_fails_loudly_when_script_runs_out():
    client = FakeLLM([])

    with pytest.raises(AssertionError, match="out of scripted"):
        client.complete([], [])
