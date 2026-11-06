"""Runtime configuration: paths, model selection and the fixed reference clock."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# The assignment fixes "now" so answers stay reproducible.
REFERENCE_NOW = "2026-06-10T09:00:00+02:00"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
TIMEZONE = ZoneInfo("Europe/Warsaw")

DATA_DIR_ENV = "CITYWHEELS_DATA_DIR"
NOW_ENV = "CITYWHEELS_NOW"
MODEL_ENV = "GEMINI_MODEL"
API_KEY_ENV = "GEMINI_API_KEY"


class ConfigError(RuntimeError):
    """Raised when the project cannot be configured."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    model: str
    api_key: str | None
    now: datetime

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                f"{API_KEY_ENV} is not set. Copy .env.example to .env and add your "
                "Google AI Studio API key."
            )
        return self.api_key


def find_data_dir(start: Path | None = None) -> Path:
    """Locate the directory that holds bookings.json and policies/.

    Resolution order: CITYWHEELS_DATA_DIR, then walking up from this file and
    from the current working directory. This keeps tests and CI independent of
    where the process was started.
    """

    override = os.getenv(DATA_DIR_ENV)
    if override:
        path = Path(override).expanduser().resolve()
        _require_data_dir(path)
        return path

    origins = [start or Path(__file__), Path.cwd()]
    seen: set[Path] = set()
    for origin in origins:
        origin = origin.resolve()
        for base in (origin, *origin.parents):
            if base in seen:
                continue
            seen.add(base)
            candidate = base / "data"
            if (candidate / "bookings.json").is_file():
                return candidate

    raise ConfigError(
        "Could not find the data directory (data/bookings.json). Set "
        f"{DATA_DIR_ENV} to the absolute path of the data folder."
    )


def _require_data_dir(path: Path) -> None:
    if not (path / "bookings.json").is_file():
        raise ConfigError(
            f"{DATA_DIR_ENV} points to {path}, but bookings.json was not found there."
        )


def parse_now(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and make it timezone-aware."""

    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"Invalid datetime {value!r}: {exc}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TIMEZONE)
    return moment


def load_settings(now: str | None = None) -> Settings:
    """Build settings from the environment (and an optional CLI override)."""

    load_dotenv()
    raw_now = now or os.getenv(NOW_ENV) or REFERENCE_NOW
    model = os.getenv(MODEL_ENV) or DEFAULT_MODEL
    return Settings(
        data_dir=find_data_dir(),
        model=model,
        api_key=os.getenv(API_KEY_ENV) or None,
        now=parse_now(raw_now),
    )
