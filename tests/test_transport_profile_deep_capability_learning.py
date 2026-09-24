from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from hip_id_agent.autonomous_dependency_runtime import apply_dependency_execution_contract
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.future_task_agent import infer_family
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
from hip_id_agent.transport_profile_deep_discovery import (
    FAMILY,
    MUTATION_PROBE_ACTIONS,
    PHASES,
    TransportProfileDeepDiscoveryFlow,
    _action_api_causal_trace,
    _execution_summary_value_free,
    _query_for,
    _value_free_transport_profile_blueprint,
)

ROOT = Path(__file__).resolve().parents[1]


def _payload():
    return json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def test_transport_profile_blueprint_is_value_free_and_has_critical_gates():
    graph = apply_dependency_execution_contract(compile_phase_state_graph(_payload(), "source_transport_profile"), phase="source_transport_profile")
    bp = _value_free_transport_profile_blueprint(graph)
    assert bp["object_family"] == "transport_profile"
    assert bp["section_order"] == ["Create Transport Profile"]
    assert bp["values_stored"] is False
    assert all(n["expected_value_stored"] is False for n in bp["nodes"])
    assert any("Existing Account" in x for x in bp["critical_gates"])
    assert any("Use Existing Folder" in x for x in bp["critical_gates"])


def test_transport_profile_dependency_chain_is_parent_child_correct():
    graph = apply_dependency_execution_contract(compile_phase_state_graph(_payload(), "source_transport_profile"), phase="source_transport_profile")
    nodes = {n["field_key"]: n for n in graph["nodes"]}
    assert nodes["system_type"]["node_id"] in nodes["partner_name"]["depends_on"]
    assert nodes["interface_type"]["node_id"] in nodes["interface_environment"]["depends_on"]
    assert nodes["interface_type"]["node_id"] in nodes["existing_account"]["depends_on"]
    assert nodes["existing_account"]["node_id"] in nodes["existing_account_name"]["depends_on"]
    assert nodes["use_existing_folder"]["node_id"] in nodes["subscription_folder"]["depends_on"]
    order = {nid: i for i, nid in enumerate(graph["dependency_execution_contract"]["ordered_node_ids"])}
    for child in ("partner_name", "interface_environment", "existing_account", "existing_account_name", "subscription_folder"):
        for parent in nodes[child]["depends_on"]:
            assert order[parent] < order[nodes[child]["node_id"]]


def test_source_and_target_transport_profiles_stay_separate():
    payload = _payload()
    assert set(PHASES) == {"source_transport_profile", "target_transport_profile"}
    src = payload["objects"]["source_transport_profile"]
    tgt = payload["objects"]["target_transport_profile"]
    assert _query_for(src) == src["profile_name"]
    assert _query_for(tgt) == tgt["profile_name"]
    assert _query_for(src) != _query_for(tgt)


def test_transport_profile_execution_summary_drops_values():
    summary = _execution_summary_value_free({
        "pass": True,
        "attempts": [{
            "node_id": "n1", "field": "existing_account_name", "input_path": "$.objects.source_transport_profile.existing_account_name",
            "expected_value": "SECRET_ACCOUNT", "actual_value": "SECRET_ACCOUNT", "success": True, "exact_verified": True,
        }],
    })
    text = json.dumps(summary)
    assert "SECRET_ACCOUNT" not in text
    assert summary["values_stored"] is False


def test_transport_profile_deep_reuses_production_wizard_and_state_runtime():
    source = (ROOT / "hip_id_agent" / "transport_profile_deep_discovery.py").read_text(encoding="utf-8")
    assert "_advance_transport_profile_add_wizard" in source
    assert "_click_add_transport_profile_with_overlay_recovery" in source
    assert "execute_phase_state_graph" in source
    assert "capture_stateful_controls" in source


def test_transport_profile_deep_mutation_probes_stay_blocked():
    assert set(MUTATION_PROBE_ACTIONS) == {"migrate", "deploy", "delete"}
    source = (ROOT / "hip_id_agent" / "transport_profile_deep_discovery.py").read_text(encoding="utf-8")
    assert 'route.abort("blockedbyclient")' in source
    assert "MUTATING_METHODS" in source
    assert "clear_portal_mutation_authorization" in source



def test_transport_profile_ui_api_causal_trace_links_exact_request_ids():
    actions = [{"action_id": "a1", "type": "click", "target": "Interface Type", "network_events_triggered": ["r1"], "success": True}]
    txs = [{"request_id": "r1", "method": "GET", "url": "https://hip.example/api/options", "status": 200, "stage": "source_transport_profile_deep_form_fill", "request_payload": None, "response_payload": {"items": []}}]
    trace = _action_api_causal_trace(actions, txs)
    assert trace["linked_transaction_count"] == 1
    assert trace["actions"][0]["triggered_transactions"][0]["request_id"] == "r1"
    assert trace["values_stored"] is False

def test_capability_graph_upgraded_to_v6(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    assert graph.SCHEMA_VERSION == "hip.capability-graph.v7"
    assert graph.manifest()["schema_version"] == "hip.capability-graph.v7"


def test_future_task_agent_knows_transport_profiles_family():
    assert infer_family('Search transport profile "TP1", expand it, then Edit') == "transport_profiles"


def test_cli_exposes_transport_profile_deep_command():
    cli = (ROOT / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    assert '@app.command("learn-transport-profiles-deep")' in cli
    assert "TransportProfileDeepDiscoveryFlow" in cli


def test_backend_exposes_transport_profile_deep_route(monkeypatch):
    import backend.app as backend_app
    captured = {}
    monkeypatch.setattr(backend_app, "_start_cli", lambda command, runs_dir="": captured.update(command=command, runs_dir=runs_dir) or {"command": command})
    client = TestClient(backend_app.app)
    resp = client.post("/api/transport-profiles/deep/start", json={"config": "config.yaml", "input_json": "x.json", "runs_dir": "runs", "require_mcp": True})
    assert resp.status_code == 200
    assert "learn-transport-profiles-deep" in captured["command"]
    assert "--require-mcp" in captured["command"]


def test_frontend_and_launcher_expose_transport_profile_deep():
    frontend = (ROOT / "frontend" / "app.py").read_text(encoding="utf-8")
    runner = (ROOT / "RUN_LEARN_TRANSPORT_PROFILES_DEEP.ps1").read_text(encoding="utf-8")
    assert "Deep Learn Transport Profiles" in frontend
    assert "/api/transport-profiles/deep/start" in frontend
    assert "learn-transport-profiles-deep" in runner
    assert "HIP_REQUIRE_AUTOGEN_075" in runner
