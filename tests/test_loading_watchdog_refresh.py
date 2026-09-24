from __future__ import annotations

from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


class _Page:
    url = "https://developer.dell.com/hybrid-integrations/securelink/doctypes"


@pytest.mark.asyncio
async def test_loading_watchdog_refreshes_after_threshold_and_continues(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.loading_watchdog_timeout_seconds = 0.03
    cfg.portal.loading_watchdog_poll_seconds = 0.005
    cfg.portal.loading_watchdog_max_refreshes_per_phase = 1
    session = BrowserSession(cfg, tmp_path)
    session.page = _Page()
    session._active_phase_name = "source_document_type"
    session._active_target_url = _Page.url
    state = {"refreshed": False}

    async def ensure_page(expected_url=""):
        return session.page

    async def loading_state():
        return {
            "active": not state["refreshed"],
            "url": session.page.url,
            "overlays": [{"text": "Loading"}] if not state["refreshed"] else [],
        }

    async def refresh(*, reason=""):
        state["refreshed"] = True
        return {"status": "refreshed", "reason": reason}

    async def capture(**kwargs):
        return kwargs

    monkeypatch.setattr(session, "_ensure_active_page", ensure_page)
    monkeypatch.setattr(session, "_current_loading_state", loading_state)
    async def vision_confirm(**kwargs):
        return {"available": True, "confirmed_blocking_loading": True, "loading_visible": True, "blocking": True, "confidence": 0.99}

    monkeypatch.setattr(session, "refresh_current_page_preserving_session", refresh)
    monkeypatch.setattr(session, "_capture_loading_watchdog_evidence", capture)
    monkeypatch.setattr(session, "_confirm_loading_with_vision", vision_confirm)

    assert await session.wait_for_portal_loading_complete(reason="test", timeout_seconds=0.03) is True
    key = session._loading_watchdog_key()
    assert session._loading_watchdog_refresh_counts[key] == 1


@pytest.mark.asyncio
async def test_loading_watchdog_fails_closed_when_loading_survives_refresh(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.loading_watchdog_timeout_seconds = 0.02
    cfg.portal.loading_watchdog_poll_seconds = 0.005
    cfg.portal.loading_watchdog_max_refreshes_per_phase = 1
    session = BrowserSession(cfg, tmp_path)
    session.page = _Page()
    session._active_phase_name = "rule"
    session._active_target_url = _Page.url
    refreshes = []

    async def ensure_page(expected_url=""):
        return session.page

    async def loading_state():
        return {"active": True, "url": session.page.url, "overlays": [{"text": "Loading"}]}

    async def refresh(*, reason=""):
        refreshes.append(reason)
        return {"status": "refreshed"}

    async def capture(**kwargs):
        return kwargs

    monkeypatch.setattr(session, "_ensure_active_page", ensure_page)
    monkeypatch.setattr(session, "_current_loading_state", loading_state)
    async def vision_confirm(**kwargs):
        return {"available": True, "confirmed_blocking_loading": True, "loading_visible": True, "blocking": True, "confidence": 0.99}

    monkeypatch.setattr(session, "refresh_current_page_preserving_session", refresh)
    monkeypatch.setattr(session, "_capture_loading_watchdog_evidence", capture)
    monkeypatch.setattr(session, "_confirm_loading_with_vision", vision_confirm)

    assert await session.wait_for_portal_loading_complete(reason="test", timeout_seconds=0.02) is False
    assert len(refreshes) == 1


def test_loading_watchdog_defaults_to_five_minutes_vision_confirmed_and_one_refresh():
    cfg = AppConfig()
    assert cfg.portal.loading_watchdog_timeout_seconds == 300
    assert cfg.portal.loading_watchdog_vision_confirm_before_refresh is True
    assert cfg.portal.loading_watchdog_max_refreshes_per_phase == 1


def test_loading_timeout_is_classified_for_safe_refresh_recovery():
    classification = RuntimeSelfHealController.classify_failure(
        "HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH: portal loading remained active after the loading watchdog"
    )
    assert classification == "blocking_overlay"
    assert RuntimeSelfHealController.CLASS_ACTIONS[classification][0] == "refresh_page_and_reopen"


def test_stale_loading_overlay_is_not_bypassed_by_pointer_event_mutation():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "browser_session.py").read_text(encoding="utf-8")
    assert "data-hip-stale-overlay-recovered" not in source
    assert "el.style.pointerEvents='none'" not in source
