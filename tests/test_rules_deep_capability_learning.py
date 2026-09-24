from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import load_config
from hip_id_agent.future_task_agent import infer_family
from hip_id_agent.rules_deep_discovery import (
    FAMILY,
    MUTATION_PROBE_ACTIONS,
    RuleDeepDiscoveryFlow,
    _execution_value_free,
    _query_for,
    _value_free_rule_blueprint,
)
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
from hip_id_agent.autonomous_dependency_runtime import apply_dependency_execution_contract

ROOT = Path(__file__).resolve().parents[1]


def _payload():
    return json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def test_rule_blueprint_is_value_free_and_orders_actions_before_conditions():
    graph = apply_dependency_execution_contract(compile_phase_state_graph(_payload(), "rule"), phase="rule")
    bp = _value_free_rule_blueprint(graph)
    assert bp["section_order"] == ["Rule Details", "Actions", "Conditions"]
    assert bp["values_stored"] is False
    assert all(n["expected_value_stored"] is False for n in bp["nodes"])
    assert any("Mapping Identifier" in x for x in bp["critical_gates"])
    assert any(n["row_kind"] == "condition" for n in bp["nodes"])
    mapping = next(n for n in bp["nodes"] if n["field_key"] == "mapping_identifier_name_version")
    first_condition = next(n for n in bp["nodes"] if n["field_key"] == "condition_type" and n["row_index"] == 0)
    assert mapping["node_id"] in first_condition["depends_on"]


def test_rule_query_comes_from_current_input_only():
    payload = _payload()
    q = _query_for(payload)
    assert q == payload["objects"]["rule"]["name"]


def test_rule_execution_summary_drops_values():
    summary = _execution_value_free({"pass": True, "attempts": [{"node_id": "n1", "field": "rule_name", "expected_value": "SECRET_CUSTOMER_VALUE", "actual_value": "SECRET_CUSTOMER_VALUE", "success": True, "exact_verified": True}]})
    text = json.dumps(summary)
    assert "SECRET_CUSTOMER_VALUE" not in text
    assert summary["values_stored"] is False


def test_rule_deep_uses_production_rules_transaction_helpers():
    source = (ROOT / "hip_id_agent" / "rules_deep_discovery.py").read_text(encoding="utf-8")
    assert "_apply_rule_condition_row_adds" in source
    assert "_rule_condition_rows_exact" in source
    assert "execute_phase_state_graph" in source
    assert "async Mapping Identifier" in source or "Mapping Identifier" in source


def test_rule_deep_mutation_probes_are_safe_actions_only():
    assert set(MUTATION_PROBE_ACTIONS) == {"migrate", "deploy", "delete"}
    source = (ROOT / "hip_id_agent" / "rules_deep_discovery.py").read_text(encoding="utf-8")
    assert 'route.abort("blockedbyclient")' in source
    assert "MUTATING_METHODS" in source
    assert "clear_portal_mutation_authorization" in source


def test_capability_graph_upgraded_to_v6(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    assert graph.SCHEMA_VERSION == "hip.capability-graph.v7"
    assert graph.manifest()["schema_version"] == "hip.capability-graph.v7"


def test_future_task_agent_knows_rules_family():
    assert infer_family('Search rule "R1", expand it, then Edit') == "rules"


def test_cli_exposes_rules_deep_command():
    cli = (ROOT / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    assert '@app.command("learn-rules-deep")' in cli
    assert "RuleDeepDiscoveryFlow" in cli


def test_backend_exposes_rules_deep_route(monkeypatch):
    import backend.app as backend_app
    captured = {}
    monkeypatch.setattr(backend_app, "_start_cli", lambda command, runs_dir="": captured.update(command=command, runs_dir=runs_dir) or {"command": command})
    client = TestClient(backend_app.app)
    resp = client.post("/api/rules/deep/start", json={"config": "config.yaml", "input_json": "x.json", "runs_dir": "runs", "require_mcp": True})
    assert resp.status_code == 200
    assert "learn-rules-deep" in captured["command"]
    assert "--require-mcp" in captured["command"]


def test_frontend_and_launcher_expose_rules_deep():
    frontend = (ROOT / "frontend" / "app.py").read_text(encoding="utf-8")
    runner = (ROOT / "RUN_LEARN_RULES_DEEP.ps1").read_text(encoding="utf-8")
    assert "Deep Learn Rules" in frontend
    assert "/api/rules/deep/start" in frontend
    assert "learn-rules-deep" in runner
    assert "HIP_REQUIRE_AUTOGEN_075" in runner
