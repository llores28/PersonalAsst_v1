"""Unified skill registry — one place to register and retrieve all tools.

Replaces the ad-hoc tool assembly in ``create_orchestrator_async()`` with a
single interface that accepts skills from any source (MCP wrappers, closure
builders, CLI manifests, agent-as-tool, raw function_tool, SKILL.md files).

Phase 1+ adds:
- Filesystem-based skill registration
- Dependency resolution
- Progressive disclosure (Level 1-3 loading)
- Knowledge-only skill support
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from src.skills.definition import (
    PROFILE_ALLOWED_GROUPS,
    SkillDefinition,
    SkillGroup,
    SkillProfile,
    SkillSourceType,
)

logger = logging.getLogger(__name__)


# Stop words dropped from routing-hint keyword extraction.
# Kept deliberately small — aggressive filtering hurts recall.
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "was", "are", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "this", "that", "these", "those", "with", "when", "where", "what", "which",
    "who", "why", "how", "not", "use", "using", "from", "into", "onto", "upon",
    "only", "also", "about", "after", "before", "while", "during", "again",
    "each", "both", "more", "most", "some", "any", "few", "all", "other",
    "your", "their", "ours", "theirs", "mine", "yours", "its",
    "please", "help",
})


def _strip_plural(token: str) -> str:
    """Cheap singularization: drop common English plural suffixes.

    Keeps small/common words unchanged. Good enough for keyword matching — we
    do not need full morphology here (and we specifically avoid pulling in a
    heavy NLP dep for this path, which runs on every turn).
    """
    t = token.lower()
    if len(t) <= 3:
        return t
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    # Only collapse "-es" when the stem ends in a sibilant-style cluster where
    # English drops the whole "es" ("boxes"→"box", "matches"→"match",
    # "dishes"→"dish"). Otherwise just treat the trailing "s" as plural so
    # "subtitles" → "subtitle", not "subtitl".
    if t.endswith(("sses", "shes", "ches", "xes", "zes")) and len(t) > 4:
        return t[:-2]
    if t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


class SkillRegistry:
    """Central registry for all skills available to an agent.

    Usage::

        registry = SkillRegistry()

        # Register skills from any source
        registry.register(gmail_skill)
        registry.register(scheduler_skill)

        # Retrieve tools + prompt text for the orchestrator
        tools = registry.get_tools(profile=SkillProfile.FULL)
        instructions = registry.get_instructions(profile=SkillProfile.FULL)
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        # Lazy FTS5 index — only built when settings.skill_fts_enabled is True
        # AND match_skills is called. Rebuilt opportunistically when a new
        # skill is registered after the index already exists.
        self._fts_index = None  # type: ignore[var-annotated]
        self._fts_dirty = True

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, skill: SkillDefinition) -> None:
        """Register a skill.  Overwrites if ``skill.id`` already exists."""
        if skill.id in self._skills:
            logger.warning("Skill '%s' replaced (was already registered)", skill.id)
        self._skills[skill.id] = skill
        self._fts_dirty = True
        logger.debug(
            "Skill registered: %s (group=%s, tools=%d, source=%s)",
            skill.id,
            skill.group.value,
            len(skill.tools),
            skill.source_type.value,
        )

    def register_filesystem_skill(
        self,
        skill_path: Path,
        skill_id: Optional[str] = None,
    ) -> SkillDefinition:
        """Register a skill from a SKILL.md file on the filesystem.

        Args:
            skill_path: Path to directory containing SKILL.md or path to SKILL.md directly
            skill_id: Optional override for skill ID (defaults to name from YAML or directory name)

        Returns:
            The registered SkillDefinition

        Raises:
            SkillLoadError: If SKILL.md is missing or invalid
        """
        from src.skills.loader import SkillLoader

        loader = SkillLoader()
        skill = loader.load_from_path(skill_path, skill_id=skill_id)
        self.register(skill)
        return skill

    def register_function_skill(
        self,
        skill_id: str,
        *,
        group: SkillGroup,
        description: str,
        tools: list[Callable],
        instructions: str = "",
        routing_hints: list[str] | None = None,
        requires_connection: bool = False,
        read_only: bool = False,
        error_handler: Optional[Callable] = None,
        tags: list[str] | None = None,
    ) -> SkillDefinition:
        """Convenience: build a :class:`SkillDefinition` from keyword args and register it."""
        skill = SkillDefinition(
            id=skill_id,
            group=group,
            description=description,
            tools=list(tools),
            instructions=instructions,
            routing_hints=routing_hints or [],
            requires_connection=requires_connection,
            read_only=read_only,
            error_handler=error_handler,
            tags=tags or [],
        )
        self.register(skill)
        return skill

    def register_agent_skill(
        self,
        skill_id: str,
        *,
        agent: object,
        tool_name: str,
        tool_description: str,
        group: SkillGroup = SkillGroup.AGENT,
        instructions: str = "",
        routing_hints: list[str] | None = None,
        read_only: bool = False,
        tags: list[str] | None = None,
    ) -> SkillDefinition:
        """Register a full Agent exposed via ``agent.as_tool()``.

        Use this for agents that genuinely need their own reasoning loop
        (SchedulerAgent, RepairAgent, etc.).
        """
        as_tool = agent.as_tool(  # type: ignore[attr-defined]
            tool_name=tool_name,
            tool_description=tool_description,
        )
        skill = SkillDefinition(
            id=skill_id,
            group=group,
            description=tool_description,
            tools=[as_tool],
            instructions=instructions,
            routing_hints=routing_hints or [],
            requires_connection=False,
            read_only=read_only,
            tags=tags or [],
        )
        self.register(skill)
        return skill

    # ------------------------------------------------------------------
    # Unregistration
    # ------------------------------------------------------------------

    def unregister(self, skill_id: str) -> bool:
        """Remove a skill by ID.  Returns ``True`` if it existed."""
        removed = self._skills.pop(skill_id, None)
        if removed:
            self._fts_dirty = True
            logger.debug("Skill unregistered: %s", skill_id)
        return removed is not None

    # ------------------------------------------------------------------
    # FTS5 hybrid retrieval (Wave A.2 / 4.9)
    # ------------------------------------------------------------------

    def _ensure_fts_index(self):
        """Build (or rebuild) the FTS5 index from current skills.

        Lazy: only runs when match_skills is called AND settings.skill_fts_enabled
        is True. Cheap to rebuild — a few hundred skills index in single-digit
        milliseconds, and we only do it when the dirty flag is set.
        """
        from src.skills.fts5_index import SkillFTS5Index, IndexedSkill
        if self._fts_index is None:
            self._fts_index = SkillFTS5Index()
        if self._fts_dirty:
            indexed = [
                IndexedSkill(
                    skill_id=s.id,
                    name=getattr(s, "name", s.id),
                    description=s.description or "",
                    tags=list(s.tags or []),
                    routing_hints=list(s.routing_hints or []),
                    instructions=s.instructions or "",
                )
                for s in self._skills.values()
                if s.is_active
            ]
            self._fts_index.rebuild(indexed)
            self._fts_dirty = False
        return self._fts_index

    def _fts_match_ids(self, user_message: str, top_k: int) -> set[str]:
        """Return skill IDs that the FTS5 index ranks against the message.
        Empty set on any failure — never raises so the keyword matcher
        always wins as a fallback."""
        try:
            index = self._ensure_fts_index()
            results = index.query(user_message, top_k=top_k)
            return {r.skill_id for r in results if r.skill_id in self._skills}
        except Exception as exc:  # noqa: BLE001 — index is best-effort
            logger.debug("FTS5 skill match failed (degrading to keyword): %s", exc)
            return set()

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_skill(self, skill_id: str) -> Optional[SkillDefinition]:
        """Look up a single skill by ID."""
        return self._skills.get(skill_id)

    def get_tools(
        self,
        profile: SkillProfile = SkillProfile.FULL,
        *,
        include_groups: frozenset[SkillGroup] | None = None,
        exclude_ids: frozenset[str] | None = None,
    ) -> list[Callable]:
        """Return the flat list of tool callables permitted by *profile*.

        Args:
            profile: Pre-built allowlist.  ``FULL`` returns everything.
            include_groups: If provided, overrides profile group filtering.
            exclude_ids: Skill IDs to explicitly exclude.
        """
        allowed_groups = include_groups or PROFILE_ALLOWED_GROUPS.get(profile)
        exclude = exclude_ids or frozenset()
        tools: list[Callable] = []
        for skill in self._skills.values():
            if skill.id in exclude:
                continue
            if allowed_groups is not None and skill.group not in allowed_groups:
                continue
            tools.extend(skill.tools)
        return tools

    def get_instructions(
        self,
        profile: SkillProfile = SkillProfile.FULL,
        *,
        include_groups: frozenset[SkillGroup] | None = None,
        exclude_ids: frozenset[str] | None = None,
        progressive: bool = True,
    ) -> str:
        """Build aggregated prompt instructions from active skills.

        Args:
            profile: Tool allowlist profile
            include_groups: Override profile group filtering
            exclude_ids: Skill IDs to explicitly exclude
            progressive: If True, load Level 2 instructions from files when needed

        Returns:
            String with routing rules and skill instructions
        """
        allowed_groups = include_groups or PROFILE_ALLOWED_GROUPS.get(profile)
        exclude = exclude_ids or frozenset()

        sections: list[str] = []
        routing_lines: list[str] = []

        for skill in self._skills.values():
            if skill.id in exclude:
                continue
            if allowed_groups is not None and skill.group not in allowed_groups:
                continue
            if not skill.is_active:
                continue

            # Progressive disclosure: load Level 2 instructions when needed
            instructions = skill.get_full_instructions() if progressive else skill.instructions
            if instructions:
                sections.append(
                    f"### Skill: {skill.name}\n{instructions}"
                )

            for hint in skill.routing_hints:
                if skill.is_knowledge_only():
                    routing_lines.append(f"- {hint} → (knowledge skill: {skill.name})")
                else:
                    tool_names = ", ".join(f"`{n}`" for n in skill.tool_names()) or skill.id
                    routing_lines.append(f"- {hint} → {tool_names}")

        parts: list[str] = []
        if routing_lines:
            parts.append("## Tool Routing Rules\n" + "\n".join(routing_lines))
        if sections:
            parts.append("## Skill Instructions\n" + "\n\n".join(sections))

        return "\n\n".join(parts)

    def match_skills(self, user_message: str) -> frozenset[str]:
        """Lightweight keyword classifier: return skill IDs relevant to *user_message*.

        Inspired by OpenClaw's selective skill injection — only inject skills
        that match the current turn instead of loading all tools every time.

        Rules:
        - INTERNAL skills (memory, scheduler) are always included (lightweight).
        - Tags are high-confidence signals (curated single keywords): exact or
          plural-stripped match against any message token selects the skill.
        - Routing hints are matched on whole meaningful tokens (length > 3, not
          in the stop-word list). Plural forms are stripped so "videos" matches
          a hint containing "video".
        - If no user/workspace skill matches, the full active set is returned
          so we never starve the model of context.
        """
        lowered = user_message.lower()
        # Normalize tokens in the message once (strip punctuation, drop plurals)
        message_tokens = {
            _strip_plural(tok.strip("',\"():.?!"))
            for tok in lowered.replace("/", " ").split()
            if tok.strip("',\"():.?!")
        }

        matched: set[str] = set()

        for skill in self._skills.values():
            if not skill.is_active:
                continue

            # Always include internal skills — they're small and always useful
            if skill.group == SkillGroup.INTERNAL:
                matched.add(skill.id)
                continue

            # 1. Tags (high-confidence): single-word curated keywords.
            if skill.tags:
                tag_tokens = {_strip_plural(t.lower().strip()) for t in skill.tags if t}
                if tag_tokens & message_tokens:
                    matched.add(skill.id)
                    continue

            # 2. Routing hints (lower-confidence): token overlap on content words.
            for hint in skill.routing_hints:
                hint_lower = hint.split("→")[0].lower()
                keywords = {
                    _strip_plural(w.strip("',\"():.?!"))
                    for w in hint_lower.split()
                    if len(w.strip("',\"():.?!")) > 3
                    and w.strip("',\"():.?!").lower() not in _STOP_WORDS
                }
                if keywords & message_tokens:
                    matched.add(skill.id)
                    break

        # Wave A.2 — hybrid retrieval. When enabled, union the keyword-tag
        # matches with FTS5 BM25-ranked hits so fuzzy phrasings still surface
        # the right skill. Off by default; enables with skill_fts_enabled=True.
        try:
            from src.settings import settings as _settings
            if getattr(_settings, "skill_fts_enabled", False):
                top_k = int(getattr(_settings, "skill_fts_top_k", 5) or 5)
                fts_ids = self._fts_match_ids(user_message, top_k=top_k)
                # Honor the workspace-cohesion + INTERNAL rules below by
                # merging into ``matched`` BEFORE the fallback check.
                matched.update(fts_ids)
        except Exception as exc:  # noqa: BLE001 — settings/import errors degrade quietly
            logger.debug("FTS5 hybrid retrieval skipped: %s", exc)

        # Fallback: if nothing matched (e.g. generic greeting), load all active
        active_ids = {s.id for s in self._skills.values() if s.is_active}
        internal_only = {s.id for s in self._skills.values() if s.group == SkillGroup.INTERNAL and s.is_active}
        if not matched or matched == internal_only:
            return frozenset(active_ids)

        # Workspace cohesion: if ANY Google Workspace skill matched, include ALL
        workspace_matched = any(
            self._skills[sid].group == SkillGroup.GOOGLE_WORKSPACE
            for sid in matched
            if sid in self._skills
        )
        if workspace_matched:
            for skill in self._skills.values():
                if skill.group == SkillGroup.GOOGLE_WORKSPACE and skill.is_active:
                    matched.add(skill.id)

        logger.debug("Selective skills for '%s...': %s", user_message[:40], sorted(matched))
        return frozenset(matched)

    def get_tools_selective(
        self,
        user_message: str,
        profile: SkillProfile = SkillProfile.FULL,
    ) -> list[Callable]:
        """Return only tools from skills relevant to *user_message*.

        Falls back to full tool set if no skills match.
        Includes knowledge-only skills (no tools) in routing but not output.
        """
        matched_ids = self.match_skills(user_message)
        allowed_groups = PROFILE_ALLOWED_GROUPS.get(profile)
        tools: list[Callable] = []
        for skill in self._skills.values():
            if skill.id not in matched_ids:
                continue
            if not skill.is_active:
                continue
            if allowed_groups is not None and skill.group not in allowed_groups:
                continue
            tools.extend(skill.tools)
        return tools

    def get_instructions_selective(
        self,
        user_message: str,
        profile: SkillProfile = SkillProfile.FULL,
    ) -> str:
        """Build prompt instructions only for skills relevant to *user_message*."""
        matched_ids = self.match_skills(user_message)
        allowed_groups = PROFILE_ALLOWED_GROUPS.get(profile)

        sections: list[str] = []
        routing_lines: list[str] = []

        for skill in self._skills.values():
            if skill.id not in matched_ids:
                continue
            if allowed_groups is not None and skill.group not in allowed_groups:
                continue

            if skill.instructions:
                sections.append(f"### Skill: {skill.id}\n{skill.instructions}")

            for hint in skill.routing_hints:
                tool_names = ", ".join(f"`{n}`" for n in skill.tool_names()) or skill.id
                routing_lines.append(f"- {hint} → {tool_names}")

        parts: list[str] = []
        if routing_lines:
            parts.append("## Tool Routing Rules\n" + "\n".join(routing_lines))
        if sections:
            parts.append("## Skill Instructions\n" + "\n\n".join(sections))

        return "\n\n".join(parts)

    def list_skills(
        self,
        profile: SkillProfile = SkillProfile.FULL,
        *,
        include_inactive: bool = False,
    ) -> list[dict]:
        """Return skill metadata (Level 1) for introspection / debugging / dashboard."""
        allowed_groups = PROFILE_ALLOWED_GROUPS.get(profile)
        result: list[dict] = []
        for skill in self._skills.values():
            if not include_inactive and not skill.is_active:
                continue
            if allowed_groups is not None and skill.group not in allowed_groups:
                continue
            result.append(skill.metadata_dict())
        return result

    # ------------------------------------------------------------------
    # Dependency Resolution
    # ------------------------------------------------------------------

    def resolve_dependencies(self, skill_id: str) -> list[SkillDefinition]:
        """Resolve and order all dependencies for a skill.

        Returns topologically sorted list with dependencies first.
        Raises ValueError if circular dependencies detected or missing deps.
        """
        if skill_id not in self._skills:
            raise ValueError(f"Skill '{skill_id}' not found in registry")

        visited: set[str] = set()
        temp_mark: set[str] = set()  # For cycle detection
        result: list[SkillDefinition] = []

        def visit(sid: str) -> None:
            if sid in temp_mark:
                raise ValueError(f"Circular dependency detected involving skill '{sid}'")
            if sid in visited:
                return

            temp_mark.add(sid)
            skill = self._skills.get(sid)
            if skill:
                # Visit dependencies first
                for dep_id in skill.requires_skills:
                    if dep_id not in self._skills:
                        raise ValueError(f"Skill '{sid}' requires missing dependency '{dep_id}'")
                    visit(dep_id)
            temp_mark.remove(sid)
            visited.add(sid)
            if skill:
                result.append(skill)

        visit(skill_id)
        return result

    def activate_skill(self, skill_id: str) -> list[SkillDefinition]:
        """Activate a skill and all its dependencies.

        Marks skills as is_active=True in dependency order.
        Returns the ordered list of activated skills.
        """
        ordered = self.resolve_dependencies(skill_id)
        for skill in ordered:
            if not skill.is_active:
                skill.is_active = True
                skill.updated_at = datetime.now()
                logger.info("Activated skill: %s", skill.id)
        return ordered

    def deactivate_skill(self, skill_id: str, cascade: bool = False) -> list[SkillDefinition]:
        """Deactivate a skill.

        Args:
            skill_id: Skill to deactivate
            cascade: If True, also deactivate skills that depend on this one

        Returns:
            List of deactivated skills
        """
        if skill_id not in self._skills:
            raise ValueError(f"Skill '{skill_id}' not found")

        deactivated: list[SkillDefinition] = []
        skill = self._skills[skill_id]

        if cascade:
            # Find all skills that depend on this one
            dependent_ids: set[str] = set()
            for sid, s in self._skills.items():
                if skill_id in s.requires_skills:
                    dependent_ids.add(sid)

            # Deactivate dependents first (reverse dependency order)
            for dep_id in dependent_ids:
                dep = self._skills[dep_id]
                if dep.is_active:
                    dep.is_active = False
                    dep.updated_at = datetime.now()
                    deactivated.append(dep)
                    logger.info("Deactivated dependent skill: %s", dep_id)

        if skill.is_active:
            skill.is_active = False
            skill.updated_at = datetime.now()
            deactivated.append(skill)
            logger.info("Deactivated skill: %s", skill_id)

        return deactivated

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def __repr__(self) -> str:
        ids = ", ".join(sorted(self._skills))
        return f"SkillRegistry([{ids}])"
