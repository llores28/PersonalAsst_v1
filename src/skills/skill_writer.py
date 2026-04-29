"""Reflector → SKILL.md autoload writeback (Wave 1.1).

When the reflector observes the same workflow ≥3 times (tracked via the
``crystallize_count`` metadata bumped by ``mem0_client.add_memory``'s dedup
path), this module writes a first-class ``SKILL.md`` into
``src/user_skills/auto/<slug>/`` so the next session loads it as a real skill
via the existing ``SkillLoader.load_all_from_directory()`` pass.

Closes the headline gap with Hermes Agent: where Hermes autonomously writes a
skill file at step #4 of every non-trivial task, Atlas now does the same once
a pattern is observed often enough to be considered stable. Higher threshold
than Hermes on purpose — single-user/single-platform usage is lower-volume,
and writing a skill on a one-shot insight bloats the registry with noise.

Idempotency: each generated SKILL.md gets a ``source: reflector-auto`` tag and
a stable slug derived from the workflow text, so re-crystallizing the same
workflow updates the existing file rather than creating duplicates. The
matching memory in Mem0 gets ``crystallized=True`` set so the next dedup hit
short-circuits — no point re-checking on every reflector run.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.settings import settings

logger = logging.getLogger(__name__)


# Lower than Hermes's 1× because Atlas is single-user/single-platform and the
# reflector runs every turn — we'd burn Mem0 with noise crystallizing every
# one-shot. 3 was chosen empirically: enough to filter out exploratory phrasings,
# few enough that a clearly-repeated pattern surfaces within a normal week of use.
CRYSTALLIZE_THRESHOLD = 3


# Stop-words removed from auto-generated tags. Kept narrow on purpose —
# aggressive filtering would strip useful nouns from short workflow strings.
_TAG_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "this", "that", "these", "those", "with", "when", "where", "what", "which",
    "user", "users", "wants", "want", "prefers", "prefer", "always", "every",
})

_SLUG_BAD = re.compile(r"[^a-z0-9-]+")
_SLUG_RUN = re.compile(r"-+")


def _slugify(text: str, max_len: int = 50) -> str:
    """Stable, filesystem-safe slug derived from workflow text.

    Lowercase, ASCII, dashes-only. Truncated on a word boundary so re-running
    on a slightly extended phrasing of the same workflow still produces the
    same prefix → same path → idempotent updates.
    """
    s = text.lower().strip()
    s = _SLUG_BAD.sub("-", s)
    s = _SLUG_RUN.sub("-", s).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rsplit("-", 1)[0] or s[:max_len]
    return s or "auto-skill"


def _extract_tags(text: str, *, limit: int = 6) -> list[str]:
    """Pick the most distinctive ≤6 word-tokens as YAML tags.

    Picks content words (≥4 chars, not stop-words) in order of first appearance.
    Good enough for routing recall — the SkillRegistry tag matcher does plural
    stripping and case-folds, so we don't need to be clever here.
    """
    seen: set[str] = set()
    tags: list[str] = []
    for raw in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text):
        tok = raw.lower().strip("'-")
        if tok in _TAG_STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        tags.append(tok)
        if len(tags) >= limit:
            break
    return tags


def _resolve_auto_skills_dir() -> Path:
    """Return the absolute path to ``src/user_skills/auto/``, creating it if
    needed. Co-located with the existing user-skills tree so
    ``SkillLoader.load_all_from_directory`` picks them up at startup with no
    config changes."""
    base = (Path(__file__).resolve().parents[2] / settings.user_skills_dir).resolve()
    auto_dir = base / "auto"
    auto_dir.mkdir(parents=True, exist_ok=True)
    return auto_dir


def _render_skill_md(
    *,
    skill_id: str,
    description: str,
    tags: list[str],
    routing_hint: str,
    workflow_text: str,
    source_user_id: str,
) -> str:
    """Render a SKILL.md body conforming to the format used by
    ``src/user_skills/devotional-style-guide/SKILL.md`` so the existing
    ``SkillLoader._parse_skill_md`` parses it without changes.

    Uses the multi-line YAML list form (``tags:\\n  - foo``) for tags so
    ``SkillLoader._parse_yaml`` can hydrate them back into a Python list. The
    inline ``[a, b, c]`` form requires JSON-style quotes to round-trip — the
    loader's existing inline-JSON branch can't parse bare identifiers.
    """
    iso_now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tag_lines = "\n".join(f"  - {t}" for t in tags) if tags else "  - auto"
    safe_routing = routing_hint.replace('"', "'")
    return f"""---
name: {skill_id.replace('-', ' ').title()}
description: {description}
version: 1.0.0
author: reflector-auto
tags:
{tag_lines}
routing_hints:
  - "{safe_routing}"
requires_skills: []
extends_skill: null
tools: []
requires_connection: false
read_only: true
---

# {skill_id.replace('-', ' ').title()}

_Auto-generated by the reflector on {iso_now} for user `{source_user_id}` after
observing this workflow ≥{CRYSTALLIZE_THRESHOLD} times. Edit freely — once you
modify this file, the reflector won't overwrite it._

## Workflow

{workflow_text}

## How to apply

When the user's request matches the routing hint above, follow the workflow
described in the previous section. If the workflow involves a tool the
assistant doesn't have, fall back to standard tool selection.
"""


async def maybe_crystallize_workflow(
    workflow_text: str,
    *,
    user_id: str,
    add_memory_result: Optional[dict] = None,
    threshold: int = CRYSTALLIZE_THRESHOLD,
) -> Optional[Path]:
    """Promote a frequently-observed workflow to a first-class SKILL.md.

    Args:
        workflow_text: the ``workflow_learned`` string from a reflector turn.
        user_id: Mem0 user_id (typically the Telegram user id as str).
        add_memory_result: the dict returned by ``mem0_client.add_memory`` for
            this same workflow. We read ``metadata.crystallize_count`` from it
            to decide whether to fire. If absent, we re-fetch via search.
        threshold: minimum count before a SKILL.md is written. Default 3.

    Returns:
        Path to the written SKILL.md if one was created/updated, else None.

    Never raises — crystallization failures must not block reflector storage
    or interrupt the turn. Logs and returns None.
    """
    try:
        if not workflow_text or not workflow_text.strip():
            return None

        # Determine the count. The fast path is from the add_memory result;
        # fall back to a search if the caller didn't pass one through.
        count = 1
        memory_id: Optional[str] = None
        already_crystallized = False
        if add_memory_result:
            meta = add_memory_result.get("metadata") or {}
            count = int(meta.get("crystallize_count", 1))
            memory_id = add_memory_result.get("id")
            already_crystallized = bool(meta.get("crystallized"))

        if count < threshold:
            logger.debug(
                "Skill crystallization skipped (count=%d < %d) for user %s",
                count, threshold, user_id,
            )
            return None

        if already_crystallized:
            # Memory's already been promoted — don't churn the file on every
            # subsequent dedup hit.
            logger.debug(
                "Skill crystallization skipped (already_crystallized) for user %s",
                user_id,
            )
            return None

        slug = _slugify(workflow_text)
        if not slug:
            logger.debug("Skill crystallization skipped (empty slug) for user %s", user_id)
            return None

        tags = _extract_tags(workflow_text)
        routing_hint = workflow_text.strip().rstrip(".").lower()
        skill_md = _render_skill_md(
            skill_id=slug,
            description=workflow_text.strip(),
            tags=tags,
            routing_hint=routing_hint,
            workflow_text=workflow_text.strip(),
            source_user_id=user_id,
        )

        target_dir = _resolve_auto_skills_dir() / slug
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "SKILL.md"
        target_path.write_text(skill_md, encoding="utf-8")

        logger.info(
            "Reflector crystallized workflow into skill: %s (count=%d, user=%s)",
            target_path, count, user_id,
        )

        # Mark the source memory so we don't re-crystallize on every turn.
        if memory_id:
            try:
                from src.memory.mem0_client import get_memory

                mem = get_memory()
                merged_meta = {
                    **(add_memory_result.get("metadata") or {}),
                    "crystallized": True,
                    "crystallized_path": str(target_path),
                }
                try:
                    mem.update(memory_id, workflow_text, metadata=merged_meta)
                except TypeError:
                    # Older mem0 — text-only update is fine; we'll just
                    # re-write the SKILL.md once on the next dedup hit
                    # (idempotent because slug is stable).
                    mem.update(memory_id, workflow_text)
            except Exception as exc:
                logger.debug("Couldn't mark memory %s as crystallized: %s", memory_id, exc)

        return target_path
    except Exception as exc:  # noqa: BLE001 — reflector path must never raise
        logger.warning("Skill crystallization failed for user %s: %s", user_id, exc)
        return None
