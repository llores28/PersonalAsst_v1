"""Tests for the routing improvements in ``SkillRegistry.match_skills``.

Covers:
- Plural stripping ("videos" matches a hint containing "video").
- Tag-based matching (tags are high-confidence single keywords).
- Stop-word list (common words do not trigger matches by themselves).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

if "agents" not in sys.modules:
    sys.modules["agents"] = MagicMock()

from src.skills.definition import SkillDefinition, SkillGroup
from src.skills.registry import SkillRegistry, _strip_plural


def _tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _skill(
    skill_id: str,
    *,
    group: SkillGroup = SkillGroup.USER,
    routing_hints: list[str] | None = None,
    tags: list[str] | None = None,
) -> SkillDefinition:
    return SkillDefinition(
        id=skill_id,
        group=group,
        description="test",
        tools=[_tool(f"{skill_id}_tool")],
        instructions="",
        routing_hints=routing_hints or [],
        tags=tags or [],
    )


class TestStripPlural:
    def test_drops_trailing_s(self) -> None:
        assert _strip_plural("videos") == "video"
        assert _strip_plural("subtitles") == "subtitle"

    def test_handles_ies_ending(self) -> None:
        assert _strip_plural("companies") == "company"

    def test_handles_es_ending(self) -> None:
        assert _strip_plural("boxes") == "box"
        assert _strip_plural("matches") == "match"

    def test_keeps_short_tokens(self) -> None:
        assert _strip_plural("ass") == "ass"
        assert _strip_plural("is") == "is"

    def test_preserves_double_s(self) -> None:
        # "pass" / "class" should not turn into "pas" / "clas"
        assert _strip_plural("class") == "class"
        assert _strip_plural("pass") == "pass"


class TestMatchSkillsRoutingImprovements:
    def test_plural_message_matches_singular_hint(self) -> None:
        reg = SkillRegistry()
        reg.register(_skill(
            "ffmpeg_video_composer",
            routing_hints=["compose a video from images"],
            tags=["ffmpeg", "video"],
        ))
        matched = reg.match_skills("Can you make me some videos from these photos?")
        assert "ffmpeg_video_composer" in matched

    def test_plural_tag_matches_singular_message(self) -> None:
        reg = SkillRegistry()
        reg.register(_skill(
            "subtitle_generator",
            routing_hints=["generate subtitles"],
            tags=["subtitles", "transcription"],
        ))
        matched = reg.match_skills("generate a subtitle for this clip")
        assert "subtitle_generator" in matched

    def test_tag_overrides_weak_hint(self) -> None:
        """A curated tag should be enough to select a skill even when routing_hints
        use phrasing different from the user's message."""
        reg = SkillRegistry()
        reg.register(_skill(
            "pdf_report",
            routing_hints=["produce a polished document"],
            tags=["pdf"],
        ))
        # Message doesn't overlap with the hint at all but does contain the tag.
        matched = reg.match_skills("make me a pdf summary")
        assert "pdf_report" in matched

    def test_stop_words_do_not_trigger_false_match(self) -> None:
        """A hint that is mostly stop-words must not match on the stop-words alone."""
        reg = SkillRegistry()
        reg.register(_skill(
            "noise_skill",
            group=SkillGroup.USER,
            routing_hints=["please help me do this with the"],
            tags=[],
        ))
        # Message contains "please" and "help" but no content overlap.
        matched = reg.match_skills("please help me with something entirely unrelated")
        # Fallback kicks in when no skills match — so we assert the skill is NOT
        # selected for a targeted reason: sprinkling stop-words alone shouldn't
        # over-select the skill when other skills are available.
        reg.register(_skill(
            "real_skill",
            routing_hints=["convert a file"],
            tags=["convert"],
        ))
        matched2 = reg.match_skills("please convert this file")
        assert "real_skill" in matched2
        assert "noise_skill" not in matched2
