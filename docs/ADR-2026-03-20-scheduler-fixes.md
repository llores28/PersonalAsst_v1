# ADR: Scheduler Fixes — DateTrigger API + FunctionTool Callable Pattern

**Date:** 2026-03-20
**Status:** Accepted
**Context:** Reminder creation failed with two separate errors: APScheduler 4.x API mismatch and `FunctionTool object is not callable`.

## Problem

1. **DateTrigger API mismatch.** `add_one_shot_job` in `src/scheduler/engine.py` used `DateTrigger(run_date=dt)`, but APScheduler 4.0.0a6 renamed the parameter to `run_time`. Every one-shot reminder creation failed with `TypeError: unexpected keyword argument 'run_date'`.

2. **Naive datetime handling.** When the LLM generates an ISO datetime string without timezone info, `datetime.fromisoformat()` produces a naive datetime. APScheduler may reject or misinterpret these.

3. **FunctionTool not callable.** `_build_bound_scheduler_tools` closures did `await create_reminder(...)` — but `create_reminder` is decorated with `@function_tool`, making it a `FunctionTool` object. The OpenAI Agents SDK `FunctionTool` is **not directly callable** (`callable(FunctionTool(...))` returns `False`). This caused `TypeError: FunctionTool object is not callable` on every bound tool invocation.

4. **Repair agent hallucination.** When the scheduler failed, the orchestrator routed the user's "can you fix it?" to the Repair Agent, which fabricated patches for a non-existent `repair_pipeline.py`. The repair agent had no codebase access but its instructions didn't explicitly prohibit fabricating file paths.

## Decision

### 1. Fix DateTrigger parameter
Changed `DateTrigger(run_date=dt)` → `DateTrigger(run_time=dt)` in `src/scheduler/engine.py`. This is the correct APScheduler 4.x API.

### 2. Timezone hardening
If `datetime.fromisoformat()` returns a naive datetime (no `tzinfo`), attach the configured `settings.default_timezone` via `ZoneInfo`. This ensures APScheduler always receives timezone-aware datetimes.

### 3. Extract `_*_impl` plain async functions
Core scheduler logic extracted into 4 plain async functions:
- `_create_reminder_impl`
- `_create_morning_brief_impl`
- `_list_schedules_impl`
- `_cancel_schedule_impl`

Both `@function_tool` wrappers (for unbound use) and bound closures in `_build_bound_scheduler_tools` delegate to these `_impl` functions. This matches the existing pattern in `memory_agent.py` (which calls raw library functions) and `tasks_agent.py` (which calls `call_workspace_tool` directly).

**Key rule:** Never `await` a `@function_tool`-decorated function from other code. Always call the underlying plain function.

### 4. Honest repair agent instructions
Updated `src/agents/repair_agent.py` instructions to explicitly state:
- No codebase access, no filesystem access
- Must not fabricate file paths, function names, or code snippets
- Must not generate diffs for files it hasn't actually read
- Can only analyze error text the user provides

## Files Changed

| File | Change |
|------|--------|
| `src/scheduler/engine.py` | `run_date` → `run_time` + naive datetime timezone attachment |
| `src/agents/scheduler_agent.py` | Extracted `_*_impl` functions; bound tools call them |
| `src/agents/repair_agent.py` | Honest instructions about limitations |
| `tests/test_scheduler.py` | Regression tests: DateTrigger param, tz handling, bound tool pattern |

## Tradeoffs

- **Code duplication in tool signatures:** The `@function_tool` wrapper and `_impl` function have identical signatures. This is intentional — the `_impl` is the single source of truth, and the wrapper just delegates. The docstring on the `@function_tool` version is what the LLM sees.
- **APScheduler 4.x alpha:** We're using `4.0.0a6`, an alpha release with unstable API. Future versions may change parameter names again. The regression test (`test_add_one_shot_job_uses_run_time_param`) will catch this.

## Tests

- 493+ passing (4 new), 30 pre-existing SDK-absent failures unchanged.
- `TestBoundToolsCallImpl` — verifies source calls `_impl`, not `FunctionTool` objects
- `TestDateTriggerParam` — verifies `run_time=` (not `run_date=`) and naive tz handling
