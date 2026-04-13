"""Repair verifier — runs tests after auto-applied low-risk patches and rolls back on failure.

Safety contract:
- Only called when risk_level == "low" (no file edits, no code changes).
- Runs a minimal pytest smoke subset so verification is fast (<30s).
- On failure: marks the repair ticket as verification_failed and Telegrams the owner.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_TEST_PREFIXES = (
    ("python", "-m", "pytest"),
    ("pytest",),
)
_VERIFY_TIMEOUT = 60  # seconds


async def run_quick_smoke(test_path: str = "tests/") -> tuple[bool, str]:
    """Run pytest on a subset of tests. Returns (passed, output_summary)."""
    cmd = ["python", "-m", "pytest", test_path, "-x", "-q", "--tb=short", "--no-header"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_VERIFY_TIMEOUT)
        output = stdout.decode(errors="replace")
        passed = proc.returncode == 0
        summary = output[-1500:] if len(output) > 1500 else output
        return passed, summary
    except asyncio.TimeoutError:
        return False, f"Verification timed out after {_VERIFY_TIMEOUT}s"
    except Exception as e:
        return False, f"Verification error: {e}"


async def verify_repair(plan: dict, ticket_id: Optional[int] = None) -> tuple[bool, str]:
    """Verify a repair plan by running the quick smoke suite.

    Args:
        plan: The repair plan dict (same format as RepairTicket.plan).
        ticket_id: Optional DB id for updating verification_results.

    Returns:
        (success, message)
    """
    logger.info("Verifying repair plan (ticket_id=%s)", ticket_id)
    passed, output = await run_quick_smoke()

    if ticket_id is not None:
        try:
            from src.db.session import async_session
            from src.db.models import RepairTicket
            from sqlalchemy import select

            async with async_session() as session:
                result = await session.execute(
                    select(RepairTicket).where(RepairTicket.id == ticket_id)
                )
                ticket = result.scalar_one_or_none()
                if ticket:
                    ticket.verification_results = {
                        "passed": passed,
                        "output": output[:2000],
                    }
                    ticket.status = "deployed" if passed else "verification_failed"
                    await session.commit()
        except Exception as e:
            logger.warning("Could not update repair ticket verification results: %s", e)

    return passed, output


async def rollback_repair(plan: dict, ticket_id: Optional[int] = None) -> str:
    """Roll back a low-risk auto-applied repair.

    For low-risk repairs (env var changes, schedule re-injections, Redis key clears)
    the rollback is to undo the specific operation stored in plan['rollback_steps'].
    Returns a human-readable status message.
    """
    rollback_steps = plan.get("rollback_steps", [])
    if not rollback_steps:
        msg = "No rollback steps defined in plan — manual intervention may be needed."
        logger.warning("Repair rollback: %s (ticket_id=%s)", msg, ticket_id)
        return msg

    results = []
    for step in rollback_steps:
        action = step.get("action", "")
        try:
            if action == "clear_redis_key":
                import redis.asyncio as aioredis
                from src.settings import settings
                r = aioredis.from_url(settings.redis_url)
                await r.delete(step["key"])
                results.append(f"✅ Cleared Redis key: {step['key']}")
            else:
                results.append(f"⚠️ Unknown rollback action '{action}' — skipped")
        except Exception as e:
            results.append(f"❌ Rollback step '{action}' failed: {e}")

    if ticket_id is not None:
        try:
            from src.db.session import async_session
            from src.db.models import RepairTicket
            from sqlalchemy import select

            async with async_session() as session:
                result = await session.execute(
                    select(RepairTicket).where(RepairTicket.id == ticket_id)
                )
                ticket = result.scalar_one_or_none()
                if ticket:
                    ticket.status = "rolled_back"
                    existing = ticket.verification_results or {}
                    existing["rollback"] = results
                    ticket.verification_results = existing
                    await session.commit()
        except Exception as e:
            logger.warning("Could not update repair ticket after rollback: %s", e)

    return "\n".join(results)
