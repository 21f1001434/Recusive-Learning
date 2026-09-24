from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.config import AppConfig, load_config
from hip_id_agent import autonomous_form_runtime as afr


PHASES = [
    "data_map",
    "source_document_type",
    "target_document_type",
    "rule",
    "source_transport_profile",
    "target_transport_profile",
    "biz_flow",
]


def test_v231_default_config_enables_autonomous_runtime_for_all_seven_form_phases():
    cfg = load_config("config.yaml")
    assert cfg.autonomous_form.enabled is True
    assert cfg.autonomous_form.apply_to_all_form_phases is True
    assert cfg.autonomous_form.apply_to_document_types is True
    assert cfg.autonomous_form.apply_to_rules is True
    assert cfg.autonomous_form.apply_to_transport_profiles is True
    assert cfg.autonomous_form.apply_to_biz_flow is True
    assert cfg.autonomous_form.max_adaptive_cycles >= 5
    for phase in PHASES:
        assert afr.autonomous_phase_enabled(cfg, phase) is True


def test_v231_individual_phase_flags_support_controlled_rollback_when_all_flag_off():
    cfg = AppConfig()
    cfg.autonomous_form.apply_to_all_form_phases = False
    cfg.autonomous_form.apply_to_rules = False
    assert afr.autonomous_phase_enabled(cfg, "rule") is False
    assert afr.autonomous_phase_enabled(cfg, "source_document_type") is True
    assert afr.autonomous_phase_enabled(cfg, "target_transport_profile") is True


def test_every_phase_module_wires_shared_autonomous_goal_runtime():
    root = Path("hip_id_agent")
    for name in ["datamap_kb.py", "doctype_kb.py", "rules_kb.py", "transport_profile_kb.py", "bizflow_kb.py"]:
        src = (root / name).read_text(encoding="utf-8")
        assert "execute_autonomous_phase_goal(" in src, name
        assert "autonomous_phase_enabled" in src, name


def test_autonomous_mission_forces_all_phase_goal_runtime():
    src = (Path("hip_id_agent") / "cli.py").read_text(encoding="utf-8")
    assert "cfg.autonomous_form.apply_to_all_form_phases = True" in src
    assert "cfg.autonomous_form.apply_to_document_types = True" in src
    assert "cfg.autonomous_form.apply_to_rules = True" in src
    assert "cfg.autonomous_form.apply_to_transport_profiles = True" in src
    assert "cfg.autonomous_form.apply_to_biz_flow = True" in src


@pytest.mark.asyncio
async def test_section_scoped_autonomous_runtime_only_plans_current_bizflow_tab(monkeypatch, tmp_path: Path):
    graph = {
        "graph_id": "biz-g1",
        "phase": "biz_flow",
        "nodes": [
            {"node_id": "n1", "field_key": "flow_name", "section": "Flow Details", "action": "fill_text", "expected_value": "FLOW-A", "required": True, "semantic_locator": {"labels": ["Flow Name"]}, "depends_on": []},
            {"node_id": "n2", "field_key": "source_document_type", "section": "Configure Source", "action": "select_single", "expected_value": "DOC-A", "required": True, "semantic_locator": {"labels": ["Document Type"]}, "depends_on": []},
        ],
        "dependency_edges": [],
    }
    controls = [{"selector": "#flow", "label": "Flow Name", "role": "textbox", "section": "Flow Details", "required": True, "value": "FLOW-A"}]
    seen = []

    async def fake_gate(page, phase):
        return {"fatal": []}

    async def fake_capture(page, phase):
        return [dict(x) for x in controls]

    def fake_diag(rows, node):
        if node.get("field_key") == "flow_name":
            return {"resolved": True, "control": dict(rows[0]), "reason": "match", "best_score": 200, "score_margin": 100}
        return {"resolved": False, "reason": "not on current tab"}

    async def fake_execute(page, g, **kwargs):
        seen.append((kwargs.get("section"), [n["field_key"] for n in g.get("nodes", [])]))
        return {
            "pass": True,
            "attempts": [{"field": "flow_name", "success": True, "exact_verified": True, "authoritative_execution": True, "executor": "playwright-mcp-fallback"}],
            "execution_stage_audit": {"fields_filled_or_verified": True, "exact_execution_verified": True, "authoritative_execution_verified": True},
        }

    monkeypatch.setattr(afr, "assert_active_surface", fake_gate)
    monkeypatch.setattr(afr, "capture_stateful_controls", fake_capture)
    monkeypatch.setattr(afr, "resolve_stateful_control_diagnostics", fake_diag)
    monkeypatch.setattr(afr, "execute_phase_state_graph", fake_execute)

    cfg = AppConfig()
    cfg.aia.enabled = False
    result = await afr.execute_autonomous_phase_goal(
        page=SimpleNamespace(), graph=graph, phase="biz_flow", input_data={}, config=cfg,
        output_dir=tmp_path, max_cycles=1, section="Flow Details", strict_live_execution=True,
    )
    assert result["pass"] is True
    assert result["section"] == "Flow Details"
    assert seen
    # Both calls are scoped to the current tab; the non-file execution graph only carries that tab's goal.
    assert all(section == "Flow Details" for section, _ in seen)
    assert seen[0][1] == ["flow_name"]


@pytest.mark.asyncio
async def test_dedicated_executor_keeps_upload_owned_by_autonomous_runtime(monkeypatch, tmp_path: Path):
    graph = {
        "graph_id": "doc-g1",
        "phase": "source_document_type",
        "nodes": [
            {"node_id": "n1", "field_key": "document_type_name", "section": "Document Type Details", "action": "fill_text", "expected_value": "DOC-A", "required": True, "semantic_locator": {"labels": ["Name"]}, "depends_on": []},
            {"node_id": "n2", "field_key": "schema_file", "section": "Validation", "action": "upload_file", "expected_value": "a.xsd", "required": True, "semantic_locator": {"labels": ["Schema"]}, "depends_on": []},
        ],
        "dependency_edges": [],
    }
    controls = [
        {"selector": "#name", "label": "Name", "role": "textbox", "section": "Document Type Details", "required": True, "value": "DOC-A"},
        {"selector": "#schema", "label": "Schema", "type": "file", "section": "Validation", "required": True, "value": "a.xsd"},
    ]
    executor_graphs = []

    async def fake_gate(page, phase): return {"fatal": []}
    async def fake_capture(page, phase): return [dict(x) for x in controls]
    def fake_diag(rows, node):
        c = rows[1] if node.get("action") == "upload_file" else rows[0]
        return {"resolved": True, "control": dict(c), "reason": "match", "best_score": 200, "score_margin": 100}
    async def fake_upload(page, control, input_data, **kwargs):
        return {"field": kwargs.get("field_key"), "success": True, "filled": True}
    async def dedicated(page, g, *, phase, max_retries=2):
        actions = [n.get("action") for n in g.get("nodes", [])]
        executor_graphs.append(actions)
        assert "upload_file" not in actions
        return {
            "pass": True,
            "attempts": [{"field": "document_type_name", "action": "fill_text", "success": True, "exact_verified": True, "transaction_proof": {"explicit_event_proof": {"pass": True}}}],
            "execution_stage_audit": {"fields_filled_or_verified": True, "exact_execution_verified": True, "authoritative_execution_verified": True},
        }

    monkeypatch.setattr(afr, "assert_active_surface", fake_gate)
    monkeypatch.setattr(afr, "capture_stateful_controls", fake_capture)
    monkeypatch.setattr(afr, "resolve_stateful_control_diagnostics", fake_diag)
    monkeypatch.setattr(afr, "attempt_upload_for_control", fake_upload)

    cfg = AppConfig(); cfg.aia.enabled = False
    result = await afr.execute_autonomous_phase_goal(
        page=SimpleNamespace(), graph=graph, phase="source_document_type", input_data={}, config=cfg,
        output_dir=tmp_path, max_cycles=1, executor=dedicated, strict_live_execution=True,
    )
    assert result["pass"] is True
    assert executor_graphs and all("upload_file" not in actions for actions in executor_graphs)
