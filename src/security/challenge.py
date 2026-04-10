"""Security challenge gate for destructive repair actions.

Flow:
1. ``issue_challenge(user_id)`` — picks PIN or random security Q,
   stores pending challenge in Redis with TTL, returns prompt text.
2. ``verify_challenge(user_id, answer)`` — checks answer against
   pending challenge.  Clears on success or expiry.

The owner must configure their PIN or security Q&A via
``/settings security`` before the repair agent can apply patches.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Optional

import redis.asyncio as aioredis

from src.settings import settings

logger = logging.getLogger(__name__)

_CHALLENGE_KEY_PREFIX = "repair_challenge:"
_DEFAULT_TTL = 60  # seconds


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def _hash_answer(answer: str) -> str:
    """Deterministic lowercase-stripped SHA-256 for comparison."""
    return hashlib.sha256(answer.strip().lower().encode()).hexdigest()


# ── Issue ──────────────────────────────────────────────────────────────

async def issue_challenge(
    user_id: int,
    pin_hash: Optional[str] = None,
    security_qa: Optional[list[dict]] = None,
    ttl: int = _DEFAULT_TTL,
) -> dict:
    """Create a pending security challenge and return it.

    Returns a dict with keys:
        - ``type``: ``"pin"`` or ``"security_question"``
        - ``prompt``: human-readable prompt to show the user
        - ``expires_in``: seconds until expiry

    *pin_hash* and *security_qa* come from ``OwnerSecurityConfig``.
    If neither is configured, raises ``ValueError``.
    """
    if not pin_hash and not security_qa:
        raise ValueError(
            "Owner has not configured a security PIN or security questions. "
            "Use /settings security to set one up before approving repairs."
        )

    r = _redis()
    key = f"{_CHALLENGE_KEY_PREFIX}{user_id}"

    if pin_hash:
        challenge = {
            "type": "pin",
            "expected_hash": pin_hash,
            "prompt": (
                "🔐 **Security verification required.**\n"
                "Enter your 4-digit security PIN to authorize this repair action:"
            ),
        }
    else:
        # Pick a random question from the configured set
        qa = secrets.choice(security_qa)  # type: ignore[arg-type]
        challenge = {
            "type": "security_question",
            "question": qa["q"],
            "expected_hash": qa["a_hash"],
            "prompt": (
                "🔐 **Security verification required.**\n"
                f"Answer this question to authorize: **{qa['q']}**"
            ),
        }

    await r.set(key, json.dumps(challenge), ex=ttl)
    await r.aclose()

    return {
        "type": challenge["type"],
        "prompt": challenge["prompt"],
        "expires_in": ttl,
    }


# ── Verify ─────────────────────────────────────────────────────────────

async def verify_challenge(user_id: int, answer: str) -> bool:
    """Check *answer* against the pending challenge.

    Returns ``True`` on success (and clears the challenge).
    Returns ``False`` on wrong answer or expired/missing challenge.
    """
    r = _redis()
    key = f"{_CHALLENGE_KEY_PREFIX}{user_id}"

    raw = await r.get(key)
    if raw is None:
        logger.warning("No pending challenge for user %s (expired?)", user_id)
        await r.aclose()
        return False

    challenge = json.loads(raw)
    expected = challenge["expected_hash"]
    actual = _hash_answer(answer)

    if actual == expected:
        await r.delete(key)
        await r.aclose()
        logger.info("Security challenge passed for user %s", user_id)
        return True

    logger.warning("Security challenge FAILED for user %s", user_id)
    await r.aclose()
    return False


# ── Helpers for setting up owner security config ───────────────────────

def hash_pin(pin: str) -> str:
    """Hash a PIN for storage in ``OwnerSecurityConfig.pin_hash``."""
    return _hash_answer(pin)


def hash_security_answer(answer: str) -> str:
    """Hash a security answer for storage in ``OwnerSecurityConfig.security_qa``."""
    return _hash_answer(answer)


async def has_pending_challenge(user_id: int) -> bool:
    """Check if there is an active pending challenge for a user."""
    r = _redis()
    key = f"{_CHALLENGE_KEY_PREFIX}{user_id}"
    exists = await r.exists(key)
    await r.aclose()
    return bool(exists)
