"""Runtime configuration: paths, provider/model selection and the fixed clock.

Everything a deployment needs to switch between Gemini and an
OpenAI-compatible endpoint (OpenAI itself, LM Studio, Ollama, vLLM, Groq,
OpenRouter, ...) is read from environment variables in this one module. No
application code has to change when the provider changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# The assignment fixes "now" so answers stay reproducible.
REFERENCE_NOW = "2026-06-10T09:00:00+02:00"
DEFAULT_PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
TIMEZONE = ZoneInfo("Europe/Warsaw")

PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI = "openai"

DATA_DIR_ENV = "CITYWHEELS_DATA_DIR"
NOW_ENV = "CITYWHEELS_NOW"
PROVIDER_ENV = "LLM_PROVIDER"
MODEL_ENV = "LLM_MODEL"
API_KEY_ENV = "LLM_API_KEY"
BASE_URL_ENV = "LLM_BASE_URL"

# Kept for backwards compatibility with the first Gemini-only releases.
LEGACY_MODEL_ENV = "GEMINI_MODEL"
LEGACY_API_KEY_ENV = "GEMINI_API_KEY"

PROVIDER_ALIASES = {
    "gemini": PROVIDER_GEMINI,
    "google": PROVIDER_GEMINI,
    "google-genai": PROVIDER_GEMINI,
    "openai": PROVIDER_OPENAI,
    "openai-compatible": PROVIDER_OPENAI,
    "compatible": PROVIDER_OPENAI,
    "local": PROVIDER_OPENAI,
}

LOCAL_API_KEY = "not-needed"


class ConfigError(RuntimeError):
    """Raised when the project cannot be configured."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    model: str
    api_key: str | None
    now: datetime
    provider: str = DEFAULT_PROVIDER
    base_url: str | None = None

    def require_api_key(self) -> str:
        """Return the key, or a clear error for the selected provider.

        A local OpenAI-compatible server usually ignores the key, so a
        placeholder is enough when ``LLM_BASE_URL`` is set.
        """

        if self.api_key:
            return self.api_key
        if self.provider == PROVIDER_OPENAI and self.base_url:
            return LOCAL_API_KEY
        if self.provider == PROVIDER_OPENAI:
            raise ConfigError(
                f"{API_KEY_ENV} is not set. Add the provider key to .env, or set "
                f"{BASE_URL_ENV} for a local OpenAI-compatible server."
            )
        raise ConfigError(
            f"{LEGACY_API_KEY_ENV} is not set. Copy .env.example to .env and add "
            "your Google AI Studio API key."
        )


def normalize_provider(value: str | None) -> str:
    """Map friendly names (``google``, ``local``, ...) to a concrete provider."""

    candidate = (value or DEFAULT_PROVIDER).strip().lower()
    provider = PROVIDER_ALIASES.get(candidate)
    if provider is None:
        allowed = ", ".join(sorted(PROVIDER_ALIASES))
        raise ConfigError(
            f"Unknown {PROVIDER_ENV} value {value!r}. Allowed values: {allowed}."
        )
    return provider


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
    provider = normalize_provider(os.getenv(PROVIDER_ENV) or DEFAULT_PROVIDER)

    model = os.getenv(MODEL_ENV) or os.getenv(LEGACY_MODEL_ENV)
    if not model:
        if provider == PROVIDER_OPENAI:
            raise ConfigError(
                f"{MODEL_ENV} must be set when {PROVIDER_ENV}={provider}. "
                "For example: LLM_MODEL=qwen3-8b."
            )
        model = DEFAULT_MODEL

    base_url = os.getenv(BASE_URL_ENV) or None
    if base_url and provider != PROVIDER_OPENAI:
        raise ConfigError(
            f"{BASE_URL_ENV} is only supported for {PROVIDER_ENV}=openai; "
            f"the current provider is {provider!r}."
        )

    return Settings(
        data_dir=find_data_dir(),
        model=model,
        api_key=os.getenv(API_KEY_ENV) or os.getenv(LEGACY_API_KEY_ENV) or None,
        now=parse_now(raw_now),
        provider=provider,
        base_url=base_url,
    )
