"""Application entry point — starts Telegram bot, seeds DB, and runs event loop."""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher

from src.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def seed_owner() -> None:
    """Seed the owner's Telegram ID into allowed_users on first boot."""
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import AllowedUser, User

    async with async_session() as session:
        existing = await session.execute(
            select(AllowedUser).where(
                AllowedUser.telegram_id == settings.owner_telegram_id
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(
                AllowedUser(
                    telegram_id=settings.owner_telegram_id,
                    role="owner",
                )
            )
            await session.commit()
            logger.info("Seeded owner Telegram ID: %d", settings.owner_telegram_id)

        # Also ensure a User row exists for the owner
        user_existing = await session.execute(
            select(User).where(User.telegram_id == settings.owner_telegram_id)
        )
        if user_existing.scalar_one_or_none() is None:
            session.add(
                User(
                    telegram_id=settings.owner_telegram_id,
                    is_owner=True,
                    timezone=settings.default_timezone,
                )
            )
            await session.commit()
            logger.info("Created User row for owner")


async def run_migrations() -> None:
    """Run Alembic migrations programmatically on startup."""
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config("alembic.ini")
    # Run in a thread to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, command.upgrade, alembic_cfg, "head")
    logger.info("Database migrations applied")


async def main() -> None:
    """Main entry point."""
    logger.info("Starting PersonalAssistant...")

    # Set OpenAI API key
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key

    # Run DB migrations
    try:
        await run_migrations()
    except Exception as e:
        logger.error("Migration failed: %s", e)
        logger.info("Continuing without auto-migration — run 'alembic upgrade head' manually")

    # Seed owner
    await seed_owner()

    # Start scheduler (Phase 4)
    from src.scheduler.engine import start_scheduler, stop_scheduler
    try:
        await start_scheduler()
        logger.info("Scheduler started")
    except Exception as e:
        logger.error("Scheduler failed to start: %s", e)
        logger.info("Continuing without scheduler")

    # Create bot and dispatcher
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    # Register handlers
    from src.bot.handlers import router
    dp.include_router(router)

    logger.info("Bot starting — polling for messages...")
    try:
        await dp.start_polling(bot)
    finally:
        await stop_scheduler()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
