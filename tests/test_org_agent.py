"""Tests for organization management tools (Phase C bridge).

Covers: 13 tools, error handler, single-session pattern, input validation,
scheduling bridge, and tool creation bridge.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure agents package is available for import even without the real SDK
if "agents" not in sys.modules:
    from typing import Generic, TypeVar
    _T = TypeVar("_T")

    class _FakeRunContextWrapper(Generic[_T]):
        """Subscriptable stand-in for RunContextWrapper."""
        pass

    _mock_agents = MagicMock()
    # Make function_tool act as a passthrough decorator that accepts kwargs
    def _mock_function_tool(fn=None, **kwargs):
        if fn is not None:
            return fn
        return lambda f: f
    _mock_agents.function_tool = _mock_function_tool
    _mock_agents.RunContextWrapper = _FakeRunContextWrapper
    sys.modules["agents"] = _mock_agents

import pytest

from src.skills.definition import SkillGroup
from src.skills.internal import build_organization_skill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_org(id=1, name="Test Org", goal="Test goal", status="active", owner_user_id=42):
    org = MagicMock()
    org.id = id
    org.name = name
    org.goal = goal
    org.status = status
    org.owner_user_id = owner_user_id
    org.description = "Test description"
    org.created_at = MagicMock()
    org.created_at.strftime = MagicMock(return_value="2026-04-08")
    return org


def _make_agent(id=1, org_id=1, name="Researcher", role="researcher", status="active"):
    agent = MagicMock()
    agent.id = id
    agent.org_id = org_id
    agent.name = name
    agent.role = role
    agent.status = status
    agent.created_at = MagicMock()
    return agent


def _make_task(id=1, org_id=1, title="Write report", priority="medium", status="pending", agent_id=None):
    task = MagicMock()
    task.id = id
    task.org_id = org_id
    task.title = title
    task.priority = priority
    task.status = status
    task.agent_id = agent_id
    task.created_at = MagicMock()
    task.completed_at = None
    return task


# ---------------------------------------------------------------------------
# Skill definition tests
# ---------------------------------------------------------------------------

class TestOrganizationSkillDefinition:
    """Verify the skill is correctly structured."""

    def test_skill_creation(self) -> None:
        skill = build_organization_skill(user_id=42)
        assert skill.id == "organizations"
        assert skill.group == SkillGroup.INTERNAL
        assert len(skill.tools) == 14
        assert skill.read_only is False

    def test_skill_has_routing_hints(self) -> None:
        skill = build_organization_skill(user_id=42)
        assert len(skill.routing_hints) > 0
        hints_text = " ".join(skill.routing_hints).lower()
        assert "organization" in hints_text
        assert "task" in hints_text
        assert "schedule" in hints_text
        assert "tool" in hints_text

    def test_skill_has_tags(self) -> None:
        skill = build_organization_skill(user_id=42)
        assert "organization" in skill.tags
        assert "project" in skill.tags
        assert "cron" in skill.tags
        assert "cli" in skill.tags

    def test_skill_has_instructions(self) -> None:
        skill = build_organization_skill(user_id=42)
        assert "list_organizations" in skill.instructions
        assert "find_organization" in skill.instructions
        assert "schedule_org_task" in skill.instructions
        assert "create_org_tool" in skill.instructions
        assert "Google Tasks" in skill.instructions


# ---------------------------------------------------------------------------
# Error handler test
# ---------------------------------------------------------------------------

class TestErrorHandler:
    def test_error_handler_returns_user_friendly_message(self) -> None:
        from src.agents.org_agent import _org_tool_error
        result = _org_tool_error(MagicMock(), ValueError("test error"))
        assert "Organization tool encountered an error" in result
        assert "ValueError" in result
        assert "test error" in result


# ---------------------------------------------------------------------------
# Tool function tests (mocked DB)
# ---------------------------------------------------------------------------

class TestListOrganizations:
    @pytest.mark.asyncio
    async def test_no_orgs_returns_prompt(self) -> None:
        tools = _get_tools()
        list_orgs = tools["list_organizations"]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        with _mock_session(execute_return=mock_result):
            result = await list_orgs()
        assert "don't have any organizations" in result

    @pytest.mark.asyncio
    async def test_lists_existing_orgs(self) -> None:
        tools = _get_tools()
        list_orgs = tools["list_organizations"]

        org = _make_org(name="Job Search")
        orgs_result = MagicMock()
        orgs_result.scalars.return_value.all.return_value = [org]

        counts_result = MagicMock()
        counts_result.all.return_value = [(org.id, 3)]

        with _mock_session(execute_returns=[orgs_result, counts_result, counts_result, counts_result]):
            result = await list_orgs()
        assert "Job Search" in result
        assert "1 organization" in result


class TestCreateOrganization:
    @pytest.mark.asyncio
    async def test_create_org(self) -> None:
        tools = _get_tools()
        create_org = tools["create_organization"]

        with _mock_session():
            result = await create_org(name="Marketing Team", goal="Grow audience")
        assert "Marketing Team" in result
        assert "Grow audience" in result

    @pytest.mark.asyncio
    async def test_create_org_empty_name(self) -> None:
        tools = _get_tools()
        create_org = tools["create_organization"]

        with _mock_session():
            result = await create_org(name="", goal="Some goal")
        assert "cannot be empty" in result

    @pytest.mark.asyncio
    async def test_create_org_empty_goal(self) -> None:
        tools = _get_tools()
        create_org = tools["create_organization"]

        with _mock_session():
            result = await create_org(name="Valid Name", goal="")
        assert "cannot be empty" in result


class TestFindOrganization:
    @pytest.mark.asyncio
    async def test_find_org_no_query(self) -> None:
        tools = _get_tools()
        find_org = tools["find_organization"]

        result = await find_org(name_query="")
        assert "Provide part of the organization name" in result

    @pytest.mark.asyncio
    async def test_find_org_single_match(self) -> None:
        tools = _get_tools()
        find_org = tools["find_organization"]

        org = _make_org(id=7, name="DevOps")
        match_result = MagicMock()
        match_result.scalars.return_value.all.return_value = [org]

        with _mock_session(execute_return=match_result):
            result = await find_org(name_query="Dev")
        assert "DevOps" in result
        assert "`7`" in result

    @pytest.mark.asyncio
    async def test_find_org_multiple_matches(self) -> None:
        tools = _get_tools()
        find_org = tools["find_organization"]

        org_a = _make_org(id=7, name="DevOps")
        org_b = _make_org(id=8, name="Dev Tools")
        match_result = MagicMock()
        match_result.scalars.return_value.all.return_value = [org_a, org_b]

        with _mock_session(execute_return=match_result):
            result = await find_org(name_query="Dev")
        assert "organizations matching 'Dev'" in result
        assert "`7`" in result
        assert "`8`" in result


class TestUpdateOrganization:
    @pytest.mark.asyncio
    async def test_update_not_found(self) -> None:
        tools = _get_tools()
        update_org = tools["update_organization"]

        with _mock_session(get_return=None):
            result = await update_org(org_id=999, name="New Name")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_update_invalid_status(self) -> None:
        tools = _get_tools()
        update_org = tools["update_organization"]

        result = await update_org(org_id=1, status="invalid")
        assert "must be" in result

    @pytest.mark.asyncio
    async def test_update_success(self) -> None:
        tools = _get_tools()
        update_org = tools["update_organization"]

        org = _make_org(name="Old Name")
        with _mock_session(get_return=org):
            result = await update_org(org_id=1, name="New Name", goal="New Goal")
        assert "Updated" in result
        assert "New Name" in result

    @pytest.mark.asyncio
    async def test_update_nothing_to_change(self) -> None:
        tools = _get_tools()
        update_org = tools["update_organization"]

        org = _make_org()
        with _mock_session(get_return=org):
            result = await update_org(org_id=1)
        assert "Nothing to update" in result


class TestGetOrganizationStatus:
    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        tools = _get_tools()
        get_status = tools["get_organization_status"]

        with _mock_session(get_return=None):
            result = await get_status(org_id=999)
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_returns_status(self) -> None:
        tools = _get_tools()
        get_status = tools["get_organization_status"]

        org = _make_org(name="Dev Team")
        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = [_make_agent()]
        tasks_result = MagicMock()
        tasks_result.scalars.return_value.all.return_value = [_make_task()]

        with _mock_session(get_return=org, execute_returns=[agents_result, tasks_result]):
            result = await get_status(org_id=1)
        assert "Dev Team" in result
        assert "Researcher" in result


class TestAddOrgAgent:
    @pytest.mark.asyncio
    async def test_add_agent_to_nonexistent_org(self) -> None:
        tools = _get_tools()
        add_agent = tools["add_org_agent"]

        with _mock_session(get_return=None):
            result = await add_agent(org_id=999, name="Writer", role="writer")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_add_agent_success(self) -> None:
        tools = _get_tools()
        add_agent = tools["add_org_agent"]

        org = _make_org()
        with _mock_session(get_return=org):
            result = await add_agent(org_id=1, name="Writer", role="writer")
        assert "Writer" in result
        assert "writer" in result


class TestAddOrgTask:
    @pytest.mark.asyncio
    async def test_add_task_invalid_priority_defaults(self) -> None:
        tools = _get_tools()
        add_task = tools["add_org_task"]

        org = _make_org()
        with _mock_session(get_return=org):
            result = await add_task(org_id=1, title="Fix bug", priority="invalid")
        assert "medium" in result

    @pytest.mark.asyncio
    async def test_add_task_success(self) -> None:
        tools = _get_tools()
        add_task = tools["add_org_task"]

        org = _make_org()
        with _mock_session(get_return=org):
            result = await add_task(org_id=1, title="Write docs", priority="high")
        assert "Write docs" in result
        assert "high" in result


class TestAssignOrgTask:
    @pytest.mark.asyncio
    async def test_assign_nonexistent_org(self) -> None:
        tools = _get_tools()
        assign_task = tools["assign_org_task"]

        with _mock_session(get_return=None):
            result = await assign_task(org_id=999, task_id=1, agent_id=1)
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_assign_task_success(self) -> None:
        tools = _get_tools()
        assign_task = tools["assign_org_task"]

        org = _make_org(id=1)
        task = _make_task(id=10, org_id=1)
        agent = _make_agent(id=20, org_id=1, name="Analyst")
        with _mock_session(get_returns={1: org, 10: task, 20: agent}):
            result = await assign_task(org_id=1, task_id=10, agent_id=20)
        assert "Analyst" in result
        assert "Assigned" in result


class TestCompleteOrgTask:
    @pytest.mark.asyncio
    async def test_complete_nonexistent_task(self) -> None:
        tools = _get_tools()
        complete_task = tools["complete_org_task"]

        org = _make_org()
        with _mock_session(get_returns={1: org, 999: None}):
            result = await complete_task(org_id=1, task_id=999)
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_complete_already_completed(self) -> None:
        tools = _get_tools()
        complete_task = tools["complete_org_task"]

        org = _make_org(id=1)
        task = _make_task(id=10, org_id=1, status="completed")
        with _mock_session(get_returns={1: org, 10: task}):
            result = await complete_task(org_id=1, task_id=10)
        assert "already completed" in result


class TestScheduleOrgTask:
    @pytest.mark.asyncio
    async def test_invalid_schedule_type(self) -> None:
        tools = _get_tools()
        schedule = tools["schedule_org_task"]

        result = await schedule(
            org_id=1, description="Test", schedule_type="invalid",
            message="Hello",
        )
        assert "must be" in result

    @pytest.mark.asyncio
    async def test_schedule_nonexistent_org(self) -> None:
        tools = _get_tools()
        schedule = tools["schedule_org_task"]

        with _mock_session(get_return=None):
            result = await schedule(
                org_id=999, description="Test", schedule_type="cron",
                message="Hello",
            )
        assert "not found" in result


class TestListOrgSchedules:
    @pytest.mark.asyncio
    async def test_list_nonexistent_org(self) -> None:
        tools = _get_tools()
        list_sched = tools["list_org_schedules"]

        with _mock_session(get_return=None):
            result = await list_sched(org_id=999)
        assert "not found" in result


class TestCancelOrgSchedule:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_org(self) -> None:
        tools = _get_tools()
        cancel_sched = tools["cancel_org_schedule"]

        with _mock_session(get_return=None):
            result = await cancel_sched(org_id=999, job_id="abc123")
        assert "not found" in result


class TestCreateOrgTool:
    @pytest.mark.asyncio
    async def test_create_tool_nonexistent_org(self) -> None:
        tools = _get_tools()
        create_tool = tools["create_org_tool"]

        with _mock_session(get_return=None):
            result = await create_tool(
                org_id=999, name="test_tool", description="Test",
                parameters_json="{}", tool_code="print('hi')",
            )
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_create_tool_uses_impl_bridge(self) -> None:
        tools = _get_tools()
        create_tool = tools["create_org_tool"]

        org = _make_org(id=1, name="DevOps")

        with _mock_session(get_return=org):
            with patch(
                "src.agents.tool_factory_agent._generate_cli_tool_impl",
                new=AsyncMock(return_value="✅ Tool **audit_tool** created and registered!"),
            ) as mock_impl:
                result = await create_tool(
                    org_id=1,
                    name="audit_tool",
                    description="Weekly audit utility",
                    parameters_json="{}",
                    tool_code="print('ok')",
                )

        assert "created and registered" in result.lower()
        mock_impl.assert_awaited_once()
        kwargs = mock_impl.await_args.kwargs
        assert kwargs["name"] == "audit_tool"
        assert kwargs["description"].startswith("[DevOps]")


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _get_tools() -> dict:
    """Build org tools and return as dict keyed by function name."""
    from src.agents.org_agent import _build_bound_org_tools
    tools = _build_bound_org_tools(user_id=42)
    return {getattr(t, "name", getattr(t, "__name__", str(i))): t for i, t in enumerate(tools)}


class _mock_session:
    """Context manager that patches async_session to return a mock session."""

    def __init__(
        self,
        execute_return=None,
        execute_returns=None,
        scalar_return=0,
        get_return="__unset__",
        get_returns=None,
    ):
        self.execute_return = execute_return
        self.execute_returns = execute_returns or []
        self.scalar_return = scalar_return
        self.get_return = get_return
        self.get_returns = get_returns or {}
        self._patcher = None

    def __enter__(self):
        mock_session = AsyncMock()

        # Handle session.execute() calls
        if self.execute_returns:
            mock_session.execute = AsyncMock(side_effect=self.execute_returns)
        elif self.execute_return is not None:
            mock_session.execute = AsyncMock(return_value=self.execute_return)

        # Handle session.scalar() calls
        mock_session.scalar = AsyncMock(return_value=self.scalar_return)

        # Handle session.get() calls
        if self.get_returns:
            async def _get_by_id(model, id):
                return self.get_returns.get(id)
            mock_session.get = AsyncMock(side_effect=_get_by_id)
        elif self.get_return != "__unset__":
            mock_session.get = AsyncMock(return_value=self.get_return)

        # Handle session.flush() and session.commit()
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        # Make async context manager work
        mock_session_factory = AsyncMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__ = AsyncMock(return_value=False)

        # Patch async_session to return our mock
        self._patcher = patch(
            "src.agents.org_agent.async_session",
            return_value=mock_session_factory,
        )
        self._patcher.start()

        # Patch _resolve_db_user_id so _get_db_owner_id resolves without
        # consuming an execute slot from the mock.
        async def _mock_resolve(session, telegram_id):
            return 42

        self._resolve_patcher = patch(
            "src.agents.org_agent._resolve_db_user_id",
            side_effect=_mock_resolve,
        )
        self._resolve_patcher.start()
        return mock_session

    def __exit__(self, *args):
        if self._patcher:
            self._patcher.stop()
        if hasattr(self, "_resolve_patcher") and self._resolve_patcher:
            self._resolve_patcher.stop()
