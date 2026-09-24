from pathlib import Path

from hip_id_agent.rules_kb import (
    _is_rule_summary_endpoint,
    _should_reuse_network_rule_profile,
    extract_rule_deep_profiles_from_payload,
)


def test_rule_summary_payload_is_not_treated_as_deep_profile():
    payload = [
        {
            "ruleName": "SAMPLE_RULE",
            "latestDevVersion": 1.0,
            "availableEnvironments": ["DEV"],
            "documentTypeName": "SAMPLE_DOC",
        }
    ]
    profiles = extract_rule_deep_profiles_from_payload(
        payload,
        source_url="https://developer.dell.com/inaas-gateway/hipService-svc/api/rule/summary",
        source="network_deep_profile_reuse",
    )
    assert _is_rule_summary_endpoint("https://developer.dell.com/inaas-gateway/hipService-svc/api/rule/summary")
    assert profiles
    assert all(not _should_reuse_network_rule_profile(p) for p in profiles)


def test_rule_detail_payload_is_reused_for_deep_profile():
    payload = {
        "ruleDetail": {
            "ruleId": 123,
            "ruleName": "SAMPLE_RULE",
            "ruleVersion": 1,
            "sourceDocumentTypeName": "SRC_DOC",
            "targetDocumentTypeName": "TRG_DOC",
            "ruleConditions": [{"attributeName": "Receiver", "operator": "Equals", "value": "ABC"}],
            "ruleActions": [{"actionType": "Mapping Transformer", "mappingIdentifier": "MAP_1", "mappingVersion": 1}],
        }
    }
    profiles = extract_rule_deep_profiles_from_payload(
        payload,
        source_url="https://developer.dell.com/inaas-gateway/hipService-svc/api/rule/123/details",
        source="network_deep_profile_reuse",
    )
    assert profiles
    assert _should_reuse_network_rule_profile(profiles[0])


def test_rules_kb_javascript_uses_document_not_undefined_rule_global():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    assert "rule.querySelector" not in source
    assert "rule.body" not in source


def test_rules_row_learning_retries_after_listing_reset():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    assert "_reset_rules_listing_for_row_learning" in source
    assert "retry_after_listing_reset" in source
    assert "search_failed_after_reset" in source


def test_rules_row_learning_does_not_click_collapse_to_close_details():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    assert "already_expanded" in source
    assert "not click it (that would close the details panel" in source


def test_rules_row_matching_prefers_exact_rule_over_testey_prefix():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    assert "Avoid selecting TESTEY_<rule>" in source
    assert "cellLowers.some(c => c === h)" in source
    assert "lower.startsWith(h + ' ')" in source


def test_rules_row_action_does_not_click_global_navigation_after_expand():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    assert "direct_is_overflow" in source
    assert "not direct_is_overflow" in source
    assert '"navigation", "side-nav", "side nav", "breadcrumb"' in source


def test_rules_row_learning_has_final_unresolved_rescue_pass():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    assert "final_unresolved_rescue_start" in source
    assert "final_rescue_rows_attempted" in source
    assert "rule_ui_row_action_detail_final_rescue" in source


def test_rules_expand_click_falls_through_to_inline_details_action():
    source = Path("hip_id_agent/rules_kb.py").read_text(encoding="utf-8")
    assert "direct_is_expander" in source
    assert "not direct_is_overflow and not direct_is_expander" in source
    assert "Expand clicks must fall through" in source
