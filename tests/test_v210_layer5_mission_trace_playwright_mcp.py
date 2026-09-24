from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import backend.app as backend_app
from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import load_config
from hip_id_agent.dds_control_driver import _click_owned_single_option, _open_owned_single_select, select_dds_combobox
from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow
from hip_id_agent.mission_controller import MissionController
from hip_id_agent.mission_trace import MissionTraceLedger, read_mission_trace
from hip_id_agent.models import ActionEvent


def test_config_requires_autowebglm_plus_playwright_mcp_primary():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    assert cfg.autowebglm.enabled is True
    assert cfg.autowebglm.primary_framework is True
    assert cfg.mcp.use_playwright_mcp is True
    assert cfg.mcp.playwright_mcp_primary_for_safe_actions is True
    assert cfg.mcp.playwright_mcp_verify_every_action is True
    assert cfg.mcp.playwright_mcp_snapshot_after_action is True
    assert cfg.mcp.playwright_mcp_required_when_require_mcp is True


def test_shared_browser_orders_autowebglm_then_pyautogui_then_playwright_fallback():
    click_source = inspect.getsource(BrowserSession.click_and_wait)
    fill_source = inspect.getsource(BrowserSession.fill_and_log)
    press_source = inspect.getsource(BrowserSession.press_and_log)
    assert click_source.index("_autowebglm_primary_decision") < click_source.index("_try_pyautogui_click") < click_source.index("playwright_mcp_backend.click")
    assert fill_source.index("_autowebglm_primary_decision") < fill_source.index("_try_pyautogui_fill") < fill_source.index("playwright_mcp_backend.fill")
    assert press_source.index("_autowebglm_primary_decision") < press_source.index("_try_pyautogui_press") < press_source.index("playwright_mcp_backend.press")
    assert '"primary_executor": "pyautogui-mcp"' in click_source
    assert '"primary_executor": "pyautogui-mcp"' in fill_source


def test_dds_select_uses_hybrid_browser_session_broker_for_physical_execution():
    select_source = inspect.getsource(select_dds_combobox)
    open_source = inspect.getsource(_open_owned_single_select)
    option_source = inspect.getsource(_click_owned_single_option)
    assert "_autowebglm_primary_gate" in select_source
    assert "_broker_click" in open_source
    assert "_broker_click" in option_source
    assert "_broker_fill" in select_source or "_broker_press" in select_source
    assert "brokered_owned_listbox_search" in select_source
    assert "_click_owned_single_option" in select_source


def test_mission_trace_records_seen_filled_clicked_and_execution_provenance(tmp_path: Path):
    trace = MissionTraceLedger(tmp_path, run_id="run-1", phases=["data_map"])
    trace.set_runtime_contract(autowebglm_primary=True, playwright_mcp_required=True, playwright_mcp_available=True)
    trace.mark_phase("data_map", status="running", attempt=1, activity="Opening Create Map")
    fill = ActionEvent(
        action_id="act-00001",
        type="fill",
        target="Map Identifier",
        value_redacted="UHAL_MAP",
        stage="data_map:fill",
        backend="playwright-mcp+python-playwright",
        execution_provenance={
            "planner": "autowebglm-primary",
            "planner_status": "aligned",
            "primary_executor": "playwright-mcp",
            "actual_executor": "playwright-mcp",
            "playwright_mcp_available": True,
            "playwright_mcp_attempted": True,
            "playwright_mcp_succeeded": True,
        },
    )
    click = ActionEvent(
        action_id="act-00002",
        type="click",
        target="Map Data upload",
        stage="data_map:upload",
        backend="playwright-mcp+python-playwright",
        execution_provenance={
            "planner": "autowebglm-primary",
            "primary_executor": "playwright-mcp",
            "actual_executor": "playwright-mcp",
            "playwright_mcp_available": True,
            "playwright_mcp_attempted": True,
            "playwright_mcp_succeeded": True,
        },
    )
    trace.record_action(fill, phase="data_map")
    trace.record_action(click, phase="data_map")
    trace.record_observation("data_map", summary="Create Map surface visible", source="playwright-mcp")
    data = read_mission_trace(tmp_path)
    step = data["steps"][0]
    assert step["step_id"] == "P01-DM"
    assert step["filled"][0]["target"] == "Map Identifier"
    assert step["filled"][0]["value"] == "UHAL_MAP"
    assert step["filled"][0]["execution"]["planner"] == "autowebglm-primary"
    assert step["filled"][0]["execution"]["actual_executor"] == "playwright-mcp"
    assert step["clicked"][0]["execution"]["playwright_mcp_succeeded"] is True
    assert any(row.get("summary") == "Create Map surface visible" for row in step["observed"])
    assert data["runtime_contract"]["primary_safe_action_executor"] == "PyAutoGUI MCP"


def test_mission_trace_masks_secret_fill_values(tmp_path: Path):
    trace = MissionTraceLedger(tmp_path, run_id="run-secret", phases=["target_transport_profile"])
    trace.mark_phase("target_transport_profile", status="running", attempt=1)
    ev = ActionEvent(
        action_id="act-00001",
        type="fill",
        target="Password",
        value_redacted="***MASKED***",
        stage="tp:fill",
        was_secret=True,
        execution_provenance={"planner":"autowebglm-primary","actual_executor":"playwright-mcp"},
    )
    trace.record_action(ev, phase="target_transport_profile")
    data = read_mission_trace(tmp_path)
    step = next(x for x in data["steps"] if x["phase"] == "target_transport_profile")
    assert step["filled"][0]["value"] == "***MASKED***"


def test_mission_controller_updates_trace_for_started_complete_and_blocked(tmp_path: Path):
    trace = MissionTraceLedger(tmp_path, run_id="run-2", phases=["data_map", "source_document_type"])
    mission = MissionController(tmp_path, run_id="run-2", phases=["data_map", "source_document_type"])
    mission.trace = trace
    mission.mark_phase_started("data_map", attempt=1)
    mission.mark_phase_complete("data_map", attempt=1, judge_pass=True)
    mission.mark_phase_started("source_document_type", attempt=2)
    mission.mark_phase_blocked("source_document_type", attempt=2, reason="selector ambiguous")
    data = read_mission_trace(tmp_path)
    by_phase = {x["phase"]: x for x in data["steps"]}
    assert by_phase["data_map"]["status"] == "completed"
    assert by_phase["source_document_type"]["status"] == "blocked"
    assert "selector ambiguous" in by_phase["source_document_type"]["blocker"]


def test_full_e2e_wires_trace_to_shared_browser_and_refreshes_phase_artifacts():
    source = inspect.getsource(FullDummyFillE2EFlow.run)
    assert "MissionTraceLedger" in source
    assert "mission.trace = mission_trace" in source
    assert "shared_browser.mission_trace = mission_trace" in source
    assert "mission_trace.refresh_phase_artifacts" in source
    assert "mission_trace.finalize" in source


def test_backend_mission_trace_endpoint_returns_current_trace(monkeypatch, tmp_path: Path):
    trace = MissionTraceLedger(tmp_path, run_id="run-api", phases=["data_map"])
    trace.mark_phase("data_map", status="running", attempt=1)
    monkeypatch.setattr(backend_app, "_resolve_trace_run_dir", lambda **_kwargs: tmp_path)
    response = TestClient(backend_app.app).get("/api/mission/trace")
    assert response.status_code == 200
    payload = response.json()
    assert payload["found"] is True
    assert payload["run_id"] == "run-api"
    assert payload["trace"]["steps"][0]["step_id"] == "P01-DM"


def test_web_ui_renders_step_ids_and_playwright_mcp_execution_details():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    js = (root / "webui" / "app.js").read_text(encoding="utf-8")
    assert "Mission step trace" in html
    assert "AutoWebGLM → PyAutoGUI MCP" in html
    assert "/api/mission/trace" in js
    assert "What the agent observed / saw" in js
    assert "What the agent filled" in js
    assert "What the agent clicked / executed" in js
    assert "actual_executor" in js


def test_runtime_status_exposes_playwright_mcp_execution_contract():
    response = TestClient(backend_app.app).get("/api/runtime/status")
    assert response.status_code == 200
    mcp = response.json()["playwright_mcp"]
    assert mcp["enabled"] is True
    assert mcp["primary_for_safe_actions"] is True
    assert "AutoWebGLM planner" in mcp["execution_contract"]
    assert "PyAutoGUI MCP primary physical interaction" in mcp["execution_contract"]
    assert "Playwright MCP fallback/verification" in mcp["execution_contract"]
