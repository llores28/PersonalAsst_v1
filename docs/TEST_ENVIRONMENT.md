# Test Environment — PersonalAsst

## Overview

Tests run locally with `pytest` + `pytest-asyncio`. OpenAI API calls are always mocked.

## Setup

```bash
# Create venv
python -m venv .venv
.venv\Scripts\activate          # Windows
# .venv/bin/activate            # Linux/macOS

# Install deps
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov ruff mypy
```

## Running Tests

```bash
# All tests
pytest tests/ -v

# Specific file
pytest tests/test_orchestrator.py -v

# With coverage
pytest tests/ --cov=src --cov-report=term-missing

# Collect only (verify discovery)
pytest tests/ --collect-only
```

## Test Infrastructure

For integration tests that need databases:

```bash
# Start only data services
docker compose up -d postgres redis qdrant

# Run integration tests
pytest tests/ -v -m integration
```

## Mocking Strategy

- **OpenAI API:** Use `unittest.mock.AsyncMock` to patch `Runner.run()` and `Agent` responses.
- **Telegram Bot:** Mock `aiogram.Bot` methods (`send_message`, `answer`).
- **Google Workspace:** Mock MCP server responses.
- **Redis:** Use `fakeredis` or real Redis container.
- **PostgreSQL:** Use test database in the Docker PostgreSQL container.

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures (mock agents, DB setup)
├── test_orchestrator.py     # Orchestrator routing tests
├── test_tools.py            # Tool registry, manifest validation
├── test_memory.py           # Mem0 integration, Redis sessions
├── test_scheduler.py        # APScheduler job creation/execution
├── test_guardrails.py       # Input/output guardrail tests
├── test_bot.py              # Telegram handler tests
└── test_persona.py          # Persona CRUD, versioning
```

## Quality Gates

```bash
# Full quality check
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/ --strict
pytest tests/ -v --tb=short
```
