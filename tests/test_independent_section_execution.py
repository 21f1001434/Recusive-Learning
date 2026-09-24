from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from typer.testing import CliRunner

from backend import app as backend_app
from backend.app import SectionRunStart
from hip_id_agent.cli import app
from hip_id_agent.dummy_fill_e2e import validate_live_input_contract
from hip_id_agent.section_scope import resolve_section, resolve_section_phases, section_catalog
from hip_id_agent.streamlit_dashboard import build_section_mission_command


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "uhaul_poasn_full_dummy_input.json"


def _payload():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_section_catalog_exposes_every_independent_hip_form_family():
    ids = {row["id"] for row in section_catalog()}
    assert {
        "data-map", "source-document-type", "target-document-type", "document-type",
        "rule", "source-transport-profile", "target-transport-profile",
        "transport-profile", "bizflow", "all",
    } <= ids


def test_transport_profile_alias_runs_only_source_and_target_transport_profiles():
    spec = resolve_section("TP")
    assert spec["id"] == "transport-profile"
    assert spec["phases"] == ["source_transport_profile", "target_transport_profile"]
    assert "data_map" not in spec["phases"]
    assert "biz_flow" not in spec["phases"]


def test_source_and_target_transport_profile_can_each_run_alone():
    assert resolve_section_phases("source tp") == ["source_transport_profile"]
    assert resolve_section_phases("target-transport-profile") == ["target_transport_profile"]


def test_other_major_sections_can_run_independently():
    assert resolve_section_phases("data map") == ["data_map"]
    assert resolve_section_phases("document type") == ["source_document_type", "target_document_type"]
    assert resolve_section_phases("rule") == ["rule"]
    assert resolve_section_phases("biz flow") == ["biz_flow"]


def test_transport_profile_only_input_contract_does_not_require_unselected_objects():
    payload = _payload()
    scoped = {
        "objects": {
            "source_transport_profile": payload["objects"]["source_transport_profile"],
            "target_transport_profile": payload["objects"]["target_transport_profile"],
        }
    }
    report = validate_live_input_contract(
        scoped,
        phases=["source_transport_profile", "target_transport_profile"],
    )
    assert report["pass"] is True
    assert report["section_scoped"] is True
    assert report["selected_phases"] == ["source_transport_profile", "target_transport_profile"]
    assert set(report["phase_coverage"]) == {"source_transport_profile", "target_transport_profile"}


def test_backend_section_start_translates_user_section_to_exact_phase_filter(monkeypatch):
    captured = {}

    def fake_start(command, *, runs_dir=""):
        captured["command"] = command
        captured["runs_dir"] = runs_dir
        return {"pid": 123, "running": True, "paused": False}

    monkeypatch.setattr(backend_app, "_start_cli", fake_start)
    out = backend_app.start_section_run(SectionRunStart(
        section="transport-profile",
        input_json="customer.json",
        runs_dir="C:/hip_runs",
        require_mcp=True,
    ))
    command = captured["command"]
    phase_idx = command.index("--phases")
    assert command[phase_idx + 1] == "source_transport_profile,target_transport_profile"
    assert "data_map" not in command[phase_idx + 1]
    assert out["selected_phases"] == ["source_transport_profile", "target_transport_profile"]
    assert out["isolated"] is True


def test_backend_rejects_unknown_section_before_start(monkeypatch):
    monkeypatch.setattr(backend_app, "_start_cli", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(HTTPException) as exc:
        backend_app.start_section_run(SectionRunStart(section="made-up-section"))
    assert exc.value.status_code == 422


def test_cli_run_section_exposes_simple_transport_profile_trigger(monkeypatch):
    captured = {}

    def fake_run(command, check=False):
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("hip_id_agent.cli.subprocess.run", fake_run)
    result = CliRunner().invoke(app, [
        "run-section", "transport-profile",
        "--input-json", str(EXAMPLE),
        "--runs-dir", "C:/hip_runs",
        "--allow-executor-fallback",
        "--no-vision-verify",
    ])
    assert result.exit_code == 0, result.output
    command = captured["command"]
    assert command[command.index("--phases") + 1] == "source_transport_profile,target_transport_profile"
    assert "--fast-form-only" in command


def test_streamlit_section_command_keeps_full_agent_features_but_not_full_mission(tmp_path):
    command = build_section_mission_command(
        project_root=ROOT,
        config="config.yaml",
        input_json=EXAMPLE,
        runs_dir=tmp_path / "runs",
        golden_screenshot_dir=ROOT / "golden_screenshots" / "UHAUL-POASN",
        upload_assets_dir=ROOT / "uploads",
        section="rule",
        api_mode="capture",
        until_complete=True,
        python_executable="python",
    )
    assert command[command.index("--phases") + 1] == "rule"
    assert "--autonomous-mission" not in command
    assert "--agentq-crawler-fusion" in command
    assert "--allow-executor-fallback" in command
    assert "--require-mcp" not in command
    assert "--section-judge" in command
    assert "--runtime-self-heal-until-complete" in command


def test_frontend_exposes_section_selector_and_section_endpoint():
    frontend = (ROOT / "frontend" / "app.py").read_text(encoding="utf-8")
    dashboard = (ROOT / "hip_id_agent" / "streamlit_dashboard.py").read_text(encoding="utf-8")
    assert "Execute One HIP Section" in frontend
    assert "Run Selected Section Only" in frontend
    assert "/api/section-run/start" in frontend
    assert "Execution scope" in dashboard
    assert "Run selected section only" in dashboard
