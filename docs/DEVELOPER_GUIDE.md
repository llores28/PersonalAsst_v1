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
      ├── Parallel Runner (asyncio.gather fan-out — M1)
      ├── Background Job runner (autonomous monitor jobs — M2)
      ├── Tool Factory Agent (Handoff — CLI tool generation)
      ├── Repair Agent (Handoff — risk classify, auto-apply, smoke test — M4)
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

### Startup migration behavior

- `run_migrations()` is gated by `STARTUP_MIGRATIONS_ENABLED`.
- Default is disabled (`false`) for safer startup behavior.
- Recommended: run migrations explicitly in ops flow (`alembic upgrade head`).

## Project Structure

All deployment-relevant files live under `src/`. Only bootstrap/project-root files live at the repo root.

```
src/
├── main.py                     # Entry point
├── settings.py                 # Pydantic Settings (from .env)
├── alembic.ini                 # Alembic config (script_location = src/db/migrations)
├── alembic/                    # Alembic migration env + versions (src/db/migrations)
├── config/                     # Persona, safety policies, tool tiers (YAML)
├── user_skills/                # User-created skills (SKILL.md, hot-reloaded via volume mount)
├── orchestration-ui/           # React Dashboard UI (src/orchestration-ui/src/Dashboard.js)
├── bot/                        # Telegram handlers, voice transcription
├── agents/                     # 19 agent / runner files (OpenAI Agents SDK)
│   ├── orchestrator.py         # Office organizer + complexity routing + SDK RedisSession
│   ├── persona_mode.py         # Persona template + runtime datetime injection
│   ├── routing_hardened.py     # TaskDomain/Intent enums + detect_parallel_domains() [M1]
│   ├── parallel_runner.py      # asyncio.gather fan-out, max 3 branches, budget guard [M1]
│   ├── background_job.py       # BackgroundJob lifecycle, tick loop, APScheduler, notify [M2]
│   ├── skill_factory_agent.py  # AI-guided SKILL.md creation wizard
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
│   ├── repair_agent.py         # Risk classify + auto-apply + Telegram notify on low-risk apply [M4+]
│   ├── persona_interview_agent.py # 3-session personality profiling interview
│   └── safety_agent.py         # Input/output guardrails
├── skills/                     # Unified skill registry (10 skills)
├── repair/                     # engine.py, verifier.py, notifications.py [M4+]
├── memory/                     # Mem0 (dedup + access tracking), Redis, persona
├── models/                     # Model catalog, complexity-aware routing, cost_tracker.py
├── tools/                      # Tool registry, sandbox, manifest, credential vault
│   └── plugins/                # Dynamic tools (Docker volume, hot-reloaded)
│       ├── linkedin/           # LinkedIn function-type tool (11 tools)
│       └── onedrive/           # OneDrive function-type tool (7 tools)
├── scheduler/                  # APScheduler 4.x engine + job callables
├── security/                   # Owner challenge gate (PIN/security Q)
├── integrations/               # Google Workspace MCP client
├── orchestration/              # FastAPI Dashboard API + system_agents registry
│   ├── api.py                  # All /api/* endpoints (orgs, agents, skills, traces, jobs, repairs, layout, activity, wizard)
│   └── system_agents.py        # Built-in system agent registry (SystemAgentInfo)
└── db/                         # SQLAlchemy models + Alembic migrations
tests/                          # 23+ test files, 841+ test cases
# Root (bootstrap/project only):
docker-compose.yml  Dockerfile  Dockerfile.orchestration
.env  .env.example  requirements*.txt  pytest.ini  README.md
```

## Key Design Decisions

| Decision | Detail | See PRD |
|----------|--------|---------|
| Single async process | Bot + agents + scheduler in one process | AD-1 |
| Redis for active conversations | 30-min TTL, archival to PostgreSQL | AD-2 |
| Handoff for Tool Factory + Repair | All other skills use direct `function_tool` closures | AD-3 |
| Filesystem watcher for tool hot-reload | New tools discovered without restart | AD-4 |
| Tell user on error, don't retry silently | User stays in control | AD-5 |
| Proactive error notification | Every captured tool error triggers Telegram push + Redis store | AD-5+ |
| Max pipeline retries | `_PIPELINE_MAX_ATTEMPTS = 3` prevents runaway self-healing loops | M4+ |
| Sequential per-user message queue | Prevents race conditions | AD-6 |

## Environment Variables

See `.env.example` for the complete list. Required:
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `OWNER_TELEGRAM_ID`
- `DB_PASSWORD`

Important optional safety settings:
- `STARTUP_MIGRATIONS_ENABLED` — enable/disable startup Alembic execution
- `CORS_ALLOWED_ORIGINS` — comma-separated dashboard origins for orchestration API CORS
- `AUTO_REPAIR_LOW_RISK` — `true` (default) auto-applies low-risk operational repair fixes without owner approval
- `REPAIR_NOTIFICATION_EMAIL` — hardcoded in `src/repair/notifications.py` as `lannys.lores@gmail.com`; change there if needed

## Database

PostgreSQL 17 with 9 tables (added `agent_traces` and `background_jobs` in migration 006). Schema defined in `src/db/models.py`, managed by Alembic.

**New tables (migration 006):**
- `agent_traces` — one row per tool-call step per `Runner.run()` (session_key, agent_name, tool_name, tool_args, tool_result_preview, step_index, duration_ms, timestamp)
- `background_jobs` — autonomous job records (goal, status, iterations_run, max_iterations, done_condition, result, created_at)
- Added `risk_level` + `auto_applied` columns on `repair_tickets`

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

1. Create `src/tools/plugins/my_tool/cli.py` (standalone argparse script).
2. Create `src/tools/plugins/my_tool/tool.py` (`@function_tool` wrapper using subprocess).
3. Create `src/tools/plugins/my_tool/manifest.json` (see PRD §8 for schema).
4. Tool is auto-discovered by the registry's filesystem watcher.

### System-Binary Tools (FFmpeg, ImageMagick, etc.)

For tools that need external binaries:
- Set `"requires_system_binary": true` in `manifest.json`
- Use the safe pattern in `cli.py`:
```python
import argparse
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description="...")
    parser.add_argument("--input", required=True)
    args = parser.parse_args()  # --help exits here, before any binary call
    cmd = ["ffmpeg", "-i", args.input, ...]  # build command as a LIST
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    print("Done.")

if __name__ == "__main__":
    main()
```

**Important:** `argparse.parse_args()` must be called before any subprocess call so `--help` works without the binary present.

## Adding a New Function-Type Tool

Function-type tools run in-process (not subprocess) and can expose multiple `function_tool` wrappers:

1. Create `src/tools/plugins/my_tool/tool.py` with `_impl` async functions + `@function_tool` wrappers.
2. Export either `tool_function` (single) or `tool_functions` (list) from the module.
3. Create `src/tools/plugins/my_tool/manifest.json` with `"type": "function"` and `"wrapper": "tool.py"`.
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

## Repair Flow (M4+)

### Error Capture → Notification
- Any tool/skill error is stored in Redis by `store_last_tool_error()` in the orchestrator.
- `_notify_owner_error()` fires immediately as a background task, pushing a Telegram alert with "say 'fix it' to start diagnosis".
- Owner can then say "fix it" or "/repair agent" to enter the full self-healing pipeline.

### Self-Healing Pipeline
1. **DebuggerAgent** — root-cause analysis, produces `DebugAnalysis` with confidence score.
2. **Ticket creation** — `create_structured_ticket()` writes `RepairTicket` to PostgreSQL; email + Telegram notification sent.
3. **ProgrammerAgent** — generates unified diff patch + test plan.
4. **QualityControlAgent** — security scan, patch applicability check, validates test commands.
5. **Sandbox** — `execute_pending_repair()` applies patch on temp git branch, runs verification commands.
6. **Fix-ready notification** — `notify_fix_ready()` sends Telegram inline keyboard: **✅ Apply fix now** / **❌ Skip for now**. Email also sent.
7. **Deploy** — owner taps button or uses `/ticket approve <id>`; `approve_ticket_deploy()` merges branch to main.

### Risk Classification
- `classify_repair_risk(plan)` in `src/repair/engine.py` returns `low | medium | high`.
- **Low-risk** (Redis clears, schedule re-injections, env-var logging): auto-applied immediately. Telegram push sent after apply. Respects `AUTO_REPAIR_LOW_RISK=false` to disable.
- **Medium/High**: stored in Redis as pending plan, require owner approval via security challenge gate.

### Robustness Guards
- `_PIPELINE_MAX_ATTEMPTS = 3` — same error fingerprint blocked after 3 failed pipeline runs to prevent loops.
- Sandbox check uses structured markers tuple, not fragile substring match.
- All notifications are fire-and-forget (`asyncio.create_task`) — never block the main flow.

### Telegram Commands
- `/tickets` — list all open repair tickets with status icons.
- `/ticket approve <id>` — merge verified branch to main (owner-only).
- `/ticket close <id>` — dismiss a ticket without deploying (owner-only).

### Key Files
| File | Role |
|------|------|
| `src/repair/engine.py` | Full pipeline orchestration, ticket CRUD, sandbox execution |
| `src/repair/verifier.py` | Post-apply smoke test + rollback for low-risk fixes |
| `src/repair/models.py` | Pydantic contracts between pipeline stages |
| `src/repair/notifications.py` | Email notifications (ticket created, fix ready) |
| `src/bot/notifications.py` | Telegram push helpers (error alert, ticket, inline keyboard) |
| `src/agents/repair_agent.py` | RepairAgent tools: analyze, propose patch, propose low-risk fix |
| `src/agents/debugger_agent.py` | DebuggerAgent: root-cause analysis with confidence score |
| `src/agents/programmer_agent.py` | ProgrammerAgent: unified diff + test plan |
| `src/agents/quality_control_agent.py` | QA validation: security scan + applicability check |

## Docker Services

| Service | Port | Purpose |
|---------|------|---------|
| assistant | — | Main application: bot, agents, scheduler |
| orchestration-api | 8000 | FastAPI Dashboard API (build context: repo root, `COPY src/`) |
| orchestration-ui | 3001 | React Dashboard UI (build context: `./src/orchestration-ui`) |
| workspace-mcp | 8083 (host-mapped) | Google Workspace MCP Server |
| postgres | 5432 (internal) | Database + APScheduler job store |
| qdrant | 6333 (internal) | Vector store (Mem0) |
| redis | 6379 (internal) | Cache + conv sessions + SDK agent sessions |

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

### Organization ownership and auditability

- Dashboard org endpoints resolve request user from `X-Telegram-Id` (with owner fallback for single-user mode) and enforce owned-org access.
- Telegram `/orgs` and dashboard org flows log lifecycle actions to `OrgActivity`.
- Destructive delete actions also log durable entries to `audit_log` before cascading org deletion.
- **Selective deletion**: `GET /api/orgs/{id}/delete-preview` returns agents, tasks, activity count. `DELETE /api/orgs/{id}` accepts optional `retain_agent_ids`/`retain_task_ids` — retained items are moved to a `__retained__` holding org (hidden from listing) before cascade-delete.

### Unified cost tracking

- `src/models/cost_tracker.py` contains `OPENAI_MODEL_PRICING` (all models in one dict) and `record_llm_cost(model, usage, ...)` which handles pricing lookup, token extraction, DB upsert (daily_costs), Redis tracking (per-provider), and diagnostic logging.
- Call `record_llm_cost()` after every `Runner.run()` — never duplicate pricing logic.
- **OpenRouter path**: `src/integrations/openrouter.py` `_track_openrouter_usage()` now calls `estimate_cost_from_model()` from the same pricing table — both tracking paths are unified.
- **Pricing table structure**: Keys use substring matching (`k in model_id`). More-specific keys must appear first (e.g. `gpt-4o-mini` before `gpt-4o`). OpenRouter models use full `provider/model` namespaced keys (e.g. `google/gemini-2.5-flash`, `anthropic/claude-3.5-sonnet`).
- **Adding a new OpenRouter model**: Add `"provider/model-name": (input_per_1M, output_per_1M)` to the OpenRouter section of `OPENAI_MODEL_PRICING`. If the model name contains a substring of an existing bare key, place it before that key or use a more specific prefix.
- **Flux / image-only models**: Set both rates to `(0.00, 0.00)` — they are billed per image, not per token.

### Dashboard layout persistence

- `GET/PUT /api/dashboard/layout` stores react-grid-layout positions in Redis per user (`dashboard_layout:{telegram_id}`, 1-year TTL).
- Frontend debounces save (1.2s) and sends full `allLayouts` object on change.

## April 13, 2026 — src/ Consolidation

All deployment-relevant directories moved under `src/`. Root now contains only project/bootstrap files.

| Moved From | Moved To |
|---|---|
| `orchestration-ui/` | `src/orchestration-ui/` |
| `user_skills/` | `src/user_skills/` |
| `config/` | `src/config/` |
| `alembic/` + `alembic.ini` | `src/alembic/` + `src/alembic.ini` |

- All `Path("user_skills/...")` references updated to `Path("src/user_skills/...")` across `api.py`, `handlers.py`, `skill_factory_agent.py`, `validation.py`, `loader.py`.
- `alembic.ini` path in `src/main.py` updated to `src/alembic.ini`.
- `docker-compose.yml` build context and volume mounts updated.
- `Dockerfile` / `Dockerfile.orchestration` simplified (no separate `COPY` for `config/`, `alembic/`, `alembic.ini` — all inside `COPY src/`).
- `.env` and `.env.example` intentionally remain at project root (consumed by Docker Compose from the same directory).

## April 2026 Hardening Snapshot

- Scheduler: one-shot DB sync now normalizes `run_at` to ISO string before scheduling.
- Startup: migration execution is explicit and gated.

## April 23, 2026 — Tool Factory and Organization Setup Fixes

### Sandbox Path Fix
- Fixed tool execution path bug where sandbox was using duplicated paths like `/app/src/tools/plugins/<tool>/src/tools/plugins/<tool>/cli.py`
- Now uses resolved absolute paths with `Path.resolve()` to ensure correct script execution

### System-Binary Tool Support
- Enhanced static analysis to accept safe subprocess patterns using variables assigned to list literals
- Previously only accepted inline list literals like `subprocess.run(["ffmpeg", ...])`
- Now accepts safe patterns like:
```python
cmd = ["ffmpeg", "-i", input_file, output_file]
subprocess.run(cmd, ...)
```

### Organization Project Setup
- Added `ORG_PROJECT` domain to routing classifier for proper MEDIUM complexity routing
- Fixed user ID mapping in orchestrator skill registry (was passing DB PK instead of Telegram ID)
- Added FFmpeg and ImageMagick to Dockerfile system dependencies
- Updated planning prompt to use only real internal skill IDs (memory, scheduler, organizations)
- Enhanced tool creation instructions with exact code template for system-binary tools

### Validation Improvements
- Tools now show specific validation errors in agent config
- Sandbox tests skip binary execution when system binary is missing (graceful degradation)
- Real-time feedback on tool creation failures instead of silent deletion
- Bot reliability: critical routing fallback paths log structured warnings instead of silent exception swallowing.
- API security: dashboard CORS moved to env-driven allowlist with wildcard suppression.
- Tests: added focused regression tests for scheduler sync, migration gating, org auth/delete audit trail, CORS parsing, and `/orgs` Telegram handlers.

## Agentic Upgrade Patterns (April 12, 2026)

### Parallel Fan-Out (M1)
Call `detect_parallel_domains(message)` — returns a list of `{domain, prompt}` dicts if ≥ 2 domains with ≥ 0.7 confidence are detected. Pass to `run_parallel_tasks(domains, user_id, budget_used)`. Falls back to sequential if budget guard fires (spend ≥ 80% daily cap).

### Background Jobs (M2)
Call `is_background_job_request(message)` — returns `True` for "monitor / watch / keep an eye on / alert me when" phrasing. Call `create_background_job(goal, user_id, telegram_id)` to create and schedule the tick loop. Jobs self-terminate after `max_iterations` (default 144 = 24h at 10-min ticks) or 3 consecutive faults.

### Trace Persistence (M3)
After every `Runner.run()` call, extract `result.new_items` and pair `FunctionCallItem` + `FunctionCallOutputItem` items. Persist each pair as an `AgentTrace` row with `session_key = f"agent_session:{telegram_id}"`. Query via `GET /api/traces?session_key=...`.

### Risk-Classified Repair (M4)
In `RepairAgent`, call `classify_repair_risk(plan)` before deciding approval path. `propose_low_risk_fix` tool auto-applies immediately (no gate) and sets `auto_applied=True` on the RepairTicket. After apply, `verify_repair()` runs smoke test; on failure, `rollback_repair()` reverses changes.

### Persona Interview Onboarding (AD-7)
Based on Stanford's "Generative Agent Simulations" research (2024) and Cambridge/DeepMind's psychometric framework (2025). A dedicated `PersonaInterviewAgent` conducts a structured 3-session conversational interview via Telegram:
- **Session 1** — Identity & Context (who you are, work, communication preferences)
- **Session 2** — Work Style & Values (daily routine, decision-making, autonomy preference)
- **Session 3** — Communication & Personality (email voice, humor, boundaries)

After each session, an LLM synthesis step generates OCEAN scores (Big Five personality traits) and a structured profile (communication, work_context, values). The expanded profile is stored in `PersonaVersion.personality` JSONB and injected into the system prompt. The Curator agent periodically re-synthesizes the profile from accumulated Mem0 memories.

See `docs/ADR-2026-03-21-persona-interview-onboarding.md` for full design rationale.
