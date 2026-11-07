"""Run the same evaluation questions against several models and compare them.

The benchmark is intentionally boring: same questions, same reference time,
same prompt and the same local scoring as ``run_eval.py``. The only thing that
changes is the provider/model pair from the JSON config. Results are written as
one Markdown report and one raw JSON file per model, plus a summary table.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from citywheels.config import (
    ConfigError,
    Settings,
    load_settings,
    normalize_provider,
)
from citywheels.llm import create_client
from citywheels.tools import Toolbox

try:  # the benchmark lives next to run_eval.py, not in the installed package
    from eval.run_eval import (
        DEFAULT_QUESTIONS,
        EvalDataError,
        Question,
        QuestionRun,
        load_questions,
        run_all,
        select_questions,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from run_eval import (  # type: ignore[no-redef]
        DEFAULT_QUESTIONS,
        EvalDataError,
        Question,
        QuestionRun,
        load_questions,
        run_all,
        select_questions,
    )

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = EVAL_DIR / "benchmark_models.json"
DEFAULT_OUTPUT_DIR = EVAL_DIR / "benchmarks"

ALLOWED_MODEL_KEYS = {
    "label",
    "provider",
    "model",
    "base_url",
    "api_key",
    "api_key_env",
    "note",
}


class BenchmarkError(ValueError):
    """The benchmark configuration is invalid."""


@dataclass(frozen=True)
class ModelSpec:
    label: str
    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    note: str = ""


@dataclass(frozen=True)
class BenchmarkConfig:
    questions_path: Path
    runs: int
    reference_now: str | None
    models: tuple[ModelSpec, ...]


@dataclass
class BenchmarkOutcome:
    spec: ModelSpec
    stats: dict[str, object] | None = None
    details: list[dict[str, object]] = field(default_factory=list)
    skipped: str | None = None


def load_config(path: Path | str = DEFAULT_CONFIG) -> BenchmarkConfig:
    """Read and validate the benchmark model list."""

    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise BenchmarkError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise BenchmarkError(f"invalid JSON in {path}: {error}") from error

    if isinstance(raw, list):
        models_raw = raw
        runs = 1
        reference_now = None
        questions_path = DEFAULT_QUESTIONS
    elif isinstance(raw, dict):
        models_raw = raw.get("models")
        runs = raw.get("runs", 1)
        reference_now = raw.get("reference_now")
        raw_questions = raw.get("questions")
        if raw_questions is not None and not isinstance(raw_questions, str):
            raise BenchmarkError("'questions' must be a path string")
        questions_path = Path(raw_questions or DEFAULT_QUESTIONS)
        if not questions_path.is_absolute():
            questions_path = (path.parent / questions_path).resolve()
    else:
        raise BenchmarkError(f"{path} must contain a JSON object or array")

    if not isinstance(models_raw, list) or not models_raw:
        raise BenchmarkError(f"{path} must contain a non-empty 'models' list")
    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
        raise BenchmarkError("'runs' must be a positive integer")
    if reference_now is not None and not isinstance(reference_now, str):
        raise BenchmarkError("'reference_now' must be an ISO-8601 string")

    models: list[ModelSpec] = []
    labels: set[str] = set()
    for index, item in enumerate(models_raw):
        if not isinstance(item, dict):
            raise BenchmarkError(f"model #{index + 1} must be a JSON object")
        unknown = sorted(set(item) - ALLOWED_MODEL_KEYS)
        if unknown:
            raise BenchmarkError(
                f"model #{index + 1} has unknown key(s): {', '.join(unknown)}"
            )

        model = item.get("model")
        if not isinstance(model, str) or not model.strip():
            raise BenchmarkError(f"model #{index + 1} has no model name")
        model = model.strip()
        label = item.get("label") or model
        if not isinstance(label, str) or not label.strip():
            raise BenchmarkError(f"model #{index + 1} has an invalid label")
        label = label.strip()
        if label in labels:
            raise BenchmarkError(f"duplicate model label: {label}")
        labels.add(label)

        try:
            provider = normalize_provider(item.get("provider"))
        except ConfigError as error:
            raise BenchmarkError(str(error)) from error

        base_url = item.get("base_url")
        if base_url is not None and not isinstance(base_url, str):
            raise BenchmarkError(f"{label}: base_url must be a string")

        api_key = item.get("api_key")
        api_key_env = item.get("api_key_env")
        if api_key is not None and not isinstance(api_key, str):
            raise BenchmarkError(f"{label}: api_key must be a string")
        if api_key_env is not None and not isinstance(api_key_env, str):
            raise BenchmarkError(f"{label}: api_key_env must be a string")

        note = item.get("note", "")
        if not isinstance(note, str):
            raise BenchmarkError(f"{label}: note must be a string")

        models.append(
            ModelSpec(
                label=label,
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                api_key_env=api_key_env,
                note=note,
            )
        )

    return BenchmarkConfig(
        questions_path=questions_path,
        runs=runs,
        reference_now=reference_now,
        models=tuple(models),
    )


def resolve_api_key(
    spec: ModelSpec, environ: Mapping[str, str] | None = None
) -> str | None:
    """Find a key without ever writing one into the committed config."""

    environ = os.environ if environ is None else environ
    if spec.api_key:
        return spec.api_key
    if spec.api_key_env:
        return environ.get(spec.api_key_env) or None
    if spec.provider == "openai" and spec.base_url:
        return "not-needed"
    return (
        environ.get("LLM_API_KEY")
        or environ.get("GEMINI_API_KEY")
        or None
    )


def build_model_settings(
    base: Settings, spec: ModelSpec, *, api_key: str | None
) -> Settings:
    return Settings(
        data_dir=base.data_dir,
        model=spec.model,
        api_key=api_key,
        now=base.now,
        provider=spec.provider,
        base_url=spec.base_url,
    )


def _token_totals(runs: Sequence[QuestionRun]) -> tuple[int, int]:
    completion = 0
    prompt = 0
    for run in runs:
        usage = run.result.usage or {}
        value = usage.get("completion_tokens")
        if isinstance(value, int) and not isinstance(value, bool):
            completion += value
        value = usage.get("prompt_tokens")
        if isinstance(value, int) and not isinstance(value, bool):
            prompt += value
    return completion, prompt


def percentile(values: Sequence[float], fraction: float) -> float:
    """Simple nearest-rank percentile; enough for a small benchmark."""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def summarize(runs: Sequence[QuestionRun]) -> dict[str, object]:
    """Aggregate a batch of question runs into comparable numbers."""

    executed = [run for run in runs if not run.blocked]
    passed = sum(1 for run in executed if run.checks.passed)
    failed = len(executed) - passed
    tool_correct = sum(
        1
        for run in executed
        if set(run.question.expected_tools) <= set(run.result.used_tools)
    )
    latencies = [run.duration_s for run in executed]
    completion_tokens, prompt_tokens = _token_totals(executed)
    total_time = sum(latencies)
    return {
        "questions": len({run.question.id for run in runs}),
        "executed": len(executed),
        "passed": passed,
        "failed": failed,
        "blocked": len(runs) - len(executed),
        "pass_rate": passed / len(executed) if executed else 0.0,
        "tool_accuracy": tool_correct / len(executed) if executed else 0.0,
        "mean_latency_s": total_time / len(executed) if executed else 0.0,
        "p95_latency_s": percentile(latencies, 0.95),
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "tokens_per_s": (
            completion_tokens / total_time
            if completion_tokens and total_time > 0
            else None
        ),
        "errors": sum(1 for run in executed if run.result.error),
    }


def _connection_failure(result: AgentResult) -> bool:
    """True when the provider could not be reached at all."""

    if not result.error:
        return False
    return any(
        name in result.error
        for name in (
            "APIConnectionError",
            "APITimeoutError",
            "ConnectionError",
            "TimeoutError",
        )
    )


def run_model(
    spec: ModelSpec,
    base_settings: Settings,
    questions: Sequence[Question],
    *,
    runs: int,
    delay: float = 1.0,
    sleep: Callable[[float], None] | None = None,
    progress: Callable[[str, int, int, QuestionRun], None] | None = None,
) -> BenchmarkOutcome:
    """Run every question ``runs`` times against one model."""

    api_key = resolve_api_key(spec)
    if api_key is None:
        return BenchmarkOutcome(
            spec=spec,
            skipped=f"missing API key (set {spec.api_key_env or 'LLM_API_KEY'})",
        )

    settings = build_model_settings(base_settings, spec, api_key=api_key)
    client = create_client(
        settings,
        timeout_ms=120_000,
        max_attempts=5,
        base_delay=2.0,
    )
    tools = Toolbox.from_data_dir(settings.data_dir, now=settings.now)

    outcomes: list[QuestionRun] = []
    details: list[dict[str, object]] = []
    completed_runs = 0

    kwargs = {"delay": delay}
    if sleep is not None:
        kwargs["sleep"] = sleep

    for run_index in range(1, runs + 1):
        def show(run: QuestionRun, index: int, total: int) -> None:
            if progress is not None:
                progress(spec.label, run_index, index, run)

        batch = run_all(questions, client, tools, progress=show, **kwargs)
        for run in batch:
            details.append(
                {
                    "run": run_index,
                    **run.to_dict(),
                    "tool_details": [
                        record.to_dict() for record in run.result.tool_calls
                    ],
                }
            )
        outcomes.extend(batch)
        completed_runs += 1
        if any(run.blocked for run in batch):
            # The daily quota is gone; another run would only repeat the error.
            break
        if batch and _connection_failure(batch[0].result):
            # Retrying every question against a dead endpoint wastes minutes.
            return BenchmarkOutcome(
                spec=spec,
                skipped=f"cannot reach the endpoint: {batch[0].result.error}",
            )

    stats = summarize(outcomes)
    stats["runs_requested"] = runs
    stats["runs_completed"] = completed_runs
    stats["blocked_by_quota"] = any(run.blocked for run in outcomes)
    return BenchmarkOutcome(spec=spec, stats=stats, details=details)


def _label_path(label: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_." else "-"
        for character in label
    )


def render_model_report(outcome: BenchmarkOutcome) -> str:
    spec = outcome.spec
    lines = [f"# Benchmark: {spec.label}", ""]
    lines += [
        f"- **Provider:** `{spec.provider}`",
        f"- **Model:** `{spec.model}`",
    ]
    if spec.base_url:
        lines.append(f"- **Base URL:** `{spec.base_url}`")
    if spec.note:
        lines.append(f"- **Note:** {spec.note}")

    if outcome.skipped:
        lines += ["", f"**Skipped:** {outcome.skipped}", ""]
        return "\n".join(lines).rstrip() + "\n"

    stats = outcome.stats or {}
    runs = stats.get("runs_completed", 0)
    requested = stats.get("runs_requested", 0)
    lines += [
        f"- **Runs:** {runs}/{requested} completed",
        f"- **Questions per run:** {stats.get('questions', 0)}",
        f"- **Pass rate:** {stats.get('passed', 0)}/"
        f"{stats.get('executed', 0)} ({_percent(stats.get('pass_rate'))})",
        f"- **Tool accuracy:** {_percent(stats.get('tool_accuracy'))}",
        f"- **Mean latency:** {_seconds(stats.get('mean_latency_s'))}",
        f"- **p95 latency:** {_seconds(stats.get('p95_latency_s'))}",
        f"- **Completion tokens:** {stats.get('completion_tokens', 0)}",
        f"- **Tokens/s:** {_rate(stats.get('tokens_per_s'))}",
        f"- **Errors:** {stats.get('errors', 0)}",
        f"- **Blocked by quota:** {stats.get('blocked', 0)}",
        "",
        "## Per-run summary",
        "",
        "| Run | Passed | Failed | Blocked | Mean latency |",
        "| --- | --- | --- | --- | --- |",
    ]

    by_run: dict[int, list[dict[str, object]]] = {}
    for detail in outcome.details:
        run_index = int(detail.get("run", 0))
        by_run.setdefault(run_index, []).append(detail)

    for run_index in sorted(by_run):
        entries = by_run[run_index]
        blocked = sum(1 for entry in entries if entry.get("blocked"))
        passed = sum(
            1
            for entry in entries
            if entry.get("passed") and not entry.get("blocked")
        )
        times = [
            float(entry.get("duration_s", 0.0))
            for entry in entries
            if not entry.get("blocked")
        ]
        mean = sum(times) / len(times) if times else 0.0
        lines.append(
            f"| {run_index} | {passed} | {len(entries) - blocked - passed} "
            f"| {blocked} | {mean:.1f}s |"
        )

    lines += [
        "",
        "## Per-question results",
        "",
        "| Run | Question | Result | Tools | Time |",
        "| --- | --- | --- | --- | --- |",
    ]
    for detail in outcome.details:
        status = "BLOCKED" if detail.get("blocked") else (
            "PASS" if detail.get("passed") else "FAIL"
        )
        tools = ", ".join(
            f"`{tool}`" for tool in detail.get("tool_calls", [])  # type: ignore[union-attr]
        ) or "-"
        lines.append(
            f"| {detail.get('run')} | `{detail.get('id')}` | {status} "
            f"| {tools} | {float(detail.get('duration_s', 0.0)):.1f}s |"
        )

    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_summary(outcomes: Sequence[BenchmarkOutcome], *, generated_at: str) -> str:
    lines = [
        "# Benchmark summary",
        "",
        f"- **Generated:** `{generated_at}`",
        "- **Scoring:** the same offline checks as `eval/run_eval.py`.",
        "",
        "| Model | Provider | Runs | Pass rate | Tool accuracy | Mean | p95 "
        "| Tokens/s | Blocked | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for outcome in outcomes:
        spec = outcome.spec
        if outcome.skipped:
            lines.append(
                f"| `{spec.label}` | `{spec.provider}` | - | - | - | - | - "
                f"| - | - | skipped: {outcome.skipped} |"
            )
            continue
        stats = outcome.stats or {}
        notes = spec.note.replace("|", "/") if spec.note else "-"
        lines.append(
            f"| `{spec.label}` | `{spec.provider}` "
            f"| {stats.get('runs_completed')}/{stats.get('runs_requested')} "
            f"| {stats.get('passed')}/{stats.get('executed')} "
            f"({_percent(stats.get('pass_rate'))}) "
            f"| {_percent(stats.get('tool_accuracy'))} "
            f"| {_seconds(stats.get('mean_latency_s'))} "
            f"| {_seconds(stats.get('p95_latency_s'))} "
            f"| {_rate(stats.get('tokens_per_s'))} "
            f"| {stats.get('blocked', 0)} | {notes} |"
        )

    lines += [
        "",
        "Raw answers and tool calls are in the per-model JSON files.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _percent(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value * 100:.1f}%"


def _seconds(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.2f}s"


def _rate(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.1f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark several models with the CityWheels evaluation."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--runs", type=int, help="Override the config run count.")
    parser.add_argument("--limit", type=int, help="Run only the first N questions.")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ID",
        help="Run only these ids (repeatable or comma-separated).",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="LABEL",
        help="Benchmark only these model labels (repeatable).",
    )
    parser.add_argument("--now", metavar="ISO8601", help="Override the reference time.")
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between questions (default: 1.0).",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        questions_path = args.questions or config.questions_path
        questions = select_questions(
            load_questions(questions_path),
            only=args.only,
            limit=args.limit,
        )
        base = load_settings(now=args.now or config.reference_now)
    except (BenchmarkError, EvalDataError, ConfigError, OSError, ValueError) as error:
        print(f"Benchmark error: {error}", file=sys.stderr)
        return 2

    selected = set(args.model)
    models = [
        spec for spec in config.models if not selected or spec.label in selected
    ]
    if selected:
        unknown = sorted(selected - {spec.label for spec in config.models})
        if unknown:
            print(
                f"Benchmark error: unknown model label(s): {', '.join(unknown)}",
                file=sys.stderr,
            )
            return 2

    runs = args.runs or config.runs
    if runs < 1:
        print("Benchmark error: --runs must be at least 1", file=sys.stderr)
        return 2

    def show(label: str, run_index: int, index: int, run: QuestionRun) -> None:
        status = "BLOCKED" if run.blocked else (
            "PASS" if run.checks.passed else "FAIL"
        )
        print(
            f"[{label} run {run_index}] [{index}/{len(questions)}] "
            f"{run.question.id}: {status} ({run.duration_s:.1f}s)",
            file=sys.stderr,
        )

    outcomes: list[BenchmarkOutcome] = []
    for spec in models:
        print(f"Benchmarking {spec.label} ({spec.provider}/{spec.model})...", file=sys.stderr)
        try:
            outcome = run_model(
                spec,
                base,
                questions,
                runs=runs,
                delay=max(0.0, args.delay),
                progress=show,
            )
        except (ConfigError, OSError, ValueError) as error:
            outcome = BenchmarkOutcome(spec=spec, skipped=str(error))
        outcomes.append(outcome)
        if outcome.skipped:
            print(f"  skipped: {outcome.skipped}", file=sys.stderr)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for outcome in outcomes:
        stem = _label_path(outcome.spec.label)
        (args.output_dir / f"{stem}.md").write_text(
            render_model_report(outcome), encoding="utf-8"
        )
        (args.output_dir / f"{stem}.json").write_text(
            json.dumps(
                {
                    "label": outcome.spec.label,
                    "provider": outcome.spec.provider,
                    "model": outcome.spec.model,
                    "base_url": outcome.spec.base_url,
                    "reference_now": base.now.isoformat(),
                    "generated_at": generated_at,
                    "skipped": outcome.skipped,
                    "stats": outcome.stats,
                    "results": outcome.details,
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

    summary = render_summary(outcomes, generated_at=generated_at)
    (args.output_dir / "summary.md").write_text(summary, encoding="utf-8")

    executed = [
        outcome
        for outcome in outcomes
        if outcome.stats is not None
    ]
    blocked = any(
        bool((outcome.stats or {}).get("blocked_by_quota")) for outcome in executed
    )
    failed = any(
        (outcome.stats or {}).get("failed", 0) for outcome in executed
    )
    skipped = sum(1 for outcome in outcomes if outcome.skipped)

    print(
        f"Wrote {len(outcomes)} report(s) to {args.output_dir} "
        f"({skipped} skipped)."
    )
    if any(outcome.skipped for outcome in outcomes) and not executed:
        return 2
    if blocked:
        return 3
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
