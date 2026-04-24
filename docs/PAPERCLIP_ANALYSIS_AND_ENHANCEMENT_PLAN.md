# PersonalAsst — Advanced Organization Wizard Plan
## (Based on PaperClip Analysis · Updated April 13, 2026)

> **Scope note:** The Organization Wizard feature (multi-step UI to create orgs + assign agents) is **not yet implemented**. The basic Org CRUD and the Agents tab in the Dashboard are complete. This plan covers what remains.

---

## Current State (April 13, 2026)

### ✅ Already Built
| Feature | Location |
|---|---|
| Organizations CRUD (create, pause, resume, delete) | `src/orchestration/api.py` + `src/db/models.py` (Org, OrgAgent) |
| OrgAgent create/update/delete | `src/orchestration/api.py` |
| Telegram `/orgs` lifecycle commands | `src/bot/handlers.py` |
| Agents tab in Dashboard (system + org agents) | `src/orchestration-ui/src/Dashboard.js` — `AgentsTab` |
| System agent registry | `src/orchestration/system_agents.py` |
| Durable delete audit trail | `audit_log` table via `src/orchestration/api.py` |
| Org ownership scoping (X-Telegram-Id) | `src/orchestration/api.py` |
| Active-org agent deletion guard (409) | `src/orchestration/api.py` |
| M1 Parallel fan-out | `src/agents/parallel_runner.py` |
| M2 Autonomous background jobs | `src/agents/background_job.py` |
| M3 Agent trace persistence | `src/db/models.py` AgentTrace + `src/orchestration/api.py` |
| M4 Risk-classified self-healing | `src/repair/engine.py` + `src/repair/verifier.py` |
| FastAPI Dashboard API | `src/orchestration/api.py` (port 8000) |
| React Dashboard UI (9 tabs) | `src/orchestration-ui/src/Dashboard.js` (port 3001) |

### ❌ Not Yet Built — Advanced Organization Wizard
The wizard is a multi-step onboarding flow for creating an organization with agents in a single guided experience, rather than the current separate Org create + manual agent assignment.

---

## Project Structure (Updated April 13, 2026)

All deployment files live under `src/`. Root contains only project-level files.

```
src/
├── orchestration-ui/src/Dashboard.js   # React UI — 9 tabs incl. Agents tab
├── orchestration/
│   ├── api.py                          # All /api/* endpoints
│   └── system_agents.py               # Built-in system agent registry
├── agents/                             # 19 agents (orchestrator, skills, handoffs, etc.)
├── db/
│   └── models.py                       # Org, OrgAgent, AgentTrace, BackgroundJob, ...
├── config/                             # persona_default.yaml, safety policies
├── user_skills/                        # User-created SKILL.md files (volume-mounted)
├── alembic.ini                         # Alembic config (script_location = src/db/migrations)
└── alembic/                            # Migration env + versions
```

Docker Compose services (all use `COPY src/ ./src/`):
- `orchestration-api` — build context: repo root → `Dockerfile.orchestration`
- `orchestration-ui` — build context: `./src/orchestration-ui`
- `assistant` — build context: repo root → `Dockerfile`

---

## What Remains: Advanced Organization Wizard

### Phase 1 — Wizard UI (Dashboard)

**Goal:** Replace the current two-step "Create Org → then manually add agents" flow with a 3-step wizard modal inside the Dashboard Agents tab (or Orgs tab).

#### Step 1: Organization Identity
- Name, description, status (active/paused)
- Industry / purpose (optional)

#### Step 2: Assign Agents
- Multi-select from the system agent registry (`GET /api/agents/system`)
- Show agent capabilities and tool counts
- Optionally set agent role/instructions for this org context

#### Step 3: Review & Create
- Summary card before submit
- `POST /api/orgs` → then `POST /api/orgs/{id}/agents` for each selected agent
- Redirect to Orgs tab on success

**Files to add/change:**
```
src/orchestration-ui/src/Dashboard.js    # Add OrgWizardDialog component
```

**No backend changes needed** — existing endpoints are sufficient.

---

### Phase 2 — Governance Layer (Future)

Org-level human approval gates for agent actions (inspired by PaperClip):

```python
# src/orchestration/governance.py  (not yet created)
class GovernanceLayer:
    async def approve_agent_action(self, org_id: int, action: str) -> bool
    async def override_budget(self, org_id: int, new_budget: float) -> bool
    async def pause_agent(self, org_id: int, agent_id: int, reason: str) -> bool
```

DB changes needed: `approval_gates` table, `org_budget` column on `Org`.

---

### Phase 3 — Per-Org Task Queue (Future)

Goal-ancestry task tracking per organization, inspired by PaperClip's atomic execution model:

```python
# src/orchestration/task_manager.py  (not yet created)
@dataclass
class OrgTask:
    id: str
    org_id: int
    title: str
    goal_ancestry: list[str]     # e.g. ["org:sales", "goal:outreach", "task:email-draft"]
    assigned_agent_id: int | None
    status: TaskStatus
    budget_allocated: float
```

DB changes needed: `org_tasks` table + Alembic migration.

---

### Phase 4 — Cost Control Per Org (Future)

```python
# src/orchestration/budget_manager.py  (not yet created)
class OrgBudgetManager:
    async def track_usage(self, org_id: int, agent_id: int, cost: float) -> None
    async def enforce_budget(self, org_id: int) -> bool
    async def get_spend_report(self, org_id: int) -> SpendReport
```

Requires: `org_spend` table + `daily_cost_cap` column on `Org`.

---

## Docker Deployment (Current — No Changes Needed for Wizard)

```yaml
# docker-compose.yml (current, correct)
services:
  orchestration-api:
    build:
      context: .
      dockerfile: Dockerfile.orchestration
    ports: ["8000:8000"]

  orchestration-ui:
    build:
      context: ./src/orchestration-ui   # ← updated April 13
    ports: ["3001:80"]

  assistant:
    build:
      context: .
      dockerfile: Dockerfile
```

The wizard UI only requires changes inside `src/orchestration-ui/src/Dashboard.js`.  
No new containers, no new Dockerfiles, no new backend endpoints for Phase 1.

---

## Security Constraints (Unchanged)

- Single-user system — no multi-tenancy at auth layer
- All org operations scoped to `X-Telegram-Id` owner resolution
- Agent deletion blocked if org is `active` (409 guard)
- Audit log entries written before any destructive operation
- No agent-to-agent communication without orchestrator mediation

---

## Implementation Priority

| Phase | Effort | Status |
|---|---|---|
| Wizard UI (3-step modal) | Dashboard.js + `POST /api/orgs/wizard` | ✅ **Completed April 13, 2026** |
| Governance Layer | Medium — new module + DB migration | Future |
| Per-Org Task Queue | Medium — new model + API endpoints | Future |
| Cost Control Per Org | Medium — new model + budget tracking | Future |

---

## Phase 1 — Implementation Notes (April 13, 2026)

### Backend
- **`POST /api/orgs/wizard`** added to `src/orchestration/api.py` — registered *before* parameterized routes to avoid FastAPI path-param collisions.
- **`OrgWizardRequest`** / **`OrgWizardResponse`** / **`WizardAgentConfig`** Pydantic models added to `api.py`.
- Atomic transaction: org row created → activity logged → OrgAgent rows created for each selected system agent → activity logged → single `commit()`.
- `get_system_agent_by_id` added to top-level import from `system_agents.py`.

### Frontend (`src/orchestration-ui/src/Dashboard.js`)
- **`OrgWizardDialog`** component added (~265 lines) with `MUI Stepper` (3 steps).
- **Step 0 (Identity):** Name (required), Goal, Description.
- **Step 1 (Agents):** Multi-select cards grouped by category (Google Workspace / Internal / Utility). Checkbox + click-to-toggle. Live count badge.
- **Step 2 (Review):** Org summary card + agent list with per-agent optional role override field. Submit calls `POST /api/orgs/wizard`.
- `OrgsTab` "New Organization" button now opens `OrgWizardDialog` instead of the old `OrgForm` dialog.
- Old `orgDialogOpen` state and `OrgForm` dialog removed from `Dashboard`.
- New MUI imports: `Stepper`, `Step`, `StepLabel`, `Checkbox`, `ListItemButton`, `ListItemIcon`, `Divider`, `AutoAwesome`, `Group`.

### Smoke Test
```
POST /api/orgs/wizard  →  {"org_id": N, "org_name": "...", "status": "active", "agents_created": 2, "agent_names": [...]}
```
Verified atomically creates org + 2 OrgAgent rows + 2 audit log entries in one DB transaction.
