import hip_id_agent

def test_v237_package_version():
    assert hip_id_agent.__version__ == "2.4.3"

from types import SimpleNamespace
from pathlib import Path

from hip_id_agent.autonomous_form_runtime import (
    _flatten_phase_input_leaves,
    _supplement_runtime_input_graph,
    _golden_runtime_context,
)


def _control(label, *, section="Create Rule", name="", framework_key="", role="", typ="text", row_index=None, row_kind=""):
    return {
        "label": label,
        "section": section,
        "name": name,
        "form_control_name": framework_key,
        "framework_key": framework_key,
        "placeholder": label,
        "semantic_key": framework_key,
        "role": role,
        "type": typ,
        "tag": "input" if role != "combobox" else "button",
        "component_tag": "dds-dropdown" if role == "combobox" else "dds-input",
        "row_index": row_index,
        "row_kind": row_kind,
        "disabled": False,
        "readonly": False,
        "interactable": True,
        "selection_mode": "single",
    }


def test_runtime_input_ledger_flattens_nested_rows_but_keeps_multiselect_as_one_leaf():
    payload = {
        "objects": {
            "source_document_type": {
                "name": "DOC",
                "attributes_to_configure": [
                    {"attribute_name": "Sender", "usage": ["Mandatory", "Searchable"]}
                ],
            }
        }
    }
    leaves = _flatten_phase_input_leaves(payload, "source_document_type")
    by_path = {x["input_path"]: x["value"] for x in leaves}
    assert by_path["$.objects.source_document_type.name"] == "DOC"
    assert by_path["$.objects.source_document_type.attributes_to_configure[0].attribute_name"] == "Sender"
    assert by_path["$.objects.source_document_type.attributes_to_configure[0].usage"] == ["Mandatory", "Searchable"]


def test_runtime_input_ledger_synthesizes_missing_optional_input_field_from_live_control():
    payload = {
        "objects": {
            "rule": {
                "name": "RULE-1",
                "description": "must also be filled",
                "new_optional_attribute": "CURRENT-INPUT-VALUE",
            }
        }
    }
    graph = {
        "phase": "rule",
        "nodes": [
            {
                "node_id": "rule.name", "phase": "rule", "section": "Create Rule", "field_key": "name",
                "action": "fill_text", "expected_value": "RULE-1", "input_path": "$.objects.rule.name",
                "semantic_locator": {"labels": ["Name"], "names": [], "placeholders": [], "roles": [], "section_aliases": [], "row_kind": "", "row_index": None},
                "required": True, "depends_on": [],
            },
            {
                "node_id": "rule.description", "phase": "rule", "section": "Create Rule", "field_key": "description",
                "action": "fill_text", "expected_value": "must also be filled", "input_path": "$.objects.rule.description",
                "semantic_locator": {"labels": ["Description"], "names": [], "placeholders": [], "roles": [], "section_aliases": [], "row_kind": "", "row_index": None},
                "required": False, "depends_on": [],
            },
        ],
        "input_accounting": [],
    }
    controls = [
        _control("Name", framework_key="name"),
        _control("Description", framework_key="description"),
        _control("New Optional Attribute", framework_key="newOptionalAttribute"),
    ]
    out, ledger = _supplement_runtime_input_graph(graph, controls, payload, phase="rule", section=None)
    new_nodes = [n for n in out["nodes"] if n.get("input_path") == "$.objects.rule.new_optional_attribute"]
    assert len(new_nodes) == 1
    assert new_nodes[0]["expected_value"] == "CURRENT-INPUT-VALUE"
    assert new_nodes[0]["action"] == "fill_text"
    assert "selector" not in new_nodes[0]
    assert ledger["pass"] is True
    assert ledger["runtime_synthesized_node_count"] == 1


def test_runtime_input_ledger_does_not_silently_pass_nonblank_unbound_leaf():
    payload = {"objects": {"rule": {"name": "R", "brand_new_field": "X"}}}
    graph = {
        "phase": "rule",
        "nodes": [{
            "node_id": "rule.name", "phase": "rule", "section": "Create Rule", "field_key": "name",
            "action": "fill_text", "expected_value": "R", "input_path": "$.objects.rule.name",
            "semantic_locator": {"labels": ["Name"], "names": [], "placeholders": [], "roles": [], "section_aliases": [], "row_kind": "", "row_index": None},
            "required": True, "depends_on": [],
        }],
        "input_accounting": [],
    }
    _, ledger = _supplement_runtime_input_graph(graph, [_control("Name", framework_key="name")], payload, phase="rule", section=None)
    assert ledger["pass"] is False
    assert ledger["unresolved_leaf_count"] == 1
    assert ledger["unresolved_input_leaves"][0]["input_path"] == "$.objects.rule.brand_new_field"


def test_bizflow_runtime_ledger_is_current_tab_scoped():
    payload = {
        "objects": {"biz_flow": {
            "flow_details": {"business_flow_name": "FLOW-X"},
            "configure_source": {"source_type": "Dell Application"},
            "configure_routing": {"actions": {"name": "ACTION-X"}},
        }}
    }
    graph = {"phase": "biz_flow", "nodes": [], "input_accounting": []}
    controls = [_control("Business Flow Name", section="Flow Details", framework_key="businessFlowName")]
    out, ledger = _supplement_runtime_input_graph(graph, controls, payload, phase="biz_flow", section="Flow Details")
    assert ledger["runtime_nonblank_leaf_count"] == 1
    assert ledger["pass"] is True
    assert len(out["nodes"]) == 1
    assert out["nodes"][0]["input_path"].endswith("flow_details.business_flow_name")


def test_golden_context_uses_run_attached_phase_references(tmp_path: Path):
    shot = tmp_path / "Rules.png"
    shot.write_bytes(b"fake")
    advisor = object()
    session = SimpleNamespace(
        golden_references_by_phase={"rule": [{"run_local_path": str(shot)}]},
        visual_feedback_agent=advisor,
    )
    page = SimpleNamespace(_hip_browser_session=session)
    refs, actual_advisor = _golden_runtime_context(page, "rule")
    assert refs == [str(shot)]
    assert actual_advisor is advisor
