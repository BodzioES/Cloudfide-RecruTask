"""Offline tests for the evaluation data and scoring logic."""

from __future__ import annotations

import json

import pytest

from citywheels.agent import AgentResult, ToolCallRecord
from citywheels.llm import FakeLLM, LLMResponse, QuotaExhausted
from citywheels.policies import PolicyIndex
from citywheels.tools import Toolbox
from eval.run_eval import (
    CheckResult,
    EvalDataError,
    Question,
    QuestionRun,
    evaluate_answer,
    load_questions,
    render_report,
    run_all,
    select_questions,
)

REQUIRED_IDS = {
    "hours-saturday",
    "price-ebike-day",
    "age-14-ebike",
    "child-seat-price",
    "deposit-city",
    "bike-abroad",
    "sell-bikes",
    "refund-1002",
    "refund-1001",
    "refund-1003",
    "refund-1007",
    "refund-1010",
    "refund-1004-voucher",
    "refusal-australia",
    "refusal-netflix",
    "unknown-booking",
}


@pytest.fixture(scope="module")
def questions():
    return load_questions()


def agent_result(answer, tools=(), error=None):
    records = tuple(
        ToolCallRecord(name, {"booking_id": "CW-1002"}, {"found": True})
        for name in tools
    )
    return AgentResult(
        answer=answer,
        tool_calls=records,
        iterations=1,
        error=error,
    )


def test_question_file_is_valid(questions):
    assert len(questions) >= 10
    assert len({question.id for question in questions}) == len(questions)


def test_question_file_covers_the_planned_cases(questions):
    assert REQUIRED_IDS <= {question.id for question in questions}


def test_question_mix_meets_the_assignment_requirements(questions):
    refunds = [
        question
        for question in questions
        if "calculate_refund" in question.expected_tools
    ]
    refusals = [question for question in questions if question.expect_refusal]

    assert len(refunds) >= 2
    assert len(refusals) >= 2
    assert any(question.id == "unknown-booking" for question in questions)


def test_every_question_has_at_least_one_assertion(questions):
    for question in questions:
        assert (
            question.expected_tools
            or question.must_match
            or question.expect_refusal
        ), question.id


def test_expected_tools_are_known(questions):
    known = {"search_policies", "get_booking", "calculate_refund"}
    for question in questions:
        assert set(question.expected_tools) <= known, question.id


def test_only_and_limit_filter_the_question_list(questions):
    only = select_questions(questions, only=["refund-1002"])
    assert [question.id for question in only] == ["refund-1002"]

    comma = select_questions(questions, only=["refund-1001,refund-1002"])
    assert {question.id for question in comma} == {"refund-1001", "refund-1002"}

    limited = select_questions(questions, limit=3)
    assert len(limited) == 3


@pytest.mark.parametrize("kwargs", [{"only": ["does-not-exist"]}, {"limit": 0}])
def test_bad_selection_is_rejected(questions, kwargs):
    with pytest.raises(EvalDataError):
        select_questions(questions, **kwargs)


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "questions.json"
    item = {"id": "same", "question": "Q?"}
    path.write_text(json.dumps([item, item]), encoding="utf-8")

    with pytest.raises(EvalDataError, match="duplicate"):
        load_questions(path)


def test_invalid_regex_is_rejected(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps([{"id": "bad", "question": "Q?", "must_match": ["("]}]),
        encoding="utf-8",
    )

    with pytest.raises(EvalDataError, match="invalid regular expression"):
        load_questions(path)


def test_checker_passes_when_everything_matches():
    question = Question(
        id="q",
        question="How much?",
        expected_tools=("calculate_refund",),
        must_match=("340", "50\\s*%"),
    )

    check = evaluate_answer(question, agent_result("340 EUR, 50% refund", ("calculate_refund",)))

    assert check.passed is True
    assert check.reasons == ()


def test_checker_reports_a_missing_tool():
    question = Question(id="q", question="?", expected_tools=("calculate_refund",))

    check = evaluate_answer(question, agent_result("340 EUR"))

    assert check.passed is False
    assert "missing tool" in check.reasons[0]


def test_checker_reports_a_missing_pattern():
    question = Question(id="q", question="?", must_match=("340",))

    check = evaluate_answer(question, agent_result("I do not know"))

    assert check.passed is False
    assert "missing required pattern" in check.reasons[0]


def test_checker_reports_a_forbidden_pattern():
    question = Question(id="q", question="?", must_not_match=("Canberra",))

    check = evaluate_answer(question, agent_result("The capital is Canberra"))

    assert check.passed is False
    assert "forbidden pattern" in check.reasons[0]


def test_refusal_requires_staff_contact_details():
    question = Question(id="q", question="?", expect_refusal=True)

    without_contact = evaluate_answer(question, agent_result("No, I cannot help."))
    with_contact = evaluate_answer(
        question,
        agent_result("I cannot help. Contact help@citywheels.example."),
    )

    assert without_contact.passed is False
    assert "contact details" in without_contact.reasons[0]
    assert with_contact.passed is True


def test_agent_error_always_fails_the_question():
    question = Question(id="q", question="?", must_match=("ok",))

    check = evaluate_answer(
        question,
        agent_result("ok", error="model_error: TimeoutError"),
    )

    assert check.passed is False
    assert "agent error" in check.reasons[0]


def test_run_all_keeps_going_after_a_failing_question(store, data_dir, fixed_now):
    toolbox = Toolbox(
        store=store,
        index=PolicyIndex.from_data_dir(data_dir),
        now=fixed_now,
    )
    questions = [
        Question(id="fails", question="?", expected_tools=("search_policies",)),
        Question(id="passes", question="?", must_match=("40",)),
    ]
    client = FakeLLM(
        [
            LLMResponse(text="nothing useful"),
            LLMResponse(text="It costs 40 EUR per day."),
        ]
    )

    runs = run_all(questions, client, toolbox, delay=0)

    assert [run.checks.passed for run in runs] == [False, True]
    assert runs[0].checks.reasons


def test_run_all_contains_a_model_exception(store, data_dir, fixed_now):
    toolbox = Toolbox(
        store=store,
        index=PolicyIndex.from_data_dir(data_dir),
        now=fixed_now,
    )
    question = Question(id="broken", question="?", must_match=("anything",))
    client = FakeLLM([RuntimeError("boom")])

    runs = run_all([question], client, toolbox, delay=0)

    assert len(runs) == 1
    assert runs[0].result.error is not None
    assert runs[0].result.error.startswith("model_error")
    assert runs[0].checks.passed is False


def test_run_all_stops_and_marks_the_rest_blocked_on_quota(store, data_dir, fixed_now):
    toolbox = Toolbox(
        store=store,
        index=PolicyIndex.from_data_dir(data_dir),
        now=fixed_now,
    )
    questions = [
        Question(id="first", question="?", must_match=("anything",)),
        Question(id="second", question="?", must_match=("anything",)),
        Question(id="third", question="?", must_match=("anything",)),
    ]
    client = FakeLLM([QuotaExhausted("daily quota", retry_delay_s=3600.0)])

    runs = run_all(questions, client, toolbox, delay=0)

    assert len(runs) == 3
    assert runs[0].blocked is False
    assert runs[0].result.error.startswith("quota_error")
    assert runs[0].checks.passed is False
    assert [run.blocked for run in runs[1:]] == [True, True]
    assert "not run" in runs[1].checks.reasons[0]
    assert client.remaining == 0


def test_report_marks_questions_blocked_by_quota():
    run = QuestionRun(
        question=Question(id="skipped", question="Q?", must_match=("x",)),
        result=AgentResult(answer="", iterations=0, error="quota_error: X"),
        checks=CheckResult(
            passed=False, reasons=("not run: the model quota is exhausted",)
        ),
        duration_s=0.0,
        blocked=True,
    )

    report = render_report(
        [run],
        model="gemini-test",
        reference_now="2026-06-10T09:00:00+02:00",
        generated_at="2026-11-06T00:00:00+00:00",
    )

    assert "`skipped` - BLOCKED" in report
    assert "0/0 executed passed" in report
    assert "1 blocked by the API quota" in report


def test_report_contains_summary_reasons_and_answers():
    passing = QuestionRun(
        question=Question(id="good", question="Q good?", must_match=("40",)),
        result=agent_result("It costs 40 EUR.", ("search_policies",)),
        checks=CheckResult(passed=True),
        duration_s=1.25,
    )
    failing = QuestionRun(
        question=Question(id="bad", question="Q bad?", must_match=("50",)),
        result=agent_result("I do not know."),
        checks=CheckResult(passed=False, reasons=("missing required pattern: 50",)),
        duration_s=2.5,
    )

    report = render_report(
        [passing, failing],
        model="gemini-test",
        reference_now="2026-06-10T09:00:00+02:00",
        generated_at="2026-11-06T00:00:00+00:00",
    )

    assert "1/2 passed" in report
    assert "`good` - PASS" in report
    assert "`bad` - FAIL" in report
    assert "missing required pattern: 50" in report
    assert "> It costs 40 EUR." in report
    assert "`search_policies`" in report
