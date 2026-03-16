"""Voice message handling — Whisper transcription via OpenAI API."""

import logging
import tempfile
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from src.settings import settings

logger = logging.getLogger(__name__)


async def transcribe_voice(file_id: str, bot) -> str:
    """Download a Telegram voice message and transcribe it via Whisper.

    Args:
        file_id: Telegram file_id of the voice message
        bot: aiogram Bot instance

    Returns:
        Transcribed text, or error message.
    """
    try:
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path

        # Download voice file from Telegram
        file_url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"

        async with httpx.AsyncClient() as client:
            response = await client.get(file_url, timeout=30)
            response.raise_for_status()
            audio_bytes = response.content

        # Write to temp file for OpenAI API
        suffix = Path(file_path).suffix or ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # Transcribe via OpenAI Whisper
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        with open(tmp_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )

        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)

        text = transcript.text.strip()
        if not text:
            return "(Voice message was empty or inaudible)"

        logger.info("Voice transcribed (%d chars): %s", len(text), text[:80])
        return text

    except Exception as e:
        logger.exception("Voice transcription failed: %s", e)
        return f"(Could not transcribe voice message: {str(e)[:100]})"
