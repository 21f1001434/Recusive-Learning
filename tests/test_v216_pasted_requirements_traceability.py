from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from hip_id_agent.config import AutoWebGLMConfig, SemanticUnderstandingConfig
from hip_id_agent.live_readiness import build_live_readiness_report
from hip_id_agent.live_witness import build_live_witness_report
from hip_id_agent.semantic_affordance import selector_looks_generation_volatile
from hip_id_agent.semantic_control import _contains_semantic, current_generation_row_binding, verify_semantic_effect


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        autowebglm=SimpleNamespace(enabled=True, primary_framework=True),
        mcp=SimpleNamespace(use_playwright_mcp=True, playwright_mcp_primary_for_safe_actions=True),
        semantic_understanding=SimpleNamespace(
            enabled=True,
            fail_closed=True,
            revalidate_before_dispatch=True,
            require_post_action_effect=True,
            use_hip_intelligence_mcp_consensus=True,
            require_playwright_mcp_evidence=True,
            require_devtools_evidence=True,
            require_hip_intelligence_mcp_evidence=True,
        ),
        browser_use=SimpleNamespace(enabled=True),
    )


def _all_tools_probe() -> dict:
    return {
        "playwright_mcp": {
            "available": True,
            "required_tool_status": {
                "browser_navigate": True,
                "browser_snapshot": True,
                "browser_find": True,
                "browser_click": True,
                "browser_type": True,
                "browser_fill_form": True,
                "browser_select_option": True,
                "browser_take_screenshot": True,
            },
        },
        "chrome_devtools_mcp": {
            "available": True,
            "required_tool_status": {
                "take_snapshot": True,
                "list_network_requests": True,
                "list_console_messages": True,
            },
        },
        "hip_intelligence_mcp": {
            "available": True,
            "required_tool_status": {
                "build_web_representation": True,
                "plan_form_action": True,
                "resolve_semantic_control": True,
                "rank_semantic_candidates": True,
                "verify_semantic_action_effect": True,
                "get_semantic_control_fingerprint": True,
                "get_semantic_control_capabilities": True,
                    "hip_get_current_surface": True, "hip_get_form_schema": True,
                    "hip_find_control": True, "hip_find_owned_popup": True,
                    "hip_get_repeatable_rows": True, "hip_get_required_fields": True,
                    "hip_get_current_values": True, "hip_compare_expected_actual": True,
                    "hip_get_safe_actions": True, "hip_verify_action_effect": True,
                    "hip_get_route_identity": True, "hip_get_form_generation": True,
            },
        },
    }


def _readiness(mcp_probe: dict) -> dict:
    return build_live_readiness_report(
        fingerprint="traceability",
        static_preflight={"pass": True, "skill_vetting": {"pass": True}, "autogen": {"pass": True}},
        browser_probe={"pass": True, "selected": "edge"},
        mcp_probe=mcp_probe,
        text_probe={"pass": True},
        vision_probe={"pass": True},
        path_probe={"pass": True},
        process_state={"running": False},
        config=_cfg(),
    )


def _assert_setter_fail_closed(module, name: str = "_set_control_value") -> None:
    src = inspect.getsource(getattr(module, name))
    guard = src.find("if semantic_runtime_enabled(page):\n        return False")
    assert guard >= 0, src
    raw_positions = [p for p in (src.find("await loc.fill"), src.find("page.evaluate")) if p >= 0]
    assert raw_positions and guard < min(raw_positions), src


def test_01_specific_control_section_only_rejected():
    assert _contains_semantic("Configure Routing", "Mapping Identifier", "Configure Routing") is False


def test_02_specific_control_label_accepted():
    assert _contains_semantic("Mapping Identifier Name (Version)", "Mapping Identifier", "Configure Routing") is True


def test_03_datamap_setter_fail_closed_guard():
    import hip_id_agent.datamap_kb as m
    _assert_setter_fail_closed(m)


def test_04_doctype_setter_fail_closed_guard():
    import hip_id_agent.doctype_kb as m
    _assert_setter_fail_closed(m)


def test_05_rules_setter_fail_closed_guard():
    import hip_id_agent.rules_kb as m
    _assert_setter_fail_closed(m)


def test_06_transport_profile_setter_fail_closed_guard():
    import hip_id_agent.transport_profile_kb as m
    _assert_setter_fail_closed(m)


def test_07_transport_profile_combobox_semantic_guard_precedes_raw_pointer_path():
    import hip_id_agent.transport_profile_kb as m
    src = inspect.getsource(m._select_transport_profile_combobox_option)
    guard = src.index("if semantic_runtime_enabled(page):\n        return False")
    raw = src.index("await loc.click")
    assert guard < raw


def test_08_live_witness_consumes_independent_dom_click_telemetry():
    import hip_id_agent.live_witness as m
    src = inspect.getsource(m.build_live_witness_report)
    assert "click_events" in src
    assert "observed_dom_click_count" in src
    assert "dom_mutation_control_click_count" in src


def test_09_blocked_dom_mutation_attempt_is_witness_violation():
    report = build_live_witness_report(
        action_events=[],
        network_events=[],
        click_events=[{
            "source": "dom",
            "text": "Save",
            "selector": "button#save",
            "extra": {
                "safety_action_text": "save",
                "safety_blocked": True,
                "safety_authorized": False,
                "safety_structural_opener": False,
                "safety_authorization_task_id": "task-1",
            },
        }],
        mission_complete=True,
        phase_count=1,
    )
    assert report["pass"] is False
    assert report["decision"] == "WITNESS_FAIL"
    assert report["dom_mutation_control_click_count"] == 1
    assert report["dom_mutation_control_clicks"][0]["reason"] == "blocked_mutation_attempt"


def test_10_page_click_guard_synchronizes_authorization_and_structural_provenance():
    from hip_id_agent.browser_session import BrowserSession
    src = inspect.getsource(BrowserSession._sync_page_click_safety_context)
    assert "__HIP_MUTATION_AUTH" in src
    assert "__HIP_STRUCTURAL_OPENER" in src
    assert "action_label" in src
    browser_src = inspect.getsource(__import__("hip_id_agent.browser_session", fromlist=["*"]))
    assert "safety_authorization_task_id" in browser_src
    assert "safety_action_text" in browser_src


def test_11_repeatable_current_generation_binding_changes_after_rerender():
    c = {"row_kind": "condition", "row_index": 1, "label": "Attribute", "section": "Conditions"}
    assert current_generation_row_binding(c, 10) != current_generation_row_binding(c, 11)


def test_12_repeatable_current_generation_binding_is_deterministic_within_generation():
    c = {"row_kind": "condition", "row_index": 1, "label": "Attribute", "section": "Conditions"}
    assert current_generation_row_binding(c, 10) == current_generation_row_binding(dict(c), 10)


def test_13_ambiguous_repeatable_control_after_rerender_fails_closed():
    import hip_id_agent.semantic_control as m
    src = inspect.getsource(m.SemanticActionGate.revalidate)
    assert "current_generation == before_generation" in src
    assert "ambiguous_after_rerender" in src
    assert '"pass": unique' in src


def test_14_nth_child_is_generation_volatile():
    assert selector_looks_generation_volatile("form > div:nth-child(2) input") is True


def test_15_nth_of_type_is_generation_volatile():
    assert selector_looks_generation_volatile("dds-row:nth-of-type(3) input") is True


def test_16_sticky_restore_skips_generation_volatile_selectors_under_semantic_runtime():
    import hip_id_agent.dds_control_driver as m
    src = inspect.getsource(m.restore_filled_values)
    assert "semantic_runtime_enabled(page) and selector_looks_generation_volatile(selector)" in src
    assert "generation_volatile_selector" in src


def test_17_readonly_dropdown_discovery_does_not_raw_click_after_mcp_failure(monkeypatch):
    import hip_id_agent.dds_control_driver as m

    class Loc:
        def __init__(self): self.raw_clicks = 0
        async def scroll_into_view_if_needed(self, **kwargs): pass
        async def click(self, **kwargs): self.raw_clicks += 1

    class Page:
        def __init__(self):
            self.loc = Loc()
            self._hip_playwright_mcp_backend = SimpleNamespace(click=self._fail)
        async def _fail(self, *args, **kwargs): raise RuntimeError("MCP execution failed")
        def locator(self, selector): return SimpleNamespace(first=self.loc)
        async def wait_for_timeout(self, ms): pass

    page = Page()

    async def prep(page, **kwargs): return ({"semantic_control_id": "SC-1"}, page.loc, kwargs["selector"], {"pass": True})
    async def gate(page, **kwargs): return {"status": "aligned"}
    async def commit(page, **kwargs): raise AssertionError("commit must not run after failed MCP click")

    monkeypatch.setattr(m, "_semantic_prepare", prep)
    monkeypatch.setattr(m, "_autowebglm_primary_gate", gate)
    monkeypatch.setattr(m, "_semantic_commit", commit)
    monkeypatch.setattr(m, "semantic_runtime_enabled", lambda page: True)
    monkeypatch.setattr(m, "_ensure_page_interactable", lambda *args, **kwargs: asyncio.sleep(0))

    ok = asyncio.run(m.open_control_for_discovery(page, "#dropdown", label="Interface Type", phase="transport_profile"))
    assert ok is False
    assert page.loc.raw_clicks == 0


def test_18_readonly_dropdown_discovery_mcp_success_runs_post_action_effect_verification(monkeypatch):
    import hip_id_agent.dds_control_driver as m

    class Loc:
        async def scroll_into_view_if_needed(self, **kwargs): raise AssertionError("raw fallback must not be used")
        async def click(self, **kwargs): raise AssertionError("raw fallback must not be used")

    class Backend:
        def __init__(self): self.clicks = 0
        async def click(self, selector, element=""): self.clicks += 1

    class Page:
        def __init__(self):
            self.loc = Loc()
            self._hip_playwright_mcp_backend = Backend()
        def locator(self, selector): return SimpleNamespace(first=self.loc)
        async def wait_for_timeout(self, ms): pass

    page = Page()
    committed = {"count": 0}

    async def prep(page, **kwargs): return ({"semantic_control_id": "SC-1"}, page.loc, kwargs["selector"], {"pass": True})
    async def gate(page, **kwargs): return {"status": "aligned"}
    async def commit(page, **kwargs):
        committed["count"] += 1
        return {"pass": True, "effect_type": "visible_listbox_count_changed"}

    monkeypatch.setattr(m, "_semantic_prepare", prep)
    monkeypatch.setattr(m, "_autowebglm_primary_gate", gate)
    monkeypatch.setattr(m, "_semantic_commit", commit)
    monkeypatch.setattr(m, "semantic_runtime_enabled", lambda page: True)
    monkeypatch.setattr(m, "_ensure_page_interactable", lambda *args, **kwargs: asyncio.sleep(0))

    ok = asyncio.run(m.open_control_for_discovery(page, "#dropdown", label="Interface Type", phase="transport_profile"))
    assert ok is True
    assert page._hip_playwright_mcp_backend.clicks == 1
    assert committed["count"] == 1


def test_19_hip_intelligence_mcp_is_required_by_default():
    cfg = SemanticUnderstandingConfig()
    assert cfg.require_hip_intelligence_mcp_evidence is True
    assert cfg.use_hip_intelligence_mcp_consensus is True


def test_20_live_go_no_go_rejects_missing_playwright_tool_inventory():
    probe = _all_tools_probe()
    probe["playwright_mcp"] = {"available": True}
    report = _readiness(probe)
    assert report["pass"] is False
    assert any(x["id"] == "playwright_mcp_live" for x in report["blockers"])


def test_21_live_go_no_go_rejects_missing_devtools_tool_inventory():
    probe = _all_tools_probe()
    probe["chrome_devtools_mcp"] = {"available": True}
    report = _readiness(probe)
    assert report["pass"] is False
    assert any(x["id"] == "chrome_devtools_mcp_live" for x in report["blockers"])


def test_22_live_go_no_go_rejects_missing_hip_intelligence_tool_inventory():
    probe = _all_tools_probe()
    probe["hip_intelligence_mcp"] = {"available": True, "required_tool_status": {}}
    report = _readiness(probe)
    assert report["pass"] is False
    assert any(x["id"] == "hip_intelligence_mcp_live" for x in report["blockers"])


def test_23_dom_generation_increment_alone_does_not_prove_click_effect():
    result = verify_semantic_effect(
        before={"dom_generation": 10, "url_path": "/a"},
        after={"dom_generation": 11, "url_path": "/a"},
        action="click",
    )
    assert result["pass"] is False
    assert "dom_generation_advanced_observation" in result["evidence"]


def test_24_autowebglm_protocol_preserves_real_press_key_intent():
    assert "press_key" in AutoWebGLMConfig().allowed_actions


def test_25_add_recovery_and_bizflow_structural_actions_are_semantically_governed():
    import hip_id_agent.bizflow_kb as biz
    import hip_id_agent.doctype_kb as dt
    import hip_id_agent.rules_kb as rules
    import hip_id_agent.transport_profile_kb as tp

    for fn in (
        dt._click_add_doctype_with_overlay_recovery,
        rules._click_add_rule_with_overlay_recovery,
        tp._click_add_transport_profile_with_overlay_recovery,
    ):
        src = inspect.getsource(fn)
        assert "semantic_overlay_recovery" in src
        assert "el.click" not in src
        assert "force=True" not in src

    nested = inspect.getsource(biz._click_bizflow_section_add)
    launcher = inspect.getsource(biz._click_bizflow_template_link_after_add)
    accordion = inspect.getsource(biz._expand_bizflow_process_step_row)
    assert "_governed_bizflow_click" in nested
    assert "force=True" not in nested and "el.click()" not in nested
    assert "structural_opener=True" in launcher
    assert "_governed_bizflow_click" in accordion
