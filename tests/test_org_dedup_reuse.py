"""Tests for Phase-3 duplicate-check reuse helpers in setup_org_project.

The helpers fuzzy-match planned agent/tool/skill names against existing items
and decide whether to reuse rather than recreate. The threshold is controlled
by `REUSE_THRESHOLD`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

if "agents" not in sys.modules:
    sys.modules["agents"] = MagicMock()

from src.agents.org_agent import (  # noqa: E402
    REUSE_THRESHOLD,
    _find_similar_existing_skill,
    _similar,
)


class TestSimilar:
    @pytest.mark.parametrize("a,b,expected_min", [
        ("Email Agent", "Email Agent", 1.0),
        ("email-agent", "Email Agent", 0.70),   # punctuation + case still close
        ("VideoComposer", "Video Composer", 0.85),
        ("Totally Different", "Random Phrase", 0.0),
    ])
    def test_similarity_bounds(self, a: str, b: str, expected_min: float) -> None:
        score = _similar(a, b)
        assert 0.0 <= score <= 1.0
        assert score >= expected_min

    def test_empty_strings_return_zero(self) -> None:
        assert _similar("", "anything") == 0.0
        assert _similar("anything", "") == 0.0
        assert _similar("", "") == 0.0

    def test_identical_is_one(self) -> None:
        assert _similar("abc", "abc") == 1.0
        assert _similar("  ABC  ", "abc") == 1.0


class TestReuseThreshold:
    def test_threshold_sensible(self) -> None:
        # Must be high enough to avoid false positives but low enough to
        # catch obvious duplicates like "Email Agent" vs "EmailAgent".
        assert 0.7 <= REUSE_THRESHOLD <= 0.95


class TestFindSimilarExistingSkill:
    def test_no_user_skills_dir_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        # Don't create user_skills/ at all
        result = _find_similar_existing_skill("anything", "Anything")
        assert result == (None, 0.0)

    def test_exact_id_match_returns_high_score(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src/user_skills/email-composer").mkdir(parents=True)
        (tmp_path / "src/user_skills/email-composer/SKILL.md").write_text(
            '---\nname: "Email Composer"\n---\n', encoding="utf-8"
        )

        sk_id, score = _find_similar_existing_skill("email-composer", "Email Composer")
        assert sk_id == "email-composer"
        assert score == 1.0

    def test_close_name_match_returns_above_threshold(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src/user_skills/email-composer").mkdir(parents=True)
        (tmp_path / "src/user_skills/email-composer/SKILL.md").write_text(
            '---\nname: "Email Composer"\n---\n', encoding="utf-8"
        )

        # planner suggests a nearly-identical id/name
        sk_id, score = _find_similar_existing_skill("emailcomposer", "Email Composer v2")
        assert sk_id == "email-composer"
        assert score >= REUSE_THRESHOLD

    def test_dissimilar_name_below_threshold(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src/user_skills/email-composer").mkdir(parents=True)
        (tmp_path / "src/user_skills/email-composer/SKILL.md").write_text(
            '---\nname: "Email Composer"\n---\n', encoding="utf-8"
        )

        sk_id, score = _find_similar_existing_skill(
            "ffmpeg-video-composer", "FFmpeg Video Composer"
        )
        # May find the email one as the "best" match, but the score must be
        # below threshold so the caller creates a new skill.
        assert score < REUSE_THRESHOLD

    def test_ignores_non_directories(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        root = tmp_path / "src/user_skills"
        root.mkdir(parents=True)
        # A stray file at the top level — must not be considered a skill
        (root / "README.md").write_text("", encoding="utf-8")

        sk_id, score = _find_similar_existing_skill("readme", "README")
        assert sk_id is None
        assert score == 0.0
