from hip_id_agent.section_judge import (
    DualModelSectionJudge,
    SectionJudgePolicy,
    build_bizflow_section_expectation,
)


def test_deterministic_section_judge_fails_when_required_value_missing():
    judge = DualModelSectionJudge(SectionJudgePolicy(enabled=True, require_text_model=False, require_vision_model=False))
    expected = {"section": "Configure Routing", "facts": [{"field": "target", "value": "SFTP_TARGET", "aliases": ["target"], "required": True}], "row_counts": {}}
    actual = {"controls": [{"label": "Target", "value": "Select"}], "visible_text": "Target Select", "row_counts": {}}
    result = judge.deterministic_judge(expected=expected, actual_state=actual, attempts=[])
    assert result["pass"] is False
    assert result["missing_values"][0]["field"] == "target"


def test_deterministic_section_judge_fails_on_failed_attempt_even_when_value_visible():
    judge = DualModelSectionJudge(SectionJudgePolicy(enabled=True, require_text_model=False, require_vision_model=False))
    expected = {"section": "Target Details", "facts": [{"field": "action", "value": "Mapping", "aliases": ["action"], "required": True}], "row_counts": {}}
    actual = {"controls": [{"label": "Action", "value": "Mapping"}], "visible_text": "Action Mapping", "row_counts": {}}
    result = judge.deterministic_judge(expected=expected, actual_state=actual, attempts=[{"label": "Rule", "success": False, "reason": "row-specific control not visible"}])
    assert result["pass"] is False
    assert result["failed_attempts"]


def test_bizflow_target_expectation_contains_dependent_process_fields():
    data = {"objects": {"biz_flow": {"configure_targets": {"target_type": "Partner", "target_application": "dce-test-partner", "target_transport_profile": "SFTP_TGT", "document_type_name_version": "TGT_DOC(1.0)"}, "process_steps": [{"step_type": "Mapping Transformer", "step_name": "Mapping-1", "configuration": {"action": "Mapping", "target_document_type_version": "TGT_DOC(1.0)", "rule_version": "RULE(1.0)"}}]}}}
    expected = build_bizflow_section_expectation(data, "Target Details")
    values = {f["value"] for f in expected["facts"]}
    assert {"Mapping Transformer", "Mapping-1", "Mapping", "TGT_DOC(1.0)", "RULE(1.0)"}.issubset(values)
    assert expected["row_counts"]["process_steps"] == 1


def test_bizflow_capture_is_fail_closed_by_section_judge():
    import inspect
    import hip_id_agent.bizflow_kb as kb
    source = inspect.getsource(kb.capture_and_fill_bizflow_multitab_form)
    assert "judge_with_repairs" in source
    assert "blocked_at_section" in source
    assert "blocked_by_section_judge" in source
    assert "_click_bizflow_continue" in source
