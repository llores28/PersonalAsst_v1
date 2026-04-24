"""Tests for the SKILL.md writer helper used by ``setup_org_project``.

The helper is responsible for turning the LLM's planned skill dicts into
filesystem SKILL.md files under ``src/user_skills/<id>/`` so the orchestrator's
selective router can see them on the next turn.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# The agents SDK is heavy — stub it out for import.
if "agents" not in sys.modules:
    sys.modules["agents"] = MagicMock()

from src.agents.org_agent import (  # noqa: E402
    _RESERVED_SKILL_IDS,
    _slugify_skill_id,
    _write_skill_md,
)


class TestSlugifySkillId:
    @pytest.mark.parametrize("raw,expected", [
        ("FFmpeg Video Composer", "ffmpeg-video-composer"),
        ("PDF Report Generator", "pdf-report-generator"),
        ("   my skill   ", "my-skill"),
        ("weird/name!!", "weird-name"),
        ("", "skill"),
        ("A", "a"),
    ])
    def test_slugify(self, raw: str, expected: str) -> None:
        assert _slugify_skill_id(raw) == expected


class TestReservedSkillIds:
    def test_contains_internal_builtins(self) -> None:
        assert "memory" in _RESERVED_SKILL_IDS
        assert "scheduler" in _RESERVED_SKILL_IDS
        assert "organizations" in _RESERVED_SKILL_IDS

    def test_contains_google_workspace_ids(self) -> None:
        # These must never be shadowed by user-written SKILL.md files.
        assert "gmail" in _RESERVED_SKILL_IDS
        assert "calendar" in _RESERVED_SKILL_IDS
        assert "drive" in _RESERVED_SKILL_IDS


class TestWriteSkillMd:
    def test_writes_valid_frontmatter_and_body(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)

        ok, msg = _write_skill_md(
            skill_id="ffmpeg-video-composer",
            name="FFmpeg Video Composer",
            description="Compose videos from images, clips, and subtitles via FFmpeg.",
            tags=["ffmpeg", "video", "subtitle"],
            routing_hints=[
                "compose a video from images and clips",
                "build a video with subtitles and music",
            ],
            instructions="## Purpose\n\nUse this skill to assemble video deliverables.",
            related_tools=["ffmpeg_combine_assets", "ffmpeg_add_subtitles"],
            org_name="FFmpeg Video Composer",
        )

        skill_path = tmp_path / "src/user_skills/ffmpeg-video-composer/SKILL.md"
        assert ok, msg
        assert skill_path.exists()
        content = skill_path.read_text(encoding="utf-8")

        # Frontmatter shape
        assert content.startswith("---\n")
        assert 'name: "FFmpeg Video Composer"' in content
        # Each list item on its own line (regression against the older
        # skill_factory_agent bug that collapsed all items into one line)
        assert '\n  - "ffmpeg"' in content
        assert '\n  - "video"' in content
        assert '\n  - "compose a video from images and clips"' in content

        # Body links the related tools so the orchestrator can see them
        assert "ffmpeg_combine_assets" in content
        assert "ffmpeg_add_subtitles" in content

    def test_does_not_overwrite_existing_skill(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "src/user_skills/existing-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("pre-existing content", encoding="utf-8")

        ok, msg = _write_skill_md(
            skill_id="existing-skill",
            name="whatever",
            description="",
            tags=[],
            routing_hints=[],
            instructions="",
            related_tools=[],
            org_name="x",
        )
        assert ok is False
        assert "already exists" in msg
        # Original content preserved
        assert (target / "SKILL.md").read_text(encoding="utf-8") == "pre-existing content"

    def test_escapes_double_quotes_in_strings(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        ok, _ = _write_skill_md(
            skill_id="quote-test",
            name='Name with "quotes"',
            description='He said "hi"',
            tags=["t"],
            routing_hints=['a hint with "quotes" inside'],
            instructions="",
            related_tools=[],
            org_name="org",
        )
        assert ok
        content = (tmp_path / "src/user_skills/quote-test/SKILL.md").read_text(encoding="utf-8")
        # Quotes escaped so the simple YAML parser doesn't choke
        assert '\\"quotes\\"' in content


class TestSkillIdSlugConsistency:
    """Regression: skill IDs stored on agents must match disk directory names.

    The LLM planner returns skill names like 'subtitle_generator' (underscores).
    _slugify_skill_id converts them to 'subtitle-generator' (hyphens) which is
    what _write_skill_md uses for the directory name. Both paths must agree so
    the Step 5 validation check doesn't produce false 'not registered' warnings.
    """

    @pytest.mark.parametrize("llm_skill_id, expected_disk_slug", [
        ("subtitle_generator", "subtitle-generator"),
        ("ffmpeg_video_composer", "ffmpeg-video-composer"),
        ("audio_mixing", "audio-mixing"),
        ("SubtitleGenerator", "subtitlegenerator"),
        ("FFmpeg Video Composer", "ffmpeg-video-composer"),
        ("loudness_normalization", "loudness-normalization"),
    ])
    def test_slugify_matches_disk_dir_format(self, llm_skill_id: str, expected_disk_slug: str) -> None:
        """Slug produced by _slugify_skill_id must equal what _write_skill_md uses as dir name."""
        slug = _slugify_skill_id(llm_skill_id)
        assert slug == expected_disk_slug, (
            f"_slugify_skill_id('{llm_skill_id}') = '{slug}', "
            f"expected '{expected_disk_slug}'. Disk dir and stored skill ID would mismatch."
        )

    def test_reserved_ids_not_slugified(self) -> None:
        """Reserved IDs (memory, scheduler, organizations) must pass through unchanged."""
        for rid in ("memory", "scheduler", "organizations"):
            assert _slugify_skill_id(rid) == rid
