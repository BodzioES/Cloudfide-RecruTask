"""Shared fixtures for the test suite."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from citywheels.bookings import BookingStore
from citywheels.config import REFERENCE_NOW, find_data_dir, parse_now


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return find_data_dir()


@pytest.fixture(autouse=True)
def _ignore_local_dotenv(monkeypatch):
    """Tests must not depend on the developer's real .env file."""

    monkeypatch.setattr("citywheels.config.load_dotenv", lambda *args, **kwargs: None)


@pytest.fixture(scope="session")
def store(data_dir: Path) -> BookingStore:
    return BookingStore.from_data_dir(data_dir)


@pytest.fixture
def fixed_now() -> datetime:
    return parse_now(REFERENCE_NOW)
