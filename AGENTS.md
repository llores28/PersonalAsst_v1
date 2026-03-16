# AGENTS.md — PersonalAsst Repository

## Repo Navigation

| Path | Purpose |
|------|---------|
| `src/` | All application source code |
| `src/bot/` | Telegram bot layer (aiogram 3.x) |
| `src/agents/` | Agent definitions (OpenAI Agents SDK) |
| `src/agents/orchestrator.py` | Main triage agent — routes to specialists |
| `src/agents/email_agent.py` | Gmail specialist (as_tool) |
| `src/agents/calendar_agent.py` | Google Calendar specialist (as_tool) |
| `src/agents/drive_agent.py` | Google Drive specialist (as_tool) |
| `src/agents/memory_agent.py` | Memory specialist — recall/store/forget (as_tool) |
| `src/agents/reflector_agent.py` | Post-interaction quality scorer (ACE pattern) |
| `src/agents/scheduler_agent.py` | Scheduling specialist — cron/interval/one-shot (as_tool) |
| `src/agents/tool_factory_agent.py` | Tool Factory — generates CLI tools (Handoff) |
| `src/agents/curator_agent.py` | Weekly self-improvement curator (ACE pattern step 3) |
| `src/agents/safety_agent.py` | Input/output guardrails |
| `src/integrations/workspace_mcp.py` | Google Workspace MCP client config |
| `src/memory/mem0_client.py` | Mem0 wrapper (self-hosted Qdrant + PostgreSQL) |
| `src/memory/conversation.py` | Redis session management (30-min TTL + archival) |
| `src/memory/persona.py` | Persona CRUD with DB versioning + Mem0 preferences |
| `src/tools/` | Tool registry, sandbox, manifest schema |
| `src/memory/` | Mem0 wrapper, Redis sessions, persona CRUD |
| `src/scheduler/` | APScheduler engine + job callables |
| `src/integrations/` | Google Workspace MCP client |
| `src/db/` | SQLAlchemy models + Alembic migrations |
| `config/` | Runtime YAML: persona, safety policies, tool tiers |
| `tools/` | Dynamic CLI tools (Docker volume, hot-reloaded) |
| `tests/` | pytest test suite |
| `PRD_PersonalAssistant.md` | **Build spec** — schemas, decisions, acceptance criteria |
| `RESEARCH_PersonalAssistant.md` | Research context (read-only reference) |

## Command Verification Policy

- **Never invent commands.** Only use commands documented in this file, `docs/DEVELOPER_GUIDE.md`, or verified from `docker-compose.yml` / `Makefile` / `pyproject.toml`.
- Prefer read-only commands first (`docker compose ps`, `pytest --collect-only`).
- Destructive commands require user approval.

## Verified Commands

```bash
# Dev
docker compose up -d              # Start all services
docker compose down               # Stop all services
docker compose build              # Rebuild images
docker compose logs -f assistant  # Tail app logs

# Database
docker compose exec assistant alembic upgrade head    # Apply migrations
docker compose exec assistant alembic downgrade -1    # Rollback last migration

# Test
pytest tests/ -v                  # Run all tests
pytest tests/ --cov=src           # Run with coverage

# Lint
ruff check src/ tests/            # Lint
ruff format src/ tests/           # Format
mypy src/ --strict                # Type check
```

## Safe Command Execution Policy

- Read-only and test commands: safe to auto-run.
- `docker compose up/down/build`: safe to run (local dev only).
- `alembic upgrade`: requires confirmation (mutates DB schema).
- `docker compose exec postgres ...`: requires confirmation (direct DB access).
- File deletion, `pip install`, network requests: always require approval.

## Testing Expectations

- Every new agent, tool, or job callable gets at least one test.
- Mock OpenAI API in tests — never hit real API.
- Use `pytest-asyncio` for async test functions.
- Run full suite before any commit.

## Documentation Update Expectations

- New agents → update this file's navigation table.
- New Telegram commands → update `docs/USER_GUIDE.md`.
- New config options → update `.env.example` and `docs/DEVELOPER_GUIDE.md`.
- Schema changes → create Alembic migration.

## Escalation Behavior

- If uncertain about a destructive action → ask user.
- If a tool call fails → log error, return user-friendly message, offer retry.
- If security concern → flag it, do not proceed silently.
- If a PRD decision seems wrong during implementation → note it, ask user before deviating.
