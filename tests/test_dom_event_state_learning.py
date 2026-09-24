from hip_id_agent.browser_session import DOM_EVENT_OBSERVER_SCRIPT
from hip_id_agent.config import ExtractionConfig
from hip_id_agent.stateful_form_runtime import summarize_dom_transition_window


def test_dom_observer_captures_full_form_lifecycle():
    for token in [
        "MutationObserver",
        "focusin",
        "focusout",
        "input",
        "change",
        "keydown",
        "added_controls",
        "removed_controls",
        "aria-expanded",
        "aria-disabled",
        "required",
    ]:
        assert token in DOM_EVENT_OBSERVER_SCRIPT


def test_dom_event_collection_is_enabled_and_bounded_by_default():
    cfg = ExtractionConfig()
    assert cfg.collect_dom_events is True
    assert cfg.collect_dom_mutations is True
    assert cfg.max_dom_event_records == 6000
    assert cfg.max_dom_mutation_records == 6000


def test_transition_summary_extracts_dynamic_child_and_commit_events():
    window = {
        "events": [
            {"type": "focusin"},
            {"type": "input"},
            {"type": "change"},
            {"type": "focusout"},
        ],
        "mutations": [
            {
                "type": "childList",
                "added_controls": [
                    {"label": "Account", "role": "combobox", "section": "Interface Details"}
                ],
                "removed_controls": [],
            },
            {
                "type": "attributes",
                "attribute": "aria-disabled",
                "old_value": "true",
                "new_value": "false",
                "target": {"label": "Folder"},
            },
        ],
    }
    summary = summarize_dom_transition_window(window)
    assert summary["commit_events_seen"] is True
    assert summary["added_control_count"] == 1
    assert summary["added_controls"][0]["label"] == "Account"
    assert summary["state_change_count"] == 1
    assert summary["state_changes"][0]["attribute"] == "aria-disabled"


def test_secret_fields_are_redacted_in_browser_observer_source():
    assert "type === 'password'" in DOM_EVENT_OBSERVER_SCRIPT
    assert "client[_ -]?secret" in DOM_EVENT_OBSERVER_SCRIPT
    assert "kind:'redacted'" in DOM_EVENT_OBSERVER_SCRIPT
