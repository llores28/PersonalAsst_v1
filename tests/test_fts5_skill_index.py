"""Tests for Wave 4.9 — SQLite FTS5 lexical retrieval over skills.

The existing keyword-tag matcher in ``SkillRegistry.match_skills`` is fast
but brittle: it requires exact tag overlap (after plural-strip + stop-word
filter). For a small Atlas-shaped catalog (~5 skills), that's fine. For a
5,400-skill agentskills.io community catalog, it misses long-tail
phrasings.

This module's FTS5 index is a complementary layer — opt-in via a future
setting flag — that returns BM25-ranked matches for fuzzy queries. These
tests pin the contract:

1. **Stemming** — "emails" matches a skill tagged "email" (Porter stemmer
   handles plural collapse).
2. **Multi-field** — instructions text is searched, not just tags. A query
   for a phrase that only appears in the body should still surface the
   skill.
3. **Operator escape** — user messages with FTS5 operator chars (``"``,
   ``*``, ``-``, ``?``, ``(``, ``)``) and bare ``OR`` / ``AND`` / ``NEAR``
   keywords don't raise.
4. **Empty query** — returns empty list, not an exception.
5. **Rebuild semantics** — rebuilding with a different skill set replaces
   the index; old skills don't bleed through.
"""

from __future__ import annotations

import pytest


def _sample_skills():
    from src.skills.fts5_index import IndexedSkill

    return [
        IndexedSkill(
            skill_id="gmail",
            name="Gmail",
            description="Read, search, draft, send Gmail messages.",
            tags=["email", "gmail", "workspace"],
            routing_hints=["check email", "what's in my inbox", "send a message"],
            instructions="Use the connected Gmail tools for inbox checks. ALWAYS draft before sending.",
        ),
        IndexedSkill(
            skill_id="calendar",
            name="Calendar",
            description="View, create, update Google Calendar events.",
            tags=["calendar", "events", "schedule"],
            routing_hints=["what's on my calendar", "schedule a meeting"],
            instructions="Calendar events only. NEVER use for tasks or reminders.",
        ),
        IndexedSkill(
            skill_id="devotional-style-guide",
            name="Devotional Style Guide",
            description="Guidelines for generating daily devotionals.",
            tags=["devotional", "writing", "preferences"],
            routing_hints=["write a devotional", "morning bible study"],
            instructions=(
                "User prefers 5-10 minute biblical study format with NIV scripture, "
                "non-denominational tone, Ellen White references for depth."
            ),
        ),
    ]


# --------------------------------------------------------------------------
# Basic retrieval
# --------------------------------------------------------------------------


class TestBasicRetrieval:
    def test_empty_query_returns_empty(self) -> None:
        from src.skills.fts5_index import SkillFTS5Index

        idx = SkillFTS5Index()
        idx.rebuild(_sample_skills())
        assert idx.query("") == []
        assert idx.query("   ") == []

    def test_exact_tag_match_surfaces_skill(self) -> None:
        from src.skills.fts5_index import SkillFTS5Index

        idx = SkillFTS5Index()
        idx.rebuild(_sample_skills())
        results = idx.query("calendar")
        assert results, "calendar query returned no results"
        assert results[0].skill_id == "calendar"

    def test_porter_stemming_collapses_plurals(self) -> None:
        """The Porter tokenizer collapses 'emails' to 'email' so a query for
        'emails' surfaces the gmail skill (which has tag 'email')."""
        from src.skills.fts5_index import SkillFTS5Index

        idx = SkillFTS5Index()
        idx.rebuild(_sample_skills())
        results = idx.query("emails")
        assert any(r.skill_id == "gmail" for r in results), (
            f"FTS5 didn't stem 'emails' → 'email'. Results: "
            f"{[r.skill_id for r in results]}"
        )

    def test_instructions_field_is_searchable(self) -> None:
        """A query for a phrase that only appears in the body should still
        surface the skill — the index covers instructions, not just tags."""
        from src.skills.fts5_index import SkillFTS5Index

        idx = SkillFTS5Index()
        idx.rebuild(_sample_skills())
        results = idx.query("biblical study")
        assert any(r.skill_id == "devotional-style-guide" for r in results)

    def test_top_k_limit_is_respected(self) -> None:
        from src.skills.fts5_index import SkillFTS5Index

        idx = SkillFTS5Index()
        idx.rebuild(_sample_skills())
        # Query with a term that hits ALL skills' instructions
        results = idx.query("user", top_k=1)
        assert len(results) <= 1


# --------------------------------------------------------------------------
# Operator escape — FTS5 syntax doesn't blow up on user input
# --------------------------------------------------------------------------


class TestOperatorEscape:
    @pytest.mark.parametrize("query", [
        "what's on my calendar?",
        'check "my" email',
        "events (urgent)",
        "send-an-email",
        "calendar OR email",
        "calendar AND email",
        "calendar NEAR email",
        "*",
        "()",
        '""',
    ])
    def test_user_input_with_fts5_operators_does_not_raise(self, query: str) -> None:
        from src.skills.fts5_index import SkillFTS5Index

        idx = SkillFTS5Index()
        idx.rebuild(_sample_skills())
        # Should never raise — sanitize_query strips operators before MATCH
        results = idx.query(query)
        assert isinstance(results, list)


# --------------------------------------------------------------------------
# Rebuild semantics
# --------------------------------------------------------------------------


class TestRebuildSemantics:
    def test_rebuild_replaces_index(self) -> None:
        """After rebuilding with a different skill set, queries against the
        old set return no results."""
        from src.skills.fts5_index import SkillFTS5Index, IndexedSkill

        idx = SkillFTS5Index()
        idx.rebuild(_sample_skills())
        assert any(r.skill_id == "gmail" for r in idx.query("emails"))

        # Rebuild with a completely different skill
        new_skills = [
            IndexedSkill(
                skill_id="weather",
                name="Weather",
                description="Get the local weather forecast.",
                tags=["weather", "forecast"],
                routing_hints=["what's the weather", "is it going to rain"],
            ),
        ]
        idx.rebuild(new_skills)
        # Old skills no longer present
        assert not any(r.skill_id == "gmail" for r in idx.query("emails"))
        # New skill is searchable
        assert any(r.skill_id == "weather" for r in idx.query("forecast"))

    def test_rebuild_returns_row_count(self) -> None:
        from src.skills.fts5_index import SkillFTS5Index

        idx = SkillFTS5Index()
        n = idx.rebuild(_sample_skills())
        assert n == 3


# --------------------------------------------------------------------------
# Hybrid retrieval contract — FTS5 results dedupe with keyword matches
# --------------------------------------------------------------------------


class TestHybridRetrievalContract:
    def test_fts5_results_can_union_with_keyword_matches(self) -> None:
        """Sketch of the hybrid pattern: take the union of
        ``match_skills`` (keyword-tag) and ``query_fts5`` results, dedupe
        by skill_id. This test pins the API surface — both return-value
        shapes are compatible."""
        from src.skills.fts5_index import SkillFTS5Index

        idx = SkillFTS5Index()
        idx.rebuild(_sample_skills())

        # Simulated keyword-tag matcher result (frozenset of skill_ids)
        keyword_matches = frozenset({"calendar"})
        # FTS5 result (list of SkillSearchResult)
        fts5_matches = idx.query("emails inbox")

        # Hybrid union — dedupe by skill_id
        all_ids = set(keyword_matches) | {r.skill_id for r in fts5_matches}
        assert "calendar" in all_ids  # from keyword matcher
        assert "gmail" in all_ids     # from FTS5 stemming "emails" → "email" tag
