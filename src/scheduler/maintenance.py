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


# ── Repair-queue worker ───────────────────────────────────────────────────
# Postgres-as-queue with FOR UPDATE SKIP LOCKED is the canonical 2025 pattern
# for ticket processors (see Procrastinate, PgQueuer, Neon's queue-system
# guide). The repair_tickets table is already an outbox: rows are committed
# atomically with the domain change that triggered them, so we just need a
# claimer that picks them up and advances them through the pipeline.
#
# Safety policy:
#   - Only `auto_applied=True` tickets are advanced autonomously. Anything
#     `approval_required=True` waits for a human click in the dashboard
#     (matches the human-in-the-loop guardrail from the self-healing-pipeline
#     research — agent proposes, human can veto, rollback automatic).
#   - Per-tick we claim AT MOST one ticket (capacity 1). The single-user
#     deployment doesn't need parallelism and one-at-a-time keeps cost
#     predictable when the pipeline calls OpenAI mid-stage.
#   - Hard cap on attempts via the `_PIPELINE_ATTEMPT_COUNTS` guard in
#     run_self_healing_pipeline — this worker does not need its own retry
#     limiter.

_REPAIR_QUEUE_BATCH = 1
_REPAIR_QUEUE_CLAIMABLE_STATUSES = ("open", "debug_analysis_ready")


async def process_repair_queue() -> dict[str, Any]:
    """Pull one auto-applicable repair ticket and advance it through the
    self-healing pipeline. Registered as an interval job from
    src/scheduler/engine.py:start_scheduler.

    Uses ``FOR UPDATE SKIP LOCKED`` so multiple workers (or overlapping
    ticks if a long pipeline run blows past the interval) can't double-
    claim the same ticket.

    Returns a structured report — never raises (matches the pattern of the
    other maintenance jobs so APScheduler's error listener stays a real
    signal).
    """
    from sqlalchemy import text
    from src.db.session import async_session
    from src.repair.engine import run_self_healing_pipeline

    claimed: list[int] = []
    advanced: list[dict] = []
    errors: list[dict] = []

    try:
        async with async_session() as session:
            # Atomic claim: SELECT ... FOR UPDATE SKIP LOCKED inside the txn,
            # then UPDATE status='processing' so a second worker (or a re-
            # entry from the same worker on a long tick) can't pick it up.
            #
            # Filter rules:
            #   - status in (open, debug_analysis_ready) — early stages where
            #     the system can drive the pipeline forward.
            #   - auto_applied=True — opt-in autonomous handling. Anything
            #     else is human-driven via the dashboard "Approve" button.
            placeholders = ",".join(f":st{i}" for i in range(len(_REPAIR_QUEUE_CLAIMABLE_STATUSES)))
            params: dict[str, Any] = {f"st{i}": s for i, s in enumerate(_REPAIR_QUEUE_CLAIMABLE_STATUSES)}
            params["batch"] = _REPAIR_QUEUE_BATCH

            result = await session.execute(
                text(
                    f"""
                    WITH cte AS (
                      SELECT id
                        FROM repair_tickets
                       WHERE status IN ({placeholders})
                         AND auto_applied = TRUE
                       ORDER BY created_at
                       LIMIT :batch
                       FOR UPDATE SKIP LOCKED
                    )
                    UPDATE repair_tickets t
                       SET status = 'processing',
                           updated_at = now()
                      FROM cte
                     WHERE t.id = cte.id
                 RETURNING t.id, t.title, t.error_context
                    """
                ),
                params,
            )
            rows = result.mappings().all()
            await session.commit()

            for row in rows:
                claimed.append(row["id"])

        if not claimed:
            logger.debug("Repair queue: nothing claimable")
            return {"claimed": 0, "advanced": 0, "errors": 0}

        # Drive the pipeline outside the claim txn — it's long-running
        # (multi-agent + sandbox) and we don't want to hold a row lock for
        # minutes. The 'processing' status acts as the lease.
        for tid in claimed:
            try:
                async with async_session() as session:
                    detail_row = await session.execute(
                        text(
                            "SELECT user_id, title, error_context "
                            "FROM repair_tickets WHERE id = :id"
                        ),
                        {"id": tid},
                    )
                    detail = detail_row.mappings().one_or_none()
                if detail is None:
                    errors.append({"ticket_id": tid, "error": "row vanished after claim"})
                    continue

                # The pipeline owns its own ticket creation today, so we
                # pass the original error context through. Hardening the
                # pipeline to RESUME an existing ticket is the natural
                # follow-up; for now this re-trigger pattern is acceptable
                # because attempt-count guards prevent runaway loops.
                outcome = await run_self_healing_pipeline(
                    user_telegram_id=int(detail["user_id"]) if detail["user_id"] else 0,
                    error_description=detail["title"] or "(no title)",
                    error_context=detail["error_context"],
                    source="scheduler",
                )
                advanced.append({
                    "ticket_id": tid,
                    "stage_reached": outcome.get("stage_reached"),
                    "decision": outcome.get("decision"),
                })
            except Exception as exc:
                logger.exception("Repair queue: ticket %s pipeline raised", tid)
                errors.append({"ticket_id": tid, "error": str(exc)[:300]})
                # Release the lease — flip back to 'open' so a future tick
                # can retry (subject to the pipeline's attempt-count cap).
                try:
                    async with async_session() as session:
                        await session.execute(
                            text(
                                "UPDATE repair_tickets SET status = 'open', updated_at = now() "
                                "WHERE id = :id AND status = 'processing'"
                            ),
                            {"id": tid},
                        )
                        await session.commit()
                except Exception:
                    logger.exception("Repair queue: could not release lease on ticket %s", tid)

    except Exception as exc:
        logger.exception("Repair queue: claim phase failed")
        return {"claimed": len(claimed), "advanced": len(advanced), "errors": 1, "fatal": str(exc)[:300]}

    summary = {
        "claimed": len(claimed),
        "advanced": len(advanced),
        "errors": len(errors),
        "advanced_tickets": advanced,
        "error_details": errors,
    }
    if claimed:
        logger.info("Repair queue tick: claimed=%d advanced=%d errors=%d",
                    len(claimed), len(advanced), len(errors))
    return summary


# ── Background-job startup recovery ───────────────────────────────────────
# APScheduler's PostgreSQL data store persists schedules across restarts —
# but only schedules that were successfully ADDED. The 2026-04-23 bug
# (TypeError on `job_args=`) meant rows landed in `background_jobs` with
# `status='running'` while their APScheduler schedule never existed. After
# the fix lands, those rows still need to be re-registered or the user's
# pre-existing background jobs stay orphaned.
#
# This is also a defensive guard: if APScheduler's data store is ever wiped
# (e.g., a bad migration or a manual DELETE), running BackgroundJob rows
# get re-attached to the scheduler instead of silently dying.

async def restore_running_background_jobs() -> dict[str, Any]:
    """Re-register APScheduler schedules for any BackgroundJob row in
    `running` status that doesn't have a live schedule. Idempotent —
    skips jobs that are already attached.

    Called once from start_scheduler before the background loop kicks off
    so the recovery runs to completion before normal ticking begins.
    """
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import BackgroundJob
    from src.scheduler.engine import get_scheduler, add_interval_job

    restored: list[int] = []
    skipped: list[int] = []
    errors: list[dict] = []

    try:
        scheduler = await get_scheduler()
        existing = {s.id for s in await scheduler.get_schedules()}

        async with async_session() as session:
            rows = (await session.execute(
                select(BackgroundJob).where(BackgroundJob.status == "running")
            )).scalars().all()

            # Find user_id → telegram_id once, in batch
            from src.db.models import User
            user_ids = {r.user_id for r in rows if r.user_id is not None}
            tg_lookup: dict[int, int] = {}
            if user_ids:
                from sqlalchemy import select as _sel
                u_rows = (await session.execute(
                    _sel(User.id, User.telegram_id).where(User.id.in_(user_ids))
                )).all()
                tg_lookup = {uid: tid for uid, tid in u_rows}

        for row in rows:
            if row.apscheduler_id and row.apscheduler_id in existing:
                skipped.append(row.id)
                continue
            tg_id = tg_lookup.get(row.user_id) if row.user_id else None
            if not tg_id:
                errors.append({"job_id": row.id, "error": "no telegram_id mapping"})
                continue
            try:
                await add_interval_job(
                    func_path="src.agents.background_job:_tick_background_job_sync",
                    job_id=row.apscheduler_id or f"bg_job_{tg_id}_{row.id}",
                    seconds=row.check_interval_seconds,
                    kwargs={"job_id": row.id, "user_telegram_id": tg_id},
                )
                restored.append(row.id)
            except Exception as exc:
                logger.warning("Could not restore BackgroundJob %d: %s", row.id, exc)
                errors.append({"job_id": row.id, "error": str(exc)[:200]})

    except Exception as exc:
        logger.exception("BackgroundJob recovery: fatal during scan")
        return {"restored": 0, "skipped": 0, "errors": 1, "fatal": str(exc)[:300]}

    if restored or errors:
        logger.info("BackgroundJob recovery: restored=%d skipped=%d errors=%d",
                    len(restored), len(skipped), len(errors))
    return {
        "restored": len(restored),
        "skipped": len(skipped),
        "errors": len(errors),
        "restored_jobs": restored,
        "error_details": errors,
    }
    return user_ids
