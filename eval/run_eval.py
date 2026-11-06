"""Evaluate the agent against a fixed question set and write ``results.md``.

The runner talks to the real Gemini API, but all checking is local and
deterministic. Each question declares which tools must run and which patterns
must (or must not) appear in the answer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from citywheels.agent import AgentResult, run_agent
from citywheels.config import ConfigError, Settings, load_settings
from citywheels.llm import GeminiClient, LLMClient
from citywheels.tools import Toolbox

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_QUESTIONS = EVAL_DIR / "questions.json"
DEFAULT_RESULTS = EVAL_DIR / "results.md"

KNOWN_TOOLS = frozenset({"search_policies", "get_booking", "calculate_refund"})
CONTACT_RE = re.compile(
    r"help@citywheels\.example|\+48[\s-]*000[\s-]*000[\s-]*000",
    re.IGNORECASE,
)

ALLOWED_KEYS = {
    "id",
    "question",
    "expected_tools",
    "must_match",
    "must_not_match",
    "expect_refusal",
}


class EvalDataError(ValueError):
    """The question file or the selection is invalid."""


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    expected_tools: tuple[str, ...] = ()
    must_match: tuple[str, ...] = ()
    must_not_match: tuple[str, ...] = ()
    expect_refusal: bool = False


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestionRun:
    question: Question
    result: AgentResult
    checks: CheckResult
    duration_s: float
    blocked: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.question.id,
            "passed": self.checks.passed,
            "blocked": self.blocked,
            "reasons": list(self.checks.reasons),
            "duration_s": round(self.duration_s, 3),
            "iterations": self.result.iterations,
            "tool_calls": [record.name for record in self.result.tool_calls],
            "answer": self.result.answer,
            "error": self.result.error,
        }


def load_questions(path: Path | str = DEFAULT_QUESTIONS) -> list[Question]:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise EvalDataError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise EvalDataError(f"invalid JSON in {path}: {error}") from error

    if not isinstance(raw, list) or not raw:
        raise EvalDataError(f"{path} must contain a non-empty JSON array")

    questions: list[Question] = []
    seen: set[str] = set()

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvalDataError(f"question #{index + 1} must be a JSON object")

        unknown = sorted(set(item) - ALLOWED_KEYS)
        if unknown:
            raise EvalDataError(
                f"question #{index + 1} has unknown key(s): {', '.join(unknown)}"
            )

        question_id = item.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise EvalDataError(f"question #{index + 1} has no id")
        question_id = question_id.strip()
        if question_id in seen:
            raise EvalDataError(f"duplicate question id: {question_id}")
        seen.add(question_id)

        text = item.get("question")
        if not isinstance(text, str) or not text.strip():
            raise EvalDataError(f"{question_id}: question text is missing")

        expected_tools = _string_list(item, "expected_tools", question_id)
        unknown_tools = sorted(set(expected_tools) - KNOWN_TOOLS)
        if unknown_tools:
            raise EvalDataError(
                f"{question_id}: unknown tool(s) in expected_tools: "
                f"{', '.join(unknown_tools)}"
            )

        must_match = _string_list(item, "must_match", question_id)
        must_not_match = _string_list(item, "must_not_match", question_id)
        for pattern in (*must_match, *must_not_match):
            try:
                re.compile(pattern)
            except re.error as error:
                raise EvalDataError(
                    f"{question_id}: invalid regular expression {pattern!r}: {error}"
                ) from error

        expect_refusal = item.get("expect_refusal", False)
        if not isinstance(expect_refusal, bool):
            raise EvalDataError(f"{question_id}: expect_refusal must be true or false")

        questions.append(
            Question(
                id=question_id,
                question=text.strip(),
                expected_tools=expected_tools,
                must_match=must_match,
                must_not_match=must_not_match,
                expect_refusal=expect_refusal,
            )
        )

    return questions


def _string_list(item: dict[str, object], key: str, question_id: str) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(entry, str) or not entry for entry in value
    ):
        raise EvalDataError(f"{question_id}: {key} must be a list of non-empty strings")
    return tuple(value)


def select_questions(
    questions: Sequence[Question],
    *,
    only: Sequence[str] = (),
    limit: int | None = None,
) -> list[Question]:
    selected = list(questions)

    wanted = {
        part.strip()
        for value in only
        for part in value.split(",")
        if part.strip()
    }
    if wanted:
        known = {question.id for question in selected}
        unknown = sorted(wanted - known)
        if unknown:
            raise EvalDataError(f"unknown question id(s): {', '.join(unknown)}")
        selected = [question for question in selected if question.id in wanted]

    if limit is not None:
        if limit < 1:
            raise EvalDataError("--limit must be at least 1")
        selected = selected[:limit]

    if not selected:
        raise EvalDataError("no questions selected")
    return selected


def evaluate_answer(question: Question, result: AgentResult) -> CheckResult:
    reasons: list[str] = []

    if result.error:
        reasons.append(f"agent error: {result.error}")

    used_tools = set(result.used_tools)
    missing_tools = [
        tool for tool in question.expected_tools if tool not in used_tools
    ]
    if missing_tools:
        reasons.append(f"missing tool(s): {', '.join(missing_tools)}")

    for pattern in question.must_match:
        if re.search(pattern, result.answer) is None:
            reasons.append(f"missing required pattern: {pattern}")

    for pattern in question.must_not_match:
        if re.search(pattern, result.answer) is not None:
            reasons.append(f"forbidden pattern matched: {pattern}")

    if question.expect_refusal and CONTACT_RE.search(result.answer) is None:
        reasons.append("refusal did not include the staff contact details")

    return CheckResult(passed=not reasons, reasons=tuple(reasons))


def run_one(question: Question, client: LLMClient, tools: Toolbox) -> QuestionRun:
    started = time.perf_counter()
    try:
        result = run_agent(question.question, client=client, tools=tools)
    except Exception as error:  # noqa: BLE001 - one question must not stop the run
        result = AgentResult(
            answer="",
            iterations=0,
            error=f"harness_error: {type(error).__name__}: {error}",
        )
    duration = time.perf_counter() - started
    return QuestionRun(
        question=question,
        result=result,
        checks=evaluate_answer(question, result),
        duration_s=duration,
    )


def run_all(
    questions: Sequence[Question],
    client: LLMClient,
    tools: Toolbox,
    *,
    delay: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[QuestionRun, int, int], None] | None = None,
) -> list[QuestionRun]:
    runs: list[QuestionRun] = []
    total = len(questions)
    for index, question in enumerate(questions, start=1):
        run = run_one(question, client, tools)
        runs.append(run)
        if progress is not None:
            progress(run, index, total)
        if _quota_blocked(run.result):
            # The daily quota is gone: running the rest would only produce more
            # errors, so mark them as blocked instead of pretending they failed.
            reason = run.result.error or "quota exhausted"
            for skipped in questions[index:]:
                runs.append(
                    QuestionRun(
                        question=skipped,
                        result=AgentResult(
                            answer="", iterations=0, error=reason
                        ),
                        checks=CheckResult(
                            passed=False,
                            reasons=("not run: the model quota is exhausted",),
                        ),
                        duration_s=0.0,
                        blocked=True,
                    )
                )
            break
        if delay > 0 and index < total:
            sleep(delay)
    return runs


def _quota_blocked(result: AgentResult) -> bool:
    return bool(result.error) and result.error.startswith("quota_error")


def _status(run: QuestionRun) -> str:
    if run.blocked:
        return "BLOCKED"
    return "PASS" if run.checks.passed else "FAIL"


def render_report(
    runs: Sequence[QuestionRun],
    *,
    model: str,
    reference_now: str,
    generated_at: str,
) -> str:
    executed = [run for run in runs if not run.blocked]
    blocked = len(runs) - len(executed)
    passed = sum(1 for run in executed if run.checks.passed)
    if blocked:
        score = (
            f"**{passed}/{len(executed)} executed passed** "
            f"({blocked} blocked by the API quota)"
        )
    else:
        score = f"**{passed}/{len(runs)} passed**"
    lines: list[str] = [
        "# Evaluation results",
        "",
        f"- **Model:** `{model}`",
        f"- **Reference time:** `{reference_now}`",
        f"- **Generated:** `{generated_at}`",
        f"- **Score:** {score}",
        "",
        "| # | ID | Result | Tools | Time |",
        "| --- | --- | --- | --- | --- |",
    ]

    for index, run in enumerate(runs, start=1):
        status = _status(run)
        tools = ", ".join(f"`{record.name}`" for record in run.result.tool_calls) or "-"
        lines.append(
            f"| {index} | `{run.question.id}` | {status} | {tools} "
            f"| {run.duration_s:.1f}s |"
        )

    lines += ["", "## Details", ""]

    for run in runs:
        status = _status(run)
        lines += [
            f"### `{run.question.id}` - {status}",
            "",
            f"**Question:** {run.question.question}",
            "",
        ]

        if run.question.expected_tools:
            lines.append(
                "**Expected tools:** "
                + ", ".join(f"`{tool}`" for tool in run.question.expected_tools)
            )
        if run.question.must_match:
            lines.append(
                "**Must match:** "
                + ", ".join(f"`{pattern}`" for pattern in run.question.must_match)
            )
        if run.question.must_not_match:
            lines.append(
                "**Must not match:** "
                + ", ".join(f"`{pattern}`" for pattern in run.question.must_not_match)
            )

        lines += ["", "**Reasons:**"]
        if run.checks.reasons:
            lines += [f"- {reason}" for reason in run.checks.reasons]
        else:
            lines.append("- none")

        lines += [
            "",
            f"**Iterations:** {run.result.iterations}",
            "",
            "**Tool calls:**",
        ]
        if run.result.tool_calls:
            for record in run.result.tool_calls:
                args = json.dumps(record.args, ensure_ascii=True, sort_keys=True)
                result = json.dumps(record.result, ensure_ascii=True, sort_keys=True)
                if len(result) > 300:
                    result = result[:297] + "..."
                lines.append(f"- `{record.name}` args=`{args}` result=`{result}`")
        else:
            lines.append("- none")

        answer = run.result.answer or "(no answer)"
        lines += ["", "**Answer:**", "", "> " + answer.replace("\n", "\n> "), ""]

    return "\n".join(lines).rstrip() + "\n"


def build_client(settings: Settings) -> GeminiClient:
    """Evaluation retries more patiently than a single interactive question."""

    return GeminiClient(
        api_key=settings.require_api_key(),
        model=settings.model,
        timeout_ms=90_000,
        max_attempts=5,
        base_delay=2.0,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CityWheels evaluation.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--limit", type=int, help="Run only the first N questions.")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ID",
        help="Run only these ids (repeatable or comma-separated).",
    )
    parser.add_argument("--model", help="Override GEMINI_MODEL for this run.")
    parser.add_argument("--now", metavar="ISO8601", help="Override the reference time.")
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between questions (default: 1.0).",
    )
    args = parser.parse_args(argv)

    try:
        questions = select_questions(
            load_questions(args.questions),
            only=args.only,
            limit=args.limit,
        )
        settings = load_settings(now=args.now)
        if args.model:
            settings = replace(settings, model=args.model)
        client = build_client(settings)
        tools = Toolbox.from_data_dir(settings.data_dir, now=settings.now)
    except (EvalDataError, ConfigError, OSError, ValueError) as error:
        print(f"Evaluation error: {error}", file=sys.stderr)
        return 2

    def show(run: QuestionRun, index: int, total: int) -> None:
        status = "PASS" if run.checks.passed else "FAIL"
        print(
            f"[{index}/{total}] {run.question.id}: {status} ({run.duration_s:.1f}s)",
            file=sys.stderr,
        )

    runs = run_all(
        questions,
        client,
        tools,
        delay=max(0.0, args.delay),
        progress=show,
    )

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = render_report(
        runs,
        model=settings.model,
        reference_now=settings.now.isoformat(),
        generated_at=generated_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")

    passed = sum(1 for run in runs if run.checks.passed and not run.blocked)
    blocked = sum(1 for run in runs if run.blocked)
    executed = len(runs) - blocked
    if blocked:
        print(
            f"{passed}/{executed} executed passed; {blocked} blocked by the "
            f"API quota; wrote {args.output}"
        )
        return 3
    print(f"{passed}/{len(runs)} passed; wrote {args.output}")
    return 0 if passed == len(runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
