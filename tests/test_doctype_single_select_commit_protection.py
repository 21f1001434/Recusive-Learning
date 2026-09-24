from __future__ import annotations

import inspect

from hip_id_agent.stateful_form_runtime import (
    _repair_document_type_control_semantics,
    capture_document_type_controls,
    execute_document_type_state_graph,
    _wait_for_parent_children_visible,
)


def _single_select(*, value: str = "", locked: str = "", selected=None):
    return {
        "semantic_key": "data_format_type",
        "section": "Document Type Details",
        "row_kind": "",
        "row_index": None,
        "role": "combobox",
        "tag": "input",
        "selection_mode": "single",
        "selected_values": list(selected or []),
        "selected_count": len(selected or []),
        "value": value,
        "raw_value": value,
        "locked_value": locked,
        "label": "Data Format Type",
    }


def test_single_select_option_universe_is_collapsed_to_committed_locked_value():
    controls = [_single_select(
        locked="XML",
        selected=["XML", "JSON", "EDIFACT", "EDIX12", "CSV", "FLAT"],
    )]
    repaired = _repair_document_type_control_semantics(controls)
    assert repaired[0]["selected_values"] == ["XML"]
    assert repaired[0]["selected_count"] == 1
    assert repaired[0]["value"] == "XML"
    assert repaired[0]["value_source"] == "selected-option"
    assert repaired[0]["selection_state_repaired"] == "single-select-option-universe-collapsed"


def test_single_select_never_accepts_multiple_selected_options_without_exact_commit_evidence():
    controls = [_single_select(
        selected=["All conditions are satisfied", "One or more conditions are satisfied"],
    )]
    repaired = _repair_document_type_control_semantics(controls)
    assert repaired[0]["selected_values"] == []
    assert repaired[0]["selected_count"] == 0
    assert repaired[0]["value"] == ""


def test_document_type_transactions_settle_parent_and_restore_only_before_fail_closed():
    capture_source = inspect.getsource(capture_document_type_controls)
    execute_source = inspect.getsource(execute_document_type_state_graph)
    child_source = inspect.getsource(_wait_for_parent_children_visible)

    # The old broad substring selector classified the entire mounted option
    # universe as selected.  Only explicit selected-state markers are allowed.
    assert "[class*=item-selected]" not in capture_source
    assert "classList.contains('dds__dropdown__item-selected')" in capture_source
    assert "locked_value" in capture_source

    # A committed parent is blurred before conditional-child discovery, and a
    # previous exact value may receive one bounded sticky restore before the
    # mutation guard fails closed.
    assert "await close_open_dropdown(page, phase)" in execute_source
    assert "restore_filled_values" in execute_source
    assert 'protected_state_changes_initial' in execute_source
    assert 'protected_value_restore' in execute_source
    assert "await close_open_dropdown(page, phase)" in child_source
