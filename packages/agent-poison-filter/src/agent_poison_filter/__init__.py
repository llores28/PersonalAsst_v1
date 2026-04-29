"""Shared poison-filter for workspace tool memories.

Prevents a self-reinforcing failure loop where:
1. The model fails to call a connected workspace tool (transient bug, missing
   creds, MCP hiccup, etc.) and produces a refusal text response.
2. The reflector summarizes the turn as a workflow ("assistant cannot access
   email — propose a search query and ask the user to paste the result").
3. That summary lands in Mem0 as a procedural memory.
4. Next turn, the persona builder retrieves that memory, the model reads it,
   and the model again refuses to call the tool — even though the tool is
   now operational. The bad memory is reinforced.

This module is the single source of truth for both filter passes:
- WRITE-time: ``reflector_agent`` consults ``is_poisoned_learning`` before
  storing any preference / workflow.
- READ-time: ``persona.build_dynamic_persona_prompt`` consults
  ``filter_stale_memories`` before injecting recalled memories.

Substring + regex layered on purpose. Substrings catch the historical
phrasings that landed in Mem0 before this filter existed; the regex catches
the broader semantic family (any "(cannot|can't|unable) ... (access|read)
... (email|gmail|inbox|calendar|drive|workspace)" pattern, plus the
"ask user to paste" workaround pattern that signals the agent gave up).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# Historical substring matches kept for backward compatibility with memories
# already in Mem0. New phrasings should be caught by the regex below.
_POISON_SUBSTRINGS: tuple[str, ...] = (
    # "Tools are broken" family
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
    "connector path",
    "drive inventory",
    "drive connector",
    "authenticated inventory",
    "authenticated listing",
    "drive access to be fixed",
    "before continuing other tasks",
    # Re-auth nudges
    "require re-authorization",
    "may require re-",
    "re-authorize",
    "reauthorize",
    "re-auth",
    "reauth",
    "request the user to complete oauth",
    "request the user to authorize",
    # Refusal / workaround patterns
    "ask for user to provide",
    "ask the user to provide",
    "ask user to provide",
    "ask the user to paste",
    "ask user to paste",
    "asks the user to paste",
    "asks user to paste",
    "paste the email",
    "paste the message",
    "paste the top result",
    "should not claim access",
    "must not claim access",
    "claiming access",
    "should clarify that it cannot",
    "should clarify that the assistant cannot",
    "offer a workaround",
    "offer the workaround",
    "without demonstrating access",
    "without verifying the user",
    "spoken-style summary",
    "in this environment/session the assistant was unable",
    "in this session the assistant was unable",
    "unable to access the user",
    "not available in this turn",
    "isn't available in this turn",
    "not available in this session",
    "isn't available in this session",
    "connected gmail path isn't available",
    "connected gmail path is not available",
    "cannot access gmail",
    "can't access gmail",
    "cannot access calendar",
    "can't access calendar",
    "cannot access drive",
    "can't access drive",
    # Patterns that slipped through prior filters and motivated this refactor
    "cannot access the user",
    "can't access the user",
    "cannot directly read",
    "can't directly read",
    "propose gmail search",
    "proposes gmail search",
    "propose a gmail search",
    "proposes a gmail search",
)


# Regex pass: catches the broader semantic family — refusal verbs paired with
# any workspace surface. Compiled with IGNORECASE; tuned to avoid false
# positives on benign mentions ("the user can read their inbox themselves").
#
# Keep these expressions narrow on purpose. Anything that fires here is
# silently dropped from memory, so the bar is "this phrasing is almost
# certainly a learned refusal pattern, not legitimate factual content."
_POISON_REGEXES: tuple[re.Pattern[str], ...] = (
    # "(cannot|can't|unable to|...) (access|read|reach|fetch|retrieve|see|use|call|check)"
    # within ~80 chars of a workspace surface noun.
    re.compile(
        r"\b(?:cannot|can(?:['‘’ʼ]|\s+)?not|can(?:['‘’ʼ]|\s+)?t|unable to|isn(?:['‘’ʼ]|\s+)?t able to|"
        r"is not able to|won(?:['‘’ʼ]|\s+)?t be able to|will not be able to|"
        r"doesn(?:['‘’ʼ]|\s+)?t have access to|does not have access to)\b"
        r"[^.\n]{0,80}?"
        r"\b(?:gmail|email|e-?mail|inbox|mail|calendar|drive|docs?|sheets?|slides?|"
        r"contacts?|tasks?|workspace|google account|connected account)\b",
        re.IGNORECASE,
    ),
    # "ask(s)? (the )? user (to|for) (paste|provide|copy|share|forward)"
    re.compile(
        r"\b(?:ask|asks|asked|asking|prompt|prompts|prompted|tell|tells|told|"
        r"request|requests|requested|requesting)\b"
        r"\s+(?:the\s+)?user(?:s)?\s+(?:to|for)\s+"
        r"(?:paste|provide|copy|share|forward|send|supply)",
        re.IGNORECASE,
    ),
    # "propose(s)? (a )? (gmail|email|calendar|drive) (search|query|filter)"
    re.compile(
        r"\b(?:propose|proposes|proposed|proposing|suggest|suggests|suggested|suggesting|"
        r"offer|offers|offered|offering)\b"
        r"\s+(?:a\s+|the\s+)?"
        r"(?:gmail|email|calendar|drive|workspace)\s+"
        r"(?:search|query|filter|link)",
        re.IGNORECASE,
    ),
    # "in[-\s]chat" + (cannot|unable) — refusals that frame the chat itself as the limitation
    re.compile(
        r"\b(?:in[-\s]?chat|in[-\s]?session|in[-\s]?this[-\s]?session)\b"
        r"[^.\n]{0,80}?"
        r"\b(?:cannot|can(?:['‘’ʼ]|\s+)?t|unable to|not available|isn(?:['‘’ʼ]|\s+)?t available)\b",
        re.IGNORECASE,
    ),
)


def is_poisoned_learning(text: str) -> bool:
    """Return True if *text* is a learned refusal pattern that would poison
    future workspace tool routing if stored in Mem0.

    Used at WRITE time by ``reflector_agent`` to drop suspect learnings
    before they reach Mem0.
    """
    if not text:
        return False
    lowered = text.lower()
    if any(phrase in lowered for phrase in _POISON_SUBSTRINGS):
        return True
    return any(pattern.search(text) for pattern in _POISON_REGEXES)


def filter_stale_memories(
    memories: list[dict],
    *,
    workspace_connected: bool,
) -> list[dict]:
    """Drop memories that contradict the current operational state.

    Used at READ time by the persona builder. Only filters when workspace
    is currently connected — when it isn't, "I cannot access Gmail" is a
    true statement and shouldn't be hidden.
    """
    if not workspace_connected or not memories:
        return list(memories)

    kept: list[dict] = []
    for mem in memories:
        text = mem.get("memory") or mem.get("text") or ""
        if is_poisoned_learning(text):
            logger.debug("Filtered stale workspace memory: %s", text[:100])
            continue
        kept.append(mem)
    return kept
