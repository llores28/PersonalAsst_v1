# PersonalAsst — Agentic AI Personal Assistant

A self-improving, multi-agent Personal Assistant that runs in Docker and talks through Telegram. It manages your Google Workspace, creates its own tools, remembers your preferences, schedules tasks, and gets smarter over time.

---

## Features

- **Telegram Chat** — Natural language + voice messages. No app to install.
- **Google Workspace** — Gmail, Calendar, Drive, Docs, Sheets via MCP integration.
- **Persistent Memory** — Remembers your preferences, past conversations, and workflows (Mem0 + Qdrant).
- **Self-Improving** — Reflector scores every interaction; Curator runs weekly optimization. Persona evolves.
- **Dynamic Tool Creation** — Ask it to build a new CLI tool and it generates, tests, and registers it automatically.
- **Task Scheduling** — "Remind me every Monday at 9am" — recurring jobs with natural language.
- **Safe by Design** — Input/output guardrails, sandboxed tool execution, cost caps, user allowlist.
- **Fully Self-Hosted** — All databases run in Docker. Zero SaaS calls for data storage.

## Architecture

```
Telegram Bot (aiogram 3.x)
  → Orchestrator Agent (OpenAI Agents SDK, GPT-5.4-mine)
    → Email Agent        (Gmail via MCP)
    → Calendar Agent     (Google Calendar via MCP)
    → Drive Agent        (Google Drive via MCP)
    → Memory Agent       (Mem0 — recall / store / forget)
    → Scheduler Agent    (APScheduler — cron / interval / one-shot)
    → Tool Factory Agent (generates CLI tools on demand)
    → Reflector Agent    (post-interaction quality scoring)
    → Curator Agent      (weekly self-improvement)
    → Safety Agent       (input injection + output PII guardrails)
```

**Docker Compose stack:** App + PostgreSQL 17 + Qdrant + Redis 7 + Google Workspace MCP

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

# 3. Build & Start
docker compose build
docker compose up -d

# 4. Chat
# Open Telegram → send /start to your bot
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Initial setup |
| `/help` | Show all commands and examples |
| `/connect google` | Connect Google Workspace (Gmail, Calendar, Drive) |
| `/persona` | View/edit assistant personality (name, style, traits) |
| `/memory` | See what the assistant remembers about you |
| `/forget <topic>` | Delete memories matching a topic |
| `/tools` | List registered tools |
| `/schedules` | List active scheduled tasks |
| `/stats` | Usage dashboard (cost, requests, tools, memory) |
| `/cancel <id>` | Cancel a scheduled task |

Or just chat naturally:
- "What's on my calendar today?"
- "Send an email to Sarah about the project update"
- "Remind me every Monday at 9am to review my goals"
- "Remember that I prefer morning meetings"
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
| Scheduling | APScheduler 4.x |
| Deployment | Docker Compose |

## Project Structure

```
src/
├── main.py                     # Entry point
├── settings.py                 # Config from .env (Pydantic)
├── bot/                        # Telegram handlers, voice transcription
├── agents/                     # 10 agent definitions
│   ├── orchestrator.py         # Main triage + routing
│   ├── email_agent.py          # Gmail specialist
│   ├── calendar_agent.py       # Google Calendar specialist
│   ├── drive_agent.py          # Google Drive specialist
│   ├── memory_agent.py         # Mem0 memory management
│   ├── scheduler_agent.py      # Task scheduling
│   ├── tool_factory_agent.py   # Dynamic tool creation
│   ├── reflector_agent.py      # Quality scoring (ACE)
│   ├── curator_agent.py        # Weekly self-improvement
│   └── safety_agent.py         # Guardrails
├── memory/                     # Mem0 client, Redis sessions, persona
├── tools/                      # Tool registry, sandbox, manifest schema
├── scheduler/                  # APScheduler engine, job callables, backup
├── integrations/               # Google Workspace MCP client
└── db/                         # SQLAlchemy models, Alembic migrations
config/                         # Persona, safety policies, tool tiers (YAML)
tools/                          # Dynamic CLI tools (hot-reloaded)
tests/                          # 7 test files, 80+ test cases
docs/                           # Developer guide, runbook, user guide, PRD
```

## Key Design Decisions

- **CLI-first tool creation** — Generated tools are standalone argparse scripts, not MCP servers. Simpler, testable, debuggable.
- **Single async process** — Bot, agents, scheduler all run in one Python process. No IPC overhead.
- **Handoff only for Tool Factory** — All other agents use `as_tool()` for bounded subtasks.
- **Redis for active conversations** — 30-min TTL, auto-archived to Mem0 episodic memory.
- **Tell user on error** — No silent retries. User stays in control.

## Security

- Telegram user ID allowlist (DB-backed, owner manages via `/allow` and `/revoke`)
- Input guardrail: prompt injection detection (pattern + LLM)
- Output guardrail: PII pattern detection (SSN, credit cards)
- Tool sandbox: empty environment, timeout, static analysis
- Cost caps: daily and monthly limits with 80% alerts
- Secrets: `.env` only, never in code or logs
- Docker: non-root user, resource limits

## Documentation

- [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) — Architecture, setup, adding agents/tools
- [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) — End-user guide for Telegram
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — Operations, troubleshooting, monitoring
- [`docs/PRD.md`](docs/PRD.md) — Product requirements with acceptance criteria
- [`PRD_PersonalAssistant.md`](PRD_PersonalAssistant.md) — Detailed build spec (schemas, decisions)
- [`RESEARCH_PersonalAssistant.md`](RESEARCH_PersonalAssistant.md) — Deep research report

## License

Private repository. All rights reserved.
