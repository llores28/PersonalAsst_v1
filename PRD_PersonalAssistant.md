# PRD — Agentic AI Personal Assistant

> **Version:** 1.0  
> **Date:** March 16, 2026  
> **Companion:** `RESEARCH_PersonalAssistant.md` (research & gaps analysis)  
> **Purpose:** Actionable build specification — every gap from the research is resolved here with concrete schemas, decisions, contracts, and acceptance criteria.

---

## Table of Contents

1. [Product Vision & Success Criteria](#1-product-vision--success-criteria)
2. [Hard Constraints](#2-hard-constraints)
3. [Architectural Decisions](#3-architectural-decisions)
4. [Project Structure](#4-project-structure)
5. [Database Schema](#5-database-schema)
6. [Configuration Specification](#6-configuration-specification)
7. [Agent Definitions & Routing](#7-agent-definitions--routing)
8. [Tool System — CLI-First](#8-tool-system--cli-first)
9. [Memory System](#9-memory-system)
10. [Persona System](#10-persona-system)
11. [Messaging Layer — Telegram](#11-messaging-layer--telegram)
12. [Scheduling System](#12-scheduling-system)
13. [Google Workspace Integration](#13-google-workspace-integration)
14. [Security & Guardrails](#14-security--guardrails)
15. [Error Handling Strategy](#15-error-handling-strategy)
16. [Phased Build Plan (Corrected)](#16-phased-build-plan-corrected)
17. [Acceptance Criteria per Phase](#17-acceptance-criteria-per-phase)
18. [Gaps Cross-Reference](#18-gaps-cross-reference)

---

## 1. Product Vision & Success Criteria

### Vision

A single-user, Dockerized personal assistant that talks through Telegram, manages Google Workspace, creates its own tools, remembers everything, and gets better over time — safe enough for someone who never touches a terminal.

### Success Criteria (MVP — end of Phase 3)

| # | Criterion | Measurable |
|---|-----------|-----------|
| S1 | User can chat via Telegram and get coherent, persona-consistent replies | 95% of responses in-character |
| S2 | User can ask "What's on my calendar today?" and get a correct answer | Matches Google Calendar API data |
| S3 | User can say "Send an email to X about Y" and it drafts, confirms, sends | Email arrives in recipient inbox |
| S4 | Assistant remembers user preferences across sessions | Recall accuracy > 90% on preference queries |
| S5 | Unauthorized Telegram users get rejected | 100% block rate for non-allowlisted IDs |
| S6 | `docker compose up -d` starts the entire stack with zero manual steps after `.env` is configured | Boot-to-ready < 60 seconds |

### Success Criteria (Full — end of Phase 6)

| # | Criterion | Measurable |
|---|-----------|-----------|
| S7 | User can request a new tool and the assistant creates, tests, and registers it | Tool usable within 2 minutes of request |
| S8 | Scheduled tasks survive container restarts | 100% job persistence |
| S9 | Daily API spend never exceeds configured cap | Zero overspend incidents |
| S10 | Assistant persona evolves based on feedback | Measurable preference drift in memory over 30 days |

---

## 2. Hard Constraints

These are non-negotiable and override any research recommendation:

| ID | Constraint | Rationale |
|----|-----------|-----------|
| HC-1 | **All databases self-hosted in Docker** — no SaaS DB/memory API calls | User data sovereignty; offline-capable data layer |
| HC-2 | **CLI-first tool creation** — MCP only as fallback | Simplicity, debuggability, testability |
| HC-3 | **OpenAI as sole LLM provider** | GPT-5.x models; single billing; SDK native support |
| HC-4 | **Responses API only** — no Assistants API | Assistants deprecated Aug 2026 |
| HC-5 | **Single-user system** | No multi-tenancy; simplifies auth, memory, persona |
| HC-6 | **Telegram as primary UX** | WhatsApp/Discord are optional future adapters |
| HC-7 | **Python 3.12+ only** | OpenAI Agents SDK requirement; async-native |
| HC-8 | **Non-technical user** must never need CLI access | All management via Telegram commands |

---

## 3. Architectural Decisions

> Resolves gaps: **B1, B2, B3, B4, B5, B6**

### AD-1: Single Process, Async Event Loop

**Decision:** One Python process runs everything — Telegram bot, agent orchestrator, scheduler, memory.  
**Why:** Simplest deployment; OpenAI Agents SDK and aiogram are both async; APScheduler 4.x is async-native. No IPC overhead. Docker resource limits handle containment.  
**Trade-off:** A hung agent blocks the event loop. Mitigation: all agent calls wrapped in `asyncio.wait_for(timeout=120)`.

### AD-2: Conversation State in Redis with PostgreSQL Archival

**Decision:** Active conversation context lives in Redis (TTL: 30 minutes of inactivity). On TTL expiry, conversation summary is archived to PostgreSQL episodic memory via Mem0.  
**Why:** Redis is fast for active sessions. PostgreSQL is durable for long-term recall. 30-min window matches typical chat session length.

```
Active conversation → Redis (key: conv:{user_id}:{session_id}, TTL: 1800s)
On expire → Mem0 summarizes → PostgreSQL episodic_memories table
```

### AD-3: Agent Routing — Handoff vs Agent.as_tool()

**Decision per agent:**

| Agent | Pattern | Rationale |
|-------|---------|-----------|
| **Orchestrator** | Entry point | Always the first agent; owns conversation |
| **Email Agent** | `Agent.as_tool()` | Bounded task: draft/send email, return result to orchestrator |
| **Calendar Agent** | `Agent.as_tool()` | Bounded task: query/create event, return result |
| **Drive Agent** | `Agent.as_tool()` | Bounded task: search/upload/download, return result |
| **Web Search Agent** | `Agent.as_tool()` | Bounded task: search, return results |
| **Scheduler Agent** | `Agent.as_tool()` | Bounded task: create/list/cancel jobs, return confirmation |
| **Memory Agent** | `Agent.as_tool()` | Bounded task: recall/forget, return result |
| **Tool Factory Agent** | `Handoff` | Extended interaction: may need clarification, multi-step generation |
| **Code Execution Agent** | `Agent.as_tool()` | Bounded: run code, return output |

**Rule:** Use `Handoff` only when the specialist may need multi-turn conversation with the user. Otherwise, `Agent.as_tool()` always.

### AD-4: Tool Hot-Reload via Filesystem Watch

**Decision:** The orchestrator watches `src/tools/plugins/` directory for changes. When a new `manifest.json` appears, it loads the tool's `function_tool` wrapper and adds it to the agent's tool list.  
**Implementation:** `watchdog` library on the `src/tools/plugins/` Docker volume. On change → validate manifest → import wrapper → update orchestrator's tool registry.

### AD-5: Error Propagation — Tell User, Don't Retry Silently

**Decision:** When a specialist agent fails:
1. Log the full error to PostgreSQL audit table
2. Return a user-friendly error message to the orchestrator
3. Orchestrator relays to user with a "Retry?" inline button
4. No automatic retry (user is in control)

**Exception:** Transient network errors (timeout, 503) get 1 automatic retry with exponential backoff before surfacing to user.

### AD-6: Concurrent Messages — Sequential Queue

**Decision:** Messages from a single user are processed sequentially via an `asyncio.Queue` per user. This prevents race conditions on conversation state and memory.  
**Why:** Single-user system; parallelism adds complexity with minimal benefit. User perceives responses as fast because each request is async internally.

---

## 4. Project Structure

```
PersonalAsst/
├── docker-compose.yml
├── Dockerfile
├── .env.example                    # All required env vars (resolves D1)
├── requirements.txt
├── README.md
│
├── src/
│   ├── __init__.py
│   ├── main.py                     # Entry point: starts bot, scheduler, tool watcher
│   ├── settings.py                 # Pydantic Settings from env vars
│   │
│   ├── bot/                        # Telegram layer
│   │   ├── __init__.py
│   │   ├── handlers.py             # Message/command handlers
│   │   ├── router.py               # MessageRouter (resolves A4)
│   │   ├── formatters.py           # Platform-specific output formatting
│   │   └── keyboards.py            # Inline keyboards, approval buttons
│   │
│   ├── agents/                     # Agent definitions
│   │   ├── __init__.py
│   │   ├── orchestrator.py         # Triage/persona agent
│   │   ├── email_agent.py
│   │   ├── calendar_agent.py
│   │   ├── drive_agent.py
│   │   ├── search_agent.py
│   │   ├── scheduler_agent.py
│   │   ├── memory_agent.py
│   │   ├── tool_factory_agent.py
│   │   └── safety_agent.py         # Guardrail definitions
│   │
│   ├── tools/                      # Tool system
│   │   ├── __init__.py
│   │   ├── registry.py             # Tool discovery, hot-reload (resolves B2)
│   │   ├── sandbox.py              # Subprocess sandboxing
│   │   └── manifest.py             # Manifest schema (resolves A2)
│   │
│   ├── memory/                     # Memory layer
│   │   ├── __init__.py
│   │   ├── mem0_client.py          # Mem0 wrapper (self-hosted config)
│   │   ├── conversation.py         # Redis session management (resolves B3)
│   │   └── persona.py              # Persona CRUD (resolves A3)
│   │
│   ├── scheduler/                  # Task scheduling
│   │   ├── __init__.py
│   │   ├── engine.py               # APScheduler setup
│   │   └── jobs.py                 # Job callables (resolves A5)
│   │
│   ├── integrations/               # External service connectors
│   │   ├── __init__.py
│   │   └── workspace_mcp.py        # Google Workspace MCP client
│   │
│   └── db/                         # Database
│       ├── __init__.py
│       ├── models.py               # SQLAlchemy models (resolves A1)
│       └── migrations/             # Alembic migrations
│
├── config/                         # Runtime config (resolves D2)
│   ├── persona_default.yaml        # Default persona settings
│   ├── safety_policies.yaml        # Declarative safety rules (resolves C5)
│   └── tool_tiers.yaml             # Google Workspace tool tier config
│
│   ├── plugins/                    # Dynamic tool plugins (bind-mounted volume)
│   │   └── _example/
│   │       ├── cli.py              # Example CLI tool
│   │       ├── tool.py             # function_tool wrapper
│       └── manifest.json           # Tool manifest
│
└── tests/
    ├── test_orchestrator.py
    ├── test_tools.py
    ├── test_memory.py
    ├── test_scheduler.py
    └── test_guardrails.py
```

---

## 5. Database Schema

> Resolves gap: **A1**

### PostgreSQL Tables

```sql
-- Core identity
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,
    display_name    VARCHAR(100),
    timezone        VARCHAR(50) DEFAULT 'UTC',           -- Resolves D3
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    is_owner        BOOLEAN DEFAULT FALSE
);

-- Persona versioning
CREATE TABLE persona_versions (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    version         INTEGER NOT NULL DEFAULT 1,
    assistant_name  VARCHAR(50) NOT NULL DEFAULT 'Atlas',
    personality     JSONB NOT NULL,                       -- {traits, style, proactivity}
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    change_reason   TEXT                                  -- Why this version was created
);

-- Audit log (every interaction)
CREATE TABLE audit_log (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    direction       VARCHAR(10) NOT NULL,                 -- 'inbound' | 'outbound'
    platform        VARCHAR(20) NOT NULL DEFAULT 'telegram',
    message_text    TEXT,
    agent_name      VARCHAR(50),
    tools_used      JSONB,                                -- [{tool_name, args, result_summary}]
    model_used      VARCHAR(50),
    token_count     INTEGER,
    cost_usd        NUMERIC(10, 6),
    error           TEXT,
    duration_ms     INTEGER
);

-- Daily cost tracking
CREATE TABLE daily_costs (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL DEFAULT CURRENT_DATE,
    user_id         INTEGER REFERENCES users(id),
    total_tokens    INTEGER DEFAULT 0,
    total_cost_usd  NUMERIC(10, 4) DEFAULT 0,
    request_count   INTEGER DEFAULT 0,
    UNIQUE(date, user_id)
);

-- Tool registry (persistent across restarts)
CREATE TABLE tools (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) UNIQUE NOT NULL,
    tool_type       VARCHAR(20) NOT NULL,                 -- 'cli' | 'function' | 'mcp'
    description     TEXT NOT NULL,
    manifest_path   VARCHAR(500) NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    created_by      VARCHAR(50) DEFAULT 'tool_factory',   -- agent or 'manual'
    last_used_at    TIMESTAMPTZ,
    use_count       INTEGER DEFAULT 0
);

-- Scheduled jobs (APScheduler uses its own table, this is metadata)
CREATE TABLE scheduled_tasks (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    apscheduler_id  VARCHAR(200) UNIQUE NOT NULL,
    description     TEXT NOT NULL,                        -- Human-readable
    natural_lang    TEXT,                                 -- Original user request
    trigger_type    VARCHAR(20) NOT NULL,                 -- 'cron' | 'interval' | 'date'
    trigger_config  JSONB NOT NULL,                       -- {day_of_week, hour, minute...}
    job_function    VARCHAR(200) NOT NULL,                -- Dotted path to callable
    job_args        JSONB,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_run_at     TIMESTAMPTZ,
    next_run_at     TIMESTAMPTZ
);

-- Allowlist for authorized users
CREATE TABLE allowed_users (
    telegram_id     BIGINT PRIMARY KEY,
    added_by        BIGINT,                               -- Telegram ID of who added them
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    role            VARCHAR(20) DEFAULT 'user'            -- 'owner' | 'user'
);
```

### Initial Migration

The owner's Telegram ID is seeded from `.env`:

```sql
INSERT INTO allowed_users (telegram_id, role) 
VALUES (${OWNER_TELEGRAM_ID}, 'owner')
ON CONFLICT DO NOTHING;
```

---

## 6. Configuration Specification

> Resolves gaps: **D1, D2, C1, D3**

### .env.example (Complete)

```bash
# ──────────────────────────────────────
# REQUIRED — Application will not start without these
# ──────────────────────────────────────

# OpenAI
OPENAI_API_KEY=sk-...                         # Your OpenAI API key

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC...              # From @BotFather
OWNER_TELEGRAM_ID=123456789                   # Your Telegram numeric user ID (run /myid bot)

# Database
DB_PASSWORD=change_me_to_random_string        # PostgreSQL password

# ──────────────────────────────────────
# OPTIONAL — Defaults provided
# ──────────────────────────────────────

# Google Workspace (required for Phase 2+)
GOOGLE_OAUTH_CLIENT_ID=                       # Google Cloud Console → OAuth 2.0 Client ID
GOOGLE_OAUTH_CLIENT_SECRET=                   # Google Cloud Console → OAuth 2.0 Client Secret

# Web Search (required for Phase 6, or use built-in WebSearchTool)
TAVILY_API_KEY=                               # https://tavily.com — optional

# Models (override defaults)
MODEL_ORCHESTRATOR=gpt-5.4-mine
MODEL_CODE_GEN=gpt-5.3-codex
MODEL_FAST=gpt-4.1-nano
MODEL_GENERAL=gpt-5.4

# Cost Control
DAILY_COST_CAP_USD=5.00                       # Max daily API spend (resolves C2)
MONTHLY_COST_CAP_USD=100.00                   # Max monthly API spend

# Persona Defaults
DEFAULT_ASSISTANT_NAME=Atlas
DEFAULT_PERSONA_STYLE=friendly                # friendly | professional | casual | brief

# User Timezone
DEFAULT_TIMEZONE=America/New_York             # IANA timezone (resolves D3)

# Security
MAX_TOOL_CALLS_PER_REQUEST=20                 # Runaway agent protection
AGENT_TIMEOUT_SECONDS=120                     # Max time per agent invocation
TOOL_SUBPROCESS_TIMEOUT=30                    # Max time for CLI tool execution
```

### config/persona_default.yaml

```yaml
assistant_name: Atlas
personality:
  traits:
    - helpful
    - proactive
    - concise
  style: friendly
  proactivity: medium        # low | medium | high
  verbosity: normal          # brief | normal | detailed
communication:
  greeting: "Hey {user_name}! How can I help?"
  error_prefix: "Hmm, something went wrong"
  confirmation_style: inline_buttons    # inline_buttons | text_prompt
rules:
  always_confirm_destructive: true
  max_proactive_suggestions_per_day: 5
  ask_feedback_frequency: every_10th     # never | every_5th | every_10th | always
```

### config/safety_policies.yaml

> Resolves gap: **C5**

```yaml
version: 1
policies:
  - name: no_financial_transactions
    description: "Never execute financial transactions without explicit user approval"
    trigger: tool_call
    match:
      tool_names: ["*payment*", "*transfer*", "*purchase*"]
    action: require_approval
    
  - name: no_bulk_delete
    description: "Block bulk delete operations"
    trigger: tool_call
    match:
      args_contain: ["delete_all", "rm -rf", "DROP"]
    action: block
    message: "Bulk delete operations are not allowed."
    
  - name: no_external_data_sharing
    description: "Don't send user data to unknown external APIs"
    trigger: tool_call
    match:
      tool_types: ["cli", "mcp"]
      network_calls_to: ["!googleapis.com", "!api.openai.com", "!api.tavily.com"]
    action: require_approval
    
  - name: pii_redaction
    description: "Redact PII from logs"
    trigger: output
    match:
      patterns: ["\\b\\d{3}-\\d{2}-\\d{4}\\b", "\\b\\d{16}\\b"]    # SSN, CC
    action: redact
    
  - name: rate_limit
    description: "Max requests per minute"
    trigger: input
    match:
      rate: 30/minute
    action: throttle
    message: "You're sending messages too quickly. Please wait a moment."
```

### config/tool_tiers.yaml

```yaml
google_workspace:
  tier: core                  # core | extended | complete
  # core: Gmail read/send, Calendar read/create, Drive search/download
  # extended: + Docs edit, Sheets write, Contacts
  # complete: + Slides, Forms, Chat, Apps Script
```

---

## 7. Agent Definitions & Routing

> Resolves gaps: **B4, E1, E2**

### Orchestrator Agent

```python
# src/agents/orchestrator.py
from agents import Agent, Runner, WebSearchTool

def create_orchestrator(
    persona_prompt: str,
    specialist_agents: dict,
    guardrails: list,
    tools: list,
) -> Agent:
    return Agent(
        name="PersonalAssistant",
        instructions=persona_prompt,
        model=settings.MODEL_ORCHESTRATOR,
        tools=[
            # Phase 1: built-in tools only
            WebSearchTool(),           # Moved to Phase 1 (resolves E1)
            *tools,                    # Dynamically loaded tools
            # Specialist agents as tools (bounded tasks)
            specialist_agents["email"].as_tool(
                tool_name="manage_email",
                tool_description="Read, search, draft, send, reply to emails via Gmail",
            ),
            specialist_agents["calendar"].as_tool(
                tool_name="manage_calendar",
                tool_description="View, create, update, delete Google Calendar events",
            ),
            specialist_agents["drive"].as_tool(
                tool_name="manage_drive",
                tool_description="Search, upload, download, share files on Google Drive",
            ),
            specialist_agents["scheduler"].as_tool(
                tool_name="manage_schedules",
                tool_description="Create, list, pause, cancel recurring tasks and reminders",
            ),
            specialist_agents["memory"].as_tool(
                tool_name="manage_memory",
                tool_description="Recall what you know about the user, or forget something",
            ),
        ],
        handoffs=[
            # Only Tool Factory gets a handoff (multi-turn interaction)
            specialist_agents["tool_factory"],
        ],
        input_guardrails=guardrails["input"],
        output_guardrails=guardrails["output"],
    )
```

### Persona Prompt Loading (Phase-Aware)

> Resolves gap: **E2** — Phase 1 uses config file; Phase 3+ uses Mem0

```python
# src/memory/persona.py
async def load_persona_prompt(user_id: int, phase: int = 1) -> str:
    """Load persona prompt. Phase 1 uses YAML config; Phase 3+ uses Mem0."""
    
    if phase < 3:
        # Phase 1-2: Static persona from config file
        config = load_yaml("config/persona_default.yaml")
        user = await get_user(user_id)
        return PERSONA_TEMPLATE.format(
            name=config["assistant_name"],
            user_name=user.display_name or "there",
            personality_traits=", ".join(config["personality"]["traits"]),
            communication_style=config["personality"]["style"],
            user_preferences="(learning your preferences...)",
            procedural_memories="(will learn your workflows over time)",
            recent_episodic_memories="(new conversation)",
            safety_rules=load_safety_rules(),
        )
    else:
        # Phase 3+: Dynamic persona from Mem0
        memories = await mem0_client.search(
            "persona preferences communication style",
            user_id=str(user_id),
        )
        persona_version = await get_active_persona(user_id)
        return PERSONA_TEMPLATE.format(
            name=persona_version.assistant_name,
            user_name=user.display_name,
            personality_traits=persona_version.personality["traits"],
            communication_style=persona_version.personality["style"],
            user_preferences=format_memories(memories),
            procedural_memories=await load_procedural_memories(user_id),
            recent_episodic_memories=await load_recent_episodes(user_id, limit=5),
            safety_rules=load_safety_rules(),
        )
```

---

## 8. Tool System — CLI-First

> Resolves gaps: **A2, B2, X1**

### Tool Manifest Schema

```json
{
  "$schema": "tool-manifest-v1",
  "name": "stock_checker",
  "version": "1.0.0",
  "description": "Check current stock prices for given ticker symbols",
  "type": "cli",
  "entrypoint": "cli.py",
  "wrapper": "tool.py",
  "parameters": {
    "symbols": {
      "type": "list[str]",
      "required": true,
      "description": "Stock ticker symbols (e.g., AAPL, GOOGL)"
    }
  },
  "output_format": "json",
  "timeout_seconds": 30,
  "requires_approval": false,
  "requires_network": true,
  "allowed_hosts": ["api.example.com"],
  "created_at": "2026-03-16T12:00:00Z",
  "created_by": "tool_factory"
}
```

### Tool Registry — Hot-Reload

```python
# src/tools/registry.py
class ToolRegistry:
    """Discovers tools from src/tools/plugins/ directory, hot-reloads on changes."""
    
    def __init__(self, tools_dir: Path):
        self.tools_dir = tools_dir
        self._tools: dict[str, FunctionTool] = {}
        self._observer = None
    
    async def load_all(self) -> list[FunctionTool]:
        """Scan plugins dir for manifest.json files, load all valid tools."""
        for manifest_path in self.tools_dir.glob("*/manifest.json"):
            await self._load_tool(manifest_path)
        return list(self._tools.values())
    
    async def start_watching(self):
        """Watch for new/changed tools (resolves B2)."""
        # Uses watchdog Observer on tools_dir
        # On new manifest.json → _load_tool() → notify orchestrator
    
    async def _load_tool(self, manifest_path: Path):
        """Validate manifest, import wrapper, register tool."""
        manifest = ToolManifest.model_validate_json(manifest_path.read_text())
        wrapper_path = manifest_path.parent / manifest.wrapper
        # Dynamic import of the function_tool wrapper
        module = importlib.import_module_from_path(wrapper_path)
        self._tools[manifest.name] = module.tool_function
```

### Subprocess Blocked-Import Clarification

> Resolves contradiction: **X1**

```python
# src/tools/sandbox.py
class ToolFactoryGuardrails:
    """These blocks apply ONLY to code generated by Tool Factory.
    The function_tool wrapper in the agent process is ALLOWED to use subprocess.
    Generated CLI tools must NOT import subprocess — they are the subprocess."""
    
    # Blocked in GENERATED tool code (cli.py files)
    BLOCKED_IMPORTS_IN_GENERATED = ["subprocess", "shutil", "ctypes", "pickle", "os.system"]
    
    # Allowed in wrapper code (tool.py files) — these call subprocess.run()
    WRAPPER_ALLOWED_IMPORTS = ["subprocess", "json", "pathlib"]
```

---

## 9. Memory System

> Resolves gap: **B3**

### Conversation State Flow

```
User sends message
  │
  ├─ 1. Check Redis for active session: conv:{user_id}
  │     ├─ EXISTS → Load conversation history (last N turns)
  │     └─ NOT EXISTS → Create new session, load Mem0 context
  │
  ├─ 2. Append user message to Redis list
  │
  ├─ 3. Run orchestrator with context
  │
  ├─ 4. Append assistant response to Redis list
  │
  ├─ 5. Reset Redis TTL to 1800 seconds (30 min)
  │
  └─ 6. On TTL expire (Redis keyspace notification):
        ├─ Summarize conversation via LLM (gpt-4.1-nano)
        ├─ Store summary in Mem0 episodic memory
        └─ Log to audit_log table
```

### Redis Key Schema

```
conv:{user_id}              → LIST of message dicts (role, content, timestamp)
conv:{user_id}:meta         → HASH {session_id, started_at, turn_count}
approval:{user_id}:{req_id} → HASH {tool_name, args, status, expires_at}
rate:{user_id}              → Counter with 60s TTL (rate limiting)
```

---

## 10. Persona System

> Resolves gap: **A3, E3**

### Persona Update Rules

| Trigger | Action | Threshold |
|---------|--------|-----------|
| User says `/persona style casual` | Immediate update | N/A — explicit command |
| User gives negative feedback 3x in a row | Reflector agent proposes adjustment | 3 consecutive negatives |
| Weekly curator review | Analyze last 7 days of interactions | Auto-run every Sunday 2am |
| Curator confidence score | Only apply changes if confidence > 0.7 | 0.7 threshold |

### Persona Version Storage

Every persona change creates a new `persona_versions` row with `change_reason`. The previous version's `is_active` is set to `false`. This enables rollback via `/persona undo`.

### ACE Self-Improvement Acceptance Criteria

> Resolves gap: **E3**

```yaml
reflector:
  runs_after: every_interaction
  metrics:
    - user_satisfaction: "Did user say thanks / positive reaction / use result?"
    - task_completion: "Did the agent complete what was asked?"
    - error_rate: "Any tool errors or guardrail triggers?"
  threshold_for_action: 
    negative_streak: 3          # 3 bad interactions → flag for review
    satisfaction_below: 0.6     # Over 10-interaction window

curator:
  runs: weekly (Sunday 2am user timezone)
  actions:
    - analyze_interaction_patterns
    - propose_persona_adjustments (if confidence > 0.7)
    - update_procedural_memories (new workflows learned)
    - prune_stale_memories (older than 90 days, low relevance score)
  requires_user_approval: false   # Runs silently; user can review via /memory
```

---

## 11. Messaging Layer — Telegram

> Resolves gap: **A4**

### Normalized Message Format

```python
# src/bot/router.py
from pydantic import BaseModel
from datetime import datetime

class NormalizedMessage(BaseModel):
    """Platform-agnostic message format (resolves A4)."""
    user_id: int                          # Internal user ID
    platform_user_id: int                 # Telegram/Discord/WhatsApp ID
    platform: str                         # "telegram" | "discord" | "whatsapp"
    text: str                             # Message text (transcribed if voice)
    attachments: list[Attachment] = []    # Files, images, voice
    reply_to_message_id: str | None = None
    timestamp: datetime
    is_command: bool = False              # Starts with /
    command: str | None = None            # e.g., "persona", "help"
    command_args: str | None = None       # e.g., "style casual"

class Attachment(BaseModel):
    type: str                             # "file" | "image" | "voice" | "document"
    file_id: str                          # Platform file ID
    file_name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None

class FormattedResponse(BaseModel):
    """Response ready for platform-specific formatting."""
    text: str
    parse_mode: str = "MarkdownV2"
    reply_markup: dict | None = None      # Inline keyboards
    files: list[dict] = []                # Files to send
```

### Message Processing Pipeline

```python
async def handle_message(message: TelegramMessage):
    # 1. Auth check
    if not await is_allowed(message.from_user.id):
        return  # Silent reject (no response to unauthorized users)
    
    # 2. Rate limit check
    if await is_rate_limited(message.from_user.id):
        await message.answer("Please wait a moment...")
        return
    
    # 3. Cost cap check
    if await is_cost_capped(message.from_user.id):
        await message.answer("Daily usage limit reached. Resets at midnight.")
        return
    
    # 4. Normalize
    normalized = await normalize_telegram(message)
    
    # 5. Command routing (fast path)
    if normalized.is_command:
        return await handle_command(normalized)
    
    # 6. Queue for sequential processing
    await user_queues[normalized.user_id].put(normalized)
```

---

## 12. Scheduling System

> Resolves gap: **A5**

### Job Callable Signatures

```python
# src/scheduler/jobs.py

async def send_reminder(user_id: int, message: str):
    """Send a text reminder to the user."""
    bot = get_bot_instance()
    telegram_id = await get_telegram_id(user_id)
    await bot.send_message(telegram_id, message)

async def run_agent_task(user_id: int, agent_name: str, prompt: str):
    """Run a specialist agent and send results to user."""
    result = await Runner.run(get_agent(agent_name), prompt)
    bot = get_bot_instance()
    telegram_id = await get_telegram_id(user_id)
    await bot.send_message(telegram_id, format_for_telegram(result.final_output))

async def summarize_new_emails(user_id: int):
    """Check for new emails and send summary."""
    await run_agent_task(
        user_id, "email",
        "Check for new unread emails since last check. Summarize the important ones."
    )

async def morning_brief(user_id: int):
    """Daily morning briefing: calendar + email + reminders."""
    await run_agent_task(
        user_id, "orchestrator",
        "Give me my morning brief: today's calendar events, important unread emails, and any pending tasks."
    )
```

### Job Error Handling

```python
async def safe_job_wrapper(job_func, *args, **kwargs):
    """All scheduled jobs are wrapped with error handling."""
    try:
        await asyncio.wait_for(job_func(*args, **kwargs), timeout=120)
    except asyncio.TimeoutError:
        await log_job_error(job_func.__name__, "Timeout after 120s")
    except Exception as e:
        await log_job_error(job_func.__name__, str(e))
        # Notify user only for critical job failures
        if is_critical_job(job_func.__name__):
            await notify_user_of_job_failure(args[0], job_func.__name__, str(e))
```

---

## 13. Google Workspace Integration

### OAuth Flow via Telegram

```
1. User: /connect google
2. Bot generates OAuth URL with state={user_id}
3. Bot sends clickable link to user
4. User clicks → Google consent screen → authorizes
5. Callback received by workspace-mcp container
6. Token stored in workspace_tokens Docker volume
7. Bot confirms: "✓ Google Workspace connected!"
```

### Graceful Degradation

If Google tokens expire mid-conversation:
1. Agent catches auth error
2. Returns to orchestrator: "Google access expired"
3. Orchestrator tells user: "I need to reconnect to Google. Click here: [OAuth link]"
4. No silent failures

---

## 14. Security & Guardrails

> Resolves gaps: **C1, C2, C3, C4**

### Allowlist Management (resolves C1)

```
Storage: PostgreSQL `allowed_users` table
Seeded from: OWNER_TELEGRAM_ID in .env (on first boot)
Management: Owner can add/remove via Telegram:
  /allow @username     → adds their Telegram ID
  /revoke @username    → removes access
  /users               → list allowed users
Only the owner (role='owner') can manage the allowlist.
```

### Cost Control (resolves C2)

```python
# Before every OpenAI API call:
async def check_cost_budget(user_id: int) -> bool:
    today = await get_daily_costs(user_id)
    if today.total_cost_usd >= settings.DAILY_COST_CAP_USD:
        return False  # Blocked
    return True

# After every OpenAI API call:
async def record_cost(user_id: int, tokens: int, cost: float):
    await update_daily_costs(user_id, tokens, cost)
    await insert_audit_log(...)
    
    # Alert at 80% of daily cap
    if today.total_cost_usd >= settings.DAILY_COST_CAP_USD * 0.8:
        await notify_user("You've used 80% of today's API budget.")
```

### Sandbox Isolation (resolves C3)

```python
# CLI tools run as subprocess in the SAME container but with restrictions:
SANDBOX_CONFIG = {
    "timeout": 30,                    # seconds
    "max_memory": "256M",             # via resource.setrlimit
    "network": True,                  # allowed (needed for API tools)
    "allowed_paths": ["/app/src/tools/plugins/"], # chroot-like restriction via validation
    "uid": 65534,                     # nobody user
    "env_whitelist": [],              # no inherited env vars (no API keys)
}
```

### OAuth Token Security (resolves C4)

- Tokens stored in Docker volume `workspace_tokens` (not in DB)
- Volume is not exposed to host by default
- Tokens auto-refresh; on theft, user re-authenticates via `/connect google`
- Future: encrypt at rest with `FERNET_KEY` from .env

---

## 15. Error Handling Strategy

> Resolves gap: **B5**

| Error Type | Detection | User Impact | Auto-Action |
|-----------|-----------|-------------|-------------|
| **Transient network error** | HTTP 503, timeout | None (invisible) | 1 retry with backoff, then surface |
| **API auth error** | HTTP 401/403 | "I lost access to X" | Prompt re-auth link |
| **Rate limit** | HTTP 429 | "I'm being rate limited, trying again shortly" | Backoff + retry |
| **Cost cap exceeded** | Budget check | "Daily limit reached" | Block until midnight |
| **Agent timeout** | asyncio.TimeoutError | "That took too long, let me try differently" | Log, no retry |
| **Tool execution error** | Non-zero exit code | "The tool failed: {stderr summary}" | Log, offer retry button |
| **Guardrail triggered** | TripwireTriggered exception | "I can't do that — {policy reason}" | Log, no retry |
| **Unknown error** | Catch-all Exception | "Something unexpected happened. I've logged it." | Log full traceback, notify owner |

---

## 16. Phased Build Plan (Corrected)

> Resolves gaps: **E1, E2, E4**

### Phase 1: Foundation (Weeks 1-3)

**Goal:** Telegram bot that responds with a persona, has guardrails, basic web search.

| Task | Depends On | Acceptance |
|------|-----------|------------|
| Docker Compose: PostgreSQL, Redis, Qdrant, app | — | `docker compose up -d` starts all services |
| Database schema + Alembic migrations | PostgreSQL | Tables created on first boot |
| Settings from .env via Pydantic | — | App fails fast on missing required vars |
| aiogram 3.x Telegram bot + message pipeline | — | Bot responds to `/start` |
| Message queue (per-user sequential) | Redis | Messages processed in order |
| Orchestrator agent with static persona (from YAML, not Mem0) | Settings | Persona-consistent responses |
| WebSearchTool integration | Orchestrator | "Search for X" returns web results |
| Input guardrail (safety check) | Orchestrator | Prompt injection blocked |
| Output guardrail (PII check) | Orchestrator | SSN/CC patterns redacted |
| User allowlist (from .env seed + DB) | PostgreSQL | Unauthorized users silently rejected |
| Cost tracking + daily cap | PostgreSQL | Cap blocks requests when exceeded |
| Audit logging | PostgreSQL | Every interaction logged |
| `/help`, `/persona`, `/cancel` commands | Bot | Commands respond correctly |

### Phase 2: Google Workspace (Weeks 4-5)

**Goal:** Email, Calendar, Drive working through Telegram.

| Task | Depends On | Acceptance |
|------|-----------|------------|
| Google Workspace MCP Server in Docker Compose | Phase 1 | Container starts, OAuth flow works |
| OAuth flow via Telegram link | Workspace MCP | User clicks link, authorizes, bot confirms |
| Email Agent (as_tool) | Workspace MCP | "Read my latest emails" returns summaries |
| Calendar Agent (as_tool) | Workspace MCP | "What's on my calendar today?" returns events |
| Drive Agent (as_tool) | Workspace MCP | "Find the budget spreadsheet" returns results |
| Approval flow for send/delete actions | Bot keyboards | "Send this email?" with Approve/Edit/Cancel |

### Phase 3: Memory & Intelligence (Weeks 6-7)

**Goal:** Persistent memory, persona evolution, self-improvement.

| Task | Depends On | Acceptance |
|------|-----------|------------|
| Mem0 integration (self-hosted: Qdrant + PostgreSQL) | Phase 1 infra | `memory.add()` and `memory.search()` work |
| Conversation → Redis session management | Redis | Sessions with 30-min TTL + archival |
| Episodic memory (conversation summaries) | Mem0 | "What did we talk about yesterday?" works |
| Semantic memory (user preferences) | Mem0 | "Remember I prefer mornings" → recalled later |
| Persona migration: YAML → Mem0-backed | Phase 1 persona | Dynamic persona loading from memory |
| Persona versioning + `/persona` management | PostgreSQL | `/persona style casual` creates new version |
| Memory Agent (as_tool) | Mem0 | `/memory` shows what assistant knows |
| `/forget` command | Mem0 | User can delete specific memories |
| Reflector agent (post-interaction quality check) | Mem0 | Quality scores logged per interaction |

### Phase 4: Scheduling & Automation (Week 8)

**Goal:** Recurring tasks, proactive notifications.

| Task | Depends On | Acceptance |
|------|-----------|------------|
| APScheduler 4.x with PostgreSQL job store | Phase 1 infra | Jobs persist across restarts |
| Scheduler Agent (as_tool) | APScheduler | "Remind me every Monday at 9am" creates job |
| Natural language → cron parsing | Orchestrator | "Every weekday at 8am" → correct cron |
| `/schedules`, `/cancel`, `/pause` commands | Bot + DB | User can manage all jobs via Telegram |
| Morning brief job | Scheduler + Email + Calendar | Daily summary at configured time |
| Timezone support | User DB record | Jobs fire at correct local time |

### Phase 5: Tool Factory (Weeks 9-10)

**Goal:** Assistant can create, test, and register new CLI tools.

| Task | Depends On | Acceptance |
|------|-----------|------------|
| Tool manifest schema + validation | — | Manifests validated against JSON schema |
| Tool registry with hot-reload | Filesystem watcher | New tool available without restart |
| Tool Factory Agent (Handoff) | GPT-5.3-Codex | User requests tool → agent generates it |
| CLI tool generation (argparse template) | Tool Factory | Generated CLI runs standalone |
| function_tool wrapper generation | Tool Factory | Wrapper integrates CLI into agent |
| Sandbox execution (subprocess, timeout, restricted env) | — | Tools can't access API keys or system files |
| Code review guardrail (static + LLM) | Safety agent | Dangerous patterns blocked |
| `/tools` command | Bot + DB | Lists all tools, active/inactive |
| Tool type decision logic | Tool Factory | CLI chosen by default; MCP only with justification |

### Phase 6: Polish & Advanced (Weeks 11-12)

**Goal:** Backup, voice, advanced search, testing.

| Task | Depends On | Acceptance |
|------|-----------|------------|
| Tavily deep search integration | Settings | "Research X thoroughly" uses Tavily |
| Voice message support (Whisper transcription) | OpenAI API | Voice message → text → processed normally |
| `/stats` usage dashboard | Audit log | Shows costs, tool usage, interaction count |
| Automated PostgreSQL backup (local volume + optionally Google Drive) | Phase 2 (optional) | Backup runs daily, restorable |
| Local backup fallback (if Google Drive unavailable) | — | Backup always works (resolves E4) |
| Curator agent (weekly self-improvement) | Phase 3 memory | Runs weekly, updates persona/memories |
| End-to-end test suite | All phases | pytest passes for critical paths |
| Security audit checklist | All phases | All C-gaps verified |

---

## 17. Acceptance Criteria per Phase

### Phase Gate: How to Know a Phase is Done

| Phase | Gate Test | Pass Criteria |
|-------|----------|---------------|
| **1** | Send 10 diverse messages via Telegram | All get persona-consistent replies; unauthorized user blocked; cost logged |
| **2** | "Read my emails", "What's on my calendar?", "Find X on Drive" | Correct data returned from Google; send email with approval flow works |
| **3** | Close Telegram, wait 1 hour, reopen and ask "What did we talk about?" | Assistant recalls previous conversation; preferences persisted |
| **4** | "Remind me every Monday at 9am" → restart container → wait for Monday | Reminder fires at correct time in user's timezone |
| **5** | "Create a tool that converts CSV to JSON" | Tool generated, tested, registered, usable within 2 minutes |
| **6** | Full day of normal usage | Voice works, stats accurate, backup completed, no errors in audit log |

---

## 18. Gaps Cross-Reference

Every gap from `RESEARCH_PersonalAssistant.md` Section 21 is resolved:

| Gap | Resolution | PRD Section |
|-----|-----------|-------------|
| **A1** No database schema | Full SQL schema with all tables | §5 |
| **A2** Tool manifest undefined | JSON schema with all fields | §8 |
| **A3** Persona storage undefined | persona_versions table + versioning rules | §5, §10 |
| **A4** Message router interface undefined | NormalizedMessage + FormattedResponse Pydantic models | §11 |
| **A5** Job callable signatures undefined | All job functions with signatures + error wrapper | §12 |
| **B1** Single vs multi-process | Single async process (AD-1) | §3 |
| **B2** Tool hot-reload undefined | Filesystem watcher + registry (AD-4) | §3, §8 |
| **B3** Conversation state unclear | Redis with 30-min TTL + PostgreSQL archival (AD-2) | §3, §9 |
| **B4** Handoff vs as_tool boundary | Per-agent decision table (AD-3) | §3, §7 |
| **B5** Error propagation undefined | Error handling strategy table | §15 |
| **B6** Concurrent requests undefined | Per-user asyncio.Queue (AD-6) | §3 |
| **C1** Allowlist management | DB table + Telegram commands (/allow, /revoke) | §14 |
| **C2** Cost control undefined | Daily/monthly caps + 80% alerts + audit tracking | §14 |
| **C3** Sandbox isolation unclear | Same container, restricted subprocess, no env vars | §14 |
| **C4** OAuth token security | Docker volume, auto-refresh, future encryption | §14 |
| **C5** Safety policy YAML undefined | Full schema with 5 example policies | §6 |
| **D1** .env template missing | Complete .env.example with all vars + descriptions | §6 |
| **D2** Config file structure undefined | 3 config files with full YAML schemas | §6 |
| **D3** Timezone handling undefined | User DB field + DEFAULT_TIMEZONE env var | §5, §6 |
| **E1** Web search Phase 6 vs Phase 1 | Moved to Phase 1 (WebSearchTool is zero-config) | §7, §16 |
| **E2** Memory Phase 3 but persona needs it Phase 1 | Phase-aware loader: YAML fallback → Mem0 | §7 |
| **E3** ACE loop no acceptance criteria | Metrics, thresholds, and run schedule defined | §10 |
| **E4** Backup depends on Google Drive | Local backup fallback added | §16 |
| **X1** subprocess blocked but needed | Clarified: block in generated code only, not wrappers | §8 |

---

> **PRD Version:** 1.0  
> **Created:** March 16, 2026  
> **Status:** Ready for Phase 1 implementation  
> **Process:** AI agent should read this PRD top-to-bottom, then implement Phase 1 tasks sequentially.
