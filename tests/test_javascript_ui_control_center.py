from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend import app as backend_app
from backend.app import MissionStart, MissionPreflightRequest, InputUploadRequest

ROOT = Path(__file__).resolve().parents[1]


def test_primary_ui_is_javascript_bun_not_streamlit():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "1.9.2"
    assert package["scripts"]["ui"] == "bun webui/server.js"
    assert package["scripts"]["platform"] == "bun webui/platform.js"
    assert "streamlit" not in package["scripts"]["ui"].lower()
    assert "streamlit" not in package["scripts"]["platform"].lower()


def test_javascript_ui_files_are_shipped_and_cover_required_controls():
    html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
    server = (ROOT / "webui" / "server.js").read_text(encoding="utf-8")
    for phrase in ["Execution scope", "API mode", "Input JSON", "Pause", "Resume", "Stop", "Certified future task", "Governed change"]:
        assert phrase in html
    for endpoint in ["/api/mission/preflight", "/api/mission/start", "/api/input/upload"]:
        assert endpoint in js
    assert "/api/discovery/${action}" in js
    assert 'processAction("pause")' in js and 'processAction("resume")' in js and 'processAction("stop")' in js
    assert "repeatable_row_plan" in js
    assert "Bun.serve" in server
    assert "HIP_API_BASE" in server


def test_default_python_requirements_no_longer_require_streamlit():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "streamlit>=1.40.0" not in requirements
    assert 'legacy-streamlit = ["streamlit>=1.40.0"]' in pyproject


def test_json_upload_endpoint_saves_browser_selected_input(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_app, "ROOT", tmp_path)
    out = backend_app.upload_input_json(InputUploadRequest(filename="customer input.json", content='{"objects":{"rule":{}}}'))
    target = Path(out["path"])
    assert target.is_file()
    assert target.name == "customer_input.json"
    assert out["object_keys"] == ["rule"]


def test_javascript_preflight_can_scope_transport_profiles(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(backend_app, "build_mission_preflight_report", lambda **kwargs: captured.update(kwargs) or {"pass": True, "phase_rows": [], "issues": [], "input_contract": {"pass": True}, "golden_screenshots_pass": True, "upload_assets_pass": True})
    monkeypatch.setattr(backend_app, "collect_repeatable_row_plan", lambda *a, **k: [])
    monkeypatch.setattr(backend_app, "autogen_runtime_status", lambda verify_imports=True: {"pass": True, "packages": {"autogen-agentchat": "0.7.5"}})
    monkeypatch.setattr(backend_app, "ROOT", tmp_path)
    input_path = tmp_path / "input.json"; input_path.write_text('{"objects":{}}', encoding="utf-8")
    out = backend_app.mission_preflight(MissionPreflightRequest(section="transport-profile", input_json=str(input_path), golden_screenshot_dir=str(tmp_path), upload_assets_dir=str(tmp_path)))
    assert captured["phases"] == ["source_transport_profile", "target_transport_profile"]
    assert out["section"]["id"] == "transport-profile"


def test_javascript_mission_start_preserves_full_section_agent_flags(monkeypatch):
    captured = {}
    monkeypatch.setattr(backend_app, "build_section_mission_command", lambda **kwargs: captured.setdefault("builder", kwargs) or ["python", "dummy"])
    # The lambda above returns kwargs dict because setdefault is truthy; use an explicit replacement.
    def build(**kwargs):
        captured["builder"] = kwargs
        return ["python", "-m", "hip_id_agent.cli", "run-full-dummy-fill", "--phases", "source_transport_profile,target_transport_profile"]
    monkeypatch.setattr(backend_app, "build_section_mission_command", build)
    def start(command, *, runs_dir="", extra_environment=None):
        captured["command"] = command; captured["runs_dir"] = runs_dir; captured["env"] = extra_environment
        return {"pid": 321, "running": True, "paused": False}
    monkeypatch.setattr(backend_app, "_start_cli", start)
    monkeypatch.setattr(backend_app, "_preflight_for_phases", lambda req, phases: {"pass": True, "skill_vetting": {"pass": True, "skills": []}, "context_budget": {"selected_phases": phases}})
    monkeypatch.setattr(backend_app, "_verify_live_readiness_receipt", lambda req, phases, api_mode: {"pass": True})
    out = backend_app.start_mission(MissionStart(section="transport-profile", input_json="input.json", runs_dir="C:/hip_runs", api_mode="capture"))
    assert captured["builder"]["section"] == "transport-profile"
    assert captured["builder"]["api_mode"] == "capture"
    assert captured["env"]["HIP_REQUIRE_AUTOGEN_075"] == "true"
    assert out["section"]["phases"] == ["source_transport_profile", "target_transport_profile"]


def test_platform_script_launches_fastapi_and_javascript_ui():
    platform = (ROOT / "webui" / "platform.js").read_text(encoding="utf-8")
    assert '"uvicorn", "backend.app:app"' in platform
    assert 'import("./server.js")' in platform
