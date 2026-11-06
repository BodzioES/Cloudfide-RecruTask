"""CLI tests: argument handling, output modes and exit codes, no network."""

from __future__ import annotations

import io
import json
import sys

import pytest

from citywheels.agent import MODEL_ERROR_ANSWER
from citywheels.cli import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE, main
from citywheels.config import Settings
from citywheels.llm import FakeLLM, LLMResponse, ToolCall


@pytest.fixture
def settings(data_dir, fixed_now):
    return Settings(
        data_dir=data_dir,
        model="gemini-test",
        api_key="test-key",
        now=fixed_now,
    )


@pytest.fixture
def use_settings(monkeypatch, settings):
    monkeypatch.setattr("citywheels.cli.load_settings", lambda now=None: settings)
    return settings


def client_with(*responses):
    return lambda settings: FakeLLM(responses)


def call(name: str, **args: object) -> LLMResponse:
    return LLMResponse(tool_calls=(ToolCall(name, args),))


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    assert "usage: citywheels" in capsys.readouterr().out


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert "citywheels" in capsys.readouterr().out


def test_missing_api_key_is_a_friendly_error(monkeypatch, data_dir, fixed_now, capsys):
    monkeypatch.setattr(
        "citywheels.cli.load_settings",
        lambda now=None: Settings(
            data_dir=data_dir,
            model="gemini-test",
            api_key=None,
            now=fixed_now,
        ),
    )

    code = main(["Hello"])

    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert "GEMINI_API_KEY" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_plain_output_is_only_the_answer(use_settings, capsys):
    code = main(
        ["Hello"],
        client_factory=client_with(LLMResponse(text="Hi, how can I help?")),
    )

    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert captured.out == "Hi, how can I help?\n"
    assert "tool" not in captured.err


def test_plain_output_is_ascii_safe(use_settings, capsys):
    code = main(
        ["Hello"],
        client_factory=client_with(LLMResponse(text="It costs \u20ac40 \u2013 total.")),
    )

    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert captured.out == "It costs EUR 40 - total.\n"


def test_json_keeps_unicode_when_parsed(use_settings, capsys):
    code = main(
        ["Hello", "--json"],
        client_factory=client_with(LLMResponse(text="It costs \u20ac40.")),
    )

    raw = capsys.readouterr().out
    assert code == EXIT_OK
    assert "\u20ac" not in raw
    assert json.loads(raw)["answer"] == "It costs \u20ac40."


def test_json_output_has_the_documented_shape(use_settings, capsys):
    code = main(
        ["Check CW-1002", "--json"],
        client_factory=client_with(
            call("get_booking", booking_id="CW-1002"),
            LLMResponse(text="Booking CW-1002 is confirmed."),
        ),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["answer"] == "Booking CW-1002 is confirmed."
    assert payload["iterations"] == 2
    assert payload["error"] is None
    assert payload["tool_calls"][0]["name"] == "get_booking"
    assert payload["tool_calls"][0]["result"]["found"] is True


def test_verbose_prints_tool_calls_to_stderr(use_settings, capsys):
    code = main(
        ["Check CW-1002", "--verbose"],
        client_factory=client_with(
            call("get_booking", booking_id="CW-1002"),
            LLMResponse(text="Confirmed."),
        ),
    )

    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert captured.out == "Confirmed.\n"
    assert "tool get_booking" in captured.err
    assert "CW-1002" in captured.err
    assert "model=gemini-test" in captured.err


def test_repl_answers_until_quit(use_settings, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("Hello\nquit\n"))

    code = main(
        [],
        client_factory=client_with(LLMResponse(text="Hi, how can I help?")),
    )

    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert captured.out == "Hi, how can I help?\n"
    assert "> " in captured.err


def test_repl_stops_at_end_of_input(use_settings, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    code = main([], client_factory=client_with())

    assert code == EXIT_OK
    assert capsys.readouterr().out == ""


def test_repl_accepts_json_lines(use_settings, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("Hello\nexit\n"))

    code = main(
        ["--json"],
        client_factory=client_with(LLMResponse(text="Hi!")),
    )

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert code == EXIT_OK
    assert json.loads(lines[0])["answer"] == "Hi!"


def test_runtime_error_returns_exit_code_one(use_settings, capsys):
    code = main(
        ["Hello"],
        client_factory=client_with(RuntimeError("network down")),
    )

    captured = capsys.readouterr()
    assert code == EXIT_RUNTIME
    assert captured.out == MODEL_ERROR_ANSWER + "\n"
    assert "Traceback" not in captured.err


def test_broken_data_directory_is_a_configuration_error(
    monkeypatch, settings, tmp_path, capsys
):
    broken = Settings(
        data_dir=tmp_path / "missing",
        model=settings.model,
        api_key=settings.api_key,
        now=settings.now,
    )
    monkeypatch.setattr("citywheels.cli.load_settings", lambda now=None: broken)

    code = main(["Hello"], client_factory=client_with())

    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert "Configuration error" in captured.err
    assert "booking file not found" in captured.err
    assert captured.out == ""


def test_empty_question_is_rejected(use_settings, capsys):
    code = main(["   "], client_factory=client_with())

    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert "empty" in captured.err


def test_now_override_is_passed_to_configuration(use_settings, monkeypatch, capsys):
    seen: dict[str, object] = {}

    def fake_load(now=None):
        seen["now"] = now
        return use_settings

    monkeypatch.setattr("citywheels.cli.load_settings", fake_load)

    main(
        ["Hello", "--now", "2026-01-01T00:00:00+01:00"],
        client_factory=client_with(LLMResponse(text="ok")),
    )

    assert seen["now"] == "2026-01-01T00:00:00+01:00"
    capsys.readouterr()


def test_client_factory_receives_the_settings(use_settings):
    seen: dict[str, object] = {}

    def factory(received):
        seen["settings"] = received
        return FakeLLM([LLMResponse(text="ok")])

    main(["Hello"], client_factory=factory)

    assert seen["settings"] is use_settings
