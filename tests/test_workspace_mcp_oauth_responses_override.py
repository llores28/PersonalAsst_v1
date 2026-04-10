import importlib
import sys
import types


def test_create_success_response_includes_dynamic_countdown_and_close_fallback() -> None:
    fastapi_module = types.ModuleType("fastapi")
    responses_module = types.ModuleType("fastapi.responses")
    fastapi_module.__path__ = []
    responses_module.__package__ = "fastapi"


    class HTMLResponse:
        def __init__(self, content: str, status_code: int = 200) -> None:
            self.body = content.encode("utf-8")
            self.status_code = status_code


    responses_module.HTMLResponse = HTMLResponse
    fastapi_module.responses = responses_module
    sys.modules["fastapi"] = fastapi_module
    sys.modules["fastapi.responses"] = responses_module


    override_module = importlib.import_module("src.integrations.workspace_mcp_oauth_responses_override")
    response = override_module.create_success_response("user@example.com")
    html = response.body.decode("utf-8")


    assert response.status_code == 200
    assert 'id="close-button"' in html
    assert 'id="countdown-value"' in html
    assert 'document.getElementById("countdown-value")' in html
    assert 'window.open("", "_self")' in html
    assert 'window.close();' in html
    assert 'Your browser prevented automatic closing. You can safely close this tab manually.' in html
    assert 'This window will close automatically in <span id="countdown-value">10</span> seconds' in html
