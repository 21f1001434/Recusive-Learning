from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

import hip_id_agent
from hip_id_agent.config import MCPConfig, SemanticUnderstandingConfig
from hip_id_agent.hip_intelligence_mcp_server import TOOLS
from hip_id_agent.hip_semantic_tools import (
    HIP_SEMANTIC_TOOL_NAMES,
    classify_action_risk,
    hip_compare_expected_actual,
    hip_find_control,
    hip_find_owned_popup,
    hip_get_current_surface,
    hip_get_form_generation,
    hip_get_form_schema,
    hip_get_repeatable_rows,
    hip_get_required_fields,
    hip_get_route_identity,
    hip_get_safe_actions,
    hip_verify_action_effect,
)
from hip_id_agent.live_readiness import build_live_readiness_report
from hip_id_agent.mcp_stdio import MCPTool
from hip_id_agent.playwright_mcp import PlaywrightMCPBackend
from hip_id_agent.semantic_control import rank_semantic_candidates


REQUIRED_HIP_TOOLS = {
    "hip_get_current_surface", "hip_get_form_schema", "hip_find_control",
    "hip_find_owned_popup", "hip_get_repeatable_rows", "hip_get_required_fields",
    "hip_get_current_values", "hip_compare_expected_actual", "hip_get_safe_actions",
    "hip_verify_action_effect", "hip_get_route_identity", "hip_get_form_generation",
}


def _surface() -> dict:
    return {
        "url_path": "/transport-profiles/add",
        "dom_generation": 42,
        "state_fingerprint": "state-42",
        "visible_drawer_count": 1,
        "visible_listbox_count": 0,
        "controls": [
            {
                "label": "SSL Certificate", "aria_label": "SSL Certificate",
                "section": "Receiver Details", "role": "combobox", "required": True,
                "enabled": True, "visible": True, "name": "sslCertificate",
                "aria_controls": "mat-select-84-panel", "aria_haspopup": "listbox",
                "selector": "[name=sslCertificate]", "anchor_match": True,
            },
            {
                "label": "Host Name", "section": "Receiver Details", "role": "textbox",
                "required": True, "enabled": True, "visible": True, "name": "hostName",
                "selector": "[name=hostName]", "row_kind": "receiver", "row_index": 0,
            },
            {
                "label": "Port", "section": "Receiver Details", "role": "textbox",
                "required": True, "enabled": True, "visible": True, "name": "port",
                "selector": "[name=port]", "row_kind": "receiver", "row_index": 0,
            },
            {"label": "Save", "role": "button", "enabled": True, "visible": True},
            {"label": "Delete", "role": "button", "enabled": True, "visible": True},
        ],
    }


def _all_mcp_probe() -> dict:
    pw_names = [
        "browser_navigate", "browser_snapshot", "browser_find", "browser_click",
        "browser_type", "browser_fill_form", "browser_select_option", "browser_take_screenshot",
    ]
    hip_names = [
        "build_web_representation", "plan_form_action", "resolve_semantic_control",
        "rank_semantic_candidates", "verify_semantic_action_effect",
        "get_semantic_control_fingerprint", "get_semantic_control_capabilities",
        *sorted(REQUIRED_HIP_TOOLS),
    ]
    return {
        "playwright_mcp": {"available": True, "required_tool_status": {x: True for x in pw_names}},
        "chrome_devtools_mcp": {"available": True, "required_tool_status": {
            "take_snapshot": True, "list_network_requests": True, "list_console_messages": True,
        }},
        "hip_intelligence_mcp": {"available": True, "required_tool_status": {x: True for x in hip_names}},
    }


def _readiness(probe: dict) -> dict:
    cfg = SimpleNamespace(
        autowebglm=SimpleNamespace(enabled=True, primary_framework=True),
        mcp=SimpleNamespace(
            use_playwright_mcp=True, playwright_mcp_primary_for_safe_actions=True,
            playwright_mcp_find_first=True, playwright_mcp_fill_form_enabled=True,
        ),
        semantic_understanding=SimpleNamespace(
            enabled=True, fail_closed=True, revalidate_before_dispatch=True,
            require_post_action_effect=True, require_playwright_mcp_evidence=True,
            require_devtools_evidence=True, require_hip_intelligence_mcp_evidence=True,
            use_hip_intelligence_mcp_consensus=True, execute_confidence_threshold=0.90,
            reobserve_confidence_threshold=0.75, self_heal_confidence_threshold=0.55,
        ),
        browser_use=SimpleNamespace(enabled=True),
    )
    return build_live_readiness_report(
        fingerprint="v217", static_preflight={"pass": True, "skill_vetting": {"pass": True}, "autogen": {"pass": True}},
        browser_probe={"pass": True, "selected": "edge"}, mcp_probe=probe,
        text_probe={"pass": True}, vision_probe={"pass": True}, path_probe={"pass": True},
        process_state={"running": False}, config=cfg,
    )


def test_01_version_promoted_to_217_or_newer():
    from packaging.version import Version
    assert Version(hip_id_agent.__version__) >= Version("2.1.7")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "' in pyproject


def test_02_layer11_confidence_policy_matches_spec():
    cfg = SemanticUnderstandingConfig()
    assert cfg.execute_confidence_threshold == 0.90
    assert cfg.reobserve_confidence_threshold == 0.75
    assert cfg.self_heal_confidence_threshold == 0.55
    assert cfg.fail_closed is True
    assert cfg.use_vision_for_ambiguity is True


def test_03_hip_intelligence_mcp_required_by_default():
    cfg = MCPConfig()
    assert cfg.use_hip_intelligence_mcp is True
    assert cfg.hip_intelligence_mcp_required is True
    assert cfg.playwright_mcp_find_first is True
    assert cfg.playwright_mcp_fill_form_enabled is True


def test_04_shipped_configs_use_adaptive_normal_profile_and_pin_playwright_079():
    for filename in ["config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"]:
        doc = yaml.safe_load(Path(filename).read_text(encoding="utf-8"))
        expected_hip_required = filename == "config.mcp-required.windows.yaml"
        assert doc["mcp"]["hip_intelligence_mcp_required"] is expected_hip_required
        assert doc["mcp"]["playwright_mcp_args"] == ["@playwright/mcp@0.0.79"]
        assert doc["mcp"]["playwright_mcp_find_first"] is True
        assert doc["mcp"]["playwright_mcp_fill_form_enabled"] is True
    package = json.loads(Path("package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["@playwright/mcp"] == "0.0.79"
    assert "@playwright/mcp@0.0.79" in package["scripts"]["mcp:playwright"]


def test_05_hip_intelligence_mcp_exposes_all_requested_website_tools():
    published = {row["name"] for row in TOOLS}
    assert REQUIRED_HIP_TOOLS == set(HIP_SEMANTIC_TOOL_NAMES)
    assert REQUIRED_HIP_TOOLS <= published


def test_06_semantic_find_control_returns_unique_ssl_certificate_match():
    out = hip_find_control({
        "surface": _surface(), "field": "SSL Certificate", "section": "Receiver Details",
        "expected_type": "single_select",
        "accessibility_text": "Receiver Details SSL Certificate combobox",
        "devtools_text": "Receiver Details sslCertificate SSL Certificate",
    })
    assert out["status"] == "unique_match"
    assert out["confidence"] >= 0.90
    assert out["control"]["label"] == "SSL Certificate"
    assert out["control"]["role"] == "combobox"


def test_07_ranked_candidates_publish_multi_source_locator_scores():
    ranked = rank_semantic_candidates(
        candidates=[_surface()["controls"][0]], action="select",
        expected_label="SSL Certificate", expected_section="Receiver Details",
        accessibility_text="Receiver Details SSL Certificate combobox",
        devtools_text="Receiver Details SSL Certificate sslCertificate",
    )
    scores = ranked[0]["source_scores"]
    assert scores["playwright_accessibility"] == 1.0
    assert scores["devtools_dom"] == 1.0
    assert "hip_historical_memory" in scores
    assert "local_dom_semantics" in scores


def test_08_surface_form_schema_required_fields_rows_and_generation_are_exposed():
    surface = _surface()
    assert hip_get_current_surface({"surface": surface})["dom_generation"] == 42
    schema = hip_get_form_schema({"surface": surface})
    assert schema["required_count"] == 3
    required = hip_get_required_fields({"surface": surface})
    assert required["count"] == 3
    rows = hip_get_repeatable_rows({"surface": surface})
    assert rows["row_count"] == 1
    assert hip_get_form_generation({"surface": surface})["state_fingerprint"] == "state-42"


def test_09_owned_popup_resolution_uses_aria_provenance():
    out = hip_find_owned_popup({
        "control": _surface()["controls"][0],
        "popups": [{"id": "mat-select-84-panel", "role": "listbox", "visible": True}],
    })
    assert out["status"] == "unique_match"
    assert out["matches"][0]["role"] == "listbox"


def test_10_route_identity_recognizes_transport_profiles():
    out = hip_get_route_identity({"surface": _surface(), "headings": ["Transport Profiles", "Receiver Details"]})
    assert out["status"] == "unique_match"
    assert out["route_identity"] == "transport_profiles"


def test_11_negative_action_semantics_classify_safe_conditional_dangerous():
    assert classify_action_risk("Search")["class"] == "SAFE"
    assert classify_action_risk("Save")["class"] == "CONDITIONAL"
    assert classify_action_risk("Delete")["class"] == "DANGEROUS"
    assert classify_action_risk("Create Biz Flow", structural_opener=True)["class"] == "SAFE"
    actions = hip_get_safe_actions({"surface": _surface()})
    assert any(x["label"] == "Save" for x in actions["conditional"])
    assert any(x["label"] == "Delete" for x in actions["dangerous"])


def test_12_expected_actual_comparison_and_effect_verification_are_value_free_outputs():
    cmp = hip_compare_expected_actual({"expected": {"ssl": "cert-a"}, "actual": {"ssl": "cert-a"}})
    assert cmp["status"] == "match"
    assert cmp["raw_values_stored"] is False
    eff = hip_verify_action_effect({
        "before": {"dom_generation": 41}, "after": {"dom_generation": 42},
        "action": "fill", "target_after": {"has_value": True}, "exact_value_verified": True,
    })
    assert eff["pass"] is True
    assert eff["values_stored"] is False


class _FakeClient:
    def __init__(self, tools: dict[str, MCPTool], responses: dict[str, object] | None = None):
        self.tools = tools
        self.responses = responses or {}
        self.calls: list[tuple[str, dict]] = []
    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return self.responses.get(name, {"content": [{"type": "text", "text": "ok"}]})
    async def close(self):
        return None


def _fill_form_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "type": {"type": "string"},
                    "ref": {"type": "string"}, "value": {},
                }},
            }
        },
    }


def test_13_playwright_browser_find_returns_ephemeral_unique_ref(tmp_path: Path):
    tools = {
        "browser_find": MCPTool("browser_find", input_schema={"type": "object", "properties": {"text": {"type": "string"}}}),
    }
    client = _FakeClient(tools, {"browser_find": {"content": [{"type": "text", "text": "Receiver Details\n- combobox 'SSL Certificate' [ref=e178]"}]}})
    backend = PlaywrightMCPBackend(client, tmp_path)
    out = asyncio.run(backend.find_ref("SSL Certificate", expected_role="combobox", section="Receiver Details"))
    assert out["status"] == "unique_match"
    assert out["ref"] == "e178"
    assert out["browser_find_used"] is True


def test_14_playwright_browser_find_ref_fails_ambiguous(tmp_path: Path):
    tools = {"browser_find": MCPTool("browser_find", input_schema={"type": "object", "properties": {"text": {"type": "string"}}})}
    text = "- textbox 'Host Name' [ref=e1]\n- textbox 'Host Name' [ref=e2]"
    backend = PlaywrightMCPBackend(_FakeClient(tools, {"browser_find": {"content": [{"type": "text", "text": text}]}}), tmp_path)
    out = asyncio.run(backend.find_ref("Host Name", expected_role="textbox"))
    assert out["status"] == "ambiguous"
    assert out["ref"] == ""


def test_15_playwright_browser_fill_form_executes_structured_verified_field_set(tmp_path: Path):
    tools = {"browser_fill_form": MCPTool("browser_fill_form", input_schema=_fill_form_schema())}
    client = _FakeClient(tools)
    backend = PlaywrightMCPBackend(client, tmp_path)
    out = asyncio.run(backend.fill_form([
        {"ref": "e10", "name": "Host Name", "type": "textbox", "value": "example"},
        {"ref": "e11", "name": "Port", "type": "textbox", "value": "443"},
    ]))
    assert out["pass"] is True and out["field_count"] == 2
    name, args = client.calls[-1]
    assert name == "browser_fill_form"
    assert [x["ref"] for x in args["fields"]] == ["e10", "e11"]


def test_16_structured_fill_is_integrated_into_state_graph_runtime_and_fails_closed():
    import hip_id_agent.stateful_form_runtime as m
    src = inspect.getsource(m.execute_phase_state_graph)
    assert "set_text_controls_batch" in src
    assert "HIP_STRUCTURED_FORM_FILL_FAILED" in src
    assert "playwright_mcp_fill_form_enabled" in src
    assert '"browser_fill_form"' in src


def test_17_text_control_has_no_raw_setter_fallback_after_semantic_mcp_failure():
    import hip_id_agent.dds_control_driver as m
    src = inspect.getsource(m.set_text_control)
    strict = src.find("if semantic_runtime_enabled(page):")
    raw = src.find("await page.evaluate")
    assert strict >= 0 and raw >= 0 and strict < raw
    assert "find_ref" in src


def test_18_live_readiness_requires_browser_find():
    probe = _all_mcp_probe()
    probe["playwright_mcp"]["required_tool_status"]["browser_find"] = False
    report = _readiness(probe)
    assert report["pass"] is False
    assert any(x["id"] == "playwright_mcp_live" for x in report["blockers"])


def test_19_live_readiness_requires_browser_fill_form():
    probe = _all_mcp_probe()
    probe["playwright_mcp"]["required_tool_status"]["browser_fill_form"] = False
    report = _readiness(probe)
    assert report["pass"] is False
    assert any(x["id"] == "playwright_mcp_live" for x in report["blockers"])


def test_20_live_readiness_requires_every_hip_semantic_tool():
    probe = _all_mcp_probe()
    probe["hip_intelligence_mcp"]["required_tool_status"]["hip_find_control"] = False
    report = _readiness(probe)
    assert report["pass"] is False
    assert any(x["id"] == "hip_intelligence_mcp_live" for x in report["blockers"])


def test_21_live_readiness_passes_with_complete_semantic_control_mcp_contract():
    report = _readiness(_all_mcp_probe())
    assert report["pass"] is True
    assert report["decision"] == "GO"
    assert "PyAutoGUI MCP primary physical click/type/key" in report["execution_contract"]
    assert "Playwright MCP deterministic fallback" in report["execution_contract"]


def test_22_mutation_observer_tracks_dynamic_hip_structural_events():
    import hip_id_agent.browser_session as m
    src = inspect.getsource(m)
    assert "MutationObserver" in src
    assert "__HIP_DOM_MUTATION_SEQ" in src
    assert "structural_events" in src
    assert "route_changed" in src
    for token in ["dialog_", "drawer_", "listbox_", "row_", "spinner_"]:
        assert token in src


def test_23_website_state_graphs_cover_all_major_hip_modules():
    import hip_id_agent.stateful_form_runtime as m
    for name in [
        "compile_document_type_state_graph", "compile_data_map_state_graph",
        "compile_rule_state_graph", "compile_transport_profile_state_graph",
        "compile_bizflow_state_graph",
    ]:
        assert callable(getattr(m, name, None)), name


def test_24_vision_is_ambiguity_only_not_primary_click_executor():
    import hip_id_agent.semantic_control as m
    src = inspect.getsource(m.SemanticActionGate.resolve)
    assert "needs_more = confidence < self.execute_threshold or margin < self.ambiguity_margin" in src
    assert "Vision is a tie-breaker only" in src
    assert "do not propose a click" in src


def test_25_fused_gate_exposes_vision_and_hip_consensus_source_scores():
    import hip_id_agent.semantic_control as m
    src = inspect.getsource(m.SemanticActionGate.resolve)
    assert 'fused_source_scores["hip_intelligence_consensus"]' in src
    assert 'fused_source_scores["vision"]' in src
    assert '"target_confidence": round(confidence, 4)' in src
