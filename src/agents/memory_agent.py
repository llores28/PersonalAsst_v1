"""Memory Agent — manages user memories via Mem0 (as_tool per AD-3)."""

import logging

from agents import Agent, function_tool

from src.models.router import ModelRole, select_model

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


def _build_bound_memory_tools(bound_user_id: int) -> list:
    """Build memory tools with user_id bound via closure (no LLM guessing)."""
    str_user_id = str(bound_user_id)

    @function_tool(name_override="recall_my_memories")
    async def recall_my_memories(query: str) -> str:
        """Search your memories by topic or keyword."""
        from src.memory.mem0_client import search_memories

        results = await search_memories(query, user_id=str_user_id, limit=10)
        if not results:
            return "I don't have any memories matching that topic."

        lines = ["Here's what I remember:"]
        for i, mem in enumerate(results, 1):
            text = mem.get("memory", mem.get("text", str(mem)))
            lines.append(f"{i}. {text}")
        return "\n".join(lines)

    @function_tool(name_override="store_my_memory")
    async def store_my_memory(text: str) -> str:
        """Store a new memory or preference."""
        from src.memory.mem0_client import add_memory

        await add_memory(text, user_id=str_user_id, metadata={"type": "semantic"})
        return f"Got it! I'll remember: {text}"

    @function_tool(name_override="list_my_memories")
    async def list_my_memories() -> str:
        """List all stored memories."""
        from src.memory.mem0_client import get_all_memories

        memories = await get_all_memories(user_id=str_user_id)
        if not memories:
            return "I don't have any memories stored yet."

        lines = [f"I have {len(memories)} memories stored:"]
        for i, mem in enumerate(memories, 1):
            text = mem.get("memory", mem.get("text", str(mem)))
            mem_id = mem.get("id", "?")
            lines.append(f"{i}. {text} (id: {mem_id})")
        return "\n".join(lines)

    @function_tool(name_override="forget_my_memory")
    async def forget_my_memory(memory_id: str) -> str:
        """Delete a specific memory by its ID."""
        from src.memory.mem0_client import delete_memory

        success = await delete_memory(memory_id)
        if success:
            return "Done! That memory has been forgotten."
        return "I couldn't find or delete that memory. Check the ID and try again."

    @function_tool(name_override="forget_all_my_memories")
    async def forget_all_my_memories() -> str:
        """Delete ALL memories. Use with extreme caution."""
        from src.memory.mem0_client import delete_all_memories

        success = await delete_all_memories(user_id=str_user_id)
        if success:
            return "All memories have been cleared. Starting fresh!"
        return "Something went wrong while clearing memories."

    @function_tool(name_override="summarize_my_conversation")
    async def summarize_my_conversation() -> str:
        """Summarize and archive the current conversation session to long-term memory."""
        from src.memory.conversation import archive_session

        summary = await archive_session(bound_user_id)
        if summary:
            return f"Session archived to long-term memory. Summary: {summary}"
        return "No conversation to archive (session is empty or too short)."

    @function_tool(name_override="get_my_recent_context")
    async def get_my_recent_context() -> str:
        """Retrieve recent conversation context from the current session."""
        from src.memory.conversation import get_session_context

        context = await get_session_context(bound_user_id)
        if context:
            return context
        return "No recent conversation context available (new session)."

    return [
        recall_my_memories,
        store_my_memory,
        list_my_memories,
        forget_my_memory,
        forget_all_my_memories,
        summarize_my_conversation,
        get_my_recent_context,
    ]


def create_memory_agent() -> Agent:
    """Create the memory specialist agent."""
    selection = select_model(ModelRole.GENERAL)
    return Agent(
        name="MemoryAgent",
        instructions=MEMORY_INSTRUCTIONS,
        model=selection.model_id,
        tools=[recall_memories, store_memory, list_all_memories, forget_memory, forget_all],
    )
