---
name: setup-test-environment
description: Set up and run the test suite for PersonalAsst
---

# Setup Test Environment

## Prerequisites
- Python 3.12+ with venv
- Docker Compose stack running (for integration tests)

## Local Test Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov ruff mypy
```

## Running Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=src --cov-report=term-missing

# Specific module
pytest tests/test_orchestrator.py -v

# Collect only (verify test discovery)
pytest tests/ --collect-only
```

## Key Test Patterns

- Mock OpenAI API calls using `unittest.mock.AsyncMock`
- Use `pytest-asyncio` for all async test functions
- Test DB operations against a test PostgreSQL (via Docker)
- Never hit real OpenAI API in tests
