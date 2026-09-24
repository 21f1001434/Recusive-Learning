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


async def _run(
    tmp_path: Path, *, phase: str, fixture: str, data: Dict[str, Any], section: Optional[str], prepare_js: str,
    max_cycles: int, broker: bool,
) -> Tuple[Dict[str, Any], List[Any]]:
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
        except Exception as exc:  # pragma: no cover - environment without Chromium
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.set_content(replica_html(fixture))
        if prepare_js:
            await page.evaluate(prepare_js)
            await page.wait_for_timeout(300)
        if broker:
            attach_broker_session(page, tmp_path, phase)
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
    max_cycles: int = 2, broker: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Any]]:
    data = uhaul_input(phase, tmp_path)
    result, dom = asyncio.run(_run(
        tmp_path, phase=phase, fixture=fixture, data=data, section=section, prepare_js=prepare_js,
        max_cycles=max_cycles, broker=broker,
    ))
    return result, autonomous_target_execution(result), dom


def failed_fields(final: Dict[str, Any]) -> List[Tuple[Any, Any, Any]]:
    return [(a.get("field"), a.get("row_index"), a.get("reason")) for a in final.get("attempts", []) if not a.get("success")]


def dom_value(dom: List[Dict[str, Any]], key: str, occurrence: int = 0) -> Any:
    matches = [f for f in dom if f.get("key") == key or f.get("name") == key]
    return matches[occurrence] if len(matches) > occurrence else None
