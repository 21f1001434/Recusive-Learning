import asyncio
import tempfile
from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.chrome_devtools_mcp import ChromeDevToolsMCPBackend
from hip_id_agent.config import AppConfig


class _TextLocator:
    def __init__(self, text: str):
        self._text = text
        self.first = self

    async def inner_text(self, timeout=0):
        return self._text

    async def wait_for(self, state=None, timeout=None):
        return None


class _SSORedirectPage:
    def __init__(self):
        self.url = "about:blank"
        self.goto_calls = []

    async def goto(self, url, wait_until="domcontentloaded", timeout=None):
        self.goto_calls.append((url, wait_until, timeout))
        self.url = "https://myaccess.dell.com/sso/idps/DSSO?stateTokenExternalId=secret-token"
        raise RuntimeError(f"Page.goto: net::ERR_ABORTED at {url}")

    def locator(self, selector):
        return _TextLocator("Dell Single Sign On Sign in")

    async def evaluate(self, script, *args):
        if "document.readyState" in script:
            return "complete"
        return None

    async def wait_for_load_state(self, state, timeout=None):
        return None

    async def screenshot(self, *args, **kwargs):
        return b"png"


@pytest.mark.asyncio
async def test_navigation_accepts_dell_sso_redirect_and_defers_same_surface_gate(monkeypatch):
    cfg = AppConfig()
    cfg.portal.timeout_ms = 50
    session = BrowserSession(cfg, Path(tempfile.mkdtemp()))
    session.page = _SSORedirectPage()

    async def must_not_run(*args, **kwargs):
        raise AssertionError("same-surface gate must not run on the temporary SSO page")

    monkeypatch.setattr(session, "_verify_dual_mcp_same_surface", must_not_run)
    await session.navigate("https://developer.dell.com/hybrid-integrations/securelink/datamaps")

    assert session.action_events[-1].success is True
    assert "deferred" in (session.action_events[-1].error or "").lower()
    evidence = session.run_dir / "mcp_runtime" / "sso_navigation_transition.json"
    assert evidence.exists()
    assert "secret-token" not in evidence.read_text(encoding="utf-8")


class _ListPagesClient:
    def __init__(self, payload):
        self.payload = payload
        self.tools = {"list_pages": object(), "select_page": object()}
        self.calls = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments or {}))
        if name == "list_pages":
            return self.payload
        return {"content": [{"type": "text", "text": "ok"}]}

    async def close(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"content": [{"type": "text", "text": "## Pages\n1. https://developer.dell.com/hybrid-integrations/securelink/datamaps [selected]"}]},
        {"content": [{"type": "text", "text": '{"pageId": 7, "url": "https://developer.dell.com/hybrid-integrations/securelink/datamaps"}'}]},
        {"pages": [{"pageId": 9, "url": "https://developer.dell.com/hybrid-integrations/securelink/datamaps"}]},
    ],
)
async def test_chrome_devtools_page_parser_accepts_current_and_legacy_formats(tmp_path, payload):
    backend = ChromeDevToolsMCPBackend(_ListPagesClient(payload), tmp_path)
    result = await backend.select_page_for_url("https://developer.dell.com/hybrid-integrations/securelink/datamaps")
    assert result["pass"] is True
    assert result["selected"]["url"].endswith("/securelink/datamaps")
    assert any(name == "select_page" for name, _ in backend.client.calls)


class _RetryChromeBackend:
    def __init__(self, url: str):
        self.url = url
        self.calls = 0

    async def select_page_for_url(self, expected_url: str):
        self.calls += 1
        if self.calls < 3:
            return {"pass": False, "pages": [], "reason": "not visible yet"}
        return {"pass": True, "selected": {"page_id": 1, "url": self.url}, "pages": []}


class _RetryPlaywrightBackend:
    def __init__(self, url: str):
        self.url = url

    async def get_current_url(self):
        return self.url


class _PortalPage:
    def __init__(self, url: str):
        self.url = url


@pytest.mark.asyncio
async def test_dual_mcp_gate_retries_devtools_page_visibility(monkeypatch, tmp_path):
    url = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"
    cfg = AppConfig()
    cfg.mcp.dual_mcp_same_surface_timeout_seconds = 1
    cfg.mcp.dual_mcp_same_surface_poll_seconds = 0.001
    session = BrowserSession(cfg, tmp_path)
    session.page = _PortalPage(url)
    session.mcp_backend = _RetryChromeBackend(url)
    session.playwright_mcp_backend = _RetryPlaywrightBackend(url)

    result = await session._verify_dual_mcp_same_surface(url)
    assert result["pass"] is True
    assert result["attempt_count"] == 3


def test_chrome_devtools_uses_official_same_browser_flag(tmp_path):
    cfg = AppConfig()
    backend = ChromeDevToolsMCPBackend.from_config(
        cfg,
        tmp_path,
        cdp_endpoint="http://127.0.0.1:9237",
    )
    assert "--browser-url=http://127.0.0.1:9237" in backend.launch_args
    assert not any(arg.startswith("--browserUrl=") for arg in backend.launch_args)
