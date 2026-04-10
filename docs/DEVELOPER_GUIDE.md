# Developer Guide — PersonalAsst

## Overview

PersonalAsst is a single-user, Dockerized, multi-agent Personal Assistant. It communicates via Telegram, uses OpenAI GPT-5.x models, manages Google Workspace, creates its own tools, and self-improves over time.

## Architecture

```
Telegram Bot (aiogram 3.x)
  → Message Router + Sequential per-user queue
    → Orchestrator (Office Organizer, dynamic complexity routing)
      ├── 8 Google Workspace Skills (45 direct tools, zero agent wrappers)
      ├── 2 Internal Skills (Memory: 7 tools, Scheduler: 4 tools)
      ├── Web Search (OpenAI WebSearchTool)
      ├── Tool Factory Agent (Handoff — CLI tool generation)
      ├── Repair Agent (Handoff — read-only diagnostics + repair plans)
      ├── Safety Agent (input injection + context-aware PII guardrails)
      ├── Reflector Agent (background quality scoring)
      └── Curator Agent (weekly self-improvement)
    → Data Layer (PostgreSQL 17, Qdrant, Redis 7 — all Docker)
    → SDK RedisSession (conversation memory, last 20 turns)
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
├── main.py                     # Entry point
├── settings.py                 # Pydantic Settings (from .env)
├── bot/                        # Telegram handlers, voice transcription
├── agents/                     # 14 agent definitions (OpenAI Agents SDK)
│   ├── orchestrator.py         # Office organizer + complexity routing + SDK RedisSession
│   ├── persona_mode.py         # Persona template + runtime datetime injection
│   ├── email_agent.py          # Gmail — 6 direct connected tools
│   ├── calendar_agent.py       # Calendar — 2 direct connected tools
│   ├── tasks_agent.py          # Tasks — 4 direct connected tools
│   ├── drive_agent.py          # Drive — 7 direct connected tools
│   ├── docs_agent.py           # Docs — 7 direct connected tools
│   ├── sheets_agent.py         # Sheets — 6 direct connected tools
│   ├── slides_agent.py         # Slides — 5 direct connected tools
│   ├── contacts_agent.py       # Contacts — 4 direct connected tools
│   ├── memory_agent.py         # Memory — 7 direct bound tools (LTM + STM)
│   ├── scheduler_agent.py      # Scheduler — 4 bound tools (_impl pattern)
│   ├── tool_factory_agent.py   # Dynamic tool creation (Handoff)
│   ├── reflector_agent.py      # Quality scoring (ACE pattern)
│   ├── curator_agent.py        # Weekly self-improvement (ACE)
│   ├── repair_agent.py         # Diagnostic agent (Handoff, read-only)
│   ├── persona_interview_agent.py # 3-session personality profiling interview
│   └── safety_agent.py         # Input/output guardrails
├── skills/                     # Unified skill registry (10 skills)
├── memory/                     # Mem0 (dedup + access tracking), Redis, persona
├── models/                     # Model catalog + complexity-aware routing
├── tools/                      # Tool registry, sandbox, manifest, credential vault
├── scheduler/                  # APScheduler 4.x engine + job callables
├── security/                   # Owner challenge gate (PIN/security Q)
├── integrations/               # Google Workspace MCP client
└── db/                         # SQLAlchemy models + Alembic migrations
config/                         # Persona, safety policies, tool tiers (YAML)
tools/                          # Dynamic tools (Docker volume, hot-reloaded)
├── _example/                   # Example CLI tool template
├── linkedin/                   # LinkedIn function-type tool (11 tools)
└── onedrive/                   # OneDrive function-type tool (7 tools)
tests/                          # 21 test files, 525+ test cases
```

## Key Design Decisions

| Decision | Detail | See PRD |
|----------|--------|---------|
| Single async process | Bot + agents + scheduler in one process | AD-1 |
| Redis for active conversations | 30-min TTL, archival to PostgreSQL | AD-2 |
| Handoff for Tool Factory + Repair | All other skills use direct `function_tool` closures | AD-3 |
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
2. Decide: direct `function_tool` closures (preferred) or `Handoff` (multi-turn only).
3. If using bound tools, extract core logic into `_*_impl` plain async functions. Both `@function_tool` wrappers and bound closures call these. **Never `await` a `@function_tool`-decorated function directly** — `FunctionTool` objects are not callable.
4. Register in `src/agents/orchestrator.py`.
5. Add tests in `tests/test_new_agent.py`.
6. Update `AGENTS.md` navigation table.

## Adding a New CLI Tool

1. Create `tools/my_tool/cli.py` (standalone argparse script).
2. Create `tools/my_tool/tool.py` (`@function_tool` wrapper using subprocess).
3. Create `tools/my_tool/manifest.json` (see PRD §8 for schema).
4. Tool is auto-discovered by the registry's filesystem watcher.

## Adding a New Function-Type Tool

Function-type tools run in-process (not subprocess) and can expose multiple `function_tool` wrappers:

1. Create `tools/my_tool/tool.py` with `_impl` async functions + `@function_tool` wrappers.
2. Export either `tool_function` (single) or `tool_functions` (list) from the module.
3. Create `tools/my_tool/manifest.json` with `"type": "function"` and `"wrapper": "tool.py"`.
4. Declare required credentials in `manifest.json` `credentials` field.
5. Add credential seeding to `src/main.py:seed_tool_credentials()` if needed.
6. Add dependencies to `requirements.txt` and `manifest.json` `dependencies` field.

See `src/tools/plugins/linkedin/` for a complete example with 11 tools and credential vault integration.
The repo also includes `src/tools/plugins/onedrive/` for Microsoft Graph-backed file organization.

## Credential Vault

Dynamic tools that need API keys or passwords use the Redis-backed credential vault (`src/tools/credentials.py`):

- **Storage:** `tool_credentials:{tool_name}` Redis hash
- **Seeding:** `seed_tool_credentials()` in `main.py` reads env vars at startup
- **CLI tools:** Credentials injected as `TOOL_*` env vars via `build_sandbox_env()`
- **Function tools:** Use `get_credentials(tool_name)` directly
- **Security:** Credentials never logged, never returned in tool output

## Repair Flow

- The `RepairAgent` can inspect the repo read-only, run allowlisted diagnostics, and record a pending repair plan.
- Pending repair plans are stored in Redis and require the owner to say `apply patch`.
- Applying a repair triggers the existing security challenge gate before the system runs `git apply` and the recorded validation commands.

## Docker Services

| Service | Port | Purpose |
|---------|------|---------|
| assistant | — | Main application (no exposed port) |
| workspace-mcp | 8081 (internal) | Google Workspace MCP Server |
| postgres | 5432 (internal) | Database + APScheduler job store |
| qdrant | 6333 (internal) | Vector store (Mem0) |
| redis | 6379 (internal) | Cache + conv sessions + SDK agent sessions |
| watchtower | — | Auto-update (optional) |

## Key Patterns

### Bound Tool Pattern (`_impl` functions)
When creating tools with bound user IDs (closures), always:
1. Write core logic in a plain `async def _my_tool_impl(...)` function
2. Create a `@function_tool` wrapper that delegates to it
3. Create bound closures in `_build_bound_*_tools()` that also delegate to `_impl`

This prevents the `FunctionTool object is not callable` error. See `scheduler_agent.py`, `memory_agent.py`.

### Defensive MCP Integration
`call_workspace_tool()` strips `None` values from arguments before sending to the MCP server (which uses `additionalProperties: false`). Always use this function — never call `server.call_tool()` directly.

### SDK Session Management
The orchestrator uses `RedisSession` (`agent_session:{telegram_id}`) for LLM conversation memory. Session history is filtered to exclude `function_call`/`function_call_output` items from previous runs to prevent stale-session 400 errors. Bot handlers include catch-and-retry for `BadRequestError` with automatic session clearing.

### Persona Interview Onboarding (AD-7)
Based on Stanford's "Generative Agent Simulations" research (2024) and Cambridge/DeepMind's psychometric framework (2025). A dedicated `PersonaInterviewAgent` conducts a structured 3-session conversational interview via Telegram:
- **Session 1** — Identity & Context (who you are, work, communication preferences)
- **Session 2** — Work Style & Values (daily routine, decision-making, autonomy preference)
- **Session 3** — Communication & Personality (email voice, humor, boundaries)

After each session, an LLM synthesis step generates OCEAN scores (Big Five personality traits) and a structured profile (communication, work_context, values). The expanded profile is stored in `PersonaVersion.personality` JSONB and injected into the system prompt. The Curator agent periodically re-synthesizes the profile from accumulated Mem0 memories.

See `docs/ADR-2026-03-21-persona-interview-onboarding.md` for full design rationale.
