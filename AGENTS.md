# PersonalAsst — AI Agent Operating System

A single-user, Dockerized, self-improving multi-agent Personal Assistant powered by OpenAI and managed via Telegram.

## Project Overview

- **Primary UX:** Telegram (aiogram 3.x)
- **LLM:** OpenAI GPT models (Responses API, NOT Assistants API)
- **Agent Framework:** OpenAI Agents SDK (Python)
- **Infra:** Docker Compose — PostgreSQL 17, Redis 7, Qdrant (all self-hosted)
- **Google Workspace:** Via MCP Server sidecar container
- **Memory:** Mem0 open-source (self-hosted) + Qdrant + PostgreSQL

## Key Directories

```
src/                    # Application source code
src/bot/                # Telegram bot layer (aiogram)
src/agents/             # Agent definitions (OpenAI Agents SDK)
src/tools/              # Tool registry, sandbox, manifest validation
src/tools/plugins/      # Dynamic tools (CLI and function-type)
src/memory/             # Mem0 wrapper, conversation state, persona
src/scheduler/          # APScheduler engine + job callables
src/integrations/       # Google Workspace MCP client
src/db/                 # SQLAlchemy models, Alembic migrations
src/orchestration/      # Organization/project management API + Dashboard API
src/models/             # Model catalog, cost_tracker.py (shared record_llm_cost helper)
src/config/             # Runtime YAML configs (persona, safety, tiers)
bootstrap/              # Nexus CLI toolkit (smoketest, debug, health, etc.)
bootstrap/cli/          # Python CLI tools entry point: bs_cli.py
.windsurf/rules/        # Always-on AI rules for this project
.windsurf/skills/       # Reusable skill definitions (SKILL.md files)
.windsurf/workflows/    # Slash-command workflow definitions
```

## Hard Constraints

- **HC-1:** All databases self-hosted in Docker — no SaaS DB/memory API calls.
- **HC-2:** CLI-first tool creation — MCP only as fallback.
- **HC-3:** OpenAI as sole LLM provider.
- **HC-4:** Responses API only — no Assistants API.
- **HC-5:** Single-user system — no multi-tenancy.
- **HC-6:** Telegram primary UX.
- **HC-7:** Python 3.12+ only.
- **HC-8:** Non-technical user must never need CLI access.

## Operating Constraints

1. **No secrets** in output, commits, or logs.
2. **No invented commands** — verify from repo files before suggesting.
3. **Minimal changes** — prefer small, reversible edits.
4. **Security defaults** — validate paths, validate URLs, no shell=True, no eval/exec.
5. **Evidence-based** — cite file paths for non-trivial claims.
6. Mark uncertainty as `TODO(verify)`.
7. **Documentation updates mandatory** — after any large fix/feature/improvement to `src/`, update the living docs listed below.

## Documentation Update Rule

After any significant `src/` change (new/removed/renamed agent, tool, command, API endpoint, env var, container, or DB model), **you MUST update** these living docs:

| Doc | What to update |
|-----|----------------|
| `README.md` | Features list, architecture diagram, tech stack, project structure, latest updates |
| `README_ORCHESTRATION.md` | Dashboard API endpoints, orchestration architecture, UI features |
| `docs/DEVELOPER_GUIDE.md` | Architecture, setup steps, adding agents/tools, new patterns |
| `docs/USER_GUIDE.md` | New/changed Telegram commands, UX flows, user-facing behavior |
| `docs/RUNBOOK.md` | New containers, env vars, health checks, troubleshooting steps |
| `docs/HANDOFF.md` | Current status, completed phases, pending work |
| `docs/CHANGELOG.md` | Summary of what changed (append new entry at top) |
| `docs/PRD.md` | New requirements, acceptance criteria, changed constraints |
| `docs/architecture-report.html` | Regenerate if architecture changed (new agents, services, data flows) |

Skip for minor typos or internal refactors with no behavior change. Make targeted edits — do not rewrite entire docs.

## Token/Quota Efficiency

- Use code search / Fast Context before reading full files.
- Read files in large chunks to avoid repeated small reads.
- Batch independent tool calls in parallel.
- Keep responses concise — no restating known context.
- For simple edits, suggest Ctrl+I (Command mode, free, no quota cost).
- Suggest user run tests manually rather than auto-executing.
- **Model selection**: Use `bootstrap/model-selection-reference.md` for strategy.

## Testing

- Run `python bootstrap/cli/bs_cli.py smoketest --level quick` for quick verification.
- Run `python bootstrap/cli/bs_cli.py prereqs` to check prerequisites.
- CLI tools emit structured JSON by default (`--format json`), human output via `--format human`.

## CLI Toolkit Commands

```
python bootstrap/cli/bs_cli.py prereqs           # Check prerequisites
python bootstrap/cli/bs_cli.py smoketest         # Run smoke tests
python bootstrap/cli/bs_cli.py debug logs <path> # Inspect logs
python bootstrap/cli/bs_cli.py health check      # Nexus health check
python bootstrap/cli/bs_cli.py supply-chain scan  # Supply chain audit
```

## Organization Project Setup Workflow

The `setup_org_project` tool enables goal-based project creation with automated agent, task, and tool generation.

### How It Works

1. **User Request**: User describes a goal like "Setup an FFmpeg Video Composer project"
2. **LLM Planning**: GPT-4o-mini generates structured JSON with agents, tasks, and tools
3. **Organization Creation**: Creates or reuses an organization as the project container
4. **Agent Creation**: Adds agents with skills and allowed tools to the organization
5. **Task Generation**: Creates and assigns tasks to the appropriate agents
6. **Tool Creation**: Generates CLI tools for system-binary dependencies (FFmpeg, etc.)

### Key Features

- **Routing Enhancement**: ORG_PROJECT domain ensures MEDIUM complexity routing
- **System-Binary Support**: Safe sandbox execution for FFmpeg, ImageMagick, sox, yt-dlp
- **Real-time Validation**: Immediate feedback on tool/skill registration issues
- **Skill Constraints**: Only real internal skills allowed (memory, scheduler, organizations)
- **Duplicate Detection**: Fuzzy-matches agents, tools, and skills (≥ 85% similarity) and reuses existing items
- **Selective Org Deletion**: Preview dialog shows agents/tasks/activity; retained items moved to `__retained__` holding org
- **Dashboard Layout Persistence**: `GET/PUT /api/dashboard/layout` stores react-grid-layout positions in Redis per user

### Example Request

```
Setup a new FFmpeg Video Composer project from scratch

Agent name: FFmpeg Video Composer
Role: media automation engineer
Goal: Create videos from images, video clips, subtitles, voiceover, and background music
Skills: ffmpeg, ffprobe, video_editing, audio_mixing, subtitle_generation
Tools: FFmpeg CLI, Python scripting, Pillow/ImageMagick
Responsibilities: convert images to video, combine clips, add music/subtitles, export optimized files
```

### Result

- Organization created with ID
- 2-4 agents added (ProjectManager, VideoProcessor, etc.)
- 4-10 tasks created and assigned
- CLI tools generated and registered (e.g., ffmpeg_convert_video)
- Validation status shown for each component

## Repair Pipeline Key Files

| File | Role |
|------|------|
| `src/repair/engine.py` | Full pipeline: classify risk, store plan, sandbox, execute, approve deploy |
| `src/repair/verifier.py` | Post-apply smoke test + rollback for low-risk auto-fixes |
| `src/repair/models.py` | Pydantic contracts between pipeline stages (DebugAnalysis, FixProposal, etc.) |
| `src/repair/notifications.py` | Email alerts → `lannys.lores@gmail.com` (ticket created, fix ready) |
| `src/bot/notifications.py` | Telegram push helpers (error alert, ticket created, inline keyboard, low-risk) |
| `src/agents/repair_agent.py` | RepairAgent: analyze, propose patch, propose low-risk fix |
| `src/agents/debugger_agent.py` | DebuggerAgent: root-cause analysis |
| `src/agents/programmer_agent.py` | ProgrammerAgent: unified diff + test plan |
| `src/agents/quality_control_agent.py` | QA: security scan + applicability check |
| `src/bot/handlers.py` | `/tickets`, `/ticket`, `cb_repair_approve`, `cb_repair_skip` callbacks |
| `src/db/models.py` | `RepairTicket` ORM model (lifecycle: open→debug_analysis_ready→plan_ready→verifying→ready_for_deploy→deployed/closed) |

## Telegram Repair Commands

| Command | Description |
|---------|-------------|
| `/tickets` | List all open repair tickets with status icons |
| `/ticket approve <id>` | Merge verified branch to main (owner-only, triggers security gate) |
| `/ticket close <id>` | Dismiss ticket without deploying (owner-only) |

Inline keyboard buttons `repair_approve:<id>` and `repair_skip:<id>` are also registered in `handlers.py` for one-tap approval from the "Apply fix now?" Telegram message.

## Dashboard API Key Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/orgs/{id}/delete-preview` | Preview agents, tasks, activity count before org deletion |
| `DELETE /api/orgs/{id}` | Selective delete with optional `retain_agent_ids` / `retain_task_ids` body |
| `GET /api/dashboard/layout` | Load saved Overview grid layout (Redis) |
| `PUT /api/dashboard/layout` | Save Overview grid layout |
| `POST /api/repairs` | Create manual repair ticket (AI Agent or Admin pipeline) |
| `GET /api/activity` | Recent audit-log rows (filterable: direction, limit) |
| `POST /api/tools/wizard/generate` | AI Wizard tool generation from interview answers |

## Model Selection

Uses the complexity ladder from `bootstrap/model-selection-reference.md`:

- **Simple tasks** (typos, formatting): SWE-1.5 (Free)
- **Moderate tasks** (multi-file edits): GPT-5 Low
- **Complex tasks** (refactoring, architecture): GPT-5 Med / Claude Sonnet
- **Expert tasks** (security audit, deep debug): Claude Sonnet Thinking / GPT-5 High
- **Frontier tasks** (threat modeling): Claude Opus Thinking
