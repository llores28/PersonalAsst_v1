"""Browser-use failure handling: classification, targeted retry, and
auto-RepairTicket on unhandled failures.

Three pieces:

1. **`classify_failure`** — examines an exception + the agent's history
   for sentinel signals and returns a `FailureKind`. Three buckets we
   actually do something different for:
     - LOGIN_EXPIRED  → page redirected to a login screen / session
                        cookie invalidated. Re-seed the profile.
     - ELEMENT_DRIFT  → "element not found", "selector did not match",
                        "frame detached". Page DOM changed. Retry once
                        with `use_vision=True` so the model can recover
                        via visual grounding.
     - TIMEOUT        → step exceeded `step_timeout`. Retry once with a
                        2x extended step_timeout in case the page is
                        just slow.
     - UNKNOWN        → anything else. No retry; escalate.

   This matches the `Self-Healing Agent Pattern` research (classify the
   failure type, give each one a different recovery strategy — don't
   retry blindly).

2. **`open_repair_ticket_for_browser_failure`** — when a browser run
   exhausts its retry plan, write a `RepairTicket` row capturing the
   action history + last screenshot path + last URL so the existing
   `process_repair_queue` worker / DebuggerAgent can analyze it. Status
   is `open` and `auto_applied=False` so the human-in-the-loop guardrail
   from the prior commit holds — a browser failure does NOT trigger an
   autonomous patch deploy.

3. **`build_step_audit_hook`** — returns a callable that browser-use can
   register as `on_step_end=` (or its closest equivalent). Each fired
   step writes one `audit_log` row, giving the dashboard a live tail of
   browser progress without us having to invent new infra.
"""
from __future__ import annotations

import enum
import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class FailureKind(str, enum.Enum):
    LOGIN_EXPIRED = "login_expired"
    ELEMENT_DRIFT = "element_drift"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


# Substrings that, if found in either the exception message OR the agent
# history's text payloads, classify the failure. Lowercase comparisons.
_LOGIN_SENTINELS = (
    "login required",
    "please sign in",
    "please log in",
    "your session has expired",
    "session expired",
    "authentication required",
    "redirecting to /login",
    "redirecting to login",
    "401 unauthorized",
    "sign in to continue",
)
_ELEMENT_SENTINELS = (
    "element not found",
    "no element matches",
    "selector did not match",
    "no such element",
    "frame detached",
    "node is detached",
    "element not visible",
    "element is not attached to the dom",
    "could not find element",
)
_TIMEOUT_SENTINELS = (
    "timeout",
    "timed out",
    "exceeded step_timeout",
    "exceeded llm_timeout",
    "deadline exceeded",
)


def _history_text_blob(history: Any) -> str:
    """Best-effort extraction of all text the agent saw/produced. We
    prefer dedicated methods (urls/action_history/model_thoughts) but
    fall back to str() for older browser-use releases that don't have
    them yet."""
    parts: list[str] = []
    for attr in ("urls", "action_history", "action_names", "model_thoughts", "extracted_content"):
        if not hasattr(history, attr):
            continue
        try:
            value = getattr(history, attr)
            if callable(value):
                value = value()
            parts.append(str(value)[:5000])
        except Exception:
            continue
    if not parts:
        try:
            parts.append(str(history)[:8000])
        except Exception:
            pass
    return "\n".join(parts).lower()


def classify_failure(exc: Optional[BaseException], history: Any = None) -> FailureKind:
    """Decide which failure bucket this run fell into.

    Args:
        exc: The exception that ended the run, or None if the agent
            simply returned a non-success result.
        history: The browser-use AgentHistoryList (or anything stringy).

    Returns:
        A `FailureKind`. The runner uses it to pick a retry strategy.
    """
    blob = (str(exc) if exc else "").lower() + "\n" + _history_text_blob(history)

    # Order matters: a timeout that lands on a login screen should
    # classify as LOGIN_EXPIRED (re-seed) rather than TIMEOUT (retry
    # with longer window) — the timeout is downstream of the redirect.
    if any(s in blob for s in _LOGIN_SENTINELS):
        return FailureKind.LOGIN_EXPIRED
    if any(s in blob for s in _ELEMENT_SENTINELS):
        return FailureKind.ELEMENT_DRIFT
    if any(s in blob for s in _TIMEOUT_SENTINELS):
        return FailureKind.TIMEOUT
    return FailureKind.UNKNOWN


# ─── Auto-RepairTicket on unhandled failure ──────────────────────────────


async def open_repair_ticket_for_browser_failure(
    *,
    user_telegram_id: int,
    task: str,
    failure_kind: FailureKind,
    exc: Optional[BaseException],
    history: Any,
) -> Optional[int]:
    """Persist a RepairTicket so the existing repair pipeline can pick
    up the browser failure.

    Status is `open`; `auto_applied=False`; risk_level `medium`. The
    `process_repair_queue` worker only auto-claims tickets where
    `auto_applied=True`, so a browser failure surfaces in the dashboard
    + creates an audit trail BUT does NOT trigger an autonomous repair
    cycle. That's deliberate: browser failures usually need a human to
    decide whether to re-seed, retry, or give up.

    Args:
        user_telegram_id: Owner ID — resolved to user_id FK.
        task: The original natural-language task the user asked for.
        failure_kind: From `classify_failure`.
        exc: The exception (if any).
        history: AgentHistoryList — captured into error_context.

    Returns:
        The new ticket id, or None if the insert itself failed (logged
        but never raised — the caller is already in an error path).
    """
    try:
        from sqlalchemy import select
        from src.db.session import async_session
        from src.db.models import RepairTicket, User

        snapshot: dict[str, Any] = {
            "task": task[:500],
            "failure_kind": failure_kind.value,
            "exception_type": type(exc).__name__ if exc else None,
            "exception_message": (str(exc)[:500] if exc else None),
        }
        # Best-effort harvest of history fields
        for attr, key in (
            ("urls", "urls"),
            ("action_names", "action_names"),
            ("action_history", "action_history"),
            ("screenshot_paths", "screenshot_paths"),
            ("number_of_steps", "number_of_steps"),
            ("total_duration_seconds", "total_duration_seconds"),
        ):
            if hasattr(history, attr):
                try:
                    value = getattr(history, attr)
                    if callable(value):
                        value = value()
                    # Truncate large lists
                    if isinstance(value, list):
                        value = value[:50]
                    snapshot[key] = value
                except Exception:
                    continue

        async with async_session() as session:
            user_row = await session.execute(
                select(User).where(User.telegram_id == user_telegram_id)
            )
            user = user_row.scalar_one_or_none()
            ticket = RepairTicket(
                user_id=user.id if user else None,
                title=f"[browser] {failure_kind.value}: {task[:80]}",
                source="scheduler" if failure_kind == FailureKind.LOGIN_EXPIRED else "telegram",
                status="open",
                priority="medium" if failure_kind != FailureKind.UNKNOWN else "low",
                risk_level="medium",
                auto_applied=False,
                approval_required=True,
                error_context=snapshot,
            )
            session.add(ticket)
            await session.commit()
            await session.refresh(ticket)
            ticket_id = ticket.id

        logger.info(
            "browser failure → RepairTicket #%d kind=%s task=%r",
            ticket_id, failure_kind.value, task[:80],
        )
        return ticket_id
    except Exception as exc_inner:
        logger.warning(
            "Could not open RepairTicket for browser failure: %s",
            exc_inner,
        )
        return None


# ─── Step-level audit trail ───────────────────────────────────────────────


def build_step_audit_hook(*, user_telegram_id: int, task: str) -> Callable:
    """Return an async callable browser-use can register as a step hook.

    Writes one `audit_log` row per step with platform=`browser`,
    direction=`outbound`. Gives the dashboard's live tail visibility
    into browser activity without inventing new infra.

    The hook signature matches browser-use's `on_step_end` shape:
    `async def hook(agent: Agent) -> None`. We accept *args, **kwargs
    so any future signature change is tolerated.
    """
    async def _hook(*args, **kwargs) -> None:
        agent = args[0] if args else kwargs.get("agent")
        try:
            # Pull the most recent step's metadata if exposed
            payload: dict[str, Any] = {"task": task[:200]}
            history = getattr(agent, "history", None) or getattr(agent, "state", None)
            if history is not None:
                for attr in ("number_of_steps", "action_names", "urls"):
                    if hasattr(history, attr):
                        try:
                            value = getattr(history, attr)
                            if callable(value):
                                value = value()
                            if isinstance(value, list):
                                value = value[-1] if value else None
                            payload[attr] = value
                        except Exception:
                            continue

            from sqlalchemy import select
            from src.db.session import async_session
            from src.db.models import AuditLog, User
            async with async_session() as session:
                user_row = await session.execute(
                    select(User).where(User.telegram_id == user_telegram_id)
                )
                user = user_row.scalar_one_or_none()
                session.add(AuditLog(
                    user_id=user.id if user else None,
                    direction="outbound",
                    platform="browser",
                    agent_name="browser_use",
                    message_text=f"step: {payload.get('action_names') or '?'}",
                    tools_used=json.loads(json.dumps(payload, default=str)),
                ))
                await session.commit()
        except Exception as exc:
            # Audit-trail failures must never abort the browser run
            logger.debug("step audit hook failed: %s", exc)

    return _hook
