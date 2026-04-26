# ADR-2026-04-26 — Weekly OAuth Heartbeat + Telegram Re-consent Nudge

**Status:** Accepted
**Date:** April 26, 2026
**Deciders:** Owner

## Context

Google's OAuth 2.0 implementation has two failure modes that disable Workspace integrations *silently* — the user never gets a notification, and our existing infrastructure had no way to detect either:

1. **6-month idle expiry.** Refresh tokens that go unused for 6 months are revoked. A user who pauses Atlas for a long vacation comes back to a working bot that quietly cannot read their email.
2. **100-token-per-client cap.** Each OAuth client gets at most 100 active refresh tokens per user. The 101st `/connect google` evicts the oldest token. Multi-device users hit this without warning.

Atlas runs against the workspace-mcp sidecar; both failure modes surface only as `[AUTH ERROR]` strings on the next tool call. Without a periodic exercise of every connected user's refresh path, we can't detect the loss until they manually try a Gmail/Calendar/Drive action and receive a degraded response.

## Decision

### A weekly `_internal_weekly_oauth_heartbeat` system job
Every Monday 09:00 UTC, the job iterates every user with a `google_email:{user_id}` Redis key and calls `call_workspace_tool("get_user_profile", {})`. The single call exercises the entire refresh-token round-trip end-to-end — that resets Google's idle clock *and* validates the access token in one operation, regardless of which Workspace tool the user actually uses day-to-day.

### Three-bucket classification (`_classify_workspace_response`)
Responses are bucketed by bracketed-tag detection, NOT HTTP code:

| Tag pattern | Bucket | Action |
|---|---|---|
| `[AUTH ERROR]` | `auth_failed` | Send re-consent nudge. |
| `[RATE LIMIT]` | `transient` | Skip; try next week. |
| `[CONNECTION ERROR]` | `transient` | Skip; try next week. |
| `[TOOL ERROR]` (anything else) | `transient` | Skip; try next week. |
| no tag | `ok` | Increment counter; record success. |

Two buckets aren't enough — collapsing transient and auth failures together would either generate false-positive nudges on 5xx blips (one bad MCP run = mass nudge storm) or hide real revocations behind "try again later" semantics.

### Telegram re-consent nudge for `auth_failed`
A new helper `notify_oauth_reauth_required(user_telegram_id, *, email=None)` in [src/bot/notifications.py](../src/bot/notifications.py) sends a Markdown message asking the user to run `/connect google`. The connected email (looked up best-effort from `google_email:{user_id}`) is included for context — "Account: alice@example.com" — so a user who connected multiple Google accounts knows which one needs attention.

### Redis-backed dedup with a 6-day TTL
Key `notification_sent:{user_id}:oauth_reauth`. The 6-day choice is intentional:

- **7 days would create a race** — the next Monday heartbeat runs exactly one cadence-cycle later, and TTL expiry timing isn't guaranteed to fall before the heartbeat. A stuck user might skip a week.
- **6 days is shorter than the cadence** — guarantees the dedup key is gone before the next heartbeat fires. A user who ignores week N's nudge gets re-nudged on week N+1.
- **Same-week ad-hoc heartbeat re-runs are still suppressed** — running `weekly_oauth_heartbeat()` manually for debugging won't double-spam.

### Fail-open Redis policy
If Redis is unreachable when checking the dedup key, send the nudge anyway. **Reasoning:** a duplicate nudge is annoying; a missed nudge means a stuck user never knows. For a security-critical re-consent prompt, fail-open is the right asymmetry. The dedup key is set ONLY after a successful Telegram send, so transient bot failures don't prematurely "use up" the dedup window.

## Consequences

### Positive
- Idle users get pulled back before Google's 6-month axe falls.
- 100-token-cap evictions surface within 7 days instead of "whenever the user happens to try Gmail next."
- The classifier is a pure function — easy to test, easy to extend with new tags as workspace-mcp adds them.
- Heartbeat report (`users_checked / users_ok / users_auth_failed / users_transient / users_nudged`) is structured for observability — visible in scheduler logs and (via `JobReleased`) in `/api/health/scheduler`.

### Trade-offs / Limitations
- **Once-per-week cadence** — a user who reconnects on a Tuesday waits until next Monday for confirmation. Acceptable: the reconnection itself works immediately; the heartbeat is just monitoring.
- **No transient-spike escalation** — if a user stays in `transient` for many weeks, we never escalate to a user-visible message. Could add a "still transient after N weeks" alert (future work).
- **Email lookup is best-effort** — if Redis is missing the `google_email:{user_id}` key, the nudge goes out without the account name. Less polished but not broken.

## Alternatives Considered

- **Per-tool-failure detection** — fire the nudge whenever any Workspace tool returns `[AUTH ERROR]`. Rejected: would also fire on transient errors and on first-time users not yet connected, generating noise. The weekly-batch approach decouples detection from any specific user action.
- **Daily heartbeat** — rejected as cost-disproportionate. One `get_user_profile` call per user per day × N users × 365 = wasted MCP capacity. Weekly is sufficient since revocations don't usually happen on day-boundaries.
- **2-bucket classifier (ok / failed)** — rejected; would either cause false-positive nudges on 5xx blips or mute real revocations.
- **24-hour dedup TTL** — rejected; too short. A user who didn't see Monday's notification (notifications collapsed, phone off) would get a fresh nudge every day — annoying enough to train them to ignore *all* nudges.
- **TTL-equal-to-cadence (7 days)** — rejected per the race argument above.
