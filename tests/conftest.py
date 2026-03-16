"""Shared test fixtures."""

import os
import pytest

# Set test environment variables before importing app modules
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key-not-real")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:ABC-TEST-TOKEN")
os.environ.setdefault("OWNER_TELEGRAM_ID", "999999999")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://assistant:testpass@localhost:5432/assistant")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("DB_PASSWORD", "testpass")


@pytest.fixture
def mock_settings(monkeypatch):
    """Provide test settings without needing real credentials."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-TEST-TOKEN")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "999999999")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://assistant:testpass@localhost:5432/assistant")
