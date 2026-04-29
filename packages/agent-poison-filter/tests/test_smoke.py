"""Smoke tests for agent-poison-filter as a standalone package.

Verifies the package can be imported via its standalone name (not via
Atlas's ``src.memory.poison_filter`` path) and that the public API
contract holds. The Atlas in-tree tests (``tests/test_adversarial_harness.py``)
cover the regex/substring coverage in depth.
"""

from __future__ import annotations

import pytest


def test_public_api_imports() -> None:
    """The two public functions must import from the top-level namespace."""
    from agent_poison_filter import is_poisoned_learning, filter_stale_memories

    assert callable(is_poisoned_learning)
    assert callable(filter_stale_memories)


def test_classic_2026_04_28_phrase_is_caught() -> None:
    """Pinned regression: the exact phrase that shipped Atlas's filter."""
    from agent_poison_filter import is_poisoned_learning

    phrase = (
        "Assistant cannot access the user's email in-chat; proposes Gmail "
        "search queries and asks user to paste the top result for summarization."
    )
    assert is_poisoned_learning(phrase)


def test_legitimate_preferences_survive() -> None:
    from agent_poison_filter import is_poisoned_learning

    keep = [
        "User prefers concise email summaries with subject and one-line body",
        "User wants morning calendar reviews on weekdays",
    ]
    for p in keep:
        assert not is_poisoned_learning(p), f"False positive: {p}"


def test_filter_stale_memories_respects_workspace_connected_flag() -> None:
    from agent_poison_filter import filter_stale_memories

    poisoned = {"memory": "I can't access your Gmail right now"}
    legit = {"memory": "User prefers concise email summaries"}

    # Workspace connected: poisoned memory dropped
    kept = filter_stale_memories([poisoned, legit], workspace_connected=True)
    assert legit in kept
    assert poisoned not in kept

    # Workspace disconnected: poisoned memory KEPT (it's a true statement)
    kept2 = filter_stale_memories([poisoned, legit], workspace_connected=False)
    assert poisoned in kept2 and legit in kept2


def test_curly_apostrophe_can_t_caught() -> None:
    """The 2026-04-28 1:31 PM regression — curly apostrophes (U+2019)
    must be caught alongside straight apostrophes."""
    from agent_poison_filter import is_poisoned_learning

    assert is_poisoned_learning("I can’t access your Gmail from this chat")
