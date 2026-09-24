from __future__ import annotations

import asyncio
import hashlib
import json
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.config import AppConfig, load_config
from hip_id_agent.live_readiness import build_live_readiness_report
from hip_id_agent.live_runtime_certification import (
    CERT_SCHEMA,
    certify_live_runtime,
    latest_runtime_certificate_path,
    runtime_environment_fingerprint,
    verify_latest_live_runtime_certificate,
)


def _cert(cfg: AppConfig, *, expires: float = 9999999999.0):
    payload = {
        "schema_version": CERT_SCHEMA,
        "certificate_id": "HIP-LIVE-TEST",
        "pass": True,
        "decision": "GO",
        "created_at": "2026-09-06T00:00:00+00:00",
        "created_at_epoch": 1.0,
        "expires_at_epoch": expires,
        "runtime_fingerprint": runtime_environment_fingerprint(cfg),
        "target_url": cfg.portal.base_url,
        "run_dir": "runs/live_runtime_certification/CERT-TEST",
        "checks": [],
        "blocker_count": 0,
        "warning_count": 0,
        "blockers": [],
        "warnings": [],
        "browser_evidence": {},
        "non_mutating_contract": "read-only",
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["certificate_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    return payload


def test_01_live_runtime_config_exists():
    cfg = AppConfig()
    assert cfg.live_runtime_certification.enabled is True
    assert cfg.live_runtime_certification.ttl_seconds >= 60


def test_02_shipped_config_requires_runtime_certificate():
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    assert cfg.live_runtime_certification.require_for_live_go_no_go is True
    assert cfg.live_runtime_certification.require_pyautogui_mcp is False


def test_03_runtime_fingerprint_is_stable_and_secret_free():
    cfg = AppConfig()
    a = runtime_environment_fingerprint(cfg)
    b = runtime_environment_fingerprint(cfg.model_copy(deep=True))
    assert a == b and len(a) == 64


def test_04_runtime_fingerprint_changes_with_mcp_runtime():
    a = AppConfig()
    b = a.model_copy(deep=True)
    b.mcp.playwright_mcp_args = ["@playwright/mcp@DIFFERENT"]
    assert runtime_environment_fingerprint(a) != runtime_environment_fingerprint(b)


def test_05_missing_certificate_fails(tmp_path: Path):
    out = verify_latest_live_runtime_certificate(AppConfig(), tmp_path)
    assert out["pass"] is False and out["status"] == "missing"


def test_06_valid_certificate_passes(tmp_path: Path):
    cfg = AppConfig()
    path = latest_runtime_certificate_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_cert(cfg)), encoding="utf-8")
    out = verify_latest_live_runtime_certificate(cfg, tmp_path)
    assert out["pass"] is True and out["digest_ok"] is True


def test_07_expired_certificate_fails(tmp_path: Path):
    cfg = AppConfig()
    path = latest_runtime_certificate_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_cert(cfg, expires=1.0)), encoding="utf-8")
    out = verify_latest_live_runtime_certificate(cfg, tmp_path)
    assert out["pass"] is False and out["fresh"] is False


def test_08_tampered_certificate_fails(tmp_path: Path):
    cfg = AppConfig()
    cert = _cert(cfg)
    cert["decision"] = "NO_GO"
    path = latest_runtime_certificate_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(cert), encoding="utf-8")
    out = verify_latest_live_runtime_certificate(cfg, tmp_path)
    assert out["pass"] is False and out["digest_ok"] is False


def test_09_runtime_mismatch_fails(tmp_path: Path):
    cfg = AppConfig()
    path = latest_runtime_certificate_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_cert(cfg)), encoding="utf-8")
    changed = cfg.model_copy(deep=True)
    changed.portal.base_url = "https://example.invalid/other"
    out = verify_latest_live_runtime_certificate(changed, tmp_path)
    assert out["pass"] is False and out["fingerprint_ok"] is False


def _readiness(cfg: AppConfig, cert_probe):
    cfg.live_runtime_certification.enabled = True
    return build_live_readiness_report(
        fingerprint="fp",
        static_preflight={"pass": True, "skill_vetting": {"pass": True}, "autogen": {"pass": True}},
        browser_probe={"pass": True},
        mcp_probe={
            "playwright_mcp": {"available": True, "required_tool_status": {x: True for x in ["browser_navigate","browser_snapshot","browser_find","browser_click","browser_type","browser_fill_form","browser_select_option","browser_take_screenshot"]}},
            "chrome_devtools_mcp": {"available": True, "required_tool_status": {x: True for x in ["take_snapshot","list_network_requests","list_console_messages"]}},
            "hip_intelligence_mcp": {"available": True, "required_tool_status": {x: True for x in ["build_web_representation","plan_form_action","resolve_semantic_control","rank_semantic_candidates","verify_semantic_action_effect","get_semantic_control_fingerprint","get_semantic_control_capabilities","hip_get_current_surface","hip_get_form_schema","hip_find_control","hip_find_owned_popup","hip_get_repeatable_rows","hip_get_required_fields","hip_get_current_values","hip_compare_expected_actual","hip_get_safe_actions","hip_verify_action_effect","hip_get_route_identity","hip_get_form_generation"]}},
            "pyautogui_mcp": {"available": True, "required_tool_status": {x: True for x in ["pyautogui_size","pyautogui_position","pyautogui_click","pyautogui_write","pyautogui_press","pyautogui_hotkey","pyautogui_screenshot"]}},
        },
        text_probe={"pass": True}, vision_probe={"pass": True}, path_probe={"pass": True}, process_state={"running": False}, config=cfg,
        runtime_certificate_probe=cert_probe,
    )


def test_10_required_runtime_certificate_blocks_readiness():
    cfg = AppConfig()
    cfg.live_runtime_certification.require_for_live_go_no_go = True
    out = _readiness(cfg, {"pass": False, "status": "missing"})
    assert out["pass"] is False
    assert any(x["id"] == "live_runtime_certificate" for x in out["blockers"])


def test_11_passing_runtime_certificate_allows_readiness():
    cfg = AppConfig()
    cfg.live_runtime_certification.require_for_live_go_no_go = True
    out = _readiness(cfg, {"pass": True, "status": "valid"})
    assert out["pass"] is True


def test_12_optional_runtime_certificate_is_warning():
    cfg = AppConfig()
    cfg.live_runtime_certification.require_for_live_go_no_go = False
    out = _readiness(cfg, {"pass": False, "status": "missing"})
    assert out["pass"] is True
    assert any(x["id"] == "live_runtime_certificate" for x in out["warnings"])


class _FakeLocator:
    async def inner_text(self, timeout=0):
        return "BizLink Partner Configuration"


class _FakePage:
    url = "https://developer.dell.com/hybrid-integrations/bizlink/partner"
    def locator(self, _): return _FakeLocator()


class _FakePW:
    async def snapshot(self, **kwargs): return {"text": "Partner [ref=e1]"}
    async def find(self, **kwargs): return {"text": "Partner [ref=e1]"}


class _FakeCDP:
    async def select_page_for_url(self, expected): return {"pass": True, "selected": {"url": expected}}
    async def get_dom_snapshot(self): return {"snapshot_text": "Partner"}
    async def get_network_events(self): return []
    async def get_console_messages(self): return []


class _FakePy:
    calls = []
    async def size(self): self.calls.append("size"); return (1920, 1080)
    async def position(self): self.calls.append("position"); return (100, 100)
    async def screenshot(self): self.calls.append("screenshot"); return {"ok": True}


class _FakeBrowser:
    last = None
    def __init__(self, cfg, run_dir):
        type(self).last = self
        self.config = cfg; self.run_dir = run_dir; self.page = _FakePage(); self.context = object(); self.backend_name = "playwright-mcp+chrome-devtools-mcp+pyautogui-mcp"
        self._cdp_health = {"ok": True}; self._authenticated_once = True
        self.playwright_mcp_backend = _FakePW(); self.mcp_backend = _FakeCDP(); self.pyautogui_mcp_backend = _FakePy()
        self.playwright_mcp_capabilities = {"tools": ["browser_snapshot","browser_find","browser_click","browser_type","browser_fill_form","browser_select_option","browser_take_screenshot"]}
        self.mcp_capabilities = {"available": True}; self.pyautogui_mcp_capabilities = {"available": True}
        self._portal_mutation_authorization = {"enabled": False}
    async def start(self): return self.page
    async def goto_base_and_complete_sso(self, target): self.page.url = target
    async def _looks_logged_in(self, page): return True
    async def collect_dom_click_log(self): return []
    async def collect_dom_event_log(self): return []
    async def flush_logs(self, force=False): return None
    async def write_failure_bundle(self, *a, **k): return {}
    async def close(self): return None


class _FakeHipClient:
    tools = {x: object() for x in ["build_web_representation","resolve_semantic_control","rank_semantic_candidates","verify_semantic_action_effect","hip_get_current_surface","hip_get_form_schema","hip_find_control","hip_get_safe_actions","hip_get_route_identity","hip_get_form_generation"]}


class _FakeHip:
    def __init__(self): self.client = _FakeHipClient()
    @classmethod
    def from_config(cls, *a, **k): return cls()
    async def start(self): pass
    async def close(self): pass
    async def get_current_surface(self, payload): return {"status":"ok","control_count":1,"dom_generation":3}
    async def get_route_identity(self, payload): return {"status":"ok","route":"partner"}


def test_13_full_runtime_certifier_emits_go_and_latest_receipt(monkeypatch, tmp_path: Path):
    import hip_id_agent.live_runtime_certification as m
    monkeypatch.setattr(m, "_is_windows_interactive", lambda: {"pass": True, "platform": "Windows"})
    async def ok_text(cfg): return {"pass": True}
    async def ok_vision(cfg): return {"pass": True}
    async def state(page): return {"controls":[{"label":"Partner","visible":True}],"dom_generation":3}
    monkeypatch.setattr(m, "_probe_text_model", ok_text)
    monkeypatch.setattr(m, "_probe_vision_model", ok_vision)
    monkeypatch.setattr(m, "BrowserSession", _FakeBrowser)
    monkeypatch.setattr(m, "HIPIntelligenceMCPBackend", _FakeHip)
    monkeypatch.setattr(m, "capture_semantic_state", state)
    cfg = AppConfig()
    out = asyncio.run(certify_live_runtime(config=cfg, runs_root=tmp_path, ttl_seconds=600, require_pyautogui_mcp=True))
    assert out["pass"] is True and out["decision"] == "GO"
    assert latest_runtime_certificate_path(tmp_path).is_file()


def test_14_runtime_certifier_pyautogui_probe_is_read_only(monkeypatch, tmp_path: Path):
    _FakePy.calls = []
    import hip_id_agent.live_runtime_certification as m
    monkeypatch.setattr(m, "_is_windows_interactive", lambda: {"pass": True, "platform": "Windows"})
    async def ok(_): return {"pass": True}
    async def state(page): return {"controls":[{"label":"Partner"}],"dom_generation":1}
    monkeypatch.setattr(m, "_probe_text_model", ok); monkeypatch.setattr(m, "_probe_vision_model", ok)
    monkeypatch.setattr(m, "BrowserSession", _FakeBrowser); monkeypatch.setattr(m, "HIPIntelligenceMCPBackend", _FakeHip); monkeypatch.setattr(m, "capture_semantic_state", state)
    asyncio.run(certify_live_runtime(config=AppConfig(), runs_root=tmp_path))
    assert _FakePy.calls == ["size", "position", "screenshot"]
    src = inspect.getsource(m.certify_live_runtime)
    assert "await py.click" not in src and "await py.write" not in src and "await py.press" not in src and "await py.hotkey" not in src


def test_15_runtime_certifier_disables_mutations_in_copy():
    import hip_id_agent.live_runtime_certification as m
    src = inspect.getsource(m.certify_live_runtime)
    assert "cfg.pyautogui.allow_mutation_clicks = False" in src
    assert "cfg.mcp.browser_backend = \"mcp\"" in src


def test_16_backend_exposes_runtime_certification_endpoint():
    import backend.app as app
    paths = {getattr(r, "path", "") for r in app.app.routes}
    assert "/api/mission/live-runtime-certification" in paths


def test_17_backend_live_readiness_consumes_latest_certificate():
    import backend.app as app
    src = inspect.getsource(app._run_live_readiness)
    assert "verify_latest_live_runtime_certificate" in src
    assert "runtime_certificate_probe=runtime_certificate_probe" in src


def test_18_cli_exposes_runtime_certification_command():
    from hip_id_agent import cli
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert '@app.command("certify-live-runtime")' in src
    assert "certify_live_runtime" in src


def test_19_webui_has_runtime_certification_control():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    js = (root / "webui" / "app.js").read_text(encoding="utf-8")
    assert 'id="liveRuntimeCertBtn"' in html
    assert "/api/mission/live-runtime-certification" in js
    assert "runLiveRuntimeCertification" in js


def test_20_certificate_non_mutating_contract_is_explicit():
    import hip_id_agent.live_runtime_certification as m
    src = Path(m.__file__).read_text(encoding="utf-8")
    for token in ["No HIP Create/Save/Delete/Deploy/Submit action is authorized", "screen-size, cursor-position and screenshot probes"]:
        assert token in src
