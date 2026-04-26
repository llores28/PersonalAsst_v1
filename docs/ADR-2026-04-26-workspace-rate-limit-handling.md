# ADR-2026-04-26 — Workspace MCP Rate-Limit Handling

**Status:** Accepted
**Date:** April 26, 2026
**Deciders:** Owner

## Context

The workspace-mcp sidecar surfaces *every* failure mode as content in its tool-result string — connection timeouts, auth expirations, rate-limit responses, file-permission errors. There is no exception channel; consumers see a string and have to classify it.

For Google Workspace specifically, rate-limit errors are *transient* by nature. Google's quota documents recommend exponential backoff with retry on 429 / `quotaExceeded` / `userRateLimitExceeded`. Without retry, a single throttling event during a multi-step agent action (e.g., "summarize last week's emails and create a Doc") would surface as a hard failure to the user, even though waiting 1-4 seconds and retrying would succeed.

The design constraint is the calling contract: `call_workspace_tool(tool_name, args)` is invoked from many agents and must return a string, never raise. The retries need to happen *inside* the wrapper without changing that contract for callers.

## Decision

### Two-layer wrapper
[src/integrations/workspace_mcp.py](../src/integrations/workspace_mcp.py) splits the tool call into:

- **`_call_workspace_tool_inner` (private)** — has a `@tenacity.retry` decorator (3 attempts, 1-4s exponential backoff). Raises `_TransientMCPError` on retryable conditions (connection timeout, rate-limit-in-result-text, rate-limit-in-exception-text). Returns formatted error strings (`[CONNECTION ERROR]`, `[AUTH ERROR]`, `[PERMISSION ERROR]`, `[TOOL ERROR]`) for *non-*retryable conditions.
- **`call_workspace_tool` (public)** — calls inner; catches the post-retry `_TransientMCPError` and converts it to a `[RATE LIMIT]` string. Callers never see exceptions.

The two layers exist so tenacity sees only the transient-shaped exception and retries it; everything else flows through as a final string immediately.

### Pattern-based rate-limit detection
The list `_RATE_LIMIT_PATTERNS` is conservative on purpose:

```python
_RATE_LIMIT_PATTERNS = (
    "429", "rate limit", "rate-limit", "ratelimit",
    "quota exceeded", "quotaexceeded",
    "too many requests",
    "userratelimitexceeded", "ratelimitexceeded",
)
```

Matched against `text.lower()`. We deliberately do NOT include the bare word `"limit"` — that would falsely trigger on benign content like "the limit of my patience" appearing in a generated email body. Each entry corresponds to a documented Google API rate-limit signal, observed either in the JSON response body or in error message strings.

The detection is run TWICE — once on the tool result text (line 197) and once on exception text (line 230) — because the workspace-mcp library inconsistently surfaces rate-limit errors: Gmail tends to put them in the result body, Drive tends to raise them as exceptions.

### `_parse_retry_after` is best-effort and informational only
We extract a `retry-after` hint from the error text via regex if present, but tenacity's wait strategy doesn't consume it — the value is logged so an operator inspecting logs can see if Google asked for longer than our 1-4s backoff actually provides. Honoring server-suggested retry-after would require a custom tenacity wait strategy and complicate the code for marginal benefit (Google's suggested values are typically 1-2s, well within our backoff range).

### `[RATE LIMIT]` is a transient classification, not a failure
The OAuth heartbeat's `_classify_workspace_response` (see [ADR-2026-04-26-oauth-heartbeat-and-reauth-nudge.md](ADR-2026-04-26-oauth-heartbeat-and-reauth-nudge.md)) explicitly buckets `[RATE LIMIT]` as `transient` — not `auth_failed`. A user who happens to be rate-limited during the heartbeat won't get a re-consent nudge for an unrelated cause.

## Consequences

### Positive
- Retries happen invisibly to callers — agents see "the action succeeded" or "[RATE LIMIT] try again," not a thrown exception.
- The classifier set (auth/connection/permission/tool/rate-limit) is the same vocabulary the MCP server already uses, so error handling stays simple.
- Rate-limit pattern list is centralized; adding a new signal is one-line.
- Heartbeat won't false-positive on rate-limited users.

### Trade-offs / Limitations
- **3 attempts × 1-4s = at most ~9s of total wait.** A genuine sustained quota outage will exhaust retries quickly. We accept this — the alternative (long retry chains) holds the user's request open and degrades the agent's responsiveness.
- **Pattern matching is heuristic.** A future Google API error string we haven't seen could escape the list and not retry. Mitigated by retrying on connection-class errors too (timeouts), which catches a broad class of transients regardless of message format.
- **Retry-after is logged but not honored.** A future improvement: implement a `wait_retry_after_or_exponential` strategy.
- **No circuit breaker.** If Google is genuinely down for an hour, we'll retry every call, every time. Tolerable since the bot is single-user and traffic is naturally bounded; a multi-user system would want a per-tool circuit breaker that opens after N consecutive `_TransientMCPError`s.

## Alternatives Considered

- **Single-layer wrapper that catches and returns inside the retry decorator.** Rejected — tenacity wouldn't see the exception, so retries wouldn't happen. The two-layer split is the cleanest way to keep tenacity's view simple and the public API string-only.
- **Honor `Retry-After` exactly via a custom wait strategy.** Deferred — implementation cost vs. marginal gain for this scale.
- **HTTP-status-code-based detection.** Rejected — `call_workspace_tool` doesn't have access to the HTTP layer; the MCP transport sits between us and the wire. We can only see what the MCP server gives us, which is text.
- **A circuit breaker library.** Deferred — would protect Google from us, not the other way around. Not a current pain point.
- **Surface rate-limit via a Telegram nudge.** Rejected — rate limits are by definition transient and self-resolving. A nudge would teach users to ignore *all* nudges. Logged-only is correct.
