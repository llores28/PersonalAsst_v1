"""One-time cleanup: remove stale Mem0 memories about workspace tools being
broken or unreachable, and clear the poisoned RedisSession conversation
history so the next turn starts clean.

Reuses the shared ``src.memory.poison_filter`` so the cleanup phrase list
stays in lockstep with the runtime filter.

Run inside the container:
    docker compose exec assistant python scripts/cleanup_stale_memories.py
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from src.memory.mem0_client import delete_memory, get_all_memories
    from src.memory.poison_filter import is_poisoned_learning
    from src.settings import settings

    user_id = str(settings.owner_telegram_id)
    print(f"Cleaning stale memories for user {user_id}...")

    all_mems = await get_all_memories(user_id)
    print(f"Found {len(all_mems)} total memories")

    deleted = 0
    for mem in all_mems:
        text = mem.get("memory") or mem.get("text") or ""
        mem_id = mem.get("id")
        if not mem_id:
            continue
        if is_poisoned_learning(text):
            print(f"  DELETING: [{mem_id}] {text[:120]}")
            await delete_memory(mem_id)
            deleted += 1

    print(f"\nDeleted {deleted} stale memories")

    # Clear the RedisSession to remove poisoned conversation history
    import redis.asyncio as aioredis
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    session_key = f"agent_session:{user_id}"
    existed = await r.delete(session_key)
    # Pattern-based cleanup for any session variants
    keys = []
    async for key in r.scan_iter(f"agent_session:{user_id}*"):
        keys.append(key)
    if keys:
        await r.delete(*keys)
    await r.aclose()
    print(f"Cleared RedisSession: {session_key} (existed={existed}, pattern_keys={len(keys)})")

    # Report on conversation keys without deleting (only the session was poisoned)
    r2 = aioredis.from_url(settings.redis_url, decode_responses=True)
    conv_keys = []
    async for key in r2.scan_iter(f"conv:{user_id}*"):
        conv_keys.append(key)
    await r2.aclose()
    print(f"Found {len(conv_keys)} conversation keys (NOT deleting — only session was poisoned)")

    print("\nDone! Restart the bot or send a new message to test.")


if __name__ == "__main__":
    asyncio.run(main())
