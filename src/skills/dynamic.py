"""Dynamic skill builder — wraps the existing ToolRegistry (CLI/function tools).

Each tool discovered from ``src/tools/plugins/*/manifest.json`` becomes its own
SkillDefinition so it gets proper routing hints and profile filtering.
"""

from __future__ import annotations

import logging
from typing import Callable

from src.skills.definition import SkillDefinition, SkillGroup

logger = logging.getLogger(__name__)


def build_dynamic_skill(
    name: str,
    description: str,
    tool: Callable,
    *,
    tags: list[str] | None = None,
) -> SkillDefinition:
    """Wrap a single dynamic tool (from ToolRegistry) as a SkillDefinition."""
    return SkillDefinition(
        id=f"dynamic_{name}",
        group=SkillGroup.DYNAMIC,
        description=description,
        tools=[tool],
        tags=tags or ["dynamic"],
    )


async def load_dynamic_skills() -> list[SkillDefinition]:
    """Load all dynamic tools from the filesystem ToolRegistry and wrap each as a skill."""
    try:
        from src.tools.registry import get_registry

        registry = await get_registry()
        await registry.load_all()

        skills: list[SkillDefinition] = []
        # Iterate all registered tools (including multi-tool function wrappers)
        for name, tool in registry._tools.items():
            # Try to find manifest — for multi-tool, the parent manifest name differs
            manifest = registry.get_manifest(name)
            if not manifest:
                # Check if this tool belongs to a parent manifest (multi-tool)
                for m_name, m in registry._manifests.items():
                    if m.type == "function" and name != m_name:
                        manifest = m
                        break
            description = manifest.description if manifest else f"Dynamic tool: {name}"
            tool_desc = getattr(tool, "description", None) or description
            tags = ["dynamic"]
            if manifest and manifest.requires_network:
                tags.append("network")
            skills.append(build_dynamic_skill(name, tool_desc, tool, tags=tags))
        if skills:
            logger.info("%d dynamic skills loaded from ToolRegistry", len(skills))
        return skills
    except Exception as e:
        logger.warning("Failed to load dynamic skills: %s", e)
        return []
