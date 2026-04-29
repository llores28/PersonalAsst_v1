# mem0-park-scoring

**Park et al. 2023** ([Generative Agents](https://arxiv.org/abs/2304.03442))
eviction scoring for Mem0-backed memory stores. Pure-Python, zero deps,
LLM-free.

## The problem

Mem0 doesn't ship a built-in eviction policy — once your store fills, you
either keep paying for vector storage + embedding-API calls forever, or
you delete oldest-first and lose forensic value. Neither is great.

Park et al.'s [Generative Agents](https://arxiv.org/abs/2304.03442) paper
proposed a composite score combining **recency**, **importance**, and
**relevance** that consistently beats single-axis policies. This package
lifts that scoring formula out of [Atlas (PersonalAsst)](https://github.com/llores28/PersonalAsst)
where it's been running in production since 2026-04-26.

## The formula

```
score = 0.45·recency + 0.25·access_norm + 0.30·importance

  recency      = exp(-Δhours / 720)                 # ~30-day half-life
  access_norm  = log(1 + access) / log(1 + max_access_in_batch)
  importance   = metadata.importance                # default 0.5
```

The relevance term from Park et al. is replaced by **access frequency**
because eviction runs offline (not at retrieval time), so we don't have a
query vector to compute relevance against. Access frequency is a strong
correlate.

## Install

```bash
pip install mem0-park-scoring
```

## Usage

```python
from mem0_park_scoring import score_memory, select_for_eviction

# Score a single memory dict (Mem0's get_all() shape)
mem = {
    "id": "m-1",
    "memory": "User prefers morning meetings",
    "created_at": "2026-04-01T08:00:00Z",
    "metadata": {"access_count": 12, "importance": 0.8},
}
print(score_memory(mem, max_access=20, now=...))  # 0.0–1.0

# Pick which memories to evict to bring the count under target
all_mems = mem_client.get_all(filters={"user_id": "alice"})
to_evict = select_for_eviction(all_mems, target_count=7200, cap=8000)
for m in to_evict:
    mem_client.delete(m["id"])
```

## Data shape contract

The functions accept any dict with these keys (Mem0's `get_all` shape works
out of the box):

| Key | Type | Description | Default |
|---|---|---|---|
| `id` | string | unique id, used for dedup | required |
| `memory` (or `text`) | string | the memory content | required |
| `created_at` | ISO 8601 string | when the memory was added | now |
| `updated_at` | ISO 8601 string | last update time (preferred over created_at) | falls back to created_at |
| `metadata.access_count` | int | how often this memory has been retrieved | 0 |
| `metadata.importance` | float ∈ [0, 1] | a-priori importance | 0.5 |
| `metadata.is_summary` | bool | summary memories are eviction-protected | false |

## Compatibility

- Python ≥ 3.10
- Works with any Mem0 backend (Qdrant, FAISS, Chroma, hosted)
- Composes with [`agent-poison-filter`](https://pypi.org/project/agent-poison-filter/)
  for write-time + read-time + eviction-time defense

## Provenance

Extracted from [Atlas (PersonalAsst)](https://github.com/llores28/PersonalAsst)
where it caps Mem0 at 8000 memories per user with a nightly summarize-then-
delete pipeline. See the original module for the two-phase
LLM-summarization implementation that protects against data loss when the
summarizer is unavailable.

## License

MIT — see `LICENSE`.
