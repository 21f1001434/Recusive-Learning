from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from hip_id_agent import __version__
from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.live_runtime_certification import latest_runtime_certificate_path, runtime_environment_fingerprint


def test_v226_version():
    assert __version__ == "2.4.3"


def test_pyautogui_recovery_flags_enabled_by_default():
    cfg = AppConfig()
    assert cfg.pyautogui.prefer_mcp_before_local_web_fallback is True
    assert cfg.pyautogui.use_for_structural_web_recovery is True
    assert cfg.pyautogui.use_for_form_fill_recovery is True
    assert cfg.pyautogui.use_for_key_recovery is True


def test_live_certificate_auto_refresh_enabled_by_default():
    cfg = AppConfig()
    assert cfg.live_runtime_certification.auto_refresh_on_live_readiness is True
    assert cfg.live_runtime_certification.auto_refresh_only_when_mission_idle is True


def test_runtime_certificate_path_respects_config(tmp_path: Path):
    cfg = AppConfig()
    cfg.live_runtime_certification.latest_certificate_relative_path = "custom/cert.json"
    assert latest_runtime_certificate_path(tmp_path, cfg) == tmp_path / "custom" / "cert.json"


def test_runtime_fingerprint_contains_package_version():
    src = inspect.getsource(runtime_environment_fingerprint)
    assert '"package_version"' in src
    assert "__version__" in src


def test_click_uses_pyautogui_mcp_as_primary_before_playwright_fallback():
    src = inspect.getsource(BrowserSession.click_and_wait)
    primary = src.index("_try_pyautogui_click")
    mcp_fallback = src.index("playwright_mcp_backend.click")
    local = src.index('executor="python-playwright-fallback"')
    assert primary < mcp_fallback < local
    assert 'should_primary("click"' in src


def test_fill_uses_pyautogui_mcp_primary_for_business_and_search_fields():
    src = inspect.getsource(BrowserSession.fill_and_log)
    py = src.index("_try_pyautogui_fill")
    mcp_fallback = src.index("playwright_mcp_backend.fill")
    local = src.index("await locator.first.fill(value)")
    assert py < mcp_fallback < local
    assert "should_primary(action_name" in src


def test_press_uses_pyautogui_mcp_primary_before_playwright_and_local():
    src = inspect.getsource(BrowserSession.press_and_log)
    py = src.index("_try_pyautogui_press")
    mcp_fallback = src.index("playwright_mcp_backend.press")
    local = src.index("await locator.first.press(key)")
    assert py < mcp_fallback < local


@pytest.mark.asyncio
async def test_live_readiness_auto_refreshes_stale_certificate(monkeypatch, tmp_path: Path):
    import backend.app as appmod

    cfg = AppConfig()
    cfg.live_runtime_certification.auto_refresh_on_live_readiness = True
    cfg.live_runtime_certification.require_for_live_go_no_go = True
    cfg.live_runtime_certification.require_pyautogui_mcp = True

    req = appmod.LiveReadinessRequest(section="all", config="config.yaml", runs_dir=str(tmp_path))
    monkeypatch.setattr(appmod, "_cfg", lambda *_: cfg)
    monkeypatch.setattr(appmod, "_preflight_for_phases", lambda *a, **k: {"pass": True})
    monkeypatch.setattr(appmod, "_readiness_fingerprint_for", lambda *a, **k: "fp")

    async def browser_probe(_cfg): return {"pass": True}
    async def mcp_probe(_cfg, _root): return {}
    async def vision_probe(_req): return {"pass": True}
    async def refresh(**kwargs):
        calls["refresh"] += 1
        return {"pass": True, "decision": "GO", "blockers": []}

    monkeypatch.setattr(appmod, "probe_browser_launch", browser_probe)
    monkeypatch.setattr(appmod, "validate_dual_browser_mcps", mcp_probe)
    monkeypatch.setattr(appmod, "test_text_model", lambda _req: {"pass": True})
    monkeypatch.setattr(appmod, "test_vision_model", vision_probe)
    monkeypatch.setattr(appmod, "_resolve_runs_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(appmod, "probe_runs_path", lambda _root: {"pass": True})
    monkeypatch.setattr(appmod, "_process_state", lambda: {"running": False})

    calls = {"verify": 0, "refresh": 0}
    def verify(_cfg, _root):
        calls["verify"] += 1
        if calls["verify"] == 1:
            return {"pass": False, "status": "invalid", "reason": "expired"}
        return {"pass": True, "status": "valid", "reason": ""}
    monkeypatch.setattr(appmod, "verify_latest_live_runtime_certificate", verify)
    monkeypatch.setattr(appmod, "certify_live_runtime", refresh)

    seen = {}
    def build(**kwargs):
        seen.update(kwargs["runtime_certificate_probe"])
        return {"pass": True, "decision": "GO", "checks": [], "blocker_count": 0, "warning_count": 0, "fingerprint": "fp"}
    monkeypatch.setattr(appmod, "build_live_readiness_report", build)
    monkeypatch.setattr(appmod, "_store_live_readiness_receipt", lambda report: {"token": "t", "pass": True})

    out = await appmod._run_live_readiness(req, ["data_map"])
    assert out["pass"] is True
    assert calls["refresh"] == 1
    assert calls["verify"] == 2
    assert seen["pass"] is True
    assert seen["auto_refresh_attempted"] is True
    assert seen["auto_refresh_decision"] == "GO"


@pytest.mark.asyncio
async def test_live_readiness_does_not_auto_refresh_when_static_preflight_fails(monkeypatch, tmp_path: Path):
    import backend.app as appmod

    cfg = AppConfig()
    cfg.live_runtime_certification.auto_refresh_on_live_readiness = True
    req = appmod.LiveReadinessRequest(section="all", config="config.yaml", runs_dir=str(tmp_path))
    monkeypatch.setattr(appmod, "_cfg", lambda *_: cfg)
    monkeypatch.setattr(appmod, "_preflight_for_phases", lambda *a, **k: {"pass": False})
    monkeypatch.setattr(appmod, "_readiness_fingerprint_for", lambda *a, **k: "fp")
    async def browser_probe(_cfg): return {"pass": True}
    async def mcp_probe(_cfg, _root): return {}
    async def vision_probe(_req): return {"pass": True}
    monkeypatch.setattr(appmod, "probe_browser_launch", browser_probe)
    monkeypatch.setattr(appmod, "validate_dual_browser_mcps", mcp_probe)
    monkeypatch.setattr(appmod, "test_text_model", lambda _req: {"pass": True})
    monkeypatch.setattr(appmod, "test_vision_model", vision_probe)
    monkeypatch.setattr(appmod, "_resolve_runs_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(appmod, "probe_runs_path", lambda _root: {"pass": True})
    monkeypatch.setattr(appmod, "_process_state", lambda: {"running": False})
    monkeypatch.setattr(appmod, "verify_latest_live_runtime_certificate", lambda *_: {"pass": False})
    called = {"refresh": 0}
    async def refresh(**kwargs):
        called["refresh"] += 1
        return {"pass": True}
    monkeypatch.setattr(appmod, "certify_live_runtime", refresh)
    monkeypatch.setattr(appmod, "build_live_readiness_report", lambda **kwargs: {"pass": False, "decision": "NO_GO", "checks": [], "blocker_count": 1, "warning_count": 0, "fingerprint": "fp"})
    monkeypatch.setattr(appmod, "_store_live_readiness_receipt", lambda report: {"token": "", "pass": False})
    out = await appmod._run_live_readiness(req, ["data_map"])
    assert out["pass"] is False
    assert called["refresh"] == 0
