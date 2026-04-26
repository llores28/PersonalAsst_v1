# Changelog

## 2026-04-26 — Cohesion audit fixes (settings symmetry + endpoint disambiguation)

A deep audit surfaced three gaps and three observability holes in the post-cleanup state. Net: zero blockers, but several places where the system *looked* configured but wasn't fully wired. Closed all six in this commit.

### Fixed — Endpoint disambiguation (was: "duplicate scheduler health route")
The repo had two scheduler-health endpoints in [src/orchestration/api.py](../src/orchestration/api.py) that the audit flagged as duplicates. They actually answer *different* questions; the fix was clarification + integration, not deletion:

- `GET /api/health/scheduler` (public, line 636) — Redis-backed *per-job observability* (last_status, consecutive_failures, total_runs/failures from the `JobReleased` listener). Best for monitoring tools.
- `GET /api/scheduler/health` (auth-gated, line 5462) — Postgres + APScheduler-runtime *liveness check* (is the container alive, how many jobs are registered, what's coming up). Best for the dashboard.

The dashboard's existing call to `/api/scheduler/health` now also embeds the observability snapshot under a new `per_job_health` field, so the UI gets both views in one round-trip. The `scheduler_health` function name on line 5462 was also renamed to `scheduler_runtime_health` to avoid the Python-level shadowing the audit caught.

### Fixed — Settings/env symmetry for `WORKSPACE_MCP_SIGNING_KEY`
- Added `workspace_mcp_signing_key: str = Field(default="")` to [src/settings.py](../src/settings.py). Previously the var was in `.env.example` and `docker-compose.yml` but had no Pydantic field, so the application couldn't read or validate it.
- Added a startup hook `_check_workspace_mcp_persistence()` in [src/main.py](../src/main.py) that emits a WARNING if `google_oauth_client_id` is set but `workspace_mcp_signing_key` is empty. Without this, the heartbeat false-positives we shipped to prevent earlier today would still fire if someone forgot to run `scripts/ensure_workspace_mcp_key.py`. Doesn't raise — dev environments without Google connected still boot cleanly.

### Updated — README_ORCHESTRATION
[README_ORCHESTRATION.md](../README_ORCHESTRATION.md) now documents both scheduler-health endpoints with a comparison table. External tooling and dashboard wiring is now unambiguous.

### Verification
- `pytest -q`: 1096 passed / 0 failed / 6 skipped / 7 xfailed (unchanged from before this commit).
- The audit produced zero BLOCKERs. The remaining open item from the audit (GAP-3: dashboard UI doesn't render `degraded` status visually) is genuine UI work and tracked separately.

## 2026-04-26 — Removed deprecated `bootstrap/` from the repo

The forked CLI copy at `bootstrap/cli/` had been marked DEPRECATED for some time but was still tracked in git, still partially edited, and still referenced from 19 `.windsurf/` skills and workflows. The migration to the editable `Nexus/` install (the `nexus` command) was effectively done; the residual `bootstrap/` tree was just confusing.

### Removed (from git tracking; local files preserved)
- `git rm -r --cached bootstrap/` — 70+ files untracked. Local copy left in place; the [updated .gitignore](../.gitignore) prevents re-tracking.
- `git rm --cached .cache/bs-cli/audit.jsonl` — stale tracking from before `.cache/` was gitignored.

### Dependency audit (no Atlas runtime breakage)
Auditor confirmed no `from bootstrap...` imports in `src/`, `tests/`, `scripts/`, no subprocess calls to `python bootstrap/cli/...`, no Docker mounts, no CI workflows referencing it. The single import at `Nexus/nexus/cli/security.py:26` (`from bootstrap.cli.utils import find_project_root`) is dead code — every actual `validate_path()` call site passes `project_root` explicitly. Filed as a separate Nexus-side cleanup; the import only fires if someone calls `validate_path("foo")` without a second argument, and nobody does.

### Updated stale references
- 19 `.windsurf/` files: 71 occurrences of `python bootstrap/cli/bs_cli.py <cmd>` → `nexus <cmd>`. One path-only reference (`bootstrap/cli/bs_cli.py` without the `python` prefix) repointed to `Nexus/nexus/cli/bs_cli.py`.
- [AGENTS.md](../AGENTS.md): directory map and command examples switched to `Nexus/` and `nexus`.
- [CLAUDE.md](../CLAUDE.md) and [.github/copilot-instructions.md](../.github/copilot-instructions.md): dropped the "DEPRECATED forked copy" callout (the directory no longer exists in git).

### Verification
- Atlas test suite still green (no imports broken).
- `nexus --help` works (the dead bootstrap import is in a code path that's never exercised by current callers).

### Migration note for contributors
- After pulling this commit, your local `bootstrap/` directory will still exist on disk (gitignored). Delete it manually with `rm -rf bootstrap` if you want to clean up — the project no longer uses it.
- If you need the tools that used to live there, run them via `nexus <subcommand>` instead.

## 2026-04-26 — ADR backfill (6 new architecture decision records)

Captured the *why* behind today's substantial design decisions before the context faded. Each ADR follows the project format (Context / Decision / Consequences / Alternatives Considered) and links to the relevant source files.

- [ADR-2026-04-26-oauth-heartbeat-and-reauth-nudge](ADR-2026-04-26-oauth-heartbeat-and-reauth-nudge.md) — why weekly Mon 09:00 UTC, why 3-bucket classification, why bracketed-tag detection over HTTP codes, why a 6-day dedup TTL (not 7), why fail-open on Redis.
- [ADR-2026-04-26-workspace-mcp-token-persistence](ADR-2026-04-26-workspace-mcp-token-persistence.md) — the three upstream defaults (`memory` proxy backend, home-dir credentials path, `GOOGLE_OAUTH_CLIENT_SECRET`-derived Fernet key) that conspired to drop tokens on rebuild, and why we pinned all three explicitly.
- [ADR-2026-04-26-memory-eviction-with-summary-distillation](ADR-2026-04-26-memory-eviction-with-summary-distillation.md) — the 0.45/0.25/0.30 generative-agents scoring formula, the 30-day half-life, the two-phase write-summaries-before-delete pipeline, and why summaries are protected from re-eviction.
- [ADR-2026-04-26-workspace-rate-limit-handling](ADR-2026-04-26-workspace-rate-limit-handling.md) — the two-layer wrapper that lets tenacity see only retryable exceptions while keeping the public `call_workspace_tool` string-only, plus the conservative pattern set for rate-limit detection.
- [ADR-2026-04-26-session-compaction-dlq](ADR-2026-04-26-session-compaction-dlq.md) — why we never silently drop conversation context, the Redis-list DLQ design (`compaction_dlq:{user_id}`, 7-day TTL), and the `last_error` scope-leak workaround.
- [ADR-2026-04-26-scheduler-observability](ADR-2026-04-26-scheduler-observability.md) — single `JobReleased` listener over per-job instrumentation, pure-function `_apply_event` for testability, why Redis (not Postgres) for `scheduler_health:*` records, and why the listener must never raise.

These bring the docs/ ADR count from 17 to 23.

## 2026-04-26 — Workspace-MCP token persistence (close OAuth heartbeat false-positive footgun)

### Why this exists
The OAuth heartbeat shipped earlier today nudges every `auth_failed` user to run `/connect google`. That's the right behavior — *unless* the auth failure was caused by us, not Google. Three default settings in the upstream `taylorwilsdon/google_workspace_mcp` image conspired to silently drop every persisted token on container rebuild:

1. **Linux default for `WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND` is `memory`** — proxy state evaporates on restart, not just rebuild.
2. **`WORKSPACE_MCP_CREDENTIALS_DIR` defaults to `~/.google_workspace_mcp/credentials`** — this lives on the container's writable layer, not in our `workspace_tokens` named volume. The volume mount at `/data/tokens` was effectively unused.
3. **The Fernet token-encryption key is derived from `GOOGLE_OAUTH_CLIENT_SECRET` if `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY` is unset** — silent token loss on any OAuth client-secret rotation.

Without these pinned, `docker compose up --build` would (a) wipe every user's tokens and (b) trigger Mon 09:00 UTC nudges to all of them — looking exactly like a mass Google revocation event.

### Changed — [docker-compose.yml](../docker-compose.yml)
- Mount the `workspace_tokens` named volume at `/data` (was `/data/tokens`) so it covers both the credentials dir and the OAuth-proxy state dir.
- Pinned env vars on the workspace-mcp service:
  - `WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND=disk`
  - `WORKSPACE_MCP_OAUTH_PROXY_DISK_DIRECTORY=/data/oauth-proxy`
  - `WORKSPACE_MCP_CREDENTIALS_DIR=/data/credentials`
  - `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY=${WORKSPACE_MCP_SIGNING_KEY:-}`
- Inline comment block on the service explains the why (so the next person doesn't "clean up" what looks like a redundant config).

### Added — [scripts/ensure_workspace_mcp_key.py](../scripts/ensure_workspace_mcp_key.py)
Idempotent bootstrap helper. Reads `.env`, looks for `WORKSPACE_MCP_SIGNING_KEY`. If missing or empty, generates a 64-char hex key via `secrets.token_hex(32)` and writes it (preserving every other line). If present and non-empty, prints a one-line summary and exits cleanly. Tested against missing/present/empty cases — all idempotent.

### Added — [.env.example](../.env.example)
`WORKSPACE_MCP_SIGNING_KEY` entry with a generation comment + warning never to rotate without re-issuing `/connect google` (rotation invalidates persisted tokens).

### Added — [docs/RUNBOOK.md](RUNBOOK.md) "Workspace-MCP token persistence" section
- Table of the four pinned settings + rationale.
- First-time setup steps (`cp .env.example .env` → `python scripts/ensure_workspace_mcp_key.py` → `docker compose up -d`).
- Recovery procedures for: lost `.env`, deleted volume (`docker compose down -v`), and a verification command that exercises the round-trip via `weekly_oauth_heartbeat()`.

### Verification
- `docker compose config --quiet` exits 0 (compose syntax + interpolation valid).
- Bootstrap script tested with three cases (missing var, present-and-empty var, present-and-set var). Idempotent in all three.

### Migration note
Existing deployments need a one-time `python scripts/ensure_workspace_mcp_key.py` and a `docker compose up -d --build`. After that, `/connect google` once per connected user (their old tokens were on the container's writable layer and are gone). The Mon 09:00 UTC heartbeat will catch users that haven't reconnected and nudge them automatically.

## 2026-04-26 — Test suite green (47 → 0 failures)

After the org-agent batch closed 35, this round closed the remaining 12 — most were content-drift assertions, two were real test issues, and one revealed a subtle global-state pollution.

### Fixed — Real test isolation issue (1 test)
- `test_pipeline_returns_needs_revision_when_qa_requests_it`: every `TestSelfHealingPipeline` test calls `run_self_healing_pipeline(user_telegram_id=12345, error_description="Test error")` — same fingerprint. The pipeline's in-memory `_PIPELINE_ATTEMPT_COUNTS` accumulates across tests in the same session, so by the third test the counter hit `_PIPELINE_MAX_ATTEMPTS` and the assertion got `MAX_RETRIES_EXCEEDED` instead of `NEEDS_REVISION`. Added an autouse fixture that clears the counter before *and* after each test in the file, making test ordering irrelevant.

### Fixed — Stale assertions / drift (8 tests)
- `test_audit_fixes.py::test_cost_pricing_uses_model_keyed_lookup`: pricing extracted out of `orchestrator.py` into `src/models/cost_tracker.py` and renamed `_MODEL_PRICING` → `OPENAI_MODEL_PRICING`. Test now follows the canonical location and also asserts the old block doesn't return to orchestrator.py.
- `test_main.py::test_run_migrations_upgrades_head_when_enabled`: alembic config moved `alembic.ini` → `src/alembic.ini`.
- `test_model_router.py::test_coding_xhigh_returns_codex`: `gpt-5.3-codex` retired from the live routing matrix; `CODING+XHIGH` now routes to `gpt-5.4` (a reasoning model). Renamed and updated.
- `test_model_router.py::test_non_reasoning_model_has_none_effort`: same retirement removed the only non-reasoning routing-matrix output. Rewrote to mock `settings.model_general` to `gpt-4o` (intentionally absent from `_REASONING_MODELS`) so the invariant ("non-reasoning model → effort=None") still has coverage.
- `test_skill_registry.py::test_frozen_prevents_mutation`: `SkillDefinition` is intentionally a regular `@dataclass` (not frozen) — the dashboard skill-edit endpoint at [src/orchestration/api.py:4573](../src/orchestration/api.py#L4573) mutates `name`/`description`/`tags` in place. Renamed the test to `test_skill_definition_is_mutable` and pinned the contract.
- `test_skill_registry.py::test_list_skills_returns_metadata`: `metadata_dict()` no longer exposes `tool_count` (Level-1 metadata is intentionally tool-agnostic). Updated the test to spot-check the real Level-1 keys (`name`, `description`, `version`, `is_active`).
- `test_tools.py::test_create_tool_factory_agent`: factory grew from 3 to 5 tools (`generate_http_tool` + `get_org_catalog` for live model/pricing lookups). Replaced bare count check with explicit name set.
- `test_tools.py::test_instructions_contain_decision_tree`: instructions restructured. Test now asserts section headers + the four canonical tool categories (CLI / HTTP API / function_tool / specialist) instead of pinning specific copy.

### Fixed — Mock setup (3 tests)
- `test_scheduler.py::test_sync_one_shot_normalizes_run_at_to_iso_string`: the `SimpleNamespace` task mock was missing `is_active`, `job_function`, `job_args` — `sync_tasks_from_db` reads all three. Without them, the inner `except` swallowed an `AttributeError` and the row never reached `added.append(...)`.
- `test_integrated_pipeline.py::test_dry_run_reports_failed_patch`: `_dry_run_patch` migrated from `git apply --check` (subprocess) to a pure-Python read-only hunk-context simulation. The old test mocked `_run_command_parts`, which is no longer called. Rewrote to feed a real diff against `requirements.txt` with deliberately bogus context — the parser detects the mismatch and returns "Patch does not apply cleanly to requirements.txt: ...".
- `test_integrated_pipeline.py::test_sandbox_test_success`: `_run_sandbox_test` matches success against the EXACT marker substring "Patch Verified in Sandbox" (not just "Patch Verified"). The mock return string was too short — extended to `"✅ Patch Verified in Sandbox - Awaiting Deploy Approval"`.

### Verification
- **Full suite: 1096 passed / 0 failed / 6 skipped / 7 xfailed.** Down from 47 failed at the start of today's session. The 6 skipped are the fastapi-conditional dashboard-API tests (intentional — `requirements-orchestration.txt` not in the bot venv); the 7 xfailed are the calibration-gap parametrizations from `TestMessageComplexityClassifier` (also intentional).

## 2026-04-26 — Org-agent test suite cleanup (35 fewer failures)

### Fixed — `test_org_agent.py` FunctionTool isolation pollution (27 tests)
- Root cause: the test file installs a passthrough `function_tool` mock only `if "agents" not in sys.modules`. In the full suite, an earlier test imports the real openai-agents SDK first, the conditional skips, and the real `function_tool` decorator produces `FunctionTool` objects that are NOT directly callable. Result: every CRUD test raised `TypeError: 'FunctionTool' object is not callable` — but only when run as part of the suite, not in isolation.
- Fix: scope the passthrough to the call site. `_get_tools()` now patches `src.agents.org_agent.function_tool` with a no-op decorator just for the `_build_bound_org_tools()` build, then restores it. The keep-the-mock-around-just-in-case `if "agents" not in sys.modules` guard remains for the import-time stub but no longer determines runtime decorator behavior.
- Now all 33 org-agent tests pass regardless of test ordering.

### Fixed — `test_skill_creation` tool count drift (1 test)
- The org skill grew `setup_org_project` to 15 tools but the test still asserted 14. Bumped and replaced the bare count check with an explicit name set so future drift fails with a useful diff instead of a number mismatch.

### Fixed — `test_agents_api.py` fastapi-optional skip (4 tests)
- 4 model-validation tests imported `src.orchestration.api`, which has a top-level `from fastapi import ...`. fastapi only ships in the orchestration container (`requirements-orchestration.txt`), not the bot venv. Tests now skip cleanly via `pytest.mark.skipif(importlib.util.find_spec("fastapi") is None)` instead of failing — install `requirements-orchestration.txt` to run them.

### Fixed — `test_bot_orgs_handlers.py` (2 tests)
- `test_cmd_orgs_create_starts_wizard` was testing a removed multi-step session-field wizard. The `/orgs create` flow now redirects to the AI-powered `/neworg <goal>` single-shot. Test rewritten to assert the redirect message is sent and `set_session_field` is NOT called (renamed to `test_cmd_orgs_create_redirects_to_neworg`).
- `test_handle_message_org_wizard_description_creates_org_and_clears_state` failed on `message.photo` access because the mock SimpleNamespace was missing that attribute. Added `photo=None` and `caption=None` to the mock to match `handle_message`'s feature-detection branches.

### Verification
- Full suite: **1084 passed / 12 failed / 6 skipped / 7 xfailed** (was 1053 / 47 / 2 / 7 at the start of this session). **35 fewer failures, 31 more passing tests, zero regressions.** Remaining 12 failures are unrelated content/contract drift (skill-frozen contract, tool-factory instructions string, model-router edge cases) — not part of this batch.

## 2026-04-26 — OAuth heartbeat: Telegram re-consent nudge

### Added — `notify_oauth_reauth_required` in [src/bot/notifications.py](../src/bot/notifications.py)
- New helper sends a Telegram message asking the user to run `/connect google` when their token has been revoked. Includes the connected email when available.
- Redis-backed dedup: `notification_sent:{user_id}:oauth_reauth` with a 6-day TTL — short enough that a stuck user gets re-nudged on the next Monday heartbeat, long enough that ad-hoc heartbeat re-runs in the same week don't spam.
- Fail-open semantics: if Redis is unreachable, the nudge still goes out (a duplicate is preferable to silent auth failure on a critical-path integration). The dedup key is only set after a *successful* Telegram send so transient bot failures don't suppress next week's retry.

### Wired — `weekly_oauth_heartbeat` now calls the nudge for `auth_failed` users
- Heartbeat report grew `users_nudged: int` and per-detail `nudge_sent: bool`.
- Nudge dispatch is isolated in `_send_reauth_nudge` (best-effort email lookup from `google_email:{user_id}` Redis key, then `notify_oauth_reauth_required`); never raises into the batch loop.
- Closes the OAuth heartbeat detection→notification loop: revoked tokens are now visible to the user, not just to logs.

### Tests — 8 new (6 helper + 2 heartbeat-integration)
- `tests/test_repair_notifications.py::TestNotifyOauthReauthRequired` — sends-when-no-key, dedup-suppresses, redis-unavailable-still-sends, failed-send-doesn't-set-key, swallows-bot-init-failure, no-email-fallback.
- `tests/test_scheduler_oauth_heartbeat.py` — added `test_auth_failure_calls_telegram_nudge_with_correct_id` and `test_auth_failure_with_failed_nudge_still_reports`. Existing tests updated to mock `_send_reauth_nudge` and assert the new `users_nudged` counter / `nudge_sent` field.

### Verification
- 21/21 scoped tests pass on first run (`tests/test_repair_notifications.py::TestNotifyOauthReauthRequired` + `tests/test_scheduler_oauth_heartbeat.py`).
- Full suite: 1053 passed / 47 failed / 7 xfailed — failure count unchanged (zero regressions). The 47 failing tests are the pre-existing org-agent / tool-count / skill-frozen / model-router backlog, none touched by this change.

## 2026-04-25 — Nexus toolkit upgrade (upstream `65b60ff`)

### Upgraded — Nexus CLI toolkit synced to latest upstream
- Pulled [llores28/Nexus](https://github.com/llores28/Nexus) `main` into the embedded clone at `bootstrap/Nexus/` (was `70b6e6e`, now `65b60ff` — 16 commits forward).
- Local `bootstrap/cli/` already carried the post-Mar-24 cherry-picks (`health.py`, `supply_chain.py`); the existing tools required no logic refresh — upstream's only changes to them were the `bootstrap.cli.*` → `nexus.cli.*` import-path rename done as part of upstream commit `7ca6023`. The local layout intentionally **stays at `bootstrap/cli/`** to avoid rippling the rename through CLAUDE.md / AGENTS.md / .windsurf rules / .github / docs / 50+ doc references.

### Added — `journal` subcommand (cross-session state tracking)
- `bootstrap/cli/tools/journal.py` and `bootstrap/cli/tools/journal_dashboard.py` — ported from upstream `nexus/cli/tools/`, with imports and git-hook templates rewritten for the local `bootstrap/cli/bs_cli.py` path.
- Subcommands: `session-start`, `session-end`, `log`, `status`, `diff`, `export`, `setup-hooks`.
- State stored at `.nexus/state.json` and rendered to `.nexus/state.md`. Dashboard exports to `.nexus/state-dashboard.html` (self-contained, no CDN).
- Optional git hooks (`post-commit` auto-logs, `pre-push` regenerates dashboard) via `journal setup-hooks`. Not auto-installed.

### Bumped — CLI version 0.1.0 → 0.2.0
- `python bootstrap/cli/bs_cli.py --version` now reports `bs-cli, version 0.2.0`.

### Skipped from upstream (intentional)
- The new `init`/`wizard` tools (upstream `390eb6a`/`65b60ff`) — they target *fresh* project bootstrap; PersonalAsst is already bootstrapped.
- The `bootstrap/` → `nexus/` directory rename (upstream `7ca6023`) — staying on `bootstrap/cli/` to keep doc references valid. Documented in [bootstrap/README.md](../bootstrap/README.md).

### Verification
- `bs_cli.py --version` → `0.2.0`. ✓
- `bs_cli.py --help` → lists `journal` alongside the existing 9 commands. ✓
- `bs_cli.py journal status --format json` → emits structured result. ✓
- `bs_cli.py journal diff --format json` → detects changed files via git. ✓
- `bs_cli.py prereqs --format json` → no regression. ✓
- `bs_cli.py health check --format json` → no regression (pre-existing 72/100 score unchanged). ✓

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
