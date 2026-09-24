from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig, load_config
from hip_id_agent.dummy_fill_e2e import phase_exact_completion_checkpoint
from hip_id_agent.mission_trace import MissionTraceLedger


def test_default_config_allows_pyautogui_or_playwright_executor():
    cfg = load_config("config.yaml")
    assert cfg.pyautogui.interaction_mode == "primary"
    assert cfg.pyautogui.mcp_required is False
    assert cfg.pyautogui.fallback_to_playwright_mcp is True
    assert cfg.pyautogui.fallback_to_python_playwright is True
    assert cfg.live_runtime_certification.require_pyautogui_mcp is False


def test_mcp_safe_selector_rejects_human_action_description():
    session = object.__new__(BrowserSession)
    assert session._mcp_safe_selector("Data Maps top-right + Add") is False
    assert session._mcp_safe_selector('button[aria-label="Add"]') is True


def test_locator_is_canonicalized_to_unique_css_for_mcp(tmp_path: Path):
    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
            page = await browser.new_page()
            await page.set_content('''
              <div class="dds__table__ribbon__action-bar">
                <dds-button kind="tertiary" size="sm"><button aria-label="Manage Columns">Manage Columns</button></dds-button>
                <dds-button kind="tertiary" size="sm"><button aria-label="Add">Add</button></dds-button>
              </div>
              <table><tr><td><button class="dds__td-expandable__button" aria-label="Expand the row">+</button></td></tr></table>
            ''')
            session = object.__new__(BrowserSession)
            session.page = page
            locator = page.get_by_role("button", name="Add", exact=True)
            result = await session._canonical_selector_for_locator(locator, "Data Maps top-right + Add")
            assert result["selector"]
            assert result["mcp_safe"] is True
            assert result["selector"] != "Data Maps top-right + Add"
            assert await page.locator(result["selector"]).count() == 1
            assert await page.locator(result["selector"]).get_attribute("aria-label") == "Add"
            await browser.close()
    asyncio.run(run())


def test_strict_completion_rejects_visible_values_without_authoritative_executor(tmp_path: Path):
    phase_dir = tmp_path / "data_map"
    target = phase_dir / "datamap_kb" / "datamap_target_branch_execution.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        "pass": True,
        "status": "pass",
        "strict_live_execution": True,
        "attempts": [{"node_id":"data_map.map_name","success":True,"exact_verified":True}],
        "failed_attempts": [],
        "execution_stage_audit": {
            "fields_filled_or_verified": True,
            "exact_execution_verified": True,
            "authoritative_execution_verified": False,
        },
    }), encoding="utf-8")
    checkpoint = phase_exact_completion_checkpoint("data_map", phase_dir)
    assert checkpoint["pass"] is False
    assert checkpoint["status"] == "no_exact_completion_proof"
    assert checkpoint["inspected"][0]["strict_stage_ok"] is False


def test_strict_completion_accepts_authoritative_exact_transaction(tmp_path: Path):
    phase_dir = tmp_path / "data_map"
    target = phase_dir / "datamap_kb" / "datamap_target_branch_execution.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        "pass": True,
        "status": "pass",
        "strict_live_execution": True,
        "attempts": [{
            "node_id":"data_map.map_name","success":True,"exact_verified":True,
            "authoritative_execution":True,"executor":"playwright-mcp-fallback",
        }],
        "failed_attempts": [],
        "execution_stage_audit": {
            "fields_filled_or_verified": True,
            "exact_execution_verified": True,
            "authoritative_execution_verified": True,
        },
    }), encoding="utf-8")
    checkpoint = phase_exact_completion_checkpoint("data_map", phase_dir)
    assert checkpoint["pass"] is True
    assert checkpoint["status"] == "exact_live_execution_completed"
    assert checkpoint["inspected"][0]["exact_verified"] is True


def test_blocked_phase_continuation_is_not_reported_as_verified_handoff(tmp_path: Path):
    trace = MissionTraceLedger(tmp_path, run_id="RUN-V229", phases=["data_map", "source_document_type"])
    trace.record_transition("data_map", "source_document_type", status="continued_from_blocked", details={"code":"BLOCKED"})
    state = trace.state
    dm = next(x for x in state["steps"] if x["phase"] == "data_map")
    sdt = next(x for x in state["steps"] if x["phase"] == "source_document_type")
    assert dm["handoff"]["status"] == "continued_from_blocked"
    assert "diagnostics" in dm["current_activity"].lower()
    assert "verified" not in dm["current_activity"].lower()
    assert "blocked" in sdt["current_activity"].lower()
