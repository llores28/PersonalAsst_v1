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
├── conftest.py                                  # Shared fixtures (env vars, mock setup)
├── test_action_policy.py                        # Action policy classification + confirmation cues
├── test_clarification.py                        # Needs-input clarification contract
├── test_google_audit.py                         # Google Workspace audit harness
├── test_google_integration.py                   # Google API integration (requires Docker)
├── test_google_tools_qa.py                      # Google tools Q&A validation
├── test_google_tools_validation.py              # Gmail/Calendar/Tasks/Drive tool schema tests
├── test_memory.py                               # Mem0 integration, Redis sessions
├── test_model_router.py                         # Model selection + complexity routing
├── test_orchestrator.py                         # Orchestrator routing, session filtering, email flows
├── test_phase6.py                               # Phase 6 features (reflector, curator, repair)
├── test_reflector_agent.py                      # Quality scoring + trend tracking
├── test_repair_agent.py                         # Repair agent creation + read-only contract
├── test_safety_agent.py                         # Input/output guardrails + context-aware PII
├── test_scheduler.py                            # APScheduler engine, bound tools, DateTrigger
├── test_security_challenge.py                   # PIN/security question challenge gate
├── test_skill_registry.py                       # Unified skill registry
├── test_temporal.py                             # Temporal parser + domain routing
├── test_tools.py                                # Tool registry, manifest validation, sandbox
├── test_workspace.py                            # MCP client, None-stripping, tool params
└── test_workspace_mcp_oauth_responses_override.py # OAuth response override tests
```

**Current count:** 493+ passing, 30 pre-existing SDK-absent failures (these tests import `agents` SDK which is only available inside Docker — they pass in the container).


## Quality Gates

```bash
# Full quality check
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/ --strict
pytest tests/ -v --tb=short
```
