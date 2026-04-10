# Orchestration Module Deep Audit — 2026-04-07

## Summary
23 gaps found across backend, frontend, Docker, and security layers.
**6 FATAL** (will crash at runtime), **6 CRITICAL** (wrong behavior), **6 MODERATE**, **5 LOW**.

---

## FATAL (Blocks all correct operation)

### F1: Sync SQLAlchemy `.query()` used with AsyncSession
**Files:** `agent_registry.py` (all methods), `api.py` (dashboard, get_agents, get_tasks)
**Problem:** Every DB method uses `session.query(Model).filter(...)` — the synchronous ORM API.
But sessions come from `async_sessionmaker(class_=AsyncSession)`. Async sessions require
`await session.execute(select(Model).where(...))`. Current code will raise
`MissingGreenlet` or `InvalidRequestError` on every single DB call.
**Fix:** Rewrite all queries to use `select()` + `await session.execute()` + `result.scalars()`.

### F2: Circular import in `__init__.py`
**File:** `src/orchestration/__init__.py`
**Problem:** Imports from both `agent_registry` AND `api`. But `api.py` creates module-level
objects (`Settings()`, `AgentRegistry(...)`, etc.) at import time. If `__init__.py` is loaded
first (e.g., by uvicorn's `src.orchestration.api:app`), it triggers `api.py` which triggers
`agent_registry.py` which may re-trigger `__init__.py` — circular chain.
**Fix:** Remove eager imports from `__init__.py`. Use lazy imports or remove `__init__.py` exports.

### F3: Missing `func` import in `api.py`
**File:** `api.py:402`
**Problem:** `func.sum(AgentDefinition.current_month_spend)` — `func` never imported from sqlalchemy.
Dashboard endpoint crashes with `NameError: name 'func' is not defined`.
**Fix:** Add `from sqlalchemy import func` or rewrite the aggregation.

### F4: Missing `timedelta` import in `agent_registry.py`
**File:** `agent_registry.py:391`
**Problem:** `timedelta(hours=current_tasks * 2)` — `timedelta` never imported.
Workload endpoint crashes with `NameError`.
**Fix:** Add `from datetime import datetime, timezone, timedelta`.

### F5: API endpoints receive body params as query params
**File:** `api.py:303-371`
**Problem:** `assign_task(task_id, agent_id)`, `complete_task(task_id, result, actual_cost)`,
`pause_agent(agent_id, reason, human_user_id)` — FastAPI treats bare `str`/`float` params
as query parameters. But the UI sends JSON bodies. Endpoints will return 422 Validation Error.
**Fix:** Create Pydantic request body models for each endpoint.

### F6: `SessionLocal` bound incorrectly
**File:** `api.py:35`
**Problem:** `SessionLocal = sessionmaker(bind=engine, class_=AsyncSession)` — you cannot bind
a sync `sessionmaker` to an async engine. The global `AgentRegistry(_session_factory)` gets
this broken factory. All methods in `agent_registry.py` that do `async with self.db() as session`
will fail because `sessionmaker` doesn't return an async context manager.
**Fix:** Use `async_sessionmaker` everywhere. Pass it to registry/task_manager/governance.

---

## CRITICAL (Wrong behavior, won't work as intended)

### C1: CORS allows port 3000, UI runs on 3001
**File:** `api.py:57`
**Problem:** `allow_origins=["http://localhost:3000", ...]` but `docker-compose.yml` maps UI to 3001.
All API calls from the dashboard are blocked by browser CORS policy.
**Fix:** Add `http://localhost:3001` to allowed origins.

### C2: UI hardcodes `http://localhost:8000` — bypasses nginx proxy
**Files:** `Dashboard.js:40`, `TaskQueue.js:69,87`
**Problem:** The nginx config proxies `/api` → `orchestration-api:8000`, but the UI calls
`http://localhost:8000` directly. This means:
- CORS is required (and currently wrong — see C1)
- Won't work if accessed from any machine other than localhost
- Bypasses nginx's proxy headers
**Fix:** Change API_BASE to `/api` (relative) so nginx handles the proxy.

### C3: OrgChart.js references undefined color variables
**File:** `OrgChart.js:58-63`
**Problem:** `purple`, `cyan`, `amber`, `grey` used in `getRoleColor()` but never imported.
Only `green, orange, red, blue` are imported. Rendering any agent with role
analyst/coordinator/specialist/assistant will crash with `ReferenceError`.
**Fix:** Import all needed colors from `@mui/material/colors`.

### C4: OrgChart.js imports non-existent icon
**File:** `OrgChart.js:14`
**Problem:** `AccountTreeNode` doesn't exist in `@mui/icons-material`. Build will fail
or produce a runtime error.
**Fix:** Remove the unused import.

### C5: MUI Chip `color` prop gets hex strings instead of theme tokens
**Files:** `TaskQueue.js:148-156`, `OrgChart.js:117`
**Problem:** `getStatusColor()` and `getPriorityColor()` return MUI color palette hex values
(e.g., `grey[500]` = `"#9e9e9e"`), but MUI's `Chip` component `color` prop only accepts
theme tokens: `"default"`, `"primary"`, `"secondary"`, `"error"`, `"info"`, `"success"`, `"warning"`.
Passing hex strings produces console warnings and wrong styling.
**Fix:** Map status/priority to MUI theme color tokens instead.

### C6: Two separate SQLAlchemy `Base` classes — metadata split
**Files:** `src/db/models.py` uses `class Base(DeclarativeBase)`,
`src/orchestration/agent_registry.py` uses `Base = declarative_base()`
**Problem:** These are completely separate metadata registries. Alembic's `target_metadata`
only knows about one. Future `alembic revision --autogenerate` will miss orchestration tables
or main app tables depending on which Base is configured.
**Fix:** Import and use the existing `Base` from `src.db.models`, or register both metadata
objects in `alembic/env.py`.

---

## MODERATE

### M1: No `.dockerignore` for orchestration-ui
**Dir:** `orchestration-ui/`
**Problem:** `node_modules/` (246MB) is sent as Docker build context every build. Wastes
~100 seconds on context transfer.
**Fix:** Add `.dockerignore` with `node_modules`, `.git`, `build`.

### M2: No authentication on orchestration API
**File:** `api.py`
**Problem:** Zero auth. Anyone on the network can create/delete agents, override budgets,
pause agents. Violates the project's security-first philosophy.
**Fix:** Add at minimum a shared secret / API key check middleware.

### M3: `OPENAI_API_KEY` exposed to orchestration container unnecessarily
**File:** `docker-compose.yml:126`
**Problem:** The orchestration API doesn't use OpenAI. Unnecessary secret exposure.
**Fix:** Remove from environment unless actually needed.

### M4: Duplicate `httpx` in requirements
**File:** `requirements-orchestration.txt:15,33`
**Problem:** `httpx==0.25.2` listed twice. Harmless but sloppy.
**Fix:** Remove duplicate.

### M5: `create_sample_organization()` references non-existent module
**File:** `agent_registry.py:519`
**Problem:** `from src.db.database import get_session_factory` — module doesn't exist.
**Fix:** Remove dead code or fix import.

### M6: `model_config` column name shadows Pydantic's `model_config`
**File:** `agent_registry.py:77`
**Problem:** SQLAlchemy column named `model_config` will conflict if model is ever
serialized with Pydantic (Pydantic v2 reserves `model_config`).
**Fix:** Rename to `agent_model_config` or `llm_config`.

---

## LOW

### L1: OrgChart label "Reports To:" actually shows subordinates
**File:** `OrgChart.js:159`
**Fix:** Change to "Direct Reports:".

### L2: Dialog `open` prop gets truthy object instead of boolean
**File:** `TaskQueue.js:346`
**Fix:** `open={!!selectedTask && !detailsDialogOpen}`.

### L3: Dockerfile copies unused `requirements.txt`
**File:** `Dockerfile.orchestration:14`
**Fix:** Remove `requirements.txt` from COPY.

### L4: Unused imports in TaskQueue.js
**File:** `TaskQueue.js:14,20,28,31`
**Problem:** `Tooltip`, `TextField`, `Pause`, `TrendingUp` imported but unused.
**Fix:** Remove.

### L5: Unused `PlayArrow` import in Dashboard.js
**File:** `Dashboard.js:31`
**Fix:** Remove.
