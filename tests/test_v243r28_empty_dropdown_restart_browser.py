"""V243R28: a dropdown with no values -> close and reopen the browser, same page, open the form, fill it.

Live report (2026-09-29): the first run filled everything correctly; in the
second run a dropdown (Data Format Type in the screenshot) showed no value or
only some text. It can happen at any phase. The recovery that works is to
close the browser, reopen it, go to the exact same link (Document Type,
Transport Profile, ...), open the form as usual and fill it.

Before R28 (reproduced on the replica, whose lists break from the second form
load of a browser session until the browser is closed): every attempt took
~2.8 minutes, failed as "control did not reach a stable exact expected value"
(classified ``exact_value_mismatch``) with every field below as "dependency
failed", and recovery only re-opened the form in the same browser (twice), then
refreshed it -- never closing the browser. The driver also typed search text
into the empty list.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController

PHASE = "source_document_type"


# --------------------------------------------------------------- real browser
def _doc_data(rows: int = 1) -> Dict[str, Any]:
    from phase_replica_support import ROOT

    payload = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    obj = dict(payload["objects"][PHASE])
    obj["attributes_to_configure"] = obj["attributes_to_configure"][:rows]
    return {"objects": {PHASE: obj}}


async def _phase_attempt(session, heal, url: str, out: Path, *, no_progress: float = 60.0) -> Dict[str, Any]:
    from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
    from hip_id_agent.phase_progress import run_with_progress_watchdog
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph

    data = _doc_data()
    page = await session._ensure_active_page(url)
    return await run_with_progress_watchdog(
        execute_autonomous_phase_goal(
            page=page, graph=compile_phase_state_graph(data, PHASE), phase=PHASE, input_data=data, config=None,
            output_dir=out, max_cycles=2, executor=execute_document_type_state_graph),
        phase=PHASE, marker_provider=lambda: session.capture_phase_progress_marker(PHASE),
        checkpoint_provider=lambda: {"pass": False}, no_progress_seconds=no_progress, poll_seconds=0.5,
        blocking_wait_seconds=heal.watchdog_blocking_wait_seconds())


def _need_browser():
    from phase_replica_support import chromium_path

    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")


def test_second_run_with_empty_dropdowns_closes_and_reopens_the_browser_then_fills(tmp_path: Path):
    """Run 1 fills; run 2 (same browser) meets "No data found"; the mission-style loop recovers."""
    _need_browser()
    from hip_id_agent.browser_session import BrowserSession
    from loader_portal_support import LoaderPortal, patch_navigation, real_session_config

    async def run():
        with LoaderPortal(stuck_loads=0, attribute_rows=1) as portal:
            portal.html = portal.html.replace("<script>", "<script>window.__lookupsBrokenFromLoad = 2;</script><script>", 1)
            session = BrowserSession(real_session_config(tmp_path), tmp_path / "run")
            await session.start()
            session._active_phase_name = PHASE
            patch_navigation(session)
            heal = RuntimeSelfHealController(config=session.config, root_dir=tmp_path / "heal", browser=session, run_id="t")
            log: List[Dict[str, Any]] = []
            try:
                await session.goto_base_and_complete_sso(portal.url)
                first = await _phase_attempt(session, heal, portal.url, tmp_path / "run1")
                log.append({"run": 1, "pass": first.get("pass")})
                # The second run opens the form again in the same browser.
                await session.goto_base_and_complete_sso(portal.url)
                for attempt in range(1, 4):
                    started = time.monotonic()
                    try:
                        result = await _phase_attempt(session, heal, portal.url, tmp_path / f"run2_{attempt}")
                        log.append({"run": 2, "attempt": attempt, "pass": result.get("pass")})
                        if result.get("pass"):
                            heal.finalize_phase(PHASE, judge_pass=True)
                            break
                        raise RuntimeError(f"phase goal not met: {result.get('status')}")
                    except Exception as exc:
                        decision = await heal.handle_failure(phase=PHASE, target_url=portal.url, attempt=attempt, message=str(exc))
                        log.append({"run": 2, "attempt": attempt, "error": str(exc), "seconds": time.monotonic() - started,
                                    "class": decision.classification, "action": decision.action, "retry": decision.retry,
                                    "starts": session._start_count})
                        if not decision.retry:
                            break
                page = await session._ensure_active_page(portal.url)
                state = await page.evaluate("""() => ({formLoad: window.__hipFormLoad, noData: window.__hipNoDataOpens,
                    empty: Array.from(document.querySelectorAll('dds-dropdown input')).filter(i => !i.value
                        && !i.closest('dds-dropdown').querySelector('.dds__tag')).map(i => i.placeholder)})""")
                return log, state, portal.loads
            finally:
                await session.close()

    log, state, loads = asyncio.run(run())
    assert log[0] == {"run": 1, "pass": True}
    failure = log[1]
    assert failure["error"].startswith("HIP_DROPDOWN_OPTIONS_EMPTY") and "'Data Format Type'" in failure["error"]
    assert "No data found" in failure["error"] and failure["seconds"] < 120  # was ~170-230 s per attempt
    assert failure["class"] == "dropdown_options_empty" and failure["action"] == "restart_browser_session"
    assert failure["retry"] is True and failure["starts"] == 2  # the browser was closed and started again
    assert log[2] == {"run": 2, "attempt": 2, "pass": True}
    # A new browser session (its first form load), every dropdown filled, no empty list.
    assert state["formLoad"] == 1 and state["noData"] == 0 and state["empty"] == []
    memory = json.loads((tmp_path / "memory" / "runtime_recovery_ladder.json").read_text(encoding="utf-8"))
    rows = {r["action"]: r for r in memory["ladders"][f"{PHASE}|lists"]}
    assert rows["restart_browser_session"]["resolved"] == 1  # learned: the restart resolved it


def test_a_slow_list_is_waited_for_and_not_treated_as_empty(tmp_path: Path):
    """Data Format Type shows "No data found" on its first 3 openings (a slow lookup), then its values."""
    _need_browser()
    from hip_id_agent.browser_session import BrowserSession
    from loader_portal_support import LoaderPortal, patch_navigation, real_session_config

    async def run():
        with LoaderPortal(stuck_loads=0, attribute_rows=1) as portal:
            portal.html = portal.html.replace(
                "<script>", "<script>window.__formatEmptyOpens = 3;</script><script>", 1)
            session = BrowserSession(real_session_config(tmp_path), tmp_path / "run")
            await session.start()
            session._active_phase_name = PHASE
            patch_navigation(session)
            heal = RuntimeSelfHealController(config=session.config, root_dir=tmp_path / "heal", browser=session, run_id="t")
            try:
                await session.goto_base_and_complete_sso(portal.url)
                result = await _phase_attempt(session, heal, portal.url, tmp_path / "out")
                page = await session._ensure_active_page(portal.url)
                return result, await page.evaluate("() => window.__hipOpenedEmpty"), session._start_count
            finally:
                await session.close()

    result, opened_empty, starts = asyncio.run(run())
    assert result["pass"] is True and starts == 1  # no restart for a list that was only slow
    assert opened_empty == 3  # it really opened empty three times before its values came


def test_any_phase_transport_profile_empty_lists_end_the_attempt_without_typing_into_them(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
    from phase_replica_support import chromium_path, replica_html, uhaul_input

    phase = "source_transport_profile"
    data = uhaul_input(phase, tmp_path)

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.set_content(replica_html("transport_profile_full_dds.html", "window.__hipLookupsBrokenNow = true;"))
            page._hip_empty_options_confirm_seconds = 2.0
            started = time.monotonic()
            try:
                await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(data, phase), phase=phase, input_data=data, config=None,
                    output_dir=tmp_path / "out", max_cycles=2)
                return {"raised": False}
            except RuntimeError as exc:
                typed = await page.evaluate("""() => Array.from(document.querySelectorAll('dds-dropdown input'))
                    .map(i => i.value).filter(Boolean)""")
                return {"raised": True, "error": str(exc), "seconds": time.monotonic() - started, "typed": typed,
                        "no_data": await page.evaluate("() => window.__hipNoDataOpens")}
            finally:
                await browser.close()

    out = asyncio.run(run())
    assert out["raised"] is True and out["error"].startswith("HIP_DROPDOWN_OPTIONS_EMPTY: phase=source_transport_profile")
    assert out["no_data"] >= 2 and out["seconds"] < 60
    assert out["typed"] == []  # no search text left in an empty list


# ------------------------------------------------------------- units
def test_empty_list_detection():
    from hip_id_agent.dds_control_driver import snapshot_options_empty

    assert snapshot_options_empty({"found": True, "options": [], "no_data": True, "empty_text": "No data found"})
    assert snapshot_options_empty({"found": True, "options": [], "expanded": True, "empty_text": ""})
    assert not snapshot_options_empty({"found": True, "options": [], "list_loading": True, "empty_text": "Loading…"})
    assert not snapshot_options_empty({"found": True, "options": [{"text": "XML"}]})
    assert not snapshot_options_empty({"found": False, "options": []})


def test_the_confirmation_window_waits_for_slow_and_loading_lists(monkeypatch):
    import hip_id_agent.dds_control_driver as d

    async def no_close(page, phase=""):
        return None

    monkeypatch.setattr(d, "close_open_dropdown", no_close)

    class _Page:
        _hip_empty_options_confirm_seconds = 0.6

        async def wait_for_timeout(self, ms):
            await asyncio.sleep(ms / 1000.0)

    empty = {"found": True, "options": [], "no_data": True, "empty_text": "No data found"}
    loading = {"found": True, "options": [], "list_loading": True, "empty_text": "Loading..."}
    filled = {"found": True, "options": [{"text": "XML"}]}

    def run(sequence):
        seq = list(sequence)

        async def reopen():
            return seq.pop(0) if len(seq) > 1 else seq[0]

        async def snap():
            return seq[0]

        return asyncio.run(d._confirm_options_empty(_Page(), "#x", snap, reopen))

    slow = run([empty, filled])
    assert slow["empty"] is False and slow["snapshot"]["options"]  # the lookup returned in time: used normally
    stuck = run([empty])
    assert stuck["empty"] is True and stuck["empty_text"] == "No data found" and stuck["opens"] >= 2
    still_loading = run([loading])
    assert still_loading["empty"] is True and still_loading["extended_for_loading"] is True
    assert still_loading["waited_seconds"] >= 1.1  # a list still loading got a second window


def test_a_dropdown_below_an_unfilled_field_is_not_a_broken_portal():
    from hip_id_agent.stateful_form_runtime import _raise_if_dropdown_options_empty

    graph = {"nodes": [{"node_id": "p", "action": "select_single"}, {"node_id": "c", "action": "select_single", "depends_on": ["p"]}]}
    node = graph["nodes"][1]
    # The parent is not filled yet: an empty child list is expected; nothing is raised.
    asyncio.run(_raise_if_dropdown_options_empty(
        None, graph, node, audit={"options_empty": True}, phase=PHASE,
        node_status={"p": False}, node_by_id={"p": graph["nodes"][0], "c": node}, document_type=True))


def test_classification_ladder_and_fatal_code():
    from hip_id_agent.environment_faults import ENVIRONMENT_FATAL_CODES

    msg = ("HIP_DROPDOWN_OPTIONS_EMPTY: phase=source_document_type; the 'Usage' (row 0) dropdown opened with no values "
           "(the portal showed 'No data found'); value mismatch; the browser session")
    assert RuntimeSelfHealController.classify_failure(msg) == "dropdown_options_empty"
    assert RuntimeSelfHealController.CLASS_ACTIONS["dropdown_options_empty"] == ("restart_browser_session",)
    assert "HIP_DROPDOWN_OPTIONS_EMPTY" in ENVIRONMENT_FATAL_CODES
    assert AppConfig().runtime_self_heal.empty_options_browser_restarts == 2


class _RestartableBrowser:
    """The fake browser of the self-heal loop tests, with a browser restart."""

    def __init__(self):
        from test_runtime_self_heal_loop import _Browser

        self._inner = _Browser()
        self.restarts = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def restart(self, *, reason=""):
        self.restarts += 1
        self._inner.calls.append("restart")
        return {"mode": "relaunched_same_browser_and_profile"}


def test_the_browser_is_closed_and_reopened_at_most_twice_then_the_phase_is_held(tmp_path: Path):
    from test_runtime_self_heal_loop import TARGET

    cfg = AppConfig()
    cfg.aia.enabled = False
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    browser = _RestartableBrowser()
    heal = RuntimeSelfHealController(config=cfg, root_dir=tmp_path, browser=browser, run_id="t")
    message = "HIP_DROPDOWN_OPTIONS_EMPTY: phase=rule; the 'Condition' dropdown opened with no values"

    async def run():
        return [await heal.handle_failure(phase="rule", target_url=TARGET, attempt=a, message=message) for a in (1, 2, 3)]

    decisions = asyncio.run(run())
    assert [d.action for d in decisions] == ["restart_browser_session", "restart_browser_session", "stop_fail_closed"]
    assert [d.retry for d in decisions] == [True, True, False]
    assert browser.restarts == 2
    # After each restart the same phase link is opened again.
    calls = browser._inner.calls
    assert calls.count("goto") >= 2 and calls.index("restart") < calls.index("goto")
    assert decisions[-1].reason.startswith("HIP_DROPDOWN_OPTIONS_EMPTY_AFTER_RECOVERY")
    assert "closed and reopened the browser" in decisions[-1].reason
    # Another phase gets its own restarts ("it can happen at any stage").
    other = asyncio.run(heal.handle_failure(phase="data_map", target_url=TARGET, attempt=1, message=message))
    assert other.action == "restart_browser_session" and other.retry is True


def test_the_mission_holds_with_the_recovery_summary():
    import inspect

    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow

    source = inspect.getsource(FullDummyFillE2EFlow.run)
    assert '"HIP_DROPDOWN_OPTIONS_EMPTY_AFTER_RECOVERY"' in source
    assert 'decision.action == "restart_browser_session"' in source  # shown as automatic recovery in the live view
