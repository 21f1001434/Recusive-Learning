from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.form_knowledge_plan import compile_phase_plan
from hip_id_agent.portal_form_exploration import _dependency_edges, _state_delta, merge_section_knowledge


def _control(label: str, key: str, selector: str, *, disabled: bool = False, value: str = ""):
    return {
        "label": label,
        "key": key,
        "selector": selector,
        "section": "Configure Target",
        "role": "combobox",
        "fingerprint": f"fp-{key}",
        "disabled": disabled,
        "value": value,
        "options": [],
    }


def test_state_delta_detects_parent_revealed_and_enabled_children():
    before = {
        "controls": [
            _control("Process Step Type", "process_step_type", "#type", value="Select"),
            _control("Action", "process_step_action", "#action", disabled=True),
        ],
        "rowCounts": {"process_steps": 1},
    }
    after = {
        "controls": [
            _control("Process Step Type", "process_step_type", "#type", value="Mapping Transformer"),
            _control("Action", "process_step_action", "#action", disabled=False),
            _control("Target Document Type", "target_document_type", "#doctype"),
            _control("Rule", "rule", "#rule"),
        ],
        "rowCounts": {"process_steps": 1},
    }
    delta = _state_delta(before, after)
    assert {c["key"] for c in delta["added_controls"]} == {"target_document_type", "rule"}
    assert [c["key"] for c in delta["enabled_controls"]] == ["process_step_action"]
    assert delta["meaningful_change"] is True


def test_dependency_edges_are_value_specific_and_observed():
    parent = _control("Process Step Type", "process_step_type", "#type")
    delta = {
        "added_controls": [_control("Target Document Type", "target_document_type", "#doctype")],
        "enabled_controls": [_control("Action", "process_step_action", "#action")],
        "removed_controls": [],
        "disabled_controls": [],
        "changed_controls": [],
    }
    edges = _dependency_edges(parent, "Mapping Transformer", delta, phase="biz_flow", section="Configure Target")
    assert len(edges) == 2
    assert all(edge["when_parent_value"] == "Mapping Transformer" for edge in edges)
    assert all(edge["evidence"].startswith("live") for edge in edges)
    assert {edge["relation"] for edge in edges} == {"PARENT_VALUE_REVEALS_CHILD", "PARENT_VALUE_ENABLES_CHILD"}


def test_merge_section_knowledge_creates_phase_graph(tmp_path: Path):
    first = {
        "status": "complete",
        "section": "Configure Source",
        "control_registry": [{"key": "condition_type"}],
        "parents": [{"key": "condition_type"}],
        "dependency_edges": [{"from": "condition_type", "to": "attribute_name"}],
        "input_mapping_edges": [],
        "repeatable_rows": [],
        "unexplored_branches": [],
        "knowledge_file": "source.json",
    }
    second = {
        "status": "complete_with_gaps",
        "section": "Configure Target",
        "control_registry": [{"key": "process_step_type"}],
        "parents": [{"key": "process_step_type"}],
        "dependency_edges": [],
        "input_mapping_edges": [],
        "repeatable_rows": [],
        "unexplored_branches": [{"value": "Unknown"}],
        "knowledge_file": "target.json",
    }
    out = tmp_path / "biz_flow_form_knowledge.json"
    merged = merge_section_knowledge([first, second], phase="biz_flow", output_file=out)
    assert out.exists()
    assert merged["status"] == "complete_with_gaps"
    assert merged["completeness"]["section_count"] == 2
    assert len(merged["control_registry"]) == 2


def test_deterministic_plan_uses_observed_parent_child_order():
    blueprint = {
        "verification_status": "pass",
        "field_steps": [
            {"key": "target_document_type", "label": "Target Document Type", "selector": "#doctype", "tab": "Configure Target", "value": "DOC(1.0)", "fill_strategy": "select"},
            {"key": "process_step_type", "label": "Process Step Type", "selector": "#type", "tab": "Configure Target", "value": "Mapping Transformer", "fill_strategy": "select"},
        ],
        "repeatable_section_plan": [],
    }
    knowledge = {
        "status": "complete",
        "dependency_edges": [
            {
                "from": "biz_flow.configure_target.process_step_type",
                "to": "biz_flow.configure_target.target_document_type",
                "relation": "PARENT_VALUE_REVEALS_CHILD",
                "when_parent_value": "Mapping Transformer",
                "child_label": "Target Document Type",
                "evidence": "live before/after DOM state delta",
            }
        ],
        "unexplored_branches": [],
    }
    payload = {"process_step_type": "Mapping Transformer", "target_document_type": "DOC(1.0)"}
    plan = compile_phase_plan(
        phase="biz_flow",
        payload=payload,
        learned_blueprint=blueprint,
        blueprint_path=None,
        exploration_knowledge=knowledge,
    )
    field_actions = [a for a in plan["actions"] if a.get("operation") in {"select", "fill"}]
    assert [a["field_key"] for a in field_actions[:2]] == ["process_step_type", "target_document_type"]
    assert any("observed-parent:process_step_type=Mapping Transformer" in p for p in field_actions[1]["preconditions"])
