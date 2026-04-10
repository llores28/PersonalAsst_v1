"""One-time cleanup: remove stale Mem0 memories about Drive tools being broken
and clear the poisoned RedisSession conversation history.

Run inside the container:
    docker compose exec assistant python scripts/cleanup_stale_memories.py
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from src.settings import settings
    from src.memory.mem0_client import get_all_memories, delete_memory

    user_id = str(settings.owner_telegram_id)
    print(f"Cleaning stale memories for user {user_id}...")

    # Stale phrases that indicate "Drive tools are broken" memories
    stale_phrases = [
        "needs authenticated",
        "needs connected",
        "tool access to be fixed",
        "tool access needs",
        "tools need fixing",
        "tools are broken",
        "tools aren't working",
        "drive tool fix",
        "drive session",
        "session issue",
        "connector issue",
        "can't access drive",
        "cannot access drive",
        "drive access to be fixed",
        "before continuing other tasks",
        "expects google drive tool access to be fixed",
    ]

    all_mems = await get_all_memories(user_id)
    print(f"Found {len(all_mems)} total memories")

    deleted = 0
    for mem in all_mems:
        text = mem.get("memory", mem.get("text", "")).lower()
        mem_id = mem.get("id")
        if not mem_id:
            continue
        if any(phrase in text for phrase in stale_phrases):
            print(f"  DELETING: [{mem_id}] {text[:100]}")
            await delete_memory(mem_id)
            deleted += 1

    print(f"\nDeleted {deleted} stale memories")

    # Clear the RedisSession to remove poisoned conversation history
    import redis.asyncio as aioredis
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    session_key = f"agent_session:{user_id}"
    existed = await r.delete(session_key)
    # Also try pattern-based cleanup for any session variants
    keys = []
    async for key in r.scan_iter(f"agent_session:{user_id}*"):
        keys.append(key)
    if keys:
        await r.delete(*keys)
    await r.aclose()
    print(f"Cleared RedisSession: {session_key} (existed={existed}, pattern_keys={len(keys)})")

    # Also clear the registry cache key pattern
    r2 = aioredis.from_url(settings.redis_url, decode_responses=True)
    conv_keys = []
    async for key in r2.scan_iter(f"conv:{user_id}*"):
        conv_keys.append(key)
    await r2.aclose()
    print(f"Found {len(conv_keys)} conversation keys (NOT deleting — only session was poisoned)")

    print("\nDone! Restart the bot or send a new message to test.")


if __name__ == "__main__":
    asyncio.run(main())
