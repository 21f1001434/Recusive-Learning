"""Shared harness for the full-length HIP form replicas (V243R15).

The replicas in ``tests/fixtures`` are rebuilt from the approved golden
screenshots with Dell DDS behaviour (``hip_dds_kit.js``): owned popup
listboxes, multi-select chips, switches, radio groups, conditional children,
a fixed header, smooth scrolling and a 720 px viewport, so most fields start
below the fold.  Each run drives the real ``execute_autonomous_phase_goal``,
optionally through the real ``BrowserSession`` click/fill broker.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.autonomous_form_runtime import autonomous_target_execution, execute_autonomous_phase_goal
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def chromium_path() -> Optional[str]:
    for candidate in (os.environ.get("HIP_TEST_CHROMIUM"), "/opt/pw-browsers/chromium", "/usr/bin/chromium"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def uhaul_input(phase: str, tmp_path: Optional[Path] = None) -> Dict[str, Any]:
    payload = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    obj = dict((payload.get("objects") or payload)[phase])
    if phase == "data_map" and tmp_path is not None:
        jar = tmp_path / "Transform_DELLCoXMLASNXX08C.jar"
        jar.write_bytes(b"PK\x03\x04replica")
        obj["map_data_file"] = str(jar)
    return {"objects": {phase: obj}}


def replica_html(fixture: str, setup_js: str = "") -> str:
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    kit = (FIXTURES / "hip_dds_kit.js").read_text(encoding="utf-8")
    return html.replace("<!--HIP_DDS_KIT-->", f"<script>{setup_js}</script><script>{kit}</script>")


def attach_broker_session(page: Any, tmp_path: Path, phase: str) -> None:
    """Route every click/fill through the real BrowserSession broker."""
    from hip_id_agent.browser_session import BrowserSession
    from hip_id_agent.config import AppConfig

    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "hip_memory")
    session = BrowserSession(cfg, tmp_path / "session")
    session.page = page
    session._active_phase_name = phase

    async def _active_page(expected_url: str = ""):
        return page

    session._ensure_active_page = _active_page
    # The live MCP evidence servers are not running in unit tests.
    if getattr(session, "semantic_action_gate", None) is not None:
        session.semantic_action_gate.enabled = False
    page._hip_browser_session = session


async def install_observers(page: Any) -> None:
    """Install the DOM event/mutation observers every live session page has."""
    from hip_id_agent.browser_session import CLICK_LISTENER_SCRIPT, DOM_EVENT_OBSERVER_SCRIPT

    for script in (CLICK_LISTENER_SCRIPT, DOM_EVENT_OBSERVER_SCRIPT):
        await page.evaluate(script)


def live_plus_html(fixture: str) -> str:
    """The replica with the live portal's legend "+" (V243R20): lists start with
    one row (or none) and grow only through the icon-only plus in their legend."""
    return replica_html(fixture).replace("<body>", "<body><script>window.__livePlus = true;</script>", 1)


async def _run(
    tmp_path: Path, *, phase: str, fixture: str, data: Dict[str, Any], section: Optional[str], prepare_js: str,
    max_cycles: int, broker: bool, live_plus: bool = False, observers: bool = False,
) -> Tuple[Dict[str, Any], List[Any]]:
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
        except Exception as exc:  # pragma: no cover - environment without Chromium
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.set_content(live_plus_html(fixture) if live_plus else replica_html(fixture))
        if prepare_js:
            await page.evaluate(prepare_js)
            await page.wait_for_timeout(300)
        if broker:
            attach_broker_session(page, tmp_path, phase)
        if observers:
            await install_observers(page)
        try:
            kwargs: Dict[str, Any] = {"section": section} if section else {}
            if "document_type" in phase:
                kwargs["executor"] = execute_document_type_state_graph
            result = await execute_autonomous_phase_goal(
                page=page, graph=compile_phase_state_graph(data, phase), phase=phase, input_data=data,
                config=None, output_dir=tmp_path / "out", max_cycles=max_cycles, **kwargs,
            )
            # Blur everything first: a dropdown value that was only typed into
            # the search box (not committed) disappears here.
            await page.mouse.click(5, 700)
            await page.wait_for_timeout(400)
            if live_plus:
                page_stats = await page.evaluate("() => ({plus_clicks: window.__hipPlusClicks || 0, rows_removed: window.__hipRowsRemoved || 0})")
                result = dict(result, live_plus_stats=page_stats)
            dom = await page.evaluate(
                """() => Array.from(document.querySelectorAll('input,textarea')).filter(e => e.offsetParent || e.type === 'file').map(e => ({
                    key: e.placeholder || e.name, name: e.name, type: e.type,
                    value: e.type === 'file' ? ((e.files[0] || {}).name || '') : e.value,
                    checked: !!e.checked,
                    chips: e.closest('dds-dropdown') ? Array.from(e.closest('dds-dropdown').querySelectorAll('.dds__tag')).map(t => t.textContent) : [],
                }))"""
            )
            return result, dom
        finally:
            await browser.close()


def run_phase_replica(
    tmp_path: Path, *, phase: str, fixture: str, section: Optional[str] = None, prepare_js: str = "",
    max_cycles: int = 2, broker: bool = False, live_plus: bool = False, observers: bool = False,
    data: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Any]]:
    data = data or uhaul_input(phase, tmp_path)
    result, dom = asyncio.run(_run(
        tmp_path, phase=phase, fixture=fixture, data=data, section=section, prepare_js=prepare_js,
        max_cycles=max_cycles, broker=broker, live_plus=live_plus, observers=observers,
    ))
    return result, autonomous_target_execution(result), dom


def failed_fields(final: Dict[str, Any]) -> List[Tuple[Any, Any, Any]]:
    return [(a.get("field"), a.get("row_index"), a.get("reason")) for a in final.get("attempts", []) if not a.get("success")]


def dom_value(dom: List[Dict[str, Any]], key: str, occurrence: int = 0) -> Any:
    matches = [f for f in dom if f.get("key") == key or f.get("name") == key]
    return matches[occurrence] if len(matches) > occurrence else None


# ---------------------------------------------------------------- V243R17
# Variant forms: the same replicas with window.__variant set add what a HIP
# form can also contain -- radio groups (hidden-input and button-style),
# checkbox groups, collapsed sections, "+ Add" row lists and branch fields --
# and the input.json below carries keys the phase compilers do not know.
VARIANT_FIXTURES = {
    "data_map": "data_map_full_dds.html",
    "source_document_type": "document_type_full_dds.html",
    "rule": "rule_full_dds.html",
    "target_transport_profile": "transport_profile_full_dds.html",
    "biz_flow": "bizflow_wizard_dds.html",
}


def variant_input(phase: str, tmp_path: Optional[Path] = None) -> Dict[str, Any]:
    data = uhaul_input(phase, tmp_path)
    obj = data["objects"][phase]
    if phase == "data_map":
        obj["cross_reference_table_details"] = "Yes"
        obj["cross_reference_map_list"] = [{"source_value": "US", "target_value": "840"}, {"source_value": "CA", "target_value": "124"}]
        obj["advanced_options"] = {"map_engine": "XSLT", "notify_on": ["Failure", "Warning"]}
    elif "document_type" in phase:
        obj["data_format_type"] = "EDIX12"
        obj["document_identifier"] = {"operation": "All conditions are satisfied", "rows": [
            {"derived_from": "TRANSACTION_ROOT_ELEMENT", "value": "ISA"},
            {"derived_from": "NAMESPACE", "value": "urn:x12"},
        ]}
        obj["attributes_to_configure"] = obj["attributes_to_configure"][:3]
        obj.update({"segment_separator": "~", "data_element_separator": "*", "sub_element_separator": ">", "acknowledgement_required": "Yes"})
    elif phase == "rule":
        obj["conditions"]["rows"].append({"condition_type": "Attributes", "operator": "Starts With", "value": "856", "attribute_name_unit": "Transaction Type"})
        obj["execute_always"] = "Yes"
        obj["advanced"] = {"priority": "High"}
    elif "transport_profile" in phase:
        obj.pop("existing_account_name", None)
        obj.pop("subscription_folder", None)
        obj.update({"existing_account": "No", "account_name": "haftnew0001", "use_existing_folder": "Yes", "existing_folder": "/Outbound"})
        obj["tags"] = [{"key": "env", "value": "uat"}, {"key": "owner", "value": "b2b"}]
        obj["notification_settings"] = {"notify_on": ["Failure"], "notification_channel": "Webhook", "notification_email": "b2b@example.com"}
    elif phase == "biz_flow":
        obj["flow_details"]["flow_type"] = "Passthrough"
        obj["flow_details"]["alert_settings"] = {"alerts_enabled": True, "alert_on": ["Failure", "Delay"]}
    return data


def variant_html(phase: str) -> str:
    fixture = VARIANT_FIXTURES[phase]
    if "document_type" in phase:
        # One attribute row and one identifier row to start; "+" adds the rest.
        return (FIXTURES / fixture).read_text(encoding="utf-8").replace(
            "<script>", "<script>window.__variant = true; window.__attributeRows = 1;</script><script>", 1)
    return replica_html(fixture, "window.__variant = true;")


_VARIANT_DOM_JS = """() => Array.from(document.querySelectorAll('input,textarea,[role=radio]'))
  .filter(e => e.offsetParent || e.type === 'radio' || e.type === 'checkbox')
  .map(e => ({key: e.placeholder || e.name || e.textContent.trim(), type: e.type || e.getAttribute('role'), value: e.value,
              checked: !!e.checked || e.getAttribute('aria-checked') === 'true',
              chips: e.closest('dds-dropdown') ? Array.from(e.closest('dds-dropdown').querySelectorAll('.dds__tag')).map(t => t.textContent) : []}))"""


async def _run_variant(tmp_path: Path, phase: str, *, section: Optional[str], broker: bool, max_cycles: int,
                       observers: bool = False) -> Tuple[Dict[str, Any], List[Any]]:
    data = variant_input(phase, tmp_path)
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.set_content(variant_html(phase))
        if broker:
            attach_broker_session(page, tmp_path, phase)
        if observers:
            # The live session installs these on every page: DOM events and
            # mutations settle a selection as soon as the portal reacts.
            from hip_id_agent.browser_session import CLICK_LISTENER_SCRIPT, DOM_EVENT_OBSERVER_SCRIPT

            for script in (CLICK_LISTENER_SCRIPT, DOM_EVENT_OBSERVER_SCRIPT):
                await page.evaluate(script)
        try:
            kwargs: Dict[str, Any] = {"section": section} if section else {}
            if "document_type" in phase:
                kwargs["executor"] = execute_document_type_state_graph
            result = await execute_autonomous_phase_goal(
                page=page, graph=compile_phase_state_graph(data, phase), phase=phase, input_data=data,
                config=None, output_dir=tmp_path / "out", max_cycles=max_cycles, **kwargs,
            )
            await page.mouse.click(5, 700)
            await page.wait_for_timeout(300)
            return result, await page.evaluate(_VARIANT_DOM_JS)
        finally:
            await browser.close()


def run_variant_replica(
    tmp_path: Path, phase: str, *, section: Optional[str] = None, broker: bool = False, max_cycles: int = 3,
    observers: bool = False,
) -> Tuple[Dict[str, Any], List[Any]]:
    return asyncio.run(_run_variant(tmp_path, phase, section=section, broker=broker, max_cycles=max_cycles, observers=observers))


def dom_values(dom: List[Dict[str, Any]], key: str) -> List[Any]:
    return [d.get("value") for d in dom if d.get("key") == key]


def dom_checked(dom: List[Dict[str, Any]], key: str) -> List[Any]:
    return [d.get("value") or d.get("key") for d in dom if d.get("checked") and d.get("key") == key]
