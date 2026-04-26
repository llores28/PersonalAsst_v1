# PersonalAsst - Enhanced with PaperClip-Inspired Orchestration

> Note: This document provides orchestration context. For the latest production-ready hardening, security, and operations updates, refer to `README.md`, `docs/RUNBOOK.md`, and `docs/CHANGELOG.md`.

## Overview

PersonalAsst has been enhanced with PaperClip-inspired agent orchestration capabilities, transforming it from a single-user assistant into a multi-agent orchestration platform while maintaining its security-first Docker deployment model.

## What's New

### 🚀 Multi-Agent Orchestration
- **Agent Registry**: Define agents with roles, capabilities, and hierarchies
- **Task Management**: Create, assign, and track tasks with goal ancestry
- **Governance Layer**: Human oversight with approval gates and budget controls
- **Web Dashboard**: React-based UI for managing agents and tasks

### 🏢 Organizational Structure
- **Org Chart**: Visual hierarchy of agents (CEO, CTO, Developers, Specialists)
- **Roles & Responsibilities**: Each agent has defined capabilities and budget
- **Cost Control**: Per-agent budgets with enforcement at infrastructure level
- **Audit Trail**: Complete logging of all decisions and actions

### 🎯 Key Features
- **Atomic Execution**: No duplicate work through task checkout system
- **Persistent State**: Agents maintain context across sessions
- **Goal-Aware Tasks**: Every task carries its full goal ancestry
- **Real-time Updates**: WebSocket-based live dashboard
- **Mobile Ready**: Access orchestration from any device

## Tasks vs Scheduled Jobs vs Background Jobs

Atlas has **three distinct concepts** that are easy to confuse. The Dashboard surfaces all three, and each lives in its own table in Postgres:

| Concept | Table | Trigger | Typical example | Where to see it |
|---|---|---|---|---|
| **Organization Task** | `org_tasks` | Manual or agent-created; marked `pending → in_progress → completed` | "Write the FFmpeg preset guide" inside the FFmpeg Video Composer org | Click an Organization in the **Organizations** tab |
| **Scheduled Job** | `scheduled_tasks` | Time (APScheduler cron/interval/once) | "Remind me every Monday at 9am to send the weekly summary" | `/schedules` from Telegram; internal to the scheduler |
| **Background Job** | `background_jobs` | Event-driven long-running loop with tick + termination condition | "Monitor my inbox until John replies, then notify me" | **Jobs** tab in the Dashboard |

### When to use which

- **Task** — Something *someone* (you or an agent) should do as part of a project. Owns a title, priority, optional assignee, and completion state. Created via `setup_org_project`, the Organizations dialog, or the `add_org_task` tool.
- **Scheduled Job** — Something that should fire *at a point in time*, repeatedly or once. Owns a cron/interval/once trigger. Created via natural language ("remind me…") or `/schedule`.
- **Background Job** — Something that should *keep running* in the background until a condition is met. Has its own tick loop, fault tolerance, and notification on completion. Created automatically when you say "monitor…" / "watch…" / "keep checking…".

All three are fully independent — a single workflow can combine them (e.g. a Task is unblocked by a Scheduled Job which itself kicks off a Background Job).

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Telegram UI    │    │   Web Dashboard  │    │   Mobile UI     │
│   (Existing)     │    │     (New)       │    │   (Future)      │
└─────────┬───────┘    └─────────┬────────┘    └─────────┬───────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Orchestration API     │
                    │   (FastAPI + SQLAlchemy) │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │    Agent Registry       │
                    │    Task Manager          │
                    │    Governance Layer      │
                    └────────────┬────────────┘
                                 │
          ┌────────────────┼────────────────┐
          │                │                │
    ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
    │   Atlas   │  │  Agents   │  │   Tasks   │
    │ (Orchestrator)│  │ (Multi)   │  │ (Queue)   │
    └───────────┘  └───────────┘  └───────────┘
```

## Quick Start

### 1. Deploy with Docker Compose

```bash
# Clone and setup
git clone <repository>
cd PersonalAsst

# Copy environment file
cp .env.example .env
# Edit .env with your API keys

# Start all services
docker compose up -d
```

Services started:
- **assistant**: Main PersonalAsst bot (port varies)
- **orchestration-api**: Orchestration API server (port 8000)
- **orchestration-ui**: Web dashboard (port 3000)
- **postgres**: Database
- **redis**: Cache/sessions
- **qdrant**: Vector storage
- **workspace-mcp**: Google Workspace integration

### 2. Access the Interfaces

#### Telegram Bot (Existing)
- Continue using your existing Telegram bot
- All current commands work unchanged
- Organization lifecycle is available via `/orgs` (`create`, `info`, `pause`, `resume`, `delete`)

#### Web Dashboard (New)
- Open http://localhost:3001
- View agent org chart
- Create and manage tasks
- Monitor costs and performance
- Real-time updates

#### API (New)
- REST API at http://localhost:8000
- OpenAPI docs at http://localhost:8000/docs
- WebSocket endpoint at ws://localhost:8000/ws

##### Scheduler health endpoints

Two complementary endpoints; pick the one that matches the question you're asking:

| Endpoint | Auth | Source | Answers |
|---|---|---|---|
| `GET /api/health/scheduler` | Public | Redis observability records (`scheduler_health:{schedule_id}`) | "Are my scheduled jobs healthy?" — per-job last_status, consecutive_failures, total_runs/failures. Returns `status: healthy / degraded / unknown`. Best for monitoring tools. See [ADR-2026-04-26-scheduler-observability.md](docs/ADR-2026-04-26-scheduler-observability.md). |
| `GET /api/scheduler/health` | API key required | Postgres `scheduled_tasks` + APScheduler runtime | "Is the scheduler container alive and registering jobs?" — runtime liveness, active task count, upcoming runs. Also embeds the observability snapshot under `per_job_health` so the dashboard gets both views in one call. |

The dashboard polls `/api/scheduler/health` because it wants both runtime liveness AND per-job health. External monitoring tools should hit `/api/health/scheduler` (public, observability-only).

### 3. Create Your First Agent Team

```python
# Example: Create a development team
import asyncio
from src.orchestration.agent_registry import AgentRegistry, AgentDefinition, AgentRole

async def setup_dev_team():
    registry = AgentRegistry(get_session_factory())
    
    # Create CTO
    cto = await registry.create_agent(AgentDefinition(
        company_id="default",
        name="Atlas CTO",
        role=AgentRole.CTO,
        description="Technical lead overseeing development",
        capabilities=["architecture", "code_review", "technical_decisions"],
        monthly_budget=300.0
    ))
    
    # Create Developers
    dev1 = await registry.create_agent(AgentDefinition(
        company_id="default",
        name="Code Developer 1",
        role=AgentRole.DEVELOPER,
        description="Full-stack developer",
        capabilities=["coding", "testing", "debugging"],
        parent_agent_id=cto.id,
        monthly_budget=150.0
    ))
    
    dev2 = await registry.create_agent(AgentDefinition(
        company_id="default",
        name="Code Developer 2",
        role=AgentRole.DEVELOPER,
        description="Frontend specialist",
        capabilities=["react", "ui_ux", "testing"],
        parent_agent_id=cto.id,
        monthly_budget=150.0
    ))
    
    return [cto, dev1, dev2]

# Run the setup
team = asyncio.run(setup_dev_team())
print(f"Created team with {len(team)} agents")
```

## Agent Roles

### CEO (Chief Executive)
- Oversees all operations
- Strategic decision making
- Budget approval
- Can hire/fire other agents

### CTO (Chief Technology)
- Technical leadership
- Architecture decisions
- Code review oversight
- Manages development team

### Developer
- Code implementation
- Testing and debugging
- Feature development
- Reports to CTO

### Analyst
- Data analysis
- Research tasks
- Reporting
- Can work independently

### Coordinator
- Task coordination
- Resource allocation
- Progress tracking
- Cross-team communication

### Specialist
- Domain-specific expertise
- Specialized tools (Drive, Gmail, etc.)
- Focused capabilities
- Reports to relevant lead

### Assistant
- General assistance
- User support
- Basic tasks
- Learning and growth

## Task Management

### Goal Ancestry
Every task carries its full goal ancestry:
```
["company:mission", "project:feature", "task:implement"]
```

This gives agents context about why they're doing something, not just what.

### Task Lifecycle
1. **Created**: Task defined with goal ancestry
2. **Pending**: Waiting for assignment
3. **Assigned**: Checked out by specific agent
4. **In Progress**: Agent working on task
5. **Completed**: Task finished with result
6. **Failed**: Task failed with error

### Atomic Execution
- Tasks are checked out atomically
- No two agents work on the same task
- Prevents duplicate work and wasted compute

## Cost Control

### Per-Agent Budgets
- Each agent has monthly budget
- Soft warning at 80% utilization
- Hard stop at 100% (requires override)
- Real-time cost tracking

### Budget Enforcement
```python
# Agent budget enforcement happens at infrastructure level
if agent.current_month_spend >= agent.monthly_budget:
    raise BudgetExceeded(f"Agent {agent.id} exceeded budget")
```

## Security Model

### Maintained from Original
- **Docker Containerization**: All services in containers
- **Input/Output Guardrails**: Safety checks on all I/O
- **Cost Caps**: Per-user and per-agent limits
- **User Allowlist**: Only authorized users
- **No Secrets in Code**: All via environment variables

### New Security Features
- **Agent Isolation**: Agents can't access each other's data
- **Task Authorization**: Only assigned agents can access tasks
- **Audit Logging**: Complete audit trail of all actions
- **Governance Controls**: Human approval required for critical actions

## API Examples

### Create Agent
```bash
curl -X POST http://localhost:8000/api/companies/default/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Security Specialist",
    "role": "specialist",
    "description": "Handles security audits and compliance",
    "capabilities": ["security_audit", "compliance_check"],
    "monthly_budget": 200.0
  }'
```

### Create Task
```bash
curl -X POST http://localhost:8000/api/companies/default/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Security Audit",
    "description": "Perform quarterly security audit",
    "goal_ancestry": ["company:mission", "project:security", "task:audit"],
    "priority": "high",
    "budget_allocated": 50.0
  }'
```

### Assign Task
```bash
curl -X POST http://localhost:8000/api/tasks/{task_id}/assign \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "security-specialist-id"}'
```

## Migration from Single-Agent

### Backward Compatibility
- Existing Telegram interface unchanged
- All current commands continue working
- Gradual migration path available

### Migration Steps
1. **Phase 1**: Deploy orchestration services
2. **Phase 2**: Create agent organization
3. **Phase 3**: Migrate tasks to multi-agent
4. **Phase 4**: Optimize agent workflows

## Monitoring

### Dashboard Metrics
- Agent utilization
- Task completion rates
- Cost tracking
- Performance metrics

### Health Checks
```bash
# API health
curl http://localhost:8000/api/health

# Service status
docker compose ps
```

### Logs
```bash
# Orchestration logs
docker compose logs orchestration-api

# Dashboard logs
docker compose logs orchestration-ui
```

## Development

### Local Development
```bash
# Start orchestration API only
cd src/orchestration
python -m uvicorn api:app --reload --host 0.0.0.0 --port 8000

# Start React UI
cd orchestration-ui
npm start
```

### Database Migrations
```bash
# Apply new orchestration tables
docker compose exec assistant alembic upgrade head
```

### Testing
```bash
# Run orchestration tests
python -m pytest tests/test_orchestration.py -v

# Run integration tests
python -m pytest tests/test_integration.py -v
```

## Dashboard Enhancement APIs (April 2026)

### Layout Persistence
```bash
# Get saved layout
curl http://localhost:8000/api/dashboard/layout \
  -H "X-Telegram-Id: 123456789"

# Save layout
curl -X PUT http://localhost:8000/api/dashboard/layout \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Id: 123456789" \
  -d '{"layouts": {"lg": [...], "md": [...], "sm": [...]}}'
```

### Selective Org Deletion
```bash
# Preview what will be deleted
curl http://localhost:8000/api/orgs/42/delete-preview

# Delete with retention
curl -X DELETE http://localhost:8000/api/orgs/42 \
  -H "Content-Type: application/json" \
  -d '{"retain_agent_ids": [1, 3], "retain_task_ids": [5]}'
```

### Activity Feed
```bash
# Get recent activity (defaults: all directions, limit 50)
curl "http://localhost:8000/api/activity?direction=inbound&limit=20"
```

### Tool Wizard
```bash
# Generate a tool from interview answers
curl -X POST http://localhost:8000/api/tools/wizard/generate \
  -H "Content-Type: application/json" \
  -d '{"answers": {"name": "my_tool", "description": "...", ...}}'
```

### Manual Repair Ticket
```bash
curl -X POST http://localhost:8000/api/repairs \
  -H "Content-Type: application/json" \
  -d '{"title": "Fix login bug", "description": "...", "pipeline": "ai_agent"}'
```

## Self-Healing Repair Pipeline (April 2026)

The Repairs tab is backed by a four-stage agent pipeline:

| Stage | Agent | File | Output |
|-------|-------|------|--------|
| 1. Audit | DebuggerAgent | `src/agents/debugger_agent.py` | `DebugAnalysis` (root cause, affected files, severity, confidence) |
| 2. Plan  | RepairAgent | `src/agents/repair_agent.py` | RepairTicket with classified risk |
| 3. Fix   | ProgrammerAgent | `src/agents/programmer_agent.py` | `FixProposal` (unified diff + file-type aware test plan) |
| 4. QA    | QualityControlAgent | `src/agents/quality_control_agent.py` | `ValidationDecision` (GO / NO_GO / NEEDS_REVISION) |

After validation passes, the patch is stored as a pending repair in Redis. Owner approval (`apply patch` in Telegram → security PIN) triggers `execute_pending_repair()`, which applies the diff in-place, runs verification, and rolls back on failure.

**File-type aware verification (added 2026-04-23):** verification commands now route through `python -m src.repair.verify_file <path>` by default, which dispatches by extension (`.py` → syntax check, `SKILL.md` → loader validation, `.yaml`/`.json`/`.toml` → structural parse). Stdlib + pyyaml only — works in the runtime container where ruff/mypy aren't installed. If verification fails because the runner doesn't apply (`failure_kind=missing_tool`), `RepairAgent.refine_pending_verification` swaps the verification commands without re-proposing the patch — the owner just says `apply patch` again.

## DB Migrations (April 2026)

| Revision | File | Adds |
|----------|------|------|
| 006_user_settings | `src/db/migrations/versions/006_add_user_settings.py` | `user_settings` (per-user preferences, daily/monthly cost caps) |
| 007_governance_spend_ancestry | `src/db/migrations/versions/007_governance_spend_ancestry.py` | Cost-attribution columns linking spend rows to ticket / org / job ancestry |
| 008_add_missing_columns | `src/db/migrations/versions/008_add_missing_columns.py` | Backfill of missing columns flagged by audit |
| 009_add_tts_voice | `src/db/migrations/versions/009_add_tts_voice.py` | TTS voice preference column on `user_settings` |
| 010_add_agent_traces | `src/db/migrations/versions/010_add_agent_traces.py` | `agent_traces` table — per-tool-call thought trace for Dashboard Timeline drawer |

Run `alembic upgrade head` (config at `src/alembic.ini` → `script_location = src/db/migrations`).

## Multi-LLM via OpenRouter (April 2026)

Provider routing (`src/models/provider_resolution.py`) selects between OpenAI, Anthropic, and 15+ OpenRouter models based on task complexity and capability tier (`src/config/openrouter_capabilities.yaml`). Cost tracking is unified through `src/models/cost_tracker.py:record_llm_cost()` which writes to PostgreSQL `daily_costs` and Redis per-provider counters. Set `OPENROUTER_API_KEY` to enable; `OPENROUTER_IMAGE_ENABLED=true` enables image generation/analysis.

### Dashboard UI Features
- **Draggable/Resizable Overview Grid** — 6 tiles (costs, quality, tools, schedules, budget, persona) powered by react-grid-layout. Drag headers to rearrange, resize by dragging edges. Layout persisted per-user in Redis.
- **Tool Wizard** — AI-guided dialog in Tools tab: interview → generate → review → save.
- **Selective Org Deletion** — Preview dialog shows agents, tasks, activity count. Check items to retain before confirming delete.
- **Manual Tickets** — "New Ticket" button in Repairs tab for opening repair tickets manually.
- **Interactions Drill-Down** — Click the Interactions tile on Overview → drawer with audit-log rows, filters (all/inbound/outbound/errors), and trace drill-down.
- **Duplicate Detection** — When setting up org projects, existing agents/tools/skills with ≥ 85% name similarity are reused.

## Roadmap

### Completed
- ✅ Basic agent registry
- ✅ Task management
- ✅ Web dashboard
- ✅ Cost control
- ✅ Governance layer
- ✅ Tool Wizard + Manual Tickets
- ✅ Selective Org Deletion + Duplicate Detection
- ✅ Draggable/Resizable Overview Grid
- ✅ Interactions Drill-Down + Activity API

### Future
- ⏳ Agent skill learning
- ⏳ Mobile app
- ⏳ AI-powered agent optimization
- ⏳ Advanced analytics

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

### Areas for Contribution
- Agent skill definitions
- UI/UX improvements
- Performance optimizations
- Documentation
- Testing

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Support

- **Documentation**: [docs/](docs/)
- **Issues**: [GitHub Issues](https://github.com/llores28/PersonalAsst_v1/issues)
- **Discussions**: [GitHub Discussions](https://github.com/llores28/PersonalAsst_v1/discussions)

## Acknowledgments

- Inspired by [PaperClip](https://github.com/paperclipai/paperclip) for orchestration concepts
- Built with FastAPI, React, and Material-UI
- Powered by OpenAI and other AI providers
