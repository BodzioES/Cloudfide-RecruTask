"""Command-line entry point (skeleton; wired to the agent in a later stage)."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load_settings


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = load_settings(now=args.now)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    print(f"CityWheels help desk agent {__version__} (skeleton)")
    print(f"Data directory: {settings.data_dir}")
    print(f"Model: {settings.model}")
    print(f"Reference time: {settings.now.isoformat()}")
    print("The agent loop is not wired up yet.")
    return 0
