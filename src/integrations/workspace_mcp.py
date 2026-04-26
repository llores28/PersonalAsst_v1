"""Google Workspace MCP client — connects to the workspace-mcp sidecar container."""

import asyncio
import logging
import re
import time
from typing import Any, Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

try:
    from agents.mcp import MCPServerStreamableHttp, MCPServerStreamableHttpParams
except ImportError:
    MCPServerStreamableHttp = None  # type: ignore[assignment,misc]
    MCPServerStreamableHttpParams = None  # type: ignore[assignment,misc]
    logging.getLogger(__name__).warning(
        "agents.mcp.MCPServerStreamableHttp not available — "
        "Google Workspace MCP integration disabled (upgrade openai-agents>=0.13.0)"
    )

from src.memory.conversation import get_redis
from src.settings import settings

logger = logging.getLogger(__name__)

# MCP server URL — points to the workspace-mcp Docker service
WORKSPACE_MCP_URL = settings.workspace_mcp_url
GOOGLE_EMAIL_KEY_PREFIX = "google_email"
_WORKSPACE_TOOL_SCHEMA_CACHE_TTL_SECONDS = 60.0
_workspace_tool_schema_cache: tuple[float, dict[str, dict[str, Any]]] | None = None


def _google_email_key(user_id: int) -> str:
    return f"{GOOGLE_EMAIL_KEY_PREFIX}:{user_id}"


def is_google_configured() -> bool:
    """Check if Google OAuth credentials are configured."""
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def create_workspace_mcp_server():
    """Create the Google Workspace MCP server connection.

    Returns None if Google credentials are not configured or if the
    agents SDK version does not support MCPServerStreamableHttp.
    """
    if MCPServerStreamableHttp is None:
        logger.warning("create_workspace_mcp_server: MCPServerStreamableHttp unavailable — skipping")
        return None

    if not is_google_configured():
        logger.info("Google Workspace not configured — skipping MCP server")
        return None

    return MCPServerStreamableHttp(
        params=MCPServerStreamableHttpParams(url=WORKSPACE_MCP_URL),
        name="google_workspace",
    )


def _extract_authorization_url(auth_message: str) -> Optional[str]:
    match = re.search(r"Authorization URL:\s*(https?://\S+)", auth_message)
    if match:
        return match.group(1).rstrip(")")

    markdown_match = re.search(r"\((https?://[^\s)]+)\)", auth_message)
    if markdown_match:
        return markdown_match.group(1)

    return None


async def store_connected_google_email(user_id: int, user_google_email: str) -> None:
    if not user_google_email:
        return

    redis = await get_redis()
    await redis.set(_google_email_key(user_id), user_google_email.strip().lower())


async def get_connected_google_email(user_id: int) -> Optional[str]:
    redis = await get_redis()
    email = await redis.get(_google_email_key(user_id))
    return email or None


def _tool_result_to_text(result: Any) -> str:
    return "\n".join(
        getattr(item, "text", str(item)) for item in getattr(result, "content", [])
    ) or str(result)


def _extract_tool_input_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None)
    return schema if isinstance(schema, dict) else {}


class _TransientMCPError(Exception):
    """Raised for transient MCP errors that are safe to retry (connection,
    rate-limit, server-side 5xx). Caller `call_workspace_tool` catches the
    final post-retry instance and returns a user-facing error string."""


# Substrings that mark a Google API rate-limit / quota response. Match against
# the lowercased error text or tool result. Conservative on purpose — matching
# benign words like "limit" alone would over-trigger retries.
_RATE_LIMIT_PATTERNS = (
    "429",
    "rate limit",
    "rate-limit",
    "ratelimit",
    "quota exceeded",
    "quotaexceeded",
    "too many requests",
    "userratelimitexceeded",
    "ratelimitexceeded",
)


def _is_rate_limit(text: str) -> bool:
    """True if the text contains a recognized rate-limit / quota signal."""
    lowered = text.lower()
    return any(pat in lowered for pat in _RATE_LIMIT_PATTERNS)


def _parse_retry_after(text: str) -> Optional[float]:
    """Best-effort extraction of a Retry-After value (seconds) from a Google
    error blob. Tenacity's wait strategy doesn't honor this directly, but we
    log it so an operator can see if the server is asking for a longer pause
    than our exponential backoff provides."""
    for pat in (
        r'"retry[_-]?after(?:_?seconds)?"\s*:\s*"?(\d+(?:\.\d+)?)',
        r"retry[- ]?after[: ]+(\d+(?:\.\d+)?)",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


@retry(
    retry=retry_if_exception_type(_TransientMCPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
async def _call_workspace_tool_inner(tool_name: str, arguments: dict[str, Any]) -> str:
    """Tenacity-retried inner. Raises `_TransientMCPError` on retryable conditions
    (connection timeout, rate limit). Other errors are returned as bracketed
    user-facing strings — same as before this refactor."""
    server = create_workspace_mcp_server()
    if server is None:
        raise RuntimeError("Google Workspace is not configured.")

    # Strip None values — MCP servers with additionalProperties: False
    # reject unknown fields, and null values for optional fields are
    # best omitted to let the server use its defaults.
    clean_args = {k: v for k, v in arguments.items() if v is not None}

    try:
        await asyncio.wait_for(server.connect(), timeout=15)
    except asyncio.TimeoutError:
        logger.error("Workspace MCP connection timed out for %s", tool_name)
        await server.cleanup()
        raise _TransientMCPError(f"Connection timeout for {tool_name}")
    except Exception as conn_err:
        logger.error("Workspace MCP connection failed for %s: %s", tool_name, conn_err)
        await server.cleanup()
        return (
            f"[CONNECTION ERROR] Could not connect to the Google Workspace service "
            f"while calling {tool_name}. The workspace-mcp sidecar may be down or "
            f"restarting. Tell the user to try again in a moment. "
            f"Do NOT use WebSearch as a fallback — the user's private data is not "
            f"available via public web search."
        )

    try:
        result = await asyncio.wait_for(
            server.call_tool(tool_name, clean_args), timeout=45
        )
        text = _tool_result_to_text(result)

        # The MCP server returns errors as tool result content, not as
        # Python exceptions.  Intercept known error patterns and rewrite
        # them so the LLM gives the user an actionable message.
        lowered_text = text.lower()
        # Rate-limit / quota in the result text — raise as transient so we retry.
        if _is_rate_limit(lowered_text):
            retry_after = _parse_retry_after(text)
            extra = f" (server suggested retry-after={retry_after}s)" if retry_after else ""
            logger.warning("Workspace tool %s rate-limited via result text%s", tool_name, extra)
            raise _TransientMCPError(f"rate-limited on {tool_name}{extra}")
        if "insufficientfilepermissions" in lowered_text or "insufficient permissions" in lowered_text:
            logger.warning("Workspace tool %s: file-level permission error", tool_name)
            return (
                f"[PERMISSION ERROR] {tool_name}: You don't have edit access to this file. "
                f"The file is likely shared with you as view-only, or owned by someone else. "
                f"Tell the user this file can't be modified because it's view-only or "
                f"they don't have editor/owner permissions. Do NOT suggest re-authenticating — "
                f"the issue is file-level permissions, not OAuth."
            )

        return text
    except asyncio.TimeoutError:
        logger.error("Workspace tool %s timed out after 45s", tool_name)
        return (
            f"[TOOL ERROR] {tool_name} timed out after 45 seconds. "
            f"The Google Workspace service may be overloaded. "
            f"Tell the user to try again in a moment. "
            f"Do NOT use WebSearch as a fallback for private Google Workspace data."
        )
    except _TransientMCPError:
        # Already shaped for retry — let tenacity see it.
        raise
    except Exception as tool_err:
        error_text = str(tool_err)
        logger.error("Workspace tool %s failed: %s", tool_name, error_text)

        lowered = error_text.lower()
        # Rate-limit / quota — convert to transient so we retry with backoff.
        if _is_rate_limit(lowered):
            retry_after = _parse_retry_after(error_text)
            extra = f" (server suggested retry-after={retry_after}s)" if retry_after else ""
            logger.warning("Workspace tool %s rate-limited%s", tool_name, extra)
            raise _TransientMCPError(f"rate-limited on {tool_name}{extra}")
        if "insufficientfilepermissions" in lowered or "insufficient permissions" in lowered:
            return (
                f"[PERMISSION ERROR] {tool_name}: You don't have edit access to this file. "
                f"The file is likely shared with you as view-only, or owned by someone else. "
                f"Tell the user this file can't be modified because it's view-only or "
                f"they don't have editor/owner permissions. Do NOT suggest re-authenticating — "
                f"the issue is file-level permissions, not OAuth."
            )
        if "auth" in lowered or "token" in lowered or "unauthorized" in lowered:
            return (
                f"[AUTH ERROR] Google authorization expired or is missing for {tool_name}. "
                f"Tell the user to run /connect google to re-authorize. "
                f"Do NOT use WebSearch as a fallback."
            )
        return (
            f"[TOOL ERROR] {tool_name} failed: {error_text}. "
            f"Report this error to the user and suggest retrying. "
            f"Do NOT use WebSearch as a fallback for private Google Workspace data."
        )
    finally:
        await server.cleanup()


async def call_workspace_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """Call a workspace MCP tool. Returns a string result (or bracketed error
    message) — never raises. Wraps `_call_workspace_tool_inner` to convert
    post-retry transient failures (rate limit, persistent connection timeouts)
    into a user-facing string so callers don't need exception handling."""
    try:
        return await _call_workspace_tool_inner(tool_name, arguments)
    except _TransientMCPError as e:
        logger.error("Workspace tool %s exhausted retries: %s", tool_name, e)
        return (
            f"[RATE LIMIT] {tool_name} could not complete after retries: {e}. "
            f"The Google service is rate-limiting or temporarily unavailable. "
            f"Tell the user to wait 30-60 seconds and retry. "
            f"Do NOT use WebSearch as a fallback for private Google Workspace data."
        )


async def list_workspace_tool_schemas(
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return the live MCP tool input schemas, cached briefly per process."""
    global _workspace_tool_schema_cache

    now = time.monotonic()
    if (
        not force_refresh
        and _workspace_tool_schema_cache is not None
        and now - _workspace_tool_schema_cache[0] < _WORKSPACE_TOOL_SCHEMA_CACHE_TTL_SECONDS
    ):
        return dict(_workspace_tool_schema_cache[1])

    server = create_workspace_mcp_server()
    if server is None:
        raise RuntimeError("Google Workspace is not configured.")

    try:
        await server.connect()
        tools = await server.list_tools()
        schemas = {
            getattr(tool, "name"): _extract_tool_input_schema(tool)
            for tool in tools
            if getattr(tool, "name", None)
        }
        _workspace_tool_schema_cache = (now, schemas)
        return dict(schemas)
    finally:
        await server.cleanup()


async def get_workspace_tool_argument_names(
    tool_name: str,
    *,
    force_refresh: bool = False,
) -> set[str]:
    """Return the argument names for a live MCP tool, or an empty set on lookup failure."""
    try:
        schemas = await list_workspace_tool_schemas(force_refresh=force_refresh)
    except Exception as e:
        logger.debug("Failed to inspect workspace tool schemas: %s", e)
        return set()

    schema = schemas.get(tool_name)
    if not isinstance(schema, dict):
        return set()

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return set()

    return {name for name in properties if isinstance(name, str)}


async def get_google_auth_url(user_id: int, user_google_email: str = None) -> str:
    server = create_workspace_mcp_server()
    if server is None:
        raise RuntimeError("Google Workspace is not configured.")

    if user_google_email is None:
        user_google_email = await get_connected_google_email(user_id)
        if user_google_email is None:
            raise ValueError("A valid Google email address is required.")

    if not user_google_email or "@" not in user_google_email:
        raise ValueError("A valid Google email address is required.")

    try:
        await server.connect()
        result = await server.call_tool(
            "start_google_auth",
            {
                "service_name": "Google Workspace",
                "user_google_email": user_google_email,
            },
        )

        result_text = _tool_result_to_text(result)
        auth_url = _extract_authorization_url(result_text)
        if auth_url is None:
            raise RuntimeError(result_text)
        return auth_url
    finally:
        await server.cleanup()
