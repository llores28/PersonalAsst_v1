from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("aiogram", reason="aiogram is not installed locally")


@pytest.mark.asyncio
async def test_run_orchestrator_with_text_sends_generated_images() -> None:
    from src.agents.orchestrator import ImageAttachment, OrchestratorResult
    from src.bot.handler_utils import _run_orchestrator_with_text

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        answer_photo=AsyncMock(),
    )

    result = OrchestratorResult(
        text="Here is your image.",
        images=[
            ImageAttachment(
                data_base64="aGVsbG8=",
                mime_type="image/png",
                caption="A generated image",
                model="test-model",
            )
        ],
    )

    with patch("src.agents.orchestrator.run_orchestrator_result", new=AsyncMock(return_value=result)):
        await _run_orchestrator_with_text(message, "draw a sunset")

    message.answer_photo.assert_awaited_once()
    message.answer.assert_awaited_once_with("Here is your image.", parse_mode="Markdown")
