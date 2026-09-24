from __future__ import annotations

import inspect

from hip_id_agent.dds_control_driver import (
    _combobox_value_variants,
    _choose_unique_single_option,
    _single_select_snapshot_matches,
    select_dds_combobox,
)
from hip_id_agent.stateful_form_runtime import _value_equal, capture_document_type_controls


def test_enum_input_generates_human_dds_display_variant():
    variants = _combobox_value_variants("TRANSACTION_ROOT_ELEMENT")
    lowered = {v.lower() for v in variants}
    assert "transaction root element" in lowered


def test_blank_search_input_still_accepts_exact_owned_selected_option_commit():
    snapshot = {
        "input_value": "",
        "selected_values": ["Element In Payload"],
        "selected_labels": [],
        "committed_candidates": ["Element In Payload"],
    }
    variants = _combobox_value_variants("ELEMENT_IN_PAYLOAD")
    assert _single_select_snapshot_matches(snapshot, variants) is True


def test_owned_listbox_option_selection_is_unique_and_enum_aware():
    snapshot = {
        "options": [
            {"text": "Transaction Root Element", "disabled": False, "id": "opt-root"},
            {"text": "File Name", "disabled": False, "id": "opt-file"},
        ]
    }
    option = _choose_unique_single_option(snapshot, "TRANSACTION_ROOT_ELEMENT", _combobox_value_variants("TRANSACTION_ROOT_ELEMENT"))
    assert option and option["id"] == "opt-root"


def test_base_name_fallback_never_guesses_between_multiple_versions():
    snapshot = {
        "options": [
            {"text": "XML_SHIPMENT_NOTICE (1.0)", "disabled": False},
            {"text": "XML_SHIPMENT_NOTICE (2.0)", "disabled": False},
        ]
    }
    # A base-only request is ambiguous and must fail closed instead of clicking
    # whichever version happens to appear first.
    option = _choose_unique_single_option(snapshot, "XML_SHIPMENT_NOTICE", _combobox_value_variants("XML_SHIPMENT_NOTICE"))
    assert option is None


def test_stateful_verifier_treats_enum_and_human_dds_label_as_same_commit():
    node = {"field_key": "attribute_derived_from", "action": "select_single", "expected_value": "ELEMENT_IN_PAYLOAD"}
    control = {"value": "", "selection_mode": "single", "selected_values": ["Element In Payload"]}
    assert _value_equal(node, control) is True


def test_doctype_capture_follows_aria_controls_and_driver_never_global_option_clicks():
    capture_source = inspect.getsource(capture_document_type_controls)
    driver_source = inspect.getsource(select_dds_combobox)
    assert "aria-controls" in capture_source
    assert "document.getElementById(listId)" in capture_source
    assert "owned listbox" in driver_source.lower()
    assert "get_by_role(\"option\"" not in driver_source
    assert "Enter is never pressed blindly" in (select_dds_combobox.__doc__ or "")
