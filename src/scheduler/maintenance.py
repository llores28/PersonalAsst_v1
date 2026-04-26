"""System-level maintenance jobs registered on app startup.

Currently:
- `nightly_memory_eviction` — caps per-user Mem0 memory count to prevent
  unbounded vector storage and embedding-API cost growth.

Design notes (per the APScheduler best-practice research):
- ONE system-level job that iterates all users, NOT per-user job entries.
  Per-user entries scale linearly in the job store and start contending on
  locks at low-thousands of users; the single-iterator pattern keeps the
  scheduler footprint constant. Single-tenant today, but this design makes
  the multi-user transition a body change rather than a topology change.
- Each user's eviction call is wrapped in tenacity retry (3 attempts,
  exponential backoff) AND a per-user try/except so one user's transient
  failure can't abort the rest of the batch.
- The job body itself never raises — it returns a structured report. That
  way APScheduler's `EVENT_JOB_ERROR` listener stays a real signal (the only
  way to fire it is a bug in the iteration code itself).
"""

import logging
from typing import Any, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


# --- Retry config (per-user) ---------------------------------------------
# Limited retry: most "transient" Mem0/Qdrant failures resolve within a
# few seconds (network blips, embedding-API rate limits). Beyond ~30s
# something is genuinely down — fail this user, move on, surface in report.
_USER_RETRY_ATTEMPTS = 3
_USER_RETRY_MIN_SEC = 2.0
_USER_RETRY_MAX_SEC = 30.0


@retry(
    stop=stop_after_attempt(_USER_RETRY_ATTEMPTS),
    wait=wait_exponential(min=_USER_RETRY_MIN_SEC, max=_USER_RETRY_MAX_SEC),
    reraise=True,
)
async def _prune_one_user(telegram_id: int, *, cap: int) -> dict[str, Any]:
    """Prune one user's memories with tenacity-backed retry.

    Imported lazily so the module can be imported in environments that
    don't have Mem0 / Qdrant available (e.g. unit tests that monkeypatch
    `prune_user_memories`).
    """
    from src.memory.eviction_runner import prune_user_memories
    return await prune_user_memories(str(telegram_id), cap=cap)


async def nightly_memory_eviction(
    *,
    cap: int = 8000,
    user_ids: Optional[list[int]] = None,
) -> dict[str, Any]:
    """Iterate all users and evict over-cap memories.

    Per-user failures are isolated — one user's exception cannot stop the
    batch. A structured report is returned (and logged) so observability
    listeners can see exactly what happened.

    Args:
        cap: Per-user memory cap. Defaults to the eviction module's default.
        user_ids: Optional override list of telegram IDs (testing/ad-hoc).
            If None, queries all users from the `users` table.

    Returns:
        {
          "users_processed":  int,
          "users_under_cap":  int,   # no eviction triggered
          "users_evicted":    int,   # eviction completed successfully
          "users_failed":     int,
          "details":          [ {user_id, status, ...}, ... ],
        }
    """
    report: dict[str, Any] = {
        "users_processed": 0,
        "users_under_cap": 0,
        "users_evicted":   0,
        "users_failed":    0,
        "details":         [],
    }

    telegram_ids: list[int]
    if user_ids is not None:
        telegram_ids = list(user_ids)
    else:
        try:
            from sqlalchemy import select
            from src.db.models import User
            from src.db.session import async_session

            async with async_session() as session:
                result = await session.execute(select(User.telegram_id))
                telegram_ids = [row[0] for row in result.all()]
        except Exception as e:
            logger.error("Could not query user list for nightly eviction: %s", e)
            report["error"] = f"user_query_failed: {e}"
            return report

    for tg_id in telegram_ids:
        report["users_processed"] += 1
        try:
            user_report = await _prune_one_user(tg_id, cap=cap)
        except Exception as e:
            logger.error(
                "Nightly eviction failed for user %s after %d retries: %s",
                tg_id, _USER_RETRY_ATTEMPTS, e,
            )
            report["users_failed"] += 1
            report["details"].append({
                "user_id": tg_id, "status": "error", "error": str(e),
            })
            continue

        if user_report.get("reason") == "under_cap":
            report["users_under_cap"] += 1
            report["details"].append({
                "user_id": tg_id, "status": "under_cap",
                "total": user_report.get("total", 0),
            })
        elif user_report.get("evicted", 0) > 0:
            report["users_evicted"] += 1
            report["details"].append({
                "user_id": tg_id, "status": "evicted",
                "total": user_report.get("total"),
                "evicted": user_report.get("evicted"),
                "summaries_added": user_report.get("summaries_added"),
            })
        else:
            # Eviction ran but report had error or zero progress (e.g. summary
            # write failed mid-flight). Reflect that distinctly so observability
            # picks it up without conflating with successful evictions.
            report["users_failed"] += 1
            report["details"].append({
                "user_id": tg_id, "status": "partial_or_error",
                **{k: v for k, v in user_report.items() if k != "details"},
            })

    logger.info(
        "Nightly memory eviction complete: processed=%d under_cap=%d evicted=%d failed=%d",
        report["users_processed"],
        report["users_under_cap"],
        report["users_evicted"],
        report["users_failed"],
    )
    return report


# --------------------------------------------------------------------------
# Weekly OAuth heartbeat
# --------------------------------------------------------------------------
#
# Why this exists:
#   Google revokes refresh tokens that go unused for 6 months and silently
#   evicts the oldest token when a user crosses the per-client 100-token cap.
#   Without a periodic heartbeat, idle Atlas users would lose Workspace
#   access without warning. Even active users can hit the cap if they
#   re-authorize from multiple devices.
#
# How it works:
#   For each user with a Redis-tracked Google email, we invoke a cheap
#   workspace-mcp tool (`get_user_profile`). The sidecar's auto-refresh path
#   exercises the refresh-token exchange end-to-end — that single call
#   resets Google's idle clock AND validates the access token. Failures are
#   classified using the patterns in the OAuth heartbeat research note
#   (see PR description / design doc) into:
#     - "ok"            : normal success
#     - "auth_failed"   : access lost (idle expiry, password change, revoke,
#                          or 100-cap eviction). User must re-consent.
#     - "transient"     : 5xx / network — try again next week.
#
# Sources:
# - https://developers.google.com/identity/protocols/oauth2
# - https://nango.dev/blog/google-oauth-invalid-grant-token-has-been-expired-or-revoked/

_GOOGLE_EMAIL_KEY_PREFIX = "google_email:"
_HEARTBEAT_TOOL = "get_user_profile"


def _classify_workspace_response(text: str) -> str:
    """Classify a workspace_mcp tool result string.

    The MCP wrapper returns bracketed error tags ([AUTH ERROR], [RATE LIMIT],
    [CONNECTION ERROR], [TOOL ERROR]) on failure, otherwise plain content.
    """
    if not text:
        return "transient"
    upper = text.upper()
    if "[AUTH ERROR]" in upper:
        return "auth_failed"
    if "[RATE LIMIT]" in upper or "[CONNECTION ERROR]" in upper:
        return "transient"
    if "[TOOL ERROR]" in upper:
        # Generic tool error — not auth, but not a clear success either.
        # Treat as transient to avoid noisy reauth prompts on flaky MCP runs.
        return "transient"
    return "ok"


async def weekly_oauth_heartbeat(*, user_ids: Optional[list[int]] = None) -> dict[str, Any]:
    """Exercise each connected user's Google OAuth refresh path.

    Resets Google's 6-month idle revocation timer by triggering the
    workspace-mcp sidecar to refresh + use the user's access token. Catches
    scope-revoke and 100-token-cap eviction by reading the response status
    rather than only the HTTP code.

    Per-user failures are isolated (try/except + tenacity inside `_heartbeat_one_user`).

    Args:
        user_ids: Optional override list of telegram IDs (testing/ad-hoc).
            If None, scans Redis for all `google_email:{user_id}` keys.

    Returns:
        {
          "users_checked":  int,
          "users_ok":       int,
          "users_auth_failed": int,   # need re-consent
          "users_transient":   int,   # try next week
          "users_nudged":      int,   # Telegram re-consent prompt sent
          "details":           [ {user_id, status, message?, nudge_sent?}, ... ],
        }
    """
    report: dict[str, Any] = {
        "users_checked": 0,
        "users_ok": 0,
        "users_auth_failed": 0,
        "users_transient": 0,
        "users_nudged": 0,
        "details": [],
    }

    telegram_ids: list[int]
    if user_ids is not None:
        telegram_ids = list(user_ids)
    else:
        try:
            telegram_ids = await _scan_connected_google_users()
        except Exception as e:
            logger.error("OAuth heartbeat: could not scan connected users: %s", e)
            report["error"] = f"user_scan_failed: {e}"
            return report

    for tg_id in telegram_ids:
        report["users_checked"] += 1
        try:
            from src.integrations.workspace_mcp import call_workspace_tool
            result_text = await call_workspace_tool(_HEARTBEAT_TOOL, {})
        except Exception as e:
            # The wrapper is supposed to return strings, never raise. If it
            # does, log it as a transient and continue — never abort batch.
            logger.warning("OAuth heartbeat for user %s raised: %s", tg_id, e)
            report["users_transient"] += 1
            report["details"].append(
                {"user_id": tg_id, "status": "transient", "error": str(e)}
            )
            continue

        status = _classify_workspace_response(result_text)
        if status == "ok":
            report["users_ok"] += 1
            report["details"].append({"user_id": tg_id, "status": "ok"})
        elif status == "auth_failed":
            report["users_auth_failed"] += 1
            logger.warning(
                "OAuth heartbeat: user %s needs re-consent (token revoked or expired)",
                tg_id,
            )
            nudge_sent = await _send_reauth_nudge(tg_id)
            if nudge_sent:
                report["users_nudged"] += 1
            report["details"].append({
                "user_id": tg_id, "status": "auth_failed",
                "message": result_text[:200],
                "nudge_sent": nudge_sent,
            })
        else:  # transient
            report["users_transient"] += 1
            report["details"].append({
                "user_id": tg_id, "status": "transient",
                "message": result_text[:200],
            })

    logger.info(
        "Weekly OAuth heartbeat complete: checked=%d ok=%d auth_failed=%d transient=%d nudged=%d",
        report["users_checked"],
        report["users_ok"],
        report["users_auth_failed"],
        report["users_transient"],
        report["users_nudged"],
    )
    return report


async def _send_reauth_nudge(telegram_id: int) -> bool:
    """Look up the user's connected Google email (best-effort) and send a
    Telegram re-consent nudge. Never raises — returns False on any failure
    so the heartbeat batch isn't aborted by a flaky bot session."""
    email: Optional[str] = None
    try:
        from src.memory.conversation import get_redis
        redis = await get_redis()
        raw = await redis.get(f"{_GOOGLE_EMAIL_KEY_PREFIX}{telegram_id}")
        if raw:
            email = raw if isinstance(raw, str) else raw.decode("utf-8", errors="ignore")
    except Exception as exc:
        logger.debug("Could not fetch email for reauth nudge user %s: %s", telegram_id, exc)

    try:
        from src.bot.notifications import notify_oauth_reauth_required
        return await notify_oauth_reauth_required(telegram_id, email=email)
    except Exception as exc:
        logger.warning("OAuth reauth nudge for user %s raised: %s", telegram_id, exc)
        return False


async def _scan_connected_google_users() -> list[int]:
    """Return the list of telegram IDs that have a `google_email:{user_id}`
    key in Redis (i.e. ran `/connect google` successfully)."""
    from src.memory.conversation import get_redis

    redis = await get_redis()
    user_ids: list[int] = []
    pattern = f"{_GOOGLE_EMAIL_KEY_PREFIX}*"
    async for key in redis.scan_iter(match=pattern, count=100):
        # Keys are returned as str when decode_responses=True; coerce defensively.
        key_str = key if isinstance(key, str) else key.decode("utf-8", errors="ignore")
        suffix = key_str[len(_GOOGLE_EMAIL_KEY_PREFIX):]
        try:
            user_ids.append(int(suffix))
        except ValueError:
            continue
    return user_ids
