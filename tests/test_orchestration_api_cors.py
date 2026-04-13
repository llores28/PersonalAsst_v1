"""Tests for dashboard API CORS origin parsing."""

import pytest

pytest.importorskip("fastapi", reason="fastapi is not installed locally")


def test_parse_allowed_origins_deduplicates_and_trims() -> None:
    from src.orchestration.api import _parse_allowed_origins

    parsed = _parse_allowed_origins(
        " http://localhost:3001,https://app.example.com,http://localhost:3001 "
    )

    assert parsed == ["http://localhost:3001", "https://app.example.com"]


def test_parse_allowed_origins_drops_wildcard() -> None:
    from src.orchestration.api import _parse_allowed_origins

    parsed = _parse_allowed_origins("*,https://app.example.com")

    assert parsed == ["https://app.example.com"]


def test_parse_allowed_origins_falls_back_when_empty() -> None:
    from src.orchestration.api import _parse_allowed_origins

    parsed = _parse_allowed_origins(" , ")

    assert parsed == ["http://localhost:3001", "http://127.0.0.1:3001"]
