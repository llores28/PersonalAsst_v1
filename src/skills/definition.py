"""Skill definition contract — the single shape every skill source must produce.

Borrows from:
- OpenClaw tool groups/profiles for organizing tools by capability domain
- OpenAI Agents SDK progressive disclosure (metadata first, full instructions on demand)
- NanoClaw CLI-first principle (deterministic execution separated from reasoning)
- Claude Agent Skills filesystem-based skill pattern with YAML frontmatter

See ADR-2026-03-19-google-workspace-skills-over-wrapper-agents.md for prior context.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SkillGroup(str, enum.Enum):
    """Logical domain a skill belongs to (inspired by OpenClaw tool groups)."""

    GOOGLE_WORKSPACE = "google_workspace"
    INTERNAL = "internal"
    DYNAMIC = "dynamic"
    MCP = "mcp"
    AGENT = "agent"
    USER = "user"  # User-created filesystem-based skills
    KNOWLEDGE = "knowledge"  # Knowledge-only skills (no tools)


class SkillSourceType(str, enum.Enum):
    """Source of the skill definition."""

    CODE = "code"  # Built-in Python code-based skills
    FILESYSTEM = "filesystem"  # SKILL.md file-based skills


class SkillProfile(str, enum.Enum):
    """Pre-built tool allowlists for different agent roles.

    Inspired by OpenClaw tool profiles (minimal / coding / messaging / full).
    """

    FULL = "full"
    READONLY = "readonly"
    WORKSPACE_ONLY = "workspace_only"
    INTERNAL_ONLY = "internal_only"
    MINIMAL = "minimal"


# Maps each profile to the set of groups it permits.
# ``None`` means "no restriction — allow all groups".
PROFILE_ALLOWED_GROUPS: dict[SkillProfile, Optional[frozenset[SkillGroup]]] = {
    SkillProfile.FULL: None,
    SkillProfile.READONLY: frozenset({
        SkillGroup.GOOGLE_WORKSPACE,
        SkillGroup.INTERNAL,
        SkillGroup.MCP,
    }),
    SkillProfile.WORKSPACE_ONLY: frozenset({SkillGroup.GOOGLE_WORKSPACE}),
    SkillProfile.INTERNAL_ONLY: frozenset({SkillGroup.INTERNAL}),
    SkillProfile.MINIMAL: frozenset(),
}


@dataclass
class SkillDefinition:
    """One skill = a logical group of related tools + expertise.

    Enhanced for Phase 1+ skill marketplace with:
    - Three-level progressive disclosure (metadata → instructions → resources)
    - Filesystem-based skill loading (SKILL.md with YAML frontmatter)
    - Knowledge-only skills (tools optional)
    - Skill dependencies and composition
    - Versioning and lifecycle management

    This is the **contract** — every registration path (MCP wrappers, closure
    builders, CLI manifests, agent-as-tool, SKILL.md files) must produce a
    ``SkillDefinition`` before the orchestrator will accept it.

    Level 1 (Metadata): Always loaded - id, name, description, version, tags
    Level 2 (Instructions): Loaded when skill is triggered - instructions, routing_hints
    Level 3 (Resources): Loaded on-demand - additional docs, scripts, templates

    Attributes:
        # Level 1: Metadata (always loaded)
        id: Unique identifier, e.g. "gmail", "scheduler", "stock_checker".
        name: Human-readable name (defaults to id if not provided).
        group: Capability domain from :class:`SkillGroup`.
        description: One-liner for orchestrator to inject into persona prompt.
        version: Semantic version string, e.g. "1.0.0".
        author: Who created this skill ("system", "user", or email/username).
        tags: Freeform labels for filtering (e.g. ["email", "draft"]).
        routing_hints: Natural-language phrases that trigger this skill.

        # Level 2: Instructions (loaded when triggered)
        instructions: Skill-specific prompt guidance (progressive disclosure).
        instructions_path: Path to SKILL.md file to load instructions from.

        # Level 3: Resources (loaded on-demand)
        resources: Dict of resource name → Path for additional markdown docs.
        scripts: Dict of script name → Path for executable scripts.
        templates: Dict of template name → Path for Jinja2 templates.

        # Tool bindings (execution layer)
        tools: The actual function_tool / as_tool callables. Can be empty
            for knowledge-only skills that provide expertise without tools.
        requires_connection: Whether a connected account is needed.
        read_only: If True, skill only performs reads (safe for readonly profiles).
        error_handler: Optional callable for skill-level error formatting.

        # Relationships
        requires_skills: List of skill IDs this skill depends on.
        extends_skill: Optional skill ID this skill extends/inherits from.

        # Source tracking
        source_type: Whether this skill is CODE or FILESYSTEM based.
        source_path: Path to source (SKILL.md file for filesystem skills).

        # Lifecycle
        is_active: Whether skill is currently enabled.
        installed_at: When this skill was installed/created.
        updated_at: When this skill was last modified.
    """

    # Level 1: Metadata (always loaded)
    id: str
    group: SkillGroup
    description: str
    name: str = ""  # Human-readable name (defaults to id if empty)
    version: str = "1.0.0"
    author: str = "system"
    tags: list[str] = field(default_factory=list)
    routing_hints: list[str] = field(default_factory=list)

    # Level 2: Instructions (loaded when triggered)
    instructions: str = ""
    instructions_path: Optional[Path] = None

    # Level 3: Resources (loaded on-demand)
    resources: dict[str, Path] = field(default_factory=dict)
    scripts: dict[str, Path] = field(default_factory=dict)
    templates: dict[str, Path] = field(default_factory=dict)

    # Tool bindings (execution layer) - now optional for knowledge-only skills
    tools: list[Callable] = field(default_factory=list)
    requires_connection: bool = False
    read_only: bool = False
    error_handler: Optional[Callable] = field(default=None, repr=False)

    # Relationships
    requires_skills: list[str] = field(default_factory=list)
    extends_skill: Optional[str] = None

    # Source tracking
    source_type: SkillSourceType = SkillSourceType.CODE
    source_path: Optional[Path] = None

    # Lifecycle
    is_active: bool = True
    installed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        """Set defaults after initialization."""
        if not self.name:
            object.__setattr__(self, "name", self.id)
        if self.installed_at is None:
            object.__setattr__(self, "installed_at", datetime.now())
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.installed_at)

    def tool_names(self) -> list[str]:
        """Return the registered names of all tools in this skill."""
        names: list[str] = []
        for t in self.tools:
            name = getattr(t, "name", None) or getattr(t, "__name__", None)
            if name:
                names.append(name)
        return names

    def is_knowledge_only(self) -> bool:
        """Check if this is a knowledge-only skill (no tools)."""
        return len(self.tools) == 0

    def metadata_dict(self) -> dict:
        """Return Level 1 metadata as a dictionary (always loaded)."""
        return {
            "id": self.id,
            "name": self.name,
            "group": self.group.value,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "tags": self.tags,
            "routing_hints": self.routing_hints,
            "is_knowledge_only": self.is_knowledge_only(),
            "requires_connection": self.requires_connection,
            "read_only": self.read_only,
            "has_resources": bool(self.resources or self.scripts or self.templates),
            "source_type": self.source_type.value,
            "is_active": self.is_active,
        }

    def get_full_instructions(self) -> str:
        """Get full instructions, loading from file if needed (Level 2).

        This is called when the skill is triggered, not at startup.
        """
        if self.instructions_path and self.instructions_path.exists():
            try:
                content = self.instructions_path.read_text(encoding="utf-8")
                # Strip YAML frontmatter if present
                return self._strip_frontmatter(content)
            except Exception as e:
                logger.warning("Failed to load instructions from %s: %s", self.instructions_path, e)
                return self.instructions
        return self.instructions

    def load_resource(self, resource_name: str) -> Optional[str]:
        """Load a Level 3 resource on demand.

        Args:
            resource_name: Name of the resource to load (from resources, scripts, or templates)

        Returns:
            Content of the resource as string, or None if not found.
        """
        path = self.resources.get(resource_name) or self.scripts.get(resource_name) or self.templates.get(resource_name)
        if path and path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to load resource %s from %s: %s", resource_name, path, e)
        return None

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Strip YAML frontmatter from markdown content.

        Frontmatter looks like:
        ---
        key: value
        ---
        # Rest of content
        """
        lines = content.split("\n")
        if len(lines) >= 3 and lines[0].strip() == "---":
            # Find closing ---
            for i, line in enumerate(lines[1:], start=1):
                if line.strip() == "---":
                    return "\n".join(lines[i + 1 :]).strip()
        return content
