# PersonalAsst — Agentic AI Personal Assistant

A self-improving, multi-agent Personal Assistant that runs in Docker and talks through Telegram. It manages your Google Workspace, creates its own tools, remembers your preferences, schedules tasks, and gets smarter over time.

---

## Features

- **Telegram Chat** — Natural language + voice messages. No app to install.
- **Google Workspace** — Gmail, Calendar, Tasks, Drive, Docs, Sheets, Slides, Contacts via MCP integration.
- **Persistent Memory** — Remembers your preferences, past conversations, and workflows (Mem0 + Qdrant).
- **Conversation Memory** — SDK RedisSession gives the LLM real conversation history (last 20 turns) including tool calls and results.
- **Self-Improving** — Reflector scores every interaction; Curator runs weekly optimization. Persona evolves.
- **Digital Clone Onboarding** — Structured 3-session conversational interview builds a deep personality profile (OCEAN scores, communication style, work context, values). Based on Stanford/DeepMind research.
- **Dynamic Tool Creation** — Ask it to build a new CLI tool and it generates, tests, and registers it automatically. The Dashboard Tools tab also has an **AI Wizard** button that walks you through tool creation interactively (interview → generate → review → save) with static-safety analysis.
- **Task Scheduling** — "Remind me every Monday at 9am" or "Remind me today at 3pm" — recurring and one-shot jobs with natural language.
- **Organization Management** — Manage organizations from Dashboard and Telegram (`/orgs create|info|pause|resume|delete`). Delete with a **preview dialog** that shows all agents, tasks, and activity that will be removed — check any items you want to **retain** (they are moved to a holding org instead of being cascade-deleted).
- **AI-Guided Skill Creation** — Create custom skills via Telegram interview or Dashboard editor. Skills use declarative SKILL.md format with routing hints for natural language matching. **Duplicate-aware**: when an agent/tool/skill planned for a new project closely matches one you already have (similarity ≥ 85%), Atlas reuses the existing item instead of creating a near-duplicate, and tells you what it reused.
- **Filesystem-Based Skills** — Drop a SKILL.md file in `user_skills/` and hot-reload without restart. Version controlled, portable, shareable.
- **Safe by Design** — Context-aware input/output guardrails, sandboxed tool execution, cost caps, user allowlist.
- **Unified Cost Tracking** — Shared `record_llm_cost()` helper (`src/models/cost_tracker.py`) with a single pricing table for all models. Every agent call is tracked to PostgreSQL (daily_costs) and Redis (per-provider). Supports OpenAI, Anthropic, and OpenRouter models.
- **Self-Healing Diagnostics** — Multi-agent repair pipeline: **DebuggerAgent** does structured root-cause analysis, **ProgrammerAgent** generates a unified-diff fix with a file-type-aware test plan, **QualityControlAgent** validates patch + security + allowlist, then **RepairAgent** stores the plan for owner approval. Verification is file-type aware (Python → syntax check, SKILL.md → loader validation, YAML/JSON/TOML → structural parse) so non-Python patches don't get rejected by ruff. If verification fails because the runner is wrong for the file type, the agent calls `refine_pending_verification` instead of re-proposing the patch. You can also **open tickets manually** from the Dashboard Repairs tab and choose whether they go to the AI Agent (auto-repair pipeline) or the Admin (pauses until owner action).
- **Multi-LLM via OpenRouter** — Provider-agnostic model routing (`src/models/provider_resolution.py`) selects between OpenAI, Anthropic, and 15+ OpenRouter models based on task complexity and configured capability tiers (`src/config/openrouter_capabilities.yaml`).
- **Customizable Dashboard** — The Overview tab uses a **draggable/resizable grid** (react-grid-layout). Rearrange and resize tiles (costs, quality, tools, schedules, budget, persona) by dragging their headers. Layout is auto-saved per user and persists across sessions. Reset to defaults with one click.
- **Explainable Observability** — Every agent tool call is persisted as a trace step. Dashboard Timeline drawer shows full step-by-step agent thought trace per interaction. The **Interactions** tile on the Overview tab is clickable — it opens a drawer listing recent audit-log rows with filters (all / inbound / outbound / errors) and inline trace drill-down per row.
- **Parallel Multi-Agent Execution** — Multi-domain requests (e.g., "Email Sarah AND schedule a meeting AND update the doc") fan out to 3 parallel agent branches simultaneously.
- **Autonomous Background Jobs** — Say "Monitor my inbox until I get a reply from John" and Atlas creates a persistent background job with tick loop, fault tolerance, and Telegram notification when done.
- **Stale Session Recovery** — Automatic detection and clearing of corrupt SDK sessions with transparent retry.
- **Fully Self-Hosted** — All databases run in Docker. Zero SaaS calls for data storage.

## Latest Updates (2026-04-23) — src/ Consolidation + Self-Healing Agent Triad

- **src/ consolidation** — `alembic/`, `config/`, `orchestration-ui/`, `user_skills/` all moved INTO `src/` per [ADR-2026-04-13](docs/ADR-2026-04-13-src-consolidation.md). Canonical alembic root is now `src/db/migrations/`.
- **Self-healing agent triad** — Added `DebuggerAgent`, `ProgrammerAgent`, `QualityControlAgent` to the repair pipeline. RepairAgent now orchestrates Phase 1 (audit) → Phase 2 (diagnose) → Phase 3 (fix plan) → Phase 4 (validation + approval).
- **File-type aware verification** — New `src/repair/verify_file.py` (stdlib + pyyaml only, runs in the bot container). `RepairAgent.refine_pending_verification` tool lets the agent swap verification commands without re-proposing the patch when the previous runner was wrong for the file type. See [docs/RUNBOOK.md](docs/RUNBOOK.md#verification-failed-because-runner-is-wrong--missing).
- **Multi-LLM via OpenRouter** — `src/integrations/openrouter.py`, `src/models/provider_resolution.py`, `src/models/cost_tracker.py` with 15+ OpenRouter model entries.
- **Migrations 006–010** — `006_add_user_settings`, `007_governance_spend_ancestry`, `008_add_missing_columns`, `009_add_tts_voice`, `010_add_agent_traces`.
- **3 new user skills** — `src/user_skills/ffmpeg-video-composition/`, `pdf-generator-and-extractor/`, `video-processing/`.

## Earlier Updates (2026-04-23) — Dashboard Enhancement Phases 1–8

- **Phase 1 — Tool Wizard** — AI-guided tool creation dialog in Dashboard Tools tab (interview → generate → review → save)
- **Phase 2a — Cost Visibility** — Raised cost-tracking log level from DEBUG to INFO/WARNING; expanded `_OPENAI_MODEL_PRICING` with GPT-5.4, Claude Opus 4, OpenRouter models
- **Phase 2b — Shared Cost Helper** — Extracted `record_llm_cost()` into `src/models/cost_tracker.py`; unified pricing table; wired into orchestrator
- **Phase 3 — Duplicate Detection** — `setup_org_project` now fuzzy-matches agents, tools, and skills (≥ 85% similarity) and reuses existing items instead of creating near-duplicates
- **Phase 4 — Selective Org Deletion** — Delete-preview endpoint (`GET /api/orgs/{id}/delete-preview`) shows agents, tasks, activity count; enhanced DELETE supports selective retention via holding org
- **Phase 5 — Manual Tickets** — NewTicketDialog in Repairs tab for opening tickets manually (AI Agent or Admin pipeline)
- **Phase 6 — Interactions Drill-Down** — Clickable Interactions tile opens a drawer with audit-log rows, filters, and inline trace drill-down
- **Phase 7 — Tasks vs Jobs** — Tooltips + README_ORCHESTRATION section clarifying task/job distinction
- **Phase 8 — Draggable Grid** — Overview tab uses react-grid-layout; 6 draggable/resizable tiles with per-user layout persistence (Redis-backed)

### Previous Updates (2026-04-12) — Agentic Upgrade M1–M4

- **M3 Explainable Observability** — `AgentTrace` table; every tool call step persisted; Dashboard Timeline drawer in Activity tab
- **M4 Self-Healing Loop** — `classify_repair_risk()`, auto-apply for low-risk fixes, `verifier.py` smoke + rollback; Repairs tab with risk/status badges
- **M1 Parallel Fan-Out** — `parallel_runner.py` with `asyncio.gather`; detects multi-domain requests and fans out up to 3 branches; budget-aware fallback
- **M2 Background Jobs** — `background_job.py`; `BackgroundJob` model; orchestrator detects monitor/watch phrases; Jobs Dashboard tab with progress + cancel
- **Repo Cleanup** — 4 backup dirs removed; `.gitignore`/`.dockerignore` hardened; Dockerfile alembic fix; `.windsurf`/`bootstrap` removed from git tracking

### Previous Reliability & Security Updates (2026-04-11)

- Fixed one-shot scheduler DB sync contract mismatch (`run_at` normalized as ISO string)
- Added startup migration safety gate via `STARTUP_MIGRATIONS_ENABLED` (default: disabled)
- Enforced org ownership checks on dashboard API org endpoints
- Added durable org delete audit logs (Dashboard API + Telegram `/orgs delete`)
- Replaced critical silent exception-swallowing paths with structured fallback logging
- Hardened dashboard CORS to env-driven allowlist (`CORS_ALLOWED_ORIGINS`) and blocked wildcard defaults

## Architecture

```
Telegram Bot (aiogram 3.x)
  → Orchestrator (Office Organizer — dynamic complexity routing)
    ├── Parallel Runner     (asyncio.gather fan-out — up to 3 domains simultaneously)
    ├── Background Job      (autonomous monitor/watch jobs with tick loop + Telegram notify)
    ├── 8 Google Workspace Skills (45 direct tools, zero agent wrappers)
    │   ├── Gmail           (6 tools — read, search, draft, send, reply, filter)
    │   ├── Calendar        (2 tools — get events, manage events)
    │   ├── Tasks           (4 tools — list, create, update, complete)
    │   ├── Drive           (7 tools — search, list, upload, download, share, trash, manage)
    │   ├── Docs            (7 tools — search, create, read, edit, find-replace, export, manage)
    │   ├── Sheets          (6 tools — create, read, update, append, clear, manage)
    │   ├── Slides          (5 tools — create, get, batch update, get page, thumbnail)
    │   └── Contacts        (4 tools — list, get, search, manage)
    ├── 2 Internal Skills (11 direct tools)
    │   ├── Memory          (7 tools — recall, store, list, forget, forget-all, summarize session, get context)
    │   └── Scheduler       (4 tools — create reminder, morning brief, list, cancel)
    ├── Web Search
    ├── Skill Factory Agent   (AI-guided skill creation via interview)
    ├── Tool Factory Agent  (Handoff — generates CLI tools on demand)
    ├── Persona Interview   (Structured 3-session personality profiling)
    ├── Repair Agent        (Handoff — risk classification, auto-apply low-risk, file-type aware verification + rollback)
    │   ├── Debugger Agent       (structured root-cause analysis)
    │   ├── Programmer Agent     (unified-diff fix generation with file-type aware test plans)
    │   └── Quality Control Agent (patch validation, security scan, allowlist enforcement)
    ├── Reflector Agent     (background quality scoring + trend tracking)
    ├── Curator Agent       (weekly self-improvement)
    └── Safety Agent        (input injection + output PII guardrails)
```

**Docker Compose stack:** App + PostgreSQL 17 + Qdrant + Redis 7 + Google Workspace MCP

## Google Cloud Setup (Required for Gmail/Calendar/Drive)

1. **Create Google Cloud Project**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Select or create a project

2. **Enable Required APIs**
   ```
   Gmail API
   Google Calendar API
   Google Tasks API
   Google Drive API
   Google Sheets API
   Google Docs API
   Google Slides API
   Google People API
   ```

3. **Configure OAuth 2.0**
   - Go to **APIs & Services → Credentials**
   - Create **OAuth 2.0 Client ID**
   - Application type: **Web application**
   - Name: `PersonalAsst`
   - Authorized redirect URI: `http://localhost:8083/oauth2callback`
   - Download credentials as `credentials.json`

4. **Update .env**
   ```bash
   cp .env.example .env
   # Add to .env:
   GOOGLE_OAUTH_CLIENT_ID=your_client_id
   GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret
   ```

5. **Place credentials file**
   ```bash
   mv ~/Downloads/credentials.json .
   ```

## Quick Start

```bash
# 1. Clone
git clone https://github.com/llores28/PersonalAsst_v1.git
cd PersonalAsst_v1

# 2. Configure
cp .env.example .env
# Edit .env — fill in:
#   OPENAI_API_KEY       (from OpenAI dashboard)
#   TELEGRAM_BOT_TOKEN   (from @BotFather on Telegram)
#   OWNER_TELEGRAM_ID    (your numeric Telegram ID)
#   DB_PASSWORD           (any random string)
#   GOOGLE_OAUTH_CLIENT_ID    (from Google Cloud Console)
#   GOOGLE_OAUTH_CLIENT_SECRET (from Google Cloud Console)
#   STARTUP_MIGRATIONS_ENABLED (true/false, default false)
#   CORS_ALLOWED_ORIGINS       (comma-separated dashboard origins)
#   AUTO_REPAIR_LOW_RISK       (true/false — auto-apply low-risk repair fixes, default true)

# 3. Build & Start
docker compose build
docker compose up -d

# 3-a. Rebuild
docker compose down --remove-orphans
docker compose up -d --build

# 4. Connect Google Workspace
# In Telegram: /connect google
# Follow OAuth link → approve → return to bot

# 5. Chat
# Open Telegram → send /start to your bot
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Initial setup |
| `/help` | Show all commands and examples |
| `/connect google` | Connect Google Workspace (Gmail, Calendar, Drive) |
| `/persona` | View/edit assistant personality (name, style, traits) |
| `/persona interview` | Start or resume the personality profiling interview |
| `/memory` | See what the assistant remembers about you |
| `/forget <topic>` | Delete memories matching a topic |
| `/tools` | List registered tools |
| `/schedules` | List active scheduled tasks |
| `/orgs` | Manage organizations: list/create/info/pause/resume/delete |
| `/skills` | Manage skills: list, create (AI-guided), delete, reload |
| `/stats` | Usage dashboard (cost, requests, tools, memory) |
| `/cancel <id>` | Cancel a scheduled task |

Or just chat naturally:
- "What's on my calendar today?"
- "Send an email to Sarah about the project update"
- "Remind me every Monday at 9am to review my goals"
- "Remember that I prefer morning meetings"
- "Create a skill for writing my weekly status reports"
- "Create a tool that converts CSV to JSON"

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | OpenAI GPT-5.4, GPT-5.4-mine, GPT-5.3-Codex |
| Agent Framework | OpenAI Agents SDK (Python) |
| Messaging | aiogram 3.x (Telegram) |
| Memory | Mem0 (open-source) + Qdrant + PostgreSQL |
| Database | PostgreSQL 17 (self-hosted Docker) |
| Cache | Redis 7 (self-hosted Docker) |
| Google | Google Workspace MCP Server |
| Scheduling | APScheduler 4.x (cron + heartbeat) |
| Skills | Declarative SKILL.md with YAML frontmatter |
| Deployment | Docker Compose |

## Project Structure

```
src/
├── main.py                     # Entry point
├── settings.py                 # Config from .env (Pydantic)
├── bot/                        # Telegram handlers, voice transcription
├── agents/                     # 27 agent / runner files
│   ├── orchestrator.py         # Office organizer + complexity routing
│   ├── persona_mode.py         # Persona template + prompt assembly (canonical)
│   ├── routing_hardened.py     # TaskDomain/Intent enums + parallel domain detection
│   ├── parallel_runner.py      # asyncio.gather fan-out (max 3, budget-aware) [M1]
│   ├── background_job.py       # Autonomous background job lifecycle [M2]
│   ├── email_agent.py          # Gmail — 6 direct connected tools
│   ├── calendar_agent.py       # Calendar — 2 direct connected tools
│   ├── tasks_agent.py          # Tasks — 4 direct connected tools
│   ├── drive_agent.py          # Drive — 7 direct connected tools
│   ├── docs_agent.py           # Docs — 7 direct connected tools
│   ├── sheets_agent.py         # Sheets — 6 direct connected tools
│   ├── slides_agent.py         # Slides — 5 direct connected tools
│   ├── contacts_agent.py       # Contacts — 4 direct connected tools
│   ├── memory_agent.py         # Memory — 7 direct bound tools (LTM + STM)
│   ├── scheduler_agent.py      # Scheduler — 4 direct bound tools
│   ├── tool_factory_agent.py   # Dynamic tool creation (Handoff)
│   ├── reflector_agent.py      # Quality scoring + score tracking (ACE)
│   ├── curator_agent.py        # Weekly self-improvement (ACE)
│   ├── repair_agent.py         # Self-healing orchestrator — risk classify, refine_pending_verification, rollback [M4]
│   ├── debugger_agent.py       # Repair Phase 1 — structured root-cause analysis
│   ├── programmer_agent.py     # Repair Phase 3 — file-type aware fix generation (unified diff + test plan)
│   ├── quality_control_agent.py # Repair Phase 4 — patch validation + security scan + allowlist enforcement
│   ├── skill_factory_agent.py  # AI-guided skill creation (interview → SKILL.md)
│   ├── persona_interview_agent.py # Structured 3-session personality interview
│   └── safety_agent.py         # Input/output guardrails
├── skills/                     # Unified skill registry (10 built-in + dynamic)
│   ├── definition.py           # SkillDefinition contract with progressive disclosure
│   ├── registry.py             # SkillRegistry — dependency resolution, activate/deactivate
│   ├── loader.py               # SKILL.md parser for filesystem skills
│   ├── validation.py           # On-demand skill testing (routing confidence)
│   ├── google_workspace.py     # 8 Google Workspace skill builders
│   ├── internal.py             # Memory + Scheduler skill builders
│   ├── openrouter.py           # OpenRouter provider skill builder (multi-LLM)
│   └── dynamic.py              # CLI/function tool skill builder
├── user_skills/                # User-created SKILL.md skills (hot-reloaded via volume mount)
├── config/                     # Persona, safety policies, tool tiers, providers, openrouter_capabilities (YAML)
├── repair/                     # Repair engine, risk classifier, verify_file (file-type aware), notifications [M4]
├── memory/                     # Mem0 (dedup + access tracking), Redis, persona
├── models/                     # Model catalog, provider_resolution.py, cost_tracker.py (multi-LLM pricing)
├── tools/                      # Tool registry, sandbox, manifest schema
├── scheduler/                  # APScheduler engine, job callables, backup
├── integrations/               # Google Workspace MCP client, openrouter.py (multi-LLM)
├── orchestration/              # FastAPI Dashboard API (+ /api/traces, /api/repairs, /api/background-jobs)
├── orchestration-ui/           # React Dashboard UI (build artifacts gitignored)
├── alembic.ini                 # Alembic config (script_location = src/db/migrations)
└── db/                         # SQLAlchemy models, Alembic migrations 001-010 (010 adds agent_traces)
tests/                          # 40+ test modules covering agents, repair pipeline, multi-LLM
docs/                           # Developer guide, runbook, user guide, PRD, 5+ ADRs
```

## Key Design Decisions

- **All skills flattened to direct tools** — Zero agent wrappers. Every Google Workspace and internal skill uses direct `function_tool` closures bound at creation time. Single LLM call per request (no nested agent reasoning).
- **Impl-function pattern for bound tools** — Scheduler (and all bound tools) use plain `_*_impl` async functions for core logic. `@function_tool` wrappers and bound closures both delegate to these. Prevents `FunctionTool object is not callable` errors.
- **Office Organizer persona** — The orchestrator is an expert office organizer with explicit domain boundaries and disambiguation rules for all 10 skill domains.
- **SDK RedisSession for conversation memory** — OpenAI Agents SDK `RedisSession` stores real conversation turns (user, assistant, tool calls). Session history filtered to exclude stale `function_call`/`function_call_output` items. Graceful degradation if Redis fails.
- **Context-aware PII guardrails** — Output guardrail receives user message via `Runner.run(context={...})`. Two-layer check: (1) context-aware email allowance, (2) output-marker fallback. Prevents false positives when user explicitly requests email operations.
- **Prompt cache optimization** — Static content (skills, rules, routing) placed first in the system prompt; dynamic content (user identity, datetime, connected email) placed last. Maximizes OpenAI prompt caching (research: 41-80% cost reduction).
- **Dynamic complexity routing** — Lightweight heuristic classifier routes simple reads to cheaper models (nano/mini) and complex multi-step requests to more capable models (standard/pro). No LLM call for classification.
- **Parallel multi-agent fan-out** — `detect_parallel_domains()` identifies multi-domain conjunctions (e.g., "email AND calendar AND doc") and fans out to up to 3 async agent branches simultaneously via `asyncio.gather`. Falls back to sequential if daily spend ≥ 80%.
- **Autonomous background jobs** — Orchestrator detects "monitor/watch/alert me when" phrases and creates a `BackgroundJob` with APScheduler tick loop, fault counter (max 3 faults), iteration cap, and Telegram notification on completion/failure.
- **Repair risk classification** — `classify_repair_risk()` scores repair plans as `low | medium | high`. Low-risk ops (Redis clears, schedule re-injections, env-var logging) are auto-applied immediately. Medium/high require owner approval.
- **Explainable trace steps** — Every `FunctionCallItem` from `Runner.run()` is persisted to `agent_traces` with tool name, args, result preview, duration, and session key. Queryable via `GET /api/traces`.
- **Memory deduplication** — Before storing, checks for semantically similar memories (>0.85 cosine) and updates the existing entry instead of creating duplicates. Access-count tracking informs the curator’s pruning decisions.
- **Unified STM/LTM** — Memory tools include both long-term operations (recall/store/forget) and short-term session operations (summarize conversation, get recent context).
- **Quality score tracking** — Reflector records quality scores per user in Redis. Trend degradation alerts when average drops below 0.5 over 5 interactions.
- **CLI-first tool creation** — Generated tools are standalone argparse scripts, not MCP servers.
- **Single async process** — Bot, agents, scheduler all run in one Python process.
- **Redis for active conversations** — 30-min TTL, auto-archived to Mem0 episodic memory. Task list caching (30s) for rapid follow-ups.
- **Defensive MCP integration** — `call_workspace_tool` strips `None` values before sending to MCP server (`additionalProperties: false`). Gmail tools use correct schema fields (`thread_id`/`in_reply_to`, not deprecated `reply_to_message_id`).
- **Stale session recovery** — Bot handlers catch `BadRequestError` from orphaned tool-call IDs in SDK sessions, clear the session, and retry once.
- **Tell user on error** — No silent retries. User stays in control.
- **Interview-based persona onboarding (AD-7)** — Structured 3-session conversational interview (Stanford approach) builds a deep personality profile with OCEAN scores, communication preferences, work context, and values. LLM synthesis generates a multi-perspective profile that shapes all assistant responses. Curator periodically re-synthesizes from accumulated memories.

## Security

- Telegram user ID allowlist (DB-backed, owner manages via `/allow` and `/revoke`)
- Input guardrail: prompt injection detection (pattern + LLM)
- Output guardrail: context-aware PII detection (SSN, credit cards) with email workflow awareness
- Tool sandbox: empty environment, timeout, static analysis
- Cost caps: daily and monthly limits with 80% alerts
- Secrets: `.env` only, never in code or logs
- Docker: non-root user, resource limits

## Documentation

- [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) — Architecture, setup, adding agents/tools
- [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) — End-user guide for Telegram
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — Operations, troubleshooting, monitoring
- [`docs/PRD.md`](docs/PRD.md) — Product requirements with acceptance criteria
- [`docs/HANDOFF.md`](docs/HANDOFF.md) — Current status, completed phases, pending work
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — Release history and recent fixes/upgrades
- [`PRD_PersonalAssistant.md`](PRD_PersonalAssistant.md) — Detailed build spec (schemas, decisions)
- [`RESEARCH_PersonalAssistant.md`](RESEARCH_PersonalAssistant.md) — Deep research report
- `docs/ADR-*.md` — Architecture Decision Records documenting key choices and fixes

## License

Private repository. All rights reserved.
