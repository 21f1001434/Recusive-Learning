from __future__ import annotations

from hip_id_agent.repeatable_row_identity import (
    annotate_controls_with_repeatable_bindings,
    reconcile_repeatable_row_bindings,
    values_equivalent,
)
from hip_id_agent.stateful_form_runtime import resolve_stateful_control_diagnostics


def _node(index: int, field: str, expected: str):
    return {
        "node_id": f"rule.condition[{index}].{field}",
        "phase": "rule",
        "section": "Conditions",
        "field_key": field,
        "action": "fill_text" if field == "condition_value" else "select_single",
        "expected_value": expected,
        "semantic_locator": {"labels": [field.replace("_", " ").title()]},
        "row_kind": "condition",
        "row_index": index,
        "required": True,
        "depends_on": [],
    }


def _control(physical: int, field: str, value: str):
    return {
        "index": physical * 10,
        "selector": f"#{field}-{physical}",
        "semantic_key": field,
        "framework_key": field,
        "label": field.replace("_", " ").title(),
        "section": "Conditions",
        "row_kind": "condition",
        "row_index": physical,
        "role": "combobox" if field != "condition_value" else "",
        "type": "text",
        "component_tag": "dds-dropdown" if field != "condition_value" else "dds-input",
        "value": value,
        "selected_values": [],
        "interactable": True,
    }


def _graph():
    return {
        "phase": "rule",
        "nodes": [
            _node(0, "condition_type", "Attributes"),
            _node(0, "condition_value", "uhaul"),
            _node(1, "condition_type", "Attributes"),
            _node(1, "condition_value", "DELL"),
        ],
    }


def test_enum_and_human_display_values_are_semantically_equivalent():
    assert values_equivalent("ELEMENT_IN_PAYLOAD", "Element In Payload")
    assert values_equivalent("TRANSACTION_ROOT_ELEMENT", "Transaction Root Element")


def test_semantic_row_binding_survives_physical_reorder():
    # Angular rebuilt the array and reversed the physical rows after both values
    # were committed. Expected JSON row 0 must still bind to physical row 1.
    controls = [
        _control(0, "condition_type", "Attributes"),
        _control(0, "condition_value", "DELL"),
        _control(1, "condition_type", "Attributes"),
        _control(1, "condition_value", "uhaul"),
    ]
    annotated, proof = annotate_controls_with_repeatable_bindings(controls, _graph())
    assert proof["pass"] is True
    assert proof["bindings"]["condition"]["0"]["physical_index"] == 1
    assert proof["bindings"]["condition"]["1"]["physical_index"] == 0
    assert proof["bindings"]["condition"]["0"]["strategy"] == "semantic_anchor_match"

    node = _node(0, "condition_value", "uhaul")
    result = resolve_stateful_control_diagnostics(annotated, node)
    assert result["resolved"] is True
    assert result["control"]["physical_row_index"] == 1
    assert result["control"]["expected_row_index"] == 0
    assert result["control"]["value"] == "uhaul"


def test_blank_rows_use_only_provisional_order_then_upgrade_after_anchor_commit():
    graph = _graph()
    blank = [
        _control(0, "condition_type", ""), _control(0, "condition_value", ""),
        _control(1, "condition_type", ""), _control(1, "condition_value", ""),
    ]
    _, first = annotate_controls_with_repeatable_bindings(blank, graph)
    assert first["bindings"]["condition"]["0"]["strategy"] == "provisional_blank_order"
    assert first["bindings"]["condition"]["1"]["strategy"] == "provisional_blank_order"

    # Row 0 is filled and Angular moves it to physical slot 1. The next capture
    # must upgrade the row to semantic identity instead of keeping old position.
    after = [
        _control(0, "condition_type", ""), _control(0, "condition_value", ""),
        _control(1, "condition_type", "Attributes"), _control(1, "condition_value", "uhaul"),
    ]
    _, second = annotate_controls_with_repeatable_bindings(after, graph)
    assert second["bindings"]["condition"]["0"]["physical_index"] == 1
    assert second["bindings"]["condition"]["0"]["strategy"] == "semantic_anchor_match"
    assert second["bindings"]["condition"]["1"]["physical_index"] == 0


def test_conflicting_nonblank_row_is_not_silently_rebound_by_position():
    graph = _graph()
    controls = [
        _control(0, "condition_type", "Something Else"),
        _control(0, "condition_value", "WRONG"),
        _control(1, "condition_type", "Attributes"),
        _control(1, "condition_value", "DELL"),
    ]
    proof = reconcile_repeatable_row_bindings(controls, graph)
    assert proof["pass"] is False
    assert any("conflicts" in str(x.get("reason")) for x in proof["ambiguities"])


def test_row_semantic_identity_contains_no_selector_or_dynamic_dom_id():
    controls = [
        {**_control(0, "condition_type", "Attributes"), "selector": "input#dds-dynamic-123", "id": "dds-dynamic-123"},
        {**_control(0, "condition_value", "uhaul"), "selector": "input#dds-dynamic-456", "id": "dds-dynamic-456"},
        _control(1, "condition_type", ""),
        _control(1, "condition_value", ""),
    ]
    annotated, proof = annotate_controls_with_repeatable_bindings(controls, _graph())
    identity = proof["bindings"]["condition"]["0"]["row_semantic_identity"]
    assert "dds-dynamic" not in identity
    assert len(identity) == 14
    assert any(c.get("row_semantic_identity") == identity for c in annotated)
