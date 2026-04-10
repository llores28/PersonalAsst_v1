import sys
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_INJECTED_MOCKS: list[str] = []
for _mod in ("agents",):
    if _mod not in sys.modules:
        _INJECTED_MOCKS.append(_mod)
        sys.modules[_mod] = MagicMock()

from src.agents.reflector_agent import _normalize_reflection


@pytest.fixture(autouse=True, scope="module")
def _cleanup_mocked_modules():
    yield
    for mod_name in _INJECTED_MOCKS:
        sys.modules.pop(mod_name, None)
    stale = [k for k in sys.modules if k.startswith("src.agents.reflector_agent")]
    for k in stale:
        sys.modules.pop(k, None)


class TestReflectorNormalization:
    def test_promised_artifact_without_delivery_is_marked_as_error(self) -> None:
        reflection = {
            "task_completed": False,
            "user_satisfied": None,
            "error_occurred": False,
            "quality_score": 0.2,
            "preference_learned": None,
            "workflow_learned": None,
            "improvement_suggestion": None,
        }
        user_message = "yes please give me the file i can download"
        assistant_response = (
            "Great! I'll create a Gmail filters.xml file based on your specifications. "
            "I'll provide a filters.xml file for direct Gmail import. "
            "Once the file is ready, you'll be able to download it here. "
            "Creating your file now..."
        )

        normalized = _normalize_reflection(reflection, user_message, assistant_response)

        assert normalized["task_completed"] is False
        assert normalized["user_satisfied"] is None
        assert normalized["error_occurred"] is True
        assert normalized["quality_score"] == 0.4
        assert normalized["improvement_suggestion"] is not None


class TestReflectOnInteraction:
    @pytest.mark.asyncio
    async def test_reflect_on_interaction_normalizes_model_output(self) -> None:
        from src.agents.reflector_agent import reflect_on_interaction

        user_message = "yes please give me the file i can download"
        assistant_response = (
            "Great! I’ll create a Gmail filters.xml file based on your specifications. "
            "I’ll provide a filters.xml file for direct Gmail import. "
            "Once the file is ready, you’ll be able to download it here. "
            "Creating your file now…"
        )
        model_output = {
            "task_completed": False,
            "user_satisfied": None,
            "error_occurred": False,
            "quality_score": 0.2,
            "preference_learned": None,
            "workflow_learned": None,
            "improvement_suggestion": "The assistant did not provide the file yet.",
        }
        result = SimpleNamespace(final_output=json.dumps(model_output))

        with patch("src.agents.reflector_agent.select_model", return_value=SimpleNamespace(model_id="test-model")):
            with patch("src.agents.reflector_agent.Runner.run", new=AsyncMock(return_value=result)):
                reflection = await reflect_on_interaction(user_message, assistant_response, "user-1")

        assert reflection["task_completed"] is False
        assert reflection["user_satisfied"] is None
        assert reflection["error_occurred"] is True
        assert reflection["quality_score"] == 0.4
        assert reflection["improvement_suggestion"] == "The assistant did not provide the file yet."
