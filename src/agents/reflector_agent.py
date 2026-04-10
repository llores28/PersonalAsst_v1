"""Reflector agent — post-interaction quality evaluation (ACE pattern step 2).

Runs after each interaction to score quality and extract learnings.
Resolves PRD gap E3 (ACE acceptance criteria).
"""

import logging
import json

from agents import Agent, Runner

from src.models.router import ModelRole, select_model

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

Additional rules:
- If the assistant promises to create, attach, or provide a file, download, link, draft, or other concrete artifact but does not actually provide it in the same response, set task_completed=false.
- Treat an undelivered promised artifact as error_occurred=true even if no explicit exception text appears.
- If the user has not given explicit satisfaction feedback, set user_satisfied=null rather than guessing.
- If the assistant misses a clear deliverable but still responds coherently, cap quality_score at 0.4 instead of 0.0 unless the response is a total non-answer.
"""


def _default_reflection() -> dict:
    return {
        "task_completed": False,
        "user_satisfied": None,
        "error_occurred": False,
        "quality_score": 0.5,
        "preference_learned": None,
        "workflow_learned": None,
        "improvement_suggestion": None,
    }


def _coerce_optional_bool(value):
    if value in (True, False, None):
        return value
    return None


def _coerce_quality_score(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, score))


def _requested_artifact(user_message: str) -> bool:
    lowered = user_message.lower()
    artifact_terms = (
        "file",
        "download",
        "xml",
        "attachment",
        "attach",
        "link",
    )
    return any(term in lowered for term in artifact_terms)


def _artifact_delivered(assistant_response: str) -> bool:
    lowered = assistant_response.lower()
    delivery_terms = (
        "<?xml",
        "```xml",
        "```",
        "https://",
        "http://",
        "attached",
        "attachment:",
        "here is the file",
        "here's the file",
        "download link",
        "download: ",
    )
    return any(term in lowered for term in delivery_terms)


def _promised_artifact_without_delivery(user_message: str, assistant_response: str) -> bool:
    if not _requested_artifact(user_message):
        return False
    lowered = assistant_response.lower()
    promise_terms = (
        "i'll create",
        "i will create",
        "i'll provide",
        "i will provide",
        "creating your file now",
        "once the file is ready",
        "you'll be able to download it here",
        "you will be able to download it here",
        "file is ready",
    )
    return any(term in lowered for term in promise_terms) and not _artifact_delivered(assistant_response)


def _is_poisoned_workspace_learning(text: str) -> bool:
    """Return True if a reflector learning would poison workspace tool routing.

    When the LLM fails to call workspace tools (a routing/model issue),
    the reflector often learns things like 'Drive may require re-auth'
    or 'connector path needs fixing'. These get stored in Mem0 and
    cause future turns to also skip calling the tools — a vicious cycle.
    """
    lowered = text.lower()
    _POISON_PHRASES = (
        "re-auth", "reauth", "re-authorize", "reauthorize",
        "connector path", "connector issue", "session issue",
        "drive session", "drive connector", "drive inventory",
        "authenticated inventory", "authenticated listing",
        "tool access needs", "tool access to be fixed",
        "tools need fixing", "tools are broken", "tools aren't working",
        "not available in this turn", "isn't available in this turn",
        "not receiving the private", "require re-authorization",
        "may require re-", "needs to be fixed",
        "access may require", "access needs",
    )
    return any(phrase in lowered for phrase in _POISON_PHRASES)


def _normalize_reflection(reflection: dict, user_message: str, assistant_response: str) -> dict:
    normalized = _default_reflection()
    normalized.update({
        "task_completed": bool(reflection.get("task_completed", False)),
        "user_satisfied": _coerce_optional_bool(reflection.get("user_satisfied")),
        "error_occurred": bool(reflection.get("error_occurred", False)),
        "quality_score": _coerce_quality_score(reflection.get("quality_score")),
        "preference_learned": reflection.get("preference_learned") or None,
        "workflow_learned": reflection.get("workflow_learned") or None,
        "improvement_suggestion": reflection.get("improvement_suggestion") or None,
    })

    if _promised_artifact_without_delivery(user_message, assistant_response):
        normalized["task_completed"] = False
        normalized["error_occurred"] = True
        if normalized["user_satisfied"] is True:
            normalized["user_satisfied"] = None
        if normalized["user_satisfied"] is False:
            normalized["quality_score"] = min(normalized["quality_score"], 0.2)
        else:
            normalized["quality_score"] = 0.4
        if normalized["improvement_suggestion"] is None:
            normalized["improvement_suggestion"] = (
                "The assistant promised a downloadable artifact but did not actually provide it. "
                "It should generate the file or link in the same response, or ask only the minimum missing details first."
            )

    return normalized


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
        selection = select_model(ModelRole.REFLECTOR)
        reflector = Agent(
            name="Reflector",
            instructions=REFLECTOR_INSTRUCTIONS,
            model=selection.model_id,
        )
        result = await Runner.run(reflector, interaction_text)

        try:
            reflection = json.loads(result.final_output)
        except json.JSONDecodeError:
            logger.warning("Reflector output was not valid JSON: %s", result.final_output[:200])
            reflection = _default_reflection()
            reflection["error_occurred"] = True
            reflection["improvement_suggestion"] = "The reflector returned invalid JSON and should be corrected to emit the required schema."
            return reflection

        reflection = _normalize_reflection(reflection, user_message, assistant_response)

        # Store learned preferences in Mem0
        if reflection.get("preference_learned"):
            pref = reflection["preference_learned"]
            if _is_poisoned_workspace_learning(pref):
                logger.info("Reflector BLOCKED poisoned preference: %s", pref[:100])
            else:
                from src.memory.mem0_client import add_memory
                await add_memory(
                    pref,
                    user_id=user_id,
                    metadata={"type": "semantic", "source": "reflector"},
                )
                logger.info("Reflector learned preference for user %s: %s",
                            user_id, pref)

        # Store learned workflows in Mem0
        if reflection.get("workflow_learned"):
            wf = reflection["workflow_learned"]
            if _is_poisoned_workspace_learning(wf):
                logger.info("Reflector BLOCKED poisoned workflow: %s", wf[:100])
            else:
                from src.memory.mem0_client import add_memory
                await add_memory(
                    wf,
                    user_id=user_id,
                    metadata={"type": "procedural", "source": "reflector"},
                )
                logger.info("Reflector learned workflow for user %s: %s",
                            user_id, wf)

        return reflection

    except Exception as e:
        logger.error("Reflector failed: %s", e)
        return {"quality_score": 0.5, "error": str(e)}
