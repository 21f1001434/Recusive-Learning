"""V243R33: WebMCP -- the tools a web page offers an agent help it complete the task.

Request (2026-09-30): "Let's integrate WebMCP in this to help the agent to
complete the task."

WebMCP (``navigator.modelContext``) lets a page register tools with a name,
description and JSON input schema -- imperatively (``registerTool`` /
``provideContext``) or declaratively (``<form toolname=…>``); Chrome's preview
exposes ``navigator.modelContextTesting`` to agents.  The agent now:

* adds a WebMCP layer to every page (native when the browser has it, a polyfill
  otherwise) plus its own in-page tools;
* classifies every tool (read-only / navigation / form edit / mutating) and
  refuses mutating ones without the three-part mutation gate;
* fills a form with the page's own form-edit tool when it covers input.json --
  then the live input.json proof decides; a wrong tool result is caught and the
  normal fill completes the form.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.webmcp import (
    WebMCPToolRefused, call_tool, catalog, classify_tool, install_on_context, list_tools, map_input_to_tool, webmcp_policy,
)

TP = "source_transport_profile"


def _need_browser():
    from phase_replica_support import chromium_path

    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")


async def _context(pw, *, native: bool = False):
    from phase_replica_support import chromium_path
    from webmcp_portal_support import FAKE_NATIVE_JS

    browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
    ctx = await browser.new_context(viewport={"width": 1280, "height": 720})
    if native:
        await ctx.add_init_script(FAKE_NATIVE_JS)
    await install_on_context(ctx)
    return browser, ctx


# ------------------------------------------------------------ real browser
def test_the_pages_webmcp_tool_fills_the_form_and_the_input_json_proof_completes_it(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
    from phase_replica_support import uhaul_input
    from webmcp_portal_support import tp_page

    data = uhaul_input(TP, tmp_path)

    async def run(bug: bool):
        async with async_playwright() as pw:
            browser, ctx = await _context(pw)
            page = await ctx.new_page()
            try:
                await page.goto(tp_page(tmp_path, bug=bug))
                await page.wait_for_timeout(300)
                started = time.monotonic()
                result = await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(data, TP), phase=TP, input_data=data, config=None,
                    output_dir=tmp_path / ("bug" if bug else "ok"), max_cycles=3)
                seconds = time.monotonic() - started
                state = await page.evaluate("""() => ({calls: window.__webmcpCalls.map(c => c.tool), saved: !!window.__saved,
                    name: document.querySelector('[formcontrolname=profileName]').value})""")
                return result, seconds, state
            finally:
                await browser.close()

    result, seconds, state = asyncio.run(run(False))
    # One call of the page's own tool filled the form; the live proof completed the phase.
    assert result["pass"] is True and result["completed_by"] == "webmcp_page_tool_then_input_json_proof"
    assert result["execution_mode"] == "webmcp_page_tool" and result["stopped_filling"] is True
    web = result["webmcp"]
    assert web["tool"] == "fill_transport_profile_form" and web["coverage"] == 1.0 and web["unmapped_inputs"] == []
    assert web["input_json_completion_proof"]["pass"] is True and web["input_json_completion_proof"]["matched_count"] == 14
    assert result["final_execution"]["attempts"] == []  # no field was driven one by one
    assert state == {"calls": ["fill_transport_profile_form"], "saved": False, "name": "SFTP_U-HAUL_ASN_PC_SRC_IB"}
    assert seconds < 20
    audit = json.loads((tmp_path / "ok" / "webmcp_page_tools.json").read_text(encoding="utf-8"))
    assert audit["values_stored"] is False and "args" not in json.dumps(audit["candidates"])

    # A page tool that puts a wrong value: the proof catches it and the normal fill corrects it.
    result, _, state = asyncio.run(run(True))
    assert result["pass"] is True and result.get("completed_by") != "webmcp_page_tool_then_input_json_proof"
    first = result["cycles"][0]["webmcp"]
    assert first["filled"] is True and first["input_json_completion_proof"]["missing_fields"] == ["profile_name"]
    assert state["name"] == "SFTP_U-HAUL_ASN_PC_SRC_IB" and state["saved"] is False


def test_tools_are_discovered_classified_and_a_mutating_tool_needs_the_gate(tmp_path: Path, monkeypatch):
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent.portal_operations import operation_gate
    from webmcp_portal_support import tp_page

    async def run():
        async with async_playwright() as pw:
            browser, ctx = await _context(pw)
            page = await ctx.new_page()
            try:
                await page.goto(tp_page(tmp_path))
                tools = await list_tools(page)
                groups = await call_tool(page, "get_deployment_groups", {"profileUsage": "Sender"}, tools=tools)
                refused = ""
                try:
                    await call_tool(page, "save_transport_profile", {}, tools=tools, gate=operation_gate(False, ""))
                except WebMCPToolRefused as exc:
                    refused = str(exc)
                saved_without = await page.evaluate("() => !!window.__saved")
                monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
                saved = await call_tool(page, "save_transport_profile", {}, tools=tools,
                                        gate=operation_gate(True, "ALLOW HIP MUTATION"))
                return tools, groups, refused, saved_without, saved, await page.evaluate("() => !!window.__saved")
            finally:
                await browser.close()

    tools, groups, refused, saved_without, saved, saved_with = asyncio.run(run())
    kinds = {t["name"]: (t["source"], t["classification"]) for t in tools}
    assert kinds["fill_transport_profile_form"] == ("registerTool", "form_edit")
    assert kinds["get_deployment_groups"] == ("registerTool", "read_only")
    assert kinds["save_transport_profile"] == ("registerTool", "mutating")
    assert {n for n, (src, _) in kinds.items() if src == "hip-agent"} == {
        "hip_page_state", "hip_read_form", "hip_form_matches", "hip_open_tab", "hip_fill_text"}
    assert groups["json"] == ["da-sender-sftphaft-dce-shared"] and groups["classification"] == "read_only"
    assert refused.startswith("HIP_WEBMCP_MUTATING_TOOL_BLOCKED") and saved_without is False
    assert saved["classification"] == "mutating" and saved_with is True
    rows = {r["name"]: r for r in catalog(tools)}
    assert "profileName" in rows["fill_transport_profile_form"]["parameters"]


def test_a_declarative_form_tool_fills_without_submitting(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from webmcp_portal_support import declarative_page

    async def run():
        async with async_playwright() as pw:
            browser, ctx = await _context(pw)
            page = await ctx.new_page()
            try:
                await page.goto(declarative_page(tmp_path))
                tools = await list_tools(page)
                filled = await call_tool(page, "fill_rule_draft", {"ruleName": "RULE_1", "priority": "High", "status": "Inactive",
                                                                   "notify": True, "__submit": True}, tools=tools)
                refused = ""
                try:
                    await call_tool(page, "submit_rule", {"ruleName": "RULE_1"}, tools=tools)
                except WebMCPToolRefused as exc:
                    refused = str(exc)
                state = await page.evaluate("""() => ({name: document.getElementById('n').value, priority: document.getElementById('p').value,
                    status: (document.querySelector('input[name=status]:checked') || {}).value, notify: document.getElementById('c').checked,
                    submits: window.__submits})""")
                return tools, filled, refused, state
            finally:
                await browser.close()

    tools, filled, refused, state = asyncio.run(run())
    draft = next(t for t in tools if t["name"] == "fill_rule_draft")
    assert draft["source"] == "declarative" and draft["classification"] == "form_edit"
    props = draft["inputSchema"]["properties"]
    assert props["ruleName"]["title"] == "Rule Name" and props["ruleName"]["description"] == "Unique rule name"
    assert props["priority"]["enum"] == ["High", "Low"] and props["status"]["enum"] == ["Active", "Inactive"]
    assert draft["inputSchema"]["required"] == ["ruleName"]
    assert next(t for t in tools if t["name"] == "submit_rule")["classification"] == "mutating"  # toolautosubmit
    assert filled["json"]["filled"] == ["ruleName", "priority", "status", "notify"] and "submitted" not in filled["json"]
    assert state == {"name": "RULE_1", "priority": "High", "status": "Inactive", "notify": True, "submits": 0}
    assert refused.startswith("HIP_WEBMCP_MUTATING_TOOL_BLOCKED")


def test_native_webmcp_is_used_when_the_browser_has_it(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from webmcp_portal_support import tp_page

    async def run():
        async with async_playwright() as pw:
            browser, ctx = await _context(pw, native=True)
            page = await ctx.new_page()
            try:
                await page.goto(tp_page(tmp_path))
                layer = await page.evaluate("() => ({polyfilled: window.__hipWebMCP.polyfilled, native: window.__hipWebMCP.native, testing: window.__hipWebMCP.nativeTesting})")
                tools = await list_tools(page)
                groups = await call_tool(page, "get_deployment_groups", {"profileUsage": "Receiver"}, tools=tools)
                listed = await page.evaluate("() => window.__nativeListed || 0")
                return layer, tools, groups, listed
            finally:
                await browser.close()

    layer, tools, groups, listed = asyncio.run(run())
    assert layer == {"polyfilled": False, "native": True, "testing": True}
    assert listed >= 1  # the agent asked Chrome's modelContextTesting
    sources = {t["name"]: t["source"] for t in tools}
    assert sources["fill_transport_profile_form"] == "native" and sources["hip_read_form"] == "hip-agent"
    assert groups["json"] == ["pt-receiver-sftphaft-dce-shared"]


def test_the_agents_in_page_tools_read_state_compare_and_navigate(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from whitelabel_portal_support import WHITELABEL_HTML

    wizard = (tmp_path / "wizard.html")
    wizard.write_text("""<html><body><div role=tablist><button role=tab aria-selected=true onclick="sel(this)">Flow Details</button>
      <button role=tab aria-selected=false onclick="sel(this)">Configure Source</button></div>
      <form><label for=a>Business Flow Name *</label><input id=a name=a value="FLOW_1">
      <label for=b>Notes</label><input id=b name=b></form>
      <script>function sel(t){document.querySelectorAll('[role=tab]').forEach(x=>x.setAttribute('aria-selected', String(x===t)));}</script>
      </body></html>""", encoding="utf-8")
    error = tmp_path / "error.html"
    error.write_text(WHITELABEL_HTML, encoding="utf-8")

    async def run():
        async with async_playwright() as pw:
            browser, ctx = await _context(pw)
            page = await ctx.new_page()
            try:
                await page.goto(wizard.as_uri())
                tools = await list_tools(page)
                state = (await call_tool(page, "hip_page_state", {}, tools=tools))["json"]
                form = (await call_tool(page, "hip_read_form", {}, tools=tools))["json"]
                typed = (await call_tool(page, "hip_fill_text", {"label": "Notes", "value": "hello"}, tools=tools))["json"]
                matches = (await call_tool(page, "hip_form_matches", {"expected": [
                    {"field": "business_flow_name", "labels": ["Business Flow Name"], "value": "FLOW_1"},
                    {"field": "notes", "labels": ["Notes"], "value": "hello"},
                    {"field": "owner", "labels": ["Owner"], "value": "x"}]}, tools=tools))["json"]
                opened = (await call_tool(page, "hip_open_tab", {"label": "Configure Source"}, tools=tools))["json"]
                selected = await page.evaluate("() => document.querySelector('[role=tab][aria-selected=true]').innerText")
                await page.goto(error.as_uri())
                broken = (await call_tool(page, "hip_page_state", {}))["json"]
                return state, form, typed, matches, opened, selected, broken
            finally:
                await browser.close()

    state, form, typed, matches, opened, selected, broken = asyncio.run(run())
    assert state["whitelabel_error"] is False and [t["text"] for t in state["tabs"]] == ["Flow Details", "Configure Source"]
    assert [(f["label"], f["value"]) for f in form] == [("Business Flow Name *", "FLOW_1"), ("Notes", "")]
    assert typed == {"filled": True}
    assert matches == {"matched": ["business_flow_name", "notes"], "missing": ["owner"], "complete": False}
    assert opened["opened"] is True and selected == "Configure Source"
    assert broken["whitelabel_error"] is True


def test_every_browser_page_gets_webmcp_also_after_a_restart(tmp_path: Path):
    _need_browser()
    from hip_id_agent.browser_session import BrowserSession
    from loader_portal_support import patch_navigation, real_session_config
    from webmcp_portal_support import tp_page

    url = tp_page(tmp_path)

    async def run():
        session = BrowserSession(real_session_config(tmp_path), tmp_path / "run")
        await session.start()
        patch_navigation(session)
        try:
            await session.goto_base_and_complete_sso(url)
            page = await session._ensure_active_page(url)
            before = [t["name"] for t in await list_tools(page) if t["source"] != "hip-agent"]
            await session.restart(reason="test")
            await session.goto_base_and_complete_sso(url)
            page = await session._ensure_active_page(url)
            after = [t["name"] for t in await list_tools(page) if t["source"] != "hip-agent"]
            return before, after, session.webmcp_installed, session._start_count
        finally:
            await session.close()

    before, after, installed, starts = asyncio.run(run())
    assert installed is True and starts == 2
    assert before == after == ["fill_transport_profile_form", "get_deployment_groups", "save_transport_profile"]


# ------------------------------------------------------------- units
def test_tool_classification_fails_closed():
    cases = {
        ("fill_create_transport_profile_form", "Fills the form. It does not save."): "form_edit",
        ("fillTransportProfile", "Fills the fields and saves the profile"): "mutating",
        ("save_transport_profile", ""): "mutating",
        ("create_rule", ""): "mutating",
        ("validate_bizflow", ""): "mutating",
        ("get_partner_list", ""): "read_only",
        ("select_document_type", ""): "form_edit",
        ("open_routing_tab", ""): "navigation",
        ("mystery_tool", ""): "mutating",
    }
    for (name, description), expected in cases.items():
        assert classify_tool({"name": name, "description": description}) == expected, name
    assert classify_tool({"name": "lookupGroups", "annotations": {"readOnlyHint": True}}) == "read_only"
    assert classify_tool({"name": "set_value", "annotations": {"destructiveHint": True}}) == "mutating"
    assert classify_tool({"name": "x", "source": "declarative", "autosubmit": True}) == "mutating"
    assert classify_tool({"name": "x", "source": "declarative", "autosubmit": False}) == "form_edit"


def test_input_json_values_are_mapped_onto_the_tools_parameters():
    tool = {"name": "fill", "inputSchema": {"type": "object", "required": ["profileName"], "properties": {
        "profileName": {"type": "string", "title": "Profile Name"},
        "postTransferAction": {"type": "string", "enum": ["Move To Archive", "Delete"]},
        "documentType": {"type": "string", "enum": ["XML_A (1.0)"]},
        "compress": {"type": "boolean", "title": "Is Compression Required"},
        "conditions": {"type": "array", "items": {"type": "object", "properties": {"attribute": {"type": "string"}, "value": {"type": "string"}}}},
    }}}
    leaves = [
        {"input_path": "$.objects.p.profile_name", "field_key": "profile_name", "value": "TP_1"},
        {"input_path": "$.objects.p.post_transfer_action", "field_key": "post_transfer_action", "value": "Move to Archive"},
        {"input_path": "$.objects.p.document_type", "field_key": "document_type", "value": "XML_A(1.0)"},
        {"input_path": "$.objects.p.is_compression_required", "field_key": "is_compression_required", "value": "FALSE"},
        {"input_path": "$.objects.p.conditions[0].attribute", "field_key": "attribute", "value": "Receiver"},
        {"input_path": "$.objects.p.conditions[1].value", "field_key": "value", "value": "U-HAUL"},
        {"input_path": "$.objects.p.not_offered", "field_key": "not_offered", "value": "x"},
    ]
    plan = map_input_to_tool(tool, leaves)
    assert plan["args"] == {"profileName": "TP_1", "postTransferAction": "Move To Archive", "documentType": "XML_A (1.0)",
                            "compress": False, "conditions": [{"attribute": "Receiver"}, {"value": "U-HAUL"}]}
    assert plan["unmapped_inputs"] == ["$.objects.p.not_offered"] and plan["coverage"] == round(6 / 7, 3)
    assert plan["missing_required"] == []
    bad = map_input_to_tool(tool, [{"input_path": "$.p.post_transfer_action", "field_key": "post_transfer_action", "value": "Burn"}])
    assert bad["args"] == {} and bad["missing_required"] == ["profileName"]  # a value the page does not offer is not sent


def test_policy_config_cli_and_control_center(monkeypatch):
    cfg = AppConfig()
    assert cfg.webmcp.enabled is True and cfg.webmcp.use_page_tools_for_fill is True and cfg.webmcp.min_input_coverage == 0.6
    assert webmcp_policy(cfg).enabled is True
    monkeypatch.setenv("HIP_WEBMCP", "off")
    assert webmcp_policy(cfg).enabled is False
    monkeypatch.delenv("HIP_WEBMCP")
    from hip_id_agent import autonomous_form_runtime, browser_session, cli

    assert "fill_with_page_tools" in inspect.getsource(autonomous_form_runtime.execute_autonomous_phase_goal)
    assert "install_on_context" in inspect.getsource(browser_session.BrowserSession.start)
    assert "webmcp_tools_cmd" in dir(cli)
    from fastapi.testclient import TestClient

    from backend.app import app

    client = TestClient(app)
    status = client.get("/api/webmcp")
    assert status.status_code == 200 and status.json()["enabled"] is True
    root = Path(__file__).resolve().parents[1]
    for ui in ("webui", "backend/webui"):
        assert 'id="webmcpMetric"' in (root / ui / "index.html").read_text(encoding="utf-8")
        assert "runtime.webmcp" in (root / ui / "app.js").read_text(encoding="utf-8")
