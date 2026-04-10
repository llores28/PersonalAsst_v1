"""Credential vault for dynamic tools — Redis-backed secure storage.

Tools declare required credentials in their manifest. The vault stores
credentials per-tool and injects them into the sandbox environment at
runtime. Credentials are NEVER logged or returned in tool output.

Security model:
- Credentials stored in Redis hash ``tool_credentials:{tool_name}``
- Only the credential keys declared in the manifest are injected
- CLI sandbox gets credentials as env vars with ``TOOL_`` prefix
- Function-type tools receive credentials as a dict argument
"""

import logging
from typing import Optional

import redis.asyncio as aioredis

from src.settings import settings

logger = logging.getLogger(__name__)

_VAULT_PREFIX = "tool_credentials"


def _key(tool_name: str) -> str:
    return f"{_VAULT_PREFIX}:{tool_name}"


async def _get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def store_credential(tool_name: str, cred_name: str, cred_value: str) -> None:
    """Store a single credential for a tool.

    Args:
        tool_name: Tool name (snake_case).
        cred_name: Credential key (e.g. 'linkedin_email').
        cred_value: Secret value.
    """
    r = await _get_redis()
    await r.hset(_key(tool_name), cred_name, cred_value)
    await r.aclose()
    logger.info("Credential '%s' stored for tool '%s'", cred_name, tool_name)


async def store_credentials(tool_name: str, creds: dict[str, str]) -> None:
    """Store multiple credentials for a tool at once."""
    if not creds:
        return
    r = await _get_redis()
    await r.hset(_key(tool_name), mapping=creds)
    await r.aclose()
    logger.info("Stored %d credentials for tool '%s'", len(creds), tool_name)


async def get_credential(tool_name: str, cred_name: str) -> Optional[str]:
    """Retrieve a single credential value."""
    r = await _get_redis()
    val = await r.hget(_key(tool_name), cred_name)
    await r.aclose()
    return val


async def get_credentials(tool_name: str) -> dict[str, str]:
    """Retrieve all credentials for a tool."""
    r = await _get_redis()
    creds = await r.hgetall(_key(tool_name))
    await r.aclose()
    return creds


async def delete_credential(tool_name: str, cred_name: str) -> None:
    """Delete a single credential."""
    r = await _get_redis()
    await r.hdel(_key(tool_name), cred_name)
    await r.aclose()
    logger.info("Credential '%s' deleted for tool '%s'", cred_name, tool_name)


async def delete_all_credentials(tool_name: str) -> None:
    """Delete all credentials for a tool."""
    r = await _get_redis()
    await r.delete(_key(tool_name))
    await r.aclose()
    logger.info("All credentials deleted for tool '%s'", tool_name)


async def list_credential_keys(tool_name: str) -> list[str]:
    """List credential keys (not values) for a tool."""
    r = await _get_redis()
    keys = await r.hkeys(_key(tool_name))
    await r.aclose()
    return keys


def build_sandbox_env(
    credentials: dict[str, str],
    allowed_keys: list[str] | None = None,
) -> dict[str, str]:
    """Build a safe environment dict for CLI subprocess execution.

    Includes minimal Python paths and approved credentials with ``TOOL_`` prefix.

    Args:
        credentials: Raw credentials from the vault.
        allowed_keys: If provided, only these credential keys are included.
            This should come from the manifest's declared credentials.

    Returns:
        Environment dict safe for subprocess use.
    """
    import sys
    import os

    env: dict[str, str] = {
        # Minimal Python runtime paths
        "PATH": os.defpath,
        "PYTHONPATH": "",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }

    # Add Python executable directory to PATH
    python_dir = str(os.path.dirname(sys.executable))
    if python_dir:
        env["PATH"] = f"{python_dir}:{env['PATH']}"

    # Inject only declared credentials with TOOL_ prefix
    for key, value in credentials.items():
        if allowed_keys is None or key in allowed_keys:
            env_key = f"TOOL_{key.upper()}"
            env[env_key] = value

    return env
