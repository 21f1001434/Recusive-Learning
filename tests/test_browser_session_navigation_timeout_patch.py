import tempfile
from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig


class _FakeLocator:
    async def inner_text(self, timeout=0):
        return "Hybrid Integrations Document Type Document Identifier Create Document Type"


class _FakePage:
    def __init__(self):
        self.url = "https://developer.dell.com/hybrid-integrations/securelink/doctypes"
        self.goto_calls = []
        self.wait_calls = []

    async def goto(self, url, wait_until="domcontentloaded", timeout=None):
        self.goto_calls.append((url, wait_until, timeout))
        self.url = url
        raise TimeoutError("Page.goto: Timeout 45000ms exceeded")

    def locator(self, selector):
        return _FakeLocator()

    async def evaluate(self, script, *args):
        if "document.readyState" in script:
            return "interactive"
        if args:
            self.url = args[0]
        return None

    async def wait_for_load_state(self, state, timeout=None):
        self.wait_calls.append((state, timeout))
        return None

    async def screenshot(self, *args, **kwargs):
        return b"png"


@pytest.mark.asyncio
async def test_navigation_timeout_is_tolerated_when_target_page_is_usable():
    cfg = AppConfig()
    cfg.portal.timeout_ms = 100
    session = BrowserSession(cfg, Path(tempfile.mkdtemp()))
    session.page = _FakePage()

    await session.navigate("https://developer.dell.com/hybrid-integrations/securelink/doctypes")

    assert session.action_events[-1].success is True
    assert "usable" in (session.action_events[-1].error or "").lower()
    # A persistent session must not issue a second same-URL goto when the
    # requested Angular surface is already active and usable.
    assert session.page.goto_calls == []
