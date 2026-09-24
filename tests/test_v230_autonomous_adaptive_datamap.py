from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.config import AppConfig, load_config
from hip_id_agent import autonomous_form_runtime as afr


def _graph():
    return {
        "graph_id": "g1",
        "phase": "data_map",
        "nodes": [
            {
                "node_id": "data_map.create_map.map_name",
                "field_key": "map_name",
                "section": "Create Map",
                "action": "fill_text",
                "expected_value": "UHAUL_MAP",
                "required": True,
                "semantic_locator": {"labels": ["Map Name"], "names": [], "placeholders": [], "roles": []},
                "depends_on": [],
            },
            {
                "node_id": "data_map.create_map.map_data_file",
                "field_key": "map_data_file",
                "section": "Create Map",
                "action": "upload_file",
                "expected_value": "uhaul.jar",
                "required": True,
                "semantic_locator": {"labels": ["Map Data"], "names": [], "placeholders": [], "roles": []},
                "depends_on": [],
            },
        ],
        "dependency_edges": [],
    }


def test_v230_default_config_enables_goal_driven_datamap_runtime():
    cfg = load_config("config.yaml")
    assert cfg.autonomous_form.enabled is True
    assert cfg.autonomous_form.apply_to_data_map is True
    assert cfg.autonomous_form.never_persist_selectors_or_coordinates is True
    assert cfg.autonomous_form.require_exact_readback is True
    assert cfg.autonomous_form.require_authoritative_executor_proof is True


def test_advisory_binding_can_adapt_to_changed_live_label_without_persisting_selector():
    graph = _graph()
    controls = [{
        "selector": "#current-generation-77",
        "label": "Transformation Name",
        "name": "transformationName",
        "role": "textbox",
        "section": "Mapping Details",
    }]
    plan = {"field_steps": [{
        "key": "map_name",
        "selector": "#current-generation-77",
        "label_hint": "Transformation Name",
        "confidence": 0.97,
        "reason": "Current portal renamed Map Name",
    }]}
    adapted, accepted = afr._apply_advisory_plan_hints(graph, controls, plan)
    node = adapted["nodes"][0]
    assert accepted and accepted[0]["field"] == "map_name"
    assert "Transformation Name" in node["semantic_locator"]["labels"]
    assert "transformationName" in node["semantic_locator"]["names"]
    # Selectors are current-generation evidence only and must never become durable locator knowledge.
    assert "selector" not in node["semantic_locator"]
    assert "#current-generation-77" not in str(node.get("adaptive_binding_evidence"))


@pytest.mark.asyncio
async def test_autonomous_runtime_executes_goal_then_file_then_authoritative_full_verification(monkeypatch, tmp_path: Path):
    graph = _graph()
    controls = [
        {"selector": "#map-name", "label": "Map Name", "role": "textbox", "required": True},
        {"selector": "#map-data", "label": "Map Data", "type": "file", "required": True},
    ]
    calls = []

    async def fake_gate(page, phase):
        return {"phase": phase, "fatal": []}

    async def fake_capture(page, phase):
        return [dict(x) for x in controls]

    def fake_diag(rows, node):
        wanted = "#map-data" if node.get("action") == "upload_file" else "#map-name"
        c = next(x for x in rows if x["selector"] == wanted)
        return {"resolved": True, "control": dict(c), "reason": "unique live semantic match", "best_score": 150, "score_margin": 80}

    async def fake_execute(page, g, **kwargs):
        actions = [n.get("action") for n in g.get("nodes", [])]
        calls.append(("execute", tuple(actions)))
        if "upload_file" in actions:
            return {
                "pass": True,
                "attempts": [
                    {"node_id": "data_map.create_map.map_name", "field": "map_name", "success": True, "exact_verified": True, "authoritative_execution": True, "executor": "playwright-mcp-fallback"},
                    {"node_id": "data_map.create_map.map_data_file", "field": "map_data_file", "success": True, "exact_verified": True, "authoritative_execution": True, "executor": "phase-specific-contract-aware-uploader"},
                ],
                "execution_stage_audit": {"exact_execution_verified": True, "authoritative_execution_verified": True},
            }
        return {
            "pass": True,
            "attempts": [{"field": "map_name", "success": True, "exact_verified": True, "authoritative_execution": True}],
            "execution_stage_audit": {"exact_execution_verified": True, "authoritative_execution_verified": True},
        }

    async def fake_upload(page, control, input_data, **kwargs):
        calls.append(("upload", kwargs.get("field_key")))
        return {"field": kwargs.get("field_key"), "success": True, "filled": True, "uploaded_file_name": "uhaul.jar"}

    monkeypatch.setattr(afr, "assert_active_surface", fake_gate)
    monkeypatch.setattr(afr, "capture_stateful_controls", fake_capture)
    monkeypatch.setattr(afr, "resolve_stateful_control_diagnostics", fake_diag)
    monkeypatch.setattr(afr, "execute_phase_state_graph", fake_execute)
    monkeypatch.setattr(afr, "attempt_upload_for_control", fake_upload)

    cfg = AppConfig()
    cfg.aia.enabled = False
    result = await afr.execute_autonomous_phase_goal(
        page=SimpleNamespace(), graph=graph, phase="data_map",
        input_data={"objects": {"data_map": {"map_name": "UHAUL_MAP", "map_data_file": "uhaul.jar"}}},
        config=cfg, output_dir=tmp_path, max_cycles=3, strict_live_execution=True,
    )
    assert result["pass"] is True
    assert result["goal_driven"] is True
    assert result["adaptive"] is True
    assert result["fixed_selectors_required"] is False
    assert result["fixed_coordinates_required"] is False
    assert ("execute", ("fill_text",)) in calls
    assert ("upload", "map_data_file") in calls
    assert ("execute", ("fill_text", "upload_file")) in calls
    assert (tmp_path / "autonomous_form_runtime.json").exists()


def test_datamap_flow_uses_autonomous_runtime_not_fixed_manual_fill_order():
    src = (Path("hip_id_agent") / "datamap_kb.py").read_text(encoding="utf-8")
    assert "execute_autonomous_phase_goal(" in src
    # V229 had a hard-coded click/fill order for every Data Map field. V230 must
    # let the state graph + live portal decide the executable order.
    assert 'fill_order = [\n                    "map_identifier"' not in src
    assert '"fixed_selectors_required": False' in (Path("hip_id_agent") / "autonomous_form_runtime.py").read_text(encoding="utf-8")


def test_uncovered_required_live_control_becomes_needs_input_not_fabricated():
    graph = _graph()
    controls = [
        {"selector": "#map-name", "label": "Map Name", "role": "textbox", "required": True, "value": ""},
        {"selector": "#new-required", "label": "New Dell Required Setting", "role": "combobox", "required": True, "value": "", "selected_values": []},
    ]
    def diag(rows, node):
        if node.get("field_key") == "map_name":
            return {"resolved": True, "control": rows[0], "reason": "match"}
        return {"resolved": False, "reason": "not present"}
    original = afr.resolve_stateful_control_diagnostics
    try:
        afr.resolve_stateful_control_diagnostics = diag
        missing = afr._uncovered_required_controls(controls, graph)
    finally:
        afr.resolve_stateful_control_diagnostics = original
    assert len(missing) == 1
    assert missing[0]["label"] == "New Dell Required Setting"
    assert "no mapped mission value" in missing[0]["reason"]
