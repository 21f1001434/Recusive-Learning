from __future__ import annotations

import inspect

from hip_id_agent.stateful_form_runtime import (
    _protected_state_changes,
    build_document_type_form_state_model,
    compile_document_type_state_graph,
    resolve_document_type_control_diagnostics,
)


def _node(field_key: str, *, row_index=None, row_kind=None, action="fill_text", labels=(), names=()):
    return {
        "node_id": f"source_document_type.test.{field_key}.{row_index}",
        "phase": "source_document_type",
        "section": "Attributes To Configure" if row_kind else "Document Type Details",
        "field_key": field_key,
        "action": action,
        "expected_value": "Receiver" if field_key == "attribute_name" else "SRC",
        "input_path": f"objects.source_document_type.{field_key}",
        "semantic_locator": {
            "labels": list(labels),
            "names": list(names),
            "placeholders": list(labels),
            "roles": [],
            "row_kind": row_kind,
            "row_index": row_index,
        },
        "row_kind": row_kind,
        "row_index": row_index,
        "required": True,
        "depends_on": [],
    }


def test_framework_binding_and_row_identity_outweigh_dynamic_selector():
    node = _node("attribute_name", row_index=1, row_kind="attribute", labels=("Attribute Name",), names=("attributeName",))
    controls = [
        {
            "index": 1,
            "selector": "input#dds-form-field-111",
            "semantic_key": "attribute_name",
            "section": "Attributes To Configure",
            "row_kind": "attribute",
            "row_index": 0,
            "label": "Attribute Name",
            "name": "attributeName",
            "framework_key": "attributeName",
            "role": "",
            "type": "text",
            "interactable": True,
        },
        {
            "index": 2,
            "selector": "input#dds-form-field-999",
            "semantic_key": "attribute_name",
            "section": "Attributes To Configure",
            "row_kind": "attribute",
            "row_index": 1,
            "label": "Attribute Name",
            "name": "attributeName",
            "framework_key": "attributeName",
            "role": "",
            "type": "text",
            "interactable": True,
        },
    ]
    result = resolve_document_type_control_diagnostics(controls, node)
    assert result["resolved"] is True
    assert result["control"]["row_index"] == 1
    assert result["score_margin"] >= 16


def test_ambiguous_unscoped_controls_are_rejected_instead_of_random_fill():
    node = _node("document_type_name", labels=("Name",), names=("name",))
    controls = [
        {
            "index": 1,
            "selector": "input#one",
            "semantic_key": "document_type_name",
            "section": "Document Type Details",
            "label": "Name",
            "name": "name",
            "framework_key": "name",
            "type": "text",
            "interactable": True,
        },
        {
            "index": 2,
            "selector": "input#two",
            "semantic_key": "document_type_name",
            "section": "Document Type Details",
            "label": "Name",
            "name": "name",
            "framework_key": "name",
            "type": "text",
            "interactable": True,
        },
    ]
    result = resolve_document_type_control_diagnostics(controls, node)
    assert result["resolved"] is False
    assert result["reason"] == "ambiguous candidate margin"


def test_form_model_detects_duplicate_node_to_control_bindings():
    graph = {
        "phase": "source_document_type",
        "graph_id": "g",
        "nodes": [
            _node("document_type_name", labels=("Name",), names=("name",)),
            {**_node("document_type_name", labels=("Name",), names=("name",)), "node_id": "duplicate-name-node"},
        ],
    }
    controls = [
        {
            "index": 1,
            "selector": "input#name",
            "semantic_key": "document_type_name",
            "section": "Document Type Details",
            "label": "Name",
            "name": "name",
            "framework_key": "name",
            "type": "text",
            "interactable": True,
        }
    ]
    model = build_document_type_form_state_model(graph, controls)
    assert model["one_to_one_pass"] is False
    assert model["duplicate_bindings"]


def test_protected_state_change_catches_version_or_prior_field_corruption():
    before = {
        "version": {"identity": "version", "value": "1.0", "selected_values": [], "checked": False, "disabled": True, "readonly": True, "aria_invalid": ""},
        "name": {"identity": "name", "value": "SRC", "selected_values": [], "checked": False, "disabled": False, "readonly": False, "aria_invalid": ""},
    }
    after = {
        "version": {**before["version"], "value": "random"},
        "name": before["name"],
    }
    changes = _protected_state_changes(before, after)
    assert changes == [
        {
            "node_id": "version",
            "change": "committed_state_changed",
            "before": before["version"],
            "after": after["version"],
        }
    ]


def test_document_type_graph_keeps_version_verify_only_and_runtime_uses_transactions():
    payload = {
        "objects": {
            "source_document_type": {
                "name": "SRC",
                "version": "1",
                "data_format_type": "XML",
                "document_identifier": {"operation": "All conditions are satisfied", "rows": []},
                "attributes_to_configure": [],
                "validation_type": "Structure",
            }
        }
    }
    graph = compile_document_type_state_graph(payload, "source_document_type")
    version = next(n for n in graph["nodes"] if n["field_key"] == "document_type_version")
    assert version["action"] == "verify_only"

    import hip_id_agent.stateful_form_runtime as runtime
    source = inspect.getsource(runtime.execute_document_type_state_graph)
    assert "_wait_for_document_type_transaction_stable" in source
    assert "HIP_DOCTYPE_UNINTENDED_MUTATION" in source
    assert "build_document_type_form_state_model" in source
    assert "completed_node_ids" in source


def test_dom_capture_includes_framework_css_and_hit_test_metadata():
    import hip_id_agent.stateful_form_runtime as runtime
    source = inspect.getsource(runtime.capture_document_type_controls)
    for token in (
        "formcontrolname",
        "ng-reflect-name",
        "framework_key",
        "component_tag",
        "semantic_path",
        "elementFromPoint",
        "interactable",
        "pointer_events",
        "bbox",
    ):
        assert token in source
