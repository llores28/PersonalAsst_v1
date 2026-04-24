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
python -m pip install -r requirements-dev.txt
```

## Running Tests

```bash
# Verify test discovery first
python -m pytest tests/ --collect-only

# Smallest relevant test while debugging
python -m pytest tests/test_orchestrator.py -v

# All tests
python -m pytest tests/ -v

# With coverage
python -m pytest tests/ --cov=src --cov-report=term-missing

# Specific module
python -m pytest tests/test_orchestrator.py -v
```

## Key Test Patterns

- Mock OpenAI API calls using `unittest.mock.AsyncMock`
- Use `pytest-asyncio` for all async test functions
- Test DB operations against a test PostgreSQL (via Docker)
- Never hit real OpenAI API in tests
