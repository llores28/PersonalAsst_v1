"""Email Agent — manages Gmail via Google Workspace MCP (as_tool per AD-3)."""

import logging
import re

from agents import Agent, function_tool

from src.integrations.workspace_mcp import call_workspace_tool
from src.agents.persona_mode import PersonaMode, build_persona_mode_addendum
from src.models.router import ModelRole, select_model

logger = logging.getLogger(__name__)


def _normalize_gmail_subject(subject: str | None, body: str) -> str:
    normalized_subject = " ".join((subject or "").split()).strip()
    if normalized_subject:
        return normalized_subject

    normalized_body = " ".join(body.split()).strip()
    if not normalized_body:
        return "Quick update"

    first_clause = re.split(r"[.!?]\s|\n", normalized_body, maxsplit=1)[0].strip(" ,;:-")
    if not first_clause:
        return "Quick update"

    words = first_clause.split()
    candidate = " ".join(words[:8]).strip(" ,;:-")
    if not candidate:
        return "Quick update"
    if len(words) > 8:
        candidate += "..."
    return candidate[0].upper() + candidate[1:]

EMAIL_INSTRUCTIONS = """\
You are an email management specialist. You help the user manage their Gmail.

## Capabilities
- Read and summarize emails (inbox, unread, by sender, by subject)
- Search emails by keyword, sender, date range, or label
- Draft and send emails (ALWAYS show draft to user before sending)
- Reply to and forward emails
- Manage labels and organize messages

## Rules
- When asked to send an email, ALWAYS draft it first and ask for confirmation.
- Summarize emails concisely — subject, sender, and 1-2 line summary.
- For unread email checks, prioritize by importance (flagged, from known contacts).
- Never forward emails without explicit user request.
- If you encounter an auth error, tell the user to run /connect google.

## Output Format
- Use markdown formatting for readability.
- For email lists, use numbered lists with sender and subject.
- For single email details, show subject, from, date, and body summary.
"""


def _build_connected_gmail_tools(connected_google_email: str) -> list:
    @function_tool(name_override="search_connected_gmail_messages")
    async def search_connected_gmail_messages(
        query: str,
        page_size: int = 10,
        page_token: str | None = None,
    ) -> str:
        return await call_workspace_tool(
            "search_gmail_messages",
            {
                "query": query,
                "user_google_email": connected_google_email,
                "page_size": page_size,
                "page_token": page_token,
            },
        )

    @function_tool(name_override="get_connected_gmail_message_content")
    async def get_connected_gmail_message_content(message_id: str) -> str:
        return await call_workspace_tool(
            "get_gmail_message_content",
            {
                "message_id": message_id,
                "user_google_email": connected_google_email,
            },
        )

    @function_tool(name_override="get_connected_gmail_thread_content")
    async def get_connected_gmail_thread_content(thread_id: str) -> str:
        return await call_workspace_tool(
            "get_gmail_thread_content",
            {
                "thread_id": thread_id,
                "user_google_email": connected_google_email,
            },
        )

    @function_tool(name_override="send_connected_gmail_message")
    async def send_connected_gmail_message(
        to: str,
        body: str,
        subject: str | None = None,
        cc: str | None = None,
        bcc: str | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> str:
        """Send an email via the connected Gmail account.

        Args:
            to: Recipient email address.
            body: Email body content (plain text).
            subject: Email subject (auto-generated from body if omitted).
            cc: Optional CC email address.
            bcc: Optional BCC email address.
            thread_id: Optional Gmail thread ID to reply within an existing thread.
            in_reply_to: Optional RFC Message-ID of the message being replied to.
        """
        resolved_subject = _normalize_gmail_subject(subject, body)
        args = {
            "user_google_email": connected_google_email,
            "to": to,
            "subject": resolved_subject,
            "body": body,
            "cc": cc,
            "bcc": bcc,
            "thread_id": thread_id,
            "in_reply_to": in_reply_to,
        }
        return await call_workspace_tool(
            "send_gmail_message",
            {k: v for k, v in args.items() if v is not None},
        )

    @function_tool(name_override="draft_connected_gmail_message")
    async def draft_connected_gmail_message(
        to: str,
        body: str,
        subject: str | None = None,
        cc: str | None = None,
        bcc: str | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> str:
        """Create a draft email in the connected Gmail account.

        Args:
            to: Recipient email address.
            body: Email body content (plain text).
            subject: Email subject (auto-generated from body if omitted).
            cc: Optional CC email address.
            bcc: Optional BCC email address.
            thread_id: Optional Gmail thread ID to reply within an existing thread.
            in_reply_to: Optional RFC Message-ID of the message being replied to.
        """
        resolved_subject = _normalize_gmail_subject(subject, body)
        args = {
            "user_google_email": connected_google_email,
            "to": to,
            "subject": resolved_subject,
            "body": body,
            "cc": cc,
            "bcc": bcc,
            "thread_id": thread_id,
            "in_reply_to": in_reply_to,
        }
        return await call_workspace_tool(
            "draft_gmail_message",
            {k: v for k, v in args.items() if v is not None},
        )

    @function_tool(name_override="get_connected_gmail_messages_batch")
    async def get_connected_gmail_messages_batch(
        message_ids: list[str],
        format: str = "full",
    ) -> str:
        """Get content of multiple Gmail messages at once."""
        return await call_workspace_tool(
            "get_gmail_messages_content_batch",
            {
                "user_google_email": connected_google_email,
                "message_ids": message_ids,
                "format": format,
            },
        )

    return [
        search_connected_gmail_messages,
        get_connected_gmail_message_content,
        get_connected_gmail_thread_content,
        send_connected_gmail_message,
        draft_connected_gmail_message,
        get_connected_gmail_messages_batch,
    ]


def create_email_agent(
    mcp_servers: list = None,
    connected_google_email: str | None = None,
    mode: PersonaMode = "workspace",
) -> Agent:
    """Create the email specialist agent."""
    instructions = f"{EMAIL_INSTRUCTIONS}\n\n{build_persona_mode_addendum(mode)}"
    tools = []
    if connected_google_email:
        instructions = (
            f"{instructions}\n\n"
            f"## Connected Google Account\n"
            f"The user's connected Google email is `{connected_google_email}`. "
            f"For inbox, unread, search, message content, or thread content requests, prefer the connected-account tools `search_connected_gmail_messages`, `get_connected_gmail_message_content`, and `get_connected_gmail_thread_content`. "
            f"When calling other Google Workspace tools directly, pass `user_google_email` as `{connected_google_email}` unless the user explicitly asks for a different connected Google account."
        )
        tools = _build_connected_gmail_tools(connected_google_email)

    # When connected tools are available, they use call_workspace_tool() which
    # manages its own MCP connection per call. Passing the shared mcp_servers
    # instance to sub-agents causes lifecycle conflicts when used via as_tool().
    effective_mcp = [] if connected_google_email else (mcp_servers or [])

    selection = select_model(ModelRole.GENERAL)
    return Agent(
        name="EmailAgent",
        instructions=instructions,
        model=selection.model_id,
        tools=tools,
        mcp_servers=effective_mcp,
    )
