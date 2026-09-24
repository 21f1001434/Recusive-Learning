import asyncio
from pathlib import Path

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.models import ActionEvent, NetworkTabEvent
from hip_id_agent.page_explorer import PageExplorer


class DummyPage:
    url = "https://developer.dell.com/hybrid-integrations/bizlink/partner"
    def locator(self, *args, **kwargs):
        raise RuntimeError("not needed")
    async def wait_for_load_state(self, *args, **kwargs):
        raise TimeoutError("no idle")


class DummySession:
    def __init__(self):
        self.page = DummyPage()
        self.network_tab_events = [NetworkTabEvent(request_id="r1", url="https://developer.dell.com/api/partners/search", method="GET", status=200)]
        self.action_events = []
    async def _begin_action(self, typ, target, value=None, screenshot_before=False):
        return ActionEvent(f"a{len(self.action_events)+1}", typ, target, value_redacted=value)
    async def _finish_action(self, ev, success=True, error=None, screenshot_after=False):
        ev.success = success
        ev.error = error
        self.action_events.append(ev)


def test_wait_after_search_network_response_satisfies_wait():
    cfg = AppConfig()
    cfg.browser.search_timeout_ms = 50
    result = asyncio.run(PageExplorer(cfg).wait_after_search(DummySession(), query="UHAL", area="partner", before_network_count=0))
    assert result["satisfied"] is True
    assert result["condition"] == "relevant_network_response"


def test_wait_after_search_timeout_returns_recovery():
    cfg = AppConfig()
    cfg.browser.search_timeout_ms = 1
    s = DummySession()
    s.network_tab_events = []
    result = asyncio.run(PageExplorer(cfg).wait_after_search(s, query="UHAL", area="partner", before_network_count=0))
    assert result["satisfied"] is False
    assert "recovery_suggestion" in result
    assert s.action_events[-1].success is False


def test_secret_target_fill_value_is_masked_in_action_sequence(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path)
    async def run():
        ev = await session._begin_action("fill", "input[name=sftp_password]", value="real-secret")
        await session._finish_action(ev, True)
    asyncio.run(run())
    assert session.action_events[0].value_redacted == "***MASKED***"
    assert session.action_events[0].was_secret is True
    assert "real-secret" not in str(session.action_events[0].__dict__)


def test_non_secret_search_value_is_not_masked(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path)
    async def run():
        ev = await session._begin_action("search", "search partner", value="UHAL")
        await session._finish_action(ev, True)
    asyncio.run(run())
    assert session.action_events[0].value_redacted == "UHAL"
    assert session.action_events[0].was_secret is False
