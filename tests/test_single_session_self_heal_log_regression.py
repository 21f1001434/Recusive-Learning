from hip_id_agent.stateful_form_runtime import (
    _repair_document_type_control_semantics,
    _value_equal,
    resolve_control,
)


EXPECTED_USAGE = [
    "Flow Identifier Expression",
    "Logging",
    "Mapping",
    "Routing",
]


def _usage_node(row_index: int):
    return {
        "field_key": "attribute_usage",
        "section": "Attributes To Configure",
        "row_kind": "attribute",
        "row_index": row_index,
        "action": "select_multi",
        "expected_value": EXPECTED_USAGE,
        "semantic_locator": {
            "labels": ["Usage"],
            "placeholders": ["Usage"],
            "names": [],
        },
    }


def test_repeated_unlabelled_dds_usage_control_gets_structural_semantic_identity():
    controls = [
        {
            "index": 19,
            "selector": "input#dds-form-field-616769323",
            "tag": "input",
            "role": "combobox",
            "label": "",
            "placeholder": "",
            "semantic_key": "",
            "section": "Attributes to Configure",
            "row_kind": "attribute",
            "row_index": 2,
            "selection_mode": "multiple",
            "selected_values": EXPECTED_USAGE,
        }
    ]
    repaired = _repair_document_type_control_semantics(controls)
    assert repaired[0]["semantic_key"] == "attribute_usage"
    assert repaired[0]["label"] == "Usage"
    assert repaired[0]["semantic_inference"] == "attribute-row-multiple-combobox"
    assert _value_equal(_usage_node(2), repaired[0]) is True


def test_resolver_accepts_unique_unlabelled_multiselect_in_exact_attribute_row():
    controls = [
        {
            "index": 18,
            "selector": "input#attribute-name-row-3",
            "tag": "input",
            "role": "",
            "label": "Attribute Name",
            "semantic_key": "attribute_name",
            "section": "Attributes To Configure",
            "row_kind": "attribute",
            "row_index": 3,
            "selection_mode": "single",
            "selected_values": [],
        },
        {
            "index": 19,
            "selector": "input#usage-row-3",
            "tag": "input",
            "role": "combobox",
            "label": "",
            "placeholder": "",
            "semantic_key": "",
            "section": "Attributes To Configure",
            "row_kind": "attribute",
            "row_index": 3,
            "selection_mode": "multiple",
            "selected_values": EXPECTED_USAGE,
        },
    ]
    resolved = resolve_control(controls, _usage_node(3))
    assert resolved is not None
    assert resolved["selector"] == "input#usage-row-3"
    assert _value_equal(_usage_node(3), resolved) is True


def test_resolver_remains_fail_closed_when_two_multiselects_exist_in_same_row():
    controls = [
        {
            "selector": "input#usage-a",
            "tag": "input",
            "role": "combobox",
            "semantic_key": "",
            "section": "Attributes To Configure",
            "row_kind": "attribute",
            "row_index": 4,
            "selection_mode": "multiple",
            "selected_values": EXPECTED_USAGE,
        },
        {
            "selector": "input#usage-b",
            "tag": "input",
            "role": "combobox",
            "semantic_key": "",
            "section": "Attributes To Configure",
            "row_kind": "attribute",
            "row_index": 4,
            "selection_mode": "multiple",
            "selected_values": EXPECTED_USAGE,
        },
    ]
    assert resolve_control(controls, _usage_node(4)) is None


def test_five_saved_rows_reconcile_as_exact_without_refill():
    controls = []
    for row in range(5):
        controls.extend([
            {
                "selector": f"input#name-{row}",
                "tag": "input",
                "role": "",
                "semantic_key": "attribute_name",
                "section": "Attributes To Configure",
                "row_kind": "attribute",
                "row_index": row,
                "selection_mode": "single",
                "selected_values": [],
            },
            {
                "selector": f"input#usage-{row}",
                "tag": "input",
                "role": "combobox",
                "semantic_key": "attribute_usage" if row == 0 else "",
                "label": "Usage" if row == 0 else "",
                "section": "Attributes To Configure",
                "row_kind": "attribute",
                "row_index": row,
                "selection_mode": "multiple",
                "selected_values": EXPECTED_USAGE,
            },
        ])
    repaired = _repair_document_type_control_semantics(controls)
    for row in range(5):
        resolved = resolve_control(repaired, _usage_node(row))
        assert resolved is not None
        assert resolved["selector"] == f"input#usage-{row}"
        assert _value_equal(_usage_node(row), resolved) is True


def test_single_session_metrics_distinguish_phase_retries_from_unique_phases(tmp_path):
    from types import SimpleNamespace
    from hip_id_agent.browser_session import BrowserSession

    config = SimpleNamespace()
    session = object.__new__(BrowserSession)
    session._borrow_count = 7
    session._phase_history = [
        {"phase": "data_map"},
        {"phase": "data_map"},
        {"phase": "source_document_type"},
        {"phase": "source_document_type"},
        {"phase": "source_document_type"},
        {"phase": "source_document_type"},
        {"phase": "source_document_type"},
    ]
    metrics = BrowserSession._session_phase_metrics(session)
    assert metrics["phase_attempt_borrow_count"] == 7
    assert metrics["unique_phases_reached"] == ["data_map", "source_document_type"]
    assert metrics["unique_phase_count"] == 2
    assert metrics["phase_retry_count"] == 5
    assert metrics["attempts_by_phase"] == {"data_map": 2, "source_document_type": 5}
