"""Email notifications for the repair pipeline.

Sends structured emails to the owner when:
- A repair ticket is created
- A fix is ready for deploy approval

Uses the connected Gmail workspace tool so no extra credentials are needed.
All functions are fire-and-forget safe — errors are swallowed with warning logs.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

REPAIR_NOTIFICATION_EMAIL = "lannys.lores@gmail.com"


async def _send_via_workspace(subject: str, body: str) -> bool:
    """Send an email via the connected Gmail workspace tool.

    Returns True on success, False on failure.
    """
    try:
        from src.integrations.workspace_mcp import call_workspace_tool
        result = await call_workspace_tool(
            "send_gmail_message",
            {
                "to": REPAIR_NOTIFICATION_EMAIL,
                "subject": subject,
                "body": body,
            },
        )
        return bool(result and "[ERROR]" not in str(result) and "[CONNECTION ERROR]" not in str(result))
    except Exception as exc:
        logger.warning("Repair email send failed via workspace: %s", exc)
        return False


async def send_ticket_created_email(
    ticket_id: int,
    title: str,
    status: str,
    error_summary: str,
    affected_files: list[str],
    confidence: float = 0.0,
    source: str = "telegram",
) -> None:
    """Email the owner when a repair ticket is created.

    Args:
        ticket_id: Newly created ticket ID.
        title: Ticket title.
        status: Initial status (open / debug_analysis_ready).
        error_summary: Short description of the detected error.
        affected_files: Files identified as needing changes.
        confidence: Debugger confidence score (0.0–1.0).
        source: Origin of the error (telegram / scheduler / dashboard).
    """
    files_str = "\n".join(f"  - {f}" for f in affected_files) or "  (not yet identified)"
    conf_pct = f"{confidence:.0%}" if confidence > 0 else "pending analysis"

    subject = f"[Atlas Repair] Ticket #{ticket_id} — {title[:80]}"
    body = f"""\
Atlas Repair Ticket Created
============================

Ticket #: {ticket_id}
Title:    {title}
Status:   {status}
Source:   {source}
Confidence: {conf_pct}

Error Summary
-------------
{error_summary}

Affected Files
--------------
{files_str}

Next Steps
----------
- Say "fix it" in Telegram to start the full repair pipeline, OR
- Use /tickets to see all open tickets and their status.
- Use /ticket approve {ticket_id} to deploy once a fix is ready.

This email was sent automatically by Atlas.
"""
    success = await _send_via_workspace(subject, body)
    if success:
        logger.info("Sent ticket-created email for ticket #%s", ticket_id)
    else:
        logger.warning("Failed to send ticket-created email for ticket #%s", ticket_id)


async def send_fix_ready_email(
    ticket_id: int,
    title: str,
    affected_files: list[str],
    branch_name: str = "",
    verification_summary: str = "",
) -> None:
    """Email the owner when a fix passes sandbox and is ready to deploy.

    Args:
        ticket_id: Repair ticket ID.
        title: Fix description.
        affected_files: Files modified by the patch.
        branch_name: Git branch the fix lives on.
        verification_summary: Output from verification commands.
    """
    files_str = "\n".join(f"  - {f}" for f in affected_files) or "  (unknown)"
    branch_info = f"\nGit Branch: {branch_name}" if branch_name else ""
    verify_block = (
        f"\nVerification Output\n-------------------\n{verification_summary[:1000]}\n"
        if verification_summary else ""
    )

    subject = f"[Atlas Repair] Fix Ready — Ticket #{ticket_id} — {title[:60]}"
    body = f"""\
Atlas Repair Fix Ready for Deployment
======================================

Ticket #: {ticket_id}
Title:    {title}{branch_info}
Status:   ready_for_deploy

All sandbox verification tests PASSED. The fix is ready to deploy.

Affected Files
--------------
{files_str}
{verify_block}
How to Deploy
-------------
Option 1 — Telegram: reply to the "Apply fix now?" message with the button.
Option 2 — Command:  /ticket approve {ticket_id}

The fix will NOT be applied until you explicitly approve it.

This email was sent automatically by Atlas.
"""
    success = await _send_via_workspace(subject, body)
    if success:
        logger.info("Sent fix-ready email for ticket #%s", ticket_id)
    else:
        logger.warning("Failed to send fix-ready email for ticket #%s", ticket_id)
