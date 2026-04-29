"""Meta-reflector agent (Wave 1.2) — every-N-turns holistic review.

The per-turn ``reflector_agent`` is reactive: it scores one interaction and
extracts at most one preference + one workflow. That's great for fine-grained
learning but it can't see *patterns across many turns* — like "this skill
fires often but its outcome consistently scores below 0.4" or "the user keeps
phrasing the same intent three different ways and we have a different SKILL.md
for each one."

The meta-reflector runs every ``settings.meta_reflector_interval`` turns
(default 15, mirroring Hermes Agent's "every 15 tasks" cadence) and emits
proposals — never auto-applied — for:

1. **Skills to retire** — auto-skills whose invocations average a low quality
   score. The user reviews and confirms before deletion.
2. **Skills to consolidate** — multiple narrow skills covering the same intent;
   the meta-reflector suggests merging them into one.
3. **Persona refinements** — recurring preferences across the window that the
   per-turn reflector kept storing as separate Mem0 rows. Promoting them into
   the persona makes them surface earlier in the prompt.

Owner-gated by design: every proposal lands in Redis at
``meta_reflector_pending:{user_id}`` with a 7-day TTL. The user can review
via ``/meta`` (Telegram command, future wave) or directly via Redis. No
filesystem changes are applied without explicit approval — that prevents the
meta-reflector itself from becoming the next source of poisoned auto-skills.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from agents import Agent, Runner

from src.models.router import ModelRole, select_model
from src.settings import settings

logger = logging.getLogger(__name__)


META_REFLECTOR_INSTRUCTIONS = """\
You are a meta-reflector. You review the last N user-assistant turns to find
recurring patterns the per-turn reflector missed. Output STRICTLY this JSON:

{
  "skills_to_retire": [{"skill_id": "...", "reason": "..."}],
  "skills_to_consolidate": [{"skill_ids": ["...", "..."], "merged_id": "...", "reason": "..."}],
  "skill_patches": [{"skill_id": "...", "diagnosis": "...", "suggested_change": "..."}],
  "persona_refinements": [{"trait": "...", "evidence": "..."}],
  "summary": "one sentence describing the period"
}

Rules:
- Only propose retirement when a skill was invoked AND the average quality
  score on those invocations was clearly below 0.4. Don't retire skills that
  were never tried.
- Only propose consolidation when ≥2 skills cover the same user intent. Cite
  the IDs you would merge and a stable merged_id.
- skill_patches: when a low-quality turn (the "low_quality_turns" input
  section) names a tag or phrasing that overlaps with one of the
  auto_skill_ids, suggest a concrete change to that skill's instructions
  ("clarify routing hint X", "add edge case Y to instructions", etc.). The
  patch is a description, not a code diff — quality_control_agent will
  validate before any file is touched.
- Persona refinements should be repeated across at least 3 turns. One-shot
  preferences belong in Mem0, not in the persona.
- Empty arrays are fine — be conservative; missing a proposal is cheaper than
  a noisy one.
- Don't propose anything that requires installing new dependencies, running
  code, or making external calls.
"""


def _default_proposals() -> dict:
    return {
        "skills_to_retire": [],
        "skills_to_consolidate": [],
        "skill_patches": [],
        "persona_refinements": [],
        "summary": "no recurring patterns detected",
    }


async def _gather_review_window(user_id: str, window: int) -> dict:
    """Collect the inputs the meta-reflector reasons over.

    Pulls:
    - the last ``window`` quality scores from Redis
    - the auto-skill IDs currently on disk (so the LLM has the universe of
      retire/consolidate candidates)
    - the last ``window`` reflector-sourced procedural memories from Mem0
    - the skill_refinement_queue (Wave 1.3): low-quality turns waiting for
      pattern-matching against auto-skills
    """
    from src.memory.conversation import (
        drain_skill_refinement_queue,
        get_redis,
    )
    from src.memory.mem0_client import search_memories
    from src.skills.skill_writer import _resolve_auto_skills_dir

    r = await get_redis()
    scores_raw = await r.lrange(f"quality_scores:{int(user_id)}", -window, -1)
    scores = [float(s) for s in scores_raw if s]

    auto_dir = _resolve_auto_skills_dir()
    auto_skill_ids = sorted(
        item.name for item in auto_dir.iterdir()
        if item.is_dir() and (item / "SKILL.md").exists()
    ) if auto_dir.exists() else []

    # Pull recent reflector-tagged procedural memories. We don't have a way
    # to filter Mem0 by metadata in v2 search, so we over-fetch and filter
    # client-side. Cap at window*2 so we never balloon the LLM input.
    procedural = []
    try:
        hits = await search_memories(
            "user workflow preference assistant", user_id=user_id, limit=window * 2,
        )
        for hit in hits:
            meta = hit.get("metadata") or {}
            if meta.get("source") == "reflector" and meta.get("type") == "procedural":
                procedural.append(hit.get("memory") or hit.get("text") or "")
    except Exception as exc:
        logger.debug("Meta-reflector mem0 fetch failed (non-critical): %s", exc)

    # Wave 1.3: drain the skill-refinement queue. We *consume* on read so a
    # given low-quality turn is reviewed at most once — if the meta-reflector
    # produces a patch proposal, it's owner-gated; if not, the turn rolls off.
    try:
        refinement_queue = await drain_skill_refinement_queue(int(user_id))
    except Exception as exc:
        logger.debug("Meta-reflector refinement-queue drain failed: %s", exc)
        refinement_queue = []

    return {
        "quality_scores": scores,
        "auto_skill_ids": auto_skill_ids,
        "recent_workflows": procedural[:window],
        "low_quality_turns": refinement_queue,
    }


async def run_meta_reflection(user_id: str, *, window: Optional[int] = None) -> dict:
    """Run a single meta-reflection pass and return the proposals dict.

    Always returns a valid dict (the ``_default_proposals`` shape on any
    failure) so callers don't need exception handling.
    """
    win = window or settings.meta_reflector_window
    review = await _gather_review_window(user_id, win)

    # Skip the LLM call entirely if there's nothing to reason over — saves
    # tokens and avoids hallucinated proposals on empty input.
    has_content = (
        review["quality_scores"] or review["recent_workflows"] or review["low_quality_turns"]
    )
    if not has_content:
        logger.debug("Meta-reflector: empty review window for user %s — skipping", user_id)
        return _default_proposals()

    low_q_lines = "\n".join(
        f"- score={t.get('quality_score', 0):.2f} :: user={(t.get('user_message') or '')[:120]!r} "
        f":: assistant={(t.get('assistant_response') or '')[:120]!r}"
        for t in review["low_quality_turns"][:window]
    ) or "(none)"
    review_text = (
        f"Last {win} quality scores: {review['quality_scores']}\n"
        f"Auto-skills on disk: {review['auto_skill_ids']}\n"
        f"Recent learned workflows:\n"
        + "\n".join(f"- {w}" for w in review["recent_workflows"][:win])
        + f"\n\nLow-quality turns awaiting skill refinement review:\n{low_q_lines}"
    )

    try:
        selection = select_model(ModelRole.REFLECTOR)
        agent = Agent(
            name="MetaReflector",
            instructions=META_REFLECTOR_INSTRUCTIONS,
            model=selection.model_id,
        )
        result = await Runner.run(agent, review_text)
        try:
            proposals = json.loads(result.final_output)
        except json.JSONDecodeError:
            logger.warning(
                "Meta-reflector returned non-JSON for user %s: %s",
                user_id, result.final_output[:200],
            )
            return _default_proposals()
    except Exception as exc:  # noqa: BLE001 — never block the turn flow
        logger.warning("Meta-reflector LLM call failed for user %s: %s", user_id, exc)
        return _default_proposals()

    # Defensive normalization: enforce the schema fields exist.
    normalized = _default_proposals()
    if isinstance(proposals, dict):
        for key in normalized:
            if key in proposals:
                normalized[key] = proposals[key]
    return normalized


async def maybe_run_meta_reflector(user_id: str) -> Optional[dict]:
    """Increment the per-user turn counter; if it hits the configured cadence,
    run the meta-reflection and persist proposals to Redis. Returns the
    proposals dict on a fire turn, ``None`` otherwise.
    """
    interval = int(settings.meta_reflector_interval)
    if interval <= 0:
        return None

    try:
        from src.memory.conversation import (
            increment_meta_reflector_count,
            store_meta_reflector_proposals,
        )

        count = await increment_meta_reflector_count(int(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Meta-reflector counter unavailable: %s", exc)
        return None

    if count % interval != 0:
        return None

    logger.info(
        "Meta-reflector firing on turn %d for user %s (interval=%d)",
        count, user_id, interval,
    )
    proposals = await run_meta_reflection(user_id)

    # Only persist when there's something actionable. An all-empty payload
    # would just clutter the pending-review queue.
    has_content = any(
        proposals.get(k) for k in (
            "skills_to_retire", "skills_to_consolidate",
            "skill_patches", "persona_refinements",
        )
    )
    if has_content:
        try:
            await store_meta_reflector_proposals(int(user_id), json.dumps(proposals))
            logger.info(
                "Meta-reflector proposals stored for user %s: "
                "%d retire / %d consolidate / %d patch / %d persona",
                user_id,
                len(proposals.get("skills_to_retire", [])),
                len(proposals.get("skills_to_consolidate", [])),
                len(proposals.get("skill_patches", [])),
                len(proposals.get("persona_refinements", [])),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist meta-reflector proposals: %s", exc)

    return proposals
