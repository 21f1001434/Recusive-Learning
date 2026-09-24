from pathlib import Path

from hip_id_agent.config import AppConfig, BrowserUseConfig, LangChainBrowserToolkitConfig, MCPConfig


def test_auxiliary_cdp_clients_are_not_startup_attached_by_default():
    assert BrowserUseConfig().startup_attach is False
    assert BrowserUseConfig().recovery_only_attach is True
    assert LangChainBrowserToolkitConfig().startup_attach is False


def test_mcp_is_not_required_during_sso_by_default():
    assert MCPConfig().use_browser_mcp_for_sso is False


def test_browser_session_has_cdp_health_probe_and_lazy_recovery_attach():
    src = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "browser_session.py").read_text(encoding="utf-8")
    assert "async def _probe_cdp_endpoint" in src
    assert "async def _wait_for_cdp_ready" in src
    assert "async def _ensure_recovery_intelligence_attached" in src
    assert "deferred_for_sso" in src


def test_browser_use_bridge_has_bounded_attach_and_snapshot_calls():
    src = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "browser_use_bridge.py").read_text(encoding="utf-8")
    assert "attach_timeout_seconds" in src
    assert "snapshot_timeout_seconds" in src
    assert "asyncio.wait_for(self.browser.start()" in src
