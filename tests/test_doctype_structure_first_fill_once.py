from __future__ import annotations

import inspect
from pathlib import Path

from hip_id_agent.dummy_fill_e2e import _artifact_actual_state
from hip_id_agent.portal_form_exploration import _logical_parent_signature, _safe_parent
from hip_id_agent.section_judge import _reconcile_text_judge, _reconcile_vision_judge
from hip_id_agent.stateful_form_runtime import (
    _repair_document_type_control_semantics,
    compile_document_type_state_graph,
)


def _payload():
    return {
        "objects": {
            "source_document_type": {
                "name": "SRC",
                "transaction_type": "856",
                "version": "1",
                "data_format_type": "XML",
                "status": "Enable",
                "description": "source",
                "validation_type": "Structure",
                "document_identifier": {
                    "operation": "All conditions are satisfied",
                    "rows": [{"derived_from": "TRANSACTION_ROOT_ELEMENT", "value": "DellAutoASN"}],
                },
                "attributes_to_configure": [
                    {
                        "attribute_name": "Receiver",
                        "derived_from": "ELEMENT_IN_PAYLOAD",
                        "usage": "Flow Identifier Expression, Logging, Mapping, Routing",
                        "expression": "/DellAutoASN/Header/To/PartnerIdentifier",
                    }
                ],
            }
        }
    }


def test_document_identifier_operation_is_not_a_repeatable_row_and_data_row_is_zero_based():
    controls = [
        {
            "semantic_key": "document_identifier_operation",
            "row_kind": "document_identifier",
            "row_index": 0,
            "label": "Operation",
        },
        {
            "semantic_key": "document_identifier_derived_from",
            "row_kind": "document_identifier",
            "row_index": 1,
            "label": "Derived From",
        },
        {
            "semantic_key": "document_identifier_value",
            "row_kind": "document_identifier",
            "row_index": 1,
            "label": "Value",
        },
    ]
    repaired = _repair_document_type_control_semantics(controls)
    operation = repaired[0]
    derived = repaired[1]
    value = repaired[2]
    assert operation["row_kind"] == "" and operation["row_index"] is None
    assert derived["row_kind"] == "document_identifier" and derived["row_index"] == 0
    assert value["row_kind"] == "document_identifier" and value["row_index"] == 0
    assert derived["raw_dom_row_index"] == 1


def test_saved_dom_counts_only_real_document_identifier_rows(tmp_path: Path):
    dom = tmp_path / "dom_snapshots"
    dom.mkdir()
    (dom / "source_document_type_after_dummy_fill_no_save.html").write_text(
        '''<html><body>
        <fieldset><legend>Document Identifier</legend>
          <div><input placeholder="Operation" value="All conditions are satisfied"></div>
          <div>
            <dds-dropdown><input role="combobox" placeholder="Derived From" value="TRANSACTION_ROOT_ELEMENT"></dds-dropdown>
            <input name="value" placeholder="Value" value="DellAutoASN">
          </div>
        </fieldset>
        </body></html>''',
        encoding="utf-8",
    )
    state = _artifact_actual_state(tmp_path, [])
    assert state["row_counts"]["document_identifier_rows"] == 1
    derived = next(c for c in state["controls"] if c["label"] == "Derived From")
    operation = next(c for c in state["controls"] if c["label"] == "Operation")
    assert derived["row_kind"] == "document_identifier" and derived["row_index"] == 0
    assert operation["row_kind"] == "" and operation["row_index"] is None


def test_document_type_version_is_verify_only_never_fill():
    graph = compile_document_type_state_graph(_payload(), "source_document_type")
    version = next(n for n in graph["nodes"] if n["field_key"] == "document_type_version")
    assert version["action"] == "verify_only"


def test_document_type_parent_learning_dedupes_dynamic_selectors_and_rows():
    first = {
        "semantic_key": "attribute_derived_from",
        "section": "Attributes To Configure",
        "row_kind": "attribute",
        "row_index": 0,
        "role": "combobox",
        "selector": "input#dds-form-field-111",
    }
    second = {**first, "row_index": 4, "selector": "#dds-form-field-222"}
    assert _logical_parent_signature(first, phase="source_document_type") == _logical_parent_signature(second, phase="source_document_type")
    assert _safe_parent({**first, "disabled": True}) is False
    assert _safe_parent({**first, "readonly": True}) is False


def test_text_and_vision_judges_use_canonical_field_aliases():
    deterministic = {
        "pass": True,
        "matched_values": [
            {
                "field": "document_identifier.operation",
                "expected": "All conditions are satisfied",
                "actual": "All conditions are satisfied",
            },
            {
                "field": "attributes[1].expression",
                "expected": "/Root/Value",
                "actual": "/Root/Value",
            },
        ],
    }
    text = _reconcile_text_judge(
        {
            "status": "ok",
            "pass": False,
            "missing_or_wrong": [
                {
                    "field": "document_identifier_operation",
                    "expected": "All conditions are satisfied",
                    "actual": "all conditions are satisfied",
                    "reason": "case mismatch",
                }
            ],
        },
        deterministic,
    )
    assert text["pass"] is True and not text["missing_or_wrong"]

    vision = _reconcile_vision_judge(
        {
            "status": "ok",
            "pass": False,
            "visible_issues": [
                {
                    "field": "attributes_1_expression",
                    "expected": "/Root/Value",
                    "observed": "different",
                    "reason": "screenshot interpretation",
                }
            ],
        },
        deterministic,
    )
    assert vision["pass"] is True and not vision["visible_issues"]


def test_doctype_runtime_learns_before_fill_and_has_no_post_fill_exploration():
    import hip_id_agent.doctype_kb as doctype_kb

    source = inspect.getsource(doctype_kb.DocumentTypeKBFlow.run)
    learn_pos = source.index("doctype_structure_first_audit.json")
    fill_pos = source.index("doctype_kb_target_branch_fill_once")
    assert learn_pos < fill_pos
    assert "post_exploration_target_restore" not in source
    assert "post_fill_exploration_allowed\"] = False" in source
    assert "verify_only_never_type" in source


def test_exact_filled_doctype_still_captures_read_only_judge_evidence():
    import hip_id_agent.doctype_kb as doctype_kb

    source = inspect.getsource(doctype_kb.DocumentTypeKBFlow.run)
    frozen_marker = source.index('exact_form_frozen = bool')
    dropdown_guard = source.index('if not exact_form_frozen:', frozen_marker)
    dom_capture = source.index('save_dom_snapshot("doctype_add_form_after_dummy_fill_no_save")', frozen_marker)
    screenshot_capture = source.index('doctype_add_form_after_dummy_fill_no_save.png', frozen_marker)
    evidence_lock = source.index('doctype_filled_form_evidence_lock.json', frozen_marker)
    assert dropdown_guard < dom_capture < evidence_lock
    assert dropdown_guard < screenshot_capture < evidence_lock
    assert 'post_fill_control_exploration_performed": False if exact_form_frozen' in source
    assert 'HIP_DOCTYPE_FILLED_FORM_EVIDENCE_CAPTURE_FAILED' in source
