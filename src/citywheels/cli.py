"""Command-line entry point: one question, or an interactive session."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from typing import Callable

from . import __version__
from .agent import AgentResult, run_agent
from .bookings import BookingDataError
from .config import ConfigError, Settings, load_settings
from .llm import GeminiClient, LLMClient
from .tools import Toolbox

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2

ClientFactory = Callable[[Settings], LLMClient]

# Windows consoles are often not UTF-8, so plain answers are kept ASCII-safe.
# JSON output uses \\u escapes instead and stays exact when parsed.
_DISPLAY_TABLE = str.maketrans(
    {
        "\u20ac": "EUR ",
        "\u2013": "-",
        "\u2014": "-",
        "\u2011": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u00a0": " ",
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="citywheels",
        description="Ask the CityWheels help desk agent a question.",
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="Question to ask. Omit it to start an interactive session.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full result as JSON instead of plain answer text.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print tool calls and debug details to stderr.",
    )
    parser.add_argument(
        "--now",
        metavar="ISO8601",
        help="Override the reference date and time (default: 2026-06-10T09:00:00+02:00).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    client_factory: ClientFactory | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    question = " ".join(args.question).strip()
    if args.question and not question:
        print("Configuration error: the question is empty.", file=sys.stderr)
        return EXIT_USAGE

    _configure_logging(args.verbose)

    try:
        settings = load_settings(now=args.now)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return EXIT_USAGE

    try:
        client = (client_factory or GeminiClient.from_settings)(settings)
        tools = Toolbox.from_data_dir(settings.data_dir, now=settings.now)
    except (ConfigError, BookingDataError, OSError, ValueError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return EXIT_USAGE

    if args.verbose:
        print(
            f"model={settings.model} now={settings.now.isoformat()} "
            f"data={settings.data_dir}",
            file=sys.stderr,
        )

    if question:
        return _ask(question, client, tools, as_json=args.json, verbose=args.verbose)

    return _repl(client, tools, as_json=args.json, verbose=args.verbose)


def _ask(
    question: str,
    client: LLMClient,
    tools: Toolbox,
    *,
    as_json: bool,
    verbose: bool,
    compact: bool = False,
) -> int:
    result = run_agent(question, client=client, tools=tools)
    _emit(result, as_json=as_json, verbose=verbose, compact=compact)
    return EXIT_OK if result.error is None else EXIT_RUNTIME


def _repl(client: LLMClient, tools: Toolbox, *, as_json: bool, verbose: bool) -> int:
    """Read questions until ``exit``, ``quit`` or end of input."""

    exit_code = EXIT_OK
    while True:
        sys.stderr.write("> ")
        sys.stderr.flush()
        line = sys.stdin.readline()
        if line == "":
            sys.stderr.write("\n")
            break

        text = line.strip()
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            break

        code = _ask(
            text,
            client,
            tools,
            as_json=as_json,
            verbose=verbose,
            compact=as_json,
        )
        if code != EXIT_OK:
            exit_code = code

    return exit_code


def _emit(
    result: AgentResult,
    *,
    as_json: bool,
    verbose: bool,
    compact: bool = False,
) -> None:
    if verbose:
        for record in result.tool_calls:
            print(
                f"tool {record.name} "
                f"{json.dumps(record.args, ensure_ascii=True, sort_keys=True)} "
                f"-> {_summarise(record.result)}",
                file=sys.stderr,
            )

    if as_json:
        indent = None if compact else 2
        print(json.dumps(result.to_dict(), ensure_ascii=True, indent=indent))
    else:
        print(_plain(result.answer))


def _plain(text: str) -> str:
    cleaned = text.translate(_DISPLAY_TABLE)
    return re.sub(r"EUR\s+", "EUR ", cleaned)


def _summarise(result: dict[str, object]) -> str:
    if "error" in result:
        return f"error={result['error']}"
    if result.get("found") is False:
        return "not found"
    if "total_refund_eur" in result:
        return f"total_refund_eur={result['total_refund_eur']}"
    if "id" in result:
        return f"booking {result['id']} ({result.get('status', 'unknown')})"
    if "results" in result:
        count = len(result["results"])  # type: ignore[arg-type]
        return f"{count} policy result(s)"
    return "ok"


def _configure_logging(verbose: bool) -> None:
    """Keep stdout for answers; logs go to stderr, and only when asked."""

    logger = logging.getLogger("citywheels")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.CRITICAL)
    logger.propagate = False
