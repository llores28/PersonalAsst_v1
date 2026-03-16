"""Memory Agent — manages user memories via Mem0 (as_tool per AD-3)."""

import logging

from agents import Agent, function_tool

from src.settings import settings

logger = logging.getLogger(__name__)

MEMORY_INSTRUCTIONS = """\
You are a memory management specialist. You help the user manage what the assistant remembers.

## Capabilities
- Recall what the assistant knows about the user
- Search memories by topic or keyword
- Add new facts or preferences to memory
- Forget specific memories when asked
- Show all stored memories

## Rules
- When asked "what do you remember about X", search memories for X and present results.
- When the user says "remember that I...", add it as a new memory.
- When asked to forget something, find the matching memory and delete it.
- Present memories in a clear, organized format.
- Never fabricate memories — only report what's actually stored.
"""


@function_tool
async def recall_memories(query: str, user_id: str) -> str:
    """Search the user's memories by topic or keyword."""
    from src.memory.mem0_client import search_memories

    results = await search_memories(query, user_id=user_id, limit=10)
    if not results:
        return "I don't have any memories matching that topic."

    lines = ["Here's what I remember:"]
    for i, mem in enumerate(results, 1):
        text = mem.get("memory", mem.get("text", str(mem)))
        lines.append(f"{i}. {text}")
    return "\n".join(lines)


@function_tool
async def store_memory(text: str, user_id: str) -> str:
    """Store a new memory or preference for the user."""
    from src.memory.mem0_client import add_memory

    await add_memory(text, user_id=user_id, metadata={"type": "semantic"})
    return f"Got it! I'll remember: {text}"


@function_tool
async def list_all_memories(user_id: str) -> str:
    """List all stored memories for the user."""
    from src.memory.mem0_client import get_all_memories

    memories = await get_all_memories(user_id=user_id)
    if not memories:
        return "I don't have any memories stored yet."

    lines = [f"I have {len(memories)} memories stored:"]
    for i, mem in enumerate(memories, 1):
        text = mem.get("memory", mem.get("text", str(mem)))
        mem_id = mem.get("id", "?")
        lines.append(f"{i}. {text} (id: {mem_id})")
    return "\n".join(lines)


@function_tool
async def forget_memory(memory_id: str) -> str:
    """Delete a specific memory by its ID."""
    from src.memory.mem0_client import delete_memory

    success = await delete_memory(memory_id)
    if success:
        return "Done! That memory has been forgotten."
    return "I couldn't find or delete that memory. Check the ID and try again."


@function_tool
async def forget_all(user_id: str) -> str:
    """Delete ALL memories for the user. Use with extreme caution."""
    from src.memory.mem0_client import delete_all_memories

    success = await delete_all_memories(user_id=user_id)
    if success:
        return "All memories have been cleared. Starting fresh!"
    return "Something went wrong while clearing memories."


def create_memory_agent() -> Agent:
    """Create the memory specialist agent."""
    return Agent(
        name="MemoryAgent",
        instructions=MEMORY_INSTRUCTIONS,
        model=settings.model_general,
        tools=[recall_memories, store_memory, list_all_memories, forget_memory, forget_all],
    )
