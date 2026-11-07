"""Offline tests for the benchmark config, scoring and reports."""

from __future__ import annotations

import json

import pytest

from citywheels.agent import AgentResult, ToolCallRecord
from citywheels.config import Settings
from citywheels.llm import FakeLLM, LLMResponse
from eval.benchmark import (
    BenchmarkError,
    BenchmarkOutcome,
    ModelSpec,
    build_model_settings,
    load_config,
    percentile,
    render_model_report,
    render_summary,
    resolve_api_key,
    run_model,
    summarize,
)
from eval.run_eval import CheckResult, Question, QuestionRun


def spec(**overrides) -> ModelSpec:
    data = {
        "label": "local",
        "provider": "openai",
        "model": "qwen3-8b",
        "base_url": "http://localhost:1234/v1",
    }
    data.update(overrides)
    return ModelSpec(**data)


def question_run(
    *,
    question_id="q",
    passed=True,
    tools=(),
    expected=(),
    duration=1.0,
    error=None,
    blocked=False,
    usage=None,
) -> QuestionRun:
    records = tuple(
        ToolCallRecord(name, {"booking_id": "CW-1002"}, {"found": True})
        for name in tools
    )
    return QuestionRun(
        question=Question(
            id=question_id,
            question="?",
            expected_tools=tuple(expected),
        ),
        result=AgentResult(
            answer="answer",
            tool_calls=records,
            iterations=1,
            error=error,
            usage=usage,
        ),
        checks=CheckResult(passed=passed),
        duration_s=duration,
        blocked=blocked,
    )


def test_load_config_reads_models_and_relative_questions(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "questions": "questions.json",
                "reference_now": "2026-06-10T09:00:00+02:00",
                "runs": 3,
                "models": [
                    {"label": "one", "provider": "gemini", "model": "gemini-x"},
                    {
                        "label": "two",
                        "provider": "openai-compatible",
                        "model": "qwen",
                        "base_url": "http://localhost:1234/v1",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.runs == 3
    assert config.questions_path == (tmp_path / "questions.json").resolve()
    assert [model.provider for model in config.models] == ["gemini", "openai"]


@pytest.mark.parametrize(
    "payload",
    [
        {"models": []},
        {"runs": 0, "models": [{"model": "x"}]},
        {"models": [{"label": "a", "model": "x"}, {"label": "a", "model": "y"}]},
        {"models": [{"model": "x", "unexpected": True}]},
        {"models": [{"label": "a"}]},
    ],
)
def test_invalid_config_is_rejected(tmp_path, payload):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError):
        load_config(path)


def test_api_key_priority_and_local_placeholder():
    assert resolve_api_key(spec(api_key="inline"), {}) == "inline"
    assert (
        resolve_api_key(spec(api_key_env="MY_KEY"), {"MY_KEY": "from-env"})
        == "from-env"
    )
    assert resolve_api_key(spec(), {}) == "not-needed"
    remote = spec(base_url=None)
    assert resolve_api_key(remote, {}) is None
    assert resolve_api_key(remote, {"LLM_API_KEY": "fallback"}) == "fallback"


def test_build_model_settings_keeps_data_and_clock():
    from datetime import datetime
    from pathlib import Path

    base = Settings(
        data_dir=Path("data"),
        model="base",
        api_key=None,
        now=datetime(2026, 6, 10, 9, 0),
    )

    built = build_model_settings(base, spec(), api_key="key")

    assert built.data_dir == base.data_dir
    assert built.now == base.now
    assert built.model == "qwen3-8b"
    assert built.provider == "openai"
    assert built.base_url == "http://localhost:1234/v1"


def test_percentile_uses_nearest_rank():
    assert percentile([], 0.95) == 0.0
    assert percentile([1.0], 0.95) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert percentile(list(range(1, 21)), 0.95) == 19.0


def test_summarize_counts_passes_tools_and_tokens():
    runs = [
        question_run(
            question_id="a",
            passed=True,
            tools=("get_booking", "calculate_refund"),
            expected=("get_booking", "calculate_refund"),
            duration=1.0,
            usage={"prompt_tokens": 10, "completion_tokens": 2},
        ),
        question_run(
            question_id="b",
            passed=False,
            tools=(),
            expected=("search_policies",),
            duration=3.0,
            error="model_error: boom",
            usage={"prompt_tokens": 20, "completion_tokens": 4},
        ),
        question_run(question_id="c", passed=False, blocked=True),
    ]

    stats = summarize(runs)

    assert stats["executed"] == 2
    assert stats["passed"] == 1
    assert stats["failed"] == 1
    assert stats["blocked"] == 1
    assert stats["pass_rate"] == 0.5
    assert stats["tool_accuracy"] == 0.5
    assert stats["mean_latency_s"] == 2.0
    assert stats["completion_tokens"] == 6
    assert stats["tokens_per_s"] == 1.5
    assert stats["errors"] == 1


def test_summary_and_model_reports_are_markdown():
    outcome = BenchmarkOutcome(
        spec=spec(),
        stats=summarize(
            [question_run(tools=("get_booking",), expected=("get_booking",))]
        ),
        details=[
            {
                "run": 1,
                "id": "q",
                "passed": True,
                "blocked": False,
                "duration_s": 1.0,
                "tool_calls": ["get_booking"],
            }
        ],
    )

    report = render_model_report(outcome)
    summary = render_summary([outcome], generated_at="2026-11-07T00:00:00+00:00")

    assert "# Benchmark: local" in report
    assert "100.0%" in report
    assert "| Run | Question | Result |" in report
    assert "# Benchmark summary" in summary
    assert "`local`" in summary
    assert "100.0%" in summary


def test_skipped_model_is_reported():
    outcome = BenchmarkOutcome(spec=spec(api_key_env="MISSING_KEY"), skipped="no key")

    report = render_model_report(outcome)
    summary = render_summary([outcome], generated_at="now")

    assert "**Skipped:** no key" in report
    assert "skipped: no key" in summary


def test_run_model_executes_every_run_and_aggregates(
    monkeypatch, data_dir, fixed_now
):
    base = Settings(
        data_dir=data_dir,
        model="base",
        api_key="key",
        now=fixed_now,
    )
    questions = [
        Question(id="q1", question="?", must_match=("ok",)),
    ]
    client = FakeLLM(
        [
            LLMResponse(text="ok"),
            LLMResponse(text="ok"),
        ]
    )
    monkeypatch.setattr("eval.benchmark.create_client", lambda *args, **kwargs: client)

    outcome = run_model(spec(), base, questions, runs=2, delay=0, sleep=lambda _: None)

    assert outcome.skipped is None
    assert outcome.stats is not None
    assert outcome.stats["runs_completed"] == 2
    assert outcome.stats["executed"] == 2
    assert outcome.stats["passed"] == 2
    assert outcome.stats["pass_rate"] == 1.0
    assert len(outcome.details) == 2


def test_run_model_stops_after_a_blocked_quota(
    monkeypatch, data_dir, fixed_now
):
    from citywheels.llm import QuotaExhausted

    base = Settings(
        data_dir=data_dir,
        model="base",
        api_key="key",
        now=fixed_now,
    )
    questions = [
        Question(id="q1", question="?", must_match=("ok",)),
        Question(id="q2", question="?", must_match=("ok",)),
    ]
    client = FakeLLM([QuotaExhausted("daily", retry_delay_s=3600.0)])
    monkeypatch.setattr("eval.benchmark.create_client", lambda *args, **kwargs: client)

    outcome = run_model(spec(), base, questions, runs=3, delay=0)

    assert outcome.stats is not None
    assert outcome.stats["runs_completed"] == 1
    assert outcome.stats["blocked"] == 1
    assert outcome.stats["blocked_by_quota"] is True
    assert len(outcome.details) == 2


def test_run_model_skips_an_unreachable_endpoint(
    monkeypatch, data_dir, fixed_now
):
    base = Settings(
        data_dir=data_dir,
        model="base",
        api_key="key",
        now=fixed_now,
    )
    questions = [
        Question(id="q1", question="?", must_match=("ok",)),
        Question(id="q2", question="?", must_match=("ok",)),
    ]
    client = FakeLLM(
        [ConnectionError("refused"), ConnectionError("refused")]
    )
    monkeypatch.setattr("eval.benchmark.create_client", lambda *args, **kwargs: client)

    outcome = run_model(spec(), base, questions, runs=3, delay=0)

    assert outcome.skipped is not None
    assert "cannot reach" in outcome.skipped
    assert outcome.stats is None
