"""V243R13: every HIP phase must complete when its object already exists in DEV.

The approved golden screenshots show the portal's natural-key duplicate message
on every create form (Map identifier / Name / Rule Name / Transport Profile
already exists) plus disabled portal-owned fields whose greyed text may be a
placeholder (Version, Rule Type, Rule Scope). These replicas drive the real
autonomous runtime + phase executors in headless Chromium, exactly as the phase
KB modules call them.
"""
from __future__ import annotations

import asyncio
import html
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.autonomous_form_runtime import autonomous_failure_summary, execute_autonomous_phase_goal
from hip_id_agent.dummy_fill_e2e import _classify_validation_messages
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph


def _chromium_path() -> str | None:
    for candidate in (os.environ.get("HIP_TEST_CHROMIUM"), "/opt/pw-browsers/chromium", "/usr/bin/chromium"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _field(f: Dict[str, Any]) -> str:
    fid = f["name"]
    attrs = f'id="{fid}" name="{fid}" formcontrolname="{fid}"'
    if f.get("disabled"):
        attrs += " disabled"
    if f.get("placeholder"):
        attrs += f' placeholder="{html.escape(f["placeholder"])}"'
    if f.get("value"):
        attrs += f' value="{html.escape(f["value"])}"'
    if f.get("kind") == "switch":
        control = f'<input type="checkbox" role="switch" {attrs} checked aria-checked="true"> <span>Enabled</span>'
    elif f.get("kind") == "textarea":
        control = f"<textarea {attrs}></textarea>"
    else:
        control = f'<input type="text" {attrs}>'
    err = f'<small id="{fid}-err" class="dds__invalid-feedback" style="display:none"></small>' if f.get("duplicate") else ""
    return f'<div class="dds__form-group" id="{fid}-group"><label for="{fid}">{html.escape(f["label"])}</label>{control}{err}</div>'


def _page(title: str, sections: List[Dict[str, Any]], extra_text: str = "") -> str:
    body = "".join(
        f'<fieldset><legend>{html.escape(s["legend"])}</legend>{"".join(_field(f) for f in s["fields"])}</fieldset>'
        for s in sections
    )
    dups = [(f["name"], f["duplicate"]) for s in sections for f in s["fields"] if f.get("duplicate")]
    script = "".join(
        f"""
        (function(){{const el=document.getElementById('{fid}');let t=null;el.addEventListener('input',()=>{{clearTimeout(t);t=setTimeout(()=>{{
          const dup=el.value.trim().length>0; const err=document.getElementById('{fid}-err');
          el.setAttribute('aria-invalid',dup?'true':'false'); err.textContent=dup?{msg!r}:''; err.style.display=dup?'block':'none';
          document.getElementById('{fid}-group').classList.toggle('dds__form-group--error',dup);}},200);}});}})();"""
        for fid, msg in dups
    )
    return (
        f'<!doctype html><html><body><div class="dds__drawer" role="dialog"><h2>{html.escape(title)}</h2>'
        f'<p>{html.escape(extra_text)}</p><form>{body}<button type="button">Cancel</button>'
        f'<button type="button">Submit</button></form></div><script>{script}</script></body></html>'
    )


PHASES = {
    "source_document_type": dict(
        input={"name": "XML_DellAutoASN_10_U-HAUL_ANS_IB", "transaction_type": "856", "version": "1", "status": "Enable",
               "description": "XML_DellAutoASN_10_U-HAUL_ANS_IB"},
        html=_page("Create Document Type", [{"legend": "Document Type Details", "fields": [
            {"label": "Name *", "name": "name", "duplicate": "Name already exists"},
            {"label": "Transaction Type", "name": "transactionType"},
            {"label": "Version", "name": "version", "disabled": True, "placeholder": "1"},
            {"label": "Status", "name": "status", "kind": "switch"},
            {"label": "Description", "name": "description", "kind": "textarea"},
        ]}]),
        executor=execute_document_type_state_graph,
        validation="Name already exists",
    ),
    "rule": dict(
        input={"name": "DELLCoXMLASNXX08C_U-HAUL_RULE", "version": "1", "rule_type": "Mapping", "rule_scope": "GLOBAL",
               "status": "Enable", "description": "DELLCoXMLASNXX08C_U-HAUL_RULE"},
        html=_page("Create Rule", [{"legend": "Rule :", "fields": [
            {"label": "Name *", "name": "ruleName", "duplicate": "Rule Name already exists"},
            {"label": "Version", "name": "version", "disabled": True, "placeholder": "1"},
            {"label": "Rule Type", "name": "ruleType", "disabled": True, "placeholder": "Mapping"},
            {"label": "Rule Scope", "name": "ruleScope", "disabled": True, "placeholder": "GLOBAL"},
            {"label": "Status", "name": "status", "kind": "switch"},
            {"label": "Description", "name": "description", "kind": "textarea"},
        ]}]),
        executor=None,
        validation="Rule Name already exists",
    ),
    "source_transport_profile": dict(
        input={"profile_name": "SFTP_U-HAUL_ASN_PC_SRC_IB"},
        html=_page("Create Transport Profile", [{"legend": "Basic Details :", "fields": [
            {"label": "Profile Name *", "name": "profileName",
             "duplicate": "Transport Profile already exists in DEV environment."},
        ]}], extra_text="Interface Type Existing Account Document Type Supported"),
        executor=None,
        validation="Transport Profile already exists in DEV environment.",
    ),
    "biz_flow": dict(
        input={"flow_details": {"business_flow_name": "U-HAUL_PC_856_ANS_MAPPING_OB", "flow_description": "Outbound 856 Ship Notice"}},
        html=_page("Create Biz Flow", [{"legend": "Flow Details", "fields": [
            {"label": "Business Flow Name *", "name": "businessFlowName", "duplicate": "Business Flow Name already exists"},
            {"label": "Flow Description *", "name": "flowDescription", "kind": "textarea"},
        ]}]),
        executor=None,
        validation="Business Flow Name already exists",
    ),
}


def _run(phase: str, tmp_path: Path) -> Dict[str, Any]:
    spec = PHASES[phase]
    payload = {"objects": {phase: spec["input"]}}

    async def run() -> Dict[str, Any]:
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=_chromium_path())
            except Exception as exc:  # pragma: no cover - environment without Chromium
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.set_content(spec["html"])
            try:
                kwargs = {"executor": spec["executor"]} if spec["executor"] else {}
                return await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(payload, phase), phase=phase,
                    input_data=payload, config=None, output_dir=tmp_path, max_cycles=2, **kwargs,
                )
            finally:
                await browser.close()
    return asyncio.run(run())


@pytest.mark.parametrize("phase", list(PHASES))
def test_phase_goal_is_proven_when_object_already_exists(phase: str, tmp_path: Path):
    result = _run(phase, tmp_path)
    assert result["pass"] is True, autonomous_failure_summary(result)
    assert [c["status"] for c in result["cycles"]] == ["goal_achieved"]


@pytest.mark.parametrize("phase", list(PHASES))
def test_validation_gate_accepts_golden_duplicate_message(phase: str):
    gate = _classify_validation_messages(phase, [{"message": PHASES[phase]["validation"]}], {})
    assert not gate["blocking"], gate
    assert gate["accepted_nonblocking"][0]["classification"] == "existing_object_reported_by_portal"


def test_row_level_duplicate_and_conflicts_stay_blocking():
    row_dup = _classify_validation_messages("source_document_type", [{"message": "Attribute Name already exists"}], {})
    assert row_dup["blocking"]
    conflict = _classify_validation_messages(
        "source_transport_profile", [{"message": "Transport Profile already exists in DEV environment."}],
        {"existing_object_resolution": {"mode": "conflicting_existing_object"}},
    )
    assert conflict["blocking"]
    other = _classify_validation_messages("rule", [{"message": "Value is invalid"}], {})
    assert other["blocking"]
