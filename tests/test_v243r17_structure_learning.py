"""V243R17: the agent learns a form's structure and reuses it; unit checks of each fix."""
from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.autonomous_form_runtime import (
    _choice_option_for_value,
    _leaf_control_score,
    _supplement_runtime_input_graph,
    _unmet_success_checks,
)
from hip_id_agent.form_structure_healer import plan_row_groups
from hip_id_agent.form_structure_memory import FormStructureMemory, path_pattern
from hip_id_agent.stateful_form_runtime import ROW_EXCLUDED_SCORE, _stateful_control_score, _value_equal
from phase_replica_support import dom_checked, dom_values, run_variant_replica


# ---------------------------------------------------------------- learning
def test_second_run_starts_from_the_learned_form_structure(tmp_path: Path):
    first, _ = run_variant_replica(tmp_path, "data_map", broker=True)
    assert first["pass"] is True, first.get("failure_summary")
    learned = first["form_structure_memory"]["learned"]
    assert learned["learned_fields"] >= 4 and learned["learned_sections"] == 1 and learned["learned_rows"] == 1
    memory = json.loads(Path(learned["file"]).read_text(encoding="utf-8"))
    assert memory["fields"]["advanced_options.notify_on"]["options"] == ["Success", "Failure", "Warning"]
    assert memory["fields"]["advanced_options.map_engine"]["group_label"] == "Map Engine"
    assert memory["rows"]["list:cross_reference_map_list"]["add_label"] == "+ Add Row"
    # Value-free: typed input values are never stored.  A radio group keeps the
    # portal's own option labels (its structure), not which one was chosen.
    text = json.dumps(memory)
    for value in ("840", "124", "US", "DELLCoXMLASNXX08C", "Transform_DELLCoXMLASNXX08C"):
        assert value not in text
    assert memory["fields"]["advanced_options.map_engine"]["options"] == ["Contivo", "XSLT"]

    second, dom = run_variant_replica(tmp_path, "data_map", broker=True)
    assert second["pass"] is True, second.get("failure_summary")
    used = second["form_structure_memory"]
    assert len(used["seeded_nodes"]) >= 6
    assert [e["title"] for e in used["reveal"]["expanded"]] == ["Advanced Options"]
    # Nothing was unknown at the start of the second run.
    assert second["cycles"][0]["runtime_input_leaf_ledger"]["unresolved_leaf_count"] == 0
    assert dom_values(dom, "Source Value") == ["US", "CA"]
    assert dom_checked(dom, "notifyOn") == ["Failure", "Warning"]


def test_a_learned_field_that_no_longer_binds_is_demoted(tmp_path: Path):
    memory = FormStructureMemory(tmp_path)
    data = memory.load("rule")
    data["fields"]["advanced.priority"] = {"pattern": "advanced.priority", "field_key": "priority", "action": "select_radio",
                                           "success_count": 1, "failure_count": 0}
    memory.save("rule", data)
    memory.demote("rule", ["advanced.priority"])
    assert memory.load("rule")["fields"]["advanced.priority"]["failure_count"] == 1
    memory.demote("rule", ["advanced.priority"])
    assert "advanced.priority" not in memory.load("rule")["fields"]


def test_seeded_checkbox_group_ticks_exactly_the_listed_options(tmp_path: Path):
    memory = FormStructureMemory(tmp_path)
    data = memory.load("data_map")
    data["fields"]["advanced_options.notify_on"] = {
        "pattern": "advanced_options.notify_on", "field_key": "notify_on", "action": "select_checkbox_group",
        "group_label": "Notify On", "options": ["Success", "Failure", "Warning"], "section": "Advanced Options",
    }
    memory.save("data_map", data)
    graph = {"nodes": []}
    added = memory.seed_graph("data_map", graph, [{"input_path": "$.objects.data_map.advanced_options.notify_on", "value": ["Warning"]}])
    assert [(a["option"]) for a in added] == ["Success", "Failure", "Warning"]
    assert [n["expected_value"] for n in graph["nodes"]] == ["false", "false", "true"]


def test_path_pattern_is_phase_relative_and_row_generic():
    assert path_pattern("$.objects.target_transport_profile.tags[1].key") == "tags[*].key"
    assert path_pattern("$.objects.rule.advanced.priority") == "advanced.priority"


# ---------------------------------------------------------------- binding
def _radio(label, group, name, section="Primary Interface Detail :", **extra):
    return {"type": "radio", "role": "", "label": label, "group_label": group, "group_name": name, "section": section,
            "selector": f"#{name}-{label}", "interactable": True, **extra}


def test_short_option_words_do_not_match_unrelated_input_keys():
    leaf = {"input_path": "$.objects.target_transport_profile.notification_settings.notify_on", "field_key": "notify_on", "value": ["Failure"]}
    no_option = _radio("No", "Use Existing Folder *", "useExistingFolder")
    assert _leaf_control_score(leaf, no_option, "target_transport_profile") <= 0


def test_radio_group_options_are_one_binding_target():
    controls = [_radio("Yes", "Existing Account *", "existingAccount"), _radio("No", "Existing Account *", "existingAccount"),
                _radio("Yes", "Use Existing Folder *", "useExistingFolder"), _radio("No", "Use Existing Folder *", "useExistingFolder")]
    data = {"objects": {"target_transport_profile": {"use_existing_folder": True}}}
    graph, ledger = _supplement_runtime_input_graph({"nodes": []}, controls, data, phase="target_transport_profile", section=None)
    assert ledger["unresolved_leaf_count"] == 0
    node = graph["nodes"][0]
    # True maps onto the group's own "Yes" option, in the right group.
    assert (node["action"], node["expected_value"], node["choice_group"]) == ("select_radio", "Yes", "Use Existing Folder *")


def test_a_row_n_field_never_binds_to_another_row():
    node = {"field_key": "condition_value", "action": "fill_text", "row_kind": "condition", "row_index": 1,
            "section": "Conditions", "semantic_locator": {"labels": ["Value"]}}
    row0 = {"label": "Value", "placeholder": "Value", "section": "Conditions :", "row_kind": "condition", "row_kind_ordinal": 0,
            "row_signature": "div > div", "type": "text", "interactable": True}
    assert _stateful_control_score(row0, node) == ROW_EXCLUDED_SCORE
    assert _stateful_control_score(dict(row0, row_kind_ordinal=1), node) > 0


def test_missing_rows_are_planned_from_input_and_graph():
    graph = {"nodes": [{"row_kind": "condition", "row_index": i, "section": "Conditions", "semantic_locator": {"labels": ["Value"]}} for i in range(3)]}
    leaves = [{"input_path": "$.objects.target_transport_profile.tags[1].key", "row_index": 1}]
    groups = {g["group"]: g for g in plan_row_groups(graph, leaves, {})}
    assert groups["kind:condition"]["needed"] == 3
    assert groups["list:$.objects.target_transport_profile.tags"]["needed"] == 2
    assert "tag" in groups["list:$.objects.target_transport_profile.tags"]["aliases"]


# ---------------------------------------------------------------- values
def test_punctuation_only_values_verify_exactly():
    node = {"action": "fill_text", "expected_value": "~", "field_key": "segment_separator"}
    assert _value_equal(node, {"value": "~"}) is True
    assert _value_equal(node, {"value": "*"}) is False


def test_boolean_and_word_values_map_onto_radio_options():
    assert _choice_option_for_value(True, ["Yes", "No"]) == "Yes"
    assert _choice_option_for_value("N", ["Yes", "No"]) == "No"
    assert _choice_option_for_value("webhook", ["Email", "Webhook"]) == "Webhook"
    assert _choice_option_for_value("Fax", ["Email", "Webhook"]) is None


def test_fields_found_after_the_fill_keep_the_goal_open():
    unmet = _unmet_success_checks({"pass": True}, [], [], {"pass": True, "runtime_synthesized_node_count": 2}, True, {}, False)
    assert "new_fields_found_after_fill" in unmet
