from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.config import load_config
from hip_id_agent.live_readiness import build_live_readiness_report
from hip_id_agent.semantic_control import (
    SemanticControlMemory,
    control_fingerprint,
    rank_semantic_candidates,
    semantic_control_id,
    semantic_control_mcp_status,
    verify_semantic_effect,
)


def _candidate(**overrides):
    row = {
        "label": "SSL Certificate",
        "aria_label": "SSL Certificate",
        "role": "combobox",
        "tag": "input",
        "type": "text",
        "section": "Receiver Details",
        "framework_key": "sslCertificate",
        "name": "sslCertificate",
        "testid": "",
        "row_kind": "",
        "selection_mode": "single",
        "aria_haspopup": "listbox",
        "visible": True,
        "enabled": True,
        "selector": '[formcontrolname="sslCertificate"]',
        "selector_generation_volatile": False,
        "anchor_match": True,
    }
    row.update(overrides)
    return row


def test_semantic_control_id_is_value_free_and_stable():
    a = _candidate(value="SECRET_A", has_value=True)
    b = _candidate(value="SECRET_B", has_value=False)
    assert control_fingerprint(a) == control_fingerprint(b)
    assert semantic_control_id(a) == semantic_control_id(b)
    assert semantic_control_id(a).startswith("SC-")


def test_ranker_prefers_vetted_anchor_with_cross_source_evidence(tmp_path: Path):
    mem = SemanticControlMemory(tmp_path / "memory")
    exact = _candidate()
    wrong = _candidate(label="Certificate Type", aria_label="Certificate Type", anchor_match=False, selector='[name="certType"]', framework_key="certType", name="certType")
    ranked = rank_semantic_candidates(
        candidates=[wrong, exact],
        action="select",
        expected_label="SSL Certificate",
        expected_section="Receiver Details",
        accessibility_text="Receiver Details SSL Certificate combobox",
        devtools_text="Receiver Details SSL Certificate formcontrolname sslCertificate",
        memory=mem,
        phase="target_transport_profile",
    )
    assert ranked[0]["candidate"]["anchor_match"] is True
    assert ranked[0]["score"] > ranked[1]["score"]
    assert "playwright_mcp_accessibility_confirmed" in ranked[0]["reasons"]
    assert "devtools_dom_confirmed" in ranked[0]["reasons"]


def test_duplicate_same_label_is_penalized_when_not_vetted_anchor():
    anchored = _candidate()
    duplicate = _candidate(anchor_match=False, selector='div:nth-of-type(9) input', framework_key="", name="")
    ranked = rank_semantic_candidates(
        candidates=[duplicate, anchored], action="select", expected_label="SSL Certificate", expected_section="Receiver Details"
    )
    assert ranked[0]["candidate"]["anchor_match"] is True
    duplicate_row = next(r for r in ranked if not r["candidate"]["anchor_match"])
    assert "duplicate_semantic_label_penalty" in duplicate_row["reasons"]


def test_exact_fill_and_upload_commit_are_valid_semantic_effects():
    base = {"url_path": "/transport-profiles", "dom_generation": 4, "control_count": 8, "visible_dialog_count": 1, "visible_listbox_count": 0, "visible_drawer_count": 1}
    after = dict(base)
    fill = verify_semantic_effect(before=base, after=after, action="fill", target_before={"has_value": False}, target_after={"has_value": True}, exact_value_verified=True)
    upload = verify_semantic_effect(before=base, after=after, action="upload", target_before={"has_value": False}, target_after={"has_value": True}, exact_value_verified=True)
    assert fill["pass"] is True and fill["confidence"] == 1.0
    assert upload["pass"] is True and upload["confidence"] == 1.0


def test_structural_click_effect_requires_real_state_change():
    before = {"url_path": "/rules", "dom_generation": 10, "control_count": 6, "visible_dialog_count": 1, "visible_listbox_count": 0, "visible_drawer_count": 0}
    no_change = verify_semantic_effect(before=before, after=dict(before), action="click", target_before={"expanded": "false"}, target_after={"expanded": "false"})
    changed = verify_semantic_effect(before=before, after={**before, "dom_generation": 11, "control_count": 9}, action="click", target_before={"expanded": "false"}, target_after={"expanded": "true"})
    assert no_change["pass"] is False
    assert changed["pass"] is True


def test_hip_intelligence_semantic_tool_contract_is_complete():
    status = semantic_control_mcp_status()
    tools = set(status["tools"])
    assert {
        "resolve_semantic_control",
        "rank_semantic_candidates",
        "verify_semantic_action_effect",
        "get_semantic_control_fingerprint",
        "get_semantic_control_capabilities",
    } <= tools
    assert status["values_stored"] is False


def _readiness_cfg():
    return SimpleNamespace(
        autowebglm=SimpleNamespace(enabled=True, primary_framework=True),
        mcp=SimpleNamespace(use_playwright_mcp=True, playwright_mcp_primary_for_safe_actions=True),
        semantic_understanding=SimpleNamespace(
            enabled=True,
            fail_closed=True,
            revalidate_before_dispatch=True,
            require_post_action_effect=True,
            use_hip_intelligence_mcp_consensus=True,
        ),
        browser_use=SimpleNamespace(enabled=True),
    )


def test_live_go_no_go_requires_layer11_hip_intelligence_tools():
    report = build_live_readiness_report(
        fingerprint="layer11",
        static_preflight={"pass": True, "skill_vetting": {"pass": True}, "autogen": {"pass": True}},
        browser_probe={"pass": True},
        mcp_probe={
            "playwright_mcp": {"available": True},
            "chrome_devtools_mcp": {"available": True},
            "hip_intelligence_mcp": {"available": False},
        },
        text_probe={"pass": True}, vision_probe={"pass": True}, path_probe={"pass": True}, process_state={"running": False}, config=_readiness_cfg(),
    )
    assert report["pass"] is False
    assert any(x["id"] == "hip_intelligence_mcp_live" for x in report["blockers"])


def test_shipped_configs_enable_strict_layer11_semantics():
    for filename in ["config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"]:
        cfg = load_config(filename)
        sem = cfg.semantic_understanding
        assert sem.enabled is True
        assert sem.fail_closed is True
        assert sem.revalidate_before_dispatch is True
        assert sem.require_post_action_effect is True
        assert sem.require_playwright_mcp_evidence is True
        assert sem.require_devtools_evidence is True
        assert sem.use_hip_intelligence_mcp_consensus is True
        assert sem.require_hip_intelligence_mcp_evidence is True


def test_remaining_shared_execution_surfaces_are_semantic_gated():
    upload = Path("hip_id_agent/upload_assets.py").read_text(encoding="utf-8")
    dds = Path("hip_id_agent/dds_control_driver.py").read_text(encoding="utf-8")
    explore = Path("hip_id_agent/portal_form_exploration.py").read_text(encoding="utf-8")
    repeatable = Path("hip_id_agent/repeatable_rows.py").read_text(encoding="utf-8")
    assert '_semantic_action_preflight' in upload and 'action="upload"' in upload
    assert 'action="upload"' in dds and 'semantic_effect' in dds
    assert '_semantic_prepare(' in explore and 'action="select"' in explore
    assert '_semantic_repeatable_add(' in repeatable and 'session.click_and_wait' in repeatable


def test_trace_and_control_center_surface_semantic_execution_provenance():
    trace = Path("hip_id_agent/mission_trace.py").read_text(encoding="utf-8")
    ui = Path("webui/app.js").read_text(encoding="utf-8")
    assert "semantic_control_id" in trace
    assert "semantic_effect_confidence" in trace
    assert "semantic_control_id" in ui
    assert "semantic action gate ON" in ui
