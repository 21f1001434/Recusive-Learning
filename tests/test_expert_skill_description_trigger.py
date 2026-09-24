from __future__ import annotations

import asyncio
from pathlib import Path

from backend import app as backend_app
from backend.app import DescriptionPlanRequest, MissionStart
from hip_id_agent.browser_use_bridge import BrowserUseStateBridge
from hip_id_agent.expert_skills import (
    build_context_budget,
    infer_execution_from_description,
    vet_expert_skills,
)
from hip_id_agent.streamlit_dashboard import build_phase_subset_mission_command

ROOT = Path(__file__).resolve().parents[1]


def test_description_is_deterministic_trigger_for_full_and_transport_profile():
    full = infer_execution_from_description("Run the full end to end HIP configuration")
    assert full["pass"] is True
    assert full["section"]["id"] == "all"
    assert len(full["phases"]) == 7

    tp = infer_execution_from_description("Fill only the Transport Profiles from my input JSON")
    assert tp["pass"] is True
    assert tp["section"]["id"] == "transport-profile"
    assert tp["phases"] == ["source_transport_profile", "target_transport_profile"]


def test_description_can_select_exact_multi_section_subset_without_adding_unmentioned_phases():
    plan = infer_execution_from_description("Do Data Map and Rule only")
    assert plan["section"]["id"] == "custom"
    assert plan["phases"] == ["data_map", "rule"]
    assert "source_transport_profile" not in plan["phases"]


def test_all_phase_expert_skills_are_real_and_vetted():
    phases = ["data_map", "source_document_type", "target_document_type", "rule", "source_transport_profile", "target_transport_profile", "biz_flow"]
    result = vet_expert_skills(phases, phase_rows=[{"phase": p, "pass": True} for p in phases])
    assert result["pass"] is True
    assert result["selected_skill_count"] == 7
    assert all(row["module_present"] is True for row in result["skills"])
    assert all("deterministic" in row["deterministic_contract"].lower() or "fill" in row["deterministic_contract"].lower() for row in result["skills"])


def test_context_budget_uses_only_selected_input_branches():
    payload = {
        "objects": {
            "data_map": {"name": "MAP"},
            "rule": {"name": "RULE"},
            "source_transport_profile": {"name": "SRC"},
            "target_transport_profile": {"name": "TGT"},
            "biz_flow": {"name": "FLOW"},
        }
    }
    budget = build_context_budget(phases=["source_transport_profile", "target_transport_profile"], input_payload=payload, max_serialized_chars=4000)
    assert budget["selected_input_keys"] == ["source_transport_profile", "target_transport_profile"]
    assert "data_map" not in budget["selected_input_keys"]
    assert budget["policy"].startswith("phase-local context")


def test_description_trigger_custom_subset_builds_exact_phase_command(tmp_path: Path):
    command = build_phase_subset_mission_command(
        project_root=ROOT,
        config="config.yaml",
        input_json="examples/uhaul_poasn_full_dummy_input.json",
        runs_dir=tmp_path,
        golden_screenshot_dir="golden_screenshots/UHAUL-POASN",
        upload_assets_dir="uploads",
        phases=["data_map", "rule"],
        python_executable="python",
    )
    idx = command.index("--phases")
    assert command[idx + 1] == "data_map,rule"
    assert "source_transport_profile" not in command[idx + 1]
    assert "--fast-form-only" in command
    assert "--section-judge" in command


def test_browser_use_recovery_context_is_compact_semantic_perception_only(monkeypatch):
    bridge = BrowserUseStateBridge(enabled=True, cdp_url="http://127.0.0.1:9237")

    async def fake_snapshot():
        return {
            "available": True,
            "status": {"attached": True},
            "url": "https://hip.example/form",
            "title": "HIP",
            "tabs": [{"title": "HIP"}],
            "state": {
                "children": [
                    {"role": "combobox", "label": "Version", "selector": "#version", "clickable": True},
                    {"role": "button", "label": "+ Add", "selector": "#add", "clickable": True},
                ]
            },
        }

    monkeypatch.setattr(bridge, "state_snapshot", fake_snapshot)
    out = asyncio.run(bridge.recovery_context(max_elements=10))
    assert out["available"] is True
    assert out["interactive_element_count"] == 2
    assert out["interactive_elements"][0]["label"] == "Version"
    assert "perception only" in out["policy"]
    assert "state" not in out  # broad tree is intentionally not returned


def test_description_plan_api_returns_skill_vetting_and_context_budget(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(backend_app, "ROOT", ROOT)
    monkeypatch.setattr(backend_app, "build_mission_preflight_report", lambda **kwargs: {
        "pass": True,
        "phase_rows": [{"phase": p, "pass": True} for p in kwargs["phases"]],
        "issues": [], "input_contract": {"pass": True}, "golden_screenshots_pass": True, "upload_assets_pass": True,
    })
    monkeypatch.setattr(backend_app, "collect_repeatable_row_plan", lambda *a, **k: [])
    monkeypatch.setattr(backend_app, "autogen_runtime_status", lambda verify_imports=True: {"pass": True})
    out = backend_app.mission_description_plan(DescriptionPlanRequest(
        description="Transport Profile only",
        input_json="examples/uhaul_poasn_full_dummy_input.json",
        golden_screenshot_dir="golden_screenshots/UHAUL-POASN",
        upload_assets_dir="uploads",
    ))
    assert out["description_triggered"] is True
    assert out["section"]["id"] == "transport-profile"
    assert out["skill_vetting"]["pass"] is True
    assert out["context_budget"]["selected_phases"] == ["source_transport_profile", "target_transport_profile"]


def test_javascript_ui_exposes_five_principles_and_description_trigger():
    html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
    assert "Description trigger" in html
    assert "Use description to choose exact HIP skills" in html
    assert "Expert skill vetting" in html
    assert "Context budget" in html
    assert "/api/mission/description-plan" in js
    assert "skill_vetting" in js and "context_budget" in js


def test_mission_start_uses_description_selected_custom_phases(monkeypatch):
    captured = {}
    monkeypatch.setattr(backend_app, "_preflight_for_phases", lambda req, phases: {"pass": True, "skill_vetting": {"pass": True}, "context_budget": {"selected_phases": phases}})
    monkeypatch.setattr(backend_app, "_verify_live_readiness_receipt", lambda req, phases, api_mode: {"pass": True})
    monkeypatch.setattr(backend_app, "build_phase_subset_mission_command", lambda **kwargs: captured.setdefault("builder", kwargs) or ["python"])
    monkeypatch.setattr(backend_app, "_start_cli", lambda command, runs_dir="", extra_environment=None: {"pid": 42, "running": True, "command": command})
    out = backend_app.start_mission(MissionStart(
        section="all", description="Data Map and Rule only", use_description_trigger=True,
        input_json="examples/uhaul_poasn_full_dummy_input.json", runs_dir="./runs",
    ))
    assert captured["builder"]["phases"] == ["data_map", "rule"]
    assert out["description_triggered"] is True
    assert out["section"]["id"] == "custom"
