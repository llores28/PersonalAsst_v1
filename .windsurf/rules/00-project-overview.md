---
trigger: always_on
---
# Project Overview — PersonalAsst

## What This Is

A single-user, Dockerized, self-improving multi-agent Personal Assistant.

- **Primary UX:** Telegram (aiogram 3.x)
- **LLM:** OpenAI GPT-5.x (Responses API, NOT Assistants API)
- **Agent Framework:** OpenAI Agents SDK (Python)
- **Infra:** Docker Compose — PostgreSQL 17, Redis 7, Qdrant (all self-hosted, no SaaS DB)
- **Google Workspace:** Via MCP Server in sidecar container
- **Tool Creation:** CLI-first → function_tool → MCP (fallback only)
- **Memory:** Mem0 open-source (self-hosted) + Qdrant + PostgreSQL

## Authoritative Documents

- `RESEARCH_PersonalAssistant.md` — deep research (read for context, not implementation)
- `PRD_PersonalAssistant.md` — **the build spec** (schemas, decisions, acceptance criteria)
- `docs/PRD.md` — generated cohesive PRD (if exists)

## Definition of Done (per feature)

1. Code follows existing patterns in `src/`.
2. Pydantic models for all data contracts.
3. At least one test per new endpoint/agent/tool.
4. No secrets in code or logs.
5. Works inside Docker Compose (`docker compose up -d`).
6. Telegram UX tested for non-technical user clarity.

## Hard Constraints

- **HC-1:** All databases self-hosted in Docker — no SaaS DB/memory API calls.
- **HC-2:** CLI-first tool creation — MCP only as fallback.
- **HC-3:** OpenAI as sole LLM provider (GPT-5.x models).
- **HC-4:** Responses API only — no Assistants API (deprecated Aug 2026).
- **HC-5:** Single-user system — no multi-tenancy.
- **HC-6:** Telegram primary UX — WhatsApp/Discord optional future.
- **HC-7:** Python 3.12+ only.
- **HC-8:** Non-technical user must never need CLI access.

## Key Directories

```
src/                    # Application source code
src/bot/                # Telegram bot layer (aiogram)
src/agents/             # Agent definitions (OpenAI Agents SDK)
src/tools/              # Tool registry, sandbox, manifest validation
src/memory/             # Mem0 wrapper, conversation state, persona
src/scheduler/          # APScheduler engine + job callables
src/integrations/       # Google Workspace MCP client
src/db/                 # SQLAlchemy models, Alembic migrations
config/                 # Runtime YAML configs (persona, safety, tiers)
tools/                  # Dynamic tool storage (Docker volume, hot-reloaded)
tests/                  # pytest test suite
```
