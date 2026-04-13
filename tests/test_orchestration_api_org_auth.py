"""Tests for organization ownership helpers in orchestration API."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi is not installed locally")
HTTPException = fastapi.HTTPException


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, values):
        self._values = list(values)

    async def execute(self, _query):
        if not self._values:
            return _Result(None)
        return _Result(self._values.pop(0))


@pytest.mark.asyncio
async def test_resolve_dashboard_user_prefers_header_match() -> None:
    from src.orchestration.api import _resolve_dashboard_user

    header_user = SimpleNamespace(id=11, telegram_id=12345)
    session = _FakeSession([header_user])

    resolved = await _resolve_dashboard_user(session, x_telegram_id=12345)

    assert resolved is header_user


@pytest.mark.asyncio
async def test_resolve_dashboard_user_falls_back_to_owner_then_first_user() -> None:
    from src.orchestration.api import _resolve_dashboard_user

    owner_user = SimpleNamespace(id=1, telegram_id=99999)
    session = _FakeSession([owner_user])

    resolved = await _resolve_dashboard_user(session, x_telegram_id=None)

    assert resolved is owner_user


@pytest.mark.asyncio
async def test_resolve_dashboard_user_raises_when_no_users_exist() -> None:
    from src.orchestration.api import _resolve_dashboard_user

    session = _FakeSession([None, None, None])

    with pytest.raises(HTTPException) as exc:
        await _resolve_dashboard_user(session, x_telegram_id=None)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_owned_org_or_404_returns_owned_org() -> None:
    from src.orchestration.api import _get_owned_org_or_404

    org = SimpleNamespace(id=7, owner_user_id=1)
    session = _FakeSession([org])

    resolved = await _get_owned_org_or_404(session, org_id=7, owner_user_id=1)

    assert resolved is org


@pytest.mark.asyncio
async def test_get_owned_org_or_404_raises_when_missing() -> None:
    from src.orchestration.api import _get_owned_org_or_404

    session = _FakeSession([None])

    with pytest.raises(HTTPException) as exc:
        await _get_owned_org_or_404(session, org_id=999, owner_user_id=1)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_org_writes_durable_audit_log_before_delete() -> None:
    from src.orchestration.api import delete_org
    from src.db.models import AuditLog

    requester = SimpleNamespace(id=123)
    org = SimpleNamespace(id=7, name="Ops Org")

    class _FakeSession:
        def __init__(self):
            self.added = []
            self.deleted = []
            self.committed = False

        def add(self, obj):
            self.added.append(obj)

        async def delete(self, obj):
            self.deleted.append(obj)

        async def commit(self):
            self.committed = True

    class _FakeSessionFactory:
        def __init__(self, session):
            self._session = session

        def __call__(self):
            return self

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_session = _FakeSession()

    with (
        patch("src.orchestration.api.async_session", new=_FakeSessionFactory(fake_session)),
        patch("src.orchestration.api._resolve_dashboard_user", new=AsyncMock(return_value=requester)),
        patch("src.orchestration.api._get_owned_org_or_404", new=AsyncMock(return_value=org)),
    ):
        response = await delete_org(org_id=7, x_telegram_id=None)

    assert response["message"] == "Organization 'Ops Org' deleted"
    assert fake_session.deleted == [org]
    assert fake_session.committed is True
    assert any(isinstance(row, AuditLog) for row in fake_session.added)

    audit_row = next(row for row in fake_session.added if isinstance(row, AuditLog))
    assert audit_row.user_id == 123
    assert audit_row.platform == "dashboard"
    assert audit_row.agent_name == "org_api"


@pytest.mark.asyncio
async def test_update_org_task_sets_completed_timestamp_for_completed_status() -> None:
    from src.orchestration.api import OrgTaskUpdate, update_org_task

    requester = SimpleNamespace(id=123)
    task = SimpleNamespace(
        id=9,
        org_id=7,
        agent_id=None,
        title="Audit codebase",
        description="Initial",
        priority="medium",
        status="pending",
        result=None,
        source="dashboard",
        due_at=None,
        created_at=None,
        assigned_at=None,
        completed_at=None,
    )

    class _FakeSession:
        def __init__(self):
            self.committed = False
            self.refreshed = []

        async def get(self, model, value):
            if value == 9:
                return task
            return None

        async def commit(self):
            self.committed = True

        async def refresh(self, obj):
            self.refreshed.append(obj)

    class _FakeSessionFactory:
        def __init__(self, session):
            self._session = session

        def __call__(self):
            return self

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_session = _FakeSession()

    with (
        patch("src.orchestration.api.async_session", new=_FakeSessionFactory(fake_session)),
        patch("src.orchestration.api._resolve_dashboard_user", new=AsyncMock(return_value=requester)),
        patch("src.orchestration.api._get_owned_org_or_404", new=AsyncMock(return_value=SimpleNamespace(id=7))),
        patch("src.orchestration.api._log_org_activity", new=AsyncMock()),
    ):
        response = await update_org_task(
            org_id=7,
            task_id=9,
            body=OrgTaskUpdate(status="completed"),
            x_telegram_id=None,
        )

    assert response.status == "completed"
    assert response.completed_at is not None
    assert fake_session.committed is True
    assert fake_session.refreshed == [task]


@pytest.mark.asyncio
async def test_delete_org_agent_removes_owned_agent() -> None:
    from src.orchestration.api import delete_org_agent

    requester = SimpleNamespace(id=123)
    agent = SimpleNamespace(id=3, org_id=7, name="Code Audit Analyst")

    class _FakeSession:
        def __init__(self):
            self.deleted = []
            self.committed = False

        async def get(self, model, value):
            if value == 3:
                return agent
            return None

        async def delete(self, obj):
            self.deleted.append(obj)

        async def commit(self):
            self.committed = True

    class _FakeSessionFactory:
        def __init__(self, session):
            self._session = session

        def __call__(self):
            return self

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_session = _FakeSession()

    with (
        patch("src.orchestration.api.async_session", new=_FakeSessionFactory(fake_session)),
        patch("src.orchestration.api._resolve_dashboard_user", new=AsyncMock(return_value=requester)),
        patch("src.orchestration.api._get_owned_org_or_404", new=AsyncMock(return_value=SimpleNamespace(id=7))),
        patch("src.orchestration.api._log_org_activity", new=AsyncMock()),
    ):
        response = await delete_org_agent(org_id=7, agent_id=3, x_telegram_id=None)

    assert response["message"] == "Agent 'Code Audit Analyst' deleted"
    assert fake_session.deleted == [agent]
    assert fake_session.committed is True
