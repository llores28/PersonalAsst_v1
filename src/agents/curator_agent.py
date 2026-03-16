"""Curator agent — weekly self-improvement review (ACE pattern step 3).

Analyzes interaction patterns, proposes persona adjustments, prunes stale memories.
Runs as a scheduled job (Sunday 2am user timezone) — resolves PRD §10 ACE criteria.
"""

import json
import logging

from agents import Agent, Runner

from src.settings import settings

logger = logging.getLogger(__name__)

CURATOR_INSTRUCTIONS = """\
You are a Curator specialist. You analyze the assistant's recent interactions and improve it.

Your job runs weekly. You receive a summary of the past week's interactions and must output
a JSON response with exactly these fields:

{
  "persona_adjustments": [
    {"field": "style|traits|proactivity", "current": "...", "proposed": "...", "reason": "...", "confidence": 0.0-1.0}
  ],
  "memories_to_prune": [
    {"memory_id": "...", "reason": "stale|irrelevant|contradicted"}
  ],
  "new_procedural_memories": [
    {"text": "...", "reason": "pattern detected in N interactions"}
  ],
  "quality_summary": {
    "avg_score": 0.0-1.0,
    "total_interactions": 0,
    "common_topics": ["..."],
    "improvement_areas": ["..."]
  }
}

Rules:
- Only propose persona changes with confidence > 0.7
- Only prune memories older than 30 days with low relevance
- Extract procedural memories only from patterns seen 3+ times
- Be conservative — small adjustments are better than big swings
"""


async def run_weekly_curation(user_id: int) -> dict:
    """Run the weekly curation cycle for a user.

    Called by APScheduler every Sunday at 2am user timezone.
    """
    from src.memory.mem0_client import search_memories, get_all_memories, delete_memory, add_memory
    from src.memory.persona import get_active_persona, create_persona_version
    from src.db.session import async_session
    from src.db.models import AuditLog, User
    from sqlalchemy import select, func as sqlfunc
    from datetime import datetime, timedelta

    logger.info("Starting weekly curation for user %d", user_id)

    # Gather last 7 days of interaction summaries from audit log
    one_week_ago = datetime.utcnow() - timedelta(days=7)
    async with async_session() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.user_id == user_id,
                AuditLog.timestamp >= one_week_ago,
            ).order_by(AuditLog.timestamp.desc()).limit(100)
        )
        logs = result.scalars().all()

    if not logs:
        logger.info("No interactions in past week for user %d — skipping curation", user_id)
        return {"skipped": True, "reason": "no_interactions"}

    # Build interaction summary for the curator
    interaction_summary = "\n".join(
        f"[{log.timestamp}] Agent: {log.agent_name or 'orchestrator'} | "
        f"Tools: {json.dumps(log.tools_used) if log.tools_used else 'none'} | "
        f"Error: {log.error or 'none'}"
        for log in logs[:50]
    )

    # Get current memories for context
    all_memories = await get_all_memories(user_id=str(user_id))
    memory_summary = "\n".join(
        f"- [{m.get('id', '?')}] {m.get('memory', m.get('text', ''))}"
        for m in all_memories[:30]
    )

    # Get current persona
    persona = await get_active_persona(user_id)
    persona_summary = json.dumps(persona) if persona else "default persona"

    curator_input = (
        f"## Weekly Review Data\n\n"
        f"### Current Persona\n{persona_summary}\n\n"
        f"### Interactions This Week ({len(logs)} total)\n{interaction_summary}\n\n"
        f"### Current Memories ({len(all_memories)} total)\n{memory_summary}"
    )

    try:
        curator = Agent(
            name="Curator",
            instructions=CURATOR_INSTRUCTIONS,
            model=settings.model_fast,
        )
        result = await Runner.run(curator, curator_input)

        try:
            curation = json.loads(result.final_output)
        except json.JSONDecodeError:
            logger.warning("Curator output not valid JSON: %s", result.final_output[:200])
            return {"error": "parse_failure"}

        # Apply persona adjustments (only high confidence)
        for adj in curation.get("persona_adjustments", []):
            if adj.get("confidence", 0) >= 0.7 and persona:
                personality = dict(persona.get("personality", {}))
                field = adj.get("field")
                proposed = adj.get("proposed")
                if field == "style" and proposed:
                    personality["style"] = proposed
                elif field == "traits" and proposed:
                    personality["traits"] = [t.strip() for t in proposed.split(",")]

                await create_persona_version(
                    user_id,
                    persona.get("assistant_name", settings.default_assistant_name),
                    personality,
                    f"Curator weekly review: {adj.get('reason', 'auto-adjustment')}",
                )
                logger.info("Curator adjusted persona for user %d: %s", user_id, adj)

        # Prune stale memories
        for mem in curation.get("memories_to_prune", []):
            mem_id = mem.get("memory_id")
            if mem_id:
                await delete_memory(mem_id)
                logger.info("Curator pruned memory %s: %s", mem_id, mem.get("reason"))

        # Add new procedural memories
        for proc in curation.get("new_procedural_memories", []):
            text = proc.get("text")
            if text:
                await add_memory(
                    text,
                    user_id=str(user_id),
                    metadata={"type": "procedural", "source": "curator"},
                )
                logger.info("Curator added procedural memory for user %d: %s", user_id, text[:80])

        logger.info("Weekly curation complete for user %d", user_id)
        return curation

    except Exception as e:
        logger.exception("Curator failed for user %d: %s", user_id, e)
        return {"error": str(e)}
