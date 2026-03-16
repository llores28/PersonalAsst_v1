"""Reflector agent — post-interaction quality evaluation (ACE pattern step 2).

Runs after each interaction to score quality and extract learnings.
Resolves PRD gap E3 (ACE acceptance criteria).
"""

import logging

from agents import Agent, Runner

from src.settings import settings

logger = logging.getLogger(__name__)

REFLECTOR_INSTRUCTIONS = """\
You are a quality reflector. You evaluate a user-assistant interaction and extract learnings.

Analyze the interaction and provide a JSON response with exactly these fields:
{
  "task_completed": true/false,
  "user_satisfied": true/false/null,
  "error_occurred": true/false,
  "quality_score": 0.0-1.0,
  "preference_learned": "string or null",
  "workflow_learned": "string or null",
  "improvement_suggestion": "string or null"
}

Scoring guide:
- 1.0 = User explicitly positive, task fully completed
- 0.8 = Task completed, no complaints
- 0.6 = Partially completed or user had to repeat
- 0.4 = Significant issues, user frustrated
- 0.2 = Failed, user unhappy
- 0.0 = Complete failure

For preference_learned: Extract any user preference revealed (e.g., "prefers morning meetings").
For workflow_learned: Extract any workflow pattern (e.g., "always reviews budget before sending reports").
Set to null if nothing was learned.
"""


async def reflect_on_interaction(
    user_message: str,
    assistant_response: str,
    user_id: str,
) -> dict:
    """Run the reflector agent on an interaction and store any learnings.

    Returns the reflection dict with quality scores and learned info.
    """
    interaction_text = (
        f"User said: {user_message}\n\n"
        f"Assistant responded: {assistant_response}"
    )

    try:
        reflector = Agent(
            name="Reflector",
            instructions=REFLECTOR_INSTRUCTIONS,
            model=settings.model_fast,
        )
        result = await Runner.run(reflector, interaction_text)

        import json
        try:
            reflection = json.loads(result.final_output)
        except json.JSONDecodeError:
            logger.warning("Reflector output was not valid JSON: %s", result.final_output[:200])
            return {"quality_score": 0.5, "error": "parse_failure"}

        # Store learned preferences in Mem0
        if reflection.get("preference_learned"):
            from src.memory.mem0_client import add_memory
            await add_memory(
                reflection["preference_learned"],
                user_id=user_id,
                metadata={"type": "semantic", "source": "reflector"},
            )
            logger.info("Reflector learned preference for user %s: %s",
                        user_id, reflection["preference_learned"])

        # Store learned workflows in Mem0
        if reflection.get("workflow_learned"):
            from src.memory.mem0_client import add_memory
            await add_memory(
                reflection["workflow_learned"],
                user_id=user_id,
                metadata={"type": "procedural", "source": "reflector"},
            )
            logger.info("Reflector learned workflow for user %s: %s",
                        user_id, reflection["workflow_learned"])

        return reflection

    except Exception as e:
        logger.error("Reflector failed: %s", e)
        return {"quality_score": 0.5, "error": str(e)}
