# Developer Guide — PersonalAsst

## Overview

PersonalAsst is a single-user, Dockerized, multi-agent Personal Assistant. It communicates via Telegram, uses OpenAI GPT-5.x models, manages Google Workspace, creates its own tools, and self-improves over time.

## Architecture

```
Telegram Bot (aiogram 3.x)
  → Message Router
    → Orchestrator Agent (OpenAI Agents SDK, GPT-5.4-mine)
      → Specialist Agents (Email, Calendar, Drive, Scheduler, Memory, Tool Factory)
        → Tool Layer (CLI-first → function_tool → MCP fallback)
          → Data Layer (PostgreSQL, Qdrant, Redis — all Docker)
```

**Single async process** runs everything: bot, agents, scheduler, tool watcher.

## Prerequisites

- **Docker Desktop** (with Docker Compose v2)
- **Python 3.12+** (for local dev/testing)
- **Telegram account** + bot token from @BotFather
- **OpenAI API key** with GPT-5.x access
- **Google Cloud project** with OAuth credentials (for Phase 2+)

## Quick Start

```bash
# 1. Clone and configure
git clone <repo-url>
cd PersonalAsst
cp .env.example .env
# Edit .env with your API keys

# 2. Build and start
docker compose build
docker compose up -d

# 3. Apply database migrations
docker compose exec assistant alembic upgrade head

# 4. Test — send /start to your Telegram bot
```

## Project Structure

```
src/
├── main.py                 # Entry point
├── settings.py             # Pydantic Settings (from .env)
├── bot/                    # Telegram handlers, router, formatters
├── agents/                 # OpenAI Agents SDK agent definitions
├── tools/                  # Tool registry, sandbox, manifest schema
├── memory/                 # Mem0 client, Redis sessions, persona CRUD
├── scheduler/              # APScheduler engine + job callables
├── integrations/           # Google Workspace MCP client
└── db/                     # SQLAlchemy models + Alembic migrations
config/                     # Runtime YAML configs
tools/                      # Dynamic CLI tools (Docker volume)
tests/                      # pytest suite
```

## Key Design Decisions

| Decision | Detail | See PRD |
|----------|--------|---------|
| Single async process | Bot + agents + scheduler in one process | AD-1 |
| Redis for active conversations | 30-min TTL, archival to PostgreSQL | AD-2 |
| Handoff only for Tool Factory | All other agents use `as_tool()` | AD-3 |
| Filesystem watcher for tool hot-reload | New tools discovered without restart | AD-4 |
| Tell user on error, don't retry silently | User stays in control | AD-5 |
| Sequential per-user message queue | Prevents race conditions | AD-6 |

## Environment Variables

See `.env.example` for the complete list. Required:
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `OWNER_TELEGRAM_ID`
- `DB_PASSWORD`

## Database

PostgreSQL 17 with 7 tables. Schema defined in `src/db/models.py`, managed by Alembic.

```bash
# Apply migrations
docker compose exec assistant alembic upgrade head

# Create new migration
docker compose exec assistant alembic revision --autogenerate -m "description"

# Rollback
docker compose exec assistant alembic downgrade -1
```

## Testing

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=src --cov-report=term-missing

# Lint + type check
ruff check src/ tests/
mypy src/ --strict
```

## Adding a New Agent

1. Create `src/agents/new_agent.py` with `Agent` definition.
2. Decide: `as_tool()` (bounded subtask) or `Handoff` (multi-turn conversation).
3. Register in `src/agents/orchestrator.py`.
4. Add tests in `tests/test_new_agent.py`.
5. Update `AGENTS.md` navigation table.

## Adding a New CLI Tool

1. Create `tools/my_tool/cli.py` (standalone argparse script).
2. Create `tools/my_tool/tool.py` (`@function_tool` wrapper using subprocess).
3. Create `tools/my_tool/manifest.json` (see PRD §8 for schema).
4. Tool is auto-discovered by the registry's filesystem watcher.

## Docker Services

| Service | Port | Purpose |
|---------|------|---------|
| assistant | — | Main application (no exposed port) |
| workspace-mcp | 8081 (internal) | Google Workspace MCP Server |
| postgres | 5432 (internal) | Database |
| qdrant | 6333 (internal) | Vector store |
| redis | 6379 (internal) | Cache + sessions |
| watchtower | — | Auto-update (optional) |
