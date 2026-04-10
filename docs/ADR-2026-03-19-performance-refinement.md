# ADR: Performance & Efficiency Refinement (2026-03-19)

## Context

Research review of 4 recent papers on agentic AI architectures, memory systems, and prompt optimization identified concrete improvements applicable to PersonalAsst. A critical bug was also discovered: the dynamic persona path used a stale duplicate template missing all new skills.

## Research Sources

- **Agentic AI Architectures** (arXiv 2601.12560) — Unified architecture taxonomy, error propagation analysis
- **A-MEM: Agentic Memory** (NeurIPS 2025, arXiv 2502.12110) — Self-organizing memory with Zettelkasten-style linking
- **AgeMem: Unified LTM/STM** (arXiv 2601.01885) — Memory management as a tool interface
- **Prompt Caching for Agentic Tasks** (arXiv 2601.06007) — 41-80% cost reduction via stable prefix ordering

## Decisions

### 1. Unified Persona Template (Bug Fix)
**What:** Deleted ~170 lines of stale duplicate `PERSONA_TEMPLATE` from `src/memory/persona.py`. Now re-exports from canonical `src/agents/persona_mode.py`.
**Why:** Dynamic persona path (every live request) was showing only 5 of 12 capabilities.

### 2. Prompt Cache-Efficient Structure
**What:** Restructured persona template: static content first (Office Organizer, skills, rules, domain boundaries), dynamic content last (user identity, preferences, datetime, connected email). Reordered `build_persona_mode_addendum` so Runtime Context (datetime) is last.
**Why:** Research shows stable prefix maximizes OpenAI prompt caching (41-80% cost reduction). Dynamic values in the prefix break the cache for everything after.

### 3. Dynamic Complexity-Aware Model Routing
**What:** Added `_classify_message_complexity()` heuristic classifier. Short/simple reads → LOW (nano/mini model), write operations → MEDIUM (standard), multi-service coordination → HIGH (standard/pro). Wired into `create_orchestrator_async`.
**Why:** ~70% of personal assistant requests are simple reads (check email, show calendar). Using the cheapest capable model for these saves 30-50% on API costs.
**Tradeoff:** Heuristic may misclassify edge cases, but defaults to LOW which is safe (model can still use all tools).

### 4. Memory Deduplication + Access Tracking
**What:** `add_memory()` now searches for semantically similar memories (>0.85 cosine) before storing. If found, updates the existing entry. `search_memories()` increments an `access_count` in memory metadata.
**Why:** A-MEM research shows self-organizing memory with dedup dramatically improves recall quality. Access count informs the curator's weekly pruning decisions.

### 5. Unified STM/LTM Tools
**What:** Added `summarize_my_conversation` and `get_my_recent_context` tools to Memory skill (now 7 tools total).
**Why:** AgeMem research shows unified LTM/STM management via explicit tool interface outperforms independent modules. The model can now explicitly manage session context.

### 6. Quality Score Tracking + Trend Alerts
**What:** Reflector background task records scores to Redis sorted list. Checks 5-interaction rolling average; logs warning when trend drops below 0.5.
**Why:** Enables data-driven quality improvement. Future: trigger early curator runs on degradation.

### 7. Task List Caching
**What:** Google Tasks list responses cached in Redis (30s TTL). Completion follow-ups check cache before re-fetching.
**Why:** Eliminates redundant API call when user says "mark it complete" right after "list tasks".

## Consequences

- **Cost:** Estimated 30-50% reduction from complexity routing + prompt caching
- **Latency:** Reduced for simple reads (cheaper model) and task follow-ups (cached)
- **Memory quality:** Dedup prevents bloat; access tracking enables smart pruning
- **Tests:** 329 passing (7 new tests added)
