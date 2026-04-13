# Handoff — PersonalAsst

## Current Status

**Phase:** All core phases (1–9) implemented and operational  
**Date:** April 12, 2026  
**Test suite:** 540+ passing (21 test files + 5 new), 30 pre-existing SDK-absent failures (import-only, pass in Docker)

## What Exists

| Item | Status | Notes |
|------|--------|-------|
| Research document | Complete | `RESEARCH_PersonalAssistant.md` — 21 sections, gaps analysis |
| PRD | Complete | `PRD_PersonalAssistant.md` — 18 sections, all gaps resolved |
| Bootstrap | Complete | Team tier bootstrap plus VS Code migration scaffolding for instructions, prompts, and tasks |
| Source code | **Complete** | `src/` — 19 agent files, 10 skills, scheduler, memory, tools (+ credential vault), security |
| Docker Compose | **Running** | 5 services: assistant, postgres, qdrant, redis, workspace-mcp |
| Tests | **493+ passing** | 20 test files covering agents, tools, guardrails, scheduling, memory |
| ADRs | **13 written** | Architecture decision records in `docs/ADR-*.md` |

## Completed Phases

### Phase 1–6 — Core Platform (Complete)
- All core infrastructure operational

### Phase 7 — Skill Management System (Complete)
- **Three-path skill creation:**
  - Telegram: `/skills` command with AI-guided interview (Skill Factory Agent)
  - Dashboard: Full CRUD + on-demand testing with routing confidence analysis
  - Filesystem: Drop SKILL.md files in `user_skills/` and hot-reload
- **Declarative skill format:** YAML frontmatter + markdown instructions
- **Progressive disclosure:** L1 (metadata) → L2 (instructions) → L3 (resources)
- **Skill validation:** On-demand testing with routing confidence scoring
- **Scheduler diagnostics:** Health checks + cron/heartbeat test endpoints

### Phase 1 — Core Bot + Orchestrator
- aiogram 3.x Telegram bot with persona-consistent replies
- Office Organizer orchestrator with dynamic complexity routing
- Input/output guardrails (prompt injection + PII detection)
- Cost tracking, audit logging, user allowlist

### Phase 2 — Google Workspace
- 8 Google Workspace skills: Gmail (6 tools), Calendar (2), Tasks (4), Drive (7), Docs (7), Sheets (6), Slides (5), Contacts (4)
- All 45 tools are direct `function_tool` closures — zero agent wrappers
- MCP integration via `call_workspace_tool()` with defensive None-stripping
- OAuth via Google Workspace MCP server sidecar

### Phase 3 — Memory + Persona
- Mem0 + Qdrant for persistent semantic memory with dedup (0.85 cosine threshold)
- 7 memory tools: recall, store, list, forget, forget-all, summarize session, get context
- SDK RedisSession for real conversation memory (last 20 turns)
- Persona CRUD with DB versioning, dynamic runtime datetime injection

### Phase 4 — Scheduling
- APScheduler 4.x with PostgreSQL job store (persists across restarts)
- 4 scheduler tools: create reminder (cron/interval/once), morning brief, list, cancel
- Bound tools use `_*_impl` pattern (plain async functions, not FunctionTool objects)
- Natural language time parsing routed via temporal parser + action policy

### Phase 5 — Tool Factory
- Dynamic CLI/function tool creation via Handoff agent
- Standalone argparse scripts, sandboxed execution, manifest schema
- Hot-reload via filesystem watcher on `tools/` directory
- **Phase 8 enhancements:** credential vault, sandbox env injection, multi-tool function wrappers

### Phase 6 — Self-Improvement + Repair
- Reflector agent: post-interaction quality scoring + trend tracking
- Curator agent: weekly self-improvement cycle (ACE pattern)
- Repair agent: read-only diagnostics (honest about no codebase access)
- Voice message transcription
- Security challenge gate for destructive repair actions

## Key Bugs Fixed (chronological)

| Date | Bug | Root Cause | Fix |
|------|-----|-----------|-----|
| Mar 18 | Shared MCP instance broke sub-agent calls | Lifecycle conflicts with SDK Runner | Per-call MCP lifecycle when connected email set |
| Mar 18 | Safety guardrail false positives | Narrow allowlist for maintenance requests | `_is_owner_maintenance_request()` bypass |
| Mar 19 | Calendar returned wrong dates | Persona prompt missing current datetime | `_atlas_runtime_lines()` injects datetime |
| Mar 19 | PII guardrail blocked email drafts | Narrow output marker allowlist | Expanded markers for draft/send phrasings |
| Mar 19 | "Something went wrong" for output trips | Only caught `InputGuardrailTripwireTriggered` | Added `OutputGuardrailTripwireTriggered` handler |
| Mar 19 | "send it" after draft loops forever | Pending payload gate too narrow | Broader `_is_email_related_request()` check |
| Mar 19 | Tasks routed to calendar | No task-capable tool exposed | Explicit tool routing rules + scheduler binding |
| Mar 20 | Gmail send rejected by MCP | `reply_to_message_id` not in MCP schema | Replaced with `thread_id`/`in_reply_to` |
| Mar 20 | Stale session 400 errors | Orphaned `function_call_output` items in SDK session | Filter session history + catch-and-retry |
| Mar 20 | Reminder tool errored | `DateTrigger(run_date=...)` — APScheduler 4.x uses `run_time` | Changed to `DateTrigger(run_time=...)` |
| Mar 20 | `FunctionTool object is not callable` | Bound tools called `@function_tool` objects directly | Extracted `_*_impl` pattern |
| Mar 20 | Repair agent hallucinated patches | No codebase access but fabricated file paths | Honest instructions about limitations |

## Key Documents to Read

1. `AGENTS.md` — **repo navigation and command verification** (read first)
2. `PRD_PersonalAssistant.md` — detailed build spec (schemas, decisions)
3. `docs/DEVELOPER_GUIDE.md` — architecture and dev workflow
4. `docs/RUNBOOK.md` — operations and troubleshooting
5. `docs/ADR-*.md` — 12 architecture decision records
6. `.github/copilot-instructions.md` — VS Code / Copilot repo instructions
7. `.vscode/tasks.json` — shared verified commands for tests, lint, typing, and Docker
8. `.github/prompts/` — VS Code prompt files wrapping the highest-value workflows
9. `.windsurf/` — legacy Windsurf rules, skills, and workflows kept as migration reference

## Phase 9 — Agentic Upgrade (Complete — April 12, 2026)

**Goal:** Make Atlas perform like Anthropic's Computer Use — parallel execution, autonomous background jobs, step-by-step observability, and auto-healing operational issues.

| Milestone | Description | Status |
|-----------|-------------|--------|
| **M3 Explainable Observability** | `AgentTrace` table, trace persistence in orchestrator, `GET /api/traces`, Dashboard Timeline drawer | ✅ Complete |
| **M4 Self-Healing Loop** | `classify_repair_risk()`, `propose_low_risk_fix` auto-apply, `verifier.py`, `risk_level`/`auto_applied` on RepairTicket, Repairs tab | ✅ Complete |
| **M1 Parallel Fan-Out** | `parallel_runner.py` asyncio.gather (max 3 branches, budget guard), `detect_parallel_domains()`, orchestrator pre-flight | ✅ Complete |
| **M2 Background Jobs** | `background_job.py`, `BackgroundJob` model, orchestrator monitor-phrase detection, Jobs Dashboard tab | ✅ Complete |
| **Repo Cleanup** | 4 backup dirs deleted, `.gitignore`/`.dockerignore` hardened, Dockerfile alembic fix, compose bind-mount removed | ✅ Complete |

New files added:
- `src/agents/parallel_runner.py` — asyncio fan-out runner
- `src/agents/background_job.py` — autonomous background job lifecycle
- `src/repair/verifier.py` — post-apply smoke test + rollback
- `alembic/versions/006_agentic_upgrade.py` — migration for agent_traces, background_jobs, repair_tickets columns
- `.dockerignore` — lean Docker build context

---

## Phase 7 — Persona Interview Onboarding (Complete)

**Goal:** Transform PersonalAsst from a generic assistant into a digital clone that communicates and decides like its owner.

**Research basis:** Stanford "Generative Agent Simulations of 1,000 People" (2024), Cambridge/DeepMind Psychometric Framework for LLMs (2025).

| Step | Description | Status |
|------|-------------|--------|
| Schema expansion | New `persona_interviews` table + expanded `PersonaVersion.personality` JSONB | ✅ Complete |
| PersonaInterviewAgent | Structured 3-session conversational interview agent | ✅ Complete |
| LLM synthesis | Multi-perspective personality analysis producing OCEAN scores + profile | ✅ Complete |
| Prompt integration | Update `persona_mode.py` to inject richer profile into system prompts | ✅ Complete |
| Telegram commands | `/persona interview` to start/resume; `/persona interview reset` | ✅ Complete |
| Curator integration | Weekly re-synthesis with OCEAN drift clamping (±0.1/week) | ✅ Complete |

See `docs/ADR-2026-03-21-persona-interview-onboarding.md` for full design rationale.

## Phase 8 — Tool Factory Infrastructure + LinkedIn (Complete)

**Goal:** Fix Tool Factory infrastructure gaps and build the first real function-type tool (LinkedIn).

| Step | Description | Status |
|------|-------------|--------|
| Credential vault | Redis-backed `src/tools/credentials.py` — store/get/delete per-tool secrets | ✅ Complete |
| Manifest schema | Added `credentials` + `dependencies` fields to `ToolManifest` | ✅ Complete |
| Sandbox env fix | `build_sandbox_env()` injects Python paths + vault credentials as `TOOL_*` vars | ✅ Complete |
| Multi-tool registry | `ToolRegistry` supports `tool_functions` list from function-type wrappers | ✅ Complete |
| LinkedIn tool | 10 function_tools: profile, search, jobs, messages, posts (unofficial Voyager API) | ✅ Complete |
| LinkedIn CLI | Manual testing CLI (`src/tools/plugins/linkedin/cli.py`) | ✅ Complete |
| Startup seeding | `seed_tool_credentials()` in `main.py` reads env vars → Redis vault | ✅ Complete |
| Tests | 47 tests in `test_tool_factory.py` covering all new infrastructure | ✅ Complete |
| Docker config | LinkedIn env vars in `.env.example`, `docker-compose.yml`, `Dockerfile` | ✅ Complete |
| /tools credentials | Telegram command for managing tool API keys (set/list/delete) | ✅ Complete |
| Browser tool | 11 Playwright function_tools: navigate, click, fill, type, text, html, screenshot, info, wait, login, close | ✅ Complete |

**LinkedIn tools available:** `linkedin_get_profile`, `linkedin_get_my_profile`, `linkedin_get_profile_views`, `linkedin_search_people`, `linkedin_search_jobs`, `linkedin_get_job`, `linkedin_get_conversations`, `linkedin_send_message`, `linkedin_create_post`, `linkedin_get_invitations`

**Browser tools available:** `browser_navigate`, `browser_click`, `browser_fill`, `browser_type`, `browser_get_text`, `browser_get_html`, `browser_screenshot`, `browser_page_info`, `browser_wait`, `browser_login`, `browser_close`

**Credential management via Telegram:** `/tools credentials set <tool> <key> <value>` — no .env editing needed.

## Pending / Future Work

- **Background job `reinject_schedule` action** — partially implemented, needs APScheduler hook
- **Easy Apply** — requires browser automation (Playwright MCP sidecar), deferred
- **Graph memory** (Apache AGE) — advanced/optional, deferred
- **WhatsApp / Discord adapters** — optional future channels
- **Google Tasks API deep integration** — workspace-mcp supports `--tools tasks`
- **Multi-user support** — requires full tenancy redesign
- **Parallel runner unit tests** — `test_parallel_runner.py` and `test_background_job.py` not yet written
- **Background job cancel via Telegram `/cancel`** — route `/cancel` to background_job cancel by ID

## Environment Requirements

- Docker Desktop with Compose v2
- Python 3.12+ (for local dev/testing)
- OpenAI API key (GPT-5.x access)
- Telegram bot token from @BotFather
- Google OAuth credentials (Google Cloud Console)
