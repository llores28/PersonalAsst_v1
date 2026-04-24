"""Skill loader for filesystem-based SKILL.md skills.

Implements progressive disclosure:
- Level 1: Parse YAML frontmatter (always loaded)
- Level 2: Load instructions from markdown body (on trigger)
- Level 3: Discover resources/ scripts/ templates/ subdirs (on demand)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.settings import settings
from src.skills.definition import SkillDefinition, SkillGroup, SkillSourceType

logger = logging.getLogger(__name__)


class SkillLoadError(Exception):
    """Raised when a skill cannot be loaded from filesystem."""
    pass


@dataclass
class ParsedSkill:
    """Intermediate representation of a parsed SKILL.md file."""

    # Level 1: Metadata
    id: str
    name: str
    group: str
    description: str
    version: str
    author: str
    tags: list[str]
    routing_hints: list[str]

    # Level 2: Instructions
    instructions: str
    instructions_path: Optional[Path]

    # Level 3: Resources
    resources: dict[str, Path]
    scripts: dict[str, Path]
    templates: dict[str, Path]

    # Relationships
    requires_skills: list[str]
    extends_skill: Optional[str]

    # Tool bindings
    tools: list[Any]  # Will be empty for knowledge-only skills
    requires_connection: bool
    read_only: bool


class SkillLoader:
    """Load skills from SKILL.md files with YAML frontmatter."""

    def __init__(self, user_skills_dir: Optional[Path] = None) -> None:
        """Initialize the skill loader.

        Args:
            user_skills_dir: Base directory for user-created skills
        """
        _default_dir = (
            Path(__file__).resolve().parents[2] / settings.user_skills_dir
        )
        self.user_skills_dir = user_skills_dir or _default_dir
        self._cache: dict[str, ParsedSkill] = {}

    def load_from_path(
        self,
        skill_path: Path,
        skill_id: Optional[str] = None,
    ) -> SkillDefinition:
        """Load a skill from a directory or SKILL.md file.

        Args:
            skill_path: Path to directory containing SKILL.md, or path to SKILL.md directly
            skill_id: Optional override for skill ID

        Returns:
            SkillDefinition ready for registration

        Raises:
            SkillLoadError: If SKILL.md is missing or invalid
        """
        # Resolve path to SKILL.md
        if skill_path.is_dir():
            skill_dir = skill_path
            skill_md = skill_path / "SKILL.md"
        else:
            skill_dir = skill_path.parent
            skill_md = skill_path

        if not skill_md.exists():
            raise SkillLoadError(f"SKILL.md not found at {skill_md}")

        # Parse the SKILL.md file
        parsed = self._parse_skill_md(skill_md, skill_dir)

        # Override ID if provided
        if skill_id:
            parsed.id = skill_id
        elif not parsed.id:
            # Default to directory name
            parsed.id = skill_dir.name

        # Default name to id if not provided
        if not parsed.name:
            parsed.name = parsed.id

        # Resolve SkillGroup
        try:
            group = SkillGroup(parsed.group.lower())
        except ValueError:
            logger.warning("Unknown skill group '%s', defaulting to USER", parsed.group)
            group = SkillGroup.USER

        # Build SkillDefinition
        return SkillDefinition(
            id=parsed.id,
            name=parsed.name,
            group=group,
            description=parsed.description,
            version=parsed.version,
            author=parsed.author,
            tags=parsed.tags,
            routing_hints=parsed.routing_hints,
            instructions=parsed.instructions,
            instructions_path=parsed.instructions_path,
            resources=parsed.resources,
            scripts=parsed.scripts,
            templates=parsed.templates,
            tools=parsed.tools,  # Currently always empty for filesystem skills
            requires_connection=parsed.requires_connection,
            read_only=parsed.read_only,
            requires_skills=parsed.requires_skills,
            extends_skill=parsed.extends_skill,
            source_type=SkillSourceType.FILESYSTEM,
            source_path=skill_md,
            is_active=True,
            installed_at=datetime.now(),
            updated_at=datetime.now(),
        )

    def _parse_skill_md(self, skill_md: Path, skill_dir: Path) -> ParsedSkill:
        """Parse a SKILL.md file into ParsedSkill.

        Expects YAML frontmatter followed by markdown body.
        """
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as e:
            raise SkillLoadError(f"Failed to read {skill_md}: {e}")

        # Split frontmatter from body
        frontmatter, body = self._split_frontmatter(content)

        # Parse YAML frontmatter
        metadata = self._parse_yaml(frontmatter) if frontmatter else {}

        # Discover Level 3 resources
        resources, scripts, templates = self._discover_resources(skill_dir)

        return ParsedSkill(
            id=metadata.get("id", ""),
            name=metadata.get("name", ""),
            group=metadata.get("group", "user"),
            description=metadata.get("description", ""),
            version=metadata.get("version", "1.0.0"),
            author=metadata.get("author", "user"),
            tags=metadata.get("tags", []),
            routing_hints=metadata.get("routing_hints", []),
            instructions=body.strip(),
            instructions_path=skill_md,
            resources=resources,
            scripts=scripts,
            templates=templates,
            requires_skills=metadata.get("requires_skills", []),
            extends_skill=metadata.get("extends_skill"),
            tools=[],  # Filesystem skills are knowledge-only initially
            requires_connection=metadata.get("requires_connection", False),
            read_only=metadata.get("read_only", True),  # Default to safe
        )

    def _split_frontmatter(self, content: str) -> tuple[str, str]:
        """Split YAML frontmatter from markdown body.

        Returns (frontmatter, body). If no frontmatter, returns ("", content).
        """
        lines = content.split("\n")
        if len(lines) >= 3 and lines[0].strip() == "---":
            # Find closing ---
            for i, line in enumerate(lines[1:], start=1):
                if line.strip() == "---":
                    frontmatter = "\n".join(lines[1:i])
                    body = "\n".join(lines[i + 1 :])
                    return frontmatter, body
        return "", content

    def _parse_yaml(self, yaml_text: str) -> dict[str, Any]:
        """Parse simple YAML frontmatter.

        Only supports basic key-value pairs, lists, and simple nesting.
        For complex YAML, consider adding pyyaml dependency.
        """
        result: dict[str, Any] = {}
        current_key: Optional[str] = None
        current_list: list[str] = []

        for line in yaml_text.split("\n"):
            stripped = line.strip()

            # Skip empty lines and comments
            if not stripped or stripped.startswith("#"):
                continue

            # List item
            if stripped.startswith("-"):
                item = stripped[1:].strip()
                if current_key:
                    current_list.append(item)
                continue

            # New key-value pair
            if ":" in stripped:
                # Save previous list if any
                if current_key and current_list:
                    result[current_key] = current_list
                    current_list = []

                key, raw_value = stripped.split(":", 1)
                key = key.strip()
                value: Any = raw_value.strip()

                # Remove quotes
                if value and value[0] == value[-1] == '"':
                    value = value[1:-1]
                elif value and value[0] == value[-1] == "'":
                    value = value[1:-1]

                # Handle inline JSON-like lists (e.g., [a, b] or [])
                if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
                    import json as _json
                    try:
                        parsed = _json.loads(value)
                        if isinstance(parsed, list):
                            value = [str(x) for x in parsed]
                    except (ValueError, TypeError):
                        pass

                # Handle booleans
                if isinstance(value, str) and value.lower() == "true":
                    value = True
                elif isinstance(value, str) and value.lower() == "false":
                    value = False

                current_key = key
                result[key] = value

        # Save final list if any
        if current_key and current_list:
            result[current_key] = current_list

        return result

    def _discover_resources(
        self, skill_dir: Path
    ) -> tuple[dict[str, Path], dict[str, Path], dict[str, Path]]:
        """Discover Level 3 resources in the skill directory.

        Looks for resources/, scripts/, templates/ subdirectories.
        Returns (resources, scripts, templates) dicts mapping name -> path.
        """
        resources: dict[str, Path] = {}
        scripts: dict[str, Path] = {}
        templates: dict[str, Path] = {}

        # Discover resources
        resources_dir = skill_dir / "resources"
        if resources_dir.exists():
            for f in resources_dir.iterdir():
                if f.is_file():
                    resources[f.stem] = f

        # Discover scripts
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists():
            for f in scripts_dir.iterdir():
                if f.is_file() and f.suffix in {".py", ".sh", ".js"}:
                    scripts[f.stem] = f

        # Discover templates
        templates_dir = skill_dir / "templates"
        if templates_dir.exists():
            for f in templates_dir.iterdir():
                if f.is_file() and f.suffix in {".j2", ".jinja", ".jinja2", ".md", ".txt"}:
                    templates[f.stem] = f

        return resources, scripts, templates

    def load_all_from_directory(self, skills_dir: Optional[Path] = None) -> list[SkillDefinition]:
        """Load all skills from a directory of skill folders.

        Each subdirectory should contain a SKILL.md file.
        """
        skills_dir = skills_dir or self.user_skills_dir
        if not skills_dir.exists():
            logger.warning("Skills directory not found: %s", skills_dir)
            return []

        skills: list[SkillDefinition] = []
        for item in skills_dir.iterdir():
            if item.is_dir():
                skill_md = item / "SKILL.md"
                if skill_md.exists():
                    try:
                        skill = self.load_from_path(item)
                        skills.append(skill)
                        logger.info("Loaded skill from filesystem: %s", skill.id)
                    except SkillLoadError as e:
                        logger.warning("Failed to load skill from %s: %s", item, e)

        return skills

    def reload_skill(self, skill_id: str) -> Optional[SkillDefinition]:
        """Reload a skill from its source path.

        Used for hot-reloading during development.
        """
        # This would require tracking source paths in a separate index
        # For now, users can re-register the skill
        logger.debug("Skill reloading not yet implemented for %s", skill_id)
        return None
