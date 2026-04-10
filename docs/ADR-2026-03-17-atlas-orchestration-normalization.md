# ADR: Normalize Atlas runtime context for persona, temporal parsing, and action policy

- Status: Accepted
- Date: 2026-03-17

## Context

Atlas orchestration had grown in three separate directions:

- persona state mixed durable preferences, reusable workflows, recent session context, and the current request without an explicit boundary between them
- temporal handling existed as narrow calendar-specific regexes in the orchestrator and did not provide a shared normalization layer for scheduler or calendar write flows
- approval behavior was mostly encoded in static prompt prose rather than reinforced with per-request runtime context about whether a request was a read, draft, internal write, or external side effect

This made time-sensitive requests harder to route consistently and increased the chance that the model would treat task-local details like long-lived memory or under-specify confirmation requirements for write actions.

## Decision

Keep the current orchestrator/specialist architecture, but normalize runtime context in three small layers:

1. Add structured persona assembly with an explicit `Current Task` section and `Memory Strata` guidance so Atlas distinguishes durable preferences, learned behaviors, session context, and request-local context.
2. Introduce a shared `src/temporal.py` parser for calendar read ranges and scheduler/calendar write normalization, and append temporal interpretation blocks to orchestrator input when a strong parse is available.
3. Introduce a lightweight runtime action classifier in `src/action_policy.py` that labels requests as `read`, `draft`, `internal_write`, or `external_side_effect`, then append approval context for write-sensitive requests before orchestrator execution.

## Tradeoffs

This keeps the implementation centralized and reversible, with most behavior changes living in prompt/input preparation rather than broad specialist rewrites. The tradeoff is that some policy enforcement remains model-mediated instead of hard-blocked in code, so future work may still add stricter tool-level guardrails for especially sensitive actions.
