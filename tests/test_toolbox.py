"""Tests for the tool registry, schemas and dispatcher."""

from __future__ import annotations

import json

import pytest

from citywheels.policies import PolicyIndex
from citywheels.tools import Toolbox

TOOL_NAMES = ["calculate_refund", "get_booking", "search_policies"]

MINIMAL_ARGS = {
    "search_policies": {"query": "opening hours"},
    "get_booking": {"booking_id": "CW-1001"},
    "calculate_refund": {"booking_id": "CW-1002"},
}


@pytest.fixture
def toolbox(store, data_dir, fixed_now):
    return Toolbox(
        store=store,
        index=PolicyIndex.from_data_dir(data_dir),
        now=fixed_now,
    )


def test_schemas_cover_exactly_the_three_tools(toolbox):
    assert sorted(schema["name"] for schema in toolbox.schemas()) == TOOL_NAMES


def test_every_schema_has_a_description_and_parameters(toolbox):
    for schema in toolbox.schemas():
        assert isinstance(schema["name"], str) and schema["name"]
        assert isinstance(schema["description"], str) and schema["description"]
        parameters = schema["parameters"]
        assert parameters["type"] == "OBJECT"
        assert set(parameters["required"]) <= set(parameters["properties"])


def test_schemas_are_json_serialisable(toolbox):
    assert json.dumps(toolbox.schemas())


def test_schemas_do_not_leak_mutable_state(toolbox):
    first = toolbox.schemas()
    first[0]["name"] = "mutated"
    assert toolbox.schemas()[0]["name"] != "mutated"


def test_every_tool_runs_with_only_its_required_arguments(toolbox):
    for schema in toolbox.schemas():
        name = schema["name"]
        result = toolbox.dispatch(name, MINIMAL_ARGS[name])
        assert "error" not in result, (name, result)


def test_dispatched_results_are_json_serialisable(toolbox):
    for name, args in MINIMAL_ARGS.items():
        assert json.dumps(toolbox.dispatch(name, args))


def test_search_policies_happy_path(toolbox):
    result = toolbox.dispatch("search_policies", {"query": "What time do you open?"})
    assert result["found"] is True
    assert result["results"]


def test_get_booking_happy_path(toolbox):
    result = toolbox.dispatch("get_booking", {"booking_id": "CW-1002"})
    assert result["found"] is True
    assert result["id"] == "CW-1002"


def test_calculate_refund_happy_path(toolbox):
    result = toolbox.dispatch("calculate_refund", {"booking_id": "CW-1002"})
    assert result["found"] is True
    assert result["total_refund_eur"] == "340.00"


def test_unknown_booking_is_not_an_error(toolbox):
    result = toolbox.dispatch("get_booking", {"booking_id": "CW-9999"})
    assert result["found"] is False
    assert "error" not in result


def test_out_of_scope_search_is_not_an_error(toolbox):
    result = toolbox.dispatch("search_policies", {"query": "capital of Australia"})
    assert result["found"] is False
    assert "error" not in result


def test_unknown_tool_is_reported(toolbox):
    result = toolbox.dispatch("delete_everything", {})
    assert result["error"] == "unknown_tool"
    assert result["available_tools"] == TOOL_NAMES


@pytest.mark.parametrize("name", [None, 42, "", "Get_Booking"])
def test_non_string_or_misspelled_tool_name_is_rejected(toolbox, name):
    assert toolbox.dispatch(name, {})["error"] == "unknown_tool"


def test_arguments_must_be_an_object(toolbox):
    result = toolbox.dispatch("get_booking", ["CW-1001"])
    assert result["error"] == "invalid_arguments"
    assert "JSON object" in result["message"]


def test_none_arguments_are_treated_as_empty(toolbox):
    result = toolbox.dispatch("get_booking")
    assert result["error"] == "invalid_arguments"
    assert "booking_id" in result["message"]


def test_missing_argument_is_reported(toolbox):
    result = toolbox.dispatch("calculate_refund", {})
    assert result["error"] == "invalid_arguments"
    assert "Missing required argument" in result["message"]


def test_wrong_argument_type_is_reported(toolbox):
    result = toolbox.dispatch("get_booking", {"booking_id": 1002})
    assert result["error"] == "invalid_arguments"
    assert "must be a string" in result["message"]


def test_boolean_is_not_accepted_as_integer(toolbox):
    result = toolbox.dispatch("search_policies", {"query": "bike", "top_k": True})
    assert result["error"] == "invalid_arguments"
    assert "must be an integer" in result["message"]


def test_empty_argument_is_rejected(toolbox):
    result = toolbox.dispatch("get_booking", {"booking_id": "   "})
    assert result["error"] == "invalid_arguments"
    assert "must not be empty" in result["message"]


def test_unexpected_argument_is_rejected(toolbox):
    result = toolbox.dispatch(
        "calculate_refund", {"booking_id": "CW-1002", "discount": 10}
    )
    assert result["error"] == "invalid_arguments"
    assert "discount" in result["message"]
    assert "allowed" in result["message"].lower()


def test_top_k_is_clamped_by_the_tool(toolbox):
    assert len(toolbox.dispatch("search_policies", {"query": "bike", "top_k": 0})["results"]) == 1
    assert len(toolbox.dispatch("search_policies", {"query": "bike", "top_k": 99})["results"]) <= 10


def test_tool_failure_is_contained(toolbox, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("citywheels.tools.bookings.get_booking", boom)
    result = toolbox.dispatch("get_booking", {"booking_id": "CW-1001"})
    assert result["error"] == "tool_failed"
    assert "unexpectedly" in result["message"]


def test_every_schema_name_has_a_handler(toolbox):
    for schema in toolbox.schemas():
        result = toolbox.dispatch(schema["name"], MINIMAL_ARGS[schema["name"]])
        assert result.get("error") != "unknown_tool"
