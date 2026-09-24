from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .bizflow_kb import BIZFLOWS_URL
from .datamap_kb import DATAMAPS_URL
from .doctype_kb import DOCTYPES_URL
from .rules_kb import RULES_URL
from .transport_profile_kb import TRANSPORT_PROFILES_URL
from .hip_form_catalog import catalog_manifest

PHASE_RUNTIME_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "data_map": {
        "display": "Data Map",
        "family": "data_map",
        "url": DATAMAPS_URL,
        "input_object": "data_map",
        "surface_markers": ["Data Map", "Map Identifier", "Map Class"],
        "repeatable_kinds": [],
        "exact_state_checks": ["map identity", "version", "class", "Contivo version", "JAR upload"],
    },
    "source_document_type": {
        "display": "Source Document Type",
        "family": "document_type",
        "url": DOCTYPES_URL,
        "input_object": "source_document_type",
        "surface_markers": ["Document Type", "Document Identifier", "Attributes to Configure"],
        "repeatable_kinds": ["attribute"],
        "exact_state_checks": ["identifier", "format", "usage exact set", "attribute rows", "expression"],
    },
    "target_document_type": {
        "display": "Target Document Type",
        "family": "document_type",
        "url": DOCTYPES_URL,
        "input_object": "target_document_type",
        "surface_markers": ["Document Type", "Document Identifier", "Attributes to Configure"],
        "repeatable_kinds": ["attribute"],
        "exact_state_checks": ["identifier", "format", "usage exact set", "attribute rows", "expression"],
    },
    "rule": {
        "display": "Rule",
        "family": "rule",
        "url": RULES_URL,
        "input_object": "rule",
        "surface_markers": ["Rule", "Condition", "Action"],
        "repeatable_kinds": ["condition", "action"],
        "exact_state_checks": ["rule identity", "condition row count", "operators", "values", "actions"],
    },
    "source_transport_profile": {
        "display": "Source Transport Profile",
        "family": "transport_profile",
        "url": TRANSPORT_PROFILES_URL,
        "input_object": "source_transport_profile",
        "surface_markers": ["Transport Profile", "Interface", "Document Type"],
        "repeatable_kinds": ["interface_parameter", "document_type"],
        "exact_state_checks": ["profile identity", "sender usage", "interface parameters", "document types", "deployment group"],
    },
    "target_transport_profile": {
        "display": "Target Transport Profile",
        "family": "transport_profile",
        "url": TRANSPORT_PROFILES_URL,
        "input_object": "target_transport_profile",
        "surface_markers": ["Transport Profile", "Interface", "Document Type"],
        "repeatable_kinds": ["interface_parameter", "document_type"],
        "exact_state_checks": ["profile identity", "receiver usage", "interface parameters", "document types", "deployment group"],
    },
    "biz_flow": {
        "display": "BizFlow",
        "family": "biz_flow",
        "url": BIZFLOWS_URL,
        "input_object": "biz_flow",
        "surface_markers": ["Business Flow", "Source Details", "Process Steps"],
        "repeatable_kinds": ["flow_identifier", "process_step", "routing"],
        "exact_state_checks": ["flow identity", "source", "target", "identifier rows", "process steps", "routing"],
    },
}


_ALL_PHASE_TRANSACTION_POLICY = {
    "semantic_binding": "section + row + field + Angular/DDS metadata + accessibility identity",
    "unique_control_binding_required": True,
    "minimum_confidence_margin": 14,
    "protect_previously_committed_fields": True,
    "stable_consecutive_observations": 2,
    "unintended_mutation_fails_action": True,
    "exact_completion_freezes_phase": True,
    "judge_cannot_reopen_completed_phase": True,
    "structural_parent_first": True,
    "explicit_click_check_for_radios_and_dropdowns": True,
    "conditional_child_visibility_gate": True,
    "animation_bounding_box_stability": True,
    "active_surface_hit_test": True,
    "stale_node_semantic_rebind": True,
    "blocking_validation_gate": True,
    "learn_once_validated_fast_replay": True,
    "multi_select_exact_set_required": True,
    "multi_select_additive_preservation": True,
    "multi_select_safe_removal_only": True,
    "select_all_guarded": True,
    "typeahead_exact_option_commit": True,
    "virtualized_option_stability_gate": True,
    "radio_group_exclusivity": True,
    "checkbox_explicit_checked_state": True,
    "dependent_selection_reset_detection": True,
    "file_upload_completion_verification": True,
    "repeatable_row_semantic_identity": True,
    "dependency_dag_scheduler": True,
    "parent_exact_commit_before_child": True,
    "sequential_repeatable_row_transactions": True,
    "state_proof_over_transport_timeout": True,
    "validated_memory_order_cannot_override_dependencies": True,
}
for _phase_contract in PHASE_RUNTIME_CONTRACTS.values():
    _phase_contract.setdefault("transaction_policy", dict(_ALL_PHASE_TRANSACTION_POLICY))
    _phase_contract.setdefault("evidence_lock", True)



def validate_phase_runtime_contracts(phases: Iterable[str]) -> Dict[str, Any]:
    requested: List[str] = list(phases)
    missing = [p for p in requested if p not in PHASE_RUNTIME_CONTRACTS]
    malformed: List[Dict[str, Any]] = []
    required_keys = {"display", "family", "url", "input_object", "surface_markers", "repeatable_kinds", "exact_state_checks", "transaction_policy", "evidence_lock"}
    for phase in requested:
        contract = PHASE_RUNTIME_CONTRACTS.get(phase)
        if not isinstance(contract, dict):
            continue
        absent = sorted(required_keys - set(contract))
        if absent or not contract.get("url") or not contract.get("surface_markers"):
            malformed.append({"phase": phase, "missing_keys": absent})
    return {
        "schema_version": "hip.phase-runtime-contract.v1",
        "pass": not missing and not malformed,
        "requested_phases": requested,
        "covered_phases": [p for p in requested if p in PHASE_RUNTIME_CONTRACTS],
        "missing_phases": missing,
        "malformed": malformed,
        "contracts": {p: PHASE_RUNTIME_CONTRACTS[p] for p in requested if p in PHASE_RUNTIME_CONTRACTS},
        "all_hip_form_policy_catalog": catalog_manifest(),
        "policy_scope": "all known HIP Portal forms plus generic /hybrid-integrations fallback",
        "lifecycle": [
            "persistent_session_borrow",
            "phase_attempt_preflight",
            "kb_guided_react_route",
            "autonomous_page_health",
            "dual_mcp_same_surface",
            "phase_input_execution",
            "exact_state_verification",
            "text_and_vision_judge",
            "bounded_self_heal",
            "candidate_memory_promotion_after_judge",
            "deep_portal_learning",
            "api_validation_transition_drift_model",
            "all_phase_unique_control_binding",
            "all_phase_state_transaction_verification",
            "all_phase_committed_field_protection",
            "all_phase_exact_state_lock",
            "structural_parent_first_hidden_field_resolution",
            "explicit_widget_event_contract",
            "conditional_child_visibility_gate",
            "animation_bbox_stability_gate",
            "validated_learn_once_fast_replay",
            "multi_select_exact_set_and_additive_preservation",
            "safe_multiselect_removal_no_empty_backspace",
            "typeahead_exact_option_commit",
            "radio_checkbox_group_state_proof",
            "virtualized_option_universe_stability",
            "dependent_selection_reset_detection",
            "universal_form_family_policy",
            "judge_gated_same_flow_pattern_memory",
            "cross_phase_same_family_fast_replay_without_value_reuse",
            "dependency_dag_topological_scheduler",
            "parent_commit_child_mount_rebind_gate",
            "sequential_repeatable_row_completion",
            "exploration_to_judge_validated_exploitation",
            "mission_phase_entity_dependency_gate",
        ],
    }
