# Agentic AI Personal Assistant — Deep Research Report

> **Date:** March 16, 2026  
> **Scope:** Standalone, self-improving, multi-agent Personal Assistant running in Docker with Telegram UX, OpenAI LLMs, Google Workspace integration, dynamic tool/agent creation (CLI-first, MCP as fallback), and production-grade safety guardrails for non-technical users.  
> **Constraints:** All databases and memory infrastructure **must be self-hosted in Docker containers only** — no SaaS database/memory API calls. Tool creation for specialized agents **always prioritizes CLI over MCP** when applicable.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Recommended Architecture Overview](#2-recommended-architecture-overview)
3. [Core Framework: OpenAI Agents SDK](#3-core-framework-openai-agents-sdk)
4. [OpenAI Models & API Strategy](#4-openai-models--api-strategy)
5. [Multi-Agent Orchestration](#5-multi-agent-orchestration)
6. [Tool Creation Strategy — CLI-First, MCP as Fallback](#6-tool-creation-strategy--cli-first-mcp-as-fallback)
7. [AI Memory System — Persistent, Self-Improving](#7-ai-memory-system--persistent-self-improving)
8. [User-Defined Persona & Self-Improving Personality](#8-user-defined-persona--self-improving-personality)
9. [Google Workspace Integration](#9-google-workspace-integration)
10. [Web Search & Information Retrieval](#10-web-search--information-retrieval)
11. [Task Scheduling & Automation](#11-task-scheduling--automation)
12. [Messaging UX — Telegram, WhatsApp, Discord](#12-messaging-ux--telegram-whatsapp-discord)
13. [Security, Safety & Guardrails](#13-security-safety--guardrails)
14. [Docker Deployment Architecture](#14-docker-deployment-architecture)
15. [Dynamic Agent & Tool Factory](#15-dynamic-agent--tool-factory)
16. [Deployment & UX Recommendations for Non-Technical Users](#16-deployment--ux-recommendations-for-non-technical-users)
17. [Technology Stack Summary](#17-technology-stack-summary)
18. [Risks & Mitigations](#18-risks--mitigations)
19. [Roadmap — Phased Build Plan](#19-roadmap--phased-build-plan)
20. [References & Sources](#20-references--sources)
21. [Gaps Analysis — Issues Found During PRD Review](#21-gaps-analysis--issues-found-during-prd-review)

---

## 1. Executive Summary

The goal is a **Dockerized, self-improving Personal Assistant** that:

- Communicates through **Telegram** (primary), with optional WhatsApp/Discord channels.
- Uses **OpenAI GPT-5.x models** (GPT-5.4, GPT-5.4-mine, GPT-5.3-Codex) as the default LLM.
- Supports a **user-defined persona** that evolves over time.
- Operates as a **multi-agent system** — spawning specialized sub-agents on demand.
- **Dynamically creates new tools** — CLI-first, then scripts, then MCP as fallback.
- Natively accesses **Google Workspace** (Gmail, Calendar, Drive, Docs, Sheets).
- **Searches the web** for real-time information.
- **Schedules recurring tasks** and automates workflows.
- **Self-improves** its personality, tools, skills, and memory over time.
- Is **safe and simple** enough for a non-technical user.

### Key Research Findings (March 2026)

| Area | Best-in-Class (2026) | Why |
|------|---------------------|-----|
| Agent Framework | **OpenAI Agents SDK** | Native GPT-5.x support, MCP built-in, guardrails, handoffs, tracing |
| Memory Layer | **Mem0 (open-source, self-hosted)** + PostgreSQL + Qdrant — **all in Docker** | Hybrid episodic/semantic/procedural; zero SaaS calls; 26% accuracy gain over pure vector |
| Google Integration | **Google Workspace MCP Server** | 12+ services via MCP, OAuth 2.1, container-friendly stateless mode |
| Messaging UX | **aiogram 3.x** (Telegram) | Fully async, modern Python, production-proven |
| Tool Creation | **CLI-first** → Python scripts → MCP (fallback) | CLI tools are simpler, faster, easier to debug; MCP only when agent-to-agent discovery is required |
| Safety | **OpenAI Guardrails** + Superagent patterns | Input/output/tool guardrails with tripwires; declarative policy enforcement |
| Scheduling | **APScheduler 4.x** | Async-native, persistent job stores, cron + interval triggers |
| Deployment | **Docker Compose (fully self-hosted)** | Single-command deploy; PostgreSQL, Redis, Qdrant all local containers — no SaaS DB calls |

---

## 2. Recommended Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    DOCKER COMPOSE STACK                  │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Telegram Bot │  │  WhatsApp    │  │  Discord Bot │  │
│  │  (aiogram 3)  │  │  (optional)  │  │  (optional)  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                 │           │
│         └────────┬────────┴────────┬────────┘           │
│                  ▼                                      │
│  ┌───────────────────────────────────────────────┐      │
│  │          MESSAGE ROUTER / GATEWAY             │      │
│  │   (normalize input → dispatch → format reply) │      │
│  └──────────────────┬────────────────────────────┘      │
│                     ▼                                   │
│  ┌───────────────────────────────────────────────┐      │
│  │        ORCHESTRATOR (Triage Agent)            │      │
│  │   OpenAI Agents SDK — GPT-5.4-mine            │      │
│  │   • Persona instructions (dynamic)            │      │
│  │   • Input/Output guardrails                   │      │
│  │   • Handoffs to specialist agents             │      │
│  │   • Agent.as_tool() for subtasks              │      │
│  └───┬───┬───┬───┬───┬───┬───┬───┬───────────────┘      │
│      │   │   │   │   │   │   │   │                      │
│      ▼   ▼   ▼   ▼   ▼   ▼   ▼   ▼                      │
│  ┌─────┐┌────┐┌────┐┌────┐┌────┐┌─────┐┌──────┐┌─────┐ │
│  │Email││Cal ││Drive││Web ││Code││Sched││Memory││Tool │ │
│  │Agent││Agt ││Agt  ││Srch││Exec││Agent││Agent ││Fctry│ │
│  └──┬──┘└──┬─┘└──┬─┘└──┬─┘└──┬─┘└──┬──┘└──┬───┘└──┬──┘ │
│     │      │     │     │     │     │      │       │     │
│     ▼      ▼     ▼     ▼     ▼     ▼      ▼       ▼     │
│  ┌───────────────────────────────────────────────────┐  │
│  │           TOOL LAYER (CLI-first priority)          │  │
│  │  1. CLI Tools (subprocess, argparse — preferred)   │  │
│  │  2. Python Script Tools (function_tool)            │  │
│  │  3. MCP Servers (fallback for agent discovery)     │  │
│  │  • Google Workspace MCP (Gmail,Cal,Drive,Sheets)   │  │
│  │  • Web Search (built-in WebSearchTool)             │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │      SELF-HOSTED DATA (Docker only, no SaaS)      │  │
│  │  ┌───────────┐ ┌────────────┐ ┌────────────┐      │  │
│  │  │PostgreSQL │ │  Qdrant    │ │   Redis    │      │  │
│  │  │(Mem0 facts│ │ (vector    │ │ (cache,    │      │  │
│  │  │ job store,│ │ embeddings)│ │  sessions, │      │  │
│  │  │ audit log)│ │            │ │  rate limit│      │  │
│  │  └───────────┘ └────────────┘ └────────────┘      │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Core Framework: OpenAI Agents SDK

**Source:** [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) — production-ready, open-source (successor to Swarm)

### Why OpenAI Agents SDK Over Alternatives

| Framework | Pros | Cons | Verdict |
|-----------|------|------|---------|
| **OpenAI Agents SDK** | Native GPT-5.x; built-in guardrails, MCP, handoffs, tracing, sessions | OpenAI-centric (can use other providers) | **Best fit** — tightest integration with our LLM |
| LangGraph | Flexible graph-based flows; large ecosystem | Heavier abstraction; more complex | Good alternative if multi-provider needed |
| CrewAI | Easy role-based agents; no-code option | Less control over orchestration | Good for simpler use cases |
| AutoGen | Microsoft-backed; cross-language | More enterprise-focused; heavier | Overkill for single-user assistant |

### SDK Core Primitives

```python
from agents import Agent, Runner, Handoff, InputGuardrail, OutputGuardrail

# 1. Agents — LLMs with instructions, tools, guardrails
# 2. Handoffs — delegate to specialist agents
# 3. Agent.as_tool() — call an agent as a subtask (returns result, not conversation control)
# 4. Guardrails — input/output/tool validation with tripwires
# 5. Sessions — persistent memory within agent loop
# 6. MCP Server integration — built-in, same interface as function tools
# 7. Tracing — built-in visualization, debugging, eval, fine-tuning
```

### Key Features for This Project

- **Agent loop** — automatic tool invocation → LLM → repeat until complete.
- **Python-first** — use `asyncio`, standard Python patterns, no DSL.
- **Function tools** — turn any Python function into a tool with automatic schema generation (Pydantic validation).
- **MCP server tool calling** — built-in; works identically to function tools.
- **Sessions** — persistent working context across agent loop turns.
- **Human-in-the-loop** — built-in mechanisms for user approval.
- **Realtime agents** — voice support with interruption detection (future expansion).

---

## 4. OpenAI Models & API Strategy

### Current Model Landscape (March 2026)

| Model | Best For | Context | Notes |
|-------|----------|---------|-------|
| **GPT-5.4** | General intelligence, reasoning | Large | Latest flagship |
| **GPT-5.4-mine** | Personalized tasks, adaptive | Large | Fine-tunable variant |
| **GPT-5.3-Codex** | Code generation, tool creation | Large | Optimized for code tasks |
| **gpt-4.1-nano** | Fast guardrail checks, classification | Standard | Cheap, fast — ideal for guardrails |
| **gpt-realtime** | Voice agents | Streaming | Low-latency speech-to-speech |

### API Strategy: Responses API (NOT Assistants)

> **Critical:** The Assistants API is **deprecated** and will be **removed August 26, 2026**. All new development must use the **Responses API**.

**Key Responses API advantages:**
- Better performance with reasoning models
- Native MCP integration (hosted and local)
- Built-in web search, code interpreter, file search
- Structured outputs for reliable data extraction
- No thread management overhead — stateless by design

### Model Selection Strategy for the Assistant

```python
MODEL_CONFIG = {
    "orchestrator": "gpt-5.4-mine",       # Main persona, routing, conversation
    "code_generation": "gpt-5.3-codex",   # Tool creation, script writing
    "fast_classification": "gpt-4.1-nano", # Guardrails, intent detection
    "general_tasks": "gpt-5.4",           # Complex reasoning, research
}
```

---

## 5. Multi-Agent Orchestration

### Orchestration Patterns (from OpenAI SDK docs)

**Two primary patterns, both used:**

#### Pattern 1: LLM-Driven (for open-ended tasks)
The orchestrator agent autonomously decides which tools to use and which sub-agents to hand off to, based on the user's request.

```python
orchestrator = Agent(
    name="PersonalAssistant",
    instructions=dynamic_persona_prompt(),  # loaded from memory
    tools=[web_search, code_interpreter, schedule_task],
    handoffs=[email_agent, calendar_agent, drive_agent, tool_factory_agent],
    input_guardrails=[safety_guardrail],
    output_guardrails=[pii_guardrail],
    model="gpt-5.4-mine",
)
```

#### Pattern 2: Code-Driven (for deterministic workflows)
Use structured outputs to classify intent, then route via Python logic.

```python
# Classify → Route → Execute → Evaluate
intent = await classifier_agent.run(user_message)  # structured output
if intent.category == "email":
    result = await Runner.run(email_agent, user_message)
elif intent.category == "schedule":
    result = await Runner.run(scheduler_agent, user_message)
```

### Best Practices from Research

1. **Invest in good prompts** — clear instructions on available tools, parameters, and boundaries.
2. **Specialized agents** excel at one task > general-purpose agent doing everything.
3. **Agent.as_tool()** for bounded subtasks (returns result to caller).
4. **Handoffs** for routing (specialist takes over the conversation).
5. **Self-critique loops** — run agent → evaluator agent → improve → repeat.
6. **Parallel execution** — use `asyncio.gather` for independent sub-tasks.

---

## 6. Tool Creation Strategy — CLI-First, MCP as Fallback

### Decision Framework: CLI vs Script vs MCP

> **Rule:** Always prefer the simplest, most debuggable approach. CLI tools are the default. MCP is reserved for cases where dynamic agent-to-agent tool discovery is genuinely required.

| Priority | Method | When to Use | Pros | Cons |
|----------|--------|-------------|------|------|
| **1st (default)** | **CLI Tool** (subprocess + argparse) | Data processing, file ops, API wrappers, system tasks | Simplest to write, test, debug; works standalone; no framework coupling | No auto-discovery by other agents |
| **2nd** | **Python Script** (function_tool) | Logic tightly coupled to agent workflow; needs Pydantic validation | Native SDK integration; type-safe; zero overhead | Tied to the agent process |
| **3rd (fallback)** | **MCP Server** (stdio/HTTP) | Multi-agent tool sharing; external service connectors (Google Workspace) | Agent auto-discovery; protocol standard | More boilerplate; harder to debug |

### CLI Tool Pattern (Preferred)

CLI tools are standalone Python scripts with `argparse` that agents invoke via `subprocess`. They are:
- **Testable independently** — run from terminal, pipe, cron, or agent
- **Debuggable** — standard stdin/stdout/stderr
- **Versionable** — simple files in `tools/` directory
- **Sandboxable** — run in restricted subprocess with timeouts

```python
# tools/stock_checker/cli.py — standalone CLI tool
import argparse
import json
import sys

def main():
    parser = argparse.ArgumentParser(description="Check stock portfolio")
    parser.add_argument("--symbols", nargs="+", required=True, help="Stock symbols")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    results = fetch_stock_data(args.symbols)  # your logic here

    if args.format == "json":
        json.dump(results, sys.stdout)
    else:
        for r in results:
            print(f"{r['symbol']}: ${r['price']:.2f} ({r['change']:+.1f}%)")

if __name__ == "__main__":
    main()
```

```python
# Agent calls the CLI tool via function_tool wrapper
from agents import function_tool
import subprocess, json

@function_tool
def check_stocks(symbols: list[str]) -> str:
    """Check current stock prices for given symbols."""
    result = subprocess.run(
        ["python", "tools/stock_checker/cli.py", "--symbols"] + symbols + ["--format", "json"],
        capture_output=True, text=True, timeout=30,
        cwd="/app",
    )
    if result.returncode != 0:
        return f"Error: {result.stderr}"
    return result.stdout
```

### When MCP IS Appropriate

MCP remains the right choice for:
- **Google Workspace integration** — complex OAuth, multi-service, already built as MCP
- **Third-party connectors** with existing MCP servers (e.g., databases, CRMs)
- **Tools that multiple agents need to discover dynamically** at runtime
- **Hosted MCP tools** provided by OpenAI (WebSearchTool, CodeInterpreter)

```python
# MCP is still used for Google Workspace (justified — complex multi-service connector)
from agents import MCPServerStreamableHttp

workspace_mcp = MCPServerStreamableHttp(
    name="google_workspace",
    params={"url": "http://workspace-mcp:8080/mcp"},
)
```

### What is MCP? (Background)

MCP (Model Context Protocol) is an **open standard** (donated to Linux Foundation, Dec 2025) that provides a universal interface for connecting LLMs to external tools and data.

**Adopted by:** OpenAI, Google, Anthropic, Microsoft, AWS, Cloudflare  
**Spec version:** 2025-11-25  
**SDK downloads:** 97M+ monthly (Python + TypeScript)

The OpenAI Agents SDK supports five MCP transports (HostedMCPTool, StreamableHttp, SSE, Stdio, Manager), but for this project **CLI tools are the default** and MCP is only used where it provides clear value over a simple subprocess call.

### MCP Approval Flows (Safety for Tool Execution)

```python
from agents import MCPToolApprovalRequest

SAFE_TOOLS = {"read_email", "list_events", "search_drive"}

def approve_tool(request: MCPToolApprovalRequest):
    if request.data.name in SAFE_TOOLS:
        return {"approve": True}
    # Dangerous tool → ask user via Telegram
    return {"approve": False, "reason": "Requires your approval"}
```

---

## 7. AI Memory System — Persistent, Self-Improving

### Memory Architecture (2026 Best Practices)

Research confirms that **context window ≠ memory** and **RAG alone is not enough**. Production agents need a multi-layered memory system.

### Recommended: Mem0 (Open-Source, Self-Hosted) + Hybrid Storage

> **Constraint:** All memory and database infrastructure runs **exclusively in Docker containers**. No SaaS memory APIs (no Mem0 Cloud, no Pinecone, no managed Qdrant). Everything is local to the Docker Compose stack.

**Mem0 open-source** (`pip install mem0ai`) is the most mature production memory layer in 2026. Benchmarks show **26% accuracy gain** over pure vector approaches. The open-source version connects directly to self-hosted PostgreSQL and Qdrant — zero external SaaS calls.

#### Memory Types

| Type | What It Stores | Storage | Example |
|------|---------------|---------|---------|
| **Short-term / Working** | Current session, last 5-10 exchanges | Redis (Docker) | "User just asked about tomorrow's meeting" |
| **Episodic** | Summarized interaction history | PostgreSQL (Docker) | "Last session, user updated the budget spreadsheet" |
| **Semantic** | Facts and preferences | Qdrant (Docker) + PostgreSQL (Docker) | "User prefers dark mode, works in fintech, name is Alex" |
| **Procedural** | Workflows and learned skills | PostgreSQL (Docker) + files (volume) | "Step-by-step: invoice approval → validate → route → notify" |
| **Graph** (advanced) | Entity-relationship maps | PostgreSQL + Apache AGE extension (Docker) | "Project Alpha → involves → Team B → deadline → March 30" |

#### Scope-Based Isolation
- **User ID** — personal preferences, history
- **Agent ID** — agent-specific learned behaviors
- **Session ID** — current conversation context
- **Organization** — shared knowledge (future multi-user)

### Self-Improvement Loop: Agentic Context Engineering (ACE)

Based on the 2025 arXiv paper achieving **+10.6% on agent benchmarks** without fine-tuning:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Generator   │────▶│  Reflector   │────▶│   Curator    │
│ (executes    │     │ (evaluates,  │     │ (extracts    │
│  task)       │     │  finds gaps) │     │  learnings,  │
│              │     │              │     │  updates     │
│              │     │              │     │  playbook)   │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
                                    ┌───────────▼──────────┐
                                    │  Context Playbook    │
                                    │  (skills.md / memory │
                                    │   store) — injected  │
                                    │   on next run        │
                                    └──────────────────────┘
```

### Implementation with Mem0 (Self-Hosted)

All storage backends point to Docker Compose services — **no external SaaS calls**:

```python
from mem0 import Memory

# ALL backends are self-hosted Docker containers
memory = Memory.from_config({
    "llm": {
        "provider": "openai",
        "config": {"model": "gpt-5.4-mine"},
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": "qdrant",       # Docker service name
            "port": 6333,           # Internal Docker network
        },
    },
    "graph_store": {
        "provider": "falkordb",     # Or PostgreSQL + AGE extension
        "config": {
            "host": "postgres",     # Docker service name
            "port": 5432,
        },
    },
    "history_db_path": "/data/mem0_history.db",  # Docker volume
})

# Add a memory after interaction
memory.add("User prefers morning schedule summaries at 8am", user_id="user_1")

# Retrieve relevant memories for context
relevant = memory.search("schedule preferences", user_id="user_1")

# Memories automatically consolidate, expire, and self-update
```

### Self-Hosted Infrastructure Summary

| Service | Docker Image | Purpose | SaaS Alternative (NOT used) |
|---------|-------------|---------|----------------------------|
| **PostgreSQL 17** | `postgres:17-alpine` | Long-term facts, episodic memory, job store, audit | Supabase, RDS, Neon |
| **Qdrant** | `qdrant/qdrant:latest` | Vector embeddings, semantic search | Pinecone, Weaviate Cloud |
| **Redis 7** | `redis:7-alpine` | Short-term cache, sessions, rate limiting | ElastiCache, Upstash |
| **Mem0** | `pip install mem0ai` (in app container) | Memory orchestration layer | Mem0 Cloud API |

---

## 8. User-Defined Persona & Self-Improving Personality

### Persona System Design

The assistant's personality is stored as a **living document** in memory, not hardcoded:

```python
PERSONA_TEMPLATE = """
You are {name}, a personal assistant for {user_name}.

## Core Personality
{personality_traits}

## Communication Style
{communication_style}

## Known Preferences
{user_preferences}

## Learned Behaviors
{procedural_memories}

## Current Context
{recent_episodic_memories}

## Rules
{safety_rules}
"""
```

### Self-Improvement Mechanism

1. **After each interaction:** The Reflector agent evaluates quality.
2. **Weekly review:** Curator agent analyzes interaction patterns.
3. **Persona updates:** Adjustments to tone, verbosity, proactive suggestions.
4. **User feedback loop:** "Was this helpful?" → stored and analyzed.
5. **All changes logged** in an audit trail for transparency.

### User Control via Telegram

```
User: /persona
Bot: Your current persona settings:
  Name: Atlas
  Style: Professional but friendly
  Proactivity: Medium (suggests tasks, doesn't auto-execute)
  
  /persona name Luna
  /persona style casual
  /persona proactivity high
```

---

## 9. Google Workspace Integration

### Recommended: Google Workspace MCP Server

**Repository:** [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp) (70+ releases, production-ready)

**Official Google MCP support** was also announced in early 2026 by Google Cloud.

### Supported Services (12+)

| Service | Capabilities |
|---------|-------------|
| **Gmail** | Read, send, search, label, draft, reply, forward, attachments |
| **Google Calendar** | Create, update, delete events; check availability; recurring events |
| **Google Drive** | Upload, download, search, share, organize files/folders |
| **Google Docs** | Create, read, edit documents with fine-grained control |
| **Google Sheets** | Read/write cells, ranges; create spreadsheets; formulas |
| **Google Slides** | Create, update presentations |
| **Google Forms** | Create forms, read responses |
| **Google Tasks** | Task/list management with hierarchy |
| **Google Chat** | Space management, messaging |
| **Google Contacts** | Contact management via People API |
| **Google Search** | Programmable Search Engine integration |
| **Apps Script** | Execute custom business logic across services |

### Authentication & Security

- **OAuth 2.0 / OAuth 2.1** with automatic token refresh
- **Stateless mode** — container-friendly, no local file storage
- **Tool tiers** — `core`, `extended`, `complete` to limit exposed capabilities
- **Per-tool approval** — dangerous operations require user confirmation

### Docker Integration

```yaml
# docker-compose.yml (excerpt)
workspace-mcp:
  image: google-workspace-mcp:latest
  environment:
    - GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID}
    - GOOGLE_OAUTH_CLIENT_SECRET=${GOOGLE_OAUTH_CLIENT_SECRET}
    - WORKSPACE_MCP_STATELESS_MODE=true
    - MCP_ENABLE_OAUTH21=true
  volumes:
    - workspace_tokens:/data/tokens
```

---

## 10. Web Search & Information Retrieval

### Options (Ranked by Fit)

| Provider | Type | Cost | Quality | Integration |
|----------|------|------|---------|-------------|
| **OpenAI WebSearchTool** | Built-in SDK tool | Included in API | High | Zero config |
| **Tavily Search API** | Dedicated AI search | $0.01/query | Very high (AI-optimized) | MCP server available |
| **Brave Search API** | Web search API | Free tier available | High | MCP server available |
| **Google PSE** | Custom search | Free tier (100/day) | High | Built into Workspace MCP |

### Recommended: Layered Approach

```python
from agents import Agent, WebSearchTool

# Built-in web search (simplest)
agent = Agent(
    name="Researcher",
    tools=[WebSearchTool()],  # OpenAI hosted
    model="gpt-5.4",
)

# For deeper research, add Tavily MCP
tavily_mcp = MCPServerStdio(
    name="tavily",
    params={"command": "npx", "args": ["-y", "tavily-mcp-server"]},
    env={"TAVILY_API_KEY": os.environ["TAVILY_API_KEY"]},
)
```

---

## 11. Task Scheduling & Automation

### APScheduler 4.x — Async-Native Task Scheduler

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url="postgresql://...")},
)

# User says: "Remind me every Monday at 9am to review my weekly goals"
scheduler.add_job(
    send_telegram_message,
    trigger="cron",
    day_of_week="mon",
    hour=9,
    args=[user_id, "Time to review your weekly goals!"],
    id="weekly_goals_reminder",
)

# User says: "Check my email every 30 minutes and summarize new ones"
scheduler.add_job(
    summarize_new_emails,
    trigger="interval",
    minutes=30,
    args=[user_id],
    id="email_digest",
)
```

### Scheduling Features

- **Cron triggers** — complex calendar-based schedules
- **Interval triggers** — every N minutes/hours
- **One-shot triggers** — specific date/time reminders
- **Persistent job store** — survives container restarts (PostgreSQL-backed)
- **Natural language** — orchestrator parses "every Tuesday at 3pm" → cron expression
- **User management via Telegram** — `/schedules`, `/cancel <id>`, `/pause <id>`

---

## 12. Messaging UX — Telegram, WhatsApp, Discord

### Primary: Telegram (via aiogram 3.x)

**Why Telegram:**
- **Free, no business verification** required (unlike WhatsApp Business API)
- **Rich UI** — inline keyboards, buttons, file sharing, voice messages, markdown
- **Bot API** is stable, well-documented, rate limits are generous
- **aiogram 3.x** — fully async, modern Python, excellent middleware support
- **No monthly fees** — unlike WhatsApp Cloud API

```python
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()

@router.message()
async def handle_message(message: Message):
    # Normalize → Route to Orchestrator → Format reply
    user_input = message.text
    context = await load_user_context(message.from_user.id)
    
    response = await orchestrator.run(user_input, context=context)
    
    await message.answer(
        format_for_telegram(response.final_output),
        parse_mode="MarkdownV2",
    )
```

### Telegram UX Features for Non-Technical Users

| Feature | Implementation |
|---------|---------------|
| **Quick actions** | Inline keyboard buttons ("Approve / Deny / Ask more") |
| **File sharing** | Upload to Drive, download from Drive — drag & drop |
| **Voice messages** | Whisper API transcription → process as text |
| **Status updates** | "Working on it..." with progress indicators |
| **Command menu** | `/help`, `/persona`, `/schedules`, `/tools`, `/memory` |
| **Approval flows** | "Agent wants to send this email. OK?" [Approve] [Edit] [Cancel] |
| **Rich formatting** | Markdown tables, code blocks, bullet lists |

### Optional: WhatsApp

- Requires **Meta Business verification** and **WhatsApp Cloud API** access
- Monthly costs for conversation-based pricing
- Best via **WaSenderAPI** or **Whapi.Cloud** for simpler integration
- Same message router pattern — just a different transport adapter

### Optional: Discord

- **Pycord 2.7+** — async, slash commands, embed messages
- Good for users already in Discord communities
- Richer formatting (embeds, threads, reactions)
- Free, no verification needed

### Message Router Pattern

```python
class MessageRouter:
    """Normalize messages from any platform → unified format → orchestrator"""
    
    async def handle(self, platform: str, raw_message: dict) -> str:
        normalized = self.normalize(platform, raw_message)
        
        # Load user context & memory
        context = await self.memory.get_context(normalized.user_id)
        
        # Run through orchestrator
        result = await Runner.run(self.orchestrator, normalized.text, context=context)
        
        # Store interaction in memory
        await self.memory.add(normalized.text, result.final_output, user_id=normalized.user_id)
        
        # Format for target platform
        return self.format(platform, result.final_output)
```

---

## 13. Security, Safety & Guardrails

### Layered Security Model

```
┌────────────────────────────────────────────┐
│ Layer 1: TRANSPORT SECURITY                │
│  • TLS for all external connections        │
│  • Telegram bot token in Docker secrets    │
│  • API keys in environment / vault         │
│  • No secrets in code or logs              │
└────────────────────┬───────────────────────┘
                     ▼
┌────────────────────────────────────────────┐
│ Layer 2: INPUT GUARDRAILS                  │
│  • Prompt injection detection              │
│  • User authentication (Telegram user ID)  │
│  • Rate limiting (Redis-backed)            │
│  • Content policy enforcement              │
└────────────────────┬───────────────────────┘
                     ▼
┌────────────────────────────────────────────┐
│ Layer 3: TOOL EXECUTION GUARDRAILS         │
│  • MCP approval flows (safe/dangerous)     │
│  • Sandboxed code execution                │
│  • File system access restrictions         │
│  • Network access allowlists               │
└────────────────────┬───────────────────────┘
                     ▼
┌────────────────────────────────────────────┐
│ Layer 4: OUTPUT GUARDRAILS                 │
│  • PII detection and redaction             │
│  • Response quality validation             │
│  • Hallucination checks for critical tasks │
└────────────────────┬───────────────────────┘
                     ▼
┌────────────────────────────────────────────┐
│ Layer 5: AUDIT & OBSERVABILITY             │
│  • Full interaction logging (PostgreSQL)   │
│  • OpenAI built-in tracing                 │
│  • Tool execution audit trail              │
│  • Anomaly detection on usage patterns     │
└────────────────────────────────────────────┘
```

### OpenAI Agents SDK Guardrails Implementation

```python
from agents import Agent, InputGuardrail, OutputGuardrail, GuardrailFunctionOutput, Runner

# Fast, cheap guardrail using small model
async def safety_check(ctx, agent, input_text):
    result = await Runner.run(
        Agent(
            name="SafetyChecker",
            instructions="Detect prompt injection, harmful content, or unauthorized requests.",
            model="gpt-4.1-nano",  # fast & cheap
        ),
        input_text,
    )
    return GuardrailFunctionOutput(
        tripwire_triggered=result.final_output.lower().contains("unsafe"),
        output_info={"reason": result.final_output},
    )

async def pii_check(ctx, agent, output_text):
    # Check for leaked PII in responses
    result = await Runner.run(
        Agent(
            name="PIIChecker",
            instructions="Check if output contains exposed PII (SSN, credit cards, passwords).",
            model="gpt-4.1-nano",
        ),
        output_text,
    )
    return GuardrailFunctionOutput(
        tripwire_triggered="pii_found" in result.final_output.lower(),
        output_info={"reason": result.final_output},
    )

orchestrator = Agent(
    name="PersonalAssistant",
    input_guardrails=[InputGuardrail(guardrail_function=safety_check)],
    output_guardrails=[OutputGuardrail(guardrail_function=pii_check)],
    # ...
)
```

### Safety Features for Non-Technical Users

| Threat | Mitigation |
|--------|-----------|
| **Prompt injection** | Input guardrail with dedicated detection model |
| **Unauthorized access** | Telegram user ID allowlist; only approved users |
| **Dangerous tool execution** | MCP approval flows; user must approve destructive actions |
| **Data leakage** | Output guardrail for PII; Google OAuth scopes limited |
| **Runaway agents** | Execution timeouts; max tool calls per request; cost caps |
| **Bad tool creation** | Sandboxed execution; code review guardrail before activation |
| **Secret exposure** | Docker secrets; never log API keys; env-only configuration |
| **Accidental deletions** | Confirmation prompts for destructive operations |

### Superagent Safety Agent Pattern

Inspired by the Superagent framework (Dec 2025):
- A **Safety Agent** runs alongside all other agents
- Evaluates every tool call and response against **declarative policies**
- Policies are defined in config (YAML), not code — security team can update without dev
- Actions that violate rules are blocked, modified, or flagged for review

---

## 14. Docker Deployment Architecture

### docker-compose.yml

```yaml
version: "3.9"

services:
  # ─── Core Application ───
  assistant:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: personal-assistant
    restart: unless-stopped
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - DATABASE_URL=postgresql://assistant:${DB_PASSWORD}@postgres:5432/assistant
      - REDIS_URL=redis://redis:6379/0
      - QDRANT_URL=http://qdrant:6333
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      qdrant:
        condition: service_started
    volumes:
      - tools_data:/app/tools        # Dynamic tool storage
      - config_data:/app/config      # Persona, schedules
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: "2.0"

  # ─── Google Workspace MCP Server ───
  workspace-mcp:
    build:
      context: ./services/workspace-mcp
    container_name: workspace-mcp
    restart: unless-stopped
    environment:
      - GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID}
      - GOOGLE_OAUTH_CLIENT_SECRET=${GOOGLE_OAUTH_CLIENT_SECRET}
      - WORKSPACE_MCP_STATELESS_MODE=true
    volumes:
      - workspace_tokens:/data/tokens
    ports:
      - "127.0.0.1:8081:8080"

  # ─── PostgreSQL (Memory, Jobs, Audit) ───
  postgres:
    image: postgres:17-alpine
    container_name: assistant-postgres
    restart: unless-stopped
    environment:
      - POSTGRES_USER=assistant
      - POSTGRES_PASSWORD=${DB_PASSWORD}
      - POSTGRES_DB=assistant
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U assistant"]
      interval: 5s
      timeout: 5s
      retries: 5

  # ─── Qdrant (Vector Embeddings) ───
  qdrant:
    image: qdrant/qdrant:latest
    container_name: assistant-qdrant
    restart: unless-stopped
    volumes:
      - qdrant_data:/qdrant/storage
    ports:
      - "127.0.0.1:6333:6333"

  # ─── Redis (Cache, Sessions, Rate Limiting) ───
  redis:
    image: redis:7-alpine
    container_name: assistant-redis
    restart: unless-stopped
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  # ─── Watchtower (Auto-Update) ───
  watchtower:
    image: containrrr/watchtower
    container_name: watchtower
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    command: --interval 86400  # Check daily

volumes:
  postgres_data:
  qdrant_data:
  redis_data:
  tools_data:
  config_data:
  workspace_tokens:
```

### Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY src/ ./src/
COPY config/ ./config/

# Non-root user
RUN useradd -m -r assistant && chown -R assistant:assistant /app
USER assistant

CMD ["python", "-m", "src.main"]
```

---

## 15. Dynamic Agent & Tool Factory

### How the Assistant Creates New Tools

The **Tool Factory Agent** is a specialized agent (using GPT-5.3-Codex) that can:

1. **Analyze the user's need** — "I need a tool that converts CSV to formatted Google Sheets"
2. **Choose the best approach (CLI-first priority):**
   - **CLI tool** (default) — standalone argparse script in `tools/`; called via subprocess
   - **Python function_tool** — when tightly coupled to agent logic; needs Pydantic validation
   - **MCP server** (fallback only) — when multiple agents need to dynamically discover the tool
3. **Generate the code** with proper error handling, input validation
4. **Test it** in a sandboxed environment (subprocess with timeout)
5. **Register it** — CLI tools get a `function_tool` wrapper; MCP servers get added to agent config

### Tool Factory Flow (CLI-First)

```
User: "I need a tool that checks my stock portfolio every morning"
   │
   ▼
Orchestrator → handoff → Tool Factory Agent
   │
   ▼
Tool Factory:
  1. Decides: CLI tool (standalone, no agent discovery needed)
  2. Generates CLI:     tools/stock_checker/cli.py (argparse)
  3. Generates wrapper:  tools/stock_checker/tool.py (function_tool → subprocess)
  4. Writes manifest:    tools/stock_checker/manifest.json
  5. Tests in sandbox:   ✓ passes (subprocess, timeout=30s)
  6. Registers wrapper with orchestrator
  7. Creates scheduled job: every day at 7am
   │
   ▼
Orchestrator: "Done! I created a stock portfolio checker.
              It runs every morning at 7am and will send you a summary.
              You can also ask me to check anytime."
```

### Tool Type Decision Tree

```
New tool request
  │
  ├─ Can it run as a standalone script? → YES → CLI Tool (default)
  │
  ├─ Does it need Pydantic types / deep agent integration? → YES → function_tool
  │
  └─ Do multiple agents need to discover it dynamically? → YES → MCP Server (fallback)
```

### Safety Guardrails for Tool Creation

```python
class ToolFactoryGuardrails:
    BLOCKED_IMPORTS = ["subprocess", "shutil", "ctypes", "pickle"]
    BLOCKED_OPERATIONS = ["rm -rf", "DROP TABLE", "DELETE FROM"]
    MAX_FILE_SIZE = 50_000  # bytes
    MAX_NETWORK_CALLS = 10  # per execution
    
    async def review_generated_code(self, code: str) -> bool:
        # 1. Static analysis — blocked imports, dangerous patterns
        # 2. LLM review — GPT-5.3-Codex reviews for safety
        # 3. Sandbox test — execute in isolated container
        # 4. User approval — show summary to user before activation
        pass
```

---

## 16. Deployment & UX Recommendations for Non-Technical Users

### One-Command Setup

```bash
# User downloads the project, sets up .env, and runs:
docker compose up -d

# That's it. The bot starts listening on Telegram.
```

### First-Time Setup Wizard (via Telegram)

```
Bot: Welcome! I'm your new Personal Assistant. Let's get set up.

1/5 — What should I call you?
User: Alex

2/5 — What's my name? Pick one or type your own:
  [Atlas] [Luna] [Nova] [Custom...]
User: Luna

3/5 — How should I communicate?
  [Professional] [Casual] [Friendly] [Brief]
User: Friendly

4/5 — Connect Google Workspace?
  [Yes, connect now] [Later]
User: Yes, connect now
Bot: Click here to authorize: [OAuth Link]
     ✓ Connected! I can now access your Gmail, Calendar, and Drive.

5/5 — What should I help with first?
  [Manage my email] [Organize my schedule] [Just chat]
User: Manage my email

Bot: Great! I'll summarize your unread emails now...
```

### UX Design Principles for Non-Technical Users

1. **No command-line required** — everything via Telegram chat
2. **Progressive disclosure** — start simple, reveal advanced features over time
3. **Always confirm destructive actions** — "Send this email?" / "Delete this file?"
4. **Plain language** — no jargon, no technical error messages
5. **Undo support** — "Oops, undo that" should work for recent actions
6. **Status transparency** — "I'm searching your Drive... found 3 files"
7. **Graceful failures** — "I couldn't access your calendar. Want me to try again?"
8. **Proactive suggestions** — "You have a meeting in 30 minutes. Need a summary?"

### Key Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Initial setup wizard |
| `/help` | Show available capabilities |
| `/persona` | View/edit assistant personality |
| `/schedules` | List all recurring tasks |
| `/tools` | List available tools |
| `/memory` | View what the assistant remembers |
| `/forget <topic>` | Ask assistant to forget something |
| `/approve` | Approve a pending action |
| `/cancel` | Cancel current operation |
| `/feedback` | Rate last interaction |

### Update & Maintenance (Non-Technical)

- **Auto-updates** via Watchtower (checks daily for new images)
- **Health monitoring** — bot sends daily "I'm healthy" ping
- **Backup** — PostgreSQL automated backups to Google Drive
- **Error recovery** — automatic restart on crash (`restart: unless-stopped`)
- **Usage dashboard** — `/stats` shows API costs, interactions, tool usage

---

## 17. Technology Stack Summary

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| **LLM** | OpenAI GPT-5.4, GPT-5.4-mine, GPT-5.3-Codex | Latest | Intelligence |
| **Agent Framework** | OpenAI Agents SDK | Latest | Multi-agent orchestration |
| **API Protocol** | OpenAI Responses API | v1 | LLM communication |
| **Tool Creation** | CLI-first (subprocess + argparse) → function_tool → MCP (fallback) | N/A | Dynamic tool creation for specialized agents |
| **Tool Protocol** | Model Context Protocol (MCP) | 2025-11-25 | Google Workspace, external connectors only |
| **Memory** | Mem0 (open-source, self-hosted) | Latest | Persistent AI memory — **no SaaS API** |
| **Vector DB** | Qdrant (self-hosted Docker) | Latest | Semantic search, embeddings — **no SaaS API** |
| **Database** | PostgreSQL 17 (self-hosted Docker) | 17-alpine | Facts, jobs, audit logs — **no SaaS API** |
| **Cache** | Redis 7 (self-hosted Docker) | 7-alpine | Sessions, rate limiting — **no SaaS API** |
| **Telegram** | aiogram | 3.x | Bot framework |
| **Scheduler** | APScheduler | 4.x | Recurring tasks |
| **Google** | Google Workspace MCP Server | Latest | Gmail, Calendar, Drive, etc. |
| **Web Search** | OpenAI WebSearchTool + Tavily | Latest | Information retrieval |
| **Container** | Docker + Docker Compose | Latest | Deployment |
| **Language** | Python | 3.12+ | Application code |
| **Validation** | Pydantic | 2.x | Schema enforcement |

---

## 18. Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| **OpenAI API outage** | Assistant goes offline | Low | Cache recent context; graceful degradation message |
| **API cost overrun** | Unexpected bills | Medium | Daily cost caps; usage alerts; prefer cheap models for guardrails |
| **Prompt injection** | Unauthorized actions | Medium | Input guardrails; user allowlist; tool approval flows |
| **Memory corruption** | Wrong information recalled | Low | Versioned memories; user can review/delete; regular pruning |
| **Tool factory abuse** | Malicious code generated | Low | Sandboxing; static analysis; user approval required |
| **Google token expiry** | Can't access Workspace | Medium | Auto-refresh; alert user if re-auth needed |
| **Docker volume loss** | Data loss | Low | Automated backups; external backup to Google Drive |
| **Model deprecation** | Breaking changes | Medium | Abstraction layer for model selection; config-driven model choice |

---

## 19. Roadmap — Phased Build Plan

### Phase 1: Foundation (Weeks 1-3)
- [ ] Docker Compose stack (PostgreSQL, Redis, Qdrant)
- [ ] Telegram bot with aiogram 3.x
- [ ] Basic orchestrator agent with OpenAI Agents SDK
- [ ] Simple persona system (configurable via /persona)
- [ ] Input/output guardrails
- [ ] User authentication (Telegram ID allowlist)

### Phase 2: Google Workspace (Weeks 4-5)
- [ ] Google Workspace MCP Server integration
- [ ] Gmail: read, search, send, reply
- [ ] Calendar: view, create, update events
- [ ] Drive: search, upload, download files
- [ ] Sheets: read/write data
- [ ] OAuth 2.1 flow via Telegram (link-based)

### Phase 3: Memory & Intelligence (Weeks 6-7)
- [ ] Mem0 integration (episodic, semantic, procedural)
- [ ] Short-term context (Redis-backed sessions)
- [ ] Long-term memory (PostgreSQL + Qdrant)
- [ ] Self-improvement loop (ACE pattern)
- [ ] Persona evolution based on interactions

### Phase 4: Scheduling & Automation (Week 8)
- [ ] APScheduler integration with PostgreSQL job store
- [ ] Natural language → cron expression parsing
- [ ] Recurring task management via Telegram
- [ ] Proactive notifications (morning brief, meeting reminders)

### Phase 5: Tool Factory (Weeks 9-10)
- [ ] Tool Factory agent (GPT-5.3-Codex)
- [ ] CLI tool generation (argparse scripts) — default path
- [ ] function_tool wrappers (subprocess → agent integration)
- [ ] MCP server generation (fallback only, for multi-agent discovery)
- [ ] Tool type decision logic (CLI → script → MCP)
- [ ] Sandboxed execution environment (subprocess with timeouts)
- [ ] Code review guardrails (static analysis + LLM review)
- [ ] Tool management via Telegram (/tools)

### Phase 6: Polish & Advanced (Weeks 11-12)
- [ ] Web search integration (WebSearchTool + Tavily)
- [ ] Voice message support (Whisper)
- [ ] Usage dashboard (/stats)
- [ ] Automated backups
- [ ] WhatsApp / Discord adapters (optional)
- [ ] End-to-end testing & security audit

---

## 20. References & Sources

### Official Documentation
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) — Agent framework docs
- [OpenAI Agents SDK — Multi-Agent Orchestration](https://openai.github.io/openai-agents-python/multi_agent/) — Handoffs, Agent.as_tool()
- [OpenAI Agents SDK — Guardrails](https://openai.github.io/openai-agents-python/guardrails/) — Input/output/tool guardrails
- [OpenAI Agents SDK — MCP](https://openai.github.io/openai-agents-python/mcp/) — MCP integration guide
- [OpenAI GPT-5 Models](https://platform.openai.com/docs/models/gpt-5) — Model capabilities
- [OpenAI Responses API](https://platform.openai.com/docs/guides/latest-model) — GPT-5.2+ guide
- [OpenAI Agent Safety](https://platform.openai.com/docs/guides/agent-builder-safety) — Safety best practices
- [MCP Specification](https://modelcontextprotocol.io/specification/2025-11-25) — Protocol spec

### Frameworks & Libraries
- [Google Workspace MCP Server](https://github.com/taylorwilsdon/google_workspace_mcp) — 12+ Google services via MCP
- [Google Workspace CLI (Official)](https://github.com/googleworkspace/cli) — Google's official CLI with MCP
- [Mem0](https://github.com/mem0ai/mem0) — Universal memory layer for AI agents
- [Mem0 Research Paper](https://arxiv.org/abs/2504.19413) — Production-ready memory architecture
- [aiogram](https://github.com/aiogram/aiogram) — Async Telegram bot framework
- [APScheduler](https://github.com/agronholm/apscheduler) — Python task scheduling

### Research & Analysis
- [Top 5 AI Agent Frameworks 2026 — Intuz](https://www.intuz.com/blog/top-5-ai-agent-frameworks-2025)
- [AI Agent Memory: Types & Best Practices 2026 — 47Billion](https://47billion.com/blog/ai-agent-memory-types-implementation-best-practices/)
- [Superagent: Guardrails for Agentic AI — HelpNetSecurity](https://www.helpnetsecurity.com/2025/12/29/superagent-framework-guardrails-agentic-ai/)
- [Agentic AI Frameworks: Enterprise Guide 2026 — SpaceO](https://www.spaceo.ai/blog/agentic-ai-frameworks/)
- [Docker Compose for AI Agents — dev.to](https://dev.to/jasdeepsinghbhalla/docker-compose-for-ai-agents-from-local-prototype-to-production-in-one-workflow-3a4m)
- [Assistants API Deprecation (Aug 2026)](https://community.openai.com/t/assistants-api-beta-deprecation-august-26-2026-sunset/1354666)
- [Graph Memory for AI Agents — Mem0 Blog](https://mem0.ai/blog/graph-memory-solutions-ai-agents)
- [MCP on Wikipedia](https://en.wikipedia.org/wiki/Model_Context_Protocol) — Protocol history & adoption

---

## 21. Gaps Analysis — Issues Found During PRD Review

> This section documents every ambiguity, missing decision, contradiction, or undefined interface found when analyzing the research for PRD-readiness. Each gap is tagged with severity and the PRD section that resolves it.

### Category A: Undefined Contracts & Data Models

| # | Gap | Severity | Detail |
|---|-----|----------|--------|
| A1 | **No database schema defined** | High | PostgreSQL is mentioned for episodic memory, job store, audit, and persona — but no tables, columns, or migrations are specified. An AI agent cannot generate code without knowing the schema. |
| A2 | **Tool manifest format undefined** | High | `tools/stock_checker/manifest.json` is referenced but the JSON structure is never specified. What fields? How does the orchestrator discover and load tools at startup? |
| A3 | **Persona storage format undefined** | Medium | The PERSONA_TEMPLATE shows placeholders but doesn't define how persona data is stored in PostgreSQL/Mem0, how it's versioned, or the update API. |
| A4 | **Message router interface undefined** | Medium | The `MessageRouter` class is sketched but the `normalize()` and `format()` method contracts are not defined. What does a normalized message look like? |
| A5 | **Scheduled job payload undefined** | Medium | APScheduler jobs reference functions like `send_telegram_message` and `summarize_new_emails` but the callable signature, arguments schema, and error handling pattern are not defined. |

### Category B: Missing Architectural Decisions

| # | Gap | Severity | Detail |
|---|-----|----------|--------|
| B1 | **Single-process vs multi-process unclear** | High | Is the assistant one Python process running everything (bot, scheduler, agents), or are these separate containers/processes? This affects error isolation and resource limits. |
| B2 | **Agent registration at runtime undefined** | High | When Tool Factory creates a new CLI tool, how does the orchestrator learn about it without a restart? Hot-reload mechanism is not specified. |
| B3 | **Conversation state management unclear** | High | The SDK supports Sessions, but it's unclear: is conversation state stored in Redis? PostgreSQL? How long does a "conversation" last? When does it reset? |
| B4 | **Handoff vs Agent.as_tool() boundary unclear** | Medium | Research says use both, but doesn't define which specialist agents get handoffs (take over conversation) vs which are called as tools (return result). This must be decided per agent. |
| B5 | **Error propagation strategy undefined** | Medium | When a specialist agent fails (e.g., Gmail API error), what happens? Does the orchestrator retry? Tell the user? Fall back to another approach? |
| B6 | **Concurrent request handling undefined** | Medium | What happens if user sends 3 messages rapidly? Queue them? Process in parallel? This matters for scheduling + long-running tasks. |

### Category C: Security & Safety Gaps

| # | Gap | Severity | Detail |
|---|-----|----------|--------|
| C1 | **Allowlist management undefined** | High | "Telegram user ID allowlist" is mentioned but: where is it stored? How is it configured? Can the owner add users via Telegram? Is it in .env, DB, or config file? |
| C2 | **Cost control mechanism undefined** | High | "Daily cost caps" mentioned as risk mitigation but no implementation specified. How are API costs tracked? What happens when the cap is hit? |
| C3 | **Sandbox isolation for Tool Factory unclear** | Medium | CLI tools run via subprocess, but are they in the same container? Separate Docker container? What UID/permissions? What network access? |
| C4 | **OAuth token storage security undefined** | Medium | Google tokens are in a Docker volume, but: encrypted at rest? Access controls? What happens on token theft? |
| C5 | **Safety policy YAML format undefined** | Medium | Superagent-style declarative policies mentioned but no schema or example provided. |

### Category D: Configuration & Environment

| # | Gap | Severity | Detail |
|---|-----|----------|--------|
| D1 | **Complete .env template missing** | High | Docker Compose references env vars but a comprehensive list of ALL required environment variables with descriptions is not provided. |
| D2 | **Config file structure undefined** | Medium | `config/` directory is referenced in Dockerfile but contents (what files, what format, what goes in each) are not specified. |
| D3 | **Timezone handling undefined** | Low | Scheduling references "every Monday at 9am" but user timezone is never captured or stored. |

### Category E: Phasing & Dependency Issues

| # | Gap | Severity | Detail |
|---|-----|----------|--------|
| E1 | **Web search is Phase 6 but orchestrator uses it in Phase 1** | Medium | The orchestrator code in Section 5 includes `web_search` in its tools list, but web search integration is Phase 6. Needs to be Phase 1 or removed from early orchestrator. |
| E2 | **Memory is Phase 3 but persona needs it in Phase 1** | Medium | The persona system loads from memory, but Mem0 integration is Phase 3. Phase 1 persona needs a simpler fallback (config file). |
| E3 | **ACE self-improvement loop has no acceptance criteria** | Medium | "Reflector evaluates quality" — but quality against what metric? What triggers a persona update vs. ignoring feedback? Thresholds are undefined. |
| E4 | **Backup strategy references Google Drive but that's Phase 2** | Low | Phase 6 backup to Google Drive depends on Phase 2 integration working. If Google integration fails, backup fails. Need local backup fallback. |

### Contradiction Found

| # | Issue | Detail |
|---|-------|--------|
| X1 | **Tool Factory guardrails block `subprocess` but CLI tools depend on it** | Section 15 `BLOCKED_IMPORTS` includes `subprocess`, but CLI tools are wrapped via `subprocess.run()`. The wrapper is in the agent process, not the generated tool — but this needs explicit clarification that the block applies only to generated tool code, not wrapper code. |

### Resolution

All gaps above are resolved in the companion document **`PRD_PersonalAssistant.md`** with concrete specifications, schemas, and acceptance criteria.

---

> **Last updated:** March 16, 2026 (revised — CLI-first tools, self-hosted-only DB/memory, gaps analysis added)  
> **Research confidence:** High — all recommendations validated against multiple 2025-2026 sources.  
> **Constraints applied:** (1) All databases/memory self-hosted in Docker only — no SaaS. (2) CLI tools always prioritized over MCP for specialized agent tool creation.  
> **Next step:** Resolve gaps via PRD, then begin Phase 1 implementation.
