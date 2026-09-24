from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.autowebglm_bridge import AutoWebGLMRecoveryBridge
from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController
from hip_id_agent.vision_runtime import VisionRuntimeBridge


class _Page:
    url = "https://developer.dell.com/hybrid-integrations/securelink/doctypes"

    async def screenshot(self, *args, **kwargs):
        return b"fake-png"


@pytest.mark.asyncio
async def test_five_minute_loader_is_not_refreshed_when_vision_rejects_it(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.loading_watchdog_consecutive_blocking_samples = 1
    cfg.portal.loading_watchdog_poll_seconds = 0.001
    cfg.portal.loading_watchdog_max_refreshes_per_phase = 1
    session = BrowserSession(cfg, tmp_path)
    session.page = _Page()
    session._active_phase_name = "source_document_type"
    session._active_target_url = _Page.url
    refreshes = []

    async def ensure_page(expected_url=""):
        return session.page

    async def loading_state(*, target_selector=""):
        return {"active": True, "blocking_fingerprint": "loader", "overlays": [{"text": "Loading"}]}

    async def capture(**kwargs):
        return kwargs

    async def vision_reject(**kwargs):
        return {
            "available": True,
            "confirmed_blocking_loading": False,
            "loading_visible": True,
            "blocking": False,
            "main_form_usable": True,
            "confidence": 0.98,
        }

    async def refresh(**kwargs):
        refreshes.append(kwargs)
        return {"status": "refreshed"}

    async def no_surface():
        return {"active": False}

    monkeypatch.setattr(session, "_ensure_active_page", ensure_page)
    monkeypatch.setattr(session, "_current_loading_state", loading_state)
    monkeypatch.setattr(session, "_capture_loading_watchdog_evidence", capture)
    monkeypatch.setattr(session, "_confirm_loading_with_vision", vision_reject)
    monkeypatch.setattr(session, "refresh_current_page_preserving_session", refresh)
    monkeypatch.setattr(session, "_active_unsaved_form_surface", no_surface)

    assert await session.wait_for_portal_loading_complete(reason="simulated 5 minute wait", timeout_seconds=0.005) is True
    assert refreshes == []


@pytest.mark.asyncio
async def test_vision_confirmed_five_minute_loader_refreshes_unsaved_form_then_requires_replay(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.loading_watchdog_consecutive_blocking_samples = 1
    cfg.portal.loading_watchdog_poll_seconds = 0.001
    cfg.portal.loading_watchdog_max_refreshes_per_phase = 1
    session = BrowserSession(cfg, tmp_path)
    session.page = _Page()
    session._active_phase_name = "source_document_type"
    session._active_target_url = _Page.url
    refreshes = []

    async def ensure_page(expected_url=""):
        return session.page

    async def loading_state(*, target_selector=""):
        return {"active": True, "blocking_fingerprint": "loader", "overlays": [{"text": "Loading"}]}

    async def capture(**kwargs):
        return kwargs

    async def surface():
        return {"active": True, "candidate_count": 1, "root_selector": "app-generic-drawer"}

    async def cannot_neutralize(**kwargs):
        return {"pass": False, "status": "still_blocked"}

    async def vision_confirm(**kwargs):
        return {
            "available": True,
            "confirmed_blocking_loading": True,
            "loading_visible": True,
            "blocking": True,
            "main_form_usable": False,
            "confidence": 0.99,
        }

    async def refresh(**kwargs):
        refreshes.append(kwargs)
        return {"status": "refreshed"}

    monkeypatch.setattr(session, "_ensure_active_page", ensure_page)
    monkeypatch.setattr(session, "_current_loading_state", loading_state)
    monkeypatch.setattr(session, "_capture_loading_watchdog_evidence", capture)
    monkeypatch.setattr(session, "_active_unsaved_form_surface", surface)
    monkeypatch.setattr(session, "_neutralize_stale_dds_loading_overlay", cannot_neutralize)
    monkeypatch.setattr(session, "_confirm_loading_with_vision", vision_confirm)
    monkeypatch.setattr(session, "refresh_current_page_preserving_session", refresh)

    assert await session.wait_for_portal_loading_complete(reason="simulated 5 minute wait", timeout_seconds=0.005) is False
    assert len(refreshes) == 1
    key = session._loading_watchdog_key()
    assert session._loading_watchdog_replay_required[key] == "vision_confirmed_loading_refresh_unsaved_form"


def test_vision_refresh_replay_has_deterministic_self_heal_classification():
    classification = RuntimeSelfHealController.classify_failure(
        "HIP_VISION_LOADING_REFRESH_REPLAY_REQUIRED: vision confirmed loader and Edge refreshed"
    )
    assert classification == "vision_loading_refresh_replay"
    assert RuntimeSelfHealController.CLASS_ACTIONS[classification][0] == "reopen_phase_from_input"


def test_chrome_and_vision_defaults_are_primary():
    cfg = AppConfig()
    assert cfg.portal.chromium_channel == "chrome"
    assert cfg.portal.browser_user_data_dir.endswith("chrome_profile")
    assert cfg.portal.fallback_to_edge is True
    assert cfg.portal.loading_watchdog_timeout_seconds == 300
    assert cfg.portal.loading_watchdog_vision_confirm_before_refresh is True
    assert cfg.vision_runtime.enabled is True
    assert cfg.vision_runtime.use_for_recovery is True
    assert cfg.vision_runtime.use_for_loading_watchdog is True
    assert cfg.vision_runtime.loading_refresh_after_seconds == 300


def test_vision_runtime_status_exposes_multimodal_role(monkeypatch):
    cfg = AppConfig()
    monkeypatch.setenv("HIP_VISION_MODEL", "enterprise-vision")
    bridge = VisionRuntimeBridge(cfg.vision_runtime, aia_config=cfg.aia)
    status = bridge.status()
    assert status["enabled"] is True
    assert status["use_for_loading_watchdog"] is True
    assert status["loading_refresh_after_seconds"] == 300
    assert "enterprise-vision" in status["explicit_model_candidates"]


def test_autowebglm_exposes_official_ten_action_protocol_and_runtime_compatibility():
    cfg = AppConfig()
    bridge = AutoWebGLMRecoveryBridge(cfg.autowebglm, aia_config=cfg.aia)
    status = bridge.status()
    assert status["runtime_compatibility"]["official_ten_action_protocol"] is True
    assert "user_input" in cfg.autowebglm.allowed_actions
    parsed = bridge.parse_action('user_input("Please complete Dell SSO")')
    assert parsed["valid"] is True
    assert parsed["action"] == "user_input"
    allowed, reason = bridge._safe_action(parsed)
    assert allowed is True, reason
