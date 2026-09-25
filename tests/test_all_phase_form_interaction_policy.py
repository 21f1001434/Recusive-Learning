from __future__ import annotations

import inspect

from hip_id_agent.form_interaction_policy import (
    FORM_INTERACTION_RULES,
    DEFAULT_FORM_INTERACTION_POLICY,
    derive_execution_profile,
    explicit_event_proof,
)
from hip_id_agent.phase_runtime_contract import PHASE_RUNTIME_CONTRACTS


def test_policy_contains_critical_and_additional_rules():
    ids = {rule["id"] for rule in FORM_INTERACTION_RULES}
    assert {
        "structural-parent-first",
        "explicit-widget-events",
        "parent-child-visibility-gate",
        "animation-stability",
        "active-surface-scope",
        "hit-test-before-action",
        "stale-node-rebind",
        "event-and-state-proof",
        "committed-field-protection",
        "validation-gate",
        "repeatable-row-effect",
        "learn-once-replay-fast",
    }.issubset(ids)
    assert all(rule["mandatory"] for rule in FORM_INTERACTION_RULES)


def test_fast_replay_requires_strong_unambiguous_live_model_or_validated_knowledge():
    model = {
        "one_to_one_pass": True,
        "structure_fingerprint": "abc",
        "ambiguous_nodes": [],
        "duplicate_bindings": {},
        "bindings": [
            {"status": "resolved", "binding": {"score_margin": 40}},
            {"status": "deferred_conditional", "binding": {"score_margin": 0}},
        ],
    }
    result = derive_execution_profile(model, {"strategy": "target-branch-first"})
    assert result["mode"] == "validated_fast_replay"
    assert result["profile"]["poll_interval_ms"] < DEFAULT_FORM_INTERACTION_POLICY["learning_mode"]["poll_interval_ms"]

    ambiguous = {**model, "ambiguous_nodes": ["x"]}
    result = derive_execution_profile(ambiguous, {})
    assert result["mode"] == "learning"

    result = derive_execution_profile(ambiguous, {"validated_replay": True})
    assert result["mode"] == "validated_fast_replay"


def test_explicit_widget_event_proof_records_click_listener_contract():
    proof = explicit_event_proof(
        {"event_types": ["click", "change"], "mutation_count": 1, "state_change_count": 1},
        "select_single",
        executor_ok=True,
    )
    assert proof["pass"] is True
    assert proof["requires_explicit_click"] is True
    assert proof["explicit_click_observed"] is True

    # Angular may replace the observed node during the click. The explicit DDS
    # executor contract remains accepted but the observer gap is preserved.
    gap = explicit_event_proof(
        {"event_types": [], "mutation_count": 0, "state_change_count": 0},
        "select_radio",
        executor_ok=True,
    )
    assert gap["pass"] is True
    assert gap["observer_gap_accepted_from_explicit_executor"] is True


def test_every_phase_contract_inherits_interaction_rules():
    for contract in PHASE_RUNTIME_CONTRACTS.values():
        policy = contract["transaction_policy"]
        assert policy["structural_parent_first"] is True
        assert policy["explicit_click_check_for_radios_and_dropdowns"] is True
        assert policy["conditional_child_visibility_gate"] is True
        assert policy["animation_bounding_box_stability"] is True
        assert policy["active_surface_hit_test"] is True
        assert policy["stale_node_semantic_rebind"] is True
        assert policy["blocking_validation_gate"] is True
        assert policy["learn_once_validated_fast_replay"] is True


def test_shared_executors_apply_policy_before_and_after_each_action():
    import hip_id_agent.stateful_form_runtime as runtime

    generic = inspect.getsource(runtime.execute_phase_state_graph)
    document_type = inspect.getsource(runtime.execute_document_type_state_graph)
    for source in (generic, document_type):
        for token in (
            "_prepare_phase_control_for_action",
            "_recommit_structural_parent_if_needed",
            "_wait_for_parent_children_visible",
            "explicit_event_proof",
            "inspect_interaction_state",
            "derive_execution_profile",
            "fast_replay_blueprint",
        ):
            assert token in source


def test_dds_driver_never_uses_raw_value_assignment_for_radio_or_dropdown_contracts():
    import hip_id_agent.dds_control_driver as driver

    radio = inspect.getsource(driver.select_radio_value)
    combo = inspect.getsource(driver.select_dds_combobox)
    multi = inspect.getsource(driver.select_dds_multiselect)
    # V243R17: radios click through _click_choice, which uses the same broker
    # (on the label when DDS clips the real input).
    assert "_click_choice" in radio
    assert "_broker_click" in inspect.getsource(driver._click_choice)
    assert "_broker_click" in combo
    assert "_click_dds_multiselect_option" in multi
    assert "el.value=value" not in radio
