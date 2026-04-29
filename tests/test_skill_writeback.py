"""Tests for Wave 1.1 — reflector → SKILL.md autoload writeback.

The reflector observes the same workflow multiple times across user turns;
``mem0_client.add_memory`` increments ``crystallize_count`` on each dedup
hit. When the count reaches ``CRYSTALLIZE_THRESHOLD`` (default 3), the skill
writer promotes the workflow to a first-class SKILL.md that the existing
``SkillLoader.load_all_from_directory`` picks up on the next session.

These tests pin:

1. **Threshold gating** — fewer than 3 observations writes nothing.
2. **First crystallization** — the 3rd observation writes a valid SKILL.md
   to ``src/user_skills/auto/<slug>/`` with all the YAML frontmatter
   ``SkillLoader._parse_skill_md`` requires.
3. **Idempotency** — once ``crystallized=True`` is in metadata, subsequent
   observations don't churn the file.
4. **Stable slug** — the same workflow text always lands at the same path.
5. **Loader compatibility** — the written SKILL.md round-trips through
   ``SkillLoader.load_from_path`` without errors. This is the contract that
   keeps the auto-write path from regressing the existing skill subsystem.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

if "agents" not in sys.modules:
    fake_agents = MagicMock()
    fake_agents.Agent = MagicMock
    fake_agents.function_tool = lambda *a, **kw: (lambda f: f) if (a and not callable(a[0])) else (a[0] if a else (lambda f: f))
    fake_agents.Runner = MagicMock()
    fake_agents.WebSearchTool = MagicMock
    sys.modules["agents"] = fake_agents
    sys.modules["agents.mcp"] = MagicMock()


@pytest.fixture
def auto_skills_dir(monkeypatch, tmp_path: Path) -> Path:
    """Redirect skill writeback to a temp dir so tests don't touch real
    user_skills/. Returns the temp ``auto/`` subdir."""
    from src.skills import skill_writer

    fake_root = tmp_path / "user_skills"
    fake_root.mkdir(parents=True, exist_ok=True)
    auto_dir = fake_root / "auto"
    auto_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        skill_writer, "_resolve_auto_skills_dir", lambda: auto_dir
    )
    return auto_dir


# --------------------------------------------------------------------------
# Threshold + idempotency
# --------------------------------------------------------------------------


class TestCrystallizeThreshold:
    @pytest.mark.asyncio
    async def test_below_threshold_writes_nothing(self, auto_skills_dir: Path) -> None:
        from src.skills.skill_writer import maybe_crystallize_workflow

        result = await maybe_crystallize_workflow(
            "User reviews the budget before sending the weekly report",
            user_id="999",
            add_memory_result={
                "deduplicated": True,
                "id": "mem-1",
                "metadata": {"crystallize_count": 2},
            },
        )
        assert result is None
        assert not list(auto_skills_dir.iterdir()), \
            f"Nothing should have been written for count=2: {list(auto_skills_dir.iterdir())}"

    @pytest.mark.asyncio
    async def test_at_threshold_writes_skill_md(self, auto_skills_dir: Path) -> None:
        from src.skills.skill_writer import maybe_crystallize_workflow

        # Pass a no-op stub for the mem0 update path so the test doesn't hit
        # qdrant. The skill_writer's mem0 import is local so we patch via
        # sys.modules.
        import src.memory.mem0_client as mem0c

        class _StubMem:
            def update(self, *_a, **_kw):
                return None
        mem0c._memory_instance = _StubMem()

        result = await maybe_crystallize_workflow(
            "User reviews the budget before sending the weekly report",
            user_id="999",
            add_memory_result={
                "deduplicated": True,
                "id": "mem-1",
                "metadata": {"crystallize_count": 3},
            },
        )
        assert result is not None, "A SKILL.md should have been written at count=3"
        assert result.exists()
        assert result.name == "SKILL.md"
        content = result.read_text(encoding="utf-8")
        assert "---" in content[:5], "Frontmatter delimiter missing"
        assert "name:" in content
        assert "tags:" in content
        assert "routing_hints:" in content
        # Workflow text appears in the body
        assert "reviews the budget" in content.lower()

    @pytest.mark.asyncio
    async def test_already_crystallized_skips(self, auto_skills_dir: Path) -> None:
        from src.skills.skill_writer import maybe_crystallize_workflow

        result = await maybe_crystallize_workflow(
            "User reviews the budget before sending the weekly report",
            user_id="999",
            add_memory_result={
                "deduplicated": True,
                "id": "mem-1",
                "metadata": {"crystallize_count": 7, "crystallized": True},
            },
        )
        assert result is None, \
            "Once crystallized=True, subsequent observations must not re-write"

    @pytest.mark.asyncio
    async def test_empty_workflow_text_returns_none(self, auto_skills_dir: Path) -> None:
        from src.skills.skill_writer import maybe_crystallize_workflow

        for text in ("", "   ", "\n\t"):
            result = await maybe_crystallize_workflow(
                text,
                user_id="999",
                add_memory_result={"metadata": {"crystallize_count": 5}},
            )
            assert result is None, f"Empty workflow text should not crystallize: {text!r}"


# --------------------------------------------------------------------------
# Slug stability
# --------------------------------------------------------------------------


class TestSlugStability:
    def test_slug_is_filesystem_safe(self) -> None:
        from src.skills.skill_writer import _slugify

        s = _slugify("User wants morning meetings, not afternoon ones! (2026 edition)")
        # No spaces, no punctuation — only [a-z0-9-]
        import re
        assert re.fullmatch(r"[a-z0-9-]+", s), f"Bad slug: {s!r}"

    def test_slug_truncates_on_word_boundary(self) -> None:
        from src.skills.skill_writer import _slugify

        long_workflow = (
            "User reviews the quarterly budget spreadsheet against last quarter's "
            "actuals before drafting any forward-looking financial commitments"
        )
        s = _slugify(long_workflow, max_len=40)
        assert len(s) <= 40
        # Truncation lands on a word boundary (no trailing partial word)
        assert not s.endswith("-")

    def test_same_workflow_produces_same_slug(self) -> None:
        from src.skills.skill_writer import _slugify

        text = "User prefers concise email summaries"
        assert _slugify(text) == _slugify(text)
        # Trailing whitespace and case shouldn't change the slug
        assert _slugify(text) == _slugify(f"  {text.upper()}  ")


# --------------------------------------------------------------------------
# Loader round-trip — the integration contract
# --------------------------------------------------------------------------


class TestLoaderRoundTrip:
    @pytest.mark.asyncio
    async def test_written_skill_loads_via_skill_loader(self, auto_skills_dir: Path) -> None:
        """The written SKILL.md must parse cleanly through the existing loader.

        Without this contract, writeback could silently regress the loader
        path — skills would be written but never registered."""
        import src.memory.mem0_client as mem0c

        class _StubMem:
            def update(self, *_a, **_kw):
                return None
        mem0c._memory_instance = _StubMem()

        from src.skills.skill_writer import maybe_crystallize_workflow
        from src.skills.loader import SkillLoader

        path = await maybe_crystallize_workflow(
            "User wants the daily devotional emailed at 6am Central",
            user_id="999",
            add_memory_result={
                "id": "mem-X",
                "metadata": {"crystallize_count": 3},
            },
        )
        assert path is not None and path.exists()

        loader = SkillLoader(user_skills_dir=auto_skills_dir.parent)
        skill = loader.load_from_path(path.parent)

        # Required SkillDefinition fields are populated
        assert skill.id, "Skill ID must be set"
        assert skill.description, "Description must be set"
        assert isinstance(skill.tags, list)
        assert isinstance(skill.routing_hints, list) and skill.routing_hints, \
            "Routing hints must be populated so SkillRegistry can match"
        # The workflow text appears in the body that becomes instructions
        assert "devotional" in skill.instructions.lower()


# --------------------------------------------------------------------------
# Mem0 metadata round-trip — the dedup increment contract
# --------------------------------------------------------------------------


class TestMem0CrystallizeCountMerge:
    @pytest.mark.asyncio
    async def test_dedup_path_increments_crystallize_count(self) -> None:
        """``add_memory`` must merge the new metadata onto the existing memory
        and increment ``crystallize_count`` so subsequent calls can detect when
        a workflow has hit the crystallization threshold."""
        from src.memory import mem0_client

        # Build a fake Mem0 that returns an existing high-similarity hit.
        captured: dict = {}

        class _FakeMem:
            def search(self, _text, **_kw):
                return {"results": [{
                    "id": "mem-1",
                    "score": 0.95,
                    "memory": "User reviews the budget weekly",
                    "metadata": {"crystallize_count": 4, "type": "procedural"},
                }]}

            def update(self, hit_id, text, metadata=None):
                captured["id"] = hit_id
                captured["text"] = text
                captured["metadata"] = metadata
                return None

            def add(self, *_a, **_kw):
                pytest.fail("add() should not run when a dedup hit was found")

        mem0_client._memory_instance = _FakeMem()

        result = await mem0_client.add_memory(
            "User reviews the budget every Monday",
            user_id="999",
            metadata={"type": "procedural", "source": "reflector"},
        )

        assert result["deduplicated"] is True
        assert result["id"] == "mem-1"
        assert captured["metadata"] is not None, "metadata must be passed to update"
        # crystallize_count incremented from 4 to 5
        assert captured["metadata"]["crystallize_count"] == 5
        # Original metadata preserved
        assert captured["metadata"]["type"] == "procedural"
        # Returned dict carries metadata so the caller (reflector) can decide
        # whether to crystallize without doing another search.
        assert result["metadata"]["crystallize_count"] == 5
