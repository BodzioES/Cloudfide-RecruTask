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
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = load_settings()
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        settings.require_api_key()
