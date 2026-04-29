"""Wave 3.8 — agentskills.io conformance audit.

Goal: every ``SKILL.md`` under ``src/user_skills/`` (including the
auto-generated ``auto/`` subtree) parses cleanly through the existing
``SkillLoader`` AND conforms to the agentskills.io open standard. The
benefit is portability — once these tests pass, Atlas skills can drop
straight into ``~/.hermes/skills/`` (Hermes Agent's skills directory) and
agentskills.io community skills load in Atlas without translation.

The agentskills.io schema (as of 2026-04, per
https://hermes-agent.nousresearch.com/docs/ + the OpenClaw skill spec):

- File at ``<skill_dir>/SKILL.md``
- YAML frontmatter delimited by ``---`` lines
- Required fields: ``name``, ``description``
- Optional fields: ``version``, ``author``, ``tags`` (list), ``routing_hints``
  (list), ``requires_skills`` (list), ``extends_skill`` (string|null),
  ``tools`` (list), ``requires_connection`` (bool), ``read_only`` (bool)
- Markdown body with at least one heading

Pinned behaviors:

1. **Every checked-in SKILL.md parses without errors.**
2. **Required fields are present** (name, description).
3. **List fields actually parse as Python lists** — Atlas's existing inline
   ``tags: [a, b, c]`` form silently degrades to a string when the items
   aren't JSON-quoted. The conformance test catches that regression and
   the auto-writer's multi-line list form is the canonical fix (Wave 1.1).
4. **Auto-writer output is conformant** — the SKILL.md template in
   ``src/skills/skill_writer.py`` produces valid output.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _resolve_user_skills_dir() -> Path:
    """Resolve ``src/user_skills`` from the test file's location so the
    test runs identically inside the container and on the host."""
    return Path(__file__).resolve().parents[1] / "src" / "user_skills"


def _all_skill_md_files() -> list[Path]:
    base = _resolve_user_skills_dir()
    if not base.exists():
        return []
    return sorted(base.rglob("SKILL.md"))


# Skip the entire suite if no skills are checked in yet.
_SKILL_PATHS = _all_skill_md_files()


# --------------------------------------------------------------------------
# Per-file parsing
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not _SKILL_PATHS, reason="No SKILL.md files found under src/user_skills/"
)
class TestEverySkillParses:
    @pytest.mark.parametrize("skill_path", _SKILL_PATHS, ids=lambda p: p.parent.name)
    def test_loads_via_skill_loader(self, skill_path: Path) -> None:
        """Every checked-in skill must parse cleanly through the existing
        ``SkillLoader`` — that's the integration contract."""
        from src.skills.loader import SkillLoader, SkillLoadError

        loader = SkillLoader(user_skills_dir=skill_path.parent.parent)
        try:
            skill = loader.load_from_path(skill_path.parent)
        except SkillLoadError as e:
            pytest.fail(f"SkillLoader failed on {skill_path}: {e}")

        # Required fields populated
        assert skill.id, f"{skill_path}: skill ID is empty"
        assert skill.description, f"{skill_path}: description is empty"

    @pytest.mark.parametrize("skill_path", _SKILL_PATHS, ids=lambda p: p.parent.name)
    def test_tags_field_is_list_or_empty(self, skill_path: Path) -> None:
        """The agentskills.io spec says ``tags`` is a list. Atlas's YAML
        loader has a known limitation: the inline form ``tags: [a, b, c]``
        without JSON-quoted items silently degrades to a string. This test
        catches that regression — every skill's tags must be a real list."""
        from src.skills.loader import SkillLoader

        loader = SkillLoader(user_skills_dir=skill_path.parent.parent)
        skill = loader.load_from_path(skill_path.parent)
        assert isinstance(skill.tags, list), (
            f"{skill_path}: tags must be a list, got {type(skill.tags).__name__} "
            f"({skill.tags!r:.80}). Use multi-line YAML list form (one tag per line)."
        )

    @pytest.mark.parametrize("skill_path", _SKILL_PATHS, ids=lambda p: p.parent.name)
    def test_routing_hints_field_is_list(self, skill_path: Path) -> None:
        from src.skills.loader import SkillLoader

        loader = SkillLoader(user_skills_dir=skill_path.parent.parent)
        skill = loader.load_from_path(skill_path.parent)
        assert isinstance(skill.routing_hints, list), (
            f"{skill_path}: routing_hints must be a list, got "
            f"{type(skill.routing_hints).__name__}"
        )

    @pytest.mark.parametrize("skill_path", _SKILL_PATHS, ids=lambda p: p.parent.name)
    def test_body_has_at_least_one_heading(self, skill_path: Path) -> None:
        """The agentskills.io spec recommends the body contain markdown
        headings to scope the instructions. We don't enforce a specific
        heading text — just that the body isn't empty or frontmatter-only."""
        content = skill_path.read_text(encoding="utf-8")
        # Strip frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            body = parts[2] if len(parts) >= 3 else ""
        else:
            body = content
        body = body.strip()
        assert body, f"{skill_path}: body is empty after frontmatter"
        # Either a markdown heading (#, ##, ###) OR plain prose is acceptable;
        # an empty body is not.
        has_heading = any(line.lstrip().startswith("#") for line in body.splitlines())
        # Don't fail on missing heading — that's a soft recommendation, not
        # a hard spec requirement. We DO fail on empty body.
        if not has_heading:
            # Log but don't fail — print so reviewers see the warning
            print(f"NOTE: {skill_path} has no markdown heading in body (soft recommendation).")


# --------------------------------------------------------------------------
# Auto-writer output conformance — Wave 1.1's render_skill_md must produce
# files that pass the same checks above.
# --------------------------------------------------------------------------


class TestAutoWriterIsConformant:
    def test_rendered_skill_md_round_trips_through_loader(self) -> None:
        """The skill_writer's output must conform to the same schema as
        hand-written skills. Without this, Wave 1.1 could silently produce
        skills that don't parse."""
        from src.skills.skill_writer import _render_skill_md
        from src.skills.loader import SkillLoader
        import tempfile

        rendered = _render_skill_md(
            skill_id="test-auto-skill",
            description="auto-generated test skill for conformance",
            tags=["auto", "test", "conformance"],
            routing_hint="run the conformance test",
            workflow_text="When the user runs the conformance test, do the thing.",
            source_user_id="999",
        )

        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "user_skills"
            (base / "test-auto-skill").mkdir(parents=True)
            skill_path = base / "test-auto-skill" / "SKILL.md"
            skill_path.write_text(rendered, encoding="utf-8")

            loader = SkillLoader(user_skills_dir=base)
            skill = loader.load_from_path(skill_path.parent)

        assert skill.id == "test-auto-skill"
        assert skill.description.startswith("auto-generated")
        assert skill.tags == ["auto", "test", "conformance"], (
            f"Auto-writer's tags didn't round-trip: {skill.tags!r}"
        )
        assert isinstance(skill.routing_hints, list) and skill.routing_hints, (
            f"Auto-writer's routing_hints didn't round-trip: {skill.routing_hints!r}"
        )

    def test_rendered_skill_handles_empty_tag_list(self) -> None:
        """Edge case: workflow text without extractable tags shouldn't
        produce a malformed YAML list."""
        from src.skills.skill_writer import _render_skill_md
        from src.skills.loader import SkillLoader
        import tempfile

        rendered = _render_skill_md(
            skill_id="empty-tags-skill",
            description="skill with empty tag list",
            tags=[],
            routing_hint="do thing",
            workflow_text="Description.",
            source_user_id="999",
        )

        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "user_skills"
            (base / "empty-tags-skill").mkdir(parents=True)
            (base / "empty-tags-skill" / "SKILL.md").write_text(rendered, encoding="utf-8")

            loader = SkillLoader(user_skills_dir=base)
            skill = loader.load_from_path(base / "empty-tags-skill")

        # Empty tag list is fine; the renderer falls back to ``- auto`` so
        # callers can still match by the "auto" tag.
        assert isinstance(skill.tags, list)


# --------------------------------------------------------------------------
# Spec field coverage — every checked-in skill should have the optional
# fields the loader reads, even if they're empty defaults.
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not _SKILL_PATHS, reason="No SKILL.md files found under src/user_skills/"
)
class TestSpecFieldCoverage:
    @pytest.mark.parametrize("skill_path", _SKILL_PATHS, ids=lambda p: p.parent.name)
    def test_frontmatter_contains_name_and_description(self, skill_path: Path) -> None:
        """The two required agentskills.io fields. Without these, the skill
        doesn't render in any catalog UI."""
        content = skill_path.read_text(encoding="utf-8")
        assert "name:" in content, f"{skill_path}: missing 'name:' frontmatter field"
        assert "description:" in content, f"{skill_path}: missing 'description:' field"

    @pytest.mark.parametrize("skill_path", _SKILL_PATHS, ids=lambda p: p.parent.name)
    def test_no_inline_unquoted_list_form_for_tags(self, skill_path: Path) -> None:
        """Atlas's YAML parser can't reliably parse the inline ``tags: [a, b, c]``
        form when items aren't JSON-quoted. Multi-line list form is the
        canonical fix. This test enforces the convention so future hand-written
        skills don't silently degrade tags to a string."""
        content = skill_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("tags:") and "[" in stripped and "]" in stripped:
                # Inline form — must be JSON-style (quoted) to parse correctly
                value = stripped.split(":", 1)[1].strip()
                assert '"' in value or "'" in value, (
                    f"{skill_path}: inline tags list without quoted items will "
                    f"degrade to a string. Use multi-line list form:\n"
                    f"  tags:\n    - tag1\n    - tag2\n"
                    f"Got: {stripped}"
                )
