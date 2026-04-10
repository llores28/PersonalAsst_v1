# ADR: SDK Sessions for Conversation Memory + Context-Aware Guardrails

**Date:** 2026-03-20
**Status:** Accepted
**Context:** Atlas got stuck in loops and blocked email outputs because (a) the LLM had no real conversation memory and (b) the PII guardrail couldn't distinguish email workflows from PII leaks.

## Problem

1. **No real conversation memory.** Conversation history was embedded as markdown text in the system prompt via `get_session_context()`. The LLM treated this as background context, not actual prior turns. It couldn't see tool call results, couldn't properly resolve references like "send it to Crystal", and lost context between turns.

2. **PII guardrail false positives.** The output guardrail blocked email addresses using a fragile allowlist of output marker phrases. Any LLM phrasing not in the list triggered a block, even when the user explicitly asked to send an email to a specific address.

3. **MaxTurnsExceeded gave generic error.** When the agent looped, the user saw "Something went wrong" instead of actionable guidance.

## Decision

### 1. Adopt OpenAI Agents SDK RedisSession

Replace system-prompt history embedding with the SDK's built-in `RedisSession` for conversation memory. The LLM now receives actual message turns (user messages, assistant responses, tool calls, and tool results) as proper conversation history.

- **Session ID:** `agent_session:{user_telegram_id}` in the existing Redis instance
- **History limit:** `session_input_callback` keeps the last 20 items
- **Direct handler responses** (gmail check, calendar check, etc.) are manually added to the SDK session via `add_items()` so the LLM sees them on subsequent turns
- **Existing Redis conv store** (`conv:{user_id}`) is kept for state management (pending sends, pending tasks, quality scores, etc.)

### 2. Context-Aware PII Guardrail

Pass `user_message` through `Runner.run(context={"user_message": ...})` so the output guardrail can make context-aware decisions.

`_is_allowed_workspace_email_output` now has two layers:
1. **Context-aware (Layer 1):** If the user's message contains email keywords or an email address → allow email addresses in output
2. **Output-marker fallback (Layer 2):** Original marker-based allowlist (broadened) as fallback when no user context is available

### 3. MaxTurnsExceeded Handler

Dedicated `except MaxTurnsExceeded` in bot handlers with actionable user message instead of generic "Something went wrong".

### 4. Safety Cue Sync

Synced `_is_contextual_follow_up_confirmation` in `safety_agent.py` with the expanded cues from `action_policy.py` ("do it", "send the email", "send the draft", "send the draft email", "yes send it").

### 5. Gmail Tool Schema Fix

`send_connected_gmail_message` and `draft_connected_gmail_message` passed `reply_to_message_id` which does not exist in the MCP server schema (`additionalProperties: false`). Replaced with the correct fields: `thread_id` and `in_reply_to`. Added None-value stripping in both the tool functions and `call_workspace_tool()` as a defensive measure.

### 6. Stale Session Recovery

Session history filtering in `_keep_recent_session_history()` excludes `function_call` and `function_call_output` items from previous `Runner.run()` calls. These reference `call_id`s that don't exist in the current API context, causing 400 errors. Bot handlers also catch `BadRequestError` with "No tool call found", clear the session, and retry once.

## Files Changed

| File | Change |
|------|--------|
| `src/agents/orchestrator.py` | Added `_get_agent_session()`, `_add_direct_response_to_session()`, `_keep_recent_session_history()` (filters tool call items). Updated `run_orchestrator` to use `RedisSession` + `RunConfig` with context. |
| `src/agents/safety_agent.py` | Updated `_is_allowed_workspace_email_output` with 2-layer context-aware check. Updated `pii_check_guardrail` to extract user_message from ctx.context. Synced confirmation cues. |
| `src/bot/handlers.py` | Added `MaxTurnsExceeded` catch. Added `BadRequestError` catch for stale session recovery (clear + retry once). |
| `src/agents/email_agent.py` | `reply_to_message_id` → `thread_id`/`in_reply_to` + None-stripping in tool argument dicts. |
| `src/integrations/workspace_mcp.py` | `call_workspace_tool` strips `None` values before sending to MCP server. |

## Tradeoffs

- **Dual Redis stores:** We keep both the SDK session and our custom conv store. Slight duplication, but the SDK session handles LLM memory while our store handles application state (pending sends, etc.). Clean separation of concerns.
- **Session size:** Capped at 20 items via `session_input_callback` to avoid token bloat. May need tuning.
- **Graceful degradation:** If `RedisSession` fails to initialize, the orchestrator runs without session (same as before). No hard dependency.

## Tests

- 493+ passing (13 new across all Mar 20 changes), 30 pre-existing SDK failures unchanged.
- `TestContextAwareEmailAllowance` — 7 tests for the 2-layer PII check
- `TestSessionHistoryFiltering` — 3 tests for `function_call`/`function_call_output` filtering
- `test_call_workspace_tool_strips_none_values` — regression for None-stripping
- `test_gmail_send_tool_uses_correct_mcp_field_names` — regression for schema field names
- Existing `TestPendingGmailSendPayload` and `TestEmailRelatedRequest` tests still pass
