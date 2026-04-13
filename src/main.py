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

    try:
        async with async_session() as session:
            # Check if owner is already in allowed_users
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
                        added_by=settings.owner_telegram_id,
                    )
                )
                await session.commit()
                logger.info(f"Owner {settings.owner_telegram_id} seeded in allowed_users")
            else:
                logger.info(f"Owner {settings.owner_telegram_id} already exists in allowed_users")
            
            # Check if owner has a User row
            user_existing = await session.execute(
                select(User).where(User.telegram_id == settings.owner_telegram_id)
            )
            if user_existing.scalar_one_or_none() is None:
                session.add(
                    User(
                        telegram_id=settings.owner_telegram_id,
                        timezone=settings.default_timezone,
                    )
                )
                await session.commit()
                logger.info("Created User row for owner")
    except Exception as e:
        logger.warning(f"Could not seed owner (tables may not exist yet): {e}")


async def seed_tool_credentials() -> None:
    """Seed tool credentials from environment variables into the Redis vault.

    This runs at startup so CLI and function-type tools can access
    credentials without reading os.environ directly.
    """
    try:
        from src.tools.credentials import store_credentials

        # LinkedIn credentials
        li_email = os.environ.get("LINKEDIN_EMAIL", "")
        li_password = os.environ.get("LINKEDIN_PASSWORD", "")
        if li_email and li_password:
            await store_credentials("linkedin", {
                "linkedin_email": li_email,
                "linkedin_password": li_password,
            })
            logger.info("LinkedIn credentials seeded into vault")

        onedrive_creds = {
            "onedrive_access_token": os.environ.get("ONEDRIVE_ACCESS_TOKEN", ""),
            "onedrive_refresh_token": os.environ.get("ONEDRIVE_REFRESH_TOKEN", ""),
            "microsoft_client_id": os.environ.get("MICROSOFT_CLIENT_ID", ""),
            "microsoft_client_secret": os.environ.get("MICROSOFT_CLIENT_SECRET", ""),
            "microsoft_tenant_id": os.environ.get("MICROSOFT_TENANT_ID", ""),
        }
        onedrive_creds = {key: value for key, value in onedrive_creds.items() if value}
        if onedrive_creds:
            await store_credentials("onedrive", onedrive_creds)
            logger.info("OneDrive credentials seeded into vault")

    except Exception as e:
        logger.warning("Could not seed tool credentials: %s", e)


async def run_migrations() -> None:
    """Run Alembic migrations programmatically when explicitly enabled."""
    if not settings.startup_migrations_enabled:
        logger.info(
            "Skipping startup migrations (set STARTUP_MIGRATIONS_ENABLED=true to enable)."
        )
        return

    logger.info("Running startup migrations via Alembic")
    try:
        from alembic import command
        from alembic.config import Config

        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        logger.info("Startup migrations applied successfully")
    except Exception as e:
        logger.exception("Startup migrations failed: %s", e)
        raise


async def main() -> None:
    """Main entry point."""
    try:
        logger.info("Starting PersonalAssistant...")

        # Set OpenAI API key
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key

        # Run DB migrations
        await run_migrations()

        # Seed owner
        await seed_owner()

        # Seed tool credentials from env vars into Redis vault
        await seed_tool_credentials()

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

        from aiogram.types import BotCommand
        from aiogram.methods import SetMyCommands, GetMyCommands

        dp = Dispatcher()

        # Register handlers
        from src.bot.handlers import router
        dp.include_router(router)

        # Register commands synchronously before polling starts
        commands = [
            BotCommand(command="start", description="Start the bot and run setup wizard"),
            BotCommand(command="help", description="Show available commands and help"),
            BotCommand(command="persona", description="View or update persona settings"),
            BotCommand(command="memory", description="Show what the assistant remembers"),
            BotCommand(command="forget", description="Delete specific memories"),
            BotCommand(command="stats", description="View usage statistics and costs"),
            BotCommand(command="tools", description="List available tools and manage credentials"),
            BotCommand(command="orgs", description="Manage organizations and teams"),
            BotCommand(command="skills", description="Manage skills (list, create, reload)"),
            BotCommand(command="schedules", description="List scheduled tasks"),
            BotCommand(command="connect", description="Connect to services (e.g., /connect google)"),
            BotCommand(command="security", description="Configure security PIN or questions"),
            BotCommand(command="cancel", description="Cancel current operation"),
        ]
        
        logger.info(f"Registering {len(commands)} commands with Telegram...")
        
        try:
            result = await bot(SetMyCommands(commands=commands))
            logger.info(f"✅ Successfully registered {len(commands)} commands with Telegram (result: {result})")
            
            # Verify commands were set
            current_commands = await bot(GetMyCommands())
            logger.info(f"✅ Verified {len(current_commands)} commands are active")
            for cmd in current_commands:
                logger.info(f"  Active: /{cmd.command}")
        except Exception as e:
            logger.error(f"❌ Failed to register commands: {e}")
            logger.exception("Command registration error details:")
        
        logger.info("Bot starting — polling for messages...")
        try:
            await dp.start_polling(bot)
        finally:
            await stop_scheduler()
            await bot.session.close()
    
    except Exception as e:
        logger.exception("Unhandled exception in main:")
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
