from pathlib import Path

from hip_id_agent.active_surface import classify_doctype_surface_text
from hip_id_agent.portal_form_exploration import FormExplorationPolicy, _input_value, _option_values


def test_real_run_opened_snapshot_is_create_surface():
    text = """
    Create Document Type
    Document Type Details Name Transaction Type Version Data Format Type Status Description
    Document Identifier Operation Derived From
    Attributes To Configure Attribute Name Derived From Usage Validation Type
    Cancel Submit
    """
    result = classify_doctype_surface_text(
        text,
        visible_labels=["Name", "Transaction Type", "Version", "Data Format Type"],
    )
    assert result["pass"] is True


def test_real_run_filter_listing_snapshot_is_rejected():
    text = """
    Document Types Filter Filters Filter by Column Name Manage Columns
    Document Type Name Data Format Type Transaction Type Latest Version Validation Type
    Items per page 10 20 40 Previous Next
    """
    result = classify_doctype_surface_text(
        text,
        visible_labels=["Document Type Name", "Data Format Type", "Transaction Type"],
        filters_open=True,
    )
    assert result["pass"] is False
    assert result["reason"] == "filters_or_listing_surface"


def test_exploration_input_lookup_is_phase_scoped_not_rule_action_type():
    payload = {
        "objects": {
            "source_document_type": {"transaction_type": "856", "data_format_type": "XML"},
            "rule": {"action_type": "Route Document", "type": "Route Document"},
        }
    }
    value, path = _input_value(payload, "transaction_type", "Transaction Type", phase="source_document_type")
    assert value == "856"
    assert "source_document_type" in path


def test_dropdown_options_require_a_control_specific_match():
    controls = {"selector": "input#transaction", "label": "Transaction Type", "key": "transaction_type"}
    dropdowns = [
        {"selector": "input#other", "label": "Action Type", "key": "action_type", "options": ["Route Document"]},
        {"selector": "input#transaction", "label": "Transaction Type", "key": "transaction_type", "options": ["850", "856"]},
    ]
    assert _option_values(dropdowns, controls) == ["850", "856"]


def test_exploration_restore_failure_is_fail_closed_by_default():
    assert FormExplorationPolicy().fail_closed_on_restore_error is True


def test_repeatable_row_resolver_never_considers_div_or_span():
    source = Path(__file__).parents[1].joinpath("hip_id_agent", "repeatable_rows.py").read_text(encoding="utf-8")
    assert "button,a,[role=\"button\"],dds-button,dds-link" in source
    assert "dds-link,span,div" not in source
    assert "after == current + 1" in source


def test_doctype_dropdown_inventory_does_not_click_page_corner():
    source = Path(__file__).parents[1].joinpath("hip_id_agent", "doctype_kb.py").read_text(encoding="utf-8")
    collect = source[source.index("async def _collect_dropdown_options"):source.index("def _annotate_control_occurrences")]
    assert "page.mouse.click(5, 5)" not in collect
    assert "close_open_dropdown" in collect
    assert "Create Document Type surface was lost" in collect


def test_aia_autogen_does_not_construct_unawaited_coroutine_on_running_loop():
    source = Path(__file__).parents[1].joinpath("hip_id_agent", "aia_client.py").read_text(encoding="utf-8")
    block = source[source.index("def autogen_reply"):source.index("def json_decision")]
    assert "asyncio.get_running_loop()" in block
    assert "ThreadPoolExecutor" in block
    assert "asyncio.new_event_loop" not in block
    assert "await closed" in block
