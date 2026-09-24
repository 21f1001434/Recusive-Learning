import inspect

from hip_id_agent.config import AutoWebGLMConfig, SemanticUnderstandingConfig
from hip_id_agent.semantic_affordance import selector_looks_generation_volatile
from hip_id_agent.semantic_control import (
    _contains_semantic,
    current_generation_row_binding,
    verify_semantic_effect,
)


def test_nth_child_selector_is_generation_volatile():
    assert selector_looks_generation_volatile("form > div:nth-child(2) input") is True


def test_nth_of_type_selector_is_generation_volatile():
    assert selector_looks_generation_volatile("dds-row:nth-of-type(3) input") is True


def test_stable_id_selector_is_not_generation_volatile():
    assert selector_looks_generation_volatile("#businessFlowName") is False


def test_current_generation_row_binding_is_generation_scoped():
    c = {"row_kind": "condition", "row_index": 1, "label": "Attribute", "section": "Conditions"}
    assert current_generation_row_binding(c, 10) != current_generation_row_binding(c, 11)


def test_current_generation_row_binding_is_deterministic_within_generation():
    c = {"row_kind": "condition", "row_index": 1, "label": "Attribute", "section": "Conditions"}
    assert current_generation_row_binding(c, 10) == current_generation_row_binding(dict(c), 10)


def test_section_text_alone_cannot_prove_specific_control():
    assert _contains_semantic("Configure Routing", "Mapping Identifier", "Configure Routing") is False


def test_specific_control_label_can_prove_control():
    assert _contains_semantic("Mapping Identifier Name (Version)", "Mapping Identifier", "Actions") is True


def test_dom_generation_increment_alone_does_not_prove_click_effect():
    result = verify_semantic_effect(
        before={"dom_generation": 10, "url": "https://example.test/a"},
        after={"dom_generation": 11, "url": "https://example.test/a"},
        action="click",
    )
    assert result["pass"] is False


def test_hip_intelligence_mcp_evidence_is_required_by_default():
    assert SemanticUnderstandingConfig().require_hip_intelligence_mcp_evidence is True


def test_autowebglm_protocol_allows_press_key():
    assert "press_key" in AutoWebGLMConfig().allowed_actions


def test_live_readiness_requires_real_mcp_tool_inventory():
    import hip_id_agent.live_readiness as m
    src = inspect.getsource(m.build_live_readiness_report)
    assert "bool(pw_status)" in src and "bool(cdp_status)" in src
    assert "require_hip_intelligence_mcp_evidence" in src


def test_live_witness_consumes_independent_dom_click_telemetry():
    import hip_id_agent.live_witness as m
    src = inspect.getsource(m.build_live_witness_report)
    assert "safety_blocked" in src
    assert "dom_mutation_control_click_count" in src


def test_page_click_guard_synchronizes_authorization_and_structural_openers():
    import hip_id_agent.browser_session as m
    src = inspect.getsource(m.BrowserSession._sync_page_click_safety_context)
    assert "__HIP_MUTATION_AUTH" in src
    assert "__HIP_STRUCTURAL_OPENER" in src


def test_transport_profile_combobox_fails_closed_before_legacy_pointer_fallback():
    import hip_id_agent.transport_profile_kb as m
    src = inspect.getsource(m._select_transport_profile_combobox_option)
    guard = src.index("if semantic_runtime_enabled(page):\n        return False")
    raw = src.index("await loc.click")
    assert guard < raw


def test_add_form_overlay_recovery_has_no_raw_dom_click_bypass():
    import hip_id_agent.doctype_kb as dt
    import hip_id_agent.rules_kb as rules
    import hip_id_agent.transport_profile_kb as tp
    for fn in (
        dt._click_add_doctype_with_overlay_recovery,
        rules._click_add_rule_with_overlay_recovery,
        tp._click_add_transport_profile_with_overlay_recovery,
    ):
        src = inspect.getsource(fn)
        assert ".evaluate(\"el => el.click" not in src
        assert ".evaluate(" not in src or "el.click" not in src
        assert "semantic_overlay_recovery" in src


def test_bizflow_nested_add_and_template_launcher_are_semantically_governed():
    import hip_id_agent.bizflow_kb as m
    nested = inspect.getsource(m._click_bizflow_section_add)
    template = inspect.getsource(m._click_bizflow_template_link_after_add)
    assert "_governed_bizflow_click" in nested
    assert "force=True" not in nested
    assert "el.click()" not in nested
    assert "structural_opener=True" in template
    assert "force=True" not in template
