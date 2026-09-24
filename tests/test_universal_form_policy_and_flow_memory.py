from __future__ import annotations

import inspect
import json

from hip_id_agent.flow_pattern_memory import FlowPatternMemory, describe_graph, pattern_similarity
from hip_id_agent.form_interaction_policy import derive_execution_profile, policy_manifest
from hip_id_agent.hip_form_catalog import HIP_FORM_FAMILIES, classify_form_surface, phase_to_form_family
from hip_id_agent.phase_runtime_contract import validate_phase_runtime_contracts


def _graph(phase: str = "source_transport_profile"):
    return {
        "phase": phase,
        "object_family": "transport_profile",
        "nodes": [
            {
                "node_id": f"{phase}.details.profile_name",
                "section": "Transport Profile Details",
                "row_kind": "",
                "row_index": None,
                "field_key": "profile_name",
                "action": "fill_text",
                "expected_value": "CURRENT INPUT VALUE MUST NOT BE STORED",
                "required": True,
                "depends_on": [],
            },
            {
                "node_id": f"{phase}.interface.interface_type",
                "section": "Interface Details",
                "row_kind": "",
                "row_index": None,
                "field_key": "interface_type",
                "action": "select_single",
                "expected_value": "SFTP-HAFT",
                "required": True,
                "depends_on": [],
            },
            {
                "node_id": f"{phase}.interface.parameter",
                "section": "Interface Details",
                "row_kind": "interface_parameter",
                "row_index": 0,
                "field_key": "parameter_value",
                "action": "fill_text",
                "expected_value": "SECRETISH CURRENT VALUE",
                "required": True,
                "depends_on": [f"{phase}.interface.interface_type"],
            },
        ],
    }


def _execution(graph):
    attempts = []
    for index, node in enumerate(graph["nodes"], start=1):
        attempts.append({
            "node_id": node["node_id"],
            "field": node["field_key"],
            "section": node["section"],
            "row_kind": node["row_kind"],
            "row_index": node["row_index"],
            "success": True,
            "order": index,
            "binding_diagnostics": {"selected_identity": f"identity::{node['field_key']}"},
            "transaction_proof": {"protected_state_changes": []},
        })
    return {
        "schema_version": "hip.stateful-form-execution.v2",
        "pass": True,
        "status": "pass",
        "attempts": attempts,
        "final_form_state_model": {"one_to_one_pass": True},
    }


def test_catalog_covers_known_and_future_hip_forms():
    assert {
        "account", "partner", "system", "domain", "deployment_group",
        "data_map", "document_type", "rule", "transport_profile", "biz_flow",
        "workflow", "transport_profile_orchestration", "flow_orchestration",
        "generic_hip_form",
    }.issubset(HIP_FORM_FAMILIES)
    manifest = policy_manifest()
    assert manifest["coverage"].startswith("all known HIP Portal forms")
    assert manifest["form_family_catalog"]["policy_inheritance"]["all_forms_use_shared_interaction_policy"] is True


def test_form_surface_classifier_uses_url_and_semantics_not_dynamic_ids():
    result = classify_form_surface(
        url="https://developer.dell.com/hybrid-integrations/securelink/transportprofiles",
        text="Create Transport Profile Interface Details Document Type Details",
        controls=[{"id": "dds-form-field-999", "label": "Interface Type", "role": "combobox"}],
    )
    assert result["family"] == "transport_profile"
    assert result["confident"] is True
    assert "dds-form-field-999" not in result["structure_fingerprint"]

    unknown = classify_form_surface(
        url="https://developer.dell.com/hybrid-integrations/new-module/form",
        text="Brand new module form",
    )
    assert unknown["family"] == "generic_hip_form"
    assert unknown["shared_policy_required"] is True


def test_phase_family_aliases_enable_cross_phase_same_type_memory():
    assert phase_to_form_family("source_document_type") == "document_type"
    assert phase_to_form_family("target_document_type") == "document_type"
    assert phase_to_form_family("source_transport_profile") == "transport_profile"
    assert phase_to_form_family("target_transport_profile") == "transport_profile"


def test_flow_pattern_memory_promotes_and_matches_without_storing_values(tmp_path):
    memory = FlowPatternMemory(tmp_path / "flow_patterns", minimum_similarity=0.70)
    graph = _graph("source_transport_profile")
    promoted = memory.promote(
        graph=graph,
        execution=_execution(graph),
        phase="source_transport_profile",
        run_id="run-1",
        judge_pass=True,
    )
    assert promoted["status"] == "promoted"
    raw = (tmp_path / "flow_patterns" / "patterns.json").read_text(encoding="utf-8")
    assert "CURRENT INPUT VALUE MUST NOT BE STORED" not in raw
    assert "SECRETISH CURRENT VALUE" not in raw
    assert '"values_stored": false' in raw.lower()

    target_graph = _graph("target_transport_profile")
    match = memory.match_graph(target_graph, phase="target_transport_profile")
    assert match["validated_match"] is True
    assert match["best"]["phase"] == "source_transport_profile"
    applied = memory.apply_to_graph(target_graph, phase="target_transport_profile")
    assert applied["validated_replay"] is True
    assert applied["memory_replay_profile"]["values_reused"] is False
    assert any(n.get("validated_memory_binding_identity") for n in applied["nodes"])


def test_failed_or_unjudged_pattern_never_enables_fast_replay(tmp_path):
    memory = FlowPatternMemory(tmp_path / "flow_patterns", minimum_similarity=0.70)
    graph = _graph()
    execution = _execution(graph)
    result = memory.promote(
        graph=graph, execution=execution, phase="source_transport_profile",
        run_id="run-candidate", judge_pass=False,
    )
    assert result["trust"] == "candidate"
    assert memory.match_graph(graph, phase="source_transport_profile")["validated_match"] is False


def test_derive_profile_uses_validated_memory_only_when_live_surface_is_not_ambiguous():
    ambiguous = {
        "one_to_one_pass": False,
        "structure_fingerprint": "live-drifted",
        "ambiguous_nodes": ["x"],
        "duplicate_bindings": {},
        "bindings": [],
    }
    graph = {"flow_pattern_memory_match": {"validated_match": True}}
    result = derive_execution_profile(ambiguous, graph)
    assert result["mode"] == "learning"
    assert result["validated_flow_pattern_memory"] is True
    assert result["flow_pattern_memory_live_safe"] is False

    safe_live = {
        "one_to_one_pass": False,
        "structure_fingerprint": "same-family-live",
        "ambiguous_nodes": [],
        "duplicate_bindings": {},
        "bindings": [{"status": "resolved", "binding": {"score_margin": 30}}],
    }
    result = derive_execution_profile(safe_live, graph)
    assert result["mode"] == "validated_fast_replay"
    assert result["flow_pattern_memory_live_safe"] is True


def test_runtime_contract_exports_all_form_catalog_and_memory_lifecycle():
    result = validate_phase_runtime_contracts(["data_map", "source_document_type", "biz_flow"])
    assert result["pass"] is True
    assert result["policy_scope"].startswith("all known HIP Portal forms")
    assert "judge_gated_same_flow_pattern_memory" in result["lifecycle"]
    assert "generic_hip_form" in result["all_hip_form_policy_catalog"]["families"]


def test_browser_session_applies_universal_policy_to_generic_click_and_fill():
    import hip_id_agent.browser_session as browser_session

    click = inspect.getsource(browser_session.BrowserSession.click_and_wait)
    fill = inspect.getsource(browser_session.BrowserSession.fill_and_log)
    assert "universal_locator_preflight" in click
    assert "universal_locator_preflight" in fill
    assert "_observe_form_memory_action" in click
    assert "_observe_form_memory_action" in fill


def test_descriptor_similarity_is_structural_and_value_independent():
    left = describe_graph(_graph("source_transport_profile"), phase="source_transport_profile")
    changed = _graph("target_transport_profile")
    changed["nodes"][0]["expected_value"] = "COMPLETELY DIFFERENT CUSTOMER NAME"
    right = describe_graph(changed, phase="target_transport_profile")
    similarity = pattern_similarity(left, right)
    assert similarity["score"] >= 0.70
    assert similarity["family_match"] is True
