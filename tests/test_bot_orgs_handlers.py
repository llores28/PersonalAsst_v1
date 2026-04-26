"""Handler-level tests for Telegram /orgs command and wizard flow."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("aiogram", reason="aiogram is not installed locally")


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, execute_values):
        self._execute_values = list(execute_values)
        self.added = []

    async def execute(self, _query):
        value = self._execute_values.pop(0) if self._execute_values else None
        return _FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for idx, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None and obj.__class__.__name__ == "Organization":
                obj.id = 100 + idx

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None

    async def delete(self, _obj):
        return None


class _FakeSessionFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Organization:
    id = 1
    owner_user_id = 1

    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.name = kwargs.get("name")
        self.goal = kwargs.get("goal")
        self.description = kwargs.get("description")
        self.owner_user_id = kwargs.get("owner_user_id")
        self.status = kwargs.get("status", "active")


class _OrgActivity:
    def __init__(self, **kwargs):
        self.org_id = kwargs.get("org_id")
        self.action = kwargs.get("action")
        self.details = kwargs.get("details")
        self.source = kwargs.get("source")


class _AuditLog:
    def __init__(self, **kwargs):
        self.user_id = kwargs.get("user_id")
        self.platform = kwargs.get("platform")


class _User:
    telegram_id = 1


@pytest.mark.asyncio
async def test_cmd_orgs_create_redirects_to_neworg() -> None:
    """`/orgs create` no longer launches the multi-step session-field wizard;
    it now points users to the AI-powered single-shot `/neworg <goal>` flow.
    The test asserts the redirect message is sent and no session state is
    written (the old wizard's set_session_field("org_creation_*", ...) calls
    must not fire)."""
    from src.bot.handlers import cmd_orgs

    fake_session = _FakeSession(execute_values=[SimpleNamespace(id=77)])
    fake_query = MagicMock()
    fake_query.where.return_value = fake_query

    fake_db_session_module = ModuleType("src.db.session")
    fake_db_session_module.async_session = _FakeSessionFactory(fake_session)

    fake_db_models_module = ModuleType("src.db.models")
    fake_db_models_module.User = _User
    fake_db_models_module.AuditLog = _AuditLog

    fake_registry_module = ModuleType("src.orchestration.agent_registry")
    fake_registry_module.Organization = _Organization
    fake_registry_module.OrgActivity = _OrgActivity

    message = SimpleNamespace(
        text="/orgs create",
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
    )

    with (
        patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
        patch("sqlalchemy.select", return_value=fake_query),
        patch.dict(
            sys.modules,
            {
                "src.db.session": fake_db_session_module,
                "src.db.models": fake_db_models_module,
                "src.orchestration.agent_registry": fake_registry_module,
            },
        ),
        patch("src.memory.conversation.set_session_field", new=AsyncMock()) as mock_set_field,
    ):
        await cmd_orgs(message)

    # No session-state writes — the wizard moved to a single-shot /neworg call.
    assert mock_set_field.await_count == 0
    # User is redirected to /neworg.
    answer_texts = [call.args[0] for call in message.answer.await_args_list]
    assert any("/neworg" in text for text in answer_texts)
    assert any("AI-Powered Organization Wizard" in text for text in answer_texts)


@pytest.mark.asyncio
async def test_handle_message_org_wizard_description_creates_org_and_clears_state() -> None:
    from src.bot.handlers import handle_message

    fake_session = _FakeSession(execute_values=[SimpleNamespace(id=42)])
    fake_query = MagicMock()
    fake_query.where.return_value = fake_query

    fake_db_session_module = ModuleType("src.db.session")
    fake_db_session_module.async_session = _FakeSessionFactory(fake_session)

    fake_db_models_module = ModuleType("src.db.models")
    fake_db_models_module.User = _User

    fake_registry_module = ModuleType("src.orchestration.agent_registry")
    fake_registry_module.Organization = _Organization
    fake_registry_module.OrgActivity = _OrgActivity

    session_values = {
        "org_creation_active": "true",
        "org_creation_step": "description",
        "org_creation_name": "Ops Org",
        "org_creation_goal": "Ship weekly reviews",
    }

    async def _get_field(_tg_id, key):
        return session_values.get(key)

    message = SimpleNamespace(
        text="skip",
        voice=None,
        photo=None,  # handle_message branches on message.photo before reaching the org wizard
        caption=None,
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
    )

    with (
        patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
        patch("src.bot.handlers.is_cost_capped", new=AsyncMock(return_value=False)),
        patch("sqlalchemy.select", return_value=fake_query),
        patch.dict(
            sys.modules,
            {
                "src.db.session": fake_db_session_module,
                "src.db.models": fake_db_models_module,
                "src.orchestration.agent_registry": fake_registry_module,
            },
        ),
        patch("src.memory.conversation.get_session_field", new=AsyncMock(side_effect=_get_field)),
        patch("src.memory.conversation.set_session_field", new=AsyncMock()),
        patch("src.memory.conversation.delete_session_field", new=AsyncMock()) as mock_delete_field,
    ):
        await handle_message(message)

    assert any("Organization created" in call.args[0] for call in message.answer.await_args_list)
    deleted_keys = [call.args[1] for call in mock_delete_field.await_args_list]
    assert deleted_keys == [
        "org_creation_active",
        "org_creation_step",
        "org_creation_name",
        "org_creation_goal",
    ]
    assert any(getattr(obj, "action", None) == "org_created" for obj in fake_session.added)
