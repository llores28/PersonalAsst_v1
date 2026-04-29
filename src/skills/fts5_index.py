"""SQLite FTS5 lexical index for skill retrieval (Wave 4.9).

The existing ``SkillRegistry.match_skills`` is a fast keyword-tag classifier
— exact tag match, plural-strip, stop-word filter. It works well at Atlas's
~5-skill scale but degrades when the skill catalog grows: a 5,400-skill
agentskills.io community installation (Hermes Agent's claim) needs more
than tag overlap to surface the right skill for a fuzzy phrasing.

This module provides a complementary lexical retrieval layer using SQLite's
built-in FTS5 extension. Index is in-memory by default (``:memory:``) and
rebuilt on every register call — no schema migrations, no on-disk cache to
invalidate. Callers that want it enabled set
``settings.skill_fts_enabled=True``; otherwise the index isn't built and
``SkillRegistry.match_skills`` keeps its current keyword-only behavior.

Why FTS5 specifically:
- Stdlib (no dep)
- BM25-ranked porter-stemmed full-text search ("emails" matches "email")
- Trivially handles 10K+ documents at sub-millisecond query time
- Compatible with the agentskills.io spec — index over name + description +
  tags + routing_hints + instructions covers the same fields Hermes's
  ``hermes/skills`` index reads.

Hybrid retrieval pattern (Wave 4.9 + future): take the union of
``match_skills`` (keyword-tag) and ``query_fts5`` (lexical-rank) results.
Deduplicate by skill_id. Order by FTS5 rank for consistency. The
keyword-tag matcher catches high-confidence exact tag hits; FTS5 catches
the long-tail phrasings the tag matcher would miss.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass
class IndexedSkill:
    """One row in the FTS5 index. Mirror of the SkillDefinition fields the
    lexical search reads — kept as a separate dataclass so the index module
    doesn't import the agents SDK transitively."""

    skill_id: str
    name: str
    description: str
    tags: list[str]
    routing_hints: list[str]
    instructions: str = ""


@dataclass
class SkillSearchResult:
    """One match from ``SkillFTS5Index.query``. Lower ``rank`` = better match
    (FTS5's BM25 score is negative-rank-ordered by default)."""

    skill_id: str
    rank: float
    snippet: str = ""


class SkillFTS5Index:
    """In-memory SQLite FTS5 index over skill metadata + instructions.

    Single-writer, multiple-reader. Index is rebuilt from scratch on
    ``rebuild`` — no incremental updates because the skill catalog is small
    and rebuild takes microseconds. If we ever scale to thousands of
    skills, swap to file-backed and add incremental ``upsert``.
    """

    def __init__(self) -> None:
        self._conn: Optional[sqlite3.Connection] = None

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        # ``check_same_thread=False`` because aiogram dispatches on multiple
        # asyncio threads. SQLite serializes per-connection internally so
        # this is safe for single-writer/many-reader use.
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
                skill_id UNINDEXED,
                name,
                description,
                tags,
                routing_hints,
                instructions,
                tokenize = 'porter unicode61'
            )
            """
        )
        self._conn = conn
        return conn

    def rebuild(self, skills: Iterable[IndexedSkill]) -> int:
        """Drop all rows, insert one per skill. Returns the row count.

        Cheap enough to call on every ``SkillRegistry.register`` — a 100-row
        rebuild on a stock laptop is ~1 ms. Don't optimize until a real
        bottleneck shows up.
        """
        conn = self._ensure_conn()
        conn.execute("DELETE FROM skills_fts")
        rows = []
        for s in skills:
            rows.append(
                (
                    s.skill_id,
                    s.name,
                    s.description,
                    " ".join(s.tags or []),
                    " ".join(s.routing_hints or []),
                    s.instructions,
                )
            )
        conn.executemany(
            "INSERT INTO skills_fts (skill_id, name, description, tags, "
            "routing_hints, instructions) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(rows)

    def query(self, text: str, *, top_k: int = 5) -> list[SkillSearchResult]:
        """Return up to ``top_k`` skills ranked by BM25 against ``text``.

        FTS5 query syntax escapes: we strip operators (``"``, ``*``, ``-``,
        ``(``, ``)``, ``OR``, ``AND``, ``NEAR``) and tokenize on whitespace
        so user messages with quotes or parens don't blow up the parser.
        """
        if not text or not text.strip():
            return []

        conn = self._ensure_conn()
        sanitized = self._sanitize_query(text)
        if not sanitized:
            return []

        try:
            cursor = conn.execute(
                "SELECT skill_id, rank, snippet(skills_fts, 5, '[', ']', '...', 12) "
                "FROM skills_fts WHERE skills_fts MATCH ? ORDER BY rank LIMIT ?",
                (sanitized, top_k),
            )
            return [
                SkillSearchResult(skill_id=row[0], rank=float(row[1]), snippet=row[2] or "")
                for row in cursor.fetchall()
            ]
        except sqlite3.OperationalError as exc:
            # FTS5 syntax errors fall through to empty results. We don't
            # raise — the caller's existing keyword matcher still works.
            logger.debug("FTS5 query failed (returning empty): %s", exc)
            return []

    @staticmethod
    def _sanitize_query(text: str) -> str:
        """Strip FTS5 syntax characters and normalize whitespace.

        FTS5 treats unquoted operators (``OR``, ``AND``, ``NEAR``) and
        special chars (``"``, ``*``, ``-``, ``(``, ``)``) specially. A user
        message like "what's on my calendar?" would otherwise raise
        ``OperationalError: fts5: syntax error near '?'``. Replace operator
        chars with spaces and rejoin tokens with implicit AND (FTS5's
        default for whitespace-separated terms).
        """
        bad_chars = '"*()-+:'
        cleaned = "".join(" " if c in bad_chars else c for c in text)
        # FTS5 keyword operators must be lowercased to be treated as terms,
        # not operators.
        tokens = []
        for tok in cleaned.split():
            tok_lower = tok.lower()
            if tok_lower in ("or", "and", "near", "not"):
                continue
            tokens.append(tok_lower)
        return " ".join(tokens)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
