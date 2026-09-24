from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import app as backend_app
from backend.app import LiveReadinessRequest, MissionStart
from hip_id_agent.live_readiness import (
    build_live_readiness_report,
    probe_runs_path,
    readiness_fingerprint,
)


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        autowebglm=SimpleNamespace(enabled=True, primary_framework=True),
        mcp=SimpleNamespace(
            use_playwright_mcp=True,
            playwright_mcp_primary_for_safe_actions=True,
        ),
        semantic_understanding=SimpleNamespace(
            enabled=True, fail_closed=True, revalidate_before_dispatch=True,
            require_post_action_effect=True, use_hip_intelligence_mcp_consensus=True,
            require_playwright_mcp_evidence=True, require_devtools_evidence=True,
            require_hip_intelligence_mcp_evidence=True,
        ),
        browser_use=SimpleNamespace(enabled=True),
    )


def test_readiness_fingerprint_changes_when_input_scope_or_api_mode_changes():
    base = dict(
        config="config.yaml",
        input_json="input.json",
        runs_dir="./runs",
        golden_screenshot_dir="./golden",
        upload_assets_dir="./uploads",
        phases=["data_map", "rule"],
        api_mode="capture",
    )
    a = readiness_fingerprint(**base)
    b = readiness_fingerprint(**{**base, "input_json": "other.json"})
    c = readiness_fingerprint(**{**base, "phases": ["data_map"]})
    d = readiness_fingerprint(**{**base, "api_mode": "validate"})
    assert len(a) == 64
    assert len({a, b, c, d}) == 4


def test_live_readiness_fails_closed_if_playwright_mcp_is_not_live():
    report = build_live_readiness_report(
        fingerprint="abc",
        static_preflight={"pass": True, "skill_vetting": {"pass": True}, "autogen": {"pass": True}},
        browser_probe={"pass": True, "selected": "edge"},
        mcp_probe={
            "playwright_mcp": {"available": False, "error": "missing tool"},
            "chrome_devtools_mcp": {"available": True, "required_tool_status": {
                "take_snapshot": True, "list_network_requests": True, "list_console_messages": True,
            }},
        },
        text_probe={"pass": True},
        vision_probe={"pass": True},
        path_probe={"pass": True},
        process_state={"running": False},
        config=_cfg(),
    )
    assert report["pass"] is False
    assert report["decision"] == "NO_GO"
    assert any(x["id"] == "playwright_mcp_live" for x in report["blockers"])
    assert "AutoWebGLM" in report["execution_contract"] and "Playwright MCP" in report["execution_contract"]


def test_live_readiness_passes_when_all_blockers_are_green():
    report = build_live_readiness_report(
        fingerprint="abc",
        static_preflight={"pass": True, "skill_vetting": {"pass": True}, "autogen": {"pass": True}},
        browser_probe={"pass": True, "selected": "chrome"},
        mcp_probe={
            "playwright_mcp": {"available": True, "required_tool_status": {
                "browser_navigate": True, "browser_snapshot": True, "browser_find": True, "browser_click": True,
                "browser_type": True, "browser_fill_form": True, "browser_select_option": True, "browser_take_screenshot": True,
            }},
            "chrome_devtools_mcp": {"available": True, "required_tool_status": {
                "take_snapshot": True, "list_network_requests": True, "list_console_messages": True,
            }},
            "hip_intelligence_mcp": {
                "available": True,
                "required_tool_status": {
                    "build_web_representation": True, "plan_form_action": True,
                    "resolve_semantic_control": True, "rank_semantic_candidates": True,
                    "verify_semantic_action_effect": True, "get_semantic_control_fingerprint": True,
                    "get_semantic_control_capabilities": True,
                    "hip_get_current_surface": True, "hip_get_form_schema": True,
                    "hip_find_control": True, "hip_find_owned_popup": True,
                    "hip_get_repeatable_rows": True, "hip_get_required_fields": True,
                    "hip_get_current_values": True, "hip_compare_expected_actual": True,
                    "hip_get_safe_actions": True, "hip_verify_action_effect": True,
                    "hip_get_route_identity": True, "hip_get_form_generation": True,
                },
            },
        },
        text_probe={"pass": True},
        vision_probe={"pass": True},
        path_probe={"pass": True},
        process_state={"running": False},
        config=_cfg(),
    )
    assert report["pass"] is True
    assert report["decision"] == "GO"
    assert report["blocker_count"] == 0


def test_runs_path_probe_uses_real_safe_io(tmp_path: Path):
    target = tmp_path / ("deep_" * 10) / "runs"
    out = probe_runs_path(target)
    assert out["pass"] is True
    assert out["writable"] is True
    assert out["safe_io"] is True


def test_live_readiness_endpoint_issues_bound_receipt(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(backend_app, "ROOT", tmp_path)
    monkeypatch.setattr(backend_app, "LIVE_READINESS_FILE", tmp_path / ".backend_runtime" / "live_readiness.json")
    monkeypatch.setattr(backend_app, "_cfg", lambda config="config.yaml": _cfg())
    monkeypatch.setattr(backend_app, "_preflight_for_phases", lambda req, phases: {"pass": True, "skill_vetting": {"pass": True}, "autogen": {"pass": True}, "issues": []})

    async def browser(_cfg): return {"pass": True, "selected": "edge"}
    async def mcps(_cfg, _run_dir): return {
        "playwright_mcp": {"available": True, "required_tool_status": {
                "browser_navigate": True, "browser_snapshot": True, "browser_find": True, "browser_click": True,
                "browser_type": True, "browser_fill_form": True, "browser_select_option": True, "browser_take_screenshot": True,
            }},
        "chrome_devtools_mcp": {"available": True, "required_tool_status": {
                "take_snapshot": True, "list_network_requests": True, "list_console_messages": True,
            }},
        "hip_intelligence_mcp": {
            "available": True,
            "required_tool_status": {
                "build_web_representation": True, "plan_form_action": True,
                "resolve_semantic_control": True, "rank_semantic_candidates": True,
                "verify_semantic_action_effect": True, "get_semantic_control_fingerprint": True,
                "get_semantic_control_capabilities": True,
                    "hip_get_current_surface": True, "hip_get_form_schema": True,
                    "hip_find_control": True, "hip_find_owned_popup": True,
                    "hip_get_repeatable_rows": True, "hip_get_required_fields": True,
                    "hip_get_current_values": True, "hip_compare_expected_actual": True,
                    "hip_get_safe_actions": True, "hip_verify_action_effect": True,
                    "hip_get_route_identity": True, "hip_get_form_generation": True,
            },
        },
    }
    async def vision(_req): return {"pass": True, "kind": "vision", "image_understanding_verified": True}
    monkeypatch.setattr(backend_app, "probe_browser_launch", browser)
    monkeypatch.setattr(backend_app, "validate_dual_browser_mcps", mcps)
    monkeypatch.setattr(backend_app, "test_text_model", lambda req: {"pass": True, "kind": "text", "expected_marker_seen": True})
    monkeypatch.setattr(backend_app, "test_vision_model", vision)
    monkeypatch.setattr(backend_app, "probe_runs_path", lambda p: {"pass": True, "path": str(p)})
    monkeypatch.setattr(backend_app, "_process_state", lambda: {"running": False})
    monkeypatch.setattr(backend_app, "_resolve_runs_root", lambda config="config.yaml", runs_dir="": tmp_path / "runs")

    req = LiveReadinessRequest(section="transport-profile", config="config.yaml", input_json="input.json", runs_dir="./runs")
    out = asyncio.run(backend_app.mission_live_readiness(req))
    assert out["pass"] is True
    assert out["decision"] == "GO"
    assert out["token"]
    receipt = backend_app._read_json(backend_app.LIVE_READINESS_FILE, {})
    assert receipt["token"] == out["token"]
    assert receipt["fingerprint"] == out["fingerprint"]


def test_mission_start_rejects_missing_live_readiness_receipt(monkeypatch):
    monkeypatch.setattr(backend_app, "_preflight_for_phases", lambda req, phases: {"pass": True, "skill_vetting": {"pass": True}, "context_budget": {}})
    with pytest.raises(HTTPException) as exc:
        backend_app.start_mission(MissionStart(section="transport-profile", input_json="input.json", api_mode="capture"))
    assert exc.value.status_code == 412
    assert "Live GO/NO-GO" in str(exc.value.detail)


def test_mission_start_accepts_matching_unexpired_receipt(monkeypatch, tmp_path: Path):
    captured = {}
    monkeypatch.setattr(backend_app, "LIVE_READINESS_FILE", tmp_path / "live_readiness.json")
    monkeypatch.setattr(backend_app, "_preflight_for_phases", lambda req, phases: {"pass": True, "skill_vetting": {"pass": True}, "context_budget": {"selected_phases": phases}})
    monkeypatch.setattr(backend_app, "build_section_mission_command", lambda **kwargs: captured.setdefault("builder", kwargs) or ["python"])
    monkeypatch.setattr(backend_app, "_start_cli", lambda command, runs_dir="", extra_environment=None: {"pid": 7, "running": True, "command": command})

    req = MissionStart(section="transport-profile", config="config.yaml", input_json="input.json", runs_dir="./runs", api_mode="capture", readiness_token="token123")
    phases = ["source_transport_profile", "target_transport_profile"]
    fp = backend_app._readiness_fingerprint_for(req, phases, api_mode="capture")
    backend_app._write_json(backend_app.LIVE_READINESS_FILE, {
        "pass": True, "token": "token123", "fingerprint": fp,
        "created_at_epoch": time.time(), "expires_at_epoch": time.time() + 300,
    })
    out = backend_app.start_mission(req)
    assert out["pid"] == 7
    assert captured["builder"]["section"] == "transport-profile"


def test_javascript_ui_requires_and_displays_live_go_no_go_gate():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    js = (root / "webui" / "app.js").read_text(encoding="utf-8")
    assert "Run Live GO/NO-GO" in html
    assert "Live GO/NO-GO readiness" in html
    assert "/api/mission/live-readiness" in js
    assert "readiness_token" in js
    assert "state.liveReadiness?.pass" in js
    assert "AutoWebGLM -> official Playwright MCP" not in js  # rendered value comes from backend receipt
