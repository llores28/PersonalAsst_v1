# ADR-2026-04-26 — Per-User Memory Cap with Summary Distillation

**Status:** Accepted
**Date:** April 26, 2026
**Deciders:** Owner

## Context

Mem0 + Qdrant gives Atlas persistent semantic memory across sessions, with dedup at 0.85 cosine similarity to suppress near-duplicates. What it does NOT give us is a cap. Every conversation potentially adds new memories; vector storage and embedding-API costs grow unbounded with usage.

For a single-user system that's used heavily over months/years, the unbounded curve is real:
- Qdrant's vector storage scales linearly with memory count.
- Every search re-embeds the query and computes cosine similarity against the entire user collection — search latency creeps up with size.
- The OpenAI text-embedding-3-small embeddings cost per memory added, even if 30% are dedup'd.

We need a per-user cap. The naive answer — "delete oldest N when over the cap" — is wrong: an old memory of `"my partner's name is Jamie"` is far more valuable than a recent memory of `"reminded me to take out the trash on Tuesday."` Eviction needs to be value-aware.

## Decision

### Score memories by a generative-agents-style formula
[`src/memory/eviction.py:compute_score`](../src/memory/eviction.py) returns:

```
score = 0.45 * recency_weight + 0.25 * access_weight + 0.30 * importance_weight
```

Where:
- `recency_weight = exp(-Δhours / 720)` — exponential decay with a 30-day half-life. A memory used yesterday scores ~1.0; a memory not touched in 90 days scores ~0.13.
- `access_weight = log(1 + access_count) / log(1 + max_access_in_corpus)` — log-normalized access frequency. A memory hit 100 times still doesn't dominate one hit 5 times by 20×; the log compresses heavy hitters.
- `importance_weight` — Mem0 metadata field. Defaults to `0.5` for legacy memories that pre-date the field.

The 45/25/30 weight choice is deliberate:
- **Recency dominates** because memory utility decays fast. A two-year-old reminder is rarely useful.
- **Importance is structurally weighted higher than access count** so that a one-off but tagged-important memory ("user prefers we discuss philosophy in long form") survives even when not recently accessed.
- **Access count is the smallest weight** because it correlates with topic prevalence, not value — frequent topics already get re-embedded as fresh memories anyway.

### Two-phase pipeline: write summaries BEFORE deleting
[`src/memory/eviction_runner.py:prune_user_memories`](../src/memory/eviction_runner.py) implements:

1. **Phase 1: select + summarize.** Score every memory; sort ascending; pick the lowest-scoring candidates until total ≤ `target_after_evict` (default 7,200, 10% headroom under the 8,000 cap). Group candidates into batches of 8 and call an OpenAI Agents SDK summarizer to produce condensed "consolidated" memories. Each summary is added to Mem0 with `metadata.atlas.is_summary = True`.
2. **Phase 2: delete originals.** ONLY if Phase 1 wrote all summaries successfully. If any summary write failed, we abort *before* deletion — the user keeps their original (over-cap) memories rather than losing both originals AND summaries.

If the Agents SDK is unavailable in the runtime container, we fall back to a deterministic stub `"[consolidated, N sources, summarization-failed]"` so the pipeline still runs (cap enforcement is the priority; quality summaries are the bonus).

### Summaries are protected from eviction
A memory with `metadata.atlas.is_summary == True` is filtered out of the eviction candidate set in [`select_for_eviction`](../src/memory/eviction.py). Otherwise the next eviction cycle would consume our own summaries — death-by-summary.

### Run nightly at 03:00 UTC via APScheduler
Single system-level job (`_internal_nightly_memory_eviction` in [src/scheduler/maintenance.py](../src/scheduler/maintenance.py)) iterates all users from the `users` table. NOT per-user job entries — at multi-user scale that pattern contends on the APScheduler job-store lock; the iterator pattern keeps the scheduler footprint constant regardless of user count.

Per-user calls are wrapped in tenacity retry (3 attempts, 2-30s exponential backoff) AND a per-user try/except so one user's transient Mem0 failure cannot abort the rest of the batch. The job body itself never raises — it returns a structured report. That way `EVENT_JOB_ERROR` listeners (the scheduler observability hook) stay a real signal: the only way to fire it is a bug in the iteration code itself.

### CLI escape hatch: `scripts/prune_memories.py`
For ad-hoc runs and migration scenarios. Uses lazy imports inside `main()` so `--help` works in environments without `OPENAI_API_KEY` set.

## Consequences

### Positive
- Per-user memory growth is now bounded in O(1) instead of O(months × usage).
- The lowest-value memories die first; high-value ones (recent, frequent, or marked important) survive.
- Summaries preserve aggregate context — losing 8 trivial memories about Tuesday meetings produces one summary that retains the gist.
- The two-phase pipeline guarantees no data loss when summarization fails: failure mode is "stay over cap one more night," not "lose memories."
- Single-iterator design carries multi-user scaling without topology change.

### Trade-offs / Limitations
- **Tunable parameters chosen by intuition.** The 45/25/30 weights, the 30-day half-life, the 8,000 cap, and the 10% target headroom were set by reasoning, not measurement. We should revisit after we have eviction-history telemetry. Adding score-distribution metrics is future work.
- **Summary quality varies.** GPT-summarized memories can drop nuance; very precise factual memories may be dulled. The dedup threshold should catch the worst case (a generic summary near-duplicating something already specific), but we don't formally test this.
- **`importance` is rarely set.** Most memories default to 0.5. Until we add automatic importance scoring (e.g. Reflector-graded), `importance_weight` is essentially a constant for most memories and `recency × access` does the real work.
- **Eviction is destructive.** Summaries are best-effort; the originals are gone. If a user later wants verbatim recall of a Tuesday meeting that got merged into a summary, that's not recoverable.

## Alternatives Considered

- **No cap; trust dedup.** Rejected — dedup catches near-duplicates but not topic drift. Six months of varied conversations yields thousands of distinct-but-low-value memories.
- **FIFO eviction (oldest N first).** Rejected — discards old-but-important memories (user's name, partner's name, long-standing preferences).
- **LRU eviction (least-recently-accessed N first).** Rejected — closer to right but ignores importance entirely; a recently-accessed Tuesday reminder beats a never-accessed-since-creation life fact.
- **Per-user APScheduler entries.** Rejected per scaling argument above; the single-iterator pattern is a body change to support multi-user, not a topology change.
- **Hard delete without summaries.** Rejected — losing aggregate context for marginal storage savings is the wrong trade for a personal assistant whose value comes from continuity.
- **Higher-quality summarization (full-context, multi-pass).** Deferred — the current single-pass summarizer is good enough for batch sizes of 8 and keeps the run-time bounded.
