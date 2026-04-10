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
- **Dynamic Tool Creation** — Ask it to build a new CLI tool and it generates, tests, and registers it automatically.
- **Task Scheduling** — "Remind me every Monday at 9am" or "Remind me today at 3pm" — recurring and one-shot jobs with natural language.
- **AI-Guided Skill Creation** — Create custom skills via Telegram interview or Dashboard editor. Skills use declarative SKILL.md format with routing hints for natural language matching.
- **Filesystem-Based Skills** — Drop a SKILL.md file in `user_skills/` and hot-reload without restart. Version controlled, portable, shareable.
- **Safe by Design** — Context-aware input/output guardrails, sandboxed tool execution, cost caps, user allowlist.
- **Self-Healing Diagnostics** — Repair agent inspects the repo read-only, generates repair plans, and can apply approved patches after security verification.
- **Stale Session Recovery** — Automatic detection and clearing of corrupt SDK sessions with transparent retry.
- **Fully Self-Hosted** — All databases run in Docker. Zero SaaS calls for data storage.

## Architecture

```
Telegram Bot (aiogram 3.x)
  → Orchestrator (Office Organizer — dynamic complexity routing)
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
    ├── Repair Agent        (Handoff — read-only diagnostics, no codebase access)
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

# 3. Build & Start
docker compose build
docker compose up -d

# 3-a. Rebuild
docker compose down & docker compose build & docker compose up -d

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
├── agents/                     # 14 agent definitions
│   ├── orchestrator.py         # Office organizer + complexity routing
│   ├── persona_mode.py         # Persona template + prompt assembly (canonical)
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
│   ├── repair_agent.py         # Self-healing (Handoff, read-only)
│   ├── persona_interview_agent.py # Structured 3-session personality interview
│   └── safety_agent.py         # Input/output guardrails
├── skills/                     # Unified skill registry (10 built-in + dynamic)
│   ├── definition.py           # SkillDefinition contract with progressive disclosure
│   ├── registry.py             # SkillRegistry — dependency resolution, activate/deactivate
│   ├── loader.py               # SKILL.md parser for filesystem skills
│   ├── validation.py           # On-demand skill testing (routing confidence)
│   ├── google_workspace.py     # 8 Google Workspace skill builders
│   ├── internal.py             # Memory + Scheduler skill builders
│   └── dynamic.py              # CLI/function tool skill builder
├── agents/
│   ├── skill_factory_agent.py  # AI-guided skill creation (interview → SKILL.md)
│   └── ...
├── user_skills/                # User-created filesystem skills (hot-reloaded)
├── memory/                     # Mem0 (dedup + access tracking), Redis, persona
├── models/                     # Model catalog + complexity-aware routing
├── tools/                      # Tool registry, sandbox, manifest schema
├── scheduler/                  # APScheduler engine, job callables, backup
├── integrations/               # Google Workspace MCP client
└── db/                         # SQLAlchemy models, Alembic migrations
config/                         # Persona, safety policies, tool tiers (YAML)
tools/                          # Dynamic CLI tools (hot-reloaded)
tests/                          # 20 test files, 493+ test cases
docs/                           # Developer guide, runbook, user guide, PRD, ADRs
```

## Key Design Decisions

- **All skills flattened to direct tools** — Zero agent wrappers. Every Google Workspace and internal skill uses direct `function_tool` closures bound at creation time. Single LLM call per request (no nested agent reasoning).
- **Impl-function pattern for bound tools** — Scheduler (and all bound tools) use plain `_*_impl` async functions for core logic. `@function_tool` wrappers and bound closures both delegate to these. Prevents `FunctionTool object is not callable` errors.
- **Office Organizer persona** — The orchestrator is an expert office organizer with explicit domain boundaries and disambiguation rules for all 10 skill domains.
- **SDK RedisSession for conversation memory** — OpenAI Agents SDK `RedisSession` stores real conversation turns (user, assistant, tool calls). Session history filtered to exclude stale `function_call`/`function_call_output` items. Graceful degradation if Redis fails.
- **Context-aware PII guardrails** — Output guardrail receives user message via `Runner.run(context={...})`. Two-layer check: (1) context-aware email allowance, (2) output-marker fallback. Prevents false positives when user explicitly requests email operations.
- **Prompt cache optimization** — Static content (skills, rules, routing) placed first in the system prompt; dynamic content (user identity, datetime, connected email) placed last. Maximizes OpenAI prompt caching (research: 41-80% cost reduction).
- **Dynamic complexity routing** — Lightweight heuristic classifier routes simple reads to cheaper models (nano/mini) and complex multi-step requests to more capable models (standard/pro). No LLM call for classification.
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
- [`PRD_PersonalAssistant.md`](PRD_PersonalAssistant.md) — Detailed build spec (schemas, decisions)
- [`RESEARCH_PersonalAssistant.md`](RESEARCH_PersonalAssistant.md) — Deep research report
- `docs/ADR-*.md` — 13 Architecture Decision Records documenting key choices and fixes

## License

Private repository. All rights reserved.
