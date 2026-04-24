# ADR: Dashboard Enhancement Phases 1–8

**Date:** 2026-04-23
**Status:** Accepted

## Context

The PersonalAsst dashboard needed several enhancements to improve usability, cost visibility, and organization management capabilities.

## Decisions

### Phase 1 — Tool Wizard
- Added `ToolWizardDialog` to the Dashboard Tools tab with interview → generate → review → save flow.
- Backend: `POST /api/tools/wizard/generate` calls GPT-4o-mini with structured JSON output.

### Phase 2a — Cost Visibility
- Raised cost-tracking log level from DEBUG to INFO/WARNING to surface budget-not-logging bugs.
- Expanded `_OPENAI_MODEL_PRICING` with GPT-5.4 family, Claude Opus 4, and OpenRouter models.

### Phase 2b — Shared Cost Helper
- Extracted `record_llm_cost()` into `src/models/cost_tracker.py` to eliminate duplicated pricing tables.
- Single function handles: pricing lookup, token extraction, DB upsert, Redis tracking, diagnostic logging.
- Wired into orchestrator; old 70-line inline block removed.

### Phase 3 — Duplicate Detection
- `setup_org_project` fuzzy-matches agents, tools, and skills (≥ 85% difflib similarity).
- Reuses existing items instead of creating near-duplicates; reports what was reused.

### Phase 4 — Selective Org Deletion
- **Decision:** Use a "holding org" pattern (`__retained__`) instead of making `org_id` nullable.
- **Why:** Avoids schema migration; `org_id NOT NULL` + `CASCADE` FK constraints remain intact.
- `GET /api/orgs/{id}/delete-preview` returns agents, tasks, activity count.
- `DELETE /api/orgs/{id}` accepts optional `retain_agent_ids` / `retain_task_ids` body.
- Retained items are moved to a system `__retained__` org (hidden from listing) before cascade-delete.
- Dashboard shows a preview dialog with checkboxes for selective retention.

### Phase 5 — Manual Ticket Creation
- `NewTicketDialog` in Repairs tab allows opening tickets with choice of pipeline (AI Agent / Admin).

### Phase 6 — Interactions Drill-Down
- Clickable Interactions tile on Overview opens a drawer with audit-log rows and filters.
- Each row supports inline trace drill-down.

### Phase 7 — Tasks vs Jobs Clarity
- Added tooltips and README_ORCHESTRATION section explaining the distinction.

### Phase 8 — Draggable/Resizable Grid
- **Decision:** Use `react-grid-layout` (Responsive + WidthProvider) for the Overview tab.
- **Why:** Mature library, supports responsive breakpoints, drag handles, and JSON-serializable layouts.
- 6 tiles: costs, quality, tools, schedules, budget, persona.
- Layout persisted per-user in Redis via `GET/PUT /api/dashboard/layout` (1-year TTL).
- Drag handle restricted to tile headers (`.grid-drag-handle`) to avoid interfering with content interaction.
- "Reset Layout" button returns to defaults.

## Tradeoffs

- **Holding org pattern (Phase 4):** Slightly less clean than nullable FK, but zero migration and zero risk of breaking existing cascades.
- **Redis for layout (Phase 8):** Simple and fast, but lost if Redis is flushed. Acceptable for a non-critical preference; user can reset.
- **Debounced save (Phase 8):** 1.2s delay avoids excessive API calls during rapid drag operations.
