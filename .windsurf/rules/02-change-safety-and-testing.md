# Change Safety & Testing

## Change Policy

- Prefer minimal, reversible edits — one concern per commit.
- Always read before editing: locate authoritative code paths first.
- Update Pydantic schemas/types first, then producers, then consumers, then tests.
- No broad refactors unless required by a concrete bug or user explicitly approves.

## Testing Expectations

- **Unit tests:** Every new agent, tool, job callable, and guardrail gets at least one test.
- **Integration tests:** Test Telegram handler → orchestrator → specialist agent flow (mocked LLM).
- **Smoke tests:** `docker compose up -d` → bot responds to `/start` within 60 seconds.
- **Tool tests:** Every generated CLI tool must pass its sandbox test before registration.
- Framework: `pytest` with `pytest-asyncio` for async tests.
- Mocking: Use `unittest.mock` for OpenAI API calls in tests (never hit real API in CI).

## Test Commands

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_orchestrator.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=term-missing
```

## Type Checking

```bash
# Type check all source
mypy src/ --strict
```

## Linting & Formatting

```bash
# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/
```

## Pre-Commit Checklist

1. `ruff check` passes
2. `mypy src/` passes (or has only known exclusions)
3. `pytest tests/` passes
4. No new secrets in code (`grep -r "sk-" src/` returns nothing)
5. Docker Compose still builds: `docker compose build`

## Database Migrations

- Use Alembic for all schema changes.
- Never modify PostgreSQL schema directly — always via migration.
- Test migrations: `alembic upgrade head` then `alembic downgrade -1` then `alembic upgrade head`.

## Agent Changes

- When changing agent instructions/tools, update the corresponding test.
- When adding a new specialist agent, add it to the routing table in `src/agents/orchestrator.py`.
- When adding a new tool, create both `cli.py` and `manifest.json` (see PRD §8).
