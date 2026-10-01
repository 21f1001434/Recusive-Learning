"""V243R34: WebMCP removed; learning tiles show the real state; the live input.json map.

Request (2026-10-01), with a Control Center screenshot: "There is no WebMCP for
the HIP Portal so remove it.  Why is everything off and not learning and
improving itself?  Also, is it mapping the HIP portal input field values to the
input.json so it knows when to stop when everything is filled correctly -- and
once it knows everything it can see what is happening in real time too, to make
it complete fast and excellently working."

Before R34: one failing part of the runtime status (the R33 WebMCP summary
scanned the run folders unguarded) blanked the whole status -- every learning
tile then read "Off ... disabled", AutoGen "blocked", although every feature was
switched on.  The input.json-to-form mapping existed only inside the completion
proof: nobody could watch it.  After a correct first pass the goal engine filled
the whole form a second time; a page without the DOM observer waited the full
timeout after every dropdown.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

TP = "source_transport_profile"


def _need_browser():
    from phase_replica_support import chromium_path

    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")


# ------------------------------------------------------------- WebMCP removed
def test_webmcp_is_removed_everywhere():
    import importlib.util

    from hip_id_agent import autonomous_form_runtime, browser_session, cli
    from hip_id_agent.config import AppConfig

    assert importlib.util.find_spec("hip_id_agent.webmcp") is None
    assert not hasattr(AppConfig(), "webmcp")
    assert "webmcp" not in inspect.getsource(autonomous_form_runtime).lower()
    assert "webmcp" not in inspect.getsource(browser_session).lower()
    assert not hasattr(cli, "webmcp_tools_cmd")
    from fastapi.testclient import TestClient

    from backend.app import app

    assert TestClient(app).get("/api/webmcp").status_code == 404
    root = Path(__file__).resolve().parents[1]
    for ui in ("webui", "backend/webui"):
        assert "webmcp" not in (root / ui / "index.html").read_text(encoding="utf-8").lower()
        assert "webmcp" not in (root / ui / "app.js").read_text(encoding="utf-8").lower()
    assert "webmcp" not in (root / "config.yaml").read_text(encoding="utf-8").lower()


# ------------------------------------------------------------- status
def test_one_failing_status_part_no_longer_turns_every_tile_off(monkeypatch):
    from fastapi.testclient import TestClient

    import backend.app as backend

    client = TestClient(backend.app)
    backend._RUNTIME_STATUS_LAST.clear()
    healthy = client.get("/api/runtime/status").json()
    assert healthy["section_errors"] == {} and not healthy.get("degraded")
    for part in ("skill_induction", "replay_policy", "model_portfolio", "recursive_self_improvement", "mlflow",
                 "run_history_learning", "edit_sections", "autogen"):
        assert part in healthy, part
    assert healthy["skill_induction"]["enabled"] is True and healthy["replay_policy"]["enabled"] is True
    assert healthy["model_portfolio"]["enabled"] is True and healthy["recursive_self_improvement"]["enabled"] is True

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("[WinError 3] The system cannot find the path specified: 'runs\\\\R\\\\very\\\\deep'")

    monkeypatch.setattr(backend, "mlflow_runtime_probe", boom)
    monkeypatch.setattr(backend, "_run_history_summary", boom)
    backend._RUNTIME_STATUS_LAST.clear()
    status = client.get("/api/runtime/status").json()
    assert status["degraded"] is True and set(status["section_errors"]) == {"mlflow", "run_history_learning"}
    assert status["mlflow"]["status_error"].startswith("OSError: [WinError 3]")
    # Everything else is still reported -- the tiles show the real state.
    assert status["skill_induction"]["enabled"] is True and status["model_portfolio"]["enabled"] is True
    assert status["recursive_self_improvement"]["enabled"] is True and status["autogen"]
    log = json.loads((backend.ROOT / ".backend_runtime" / "runtime_status_errors.json").read_text(encoding="utf-8"))
    assert "Traceback" in log["mlflow"]["traceback"]
    root = Path(__file__).resolve().parents[1]
    for ui in ("webui", "backend/webui"):
        js = (root / ui / "app.js").read_text(encoding="utf-8")
        assert "status part failed" in js and "runtime.section_errors" in js and "AutoGen status unavailable" in js


# ------------------------------------------------------------- live map (real browser)
def test_the_live_input_json_map_shows_every_value_on_the_form_as_it_fills(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
    from hip_id_agent.input_json_authority import quiet_completion_probe
    from hip_id_agent.phase_live_reproof import live_input_field_map
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
    from phase_replica_support import chromium_path, replica_html, uhaul_input

    data = uhaul_input(TP, tmp_path)
    html = replica_html("transport_profile_full_dds.html").replace(
        "duplicateMessage: 'Transport Profile already exists in DEV environment.'", "")

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            try:
                await page.set_content(html)
                await page.wait_for_timeout(300)
                before = await live_input_field_map(page=page, phase=TP, phase_input=data)
                await page.fill("input[name=profileName]", "SOMETHING_ELSE")
                wrong = await live_input_field_map(page=page, phase=TP, phase_input=data)
                started = time.monotonic()
                result = await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(data, TP), phase=TP, input_data=data, config=None,
                    output_dir=tmp_path / "out", max_cycles=2)
                seconds = time.monotonic() - started
                after = await live_input_field_map(page=page, phase=TP, phase_input=data)
                probe = await quiet_completion_probe(page=page, phase=TP, phase_input=data)
                return before, wrong, result, seconds, after, probe
            finally:
                await browser.close()

    before, wrong, result, seconds, after, probe = asyncio.run(run())
    assert (before["total"], before["exact"], before["not_on_screen"], before["complete"]) == (14, 0, 14, False)
    name = next(r for r in wrong["rows"] if r["field"] == "profile_name")
    assert name["status"] == "different" and name["live"] == "SOMETHING_ELSE" and name["expected"] == "SFTP_U-HAUL_ASN_PC_SRC_IB"
    assert result["pass"] is True
    assert (after["total"], after["exact"], after["complete"]) == (14, 14, True)
    rows = {r["field"]: r for r in after["rows"]}
    # Mapped onto the form's own labels; the portal's spelling of a value counts as exact.
    assert rows["partner_name"]["label"] == "System Name" and rows["profile_name"]["label"] == "Profile Name"
    assert (rows["post_transfer_action"]["expected"], rows["post_transfer_action"]["live"]) == ("Move to Archive", "Move To Archive")
    assert rows["existing_account"]["live"] == "Yes" and rows["use_existing_folder"]["live"] == "No"
    assert rows["is_compression_required"]["status"] == "not_checked"  # "FALSE": nothing to prove
    assert probe["pass"] is True and probe["status"] == "complete" and probe["map"]["exact"] == 14
    # One pass: the live proof replaced the second full fill; the fill no longer waits
    # for a DOM observer the page does not have.
    cycle = result["cycles"][0]
    assert cycle["single_pass_input_json_proof"]["pass"] is True
    assert cycle["full_goal_execution"]["single_pass_proved_by_input_json"] is True
    assert result["execution_mode"] == "adaptive_learning" and len(result["cycles"]) == 1
    assert cycle["stage_seconds"]["verify_pass"] < 3
    assert seconds < 60  # was ~95 s


def test_no_dom_observer_means_no_waiting_for_one(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent.stateful_form_runtime import _wait_for_dom_transition_activity
    from phase_replica_support import chromium_path

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page()
            try:
                await page.set_content("<html><body><input></body></html>")
                started = time.monotonic()
                bare = await _wait_for_dom_transition_activity(page, {"event_seq": 0, "mutation_seq": 0}, timeout_ms=3000)
                bare_seconds = time.monotonic() - started
                await page.evaluate("window.__HIP_DOM_MUTATION_LOG = []; window.__HIP_DOM_EVENT_LOG = [];")
                started = time.monotonic()
                await _wait_for_dom_transition_activity(page, {"event_seq": 0, "mutation_seq": 0}, timeout_ms=600)
                return bare, bare_seconds, time.monotonic() - started
            finally:
                await browser.close()

    bare, bare_seconds, observed_seconds = asyncio.run(run())
    assert bare["observer"] is False and bare_seconds < 0.5
    assert observed_seconds >= 0.5  # with an observer it still waits for the change


# ------------------------------------------------------------- watchdog
def _marker_factory(state: Dict[str, int]):
    async def marker():
        state["n"] += 1
        return {"signature": f"s{state['n']}", "executor_progress": f"t{state['n']}", "progress_units": state.get("units", 14),
                "successful_fill_count": state.get("fills", 0) + (state["n"] if state.get("refilling") else 0)}
    return marker


def test_the_live_map_refreshes_on_its_own_cadence_and_a_refill_after_complete_is_stopped():
    from hip_id_agent.phase_progress import PhaseNoProgressError, run_with_progress_watchdog

    async def refilling():
        while True:
            await asyncio.sleep(0.01)

    probes: List[float] = []

    async def probe():
        probes.append(time.monotonic())
        return {"pass": True, "status": "complete"}

    async def run():
        return await run_with_progress_watchdog(
            refilling(), phase=TP, marker_provider=_marker_factory({"n": 0, "refilling": 1}),
            checkpoint_provider=lambda: {"pass": True}, no_progress_seconds=60, poll_seconds=0.05,
            completion_probe=probe, refill_probe_seconds=60, refill_loop_seconds=60,
            live_map_seconds=0.1, post_complete_fill_seconds=0.4)

    started = time.monotonic()
    with pytest.raises(PhaseNoProgressError) as info:
        asyncio.run(run())
    assert info.value.code == "HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL"
    assert info.value.payload["stop_reason"] == "filled_again_after_complete"
    assert len(probes) >= 3 and time.monotonic() - started < 5


def test_a_complete_form_that_is_only_finishing_its_checks_is_not_interrupted():
    from hip_id_agent.phase_progress import run_with_progress_watchdog

    async def finishing():
        await asyncio.sleep(0.8)  # read-back, evidence, learning -- no more fills
        return "finished"

    probes: List[float] = []

    async def probe():
        probes.append(time.monotonic())
        return {"pass": True, "status": "complete"}

    async def run():
        return await run_with_progress_watchdog(
            finishing(), phase=TP, marker_provider=_marker_factory({"n": 0, "fills": 14}),
            checkpoint_provider=lambda: {"pass": True}, no_progress_seconds=60, poll_seconds=0.05,
            completion_probe=probe, refill_probe_seconds=60, refill_loop_seconds=60,
            live_map_seconds=0.1, post_complete_fill_seconds=0.2)

    assert asyncio.run(run()) == "finished" and len(probes) >= 3


# ------------------------------------------------------------- mission, backend, UI
def test_the_mission_writes_the_live_map_and_the_control_center_shows_it(tmp_path: Path):
    from fastapi.testclient import TestClient

    from backend.app import app
    from hip_id_agent import dummy_fill_e2e
    from hip_id_agent.config import AppConfig

    source = inspect.getsource(dummy_fill_e2e)
    assert 'safe_write_json(root_dir / "input_json_live_map.json", record)' in source
    assert "live_map_seconds=float(" in source and "post_complete_fill_seconds=float(" in source
    cfg = AppConfig()
    assert cfg.runtime_self_heal.live_map_seconds == 5.0 and cfg.runtime_self_heal.post_complete_fill_seconds == 30.0
    assert cfg.autonomous_form.single_pass_when_input_json_exact is True

    run = tmp_path / "runs" / "HIP-DUMMY-FILL-1"
    run.mkdir(parents=True)
    (run / "mission_trace.json").write_text(json.dumps({"run_id": run.name}), encoding="utf-8")
    record = {"schema_version": "hip.live-input-json-map.v1", "phase": TP, "phase_display": "Source Transport Profile",
              "total": 2, "exact": 1, "different": 1, "complete": False,
              "rows": [{"field": "profile_name", "label": "Profile Name", "expected": "A", "live": "A", "status": "exact"},
                       {"field": "profile_usage", "label": "Profile Usage", "expected": "Sender", "live": "Receiver", "status": "different"}]}
    (run / "input_json_live_map.json").write_text(json.dumps(record), encoding="utf-8")
    payload = TestClient(app).get("/api/mission/live-input-map", params={"runs_dir": str(tmp_path / "runs")}).json()
    assert payload["found"] is True and payload["map"]["exact"] == 1 and payload["map"]["rows"][1]["status"] == "different"
    root = Path(__file__).resolve().parents[1]
    for ui in ("webui", "backend/webui"):
        html = (root / ui / "index.html").read_text(encoding="utf-8")
        js = (root / ui / "app.js").read_text(encoding="utf-8")
        assert 'id="liveInputMapRows"' in html and "Live input.json ↔ HIP form" in html
        assert "/api/mission/live-input-map" in js and "renderLiveInputMap(liveInputMapPayload)" in js
