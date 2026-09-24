from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend import app as backend_app
from backend.app import MissionStart
from hip_id_agent.live_readiness import readiness_fingerprint
from hip_id_agent.live_witness import build_live_witness_report
from hip_id_agent.models import ActionEvent
from hip_id_agent.streamlit_dashboard import build_mission_command


def test_witness_fingerprint_is_distinct_from_standard_profile():
    base = dict(
        config="config.yaml", input_json="input.json", runs_dir="./runs",
        golden_screenshot_dir="./golden", upload_assets_dir="./uploads",
        phases=["data_map"], api_mode="capture",
    )
    standard = readiness_fingerprint(**base, execution_profile="standard")
    witness = readiness_fingerprint(**base, execution_profile="live_witness")
    assert standard != witness


def test_witness_builder_forces_no_submit_capture_and_capture_api_mode(tmp_path: Path):
    cmd = build_mission_command(
        project_root=tmp_path,
        config="config.yaml", input_json="input.json", runs_dir="runs",
        golden_screenshot_dir="golden", upload_assets_dir="uploads",
        api_mode="validate", witness_mode=True, python_executable="python",
    )
    assert "--live-witness" in cmd
    assert "--no-capture-submit-api" in cmd
    assert "--api-capture-best-effort" in cmd
    idx = cmd.index("--api-mode")
    assert cmd[idx + 1] == "capture"


def test_witness_builder_rejects_api_write_or_mutation_authorization(tmp_path: Path):
    with pytest.raises(ValueError):
        build_mission_command(
            project_root=tmp_path,
            config="config.yaml", input_json="input.json", runs_dir="runs",
            golden_screenshot_dir="golden", upload_assets_dir="uploads",
            api_mode="write", allow_api_mutation=True, witness_mode=True,
            python_executable="python",
        )


def test_witness_certificate_passes_safe_navigation_fill_and_search_post():
    actions = [
        ActionEvent(action_id="1", type="click", target="+ Add Data Map"),
        ActionEvent(action_id="2", type="fill", target="Data Map Name", value_redacted="TEST"),
        ActionEvent(action_id="3", type="click", target="Expand row"),
    ]
    network = [
        {"method":"GET", "url":"https://hip.example/api/maps", "status":200},
        {"method":"POST", "url":"https://hip.example/api/maps/search", "request_body_redacted":{"page":1,"pageSize":20}, "status":200},
    ]
    report = build_live_witness_report(action_events=actions, network_events=network, mission_complete=True, phase_count=7)
    assert report["pass"] is True
    assert report["mutation_control_click_count"] == 0
    assert report["mutating_request_count"] == 0


def test_witness_certificate_fails_on_save_click_even_without_network():
    report = build_live_witness_report(
        action_events=[ActionEvent(action_id="x", type="click", target="Save")],
        network_events=[], mission_complete=True, phase_count=1,
    )
    assert report["pass"] is False
    assert report["safety_pass"] is False
    assert report["mutation_control_click_count"] == 1


def test_witness_certificate_fails_on_mutating_api_request():
    report = build_live_witness_report(
        action_events=[],
        network_events=[{"method":"DELETE", "url":"https://hip.example/api/maps/123", "status":204}],
        mission_complete=True, phase_count=1,
    )
    assert report["pass"] is False
    assert report["mutating_request_count"] == 1


def test_backend_start_binds_receipt_to_witness_profile_and_passes_builder_flag(monkeypatch, tmp_path: Path):
    captured = {}
    monkeypatch.setattr(backend_app, "LIVE_READINESS_FILE", tmp_path / "receipt.json")
    monkeypatch.setattr(backend_app, "_preflight_for_phases", lambda req, phases: {"pass": True, "skill_vetting": {"pass": True}, "context_budget": {}})
    monkeypatch.setattr(backend_app, "build_section_mission_command", lambda **kwargs: captured.setdefault("builder", kwargs) or ["python"])
    monkeypatch.setattr(backend_app, "_start_cli", lambda command, runs_dir="", extra_environment=None: {"pid": 11, "running": True, "command": command, "env": extra_environment})
    req = MissionStart(
        section="transport-profile", witness_mode=True, config="config.yaml",
        input_json="input.json", runs_dir="./runs", api_mode="capture", readiness_token="witness-token",
    )
    phases = ["source_transport_profile", "target_transport_profile"]
    fp = backend_app._readiness_fingerprint_for(req, phases, api_mode="capture")
    backend_app._write_json(backend_app.LIVE_READINESS_FILE, {
        "pass": True, "token": "witness-token", "fingerprint": fp,
        "created_at_epoch": time.time(), "expires_at_epoch": time.time() + 300,
    })
    out = backend_app.start_mission(req)
    assert out["witness_mode"] is True
    assert captured["builder"]["witness_mode"] is True
    assert out["api_mode"] == "capture"


def test_backend_rejects_witness_with_mutation_authorization(monkeypatch):
    monkeypatch.setattr(backend_app, "_preflight_for_phases", lambda req, phases: {"pass": True, "skill_vetting": {"pass": True}, "context_budget": {}})
    with pytest.raises(HTTPException) as exc:
        backend_app.start_mission(MissionStart(
            section="transport-profile", witness_mode=True, input_json="input.json",
            api_mode="write", allow_api_mutation=True, readiness_token="unused",
        ))
    assert exc.value.status_code == 422
    assert "strictly non-mutating" in str(exc.value.detail)


def test_ui_defaults_first_live_test_to_witness_mode():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    js = (root / "webui" / "app.js").read_text(encoding="utf-8")
    assert 'id="liveWitnessMode"' in html and "checked" in html
    assert "Live witness mode (no mutation controls)" in html
    assert "witness_mode" in js
    assert "Start live witness" in js
    assert "Live witness forces API mode to capture" in js
