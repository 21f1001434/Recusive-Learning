from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as backend_app
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from hip_id_agent.doctype_deep_discovery import (
    FAMILY,
    PHASES,
    DocumentTypeDeepDiscoveryFlow,
    _execution_summary_value_free,
    _value_free_dependency_blueprint,
)
from hip_id_agent.stateful_form_runtime import compile_document_type_state_graph


def _payload():
    return json.loads(Path("examples/uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def test_doctype_deep_blueprint_covers_both_phases_and_parent_child_rows_without_values():
    payload = _payload()
    for phase in PHASES:
        graph = compile_document_type_state_graph(payload, phase)
        bp = _value_free_dependency_blueprint(graph)
        assert bp["values_stored"] is False
        assert bp["section_order"] == ["Document Type Details", "Document Identifier", "Attributes To Configure", "Validation"]
        assert bp["repeatable_rows"]["attribute"] == 5
        keys = [n["field_key"] for n in bp["nodes"]]
        assert "document_identifier_operation" in keys
        assert "document_identifier_derived_from" in keys
        assert "document_identifier_value" in keys
        assert keys.count("attribute_name") == 5
        assert keys.count("attribute_derived_from") == 5
        assert keys.count("attribute_usage") == 5
        # Blank conditional expressions are intentionally absent from executable nodes.
        assert keys.count("attribute_expression") == 4
        text = json.dumps(bp)
        assert payload["objects"][phase]["name"] not in text
        assert "DellAutoASN" not in text
        assert "SHIPMENT_NOTICE" not in text


def test_doctype_dependency_blueprint_records_real_parent_child_edges_value_free():
    graph = compile_document_type_state_graph(_payload(), "source_document_type")
    bp = _value_free_dependency_blueprint(graph)
    relations = {e["relation"] for e in bp["dependency_edges"]}
    assert "parent_value_reveals_child" in relations
    assert "parent_value_enables_child" in relations
    assert all(e["parent_value_stored"] is False for e in bp["dependency_edges"])
    assert any("derived_from" in str(e.get("parent_value_source") or "") for e in bp["dependency_edges"])


def test_execution_summary_never_persists_expected_or_actual_values():
    execution = {
        "pass": True,
        "status": "pass",
        "graph_id": "g1",
        "attempts": [{
            "node_id": "n1", "field": "document_type_name", "input_path": "objects.source_document_type.name",
            "section": "Document Type Details", "success": True, "exact_verified": True,
            "selector": "#name", "expected_value": "CUSTOMER_SECRET_NAME", "actual_value": "CUSTOMER_SECRET_NAME",
        }],
        "completed_node_ids": ["n1"],
    }
    clean = _execution_summary_value_free(execution)
    text = json.dumps(clean)
    assert "CUSTOMER_SECRET_NAME" not in text
    assert clean["values_stored"] is False
    assert clean["attempts"][0]["exact_verified"] is True


def test_capability_graph_v4_replay_preserves_input_paths_and_dependency_metadata_only(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    cap = graph.observe_capability(page_family=FAMILY, kind="form_field", label="Derived From", selector="#derived")
    profile = graph.observe_replay_profile(
        page_family=FAMILY,
        name="create_source_document_type",
        verified=True,
        steps=[{
            "type": "form_field", "action": "select_single", "capability_id": cap["capability_id"],
            "value_source": "objects.source_document_type.attributes_to_configure[0].derived_from",
            "input_path": "objects.source_document_type.attributes_to_configure[0].derived_from",
            "section": "Attributes To Configure", "row_kind": "attribute", "row_index": 0,
            "depends_on": ["parent-node"], "verification": "exact_committed_control_value",
        }],
    )
    graph.save()
    assert graph.SCHEMA_VERSION == "hip.capability-graph.v7"
    step = profile["steps"][0]
    assert step["input_path"].endswith("derived_from")
    assert step["row_kind"] == "attribute"
    assert step["depends_on"] == ["parent-node"]
    assert step["verification"] == "exact_committed_control_value"
    assert "CUSTOMER" not in graph.path.read_text(encoding="utf-8")


def test_promote_form_topology_builds_verified_value_free_create_replay(tmp_path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    flow = DocumentTypeDeepDiscoveryFlow(cfg)
    graph = compile_document_type_state_graph(_payload(), "source_document_type")
    attempts = []
    for node in graph["nodes"]:
        attempts.append({
            "node_id": node["node_id"], "input_path": node["input_path"], "selector": f"#s{len(attempts)}",
            "success": True, "exact_verified": True,
        })
    execution = {"pass": True, "attempts": attempts}
    create = flow.graph.observe_capability(page_family=FAMILY, kind="create_entry", label="Add", selector="#add", risk="draft")
    promoted = flow._promote_form_topology(
        phase="source_document_type", graph=graph, execution=execution,
        create_capability_id=create["capability_id"], run_id="RUN-1",
    )
    assert promoted["verified"] is True
    profile = promoted["replay_profile"]
    assert profile["name"] == "create_source_document_type"
    assert any(s["row_kind"] == "attribute" for s in profile["steps"])
    text = json.dumps(flow.graph.data)
    assert _payload()["objects"]["source_document_type"]["name"] not in text


def test_backend_exposes_document_type_deep_route(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(backend_app, "_start_cli", lambda command, runs_dir="": captured.update({"command": command}) or {"running": True, "command": command})
    client = TestClient(backend_app.app)
    resp = client.post("/api/document-types/deep/start", json={
        "config": "config.yaml", "input_json": "input.json", "runs_dir": str(tmp_path), "require_mcp": True,
    })
    assert resp.status_code == 200
    assert "learn-doctypes-deep" in captured["command"]
    assert "--require-mcp" in captured["command"]


def test_document_type_deep_frontend_cli_and_network_safety_are_wired():
    frontend = Path("frontend/app.py").read_text(encoding="utf-8")
    cli = Path("hip_id_agent/cli.py").read_text(encoding="utf-8")
    source = Path("hip_id_agent/doctype_deep_discovery.py").read_text(encoding="utf-8")
    assert "Deep Learn Document Types" in frontend
    assert "/api/document-types/deep/start" in frontend
    assert '@app.command("learn-doctypes-deep")' in cli
    assert "execute_document_type_state_graph" in source
    assert 'await page.route("**/*", handler)' in source
    assert 'await route.abort("blockedbyclient")' in source
    assert "save_create_submit_executed\": False" in source


def test_document_type_deep_uses_both_source_and_target_input_objects():
    source = Path("hip_id_agent/doctype_deep_discovery.py").read_text(encoding="utf-8")
    assert 'PHASES = ("source_document_type", "target_document_type")' in source
    assert "for phase in PHASES" in source
    assert "create_form_parent_child_learning" in source


def test_doctype_deep_actively_inspects_filter_options_without_selecting():
    source = Path("hip_id_agent/doctype_deep_discovery.py").read_text(encoding="utf-8")
    assert "option_labels" in source
    assert "selection_changed\": False" in source
    assert "inspect Document Type filter" in source
    assert "page.keyboard.press(\"Escape\")" in source


def test_doctype_deep_validates_pagination_next_and_restores_previous():
    source = Path("hip_id_agent/doctype_deep_discovery.py").read_text(encoding="utf-8")
    assert "pagination_next_validation" in source
    assert "pagination_state_delta" in source
    assert "pagination_restore" in source
    assert 'stage="pagination_previous"' in source
