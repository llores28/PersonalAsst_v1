# Changelog

## 2026-04-23 — Repair Workflow: File-Type Aware Verification

### Fixed — Verification ran wrong tool for the file type
- The repair pipeline used to default to `python -m ruff check <path>` for every patched file, including `SKILL.md`. That failed on Markdown (ruff is a Python linter) and also failed in the runtime container, where ruff is a dev-only dep (`requirements-dev.txt`) and not installed alongside `requirements.txt`.
- Symptom: applying a SKILL.md patch produced `No module named ruff` and triggered an automatic rollback even though the patch was correct.

### Added — `src/repair/verify_file.py`
- File-type aware verifier callable as `python -m src.repair.verify_file <path> [<path> ...]`.
- Dispatches by extension: `.py` → `compile()` syntax check; `SKILL.md` (under `src/user_skills/`) → YAML frontmatter parse + required-field check; `.md` / `.yaml` / `.json` / `.toml` → structural parse via stdlib + pyyaml.
- Self-contained — depends only on packages already in `requirements.txt`, so it works in the runtime container.

### Added — `suggest_verification_commands()` and `update_pending_verification_commands()`
- New helpers in `src/repair/engine.py` that pick file-type-correct verification commands and atomically replace the verification step on a stored repair plan.
- Engine allowlist now includes `python -m src.repair.verify_file`.

### Added — `RepairAgent.refine_pending_verification` tool
- New `@function_tool` on the repair agent: when a verification step fails because the runner is wrong for the file type (or missing), the agent calls this to swap the verification commands without re-proposing the patch. The owner re-triggers `apply patch` to retry.
- Closes the dead-end where the agent previously refused to continue after the user accepted the offer to "determine a better verification command".

### Improved — Missing-tool detection in `execute_pending_repair()`
- `_run_verification_commands()` now flags `failure_kind: "missing_tool"` when stderr/stdout matches `No module named …`, `command not found`, or the Windows equivalent. The rollback message tells the owner the patch wasn't to blame and points them at `fix it` (which now has a tool that actually fixes it).
- The stored last-tool-error includes `failure_kind` and `affected_files` so the repair agent's instructions can branch correctly.

### Updated — Agent prompts
- `src/agents/programmer_agent.py`: replaced the hard-coded ruff/pytest/mypy test-plan example with file-type-aware guidance defaulting to `python -m src.repair.verify_file`.
- `src/agents/repair_agent.py`: updated instructions to distinguish `failure_kind: missing_tool` (call `refine_pending_verification`) from `failure_kind: code_failure` (revise the patch).

## 2026-05-XX — OpenRouter Model Pricing & Cost-Tracking Audit

### Fixed — GAP 1 & 7: Missing OpenRouter model pricing
- Added 15 OpenRouter-prefixed model entries to `OPENAI_MODEL_PRICING` in `src/models/cost_tracker.py`:
  - Google Gemini family: `google/gemini-2.5-pro`, `google/gemini-2.5-flash`, `google/gemini-3.1-flash`, `google/gemini-2.0-flash`, `google/gemma-2-9b-it`
  - Anthropic via OpenRouter: `anthropic/claude-sonnet-4`, `anthropic/claude-3.5-sonnet`, `anthropic/claude-3-opus`, `anthropic/claude-3-haiku`
  - OpenAI via OpenRouter: `openai/gpt-4o-mini`, `openai/gpt-4o`, `openai/o3-mini`
  - Black Forest Labs: `black-forest-labs/flux` (image-only, zero token cost)

### Fixed — GAP 2: Dual cost-tracking paths
- `_track_openrouter_usage()` in `src/integrations/openrouter.py` now calls `estimate_cost_from_model()` instead of `ProviderResolver.estimate_cost()`, unifying both tracking flows onto the same pricing table.

### Fixed — GAP 3 & 8: Silent cost-tracking failures
- Raised `logger.debug()` → `logger.warning()` for OpenRouter cost-tracking exceptions in `generate_image()` and `analyze_image()`.
- `_track_openrouter_usage()` now logs a warning when the model ID is not in the pricing table.

### Fixed — GAP 5: Stale OpenRouter default model
- `src/models/provider_resolution.py`: OpenRouter `default_model` updated from `anthropic/claude-3.5-sonnet` → `anthropic/claude-sonnet-4`.

### Fixed — GAP 4 & 6: Stale model aliases and quality tiers
- `src/config/providers.yaml`: `balanced` alias updated to `anthropic/claude-sonnet-4`; `high` alias updated to `anthropic/claude-opus-4-6`.
- `src/config/openrouter_capabilities.yaml`: `best` quality tier now picks index 1 (higher-quality model) instead of index 0 (same as `balanced`); `fast` tier uses explicit index 2 instead of `-1`.

### Added — Tests
- `tests/test_cost_tracker_helper.py`: `TestOpenRouterPricing` class — 17 test cases covering all OpenRouter model IDs, Flux zero-cost, pricing tier sanity, and unknown-model fallback behavior.

## 2026-04-23 — Dashboard Enhancement Phases 1–8

### Added — Phase 1: Tool Wizard
- `ToolWizardDialog` in Dashboard Tools tab — interview → generate → review → save flow.
- `POST /api/tools/wizard/generate` endpoint using GPT-4o-mini with structured JSON output.

### Added — Phase 2a: Cost Visibility
- Raised cost-tracking log level from DEBUG to INFO/WARNING.
- Expanded `_OPENAI_MODEL_PRICING` with GPT-5.4 family, Claude Opus 4, OpenRouter models.

### Added — Phase 2b: Shared Cost Helper
- Extracted `record_llm_cost()` into `src/models/cost_tracker.py` — unified pricing table, token extraction, DB upsert, Redis tracking.
- Replaced 70-line inline cost-tracking block in `orchestrator.py` with single function call.

### Added — Phase 3: Duplicate Detection
- `setup_org_project` fuzzy-matches agents, tools, and skills (≥ 85% difflib similarity).
- Reuses existing items instead of creating near-duplicates; reports what was reused.

### Added — Phase 4: Selective Org Deletion
- `GET /api/orgs/{id}/delete-preview` — returns agents, tasks, activity count.
- Enhanced `DELETE /api/orgs/{id}` — accepts optional `retain_agent_ids` / `retain_task_ids` body.
- `_ensure_holding_org()` creates/reuses `__retained__` system org for retained entities.
- `list_orgs` filters out `__retained__` org from dashboard listing.
- Dashboard `OrgDeleteDialog` with checkboxes for selective retention.

### Added — Phase 5: Manual Ticket Creation
- `NewTicketDialog` in Repairs tab — open tickets manually with AI Agent or Admin pipeline choice.
- `POST /api/repairs` endpoint.

### Added — Phase 6: Interactions Drill-Down
- Clickable Interactions tile on Overview opens drawer with audit-log rows and filters (all/inbound/outbound/errors).
- `GET /api/activity` endpoint with direction/limit parameters.

### Added — Phase 7: Tasks vs Jobs Clarity
- Tooltips on Dashboard distinguishing Tasks, Scheduled Jobs, and Background Jobs.
- "Tasks vs Scheduled Jobs vs Background Jobs" section in `README_ORCHESTRATION.md`.

### Added — Phase 8: Draggable/Resizable Grid
- `react-grid-layout ^1.4.4` added to orchestration-ui.
- `GET/PUT /api/dashboard/layout` — Redis-backed layout persistence per user (1-year TTL).
- `OverviewTab` rewritten with `ResponsiveGridLayout` — 6 draggable/resizable tiles (costs, quality, tools, schedules, budget, persona).
- Drag via tile headers (`.grid-drag-handle`), debounced save (1.2s), "Reset Layout" button.
- 3 responsive breakpoints: lg (12-col), md (10-col), sm (6-col).

---

## 2026-04-22 — Repair Pipeline Hardening (M1–M7)

### Added — Proactive Error Notifications
- `src/bot/notifications.py` — four Telegram push helpers: `notify_owner_of_error` (error alert with "say fix it" CTA), `notify_ticket_created`, `notify_fix_ready` (inline ✅/❌ keyboard), `notify_low_risk_applied`.
- `_notify_owner_error()` fires in `orchestrator.py` after every captured tool error via `_fire_and_forget` — owner sees error in Telegram immediately, no manual polling.
- `propose_low_risk_fix` in `repair_agent.py` now sends Telegram push after auto-apply (was silent before).

### Added — Email Notifications
- `src/repair/notifications.py` — `send_ticket_created_email` and `send_fix_ready_email` via connected Gmail → `lannys.lores@gmail.com`.
- Both include ticket #, title, affected files, confidence %, and deploy instructions.

### Added — `/tickets` and `/ticket` Commands
- `/tickets` — lists all open (non-deployed, non-closed) repair tickets with status icons and created timestamps.
- `/ticket approve <id>` — calls `approve_ticket_deploy` to merge verified branch to main; owner-only.
- `/ticket close <id>` — marks ticket closed without deploying; owner-only.
- Both registered in Telegram BotCommand menu via `src/main.py`.

### Added — Inline Keyboard "Apply fix now?"
- After sandbox verification passes, `execute_pending_repair` fires `notify_fix_ready` with an inline keyboard: **✅ Apply fix now** (`repair_approve:<id>`) and **❌ Skip for now** (`repair_skip:<id>`).
- `cb_repair_approve` callback in `handlers.py` calls `approve_ticket_deploy` directly from the button tap.
- `cb_repair_skip` edits the message and gives the `/ticket approve` fallback command.

### Fixed — Pipeline Robustness (M6)
- `_run_sandbox_test` in `engine.py`: replaced fragile `"✅ Patch Verified" in result` string check with tuple-of-markers test (`"Patch Verified in Sandbox"`, `"Awaiting Deploy Approval"`, `"ready_for_deploy"`).
- `run_self_healing_pipeline`: added `_PIPELINE_MAX_ATTEMPTS = 3` guard — same error fingerprint blocked after 3 failed attempts to prevent runaway loops.
- `propose_low_risk_fix` now pushes real Telegram notification; removed misleading comment saying "you should confirm via Telegram".

### Fixed — Test Warnings (M1)
- `tests/test_repair_engine.py`: 4 `RuntimeWarning: coroutine never awaited` fixed by setting `mock_session.add = MagicMock()` (SQLAlchemy `.add()` is synchronous; `AsyncMock` was making it return an unawaited coroutine).
- `tests/test_repair_flow.py`: stale assertion updated — `execute_pending_repair` no longer auto-merges; now returns "Patch Verified" awaiting deploy approval.

### Tests
- `tests/test_repair_notifications.py` — 13 tests covering all 4 Telegram helpers + 2 email helpers + pipeline retry guard.
- `tests/test_repair_tickets_command.py` — 15 tests covering `/tickets`, `/ticket approve|close`, `cb_repair_approve`, `cb_repair_skip`.
- **Total: 841 passing** (added 26 new, zero regressions).

---

## 2026-04-22 — Multimodal Capabilities + TTS Voice Replies

### Added — Image Generation
- Direct image generation fast path in orchestrator (`_maybe_handle_direct_image_generation`) — bypasses LLM routing for explicit requests, returns images as Telegram photos.
- `src/integrations/openrouter.py` — `generate_image()` with modalities, aspect ratio inference from prompt cues (landscape/portrait/square), retry logic.
- `src/config/openrouter_capabilities.yaml` — model preferences and timeout config.
- `docker-compose.yml` — `OPENROUTER_API_KEY`, `OPENROUTER_IMAGE_ENABLED`, `OPENROUTER_DAILY_COST_CAP_USD` passed into `assistant` container.

### Added — Photo Analysis
- Direct photo analysis fast path (`_maybe_handle_direct_image_analysis`) — reads `latest_uploaded_image` from Redis session and calls OpenRouter `analyze_image()` without LLM routing.
- Telegram photo handler downloads photo, encodes base64, stores in session, then routes caption/default prompt through orchestrator.
- `src/integrations/openrouter.py` — `analyze_image()` with multimodal message format.

### Added — TTS Voice Replies
- `src/bot/voice.py` — `synthesize_speech()` using OpenAI `tts-1`. Auto-resolves user's saved voice preference via `get_user_tts_voice()`.
- `src/bot/handler_utils.py` — `_maybe_send_tts_reply()` checks `wants_audio_reply` session flag, strips Markdown, synthesizes and sends voice message.
- Voice messages auto-set `wants_audio_reply` flag (voice-in → voice-out).
- Text cues (`"reply with audio"`, `"say it"`, `"voice reply"`, etc.) set the flag for one turn.

### Added — `/voice` Command
- `/voice` — shows current TTS voice, lists all 6 options.
- `/voice <name>` — persists preference to `user_settings.tts_voice` (DB column + migration `009_add_tts_voice`).
- Registered in Telegram command menu via `src/main.py`.

### Fixed — Cost Tracking int32 Overflow
- `src/models/cost_tracker.py` — added `_resolve_db_user_id()` to resolve Telegram ID → internal `users.id` (PK) before writing to `daily_costs`. Telegram IDs > 2.1B were crashing the update query.

### Improved — Image UX
- Typing indicator shown immediately when message is received.
- `upload_photo` action shown while sending generated image.
- Captions cleaned: first sentence of revised prompt, max 200 chars, falls back to first 12 words of original prompt.
- Specific error messages for cost cap exceeded and model unavailable.

## 2026-04-13 — src/ Consolidation + Agents Tab

### Added — Agents Tab (Dashboard UI)
- New **Agents** tab between Organizations and Activity in the Dashboard.
- `AgentsTab` component: toggle between "My Agents" (org agents) and "System Agents".
- `AgentsOrgSection`: table view of all `OrgAgent` records with org name, status, edit and delete (delete blocked with tooltip if org is active).
- `AgentsSystemSection`: card grid of all system/internal agents, filterable by category (Google Workspace, Internal, Utility).
- `GET /api/agents/system` endpoint returns `SystemAgentInfo` list from `src/orchestration/system_agents.py`.
- `GET /api/agents/org` endpoint returns all `OrgAgent` rows joined with their organization's name/status and a `can_delete` / `delete_reason` field.
- `DELETE /api/orgs/{org_id}/agents/{agent_id}` now enforces active-org guard (returns 409 if org status is `active`).
- `src/orchestration/system_agents.py` — registry of all built-in system agents with `SystemAgentInfo` Pydantic model.
- `tests/test_agents_api.py` — tests for system agents registry and OrgAgent deletion checks.

### Changed — src/ Directory Consolidation
Moved all deployment-relevant directories under `src/` so that only bootstrap and project-root files live at the repo root:

| Was (root) | Now |
|---|---|
| `orchestration-ui/` | `src/orchestration-ui/` |
| `user_skills/` | `src/user_skills/` |
| `config/` | `src/config/` |
| `alembic/` | `src/alembic/` |
| `alembic.ini` | `src/alembic.ini` |

**Path updates (14 sites):**
- `src/agents/skill_factory_agent.py`: `USER_SKILLS_DIR = Path("src/user_skills")`
- `src/skills/loader.py`: default `user_skills_dir` → `/app/src/user_skills`
- `src/skills/validation.py`: all `Path(f"user_skills/{skill_id}")` → `src/user_skills/`
- `src/agents/orchestrator.py`: `config/persona_default.yaml` → `src/config/persona_default.yaml`
- `src/orchestration/api.py`: all 8 `user_skills` path references updated
- `src/bot/handlers.py`: all 3 `user_skills` path references updated
- `src/main.py`: `alembic.ini` → `src/alembic.ini`

**Infrastructure updates:**
- `Dockerfile`: removed now-redundant `COPY config/ alembic/ alembic.ini` (all inside `COPY src/`)
- `Dockerfile.orchestration`: same cleanup
- `docker-compose.yml`: `orchestration-ui` build context → `./src/orchestration-ui`; volume mounts → `./src/user_skills:/app/src/user_skills`
- `.gitignore`: `orchestration-ui/node_modules/` → `src/orchestration-ui/node_modules/`
- `.dockerignore`: replaced broad `orchestration-ui/` exclusion with targeted `src/orchestration-ui/node_modules/` and `src/orchestration-ui/build/`

### Operational Notes
- Rebuild sequence:
  - `docker compose down --remove-orphans`
  - `docker compose build --no-cache orchestration-api orchestration-ui`
  - `docker compose up -d`
- `.env` and `.env.example` stay at the project root (consumed by `docker-compose.yml` from the same directory).

---

## 2026-04-12 — Agentic Upgrade (M1–M4) + Repo Cleanup

### Added — M3: Explainable Observability
- `AgentTrace` SQLAlchemy model + Alembic migration 006 (`agent_traces` table)
- Trace persistence in `orchestrator.py`: every tool call step recorded after `Runner.run()`
- `GET /api/traces` and `GET /api/traces/sessions` API endpoints
- Dashboard **Activity** tab: Timeline icon on each row → side drawer with step-by-step agent thought trace (tool name, args, result preview, duration)

### Added — M4: Tightened Self-Healing Loop
- `classify_repair_risk(plan)` in `src/repair/engine.py` → `low | medium | high` based on action types and file extensions
- `propose_low_risk_fix` tool on `RepairAgent`: auto-applies operational fixes (Redis key clears, schedule re-injections) immediately without owner approval gate
- `src/repair/verifier.py`: `run_quick_smoke()`, `verify_repair()`, `rollback_repair()` for post-apply verification
- `risk_level` and `auto_applied` columns on `RepairTicket` (migration 006)
- Dashboard **Repairs** tab: risk-level chips + green "Auto-applied" / yellow "Pending approval" badges
- `AUTO_REPAIR_LOW_RISK` env var (default `true`) to disable auto-apply if needed

### Added — M1: Parallel Multi-Agent Fan-Out
- `src/agents/parallel_runner.py`: `run_parallel_tasks()` with `asyncio.gather()`, max 3 branches, budget guard (falls back to sequential if daily spend ≥ 80%)
- `detect_parallel_domains()` in `routing_hardened.py`: conjunction + multi-domain keyword detection
- `PARALLEL` intent added to `TaskIntent` enum
- Orchestrator pre-flight: multi-domain messages fan-out before single-agent path

### Added — M2: Autonomous Background Jobs
- `src/agents/background_job.py`: `create_background_job()`, APScheduler tick loop, fault counter, Telegram notifications on complete/fail
- `BackgroundJob` SQLAlchemy model + migration 006
- Orchestrator: detects "monitor / watch / keep an eye / alert me when" phrases → creates background job, returns confirmation with interval + iteration cap
- Dashboard **Jobs** tab: progress bar, tick counter, cancel (Stop) button, status chips; tab badge shows active job count

### Changed — Repo Cleanup
- Deleted 4 orphan backup folders: `bootstrap-backup-/`, `bootstrap-backup-20260401-111517/`, `bootstrap_backup/`, `bootstrap_backup_/`
- Expanded `.gitignore`: `.windsurf/`, `bootstrap/`, `orchestration-ui/node_modules`, `orchestration-ui/build/`, `.tmp/`, cache dirs, `gmail-filters.xml`
- Created root `.dockerignore` for lean Docker build context (excludes docs, bootstrap, tests, node_modules, .windsurf, .git)
- Fixed `Dockerfile` (bot): added missing `COPY alembic/ ./alembic/` so container can run migrations
- Removed narrow `./src/orchestration` bind-mount from `docker-compose.yml` `orchestration-api` service
- Untracked `.windsurf/`, `bootstrap/`, `gmail-filters.xml` from git index (`git rm --cached`)

### Operational Notes
- Rebuild sequence:
  - `docker compose down --remove-orphans`
  - `docker compose build`
  - `docker compose up -d`
  - `docker compose exec assistant alembic upgrade head`

---

## 2026-04-11 — Reliability & Security Hardening (Audit Phase 1/2)

### Added
- Telegram organization command coverage with `/orgs` lifecycle support:
  - `/orgs create`, `/orgs info <id>`, `/orgs pause <id>`, `/orgs resume <id>`, `/orgs delete <id>`
- Durable delete audit trail for organization deletes in:
  - Dashboard API delete path
  - Telegram `/orgs delete` path
- New focused regression tests:
  - `tests/test_main.py`
  - `tests/test_orchestration_api_org_auth.py`
  - `tests/test_orchestration_api_cors.py`
  - `tests/test_bot_handlers_resilience.py`
  - `tests/test_bot_orgs_handlers.py`

### Changed
- Scheduler one-shot DB sync now normalizes `trigger_config.once.run_at` to canonical ISO string before scheduling.
- Startup migrations are now controlled by `STARTUP_MIGRATIONS_ENABLED` (default disabled).
- Dashboard API organization endpoints now enforce ownership resolution and org-scoped access checks.
- Dashboard API CORS moved from wildcard to env-driven allowlist via `CORS_ALLOWED_ORIGINS`.
- Bot routing resilience improved: critical fallback paths now emit structured warning logs instead of swallowing exceptions silently.

### Security
- Wildcard dashboard CORS origin (`*`) is rejected in parser logic.
- Org delete operations now preserve durable audit evidence in `audit_log` even when org activity rows cascade delete.

### Operational Notes
- Recommended rebuild sequence:
  - `docker compose down --remove-orphans`
  - `docker compose build`
  - `docker compose up -d`
- Recommended migration operation remains explicit:
  - `docker compose exec assistant alembic upgrade head`

### Verification Snapshot
- Targeted remediation suite passes:
  - `21 passed, 2 skipped`
- Full repository suite currently includes unrelated pre-existing failures outside this remediation scope (e.g., legacy org-agent `FunctionTool` callable expectations and complexity classifier expectation drift).
