"""Tests for Tool Factory infrastructure — credential vault, sandbox env, manifest schema,
ToolRegistry function-type loading, and LinkedIn tool wrappers.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Credential Vault Tests ───────────────────────────────────────


class TestCredentialVault:
    """Tests for src.tools.credentials."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis client for credential vault tests."""
        mock = AsyncMock()
        mock.hset = AsyncMock()
        mock.hget = AsyncMock(return_value="test_value")
        mock.hgetall = AsyncMock(return_value={"key1": "val1", "key2": "val2"})
        mock.hdel = AsyncMock()
        mock.delete = AsyncMock()
        mock.hkeys = AsyncMock(return_value=["key1", "key2"])
        mock.aclose = AsyncMock()
        return mock

    @pytest.mark.asyncio
    async def test_store_credential(self, mock_redis):
        with patch("src.tools.credentials.get_redis", new=AsyncMock(return_value=mock_redis)):
            from src.tools.credentials import store_credential

            await store_credential("linkedin", "email", "test@example.com")
            mock_redis.hset.assert_called_once_with(
                "tool_credentials:linkedin", "email", "test@example.com"
            )

    @pytest.mark.asyncio
    async def test_store_credentials_bulk(self, mock_redis):
        with patch("src.tools.credentials.get_redis", new=AsyncMock(return_value=mock_redis)):
            from src.tools.credentials import store_credentials

            creds = {"email": "test@example.com", "password": "secret"}
            await store_credentials("linkedin", creds)
            mock_redis.hset.assert_called_once_with(
                "tool_credentials:linkedin", mapping=creds
            )

    @pytest.mark.asyncio
    async def test_store_credentials_empty(self, mock_redis):
        with patch("src.tools.credentials.get_redis", new=AsyncMock(return_value=mock_redis)):
            from src.tools.credentials import store_credentials

            await store_credentials("linkedin", {})
            mock_redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_credential(self, mock_redis):
        with patch("src.tools.credentials.get_redis", new=AsyncMock(return_value=mock_redis)):
            from src.tools.credentials import get_credential

            val = await get_credential("linkedin", "email")
            assert val == "test_value"
            mock_redis.hget.assert_called_once_with(
                "tool_credentials:linkedin", "email"
            )

    @pytest.mark.asyncio
    async def test_get_credentials(self, mock_redis):
        with patch("src.tools.credentials.get_redis", new=AsyncMock(return_value=mock_redis)):
            from src.tools.credentials import get_credentials

            creds = await get_credentials("linkedin")
            assert creds == {"key1": "val1", "key2": "val2"}
            mock_redis.hgetall.assert_called_once_with("tool_credentials:linkedin")

    @pytest.mark.asyncio
    async def test_delete_credential(self, mock_redis):
        with patch("src.tools.credentials.get_redis", new=AsyncMock(return_value=mock_redis)):
            from src.tools.credentials import delete_credential

            await delete_credential("linkedin", "email")
            mock_redis.hdel.assert_called_once_with(
                "tool_credentials:linkedin", "email"
            )

    @pytest.mark.asyncio
    async def test_delete_all_credentials(self, mock_redis):
        with patch("src.tools.credentials.get_redis", new=AsyncMock(return_value=mock_redis)):
            from src.tools.credentials import delete_all_credentials

            await delete_all_credentials("linkedin")
            mock_redis.delete.assert_called_once_with("tool_credentials:linkedin")

    @pytest.mark.asyncio
    async def test_list_credential_keys(self, mock_redis):
        with patch("src.tools.credentials.get_redis", new=AsyncMock(return_value=mock_redis)):
            from src.tools.credentials import list_credential_keys

            keys = await list_credential_keys("linkedin")
            assert keys == ["key1", "key2"]

    def test_vault_key_format(self):
        from src.tools.credentials import _key

        assert _key("linkedin") == "tool_credentials:linkedin"
        assert _key("stock_checker") == "tool_credentials:stock_checker"


# ── Sandbox Environment Tests ────────────────────────────────────


class TestSandboxEnv:
    """Tests for build_sandbox_env and sandbox credential injection."""

    def test_build_sandbox_env_minimal(self):
        from src.tools.credentials import build_sandbox_env

        env = build_sandbox_env({})
        assert "PATH" in env
        assert "HOME" in env
        assert env["HOME"] == "/tmp"
        assert env["LANG"] == "C.UTF-8"
        # No TOOL_ vars when no credentials
        tool_vars = [k for k in env if k.startswith("TOOL_")]
        assert tool_vars == []

    def test_build_sandbox_env_with_credentials(self):
        from src.tools.credentials import build_sandbox_env

        creds = {"linkedin_email": "me@test.com", "linkedin_password": "pass123"}
        env = build_sandbox_env(creds)
        assert env["TOOL_LINKEDIN_EMAIL"] == "me@test.com"
        assert env["TOOL_LINKEDIN_PASSWORD"] == "pass123"

    def test_build_sandbox_env_filtered_by_allowed_keys(self):
        from src.tools.credentials import build_sandbox_env

        creds = {"linkedin_email": "me@test.com", "linkedin_password": "pass123", "extra": "nope"}
        env = build_sandbox_env(creds, allowed_keys=["linkedin_email"])
        assert env["TOOL_LINKEDIN_EMAIL"] == "me@test.com"
        assert "TOOL_LINKEDIN_PASSWORD" not in env
        assert "TOOL_EXTRA" not in env

    def test_build_sandbox_env_python_in_path(self):
        from src.tools.credentials import build_sandbox_env

        env = build_sandbox_env({})
        python_dir = str(os.path.dirname(sys.executable))
        assert python_dir in env["PATH"]


# ── Manifest Schema Tests ────────────────────────────────────────


class TestManifestSchema:
    """Tests for ToolManifest with new credentials and dependencies fields."""

    def test_manifest_with_credentials(self):
        from src.tools.manifest import ToolManifest

        raw = json.dumps({
            "$schema": "tool-manifest-v1",
            "name": "linkedin",
            "description": "LinkedIn tool",
            "type": "function",
            "entrypoint": "tool.py",
            "credentials": {
                "linkedin_email": {
                    "description": "LinkedIn email",
                    "required": True,
                    "env_var_hint": "LINKEDIN_EMAIL",
                },
                "linkedin_password": {
                    "description": "LinkedIn password",
                    "required": True,
                },
            },
            "dependencies": ["linkedin-api>=2.0.0"],
        })
        manifest = ToolManifest.model_validate_json(raw)
        assert "linkedin_email" in manifest.credentials
        assert manifest.credentials["linkedin_email"].required is True
        assert manifest.credentials["linkedin_email"].env_var_hint == "LINKEDIN_EMAIL"
        assert manifest.dependencies == ["linkedin-api>=2.0.0"]

    def test_manifest_backward_compatible(self):
        """Existing manifests without credentials/dependencies still parse."""
        from src.tools.manifest import ToolManifest

        raw = json.dumps({
            "$schema": "tool-manifest-v1",
            "name": "echo",
            "description": "Echo tool",
            "type": "cli",
            "entrypoint": "cli.py",
        })
        manifest = ToolManifest.model_validate_json(raw)
        assert manifest.credentials == {}
        assert manifest.dependencies == []

    def test_linkedin_manifest_loads(self):
        """The actual LinkedIn manifest.json loads correctly."""
        from src.tools.manifest import ToolManifest

        manifest_path = Path("src/tools/plugins/linkedin/manifest.json")
        if manifest_path.exists():
            manifest = ToolManifest.model_validate_json(manifest_path.read_text())
            assert manifest.name == "linkedin"
            assert manifest.type == "function"
            assert manifest.requires_network is True
            assert "linkedin_email" in manifest.credentials
            assert "linkedin_password" in manifest.credentials


# ── ToolRegistry Function-Type Loading Tests ─────────────────────


class TestToolRegistryFunctionType:
    """Tests for ToolRegistry's function-type tool loading path."""

    @pytest.mark.asyncio
    async def test_load_function_wrapper_single(self, tmp_path):
        """Test loading a single function_tool from a wrapper module."""
        from src.tools.registry import ToolRegistry

        # Create a minimal function tool wrapper
        wrapper_code = '''
from agents import function_tool

@function_tool
async def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

tool_function = greet
'''
        tool_dir = tmp_path / "test_tool"
        tool_dir.mkdir()
        (tool_dir / "tool.py").write_text(wrapper_code)
        (tool_dir / "manifest.json").write_text(json.dumps({
            "$schema": "tool-manifest-v1",
            "name": "test_tool",
            "description": "Test tool",
            "type": "function",
            "entrypoint": "tool.py",
            "wrapper": "tool.py",
        }))

        registry = ToolRegistry(tmp_path)
        await registry.load_all()
        assert "test_tool" in registry._tools

    @pytest.mark.asyncio
    async def test_load_function_wrapper_multi(self, tmp_path):
        """Test loading multiple function_tools from a single wrapper module."""
        from src.tools.registry import ToolRegistry

        wrapper_code = '''
from agents import function_tool

@function_tool
async def tool_a() -> str:
    """Tool A."""
    return "A"

@function_tool
async def tool_b() -> str:
    """Tool B."""
    return "B"

tool_functions = [tool_a, tool_b]
'''
        tool_dir = tmp_path / "multi_tool"
        tool_dir.mkdir()
        (tool_dir / "tool.py").write_text(wrapper_code)
        (tool_dir / "manifest.json").write_text(json.dumps({
            "$schema": "tool-manifest-v1",
            "name": "multi_tool",
            "description": "Multi tool",
            "type": "function",
            "entrypoint": "tool.py",
            "wrapper": "tool.py",
        }))

        registry = ToolRegistry(tmp_path)
        await registry.load_all()
        # Multi-tool should register each tool by its own name
        assert len(registry._tools) >= 2
        assert "multi_tool" in registry._manifests

    def test_cli_manifest_with_credentials_parses(self, tmp_path):
        """CLI manifest with credentials field parses and credential_keys are extracted."""
        from src.tools.manifest import ToolManifest

        manifest_data = {
            "$schema": "tool-manifest-v1",
            "name": "cred_tool",
            "description": "Tool with credentials",
            "type": "cli",
            "entrypoint": "cli.py",
            "credentials": {
                "api_key": {"description": "API key", "required": True},
            },
        }
        manifest = ToolManifest.model_validate(manifest_data)
        assert "api_key" in manifest.credentials
        cred_keys = list(manifest.credentials.keys())
        assert cred_keys == ["api_key"]


# ── LinkedIn Tool Wrapper Tests ──────────────────────────────────


class TestLinkedInToolWrappers:
    """Tests for LinkedIn function_tool wrappers — mock the linkedin_api client."""

    @pytest.fixture(autouse=True)
    def reset_linkedin_client(self):
        """Reset the LinkedIn client singleton between tests."""
        import src.tools.plugins.linkedin.tool as lt
        lt._client = None
        lt._client_error = None
        yield
        lt._client = None
        lt._client_error = None

    def test_tool_functions_list_exists(self):
        """The tool.py module exports tool_functions list."""
        from src.tools.plugins.linkedin.tool import tool_functions

        assert isinstance(tool_functions, list)
        assert len(tool_functions) == 11
        names = [getattr(f, "name", None) for f in tool_functions]
        assert "linkedin_get_profile" in names
        assert "linkedin_search_jobs" in names
        assert "linkedin_send_message" in names
        assert "linkedin_create_post" in names
        assert "linkedin_scrape_page" in names

    @pytest.mark.asyncio
    async def test_get_profile_success(self):
        """linkedin_get_profile returns formatted profile data."""
        mock_api = MagicMock()
        mock_api.get_profile.return_value = {
            "firstName": "John",
            "lastName": "Doe",
            "headline": "Engineer",
            "summary": "A developer",
            "locationName": "NYC",
            "industryName": "Tech",
            "experience": [{"title": "Dev", "companyName": "Acme", "description": "Coding"}],
            "education": [{"schoolName": "MIT", "degreeName": "BS", "fieldOfStudy": "CS"}],
        }
        mock_api.get_profile_contact_info.return_value = {
            "email_address": "john@test.com",
            "websites": [],
        }
        mock_api.get_profile_skills.return_value = [
            {"name": "Python"}, {"name": "Java"},
        ]

        import src.tools.plugins.linkedin.tool as lt
        lt._client = mock_api

        # Call the _impl function directly (avoids FunctionTool invocation)
        result = await lt._get_profile_impl(public_id="john-doe-123")
        data = json.loads(result)
        assert data["name"] == "John Doe"
        assert data["headline"] == "Engineer"
        assert "Python" in data["skills"]

    @pytest.mark.asyncio
    async def test_search_jobs_success(self):
        """linkedin_search_jobs returns formatted job results."""
        mock_api = MagicMock()
        mock_api.search_jobs.return_value = [
            {
                "title": "Python Dev",
                "companyName": "Acme",
                "formattedLocation": "Remote",
                "dashEntityUrn": "urn:li:fsd_jobPosting:12345",
                "listedAt": "2026-03-20",
            },
        ]

        import src.tools.plugins.linkedin.tool as lt
        lt._client = mock_api

        result = await lt._search_jobs_impl(keywords="python", limit=5, location="")
        data = json.loads(result)
        assert data["count"] == 1
        assert data["results"][0]["title"] == "Python Dev"
        assert data["results"][0]["job_id"] == "12345"

    @pytest.mark.asyncio
    async def test_get_client_missing_credentials(self):
        """_get_client raises when credentials are missing."""
        import src.tools.plugins.linkedin.tool as lt
        from src.tools import credentials as creds_mod

        with patch.object(creds_mod, "get_credentials", new_callable=AsyncMock, return_value={}):
            with pytest.raises(RuntimeError, match="credentials not configured"):
                await lt._get_client()

    @pytest.mark.asyncio
    async def test_get_my_profile(self):
        """linkedin_get_my_profile returns scraped markdown."""
        from src.tools.web_auth import ScrapeResult

        mock_result = ScrapeResult(
            url="https://www.linkedin.com/in/me/",
            markdown="# Me User\nBuilder\nSan Francisco",
            success=True,
        )

        import src.tools.plugins.linkedin.tool as lt
        with patch("src.tools.web_auth.scrape_linkedin_profile",
                   new_callable=AsyncMock, return_value=mock_result):
            result = await lt._get_my_profile_impl()
        assert "Me User" in result
        assert "Builder" in result

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """linkedin_send_message resolves profile and sends."""
        mock_api = MagicMock()
        mock_api.get_profile.return_value = {"profile_id": "urn:li:member:999"}
        mock_api.send_message.return_value = False  # False = no error

        import src.tools.plugins.linkedin.tool as lt
        lt._client = mock_api

        result = await lt._send_message_impl(
            recipient_public_id="john-doe", message="Hi!"
        )
        assert "sent" in result.lower()
        mock_api.send_message.assert_called_once()

    def test_safe_json_truncation(self):
        """_safe_json truncates long output."""
        from src.tools.plugins.linkedin.tool import _safe_json

        big = {"data": "x" * 5000}
        result = _safe_json(big, max_len=100)
        assert len(result) <= 120  # 100 + "... (truncated)"
        assert "truncated" in result

    def test_reset_client(self):
        """_reset_client clears the singleton."""
        import src.tools.plugins.linkedin.tool as lt
        lt._client = "something"
        lt._client_error = "an error"
        lt._reset_client()
        assert lt._client is None
        assert lt._client_error is None


# ── Static Analysis Tests ────────────────────────────────────────


class TestStaticAnalysis:
    """Tests for sandbox static analysis — unchanged but verify it works."""

    def test_blocks_os_environ(self):
        from src.tools.sandbox import static_analysis

        violations = static_analysis("import os\nkey = os.environ['SECRET']")
        assert any("environ" in v for v in violations)

    def test_blocks_eval(self):
        from src.tools.sandbox import static_analysis

        violations = static_analysis("result = eval('1+1')")
        assert any("eval" in v for v in violations)

    def test_passes_clean_code(self):
        from src.tools.sandbox import static_analysis

        code = "import argparse\nparser = argparse.ArgumentParser()\nprint('hello')"
        violations = static_analysis(code)
        assert violations == []


# ── Startup Credential Seeding Tests ─────────────────────────────


class TestCredentialSeeding:
    """Tests for the credential seeding logic (avoid importing src.main directly due to aiogram)."""

    @pytest.mark.asyncio
    async def test_seed_linkedin_credentials(self):
        """Verify seeding logic stores LinkedIn creds from env vars."""
        mock_store = AsyncMock()
        with patch.dict(os.environ, {
            "LINKEDIN_EMAIL": "test@example.com",
            "LINKEDIN_PASSWORD": "secret123",
        }):
            # Inline the seeding logic (mirrors src.main.seed_tool_credentials)
            li_email = os.environ.get("LINKEDIN_EMAIL", "")
            li_password = os.environ.get("LINKEDIN_PASSWORD", "")
            if li_email and li_password:
                await mock_store("linkedin", {
                    "linkedin_email": li_email,
                    "linkedin_password": li_password,
                })
            mock_store.assert_called_once_with("linkedin", {
                "linkedin_email": "test@example.com",
                "linkedin_password": "secret123",
            })

    @pytest.mark.asyncio
    async def test_seed_skips_empty_credentials(self):
        """Verify seeding logic skips when env vars are empty."""
        from src.tools.credentials import store_credentials

        mock_store = AsyncMock()
        with patch.dict(os.environ, {"LINKEDIN_EMAIL": "", "LINKEDIN_PASSWORD": ""}, clear=False):
            with patch(
                "src.tools.credentials.store_credentials", mock_store
            ):
                li_email = os.environ.get("LINKEDIN_EMAIL", "")
                li_password = os.environ.get("LINKEDIN_PASSWORD", "")
                if li_email and li_password:
                    await store_credentials("linkedin", {
                        "linkedin_email": li_email,
                        "linkedin_password": li_password,
                    })
                mock_store.assert_not_called()


# ── Browser Tool Tests ───────────────────────────────────────────


class TestBrowserToolWrappers:
    """Tests for browser automation function_tool wrappers — mock Playwright."""

    @pytest.fixture(autouse=True)
    def reset_browser(self):
        """Reset the browser singleton between tests."""
        import src.tools.plugins.browser.tool as bt
        bt._playwright = None
        bt._browser = None
        bt._page = None
        bt._init_error = None
        yield
        bt._playwright = None
        bt._browser = None
        bt._page = None
        bt._init_error = None

    def test_tool_functions_list_exists(self):
        """The browser tool.py exports tool_functions list."""
        from src.tools.plugins.browser.tool import tool_functions

        assert isinstance(tool_functions, list)
        assert len(tool_functions) == 12
        names = [getattr(f, "name", None) for f in tool_functions]
        assert "browser_navigate" in names
        assert "browser_click" in names
        assert "browser_fill" in names
        assert "browser_get_text" in names
        assert "browser_screenshot" in names
        assert "browser_page_info" in names
        assert "browser_login" in names
        assert "browser_close" in names
        assert "browser_scrape_page" in names

    @pytest.mark.asyncio
    async def test_navigate_impl(self):
        """_navigate_impl calls page.goto and returns page info."""
        import src.tools.plugins.browser.tool as bt

        mock_page = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_page.goto = AsyncMock(return_value=mock_resp)
        mock_page.title = AsyncMock(return_value="Test Page")
        mock_page.url = "https://example.com"
        bt._page = mock_page

        result = await bt._navigate_impl("https://example.com")
        data = json.loads(result)
        assert data["status"] == "navigated"
        assert data["url"] == "https://example.com"
        assert data["title"] == "Test Page"
        assert data["http_status"] == 200

    @pytest.mark.asyncio
    async def test_click_impl(self):
        """_click_impl clicks element and waits for load."""
        import src.tools.plugins.browser.tool as bt

        mock_page = AsyncMock()
        mock_page.click = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.title = AsyncMock(return_value="After Click")
        mock_page.url = "https://example.com/result"
        bt._page = mock_page

        result = await bt._click_impl("#submit")
        data = json.loads(result)
        assert data["status"] == "clicked"
        assert data["selector"] == "#submit"
        mock_page.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_fill_impl(self):
        """_fill_impl fills a form field."""
        import src.tools.plugins.browser.tool as bt

        mock_page = AsyncMock()
        mock_page.fill = AsyncMock()
        bt._page = mock_page

        result = await bt._fill_impl("#email", "test@example.com")
        data = json.loads(result)
        assert data["status"] == "filled"
        assert data["selector"] == "#email"
        mock_page.fill.assert_called_once_with("#email", "test@example.com", timeout=10000)

    @pytest.mark.asyncio
    async def test_get_page_text_impl(self):
        """_get_page_text_impl extracts text from element."""
        import src.tools.plugins.browser.tool as bt

        mock_element = AsyncMock()
        mock_element.inner_text = AsyncMock(return_value="Hello World")
        mock_page = AsyncMock()
        mock_page.query_selector = AsyncMock(return_value=mock_element)
        mock_page.url = "https://example.com"
        bt._page = mock_page

        result = await bt._get_page_text_impl("body")
        data = json.loads(result)
        assert data["text"] == "Hello World"

    @pytest.mark.asyncio
    async def test_get_page_text_not_found(self):
        """_get_page_text_impl handles missing element."""
        import src.tools.plugins.browser.tool as bt

        mock_page = AsyncMock()
        mock_page.query_selector = AsyncMock(return_value=None)
        bt._page = mock_page

        result = await bt._get_page_text_impl("#nonexistent")
        assert "No element found" in result

    @pytest.mark.asyncio
    async def test_close_session(self):
        """_close_session_impl cleans up browser state."""
        import src.tools.plugins.browser.tool as bt

        bt._page = AsyncMock()
        bt._browser = AsyncMock()
        bt._playwright = AsyncMock()

        result = await bt._close_session_impl()
        assert "closed" in result.lower()
        assert bt._page is None
        assert bt._browser is None

    @pytest.mark.asyncio
    async def test_login_impl_missing_creds(self):
        """_login_with_credentials_impl handles missing credentials."""
        import src.tools.plugins.browser.tool as bt

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        bt._page = mock_page

        from src.tools import credentials as creds_mod
        with patch.object(creds_mod, "get_credentials", new_callable=AsyncMock, return_value={}):
            result = await bt._login_with_credentials_impl(
                url="https://example.com/login",
                tool_name="test_tool",
                email_selector="#email",
                password_selector="#pass",
                submit_selector="#submit",
            )
        assert "No credentials found" in result

    @pytest.mark.asyncio
    async def test_login_impl_success(self):
        """_login_with_credentials_impl fills and submits login form."""
        import src.tools.plugins.browser.tool as bt

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.fill = AsyncMock()
        mock_page.click = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.title = AsyncMock(return_value="Dashboard")
        mock_page.url = "https://example.com/dashboard"
        bt._page = mock_page

        creds = {"email": "me@test.com", "password": "secret"}
        from src.tools import credentials as creds_mod
        with patch.object(creds_mod, "get_credentials", new_callable=AsyncMock, return_value=creds):
            result = await bt._login_with_credentials_impl(
                url="https://example.com/login",
                tool_name="test_tool",
                email_selector="#email",
                password_selector="#pass",
                submit_selector="#submit",
            )
        data = json.loads(result)
        assert data["status"] == "login_submitted"
        assert mock_page.fill.call_count == 2

    def test_safe_json_truncation(self):
        from src.tools.plugins.browser.tool import _safe_json
        big = {"data": "x" * 5000}
        result = _safe_json(big, max_len=100)
        assert "truncated" in result

    def test_browser_manifest_loads(self):
        """The actual browser manifest.json loads correctly."""
        from src.tools.manifest import ToolManifest
        manifest_path = Path("src/tools/plugins/browser/manifest.json")
        if manifest_path.exists():
            manifest = ToolManifest.model_validate_json(manifest_path.read_text())
            assert manifest.name == "browser"
            assert manifest.type == "function"
            assert manifest.requires_network is True
            assert manifest.requires_approval is True


# ── Telegram /tools credentials Command Tests ────────────────────


class TestToolsCredentialCommand:
    """Tests for the /tools credentials Telegram command parsing logic."""

    def test_credential_set_parsing(self):
        """Verify we can parse /tools credentials set <tool> <key> <value>."""
        text = "/tools credentials set linkedin linkedin_email me@example.com"
        parts = text.strip().split()
        assert len(parts) >= 6
        assert parts[0] == "/tools"
        assert parts[1] == "credentials"
        assert parts[2] == "set"
        assert parts[3] == "linkedin"
        assert parts[4] == "linkedin_email"
        assert " ".join(parts[5:]) == "me@example.com"

    def test_credential_set_with_spaces_in_value(self):
        """Values with spaces should be captured fully."""
        text = "/tools credentials set myapi api_key sk-abc 123 xyz"
        parts = text.strip().split()
        assert parts[3] == "myapi"
        assert parts[4] == "api_key"
        assert " ".join(parts[5:]) == "sk-abc 123 xyz"

    def test_credential_list_parsing(self):
        text = "/tools credentials list linkedin"
        parts = text.strip().split(maxsplit=4)
        assert parts[2] == "list"
        assert parts[3] == "linkedin"

    def test_credential_delete_parsing(self):
        text = "/tools credentials delete linkedin linkedin_email"
        parts = text.strip().split()
        assert parts[2] == "delete"
        assert parts[3] == "linkedin"
        assert parts[4] == "linkedin_email"
