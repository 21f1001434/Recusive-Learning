"""V243R38: whole missions keep going -- and say what they learned.

Reproduced with ``hip_portal_sim`` (every HIP module at its real
developer.dell.com address, Chromium resolving the host to a local HTTPS
server) and the real ``FullDummyFillE2EFlow``:

* every structural click ("+ Add", a row "+") waited 45 s after it had worked,
  because the form-memory observer looked the clicked element up again by a
  label used as a selector; the no-progress watchdog then cancelled the attempt
  and reopened the form -- "Live form not yet exact: name, transaction_type, ..."
  on the listing, attempt after attempt;
* the listing-era row adders (Document Type attributes, Rule conditions) cannot
  click the live legend "+" and failed the attempt instead of letting the goal
  engine add the rows;
* Source and Target Document Type share one URL: the handoff left Source's
  filled drawer open and Target typed over it;
* a module that never finished loading (endless spinner, or the portal bouncing
  elsewhere) was "usable" because the menu names every module, or failed the
  navigation with nothing stronger than the same goto again; a phase that could
  not recover held the browser for a human forever, so a seven-phase mission
  stopped after one or two phases;
* with the section judges off a completed mission was reported blocked;
* nobody could tell whether a deterministic script was generated.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from hip_portal_sim import HipPortalSim, mission_input, sim_session_config
from phase_replica_support import chromium_path

pytestmark = pytest.mark.skipif(chromium_path() is None, reason="Chromium unavailable")


async def _session(tmp: Path, sim: HipPortalSim, *, step_seconds: float = 4.0, memory=None):
    from hip_id_agent.browser_session import BrowserSession

    cfg = sim_session_config(tmp, sim)
    cfg.runtime_self_heal.route_recovery_step_seconds = step_seconds
    cfg.portal.timeout_ms = 12000
    session = BrowserSession(cfg, tmp / "run")
    if memory is not None:
        session.flow_pattern_memory = memory
    await session.start()
    return session


# ---------------------------------------------------------------- the 45 s click
class _Memory:
    def __init__(self) -> None:
        self.rows = []

    def observe_action(self, row: Dict[str, Any]) -> None:
        self.rows.append(row)


def test_a_click_that_opens_the_form_is_not_held_for_the_page_timeout(tmp_path: Path):
    async def run():
        with HipPortalSim(tmp_path / "cert") as sim:
            memory = _Memory()
            session = await _session(tmp_path, sim, memory=memory)
            try:
                session.context.set_default_timeout(45000)
                await session.page.goto(sim.url("doctypes"))
                started = time.monotonic()
                # The selector hint of the Document Type "+ Add" is a label, not a selector.
                await session._observe_form_memory_action(action_type="click", selector="Add Document Type",
                                                          audit={}, success=True)
                return time.monotonic() - started, memory.rows
            finally:
                await session.close()

    elapsed, rows = asyncio.run(run())
    assert elapsed < 3.0, elapsed  # was 45 s: the page timeout
    assert rows and rows[0]["semantic_target"] == "Add Document Type"


def test_add_click_waits_for_the_drawer_and_never_retries_a_vanished_button(tmp_path: Path):
    from hip_id_agent import doctype_kb as dk

    async def run():
        with HipPortalSim(tmp_path / "cert") as sim:
            session = await _session(tmp_path, sim, memory=_Memory())
            try:
                session.semantic_action_gate.enabled = False
                await session.page.goto(sim.url("doctypes"))
                add = await dk._find_add_button(session.page)
                started = time.monotonic()
                warnings = []
                opened = await dk._click_add_doctype_with_overlay_recovery(
                    session.page, session, add, kb_dir=tmp_path / "kb", warnings=warnings)
                return opened, time.monotonic() - started, await dk._locator_present(add), warnings
            finally:
                await session.close()

    opened, seconds, add_still_there, warnings = asyncio.run(run())
    assert opened is True and not warnings
    assert seconds < 20, seconds


def test_a_row_shortfall_is_handed_to_the_goal_engine_not_raised():
    from hip_id_agent.doctype_kb import _defer_row_deficit_to_goal_engine

    warnings = []
    audit = {"summary": {"exact_row_count_pass": False, "clicked": 0, "planned_add_clicks": 4}}
    _defer_row_deficit_to_goal_engine(audit, warnings, stage="deterministic-first")
    assert audit["deferred_to_goal_engine"] is True
    assert "goal engine adds the remaining rows" in warnings[0]
    exact = {"summary": {"exact_row_count_pass": True}}
    _defer_row_deficit_to_goal_engine(exact, warnings, stage="x")
    assert "deferred_to_goal_engine" not in exact and len(warnings) == 1


def test_the_rule_form_from_the_golden_screenshots_is_recognised(tmp_path: Path):
    from hip_id_agent.rules_kb import _looks_like_rule_add_form

    async def run():
        with HipPortalSim(tmp_path / "cert") as sim:
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True, executable_path=chromium_path(), args=sim.launch_args)
                page = await browser.new_page()
                await page.goto(sim.url("rules"))
                listing = await _looks_like_rule_add_form(page)
                await page.click("#add")
                await page.wait_for_timeout(800)
                form = await _looks_like_rule_add_form(page)
                await browser.close()
                return listing, form

    listing, form = asyncio.run(run())
    assert listing is False and form is True


# ---------------------------------------------------------------- handoff and routes
def test_the_previous_phase_form_is_closed_before_the_next_phase_on_the_same_url(tmp_path: Path):
    async def run():
        with HipPortalSim(tmp_path / "cert") as sim:
            session = await _session(tmp_path, sim)
            try:
                page = session.page
                await page.goto(sim.url("doctypes"))
                await page.click("#add")
                await page.wait_for_timeout(800)
                await page.fill("input[name=name]", "XML_DellAutoASN_10_U-HAUL_ANS_IB")
                # A portal whose Cancel does nothing (the drawer stays).
                await page.evaluate("() => document.querySelector('#cancel').replaceWith(document.querySelector('#cancel').cloneNode(true))")
                await page.evaluate("() => { const b = document.querySelector('#cancel'); b.addEventListener('click', (e) => e.stopPropagation()); }")
                session._active_phase_name = "source_document_type"
                audit = await session._dismiss_transient_ui(next_phase="target_document_type")
                drawer = await page.locator("app-generic-drawer").count()
                return audit, drawer
            finally:
                await session.close()

    audit, drawer = asyncio.run(run())
    assert audit["cancel_clicked"] is True
    assert audit["create_surface_still_open"] is True and audit["reloaded_to_discard_form"] is True
    assert drawer == 0  # the Target phase starts on a clean listing


def test_an_endless_module_spinner_is_not_usable_and_is_recovered_in_place(tmp_path: Path):
    async def run():
        with HipPortalSim(tmp_path / "cert") as sim:
            sim.fail("doctypes", "loader", loads=3)
            session = await _session(tmp_path, sim)
            try:
                session._active_phase_name = "source_document_type"
                await session.page.goto(sim.url("doctypes"))
                stuck = await session._navigation_usability(sim.url("doctypes"))
                await session.navigate(sim.url("doctypes"))
                usable = await session._navigation_usability(sim.url("doctypes"))
                audit = json.loads((session.run_dir / "mcp_runtime" / "route_recovery.json").read_text())
                memory = json.loads(session._route_recovery_path().read_text())
                return stuck, usable, audit, memory, list(sim.served)
            finally:
                await session.close()

    stuck, usable, audit, memory, served = asyncio.run(run())
    # The menu names "Document Types", but the module itself never rendered.
    assert stuck["usable"] is False and stuck["reason"] == "module_loading"
    assert usable["usable"] is True
    assert audit["pass"] is True and audit["resolved_by"] in {"reload", "portal_menu", "fresh_document"}
    resolved = memory["modules"]["doctypes"][audit["resolved_by"]]
    assert resolved["resolved"] == 1
    assert [s["fault"] for s in served][-1] == ""


def test_a_module_bouncing_to_the_portal_home_is_opened_from_the_menu(tmp_path: Path):
    async def run():
        with HipPortalSim(tmp_path / "cert") as sim:
            sim.fail("rules", "home", loads=3)
            session = await _session(tmp_path, sim)
            try:
                await session.navigate(sim.url("rules"))
                usable = await session._navigation_usability(sim.url("rules"))
                audit = json.loads((session.run_dir / "mcp_runtime" / "route_recovery.json").read_text())
                return usable, audit
            finally:
                await session.close()

    usable, audit = asyncio.run(run())
    assert usable["usable"] is True
    assert audit["pass"] is True
    assert [s["step"] for s in audit["steps"]][-1] == audit["resolved_by"]


def test_the_learned_ladder_order_puts_the_step_that_worked_first(tmp_path: Path):
    from hip_id_agent.browser_session import BrowserSession

    memory = {"modules": {"rules": {"wait": {"tried": 3, "resolved": 0}, "reload": {"tried": 3, "resolved": 0},
                                    "portal_menu": {"tried": 2, "resolved": 2}}}}
    order = BrowserSession._route_recovery_order(BrowserSession.__new__(BrowserSession), "rules", memory)
    assert order[0] == "portal_menu" and order[-2:] == ["wait", "reload"]


def test_a_handoff_to_a_module_stuck_for_good_restarts_the_browser_once(tmp_path: Path):
    async def run():
        with HipPortalSim(tmp_path / "cert") as sim:
            session = await _session(tmp_path, sim, step_seconds=2.0)
            try:
                session.semantic_action_gate.enabled = False

                async def same_surface(*args, **kwargs):  # no MCP servers in tests
                    return {"pass": True}

                session._verify_dual_mcp_same_surface = same_surface
                await session.page.goto(sim.url("datamaps"))
                # Every load of Rules spins until the browser is restarted.
                sim.fail("rules", "loader", loads=8)
                result = await session.handoff_to_next_phase(
                    from_phase="target_document_type", to_phase="rule", to_url=sim.url("rules"),
                    exact_checkpoint_passed=True)
                return result, session._start_count
            finally:
                await session.close()

    result, starts = asyncio.run(run())
    assert result["pass"] is True, result
    assert result["browser_restart"]["status"] in {"restarted", "relaunched"} or starts == 2


def test_a_stuck_route_is_a_loader_for_the_mission_self_heal():
    from hip_id_agent.runtime_self_heal import RuntimeSelfHealController

    message = "HIP_ROUTE_STUCK_LOADING: Navigation failed after retries and the recovery ladder: commit goto returned a non-target"
    assert RuntimeSelfHealController.classify_failure(message) == "portal_loading_stuck"
    assert RuntimeSelfHealController.CLASS_ACTIONS["portal_loading_stuck"] == ("refresh_page_and_reopen", "restart_browser_session")


# ---------------------------------------------------------------- bounded hold and deferral
def test_with_more_phases_to_run_a_blocked_phase_is_deferred_not_held_forever(tmp_path: Path):
    from hip_id_agent import agent_chat
    from hip_id_agent.config import AppConfig
    from hip_id_agent.dummy_fill_e2e import _HOLD_POLICY, _hold_incomplete_phase_for_human
    from hip_id_agent.human_phase_review import HumanPhaseReviewStore
    from hip_id_agent.mission_trace import MissionTraceLedger

    class _Browser:
        page = None

        async def screenshot(self, path, full_page=True):
            Path(path).write_bytes(b"png")

    cfg = AppConfig()
    cfg.human_in_the_loop.incomplete_phase_wait_seconds = 0  # indefinite on its own
    cfg.human_in_the_loop.incomplete_phase_poll_seconds = 0.05
    store = HumanPhaseReviewStore(tmp_path / "reviews", cfg.human_in_the_loop)
    trace = MissionTraceLedger(tmp_path / "run", run_id="R38", phases=["rule", "source_transport_profile"])
    agent_chat.activate(trace.chat)
    policy = {"defer_after_seconds": 1, "next": "source_transport_profile", "phase": "rule"}

    async def run():
        _HOLD_POLICY.set(policy)
        started = time.monotonic()
        result = await _hold_incomplete_phase_for_human(
            store=store, browser=_Browser(), run_id="R38", phase="rule", phase_display="Rule", recovery_round=3,
            phase_dir=tmp_path / "rule", reason="conditions not filled", config=cfg)
        return result, time.monotonic() - started

    try:
        result, seconds = asyncio.run(run())
    finally:
        agent_chat.deactivate(trace.chat)
    assert result["timed_out"] is True and result["deferred"] is True and policy["deferred"] is True
    assert seconds < 10
    wait = json.loads((tmp_path / "rule" / "INCOMPLETE_PHASE_WAITING.json").read_text())
    assert wait["status"] == "deferred_to_end_of_mission" and wait["next_phase"] == "source_transport_profile"
    texts = [json.loads(line)["text"] for line in (tmp_path / "run" / "agent_chat.jsonl").read_text().splitlines()]
    assert any("come back to Rule at the end" in t for t in texts)


def test_finalize_says_when_every_phase_finished_but_the_final_check_failed(tmp_path: Path):
    from hip_id_agent.mission_trace import MissionTraceLedger

    trace = MissionTraceLedger(tmp_path, run_id="R38", phases=["data_map"])
    trace.mark_phase("data_map", status="completed", attempt=1)
    trace.finalize(complete=False)
    last = json.loads((tmp_path / "agent_chat.jsonl").read_text().splitlines()[-1])["text"]
    assert "1/1 phases done, but the final completion check did not pass" in last
    assert "blocked phase above" not in last


# ---------------------------------------------------------------- the deterministic script
def _skill(status: str = "candidate") -> Dict[str, Any]:
    return {"schema_version": "hip.portal-skill-library.v1", "phase": "data_map", "skills": {"s1": {
        "skill_id": "s1", "phase": "data_map", "operation": "create", "status": status, "learned_at": "2026-10-03T10:00:00Z",
        "certified_at": "2026-10-03T11:00:00Z" if status == "certified" else None,
        "bindings": {"map_identifier": {"action": "fill_text"}, "contivo_version": {"action": "select_single"},
                     "map_data_file": {"action": "upload_file"}},
        "structure": {"rows": {}}, "stats": {"replays": 1 if status == "certified" else 0}, "values_stored": False}}}


def test_the_deterministic_script_is_written_readable_and_value_free(tmp_path: Path):
    from hip_id_agent.deterministic_script import chat_line, list_scripts, write_phase_script
    from hip_id_agent.dummy_fill_e2e import PHASE_URLS, make_phase_input
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph

    memory = tmp_path / "memory"
    (memory / "portal_skills").mkdir(parents=True)
    (memory / "portal_skills" / "data_map.json").write_text(json.dumps(_skill("candidate")))
    data = make_phase_input(json.loads(mission_input(tmp_path).read_text()), "data_map")
    summary = write_phase_script(phase="data_map", phase_dir=tmp_path / "run" / "data_map", memory_dir=memory,
                                 graph=compile_phase_state_graph(data, "data_map"), url=PHASE_URLS["data_map"], run_id="R38")
    md = (tmp_path / "run" / "data_map" / "deterministic_script.md").read_text()
    assert summary["status"] == "candidate" and summary["fields"] >= 3
    assert "Click “+ Add”" in md and "input.json → map_identifier" in md and "Upload the file from input.json → map_data_file" in md
    assert "Stop — nothing is saved" in md
    assert "DELLCoXMLASNXX08C" not in md  # no values
    line = chat_line(summary, "Data Map")
    assert line.startswith("📜 Deterministic script for Data Map saved (candidate)") and "deterministic_script.md" in line
    # A later run certifies the skill: the listing reflects it without rewriting.
    (memory / "portal_skills" / "data_map.json").write_text(json.dumps(_skill("certified")))
    listing = list_scripts(memory)
    assert listing["certified"] == 1 and listing["scripts"][0]["status"] == "certified"
    assert "certified — a later run replayed it" in listing["scripts"][0]["markdown"]


def test_the_control_center_lists_the_deterministic_scripts(tmp_path: Path):
    import yaml
    from fastapi.testclient import TestClient

    from backend.app import app

    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    data["reporting"]["memory_dir"] = str(tmp_path / "memory")
    data["reporting"]["runs_dir"] = str(tmp_path / "runs")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(data), encoding="utf-8")
    (tmp_path / "memory" / "portal_skills").mkdir(parents=True)
    (tmp_path / "memory" / "portal_skills" / "data_map.json").write_text(json.dumps(_skill("candidate")))
    from hip_id_agent.deterministic_script import write_phase_script

    write_phase_script(phase="data_map", phase_dir=tmp_path / "runs" / "R" / "data_map", memory_dir=tmp_path / "memory")
    body = TestClient(app).get("/api/deterministic-scripts", params={"config": str(cfg)}).json()
    assert body["count"] == 1 and body["candidates"] == 1
    assert body["scripts"][0]["phase"] == "data_map" and "# Deterministic script — Data Map" in body["scripts"][0]["markdown"]
    for ui in (root / "webui", root / "backend" / "webui"):
        assert 'id="deterministicScriptsPanel"' in (ui / "index.html").read_text(encoding="utf-8")
        assert "/api/deterministic-scripts" in (ui / "app.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- one real mission
def test_a_data_map_mission_completes_and_reports_its_deterministic_script(tmp_path: Path):
    """The real FullDummyFillE2EFlow against the simulator, judges off (no models here)."""
    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow, FullDummyFillOptions
    from hip_id_agent.models import RunContext

    async def run():
        with HipPortalSim(tmp_path / "cert") as sim:
            cfg = sim_session_config(tmp_path, sim)
            run_dir = tmp_path / "runs" / "R38_DM"
            (run_dir / "screenshots").mkdir(parents=True)
            ctx = RunContext(run_id="R38_DM", customer="SIM", partner_query="", system_query="", run_dir=run_dir,
                             screenshots_dir=run_dir / "screenshots")
            options = FullDummyFillOptions(
                phases=["data_map"], vision_verify=False, section_judge=False, require_text_judge=False,
                require_vision_judge=False, upload_assets_dir=str(tmp_path / "uploads"), portal_brain_enabled=False,
                runtime_self_heal_max_phase_attempts=2, continue_after_phase_block=True, qualify_models_on_first_page=False)
            await asyncio.wait_for(FullDummyFillE2EFlow(cfg, options).run(ctx, input_json=str(mission_input(tmp_path))), timeout=600)
            return run_dir

    run_dir = asyncio.run(run())
    gate = json.loads((run_dir / "mission_terminal_completion_gate.json").read_text())
    assert gate["pass"] is True, gate  # was blocked whenever the judges were off
    chat = [json.loads(line) for line in (run_dir / "agent_chat.jsonl").read_text().splitlines()]
    texts = [m["text"] for m in chat]
    assert any(t.startswith("✅ Data Map is complete") for t in texts)
    assert any(t.startswith("📜 Deterministic script for Data Map saved (candidate)") for t in texts)
    assert any("🏁 Mission complete" in t for t in texts)
    assert (run_dir / "data_map" / "deterministic_script.md").is_file()
    # The live map ends on the completed form, not on a mid-fill count.
    live = json.loads((run_dir / "data_map" / "input_json_live_map.json").read_text())
    assert live["complete"] is True and live["exact"] == live["total"]
    assert (tmp_path / "memory" / "deterministic_scripts" / "index.json").is_file()


def test_an_exact_live_form_is_not_blocked_by_a_disagreeing_coverage_report():
    """The Rule form was "filled and committed" (19/19 exact) and still reopened three times."""
    import inspect

    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow

    source = inspect.getsource(FullDummyFillE2EFlow.run)
    start = source.index("phase_acceptance_committed = bool(")
    gate = source[start:source.index("if evidence_incomplete and phase_acceptance_committed:", start)]
    assert "exact_authority = input_authority" in gate and 'exact_authority.get("pass")' in gate
    assert "phase_acceptance_committed = True" in gate
    # Each attempt starts without an earlier attempt's proof.
    assert "input_authority = {}  # V243R38" in source
