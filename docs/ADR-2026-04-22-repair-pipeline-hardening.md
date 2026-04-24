# ADR-2026-04-22 — Repair Pipeline Hardening

**Status:** Accepted  
**Date:** April 22, 2026  
**Deciders:** Owner  

## Context

The Phase 9 M4 self-healing loop existed but had three critical gaps:

1. **Silent failures** — Errors were stored in Redis but the owner was never proactively notified. The user had to discover errors by seeing a broken response, then manually ask "what went wrong".
2. **No end-to-end deploy UX** — The sandbox produced a verified patch but the owner had no fast path to approve it. They needed to type specific text commands and navigate a security challenge without any prompts.
3. **No ticket visibility** — There was no way to see all open repair tickets from Telegram. Tickets existed only in PostgreSQL and the Dashboard.
4. **Fragile internals** — `_run_sandbox_test` used `"✅ Patch Verified" in result` (emoji-sensitive string match), `propose_low_risk_fix` claimed to notify but only returned text, and `run_self_healing_pipeline` had no retry cap.

## Decision

### Proactive Error Push (M2)
Add `_notify_owner_error()` as a fire-and-forget background task in the orchestrator. Fires after every `store_last_tool_error()` call. Sends a Telegram message with the error summary and a "say 'fix it'" CTA. Never blocks the main response path.

### Email Notifications (M3)
Create `src/repair/notifications.py` with `send_ticket_created_email()` and `send_fix_ready_email()`. Both route through the connected Gmail workspace tool (`call_workspace_tool("send_gmail_message", ...)`) so no extra credentials are needed. Target address hardcoded to `lannys.lores@gmail.com` (single-user system, HC-5). Failures are swallowed with warning logs — email must never break the pipeline.

### Inline Keyboard "Apply fix now?" (M4)
After `execute_pending_repair()` verifies the sandbox, it fires `notify_fix_ready()` with an aiogram `InlineKeyboardMarkup` containing two buttons: **✅ Apply fix now** (`repair_approve:<id>`) and **❌ Skip for now** (`repair_skip:<id>`). The `cb_repair_approve` callback in `handlers.py` calls `approve_ticket_deploy()` directly. This reduces the deploy approval flow from 3+ message exchanges to a single button tap.

### `/tickets` and `/ticket` Commands (M5)
- `/tickets` — queries `RepairTicket` rows for the current user filtered to non-terminal statuses, renders a status-icon list.
- `/ticket approve <id>` — owner-only, calls `approve_ticket_deploy()`.
- `/ticket close <id>` — owner-only, marks ticket `closed` without deploying.
- Both registered in Telegram BotCommand menu via `src/main.py`.

### Pipeline Robustness (M6)
- **Structured success check:** replaced fragile `"✅ Patch Verified" in result` with `any(marker in result for marker in _SUCCESS_MARKERS)` — insensitive to emoji changes.
- **Max retries guard:** `_PIPELINE_ATTEMPT_COUNTS` dict keyed by `"{user_id}:{error_description[:80]}"`. Capped at `_PIPELINE_MAX_ATTEMPTS = 3`. Returns `MAX_RETRIES_EXCEEDED` decision after the cap.
- **Low-risk Telegram notify:** `propose_low_risk_fix` now calls `notify_low_risk_applied()` after auto-apply instead of the previous comment saying "you should confirm via Telegram".

## Consequences

### Positive
- Owner sees every error immediately in Telegram — no more silent failures.
- Deploy approval is a one-tap UX instead of a multi-step conversation.
- `/tickets` gives full visibility into repair state without Dashboard access.
- Pipeline retry cap prevents runaway self-healing from hammering the same broken code path.
- Sandbox detection is now symbol-agnostic.

### Trade-offs / Limitations
- `_PIPELINE_ATTEMPT_COUNTS` is in-memory — resets on container restart. A persistent counter in Redis would be more durable (future work).
- Email delivery depends on Gmail MCP being connected. If disconnected, emails are silently skipped (logged at WARNING). An SMTP fallback could be added (future work).
- Inline keyboard `repair_approve` callback calls `approve_ticket_deploy()` directly, bypassing the security challenge gate. The security gate remains for the text-based flow (`/ticket approve <id>` → security challenge). The button flow trusts Telegram's authentication of the chat owner (acceptable for single-user system, HC-5).

## Alternatives Considered

- **WebSocket/dashboard approval only** — rejected; violates HC-8 (non-technical user must never need browser access for routine ops).
- **SMTP email directly** — rejected in favour of workspace Gmail to avoid new credentials and stay consistent with HC-1 (all infra self-hosted).
- **Persisting retry counts to Redis** — deferred to keep scope minimal; added to Pending Work.
