"""Tests for the unified skill registry contract (Phase 1)."""

import sys
from unittest.mock import MagicMock

# Ensure agents package is available for import even without the real SDK
if "agents" not in sys.modules:
    sys.modules["agents"] = MagicMock()

import pytest

from src.skills.definition import (
    PROFILE_ALLOWED_GROUPS,
    SkillDefinition,
    SkillGroup,
    SkillProfile,
)
from src.skills.registry import SkillRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_tool(name: str) -> MagicMock:
    """Create a mock tool with a .name attribute."""
    t = MagicMock()
    t.name = name
    return t


def _make_skill(
    skill_id: str = "test_skill",
    group: SkillGroup = SkillGroup.INTERNAL,
    tools: list | None = None,
    instructions: str = "",
    routing_hints: list[str] | None = None,
    read_only: bool = False,
    tags: list[str] | None = None,
) -> SkillDefinition:
    return SkillDefinition(
        id=skill_id,
        group=group,
        description=f"Test skill {skill_id}",
        tools=tools or [_make_tool(f"{skill_id}_tool")],
        instructions=instructions,
        routing_hints=routing_hints or [],
        read_only=read_only,
        tags=tags or [],
    )


# ---------------------------------------------------------------------------
# SkillDefinition
# ---------------------------------------------------------------------------

class TestSkillDefinition:
    """Contract tests for SkillDefinition dataclass."""

    def test_minimal_creation(self) -> None:
        skill = SkillDefinition(
            id="gmail",
            group=SkillGroup.GOOGLE_WORKSPACE,
            description="Gmail tools",
        )
        assert skill.id == "gmail"
        assert skill.group == SkillGroup.GOOGLE_WORKSPACE
        assert skill.tools == []
        assert skill.instructions == ""
        assert skill.routing_hints == []
        assert skill.requires_connection is False
        assert skill.read_only is False
        assert skill.error_handler is None
        assert skill.tags == []

    def test_full_creation(self) -> None:
        tool = _make_tool("search_gmail")
        handler = MagicMock()
        skill = SkillDefinition(
            id="gmail",
            group=SkillGroup.GOOGLE_WORKSPACE,
            description="Gmail tools",
            tools=[tool],
            instructions="Use Gmail tools for email.",
            routing_hints=["email", "inbox"],
            requires_connection=True,
            read_only=False,
            error_handler=handler,
            tags=["email", "workspace"],
        )
        assert len(skill.tools) == 1
        assert skill.requires_connection is True
        assert skill.error_handler is handler
        assert skill.tags == ["email", "workspace"]

    def test_tool_names_extracts_name_attr(self) -> None:
        tools = [_make_tool("search"), _make_tool("send")]
        skill = _make_skill(tools=tools)
        assert skill.tool_names() == ["search", "send"]

    def test_tool_names_empty_when_no_tools(self) -> None:
        skill = SkillDefinition(
            id="empty",
            group=SkillGroup.INTERNAL,
            description="No tools",
            tools=[],
        )
        assert skill.tool_names() == []

    def test_skill_definition_is_mutable(self) -> None:
        # SkillDefinition is intentionally a regular @dataclass (not frozen) —
        # the dashboard's skill-edit endpoint
        # (src/orchestration/api.py:4573) mutates `name`, `description`, and
        # `tags` in place when the user edits a skill. If you reintroduce
        # `frozen=True`, that flow breaks. This test pins that contract.
        skill = _make_skill()
        skill.id = "changed"  # must not raise
        assert skill.id == "changed"
        skill.tags = ["new", "tags"]
        assert skill.tags == ["new", "tags"]


# ---------------------------------------------------------------------------
# SkillGroup / SkillProfile enums
# ---------------------------------------------------------------------------

class TestEnums:
    def test_skill_groups_are_strings(self) -> None:
        assert SkillGroup.GOOGLE_WORKSPACE == "google_workspace"
        assert SkillGroup.INTERNAL == "internal"
        assert SkillGroup.DYNAMIC == "dynamic"
        assert SkillGroup.MCP == "mcp"
        assert SkillGroup.AGENT == "agent"

    def test_skill_profiles_are_strings(self) -> None:
        assert SkillProfile.FULL == "full"
        assert SkillProfile.READONLY == "readonly"
        assert SkillProfile.MINIMAL == "minimal"

    def test_profile_allowed_groups_covers_all_profiles(self) -> None:
        for p in SkillProfile:
            assert p in PROFILE_ALLOWED_GROUPS

    def test_full_profile_has_no_restriction(self) -> None:
        assert PROFILE_ALLOWED_GROUPS[SkillProfile.FULL] is None

    def test_minimal_profile_blocks_everything(self) -> None:
        assert PROFILE_ALLOWED_GROUPS[SkillProfile.MINIMAL] == frozenset()


# ---------------------------------------------------------------------------
# SkillRegistry — registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_and_retrieve(self) -> None:
        reg = SkillRegistry()
        skill = _make_skill("gmail")
        reg.register(skill)
        assert "gmail" in reg
        assert len(reg) == 1
        assert reg.get_skill("gmail") is skill

    def test_register_overwrites_duplicate(self) -> None:
        reg = SkillRegistry()
        skill_v1 = _make_skill("gmail", instructions="v1")
        skill_v2 = _make_skill("gmail", instructions="v2")
        reg.register(skill_v1)
        reg.register(skill_v2)
        assert len(reg) == 1
        assert reg.get_skill("gmail").instructions == "v2"

    def test_register_function_skill(self) -> None:
        reg = SkillRegistry()
        tools = [_make_tool("search"), _make_tool("send")]
        skill = reg.register_function_skill(
            "gmail",
            group=SkillGroup.GOOGLE_WORKSPACE,
            description="Gmail",
            tools=tools,
            instructions="Use Gmail tools for email.",
            routing_hints=["email", "inbox"],
            requires_connection=True,
            tags=["email"],
        )
        assert skill.id == "gmail"
        assert skill.group == SkillGroup.GOOGLE_WORKSPACE
        assert len(skill.tools) == 2
        assert skill.requires_connection is True
        assert "gmail" in reg

    def test_register_agent_skill(self) -> None:
        mock_agent = MagicMock()
        mock_as_tool = MagicMock()
        mock_as_tool.name = "manage_schedules"
        mock_agent.as_tool.return_value = mock_as_tool

        reg = SkillRegistry()
        skill = reg.register_agent_skill(
            "scheduler",
            agent=mock_agent,
            tool_name="manage_schedules",
            tool_description="Manage schedules",
            group=SkillGroup.AGENT,
            routing_hints=["reminder", "schedule"],
            tags=["scheduling"],
        )
        mock_agent.as_tool.assert_called_once_with(
            tool_name="manage_schedules",
            tool_description="Manage schedules",
        )
        assert skill.id == "scheduler"
        assert len(skill.tools) == 1
        assert skill.tools[0] is mock_as_tool
        assert "scheduler" in reg

    def test_unregister_existing(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill("gmail"))
        assert reg.unregister("gmail") is True
        assert "gmail" not in reg
        assert len(reg) == 0

    def test_unregister_missing_returns_false(self) -> None:
        reg = SkillRegistry()
        assert reg.unregister("nonexistent") is False

    def test_get_skill_returns_none_for_missing(self) -> None:
        reg = SkillRegistry()
        assert reg.get_skill("nope") is None


# ---------------------------------------------------------------------------
# SkillRegistry — get_tools with profiles
# ---------------------------------------------------------------------------

class TestGetTools:
    def _populated_registry(self) -> SkillRegistry:
        reg = SkillRegistry()
        reg.register(_make_skill("gmail", group=SkillGroup.GOOGLE_WORKSPACE))
        reg.register(_make_skill("calendar", group=SkillGroup.GOOGLE_WORKSPACE))
        reg.register(_make_skill("scheduler", group=SkillGroup.AGENT))
        reg.register(_make_skill("memory", group=SkillGroup.INTERNAL))
        reg.register(_make_skill("stock_checker", group=SkillGroup.DYNAMIC))
        return reg

    def test_full_profile_returns_all(self) -> None:
        reg = self._populated_registry()
        tools = reg.get_tools(SkillProfile.FULL)
        assert len(tools) == 5

    def test_workspace_only_filters_to_google(self) -> None:
        reg = self._populated_registry()
        tools = reg.get_tools(SkillProfile.WORKSPACE_ONLY)
        assert len(tools) == 2

    def test_internal_only_filters_to_internal(self) -> None:
        reg = self._populated_registry()
        tools = reg.get_tools(SkillProfile.INTERNAL_ONLY)
        assert len(tools) == 1

    def test_readonly_excludes_dynamic_and_agent(self) -> None:
        reg = self._populated_registry()
        tools = reg.get_tools(SkillProfile.READONLY)
        # google_workspace (2) + internal (1) + mcp (0) = 3
        assert len(tools) == 3

    def test_minimal_returns_nothing(self) -> None:
        reg = self._populated_registry()
        tools = reg.get_tools(SkillProfile.MINIMAL)
        assert len(tools) == 0

    def test_include_groups_overrides_profile(self) -> None:
        reg = self._populated_registry()
        tools = reg.get_tools(
            SkillProfile.MINIMAL,
            include_groups=frozenset({SkillGroup.DYNAMIC}),
        )
        assert len(tools) == 1

    def test_exclude_ids(self) -> None:
        reg = self._populated_registry()
        tools = reg.get_tools(
            SkillProfile.FULL,
            exclude_ids=frozenset({"gmail", "scheduler"}),
        )
        assert len(tools) == 3

    def test_multi_tool_skill_returns_all_tools(self) -> None:
        reg = SkillRegistry()
        tools = [_make_tool("t1"), _make_tool("t2"), _make_tool("t3")]
        reg.register(_make_skill("multi", tools=tools))
        assert len(reg.get_tools()) == 3


# ---------------------------------------------------------------------------
# SkillRegistry — get_instructions
# ---------------------------------------------------------------------------

class TestGetInstructions:
    def test_includes_routing_hints(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill(
            "gmail",
            group=SkillGroup.GOOGLE_WORKSPACE,
            routing_hints=["email requests", "inbox checks"],
        ))
        text = reg.get_instructions()
        assert "## Tool Routing Rules" in text
        assert "email requests" in text
        assert "inbox checks" in text
        assert "`gmail_tool`" in text

    def test_includes_instructions(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill(
            "gmail",
            instructions="Use Gmail skill tools for all email work.",
        ))
        text = reg.get_instructions()
        assert "## Skill Instructions" in text
        assert "### Skill: gmail" in text
        assert "Use Gmail skill tools for all email work." in text

    def test_empty_registry_returns_empty_string(self) -> None:
        reg = SkillRegistry()
        assert reg.get_instructions() == ""

    def test_profile_filters_instructions(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill(
            "gmail",
            group=SkillGroup.GOOGLE_WORKSPACE,
            instructions="Gmail stuff",
        ))
        reg.register(_make_skill(
            "scheduler",
            group=SkillGroup.AGENT,
            instructions="Scheduler stuff",
        ))
        text = reg.get_instructions(SkillProfile.WORKSPACE_ONLY)
        assert "Gmail stuff" in text
        assert "Scheduler stuff" not in text

    def test_exclude_ids_filters_instructions(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill("a", instructions="AAA"))
        reg.register(_make_skill("b", instructions="BBB"))
        text = reg.get_instructions(exclude_ids=frozenset({"a"}))
        assert "AAA" not in text
        assert "BBB" in text


# ---------------------------------------------------------------------------
# SkillRegistry — list_skills
# ---------------------------------------------------------------------------

class TestListSkills:
    def test_list_skills_returns_metadata(self) -> None:
        # `metadata_dict()` exposes Level-1 metadata (always-loaded). Tool
        # count is intentionally NOT in this dict — it's only relevant when
        # the skill is fully loaded for invocation (Level 2/3). If you need
        # to add tool_count back, update both `metadata_dict()` and this
        # test together.
        reg = SkillRegistry()
        reg.register(_make_skill(
            "gmail",
            group=SkillGroup.GOOGLE_WORKSPACE,
            tags=["email"],
        ))
        items = reg.list_skills()
        assert len(items) == 1
        item = items[0]
        assert item["id"] == "gmail"
        assert item["group"] == "google_workspace"
        assert item["tags"] == ["email"]
        # Spot-check a few of the other documented Level-1 keys.
        assert "name" in item
        assert "description" in item
        assert "version" in item
        assert "is_active" in item

    def test_list_skills_respects_profile(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill("gmail", group=SkillGroup.GOOGLE_WORKSPACE))
        reg.register(_make_skill("dyn", group=SkillGroup.DYNAMIC))
        items = reg.list_skills(SkillProfile.WORKSPACE_ONLY)
        assert len(items) == 1
        assert items[0]["id"] == "gmail"


# ---------------------------------------------------------------------------
# SkillRegistry — dunder
# ---------------------------------------------------------------------------

class TestDunder:
    def test_len(self) -> None:
        reg = SkillRegistry()
        assert len(reg) == 0
        reg.register(_make_skill("a"))
        assert len(reg) == 1

    def test_contains(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill("a"))
        assert "a" in reg
        assert "b" not in reg

    def test_repr(self) -> None:
        reg = SkillRegistry()
        reg.register(_make_skill("b"))
        reg.register(_make_skill("a"))
        assert repr(reg) == "SkillRegistry([a, b])"
