from __future__ import annotations

import inspect

from hip_id_agent.phase_runtime_contract import PHASE_RUNTIME_CONTRACTS, validate_phase_runtime_contracts
from hip_id_agent.stateful_form_runtime import (
    _protected_state_changes,
    build_phase_form_state_model,
    resolve_stateful_control_diagnostics,
)


def _node(phase: str, field_key: str, *, section: str, row_kind=None, row_index=None, action="fill_text"):
    return {
        "node_id": f"{phase}.{field_key}.{row_index}",
        "phase": phase,
        "section": section,
        "field_key": field_key,
        "action": action,
        "expected_value": "Receiver",
        "input_path": f"objects.{phase}.{field_key}",
        "semantic_locator": {
            "labels": [field_key.replace("_", " ").title()],
            "names": [field_key],
            "placeholders": [field_key.replace("_", " ").title()],
            "roles": ["combobox"] if action.startswith("select") else [],
        },
        "row_kind": row_kind,
        "row_index": row_index,
        "required": True,
        "depends_on": [],
    }


def test_all_phase_contracts_require_transaction_and_evidence_lock():
    result = validate_phase_runtime_contracts(PHASE_RUNTIME_CONTRACTS)
    assert result["pass"] is True
    assert set(result["covered_phases"]) == set(PHASE_RUNTIME_CONTRACTS)
    for contract in PHASE_RUNTIME_CONTRACTS.values():
        policy = contract["transaction_policy"]
        assert policy["unique_control_binding_required"] is True
        assert policy["protect_previously_committed_fields"] is True
        assert policy["exact_completion_freezes_phase"] is True
        assert policy["judge_cannot_reopen_completed_phase"] is True
        assert contract["evidence_lock"] is True


def test_rule_repeated_value_binding_uses_row_identity_not_random_highest_score():
    node = _node("rule", "condition_value", section="Conditions", row_kind="condition", row_index=1)
    controls = [
        {
            "index": 1, "selector": "input#one", "semantic_key": "condition_value",
            "section": "Conditions", "row_kind": "condition", "row_index": 0,
            "label": "Condition Value", "name": "condition_value", "framework_key": "condition_value",
            "type": "text", "interactable": True,
        },
        {
            "index": 2, "selector": "input#two", "semantic_key": "condition_value",
            "section": "Conditions", "row_kind": "condition", "row_index": 1,
            "label": "Condition Value", "name": "condition_value", "framework_key": "condition_value",
            "type": "text", "interactable": True,
        },
    ]
    result = resolve_stateful_control_diagnostics(controls, node)
    assert result["resolved"] is True
    assert result["control"]["row_index"] == 1
    assert result["score_margin"] >= 14


def test_bizflow_ambiguous_unscoped_control_is_rejected():
    node = _node("biz_flow", "document_type_name", section="Configure Source", action="select_single")
    controls = [
        {
            "index": 1, "selector": "input#source", "semantic_key": "document_type_name",
            "section": "Configure Source", "label": "Document Type Name", "name": "document_type_name",
            "framework_key": "document_type_name", "role": "combobox", "component_tag": "dds-dropdown",
            "interactable": True,
        },
        {
            "index": 2, "selector": "input#target", "semantic_key": "document_type_name",
            "section": "Configure Source", "label": "Document Type Name", "name": "document_type_name",
            "framework_key": "document_type_name", "role": "combobox", "component_tag": "dds-dropdown",
            "interactable": True,
        },
    ]
    result = resolve_stateful_control_diagnostics(controls, node)
    assert result["resolved"] is False
    assert result["reason"] == "ambiguous candidate margin"


def test_transport_profile_form_model_rejects_duplicate_physical_binding():
    graph = {
        "phase": "source_transport_profile",
        "graph_id": "tp-g",
        "nodes": [
            _node("source_transport_profile", "profile_name", section="Create Transport Profile"),
            {**_node("source_transport_profile", "profile_name", section="Create Transport Profile"), "node_id": "duplicate"},
        ],
    }
    controls = [{
        "index": 1, "selector": "input#profile", "semantic_key": "profile_name",
        "section": "Create Transport Profile", "label": "Profile Name", "name": "profile_name",
        "framework_key": "profile_name", "type": "text", "interactable": True,
    }]
    model = build_phase_form_state_model(graph, controls, phase="source_transport_profile")
    assert model["one_to_one_pass"] is False
    assert model["duplicate_bindings"]


def test_shared_executor_uses_transactions_and_orchestrator_locks_all_completed_phases():
    import hip_id_agent.stateful_form_runtime as runtime
    import hip_id_agent.dummy_fill_e2e as e2e

    executor = inspect.getsource(runtime.execute_phase_state_graph)
    for token in (
        "resolve_stateful_control_diagnostics",
        "_wait_for_stateful_transaction_stable",
        "_snapshot_stateful_node_states",
        "HIP_PHASE_UNINTENDED_MUTATION",
        "build_phase_form_state_model",
        "completed_node_ids",
    ):
        assert token in executor

    orchestration = inspect.getsource(e2e.FullDummyFillE2EFlow.run)
    assert 'phase_exact_state_lock.json' in orchestration
    assert 'if diagnosis:' in orchestration
    assert 'phase_replay_allowed' in orchestration
    assert 'completed_phase_no_replay' in orchestration


def test_committed_field_protection_is_phase_agnostic():
    before = {"map_name": {"identity": "map_name", "value": "M1", "selected_values": [], "checked": False}}
    after = {"map_name": {**before["map_name"], "value": "random"}}
    changes = _protected_state_changes(before, after)
    assert changes and changes[0]["change"] == "committed_state_changed"
