from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from hip_id_agent.autonomous_dependency_runtime import apply_dependency_execution_contract
from hip_id_agent.bizflow_deep_discovery import (
    FAMILY,
    MUTATION_PROBE_ACTIONS,
    PHASE,
    BizFlowDeepDiscoveryFlow,
    _execution_summary_value_free,
    _query_for,
    _value_free_bizflow_blueprint,
)
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.future_task_agent import infer_family
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph

ROOT = Path(__file__).resolve().parents[1]


def _payload():
    return json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def test_bizflow_blueprint_is_value_free_and_models_all_sections():
    graph = apply_dependency_execution_contract(compile_phase_state_graph(_payload(), PHASE), phase=PHASE)
    bp = _value_free_bizflow_blueprint(graph)
    assert bp["object_family"] == "biz_flow"
    assert bp["section_order"] == ["Flow Details", "Configure Source", "Configure Target(s)", "Configure Routing"]
    assert bp["values_stored"] is False
    assert all(n["expected_value_stored"] is False for n in bp["nodes"])
    assert any("Process Step Type" in x for x in bp["critical_gates"])
    assert any("Routing Action Type" in x for x in bp["critical_gates"])


def test_bizflow_graph_models_nested_repeatable_rows_and_is_acyclic():
    graph = apply_dependency_execution_contract(compile_phase_state_graph(_payload(), PHASE), phase=PHASE)
    assert graph["repeatable_rows"] == {"flow_identifier": 2, "process_step": 2, "routing_condition": 2, "routing_action": 1}
    assert len(graph["nodes"]) == 58
    assert not (graph.get("dependency_execution_contract") or {}).get("cycles")


def test_bizflow_dependency_order_preserves_parent_child_gates():
    graph = apply_dependency_execution_contract(compile_phase_state_graph(_payload(), PHASE), phase=PHASE)
    nodes = graph["nodes"]
    order = {nid: i for i, nid in enumerate(graph["dependency_execution_contract"]["ordered_node_ids"])}
    by_key = {}
    for n in nodes:
        by_key.setdefault((n["field_key"], n.get("row_kind"), n.get("row_index")), n)
    pairs = [
        (("source_type", "", None), ("source_application", "", None)),
        (("target_type", "", None), ("target_application", "", None)),
        (("flow_identifier_document_type", "flow_identifier", 0), ("flow_attribute_name", "flow_identifier", 0)),
        (("process_step_type", "process_step", 0), ("process_action", "process_step", 0)),
        (("process_target_file_name_config", "process_step", 1), ("process_file_extension", "process_step", 1)),
        (("route_action_type", "", None), ("route_action_target", "", None)),
        (("route_condition_type", "routing_condition", 0), ("route_condition_attribute", "routing_condition", 0)),
    ]
    for parent_key, child_key in pairs:
        p = by_key[parent_key]; c = by_key[child_key]
        assert p["node_id"] in c["depends_on"]
        assert order[p["node_id"]] < order[c["node_id"]]


def test_bizflow_query_comes_from_current_input_only():
    payload = _payload()
    assert _query_for(payload) == payload["objects"]["biz_flow"]["flow_details"]["business_flow_name"]


def test_bizflow_execution_summary_drops_values():
    summary = _execution_summary_value_free({
        "pass": True,
        "attempts": [{
            "node_id": "n1", "field": "route_action_target", "input_path": "$.objects.biz_flow.configure_routing.actions.target",
            "expected_value": "SECRET_TP", "actual_value": "SECRET_TP", "success": True, "exact_verified": True,
        }],
    })
    text = json.dumps(summary)
    assert "SECRET_TP" not in text
    assert summary["values_stored"] is False


def test_bizflow_deep_reuses_production_multitab_runtime():
    source = (ROOT / "hip_id_agent" / "bizflow_deep_discovery.py").read_text(encoding="utf-8")
    assert "capture_and_fill_bizflow_multitab_form" in source
    assert "_click_bizflow_template_link_after_add" in source
    assert "apply_dependency_execution_contract" in source
    assert "ui_api_causal_trace" in source


def test_bizflow_deep_mutation_probes_stay_blocked():
    assert set(MUTATION_PROBE_ACTIONS) == {"migrate", "deploy", "delete"}
    source = (ROOT / "hip_id_agent" / "bizflow_deep_discovery.py").read_text(encoding="utf-8")
    assert 'route.abort("blockedbyclient")' in source
    assert "clear_portal_mutation_authorization" in source
    assert "mutation_delivered\": False" in source


def test_future_task_agent_knows_bizflow_family():
    assert infer_family('Search biz flow "FLOW1", expand it, then Edit') == FAMILY


def test_cli_and_backend_expose_bizflow_deep(monkeypatch):
    cli = (ROOT / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    assert '@app.command("learn-bizflows-deep")' in cli
    assert "BizFlowDeepDiscoveryFlow" in cli
    import backend.app as backend_app
    captured = {}
    monkeypatch.setattr(backend_app, "_start_cli", lambda command, runs_dir="": captured.update(command=command, runs_dir=runs_dir) or {"command": command})
    client = TestClient(backend_app.app)
    resp = client.post("/api/bizflows/deep/start", json={"config": "config.yaml", "input_json": "x.json", "runs_dir": "runs", "require_mcp": True})
    assert resp.status_code == 200
    assert "learn-bizflows-deep" in captured["command"]
    assert "--require-mcp" in captured["command"]


def test_frontend_and_launcher_expose_bizflow_deep():
    frontend = (ROOT / "frontend" / "app.py").read_text(encoding="utf-8")
    runner = (ROOT / "RUN_LEARN_BIZFLOWS_DEEP.ps1").read_text(encoding="utf-8")
    assert "Deep Learn BizFlows" in frontend
    assert "/api/bizflows/deep/start" in frontend
    assert "learn-bizflows-deep" in runner
    assert "HIP_REQUIRE_AUTOGEN_075" in runner
