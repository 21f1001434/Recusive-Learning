from __future__ import annotations

import inspect

from hip_id_agent.form_interaction_policy import (
    DEFAULT_FORM_INTERACTION_POLICY,
    FORM_INTERACTION_RULES,
    multiselect_exact_set_proof,
    normalize_selection_values,
)
from hip_id_agent.phase_runtime_contract import PHASE_RUNTIME_CONTRACTS


def test_policy_contains_advanced_selection_and_widget_rules():
    ids = {rule["id"] for rule in FORM_INTERACTION_RULES}
    required = {
        "multi-select-exact-set",
        "multi-select-additive-preservation",
        "multi-select-safe-removal",
        "multi-select-select-all-guard",
        "typeahead-option-commit",
        "virtualized-option-exact-match",
        "radio-group-exclusivity",
        "checkbox-exact-state",
        "dependent-selection-reset-awareness",
        "option-loading-complete",
        "focus-containment",
        "file-upload-completion",
        "tab-accordion-activation",
        "repeatable-row-semantic-identity",
        "read-only-generated-value-protection",
    }
    assert required.issubset(ids)
    assert DEFAULT_FORM_INTERACTION_POLICY["schema_version"] == "hip.form-interaction-policy.v2"


def test_multiselect_policy_forbids_bulk_and_blind_selection():
    contract = DEFAULT_FORM_INTERACTION_POLICY["widget_contracts"]["multi_select"]
    assert contract["comparison"] == "order-insensitive exact normalized set"
    assert contract["forbid_empty_search_backspace"] is True
    assert contract["forbid_bulk_clear"] is True
    assert contract["select_all_default"] == "forbidden"
    assert contract["reopen_for_authoritative_verification"] is True


def test_selection_normalization_deduplicates_and_ignores_presentation_text():
    assert normalize_selection_values([
        " Mapping ", "mapping", "4 selected", "Select All", "Logging"
    ]) == ["Mapping", "Logging"]


def test_multiselect_exact_set_proof_passes_only_for_exact_multiple_set():
    proof = multiselect_exact_set_proof(
        ["Mapping", "Logging", "Routing"],
        ["routing", "Mapping", "Logging"],
        selection_mode="multiple",
        selected_count=3,
        available_options=["Mapping", "Logging", "Routing", "Other"],
        option_universe_stable=True,
    )
    assert proof["pass"] is True
    assert proof["missing"] == []
    assert proof["extra"] == []

    missing = multiselect_exact_set_proof(
        ["Mapping", "Logging"],
        ["Mapping"],
        selection_mode="multiple",
        selected_count=1,
        option_universe_stable=True,
    )
    assert missing["pass"] is False
    assert missing["missing"] == ["Logging"]

    wrong_mode = multiselect_exact_set_proof(
        ["Mapping"], ["Mapping"], selection_mode="single", selected_count=1
    )
    assert wrong_mode["pass"] is False


def test_duplicate_requested_multiselect_values_fail_proof():
    proof = multiselect_exact_set_proof(
        ["Mapping", "mapping"],
        ["Mapping"],
        selection_mode="multiple",
        selected_count=1,
    )
    assert proof["pass"] is False
    assert proof["duplicate_expected_values"] is True


def test_every_phase_contract_inherits_advanced_widget_rules():
    keys = {
        "multi_select_exact_set_required",
        "multi_select_additive_preservation",
        "multi_select_safe_removal_only",
        "select_all_guarded",
        "typeahead_exact_option_commit",
        "virtualized_option_stability_gate",
        "radio_group_exclusivity",
        "checkbox_explicit_checked_state",
        "dependent_selection_reset_detection",
        "file_upload_completion_verification",
        "repeatable_row_semantic_identity",
    }
    for contract in PHASE_RUNTIME_CONTRACTS.values():
        policy = contract["transaction_policy"]
        assert all(policy[key] is True for key in keys)


def test_multiselect_driver_uses_exact_delta_and_authoritative_reopen():
    import hip_id_agent.dds_control_driver as driver

    source = inspect.getsource(driver.select_dds_multiselect)
    for token in (
        "_multiselect_delta",
        "_wait_dds_multiselect_snapshot_stable",
        "additive selection proof failed",
        "Select All is forbidden",
        "final_snapshot",
        "selected_count_match",
        "_publish_multiselect_audit",
    ):
        assert token in source
    assert "press(selector, \"Enter\"" not in source
    assert "bulk clear" in source


def test_stateful_engines_require_multiselect_exact_set_proof():
    import hip_id_agent.stateful_form_runtime as runtime

    source = inspect.getsource(runtime.execute_phase_state_graph) + inspect.getsource(runtime.execute_document_type_state_graph)
    assert source.count("multiselect_exact_set_proof") >= 2
    assert source.count("HIP_MULTISELECT_EXACT_SET_PROOF_FAILED") >= 2
    assert source.count("multi_select_driver_audit") >= 2


def test_transport_profile_radio_and_checkbox_do_not_assign_dom_state_directly():
    import hip_id_agent.transport_profile_kb as tp

    radio = inspect.getsource(tp._select_transport_profile_radio_option)
    checkbox = inspect.getsource(tp._set_transport_profile_checkbox)
    assert "select_radio_value" in radio
    assert "set_checkbox_value" in checkbox
    assert ".checked =" not in radio
    assert ".checked =" not in checkbox
    assert "dispatchEvent" not in radio
    assert "dispatchEvent" not in checkbox
