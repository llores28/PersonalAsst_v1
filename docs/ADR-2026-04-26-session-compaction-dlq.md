# ADR-2026-04-26 — Session Compaction Dead-Letter Queue

**Status:** Accepted
**Date:** April 26, 2026
**Deciders:** Owner

## Context

Atlas's conversation memory uses two layers: a Redis-backed rolling session of the last 20 turns (fast-path context), and Mem0+Qdrant for long-term semantic memory. When a session exceeds `MAX_TURNS = 20`, [`add_turn`](../src/memory/conversation.py) drops the oldest turns and fires a fire-and-forget task that LLM-summarizes them and writes the summary to Mem0.

That fire-and-forget chain has two failure modes:
1. **OpenAI API errors** during the summarization call — rate limit, model outage, transient 5xx.
2. **Mem0/Qdrant write failures** — Qdrant unhealthy, embedding service down, dedup-comparison timeout.

The naive implementation logs the failure and moves on. That silently destroys conversational context: the dropped turns are gone from the session (already dropped before we tried to summarize), and now they never make it to long-term memory either. The user's bot just *forgets* a chunk of history, with no way to recover.

## Decision

### Two-stage chain: tenacity-retried inner, DLQ-writing outer
[src/memory/conversation.py](../src/memory/conversation.py) splits `_compact_turns_to_memory` into two functions:

- **`_compact_turns_to_memory_with_retry`** — `@retry`-decorated (3 attempts, exponential backoff, `reraise=True`). Does the LLM summarize + Mem0 write. Either succeeds or raises after retries.
- **`_compact_turns_to_memory` (outer)** — calls the inner; on exhaustion, dead-letters the raw turns to Redis at `compaction_dlq:{user_id}` with a 7-day TTL.

The DLQ payload is the original turns + a `failed_at` timestamp + a 500-char-truncated last error message. JSON-serialized; one entry per failed batch (RPUSH'd to a list so multiple failures in the same week accumulate).

### Why a list, not a set or single key
- Multiple compaction failures for the same user must NOT overwrite each other.
- Order matters — we want to replay in the original sequence if we ever build a replayer.
- 7-day TTL is applied to the LIST, not per-entry, because Redis lists don't support per-entry TTL natively. If a new failure pushes within the 7 days, the TTL is bumped (correct: still indicates "active failure window").

### `last_error` captured into outer scope BEFORE the DLQ write
Python's `except` block scope-leaks the exception variable in CPython but not in PyPy or alternative implementations — and even in CPython, code linters flag `e` references after the `except` block. We assign `last_error = str(e)` *inside* the `except` block, so the DLQ write below can read it without scoping concerns.

### "Last-ditch" critical log if the DLQ write itself fails
If Redis is also down, we log at `CRITICAL` with the count of lost turns. That's the only branch where we accept context loss; everywhere else we either succeed, retry, or dead-letter.

## Consequences

### Positive
- Conversational context is never silently dropped. Even an hour-long Mem0 outage just queues the turns until they can be replayed (replayer is future work; the data is preserved either way).
- The DLQ list is human-inspectable: `redis-cli LRANGE compaction_dlq:{user_id} 0 -1` shows every failed batch, with timestamps and root-cause errors.
- The 7-day TTL prevents the DLQ from growing unbounded if a user has chronic failures we never get around to fixing.
- The two-stage split keeps tenacity's exception view simple (it sees only the inner function's failures) while letting the outer handle the post-retry recovery path cleanly.

### Trade-offs / Limitations
- **No replay tool yet.** DLQ entries currently accumulate but aren't auto-replayed. A future `replay_compaction_dlq()` admin action could re-feed them through the summarization path. Until then, the DLQ is forensic, not self-healing.
- **7-day TTL caps recovery window.** If a user's DLQ sits unworked for 8 days, those turns are gone. Acceptable for a personal assistant; a multi-tenant system should index the DLQ in a database with no TTL.
- **DLQ stores raw turns, not summarization-attempt artifacts.** We don't preserve partial LLM outputs. If summarization got 80% through and crashed, the partial summary is gone — replay starts from scratch. Storage was the right trade (raw is small; partial outputs would be inconsistent in shape).
- **`metric: total_dlq_entries` is not yet emitted.** No alerting if the DLQ is silently growing. Could add a Prometheus-style counter (future work).

## Alternatives Considered

- **Just log and drop on failure.** Original behavior. Rejected — silent context loss erodes trust in the assistant's memory.
- **Synchronous summarize-on-MAX_TURNS in `add_turn`.** Rejected — would block the user's *next* response while we summarize the previous batch. Latency cost is wrong direction.
- **PostgreSQL-backed DLQ.** Rejected — adds a new table and migration for a queue that's almost always empty. Redis lists are good enough and consistent with how we already use Redis for ephemeral state.
- **Mem0's own retry mechanism.** Rejected — Mem0 (at the SDK layer we use) has limited retry config and doesn't distinguish transient from permanent failures the way tenacity does. Wrapping our own retry gives us per-error-class control.
- **Persistent error counter per user.** Considered for triggering an alert after N failed compactions. Deferred until we have evidence of chronic failures; cost-disproportionate for a single-user system today.
