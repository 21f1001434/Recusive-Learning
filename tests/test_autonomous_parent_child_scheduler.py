from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.autonomous_dependency_runtime import (
    apply_dependency_execution_contract,
    compile_dependency_execution_contract,
    scheduler_snapshot,
)
from hip_id_agent.deterministic_evidence import build_validated_phase_trajectory
from hip_id_agent.flow_pattern_memory import FlowPatternMemory
from hip_id_agent.mission_controller import MissionController
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, compile_rule_state_graph


def _input() -> dict:
    return json.loads(Path("examples/uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def _node(graph: dict, field: str, row_index: int | None = None) -> dict:
    return next(
        node
        for node in graph["nodes"]
        if node.get("field_key") == field and (row_index is None or node.get("row_index") == row_index)
    )


def test_rule_parent_child_contract_executes_actions_before_conditions():
    graph = apply_dependency_execution_contract(compile_rule_state_graph(_input(), "rule"), phase="rule")
    fields = [node["field_key"] for node in graph["nodes"]]
    assert fields.index("action_name") < fields.index("condition_type")
    assert fields.index("action_type") < fields.index("condition_type")
    assert fields.index("mapping_identifier_name_version") < fields.index("condition_type")

    condition = _node(graph, "condition_type", 0)
    dependency_map = graph["dependency_execution_contract"]["dependency_map"]
    parent_fields = {
        next(node["field_key"] for node in graph["nodes"] if node["node_id"] == parent)
        for parent in dependency_map[condition["node_id"]]
    }
    assert {
        "rule_name",
        "document_type_name_version",
        "action_name",
        "action_type",
        "mapping_identifier_name_version",
    }.issubset(parent_fields)


def test_repeatable_rows_are_completed_sequentially():
    graph = apply_dependency_execution_contract(
        compile_phase_state_graph(_input(), "source_document_type"),
        phase="source_document_type",
    )
    row0 = [n for n in graph["nodes"] if n.get("row_kind") == "attribute" and n.get("row_index") == 0]
    row1 = [n for n in graph["nodes"] if n.get("row_kind") == "attribute" and n.get("row_index") == 1]
    dependencies = graph["dependency_execution_contract"]["dependency_map"]
    required_row0 = {n["node_id"] for n in row0 if n.get("required", True)}
    assert required_row0
    assert all(required_row0.issubset(set(dependencies[n["node_id"]])) for n in row1)


def test_validated_memory_order_cannot_break_parent_dependency():
    graph = {
        "phase": "demo",
        "object_family": "generic_hip_form",
        "validated_replay": True,
        "nodes": [
            {
                "node_id": "child",
                "section": "Details",
                "field_key": "child",
                "action": "fill_text",
                "expected_value": "child-value",
                "required": True,
                "depends_on": ["parent"],
                "validated_memory_order": 1,
            },
            {
                "node_id": "parent",
                "section": "Details",
                "field_key": "parent",
                "action": "select_single",
                "expected_value": "parent-value",
                "required": True,
                "depends_on": [],
                "validated_memory_order": 2,
            },
        ],
        "dependency_edges": [],
    }
    applied = apply_dependency_execution_contract(graph, phase="demo")
    assert [n["node_id"] for n in applied["nodes"]] == ["parent", "child"]
    assert applied["dependency_execution_contract"]["mode"] == "exploitation"


def test_dependency_cycle_is_fail_closed():
    graph = {
        "phase": "demo",
        "object_family": "generic_hip_form",
        "nodes": [
            {"node_id": "a", "field_key": "a", "section": "A", "action": "fill_text", "depends_on": ["b"]},
            {"node_id": "b", "field_key": "b", "section": "A", "action": "fill_text", "depends_on": ["a"]},
        ],
        "dependency_edges": [],
    }
    contract = compile_dependency_execution_contract(graph, phase="demo")
    assert contract["pass"] is False
    assert set(contract["cycle_node_ids"]) == {"a", "b"}


def test_scheduler_releases_child_only_after_parent_commit():
    contract = compile_dependency_execution_contract(
        {
            "phase": "demo",
            "object_family": "generic_hip_form",
            "nodes": [
                {"node_id": "parent", "field_key": "parent", "section": "A", "action": "select_single", "depends_on": []},
                {"node_id": "child", "field_key": "child", "section": "A", "action": "fill_text", "depends_on": ["parent"]},
            ],
            "dependency_edges": [],
        },
        phase="demo",
    )
    first = scheduler_snapshot(contract, {})
    assert first["ready_node_ids"] == ["parent"]
    assert first["waiting_nodes"][0]["node_id"] == "child"
    second = scheduler_snapshot(contract, {"parent": True})
    assert second["ready_node_ids"] == ["child"]


def test_flow_pattern_memory_stores_contract_without_customer_values(tmp_path):
    graph = apply_dependency_execution_contract(
        {
            "phase": "demo",
            "object_family": "generic_hip_form",
            "nodes": [
                {
                    "node_id": "parent",
                    "field_key": "parent",
                    "section": "Details",
                    "action": "select_single",
                    "expected_value": "CUSTOMER-SECRET-PARENT",
                    "required": True,
                    "depends_on": [],
                },
                {
                    "node_id": "child",
                    "field_key": "child",
                    "section": "Details",
                    "action": "fill_text",
                    "expected_value": "CUSTOMER-SECRET-CHILD",
                    "required": True,
                    "depends_on": ["parent"],
                },
            ],
            "dependency_edges": [],
        },
        phase="demo",
    )
    execution = {
        "pass": True,
        "status": "pass",
        "attempts": [
            {
                "node_id": node["node_id"],
                "field": node["field_key"],
                "section": node["section"],
                "row_kind": "",
                "row_index": None,
                "success": True,
                "order": index,
                "binding_diagnostics": {"selected_identity": f"identity-{node['field_key']}"},
                "transaction_proof": {"protected_state_changes": []},
            }
            for index, node in enumerate(graph["nodes"], 1)
        ],
        "final_form_state_model": {"one_to_one_pass": True},
        "dependency_execution_contract": graph["dependency_execution_contract"],
    }
    memory = FlowPatternMemory(tmp_path / "memory", minimum_similarity=0.5)
    result = memory.promote(graph=graph, execution=execution, phase="demo", run_id="run-1", judge_pass=True)
    assert result["trust"] == "validated"
    raw = memory.patterns_path.read_text(encoding="utf-8")
    assert "CUSTOMER-SECRET-PARENT" not in raw
    assert "CUSTOMER-SECRET-CHILD" not in raw
    stored = json.loads(raw)
    pattern = next(iter(stored["patterns"].values()))
    assert pattern["replay"]["dependency_contract"]["values_stored"] is False
    assert pattern["replay"]["dependency_contract"]["ordered_node_ids"] == ["parent", "child"]


def test_mission_phase_dependencies_prevent_rule_before_upstream_entities(tmp_path):
    mission = MissionController(
        tmp_path,
        run_id="run-parent-child",
        phases=["data_map", "source_document_type", "rule", "biz_flow"],
    )
    assert mission.phase_readiness("rule")["ready"] is False
    mission.mark_phase_complete("data_map", attempt=1, judge_pass=True)
    assert mission.phase_readiness("rule")["ready"] is False
    mission.mark_phase_complete("source_document_type", attempt=1, judge_pass=True)
    assert mission.phase_readiness("rule")["ready"] is True
    assert mission.phase_readiness("biz_flow")["ready"] is False


def test_every_full_mission_phase_has_acyclic_dependency_contract():
    payload = _input()
    phases = [
        "data_map",
        "source_document_type",
        "target_document_type",
        "rule",
        "source_transport_profile",
        "target_transport_profile",
        "biz_flow",
    ]
    for phase in phases:
        graph = apply_dependency_execution_contract(compile_phase_state_graph(payload, phase), phase=phase)
        contract = graph["dependency_execution_contract"]
        assert contract["pass"] is True, (phase, contract["cycle_node_ids"])
        assert len(contract["ordered_node_ids"]) == len(graph["nodes"])


def test_validated_trajectory_contains_value_free_parent_child_contract(tmp_path):
    phase_dir = tmp_path / "rule"
    phase_dir.mkdir()
    execution = {
        "schema_version": "hip.stateful-form-execution.v2",
        "pass": True,
        "status": "pass",
        "attempts": [
            {
                "node_id": "parent",
                "field": "action_type",
                "section": "Actions",
                "success": True,
                "exact_verified": True,
                "dependencies": [],
                "binding_diagnostics": {"selected_identity": "actions|action_type"},
                "transaction_proof": {"protected_state_changes": [], "conditional_child_visibility": {"pass": True}},
            },
            {
                "node_id": "child",
                "field": "mapping_identifier_name_version",
                "section": "Actions",
                "success": True,
                "exact_verified": True,
                "dependencies": ["parent"],
                "binding_diagnostics": {"selected_identity": "actions|mapping"},
                "transaction_proof": {"protected_state_changes": []},
            },
        ],
        "final_form_state_model": {"one_to_one_pass": True},
        "dependency_execution_contract": {
            "schema_version": "hip.autonomous-parent-child-contract.v1",
            "contract_fingerprint": "abc123",
            "mode": "exploitation",
            "ordered_node_ids": ["parent", "child"],
            "dependency_map": {"parent": [], "child": ["parent"]},
            "dependency_levels": {"parent": 0, "child": 1},
            "wait_profiles": {"child": {"mount_timeout_ms": 7000}},
            "section_sequence": ["actions"],
            "scheduler_policy": {"customer_values_persisted": False},
        },
    }
    (phase_dir / "rule_state_graph_execution.json").write_text(json.dumps(execution), encoding="utf-8")
    result = build_validated_phase_trajectory(
        phase="rule",
        phase_dir=phase_dir,
        attempt=1,
        verification={"status": "pass"},
        judge_result={"pass": True},
    )
    assert result["parent_child_contract"]["contract_fingerprint"] == "abc123"
    assert result["ordered_steps"][1]["parent_node_ids"] == ["parent"]
    raw = Path(result["path"]).read_text(encoding="utf-8")
    assert '"values_stored": false' in raw.lower()


def test_runtime_self_heal_evidence_identifies_earliest_unresolved_parent(tmp_path):
    from types import SimpleNamespace
    from hip_id_agent.runtime_self_heal import RuntimeSelfHealController

    phase_dir = tmp_path / "rule"
    phase_dir.mkdir()
    (phase_dir / "parent_child_execution_contract.json").write_text(
        json.dumps(
            {
                "contract_fingerprint": "contract-1",
                "mode": "exploration",
                "pass": True,
                "ordered_node_ids": ["parent", "child"],
                "dependency_map": {"parent": [], "child": ["parent"]},
            }
        ),
        encoding="utf-8",
    )
    (phase_dir / "rule_state_graph_execution.json").write_text(
        json.dumps(
            {
                "attempts": [
                    {"node_id": "parent", "field": "action_type", "success": False, "reason": "not committed"}
                ],
                "node_status": {"parent": False, "child": False},
            }
        ),
        encoding="utf-8",
    )
    config = SimpleNamespace(runtime_self_heal=SimpleNamespace(), aia=SimpleNamespace(enabled=False))
    controller = RuntimeSelfHealController(config=config, root_dir=tmp_path, browser=object())
    evidence = controller._dependency_scheduler_forensics("rule")
    assert evidence["waiting_nodes"][0]["node_id"] == "parent"
    child = next(row for row in evidence["waiting_nodes"] if row["node_id"] == "child")
    assert child["unmet_parent_node_ids"] == ["parent"]
    assert evidence["failed_attempts"][0]["field"] == "action_type"


def test_dependency_contract_cycle_is_not_retried_forever():
    from hip_id_agent.runtime_self_heal import RuntimeSelfHealController

    assert RuntimeSelfHealController.classify_failure(
        "HIP_PARENT_CHILD_DEPENDENCY_CYCLE: a, b",
        failure_kind="execution_exception",
    ) == "dependency_contract_invalid"
    assert RuntimeSelfHealController.CLASS_ACTIONS["dependency_contract_invalid"] == ("stop_fail_closed",)
