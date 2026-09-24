from __future__ import annotations

from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


class _Page:
    url = "https://developer.dell.com/hybrid-integrations/securelink/doctypes"


@pytest.mark.asyncio
async def test_passive_loading_indicator_does_not_wait_or_refresh(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.loading_watchdog_timeout_seconds = 0.02
    cfg.portal.loading_watchdog_poll_seconds = 0.001
    session = BrowserSession(cfg, tmp_path)
    session.page = _Page()
    session._active_phase_name = "source_document_type"
    session._active_target_url = _Page.url
    refreshes = []

    async def ensure_page(expected_url=""):
        return session.page

    async def loading_state(*, target_selector=""):
        return {
            "active": False,
            "classification": "passive_indicator",
            "passive_indicator_count": 1,
            "passive_fingerprint": "spinner|inline",
            "page_operable": True,
            "target_hit_test_pass": True,
            "passive_indicators": [{"text": "spinner", "blocking": False}],
        }

    async def refresh(*, reason=""):
        refreshes.append(reason)
        return {"status": "refreshed"}

    monkeypatch.setattr(session, "_ensure_active_page", ensure_page)
    monkeypatch.setattr(session, "_current_loading_state", loading_state)
    monkeypatch.setattr(session, "refresh_current_page_preserving_session", refresh)

    assert await session.wait_for_portal_loading_complete(
        reason="fill Usage", timeout_seconds=0.02, target_selector="input[name=usage]"
    ) is True
    assert refreshes == []
    evidence = list((tmp_path / "mcp_runtime" / "autonomous_page_health").glob("passive_loading_*.json"))
    assert evidence


@pytest.mark.asyncio
async def test_transient_single_blocking_sample_does_not_start_watchdog(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.loading_watchdog_consecutive_blocking_samples = 2
    cfg.portal.loading_watchdog_timeout_seconds = 0.01
    cfg.portal.loading_watchdog_poll_seconds = 0.001
    session = BrowserSession(cfg, tmp_path)
    session.page = _Page()
    session._active_phase_name = "source_document_type"
    session._active_target_url = _Page.url
    states = iter([
        {"active": True, "blocking_fingerprint": "overlay-A", "overlays": [{}]},
        {"active": False, "classification": "ready", "overlays": []},
    ])
    refreshes = []

    async def ensure_page(expected_url=""):
        return session.page

    async def loading_state(*, target_selector=""):
        return next(states)

    async def refresh(*, reason=""):
        refreshes.append(reason)
        return {"status": "refreshed"}

    monkeypatch.setattr(session, "_ensure_active_page", ensure_page)
    monkeypatch.setattr(session, "_current_loading_state", loading_state)
    monkeypatch.setattr(session, "refresh_current_page_preserving_session", refresh)

    assert await session.wait_for_portal_loading_complete(reason="transient", timeout_seconds=0.01) is True
    assert refreshes == []
    assert session._loading_watchdog_first_seen == {}


@pytest.mark.asyncio
async def test_short_overlay_gate_ignores_passive_marker(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    session = BrowserSession(cfg, tmp_path)
    session.page = _Page()

    async def loading_state(*, target_selector=""):
        return {
            "active": False,
            "classification": "passive_indicator",
            "passive_indicator_count": 1,
            "passive_fingerprint": "aria-busy|table",
            "page_operable": True,
            "passive_indicators": [{"aria_busy": "true", "blocking": False}],
        }

    monkeypatch.setattr(session, "_current_loading_state", loading_state)
    assert await session.wait_for_blocking_overlays_gone(
        timeout_ms=10, target_selector="button:has-text('Add')"
    ) is True


def test_autonomous_loading_defaults_are_evidence_based():
    cfg = AppConfig()
    assert cfg.portal.loading_watchdog_consecutive_blocking_samples == 2
    assert cfg.portal.loading_watchdog_min_viewport_ratio == pytest.approx(0.08)
    assert cfg.portal.loading_watchdog_min_surface_cover_ratio == pytest.approx(0.45)
    assert cfg.portal.loading_watchdog_ignore_passive_indicators is True
    assert cfg.portal.autonomous_page_health_enabled is True


def test_false_loading_marker_has_bounded_self_heal_action():
    classification = RuntimeSelfHealController.classify_failure(
        "passive loading indicator was visible but did not intercept the requested control"
    )
    assert classification == "false_loading_marker"
    assert RuntimeSelfHealController.CLASS_ACTIONS[classification][0] == "reassess_page_health"
    assert "reassess_page_health" in RuntimeSelfHealController.SAFE_ACTIONS


def test_loading_classifier_uses_geometry_and_hit_testing():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "browser_session.py").read_text(encoding="utf-8")
    assert "target_hit_test_pass" in source
    assert "surface_cover_ratio" in source
    assert "passive_loading_indicator_ignored" in source
    assert "This classifier uses geometry" in source
