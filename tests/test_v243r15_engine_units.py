"""V243R15: engine rules behind the all-phase replica fixes (no browser)."""
from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.autonomous_dependency_runtime import apply_dependency_execution_contract
from hip_id_agent.autonomous_form_runtime import _display_only_evidence
from hip_id_agent.form_interaction_policy import ordering_only_dependencies, structural_dependencies
from hip_id_agent.stateful_form_runtime import (
    _effective_action,
    _node_section_score,
    _protected_state_changes,
    _stateful_control_score,
    _stateful_value_equal,
    attempt_actual_value,
    compile_phase_state_graph,
)

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def _node(graph, field, row=None):
    return next(n for n in graph["nodes"] if n["field_key"] == field and n.get("row_index") == row)


def test_transport_profile_chain_only_blocks_on_real_parents():
    graph = apply_dependency_execution_contract(compile_phase_state_graph(PAYLOAD, "source_transport_profile"), phase="source_transport_profile")
    system, profile = _node(graph, "partner_name"), _node(graph, "profile_name")
    usage, group, interface = _node(graph, "profile_usage"), _node(graph, "deployment_group"), _node(graph, "interface_type")
    # System Name options depend on System Type; Deployment Group on Profile Usage.
    assert _node(graph, "system_type")["node_id"] in structural_dependencies(graph, system)
    assert usage["node_id"] in structural_dependencies(graph, group)
    # Name / Usage / Interface Type are only filled top to bottom.
    assert structural_dependencies(graph, profile) == []
    assert system["node_id"] in ordering_only_dependencies(graph, profile)
    assert structural_dependencies(graph, usage) == []
    assert structural_dependencies(graph, interface) == []


def test_generic_create_section_does_not_favour_the_page_heading():
    graph = compile_phase_state_graph(PAYLOAD, "source_transport_profile")
    node = _node(graph, "partner_name")
    assert _node_section_score(node, "Create Transport Profile") == _node_section_score(node, "Application Details :")
    system_type = {"label": "System Type *", "section": "Create Transport Profile", "role": "combobox", "semantic_key": "system_type", "interactable": True}
    system_name = {"label": "System Name *", "section": "Application Details :", "role": "combobox", "semantic_key": "system_name", "interactable": True}
    assert _stateful_control_score(system_name, node) - _stateful_control_score(system_type, node) >= 14


def test_radio_group_label_tells_two_yes_no_groups_apart():
    graph = compile_phase_state_graph(PAYLOAD, "source_transport_profile")
    existing, folder = _node(graph, "existing_account"), _node(graph, "use_existing_folder")
    yes_account = {"type": "radio", "label": "Yes", "group_label": "Existing Account *", "section": "Primary Interface Detail :", "interactable": True}
    yes_folder = {"type": "radio", "label": "Yes", "group_label": "Use Existing Folder *", "section": "Primary Interface Detail :", "interactable": True}
    no_folder = dict(yes_folder, label="No")
    checkbox = {"type": "checkbox", "label": "System not onboarded?", "section": "Application Details :", "interactable": True}
    scores = {k: _stateful_control_score(c, existing) for k, c in {"yes_account": yes_account, "yes_folder": yes_folder, "checkbox": checkbox}.items()}
    assert scores["yes_account"] - max(scores["yes_folder"], scores["checkbox"]) >= 14
    assert _stateful_control_score(no_folder, folder) - _stateful_control_score(dict(yes_account, label="No"), folder) >= 14
    assert attempt_actual_value(existing, dict(yes_account, checked=True, value="true")) == "Yes"


def test_version_spacing_is_the_same_single_select_value():
    node = {"phase": "source_transport_profile", "field_key": "document_type", "action": "select_single",
            "expected_value": "XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)"}
    assert _stateful_value_equal(node, {"value": "XML_DellAutoASN_10_U-HAUL_ANS_IB (1.0)"})
    assert not _stateful_value_equal(node, {"value": "XML_DellAutoASN_10_U-HAUL_ANS_IB (2.0)"})


def test_presentation_only_changes_are_not_mutations():
    before = {"n": {"identity": "sec|row|0|k", "value": "Dell Application", "selected_values": [], "aria_invalid": "", "selector": "#a"}}
    after = {"n": {"identity": "sec|row|abc123|k", "value": "Dell Application", "selected_values": ["dell application"], "aria_invalid": "false", "selector": "#a"}}
    assert _protected_state_changes(before, after) == []
    changed = {"n": dict(after["n"], value="AIC - DCE", selected_values=[])}
    assert _protected_state_changes(before, changed)[0]["change"] == "committed_state_changed"
    assert _protected_state_changes(before, {})[0]["change"] == "control_disappeared"


def test_driver_follows_the_live_control():
    text_node = {"action": "fill_text", "expected_value": "yyyyddMMhhmmss"}
    assert _effective_action(text_node, {"role": "combobox", "component_tag": "dds-dropdown"}) == "select_single"
    assert _effective_action(text_node, {"tag": "input", "type": "text"}) == "fill_text"
    select_node = {"action": "select_single", "expected_value": "Ship"}
    assert _effective_action(select_node, {"tag": "input", "type": "text"}) == "fill_text"
    assert _effective_action(select_node, {"tag": "input", "role": "combobox"}) == "select_single"


def test_portal_display_values_are_proven_without_a_control():
    version = {"field_key": "current_flow_version", "value": "1", "input_path": "$.objects.biz_flow.flow_details.current_flow_version"}
    assert _display_only_evidence(version, "Create Biz Flow\nCurrent Flow version : 1.0\nBusiness Flow Name")["status"] == "portal_display_text"
    assert _display_only_evidence(dict(version, value="2"), "Current Flow version : 1.0") is None
    ordinal = {"field_key": "step_number", "value": 2, "input_path": "$.objects.biz_flow.process_steps[1].step_number"}
    assert _display_only_evidence(ordinal, "")["status"] == "portal_row_ordinal"
    assert _display_only_evidence(dict(ordinal, value=5), "") is None
    # Editable fields are never excused by page text.
    assert _display_only_evidence({"field_key": "description", "value": "x", "input_path": "a.description"}, "Description x") is None
