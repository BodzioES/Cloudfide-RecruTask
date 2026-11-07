"""Tests for configuration, data-path resolution and the reference clock."""

from __future__ import annotations

from datetime import datetime

import pytest

from citywheels.config import (
    DEFAULT_MODEL,
    REFERENCE_NOW,
    ConfigError,
    find_data_dir,
    load_settings,
    parse_now,
)


def test_data_dir_contains_expected_files(data_dir):
    assert (data_dir / "bookings.json").is_file()
    assert (data_dir / "policies" / "prices.md").is_file()
    assert (data_dir / "policies" / "cancellations.md").is_file()
    assert (data_dir / "policies" / "faq.md").is_file()


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CITYWHEELS_DATA_DIR", str(tmp_path))
    with pytest.raises(ConfigError):
        find_data_dir()


def test_parse_now_keeps_timezone():
    moment = parse_now(REFERENCE_NOW)
    assert moment.tzinfo is not None
    assert moment.utcoffset().total_seconds() == 2 * 3600
    assert (moment.year, moment.month, moment.day) == (2026, 6, 10)
    assert (moment.hour, moment.minute) == (9, 0)


def test_parse_now_rejects_garbage():
    with pytest.raises(ConfigError):
        parse_now("not-a-date")


def test_parse_now_assumes_warsaw_for_naive_input():
    moment = parse_now("2026-06-10T09:00:00")
    assert moment.tzinfo is not None


def test_load_settings_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("CITYWHEELS_NOW", raising=False)
    settings = load_settings()
    assert settings.model == DEFAULT_MODEL
    assert isinstance(settings.now, datetime)
    assert settings.data_dir.is_dir()


def test_load_settings_cli_override_wins(monkeypatch):
    monkeypatch.setenv("CITYWHEELS_NOW", "2026-01-01T00:00:00+01:00")
    settings = load_settings(now="2026-06-10T09:00:00+02:00")
    assert settings.now.isoformat() == REFERENCE_NOW


def test_require_api_key_has_helpful_error(monkeypatch):
    for name in ("LLM_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        settings.require_api_key()


def test_provider_defaults_to_gemini(monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    settings = load_settings()

    assert settings.provider == "gemini"
    assert settings.model == DEFAULT_MODEL
    assert settings.base_url is None


def test_openai_compatible_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_MODEL", "qwen3-8b")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("LLM_API_KEY", "lm-studio")

    settings = load_settings()

    assert settings.provider == "openai"
    assert settings.model == "qwen3-8b"
    assert settings.base_url == "http://localhost:1234/v1"
    assert settings.api_key == "lm-studio"


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "telepathy")

    with pytest.raises(ConfigError, match="Unknown LLM_PROVIDER"):
        load_settings()


def test_openai_provider_requires_a_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    with pytest.raises(ConfigError, match="LLM_MODEL"):
        load_settings()


def test_base_url_is_rejected_for_gemini(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/v1")

    with pytest.raises(ConfigError, match="only supported"):
        load_settings()


def test_legacy_gemini_variable_names_still_work(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-legacy")
    monkeypatch.setenv("GEMINI_API_KEY", "legacy-key")

    settings = load_settings()

    assert settings.model == "gemini-legacy"
    assert settings.api_key == "legacy-key"


def test_local_openai_endpoint_does_not_need_a_real_key(monkeypatch):
    for name in ("LLM_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()
    local = type(settings)(
        data_dir=settings.data_dir,
        model="qwen3-8b",
        api_key=None,
        now=settings.now,
        provider="openai",
        base_url="http://localhost:1234/v1",
    )

    assert local.require_api_key() == "not-needed"


def test_remote_openai_endpoint_requires_a_key(monkeypatch):
    for name in ("LLM_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()
    remote = type(settings)(
        data_dir=settings.data_dir,
        model="gpt-4.1-mini",
        api_key=None,
        now=settings.now,
        provider="openai",
    )

    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        remote.require_api_key()
