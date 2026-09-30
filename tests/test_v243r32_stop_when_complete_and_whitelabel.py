"""V243R32: stop filling once every input.json value is filled; a Whitelabel Error Page restarts the stage.

Request (2026-09-30): "It filled correctly but it didn't know when to stop -- it
kept on filling and filling and handling errors.  After filling everything it
should understand and stop filling.  Also there can be an issue called
Whitelabel Error; if that occurs, close the browser, reopen and start from the
stage."

Before R32 (reproduced on the Transport Profile replica with one portal-required
field input.json does not name): the goal engine ran every one of its cycles
(4 of 4), filling the complete form again each time, because refilling proven
fields counted as progress, then failed "needs_input" -- and the mission
reopened the form and filled it again.  The read-only input.json proof that
should have stopped it (R29) could not read Transport Profile, BizFlow, Rule or
Data Map forms at all: it looked for the form title as the fields' section and
dropped checked "No" radios.  After a correct fill, Transport Profile, BizFlow,
Rule and Data Map also switched every parent dropdown to its other values to
"explore" branches and then filled the whole form again.  A Whitelabel Error
Page was not recognised at all.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController

DOC = "source_document_type"
TP = "source_transport_profile"


def _need_browser():
    from phase_replica_support import chromium_path

    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")


# ------------------------------------------------------------ real browser
_EXTRA_REQUIRED = """() => {
  const anchor = document.querySelector('input[name=profileName]').closest('.dds__form-group');
  const g = window.HIP.text({label: 'Approval Reference *', name: 'approvalReference', placeholder: 'Approval Reference'});
  const i = g.querySelector('input'); i.required = true; i.setAttribute('aria-required', 'true');
  anchor.parentElement.appendChild(g);
}"""


def test_a_form_holding_every_input_json_value_stops_filling(tmp_path: Path):
    """The engine's own checks fail (a required portal field input.json does not name), the form is complete."""
    _need_browser()
    from playwright.async_api import async_playwright

    import hip_id_agent.autonomous_form_runtime as engine
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
    from phase_replica_support import chromium_path, replica_html, uhaul_input

    data = uhaul_input(TP, tmp_path)
    html = replica_html("transport_profile_full_dds.html").replace(
        "duplicateMessage: 'Transport Profile already exists in DEV environment.'", "")

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.set_content(html)
            await page.wait_for_timeout(300)
            await page.evaluate(_EXTRA_REQUIRED)
            try:
                first = await engine.execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(data, TP), phase=TP, input_data=data, config=None,
                    output_dir=tmp_path / "stop", max_cycles=4)
                # The same complete form with the stop switched off: refilling proven
                # fields is no longer progress, so the cycles end after two idle ones.
                original = engine._input_json_exact_now

                async def never_exact(*a: Any, **k: Any) -> Dict[str, Any]:
                    return {"pass": False, "status": "disabled_for_test"}

                engine._input_json_exact_now = never_exact
                try:
                    second = await engine.execute_autonomous_phase_goal(
                        page=page, graph=compile_phase_state_graph(data, TP), phase=TP, input_data=data, config=None,
                        output_dir=tmp_path / "no_stop", max_cycles=4)
                finally:
                    engine._input_json_exact_now = original
                return first, second
            finally:
                await browser.close()

    first, second = asyncio.run(run())
    # Stopped after one cycle: the form holds every input.json value.
    assert first["pass"] is True and first["completed_by"] == "input_json_exact_on_live_form", first.get("status")
    assert len(first["cycles"]) == 1 and first["stopped_filling"] is True
    assert first["cycles"][0]["unmet_success_checks"] == ["required_controls_without_input"]
    proof = first["input_json_completion_proof"]
    assert proof["pass"] is True and proof["matched_count"] == 14 and proof["missing_fields"] == []
    stage = first["final_execution"]["execution_stage_audit"]
    assert stage["verified_by"] == "input_json_live_read_only_proof" and first["final_execution"]["executor_pass"] is True
    assert first["skill"]["outcome"]["status"] == "not_saved"  # a skill is saved only from a fully checked run
    # Without the stop, refills are not progress: 3 cycles instead of all 4 (pre-R32).
    assert len(second["cycles"]) == 3 and second["pass"] is False
    assert [c["progress"]["newly_proven_fields"] > 0 for c in second["cycles"]] == [True, False, False]


def test_a_whitelabel_error_page_closes_the_browser_and_restarts_the_stage(tmp_path: Path):
    """Mid-fill the portal replaces the form with its Whitelabel Error Page; then the link itself answers with it."""
    _need_browser()
    from hip_id_agent.browser_session import BrowserSession
    from loader_portal_support import patch_navigation, real_session_config
    from test_v243r28_empty_dropdown_restart_browser import _phase_attempt
    from whitelabel_portal_support import WhitelabelPortal

    async def run():
        with WhitelabelPortal(armed_loads=[1], error_loads=[2]) as portal:
            session = BrowserSession(real_session_config(tmp_path), tmp_path / "run")
            await session.start()
            session._active_phase_name = DOC
            patch_navigation(session)
            heal = RuntimeSelfHealController(config=session.config, root_dir=tmp_path / "heal", browser=session, run_id="t")
            log: List[Dict[str, Any]] = []
            try:
                await session.goto_base_and_complete_sso(portal.url)
                for attempt in range(1, 6):
                    started = time.monotonic()
                    try:
                        result = await _phase_attempt(session, heal, portal.url, tmp_path / f"attempt{attempt}")
                        log.append({"attempt": attempt, "pass": result.get("pass")})
                        if result.get("pass"):
                            heal.finalize_phase(DOC, judge_pass=True)
                            break
                        raise RuntimeError(f"phase goal not met: {result.get('status')}")
                    except Exception as exc:
                        decision = await heal.handle_failure(phase=DOC, target_url=portal.url, attempt=attempt, message=str(exc))
                        log.append({"attempt": attempt, "error": str(exc), "class": decision.classification,
                                    "action": decision.action, "retry": decision.retry, "starts": session._start_count,
                                    "seconds": time.monotonic() - started})
                        if not decision.retry:
                            break
                page = await session._ensure_active_page(portal.url)
                state = await page.evaluate("""() => ({formLoad: window.__hipFormLoad, url: location.pathname,
                    empty: Array.from(document.querySelectorAll('dds-dropdown input')).filter(i => !i.value
                        && !i.closest('dds-dropdown').querySelector('.dds__tag')).map(i => i.placeholder)})""")
                return log, state, portal.loads, portal.error_pages
            finally:
                await session.close()

    log, state, loads, error_pages = asyncio.run(run())
    mid_fill, at_link, done = log
    # 1. Mid-fill: the watchdog saw the error page within seconds and the browser was closed and reopened.
    assert mid_fill["error"].startswith("HIP_WHITELABEL_ERROR_PAGE") and "status 500" in mid_fill["error"]
    assert mid_fill["class"] == "whitelabel_error_page" and mid_fill["action"] == "restart_browser_session"
    assert mid_fill["retry"] is True and mid_fill["starts"] == 2 and mid_fill["seconds"] < 60
    # 2. The same phase link answered with the error page: the form check saw it, restarted again.
    assert at_link["error"].startswith("HIP_WHITELABEL_ERROR_PAGE") and at_link["starts"] == 3
    assert at_link["action"] == "restart_browser_session" and at_link["retry"] is True
    # 3. The stage started again from its link, the form was opened and filled.
    assert done == {"attempt": 3, "pass": True}
    assert loads == 3 and error_pages == 2 and state["url"] == "/doctypes" and state["empty"] == []
    memory = json.loads((tmp_path / "memory" / "runtime_recovery_ladder.json").read_text(encoding="utf-8"))
    rows = {r["action"]: r for r in memory["ladders"][f"{DOC}|whitelabel"]}
    assert rows["restart_browser_session"]["resolved"] == 1


def test_whitelabel_detection_reads_springs_own_page():
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent.environment_faults import whitelabel_error_on
    from phase_replica_support import chromium_path
    from whitelabel_portal_support import WHITELABEL_HTML

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page()
            try:
                await page.set_content(WHITELABEL_HTML)
                found = await whitelabel_error_on(page)
                await page.set_content("<html><body><h1>Document Types</h1><p>An error page is not this.</p></body></html>")
                return found, await whitelabel_error_on(page)
            finally:
                await browser.close()

    found, normal = asyncio.run(run())
    assert found["status"] == "500" and found["type"] == "Internal Server Error"
    assert normal is None


# ------------------------------------------------------------- units
def test_the_live_proof_reads_form_level_fields_and_radio_groups():
    from hip_id_agent.phase_live_reproof import _form_level_facts_relaxed, _radio_group_answers

    controls = [
        {"label": "System Type *", "section": "Create Transport Profile", "value": "Dell Application"},
        {"label": "Profile Name *", "section": "Basic Details :", "value": "TP_1"},
        {"type": "radio", "group_label": "Use Existing Folder *", "group_name": "useExistingFolder", "label": "Yes",
         "checked": False, "value": "", "section": "Primary Interface Detail :"},
        {"type": "radio", "group_label": "Use Existing Folder *", "group_name": "useExistingFolder", "label": "No",
         "checked": True, "value": "false", "section": "Primary Interface Detail :"},
    ]
    groups = _radio_group_answers(controls)
    assert groups == [dict(groups[0], label="Use Existing Folder *", value="No", selected_values=["No"])]
    facts = [
        {"field": "system_type", "value": "Dell Application", "aliases": ["System Type"], "section": "Create Transport Profile"},
        {"field": "profile_name", "value": "TP_1", "aliases": ["Profile Name"], "section": "Create Transport Profile"},
        {"field": "attr", "value": "X", "aliases": ["Name"], "section": "Create Transport Profile", "row_kind": "tag", "row_index": 0},
    ]
    from hip_id_agent.section_judge import DualModelSectionJudge

    judge = DualModelSectionJudge()
    state = {"controls": [*controls, *groups]}
    strict = judge.deterministic_judge(expected={"facts": facts[:2]}, actual_state=state, attempts=[])
    assert [m["field"] for m in strict["missing_values"]] == ["profile_name"]  # the pre-R32 blind spot
    relaxed = _form_level_facts_relaxed({"facts": facts}, controls, strict)
    assert relaxed["facts"][1]["section"] == "" and relaxed["facts"][2]["section"] == "Create Transport Profile"
    again = judge.deterministic_judge(expected={"facts": relaxed["facts"][:2]}, actual_state=state, attempts=[])
    assert again["pass"] is True
    # The exact committed value is still required on the whole form.
    wrong = judge.deterministic_judge(expected={"facts": [dict(relaxed["facts"][1], value="TP_2")]}, actual_state=state, attempts=[])
    assert wrong["pass"] is False


def test_refilling_is_not_progress_the_watchdog_stops_a_complete_form():
    from hip_id_agent.phase_progress import PhaseNoProgressError, run_with_progress_watchdog

    ticks = {"n": 0}

    async def refilling_forever():
        while True:
            await asyncio.sleep(0.01)

    async def marker():
        ticks["n"] += 1  # a new screen and executor heartbeat every sample -- the old "progress"
        return {"signature": f"s{ticks['n']}", "executor_progress": f"t{ticks['n']}", "progress_units": 14,
                "successful_fill_count": ticks["n"]}

    probes: List[int] = []

    async def probe():
        probes.append(1)
        return {"pass": True, "status": "exact_live_state_reproved"}

    checked: List[int] = []

    async def checkpoint():
        checked.append(1)
        return {"pass": True}

    async def run():
        return await run_with_progress_watchdog(
            refilling_forever(), phase=TP, marker_provider=marker, checkpoint_provider=checkpoint,
            no_progress_seconds=30, poll_seconds=0.05, completion_probe=probe, refill_probe_seconds=0.2,
            refill_loop_seconds=30)

    started = time.monotonic()
    with pytest.raises(PhaseNoProgressError) as info:
        asyncio.run(run())
    assert info.value.code == "HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL"
    assert info.value.payload["stop_reason"] == "input_json_complete" and len(probes) == 2 and checked == [1]
    assert "stopped filling" in info.value.payload["message"] and time.monotonic() - started < 5


def test_a_refill_loop_on_an_incomplete_form_goes_to_recovery():
    from hip_id_agent.phase_progress import PhaseNoProgressError, run_with_progress_watchdog

    ticks = {"n": 0}

    async def refilling_forever():
        while True:
            await asyncio.sleep(0.01)

    async def marker():
        ticks["n"] += 1
        return {"signature": f"s{ticks['n']}", "executor_progress": f"t{ticks['n']}", "progress_units": 9}

    async def run():
        return await run_with_progress_watchdog(
            refilling_forever(), phase=TP, marker_provider=marker, checkpoint_provider=lambda: {"pass": False},
            no_progress_seconds=0.1, poll_seconds=0.05, completion_probe=lambda: {"pass": False, "status": "busy"},
            refill_probe_seconds=0.1, refill_loop_seconds=0.5)

    with pytest.raises(PhaseNoProgressError) as info:
        asyncio.run(run())
    assert info.value.code == "HIP_PHASE_NO_PROGRESS_WATCHDOG" and info.value.payload["stop_reason"] == "refill_loop"
    assert "refill loop" in info.value.payload["message"]


def test_new_fields_keep_an_attempt_running():
    from hip_id_agent.phase_progress import run_with_progress_watchdog

    ticks = {"n": 0}

    async def filling():
        await asyncio.sleep(0.6)
        return "done"

    async def marker():
        ticks["n"] += 1
        return {"signature": f"s{ticks['n']}", "progress_units": ticks["n"]}  # a new field every sample

    async def run():
        return await run_with_progress_watchdog(
            filling(), phase=TP, marker_provider=marker, checkpoint_provider=lambda: {"pass": True},
            no_progress_seconds=5, poll_seconds=0.05, completion_probe=lambda: {"pass": True},
            refill_probe_seconds=0.2, refill_loop_seconds=0.3)

    assert asyncio.run(run()) == "done"


def test_the_watchdog_ends_the_attempt_on_a_whitelabel_page():
    from hip_id_agent.phase_progress import PhaseNoProgressError, run_with_progress_watchdog

    async def filling():
        await asyncio.sleep(30)

    samples = {"n": 0}

    async def marker():
        samples["n"] += 1
        found = {"status": "500", "type": "Internal Server Error"} if samples["n"] > 2 else None
        return {"signature": f"s{samples['n']}", "progress_units": samples["n"], "whitelabel_error": found}

    async def run():
        return await run_with_progress_watchdog(
            filling(), phase=DOC, marker_provider=marker, checkpoint_provider=lambda: {"pass": True},
            no_progress_seconds=30, poll_seconds=0.05)

    started = time.monotonic()
    with pytest.raises(PhaseNoProgressError) as info:
        asyncio.run(run())
    assert info.value.code == "HIP_WHITELABEL_ERROR_PAGE" and time.monotonic() - started < 2
    assert str(info.value).startswith("HIP_WHITELABEL_ERROR_PAGE: the portal showed a Whitelabel Error Page")


class _FakeBrowser:
    def __init__(self, whitelabel: bool = True) -> None:
        self.restarts = 0
        self.gotos: List[str] = []
        self.page = SimpleNamespace(evaluate=self._evaluate, frames=[])
        self.whitelabel = whitelabel

    async def _evaluate(self, script: str, *args: Any) -> Any:
        if "whitelabel" not in script:
            return {"signature": "a page stub answers every script"}
        return {"whitelabel": True, "status": "500", "type": "Internal Server Error"} if self.whitelabel else None

    async def restart(self, *, reason: str = "") -> Dict[str, Any]:
        self.restarts += 1
        return {"restarted": True}

    async def goto_base_and_complete_sso(self, url: str = "") -> None:
        self.gotos.append(url)


def test_a_persistent_whitelabel_page_restarts_the_browser_three_times_then_asks_for_help(tmp_path: Path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    browser = _FakeBrowser()
    heal = RuntimeSelfHealController(config=cfg, root_dir=tmp_path / "heal", browser=browser, run_id="t")
    decisions = []

    async def run():
        for attempt in range(1, 6):
            # The error only says the page context was destroyed; the page shows the error page.
            decisions.append(await heal.handle_failure(
                phase=DOC, target_url="https://hip/doctypes", attempt=attempt,
                message="Execution context was destroyed, most likely because of a navigation"))
            if not decisions[-1].retry:
                break

    asyncio.run(run())
    assert [d.classification for d in decisions] == ["whitelabel_error_page"] * 4
    assert [d.action for d in decisions] == ["restart_browser_session"] * 3 + ["stop_fail_closed"]
    assert browser.restarts == 3 and browser.gotos == ["https://hip/doctypes"] * 3  # the same stage link each time
    assert decisions[-1].retry is False and decisions[-1].reason.startswith("HIP_WHITELABEL_ERROR_AFTER_RECOVERY")
    assert "closed and reopened the browser" in decisions[-1].reason


def test_whitelabel_is_classified_and_environment_fatal():
    from hip_id_agent.environment_faults import ENVIRONMENT_FATAL_CODES, is_environment_fatal, whitelabel_message
    from hip_id_agent.input_json_authority import NOT_ELIGIBLE_CLASSES

    message = whitelabel_message({"status": "500", "type": "Internal Server Error"}, "source_document_type form")
    assert "HIP_WHITELABEL_ERROR_PAGE" in ENVIRONMENT_FATAL_CODES and is_environment_fatal(RuntimeError(message))
    assert RuntimeSelfHealController.classify_failure(message) == "whitelabel_error_page"
    assert RuntimeSelfHealController.CLASS_ACTIONS["whitelabel_error_page"] == ("restart_browser_session",)
    assert "whitelabel_error_page" in NOT_ELIGIBLE_CLASSES  # the form is gone: no "is it complete?" proof
    # A mutation outcome stays a mutation question, whatever page followed it.
    assert RuntimeSelfHealController.classify_failure(
        "HIP_MUTATION_QUARANTINE_ACTIVE: save outcome unknown; " + message) == "unsafe_or_mutating"


def test_navigation_to_a_whitelabel_page_raises_the_recoverable_error(tmp_path: Path):
    from hip_id_agent.browser_session import BrowserSession

    session = BrowserSession(AppConfig(), tmp_path / "run")
    session.page = _FakeBrowser().page

    async def route_fails(url: str = "") -> None:
        raise RuntimeError("HIP_ROUTE_NOT_COMMITTED: the requested module is not the requested module")

    async def route_ok(url: str = "") -> None:
        return None

    async def run(route):
        session._goto_base_and_complete_sso_route = route
        try:
            await session.goto_base_and_complete_sso("https://hip/doctypes")
        except RuntimeError as exc:
            return str(exc)
        return ""

    assert asyncio.run(run(route_fails)).startswith("HIP_WHITELABEL_ERROR_PAGE")
    assert asyncio.run(run(route_ok)).startswith("HIP_WHITELABEL_ERROR_PAGE")
    session.page = _FakeBrowser(whitelabel=False).page
    assert asyncio.run(run(route_ok)) == ""
    assert asyncio.run(run(route_fails)).startswith("HIP_ROUTE_NOT_COMMITTED")


def test_operations_restart_the_browser_and_repeat_only_when_nothing_was_saved(tmp_path: Path):
    from hip_id_agent.portal_operations import PortalOperationRunner

    cfg = AppConfig()
    browser = _FakeBrowser()
    runner = PortalOperationRunner(cfg, browser, tmp_path / "ops")
    calls: List[str] = []

    async def run_one(spec, input_data, gate, *, index=1):
        calls.append(spec["target"])
        if spec["target"] == "TP_A" and calls.count("TP_A") == 1:
            raise RuntimeError("HIP_WHITELABEL_ERROR_PAGE: the portal showed a Whitelabel Error Page at listing")
        if spec["target"] == "TP_B":
            runner._commit_clicked = True  # Save was clicked, then the error page came
            raise RuntimeError("HIP_WHITELABEL_ERROR_PAGE: the portal showed a Whitelabel Error Page after Save")
        return {"phase": spec["phase"], "operation": spec["operation"], "target": spec["target"], "pass": True,
                "status": "committed_and_verified", "result": "UPDATED"}

    runner.run_one = run_one
    ops = {"operations": [{"phase": TP, "operation": "edit", "target": "TP_A", "values": {}},
                          {"phase": TP, "operation": "edit", "target": "TP_B", "values": {}}]}
    report = asyncio.run(runner.run(ops))
    first, second = report["operations"]
    assert first["pass"] is True and first["whitelabel_recoveries"][0]["restarted"] is True
    assert calls == ["TP_A", "TP_A", "TP_B"] and browser.restarts == 1  # TP_B was not repeated
    assert second["status"] == "whitelabel_error_page" and second["whitelabel_after_commit"] is True
    assert second["environment_fault"] is True and report["pass"] is False

    # The error only says the page context was destroyed; the page shows the error page.
    calls.clear()

    async def context_destroyed(spec, input_data, gate, *, index=1):
        calls.append(spec["target"])
        if len(calls) == 1:
            raise RuntimeError("Execution context was destroyed, most likely because of a navigation")
        browser.whitelabel = False
        return {"phase": spec["phase"], "operation": spec["operation"], "target": spec["target"], "pass": True}

    runner.run_one = context_destroyed
    report = asyncio.run(runner.run({"operations": [ops["operations"][0]]}))
    assert report["pass"] is True and calls == ["TP_A", "TP_A"] and browser.restarts == 2


def test_no_branch_exploration_changes_a_filled_form_by_default(monkeypatch):
    from hip_id_agent import bizflow_kb, datamap_kb, rules_kb, transport_profile_kb
    from hip_id_agent.portal_form_exploration import explore_after_fill, form_changed_by_exploration, recorded_dropdowns

    cfg = AppConfig()
    assert cfg.exploration.explore_branches_after_fill is False and explore_after_fill(cfg) is False
    monkeypatch.setenv("HIP_EXPLORE_BRANCHES_AFTER_FILL", "true")
    assert explore_after_fill(cfg) is True
    monkeypatch.delenv("HIP_EXPLORE_BRANCHES_AFTER_FILL")
    cfg.exploration.explore_branches_after_fill = True
    assert explore_after_fill(cfg) is True
    assert form_changed_by_exploration({"completeness": {"parent_values_live_explored": 0}}) is False
    assert form_changed_by_exploration({"completeness": {"parent_values_live_explored": 3}}) is True
    assert recorded_dropdowns([{"label": "Usage", "options": [{"text": "Sender"}, "Receiver"]}, {"label": "Name"}]) == [
        {"label": "Usage", "selector": None, "kind": "select", "options": ["Sender", "Receiver"], "source": "captured_without_opening"}]
    for module in (transport_profile_kb, rules_kb, datamap_kb, bizflow_kb):
        source = inspect.getsource(module)
        assert "allow_live_branching=True" not in source, module.__name__
        assert "form_changed_by_exploration(" in source, module.__name__


def test_the_mission_stops_filling_and_restarts_the_stage_on_a_whitelabel_page():
    from hip_id_agent import dummy_fill_e2e

    source = inspect.getsource(dummy_fill_e2e)
    # The pre-judge gate proves the live form before the phase is reopened and refilled.
    assert 'reason=f"attempt_{attempt_no}_pre_judge_gate"' in source
    # The running attempt is watched for "complete" and for refill loops.
    assert "completion_probe=lambda: _quiet_completion_probe(phase, input_path)" in source
    assert "refill_loop_seconds=" in source
    # A Whitelabel Error Page is recognised from the page and never treated as a finished form.
    assert 'classification = "whitelabel_error_page"' in source
    assert 'and classification != "whitelabel_error_page"' in source
    assert '"HIP_WHITELABEL_ERROR_AFTER_RECOVERY"' in source
    cfg = AppConfig()
    assert cfg.runtime_self_heal.refill_probe_seconds == 120 and cfg.runtime_self_heal.refill_loop_seconds == 600
    assert cfg.runtime_self_heal.whitelabel_browser_restarts == 3
    assert cfg.autonomous_form.stop_when_input_json_exact is True
