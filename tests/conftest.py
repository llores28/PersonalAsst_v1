"""Shared test fixtures."""

import os

# Set test environment variables before importing app modules
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key-not-real")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:ABC-TEST-TOKEN")
os.environ.setdefault("OWNER_TELEGRAM_ID", "999999999")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://assistant:testpass@localhost:5432/assistant")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("DB_PASSWORD", "testpass")

# Eagerly import the real ``agents`` SDK (if installed) BEFORE any test
# module loads. Several test files install a MagicMock stub via
# ``if "agents" not in sys.modules`` to keep their tests fast; without this
# pre-load, the file pytest collects first wins, and the SDK-required tests
# (e.g., test_repair_agent.py) silently regress when alphabetical ordering
# changes — exactly the test-ordering trap that bit Wave 2.4 and Wave 2.7.
# Best-effort: if the SDK isn't installed in this venv, individual test
# files fall back to their stubs.
try:  # pragma: no cover — import-time guard
    import agents  # noqa: F401
    import agents.mcp  # noqa: F401
    import agents.exceptions  # noqa: F401
except ImportError:
    pass
