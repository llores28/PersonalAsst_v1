"""Voice message handling — Whisper transcription and OpenAI TTS."""

import logging
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from openai import AsyncOpenAI
from sqlalchemy import select, update, insert

from src.settings import settings

logger = logging.getLogger(__name__)

TTS_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
_DEFAULT_VOICE = "alloy"


async def get_user_tts_voice(telegram_id: int) -> str:
    """Return the user's saved TTS voice, falling back to the default."""
    try:
        from src.db.session import async_session
        from src.db.models import User, UserSettings

        async with async_session() as session:
            row = await session.execute(
                select(UserSettings.tts_voice)
                .join(User, User.id == UserSettings.user_id)
                .where(User.telegram_id == telegram_id)
            )
            result = row.scalar_one_or_none()
            return result if result in TTS_VOICES else _DEFAULT_VOICE
    except Exception as exc:
        logger.debug("Could not read tts_voice for %s: %s", telegram_id, exc)
        return _DEFAULT_VOICE


async def set_user_tts_voice(telegram_id: int, voice: str) -> bool:
    """Persist the user's preferred TTS voice. Returns True on success."""
    if voice not in TTS_VOICES:
        return False
    try:
        from src.db.session import async_session
        from src.db.models import User, UserSettings

        async with async_session() as session:
            db_user = await session.execute(
                select(User.id).where(User.telegram_id == telegram_id)
            )
            user_id = db_user.scalar_one_or_none()
            if user_id is None:
                return False

            result = await session.execute(
                update(UserSettings)
                .where(UserSettings.user_id == user_id)
                .values(tts_voice=voice)
            )
            if result.rowcount == 0:
                await session.execute(
                    insert(UserSettings).values(user_id=user_id, tts_voice=voice)
                )
            await session.commit()
            return True
    except Exception as exc:
        logger.exception("Could not save tts_voice for %s: %s", telegram_id, exc)
        return False


async def transcribe_voice(file_id: str, bot) -> str:
    """Download a Telegram voice message and transcribe it via Whisper.

    Args:
        file_id: Telegram file_id of the voice message
        bot: aiogram Bot instance

    Returns:
        Transcribed text, or error string starting with '('.
    """
    try:
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path

        file_url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"

        async with httpx.AsyncClient() as client:
            response = await client.get(file_url, timeout=30)
            response.raise_for_status()
            audio_bytes = response.content

        suffix = Path(file_path).suffix or ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            tmp_path = tmp.name

            openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
            with open(tmp_path, "rb") as audio_file:
                transcript = await openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                )

        text = transcript.text.strip()
        if not text:
            return "(Voice message was empty or inaudible)"

        logger.info("Voice transcribed (%d chars): %s", len(text), text[:80])
        return text

    except Exception as e:
        logger.exception("Voice transcription failed: %s", e)
        return f"(Could not transcribe voice message: {str(e)[:100]})"


async def synthesize_speech(
    text: str,
    voice: Optional[str] = None,
    telegram_id: Optional[int] = None,
) -> bytes:
    """Convert text to speech using OpenAI TTS.

    Args:
        text: Text to convert to speech (truncated to 4096 chars).
        voice: TTS voice name override. If None, looks up the user's saved preference.
        telegram_id: Telegram user ID used to resolve saved voice preference.

    Returns:
        MP3 audio bytes.

    Raises:
        RuntimeError: If TTS fails.
    """
    MAX_TTS_CHARS = 4096
    if len(text) > MAX_TTS_CHARS:
        text = text[:MAX_TTS_CHARS]

    if voice is None:
        voice = await get_user_tts_voice(telegram_id) if telegram_id else _DEFAULT_VOICE

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="mp3",
    )
    audio_bytes = response.content
    logger.info("TTS synthesized %d chars → %d bytes (voice=%s)", len(text), len(audio_bytes), voice)
    return audio_bytes
