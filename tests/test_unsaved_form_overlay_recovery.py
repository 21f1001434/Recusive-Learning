from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig


class DummyPage:
    url = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"

    async def evaluate(self, script, arg=None):
        text = str(script)
        if "candidate_count" in text:
            return {"active": True, "candidate": {"controls": 8, "phaseMarker": True}, "candidate_count": 1}
        if "data-hip-stale-overlay-neutralized" in text:
            return {"pass": True, "status": "neutralized", "changed_count": 1, "target_hit_test_pass": True}
        return {}


@pytest.mark.asyncio
async def test_watchdog_neutralizes_stale_overlay_without_refresh(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.loading_watchdog_timeout_seconds = 0.001
    cfg.vision_runtime.loading_refresh_after_seconds = 0.001
    cfg.portal.loading_watchdog_poll_seconds = 0.001
    cfg.portal.loading_watchdog_max_refreshes_per_phase = 1
    cfg.portal.loading_watchdog_consecutive_blocking_samples = 1
    session = BrowserSession(cfg, tmp_path)
    session.page = DummyPage()
    session._active_target_url = session.page.url
    session._active_phase_name = "data_map"
    states = [
        {"active": True, "blocking_fingerprint": "dds-overlay", "target_found": True, "target_visible": True, "target_enabled": True},
        {"active": False, "classification": "ready", "target_hit_test_pass": True},
    ]
    async def current(*args, **kwargs):
        return states.pop(0) if states else {"active": False, "classification": "ready"}
    async def ensure(url=""):
        return session.page
    async def observers():
        return None
    refreshed = {"count": 0}
    async def refresh(**kwargs):
        refreshed["count"] += 1
        return {}
    monkeypatch.setattr(session, "_current_loading_state", current)
    monkeypatch.setattr(session, "_ensure_active_page", ensure)
    monkeypatch.setattr(session, "_ensure_page_observers", observers)
    monkeypatch.setattr(session, "refresh_current_page_preserving_session", refresh)
    monkeypatch.setattr(session, "_capture_loading_watchdog_evidence", lambda **kwargs: _async_value({}))
    assert await session.wait_for_portal_loading_complete(reason="fill text control", target_selector="#map") is True
    assert refreshed["count"] == 0


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_watchdog_never_refreshes_unsaved_form_when_neutralization_fails(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.loading_watchdog_timeout_seconds = 0.001
    cfg.vision_runtime.loading_refresh_after_seconds = 0.001
    cfg.portal.loading_watchdog_poll_seconds = 0.001
    cfg.portal.loading_watchdog_max_refreshes_per_phase = 1
    cfg.portal.loading_watchdog_consecutive_blocking_samples = 1
    session = BrowserSession(cfg, tmp_path)
    session.page = DummyPage()
    session._active_target_url = session.page.url
    session._active_phase_name = "data_map"
    async def current(*args, **kwargs):
        return {"active": True, "blocking_fingerprint": "dds-overlay", "target_found": True, "target_visible": True, "target_enabled": True}
    async def ensure(url=""):
        return session.page
    async def surface():
        return {"active": True, "candidate_count": 1}
    async def neutralize(**kwargs):
        return {"pass": False, "status": "target_still_blocked"}
    refreshed = {"count": 0}
    async def refresh(**kwargs):
        refreshed["count"] += 1
        return {}
    monkeypatch.setattr(session, "_current_loading_state", current)
    monkeypatch.setattr(session, "_ensure_active_page", ensure)
    monkeypatch.setattr(session, "_active_unsaved_form_surface", surface)
    monkeypatch.setattr(session, "_neutralize_stale_dds_loading_overlay", neutralize)
    monkeypatch.setattr(session, "refresh_current_page_preserving_session", refresh)
    monkeypatch.setattr(session, "_capture_loading_watchdog_evidence", lambda **kwargs: _async_value({}))
    assert await session.wait_for_portal_loading_complete(reason="fill text control", target_selector="#map") is False
    assert refreshed["count"] == 0

@pytest.mark.asyncio
async def test_state_graph_refuses_listing_surface_before_control_binding(monkeypatch):
    from hip_id_agent import stateful_form_runtime as runtime

    class Page:
        pass

    async def gate(page, phase):
        return {"fatal": ["Data Map active form root is not the Create Map surface with required upload fields."]}

    async def no_controls(page, phase):
        return []

    monkeypatch.setattr(runtime, "assert_active_surface", gate)
    monkeypatch.setattr(runtime, "capture_stateful_controls", no_controls)
    with pytest.raises(RuntimeError, match="HIP_PHASE_ACTIVE_FORM_SURFACE_LOST"):
        await runtime.execute_phase_state_graph(Page(), {"nodes": []}, phase="data_map")
