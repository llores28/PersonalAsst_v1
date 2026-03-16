"""Automated PostgreSQL backup — local volume + optional Google Drive upload.

Resolves PRD gap E4 (local backup fallback).
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from src.settings import settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("/app/backups")


async def run_pg_backup() -> str:
    """Run pg_dump and save to local backup directory.

    Returns the backup file path on success, or error message.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"assistant_backup_{timestamp}.sql"

    db_url = settings.database_url
    # Extract connection params from async URL
    # postgresql+asyncpg://user:pass@host:port/db → pg_dump compatible
    clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

    cmd = [
        "pg_dump",
        "--no-owner",
        "--no-privileges",
        f"--dbname={clean_url}",
        f"--file={backup_file}",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode == 0:
            size_kb = backup_file.stat().st_size / 1024
            logger.info("Backup created: %s (%.1f KB)", backup_file, size_kb)

            # Prune old backups (keep last 7)
            await _prune_old_backups(keep=7)

            return str(backup_file)
        else:
            err = stderr.decode("utf-8", errors="replace")
            logger.error("pg_dump failed: %s", err[:500])
            return f"Backup failed: {err[:200]}"

    except asyncio.TimeoutError:
        logger.error("pg_dump timed out after 120s")
        return "Backup failed: timeout"
    except FileNotFoundError:
        logger.warning("pg_dump not found — backup requires PostgreSQL client tools in container")
        return "Backup failed: pg_dump not available in container"
    except Exception as e:
        logger.exception("Backup failed: %s", e)
        return f"Backup failed: {str(e)}"


async def _prune_old_backups(keep: int = 7) -> None:
    """Remove old backup files, keeping the most recent N."""
    if not BACKUP_DIR.exists():
        return

    backups = sorted(BACKUP_DIR.glob("assistant_backup_*.sql"), reverse=True)
    for old_backup in backups[keep:]:
        old_backup.unlink(missing_ok=True)
        logger.info("Pruned old backup: %s", old_backup.name)


async def scheduled_backup(user_id: int) -> None:
    """Scheduled job callable — runs backup and notifies user."""
    result = await run_pg_backup()

    if result.startswith("/"):
        # Success — optionally notify user
        from src.scheduler.jobs import _get_bot, _get_telegram_id
        telegram_id = await _get_telegram_id(user_id)
        if telegram_id:
            size_kb = Path(result).stat().st_size / 1024
            bot = await _get_bot()
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ Daily backup complete ({size_kb:.0f} KB)",
                )
            finally:
                await bot.session.close()
    else:
        logger.error("Scheduled backup failed: %s", result)
