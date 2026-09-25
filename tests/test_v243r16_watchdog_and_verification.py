"""V243R16: a slow but advancing fill is not killed; a finished phase shows its verdict.

Live symptoms:

* Source Document Type filled up to Document Identifier, then
  HIP_PHASE_NO_PROGRESS_WATCHDOG cancelled the attempt and asked for review.
  The watchdog counted only new DOM states as progress, but retrying a DDS
  dropdown revisits known states and the model decisions around each action
  change nothing on screen.
* Data Map was complete and handed off, yet its Verification read
  "Pending • judge pass": the verification payload reports its verdict as
  ``status`` and the mission trace read a ``pass`` key that does not exist.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

import hip_id_agent.dds_control_driver as dds
from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
from hip_id_agent.mission_trace import MissionTraceLedger, read_mission_trace, verification_verdict
from hip_id_agent.phase_progress import PhaseNoProgressError, run_with_progress_watchdog
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph
from phase_replica_support import ROOT, attach_broker_session, chromium_path


def _markers(rows):
    it = iter(rows)
    last = {}

    async def provider():
        nonlocal last
        last = next(it, last)
        return dict(last)

    return provider


async def _sleep_forever():
    await asyncio.sleep(3600)


def test_executor_heartbeat_counts_as_progress_but_a_real_stall_still_trips():
    # Same DOM signature every sample, but the executor keeps starting new work.
    advancing = _markers([{"signature": "A", "executor_progress": f"1|node{i}|attempt|0"} for i in range(200)])

    async def finishes():
        await asyncio.sleep(0.6)
        return "done"

    assert asyncio.run(run_with_progress_watchdog(
        finishes(), phase="p", marker_provider=advancing, checkpoint_provider=lambda: {"pass": False},
        no_progress_seconds=0.2, poll_seconds=0.05,
    )) == "done"

    # A->B->A cycling with no new executor work is still a stall.
    cycling = _markers([{"signature": s, "executor_progress": "1|node0|attempt|0"} for s in "AB" * 200])
    with pytest.raises(PhaseNoProgressError) as info:
        asyncio.run(run_with_progress_watchdog(
            _sleep_forever(), phase="p", marker_provider=cycling, checkpoint_provider=lambda: {"pass": False},
            no_progress_seconds=0.2, poll_seconds=0.05,
        ))
    assert info.value.code == "HIP_PHASE_NO_PROGRESS_WATCHDOG"
    assert info.value.payload["executor_progress_units"] == 1


def test_new_successful_fills_count_as_progress():
    fills = _markers([{"signature": "A", "successful_fill_count": i} for i in range(200)])

    async def finishes():
        await asyncio.sleep(0.6)
        return "done"

    assert asyncio.run(run_with_progress_watchdog(
        finishes(), phase="p", marker_provider=fills, checkpoint_provider=lambda: {"pass": False},
        no_progress_seconds=0.2, poll_seconds=0.05,
    )) == "done"


@pytest.mark.parametrize("payload,expected", [
    ({"status": "pass"}, True),
    ({"status": "pass_with_warnings"}, True),
    ({"status": "failed"}, False),
    ({"pass": False, "status": "pass"}, False),
    ({"exact_completion_checkpoint_pass": True}, True),
    ({"status": ""}, None),
])
def test_verification_verdict_reads_the_status_the_payload_actually_has(payload, expected):
    assert verification_verdict(payload) is expected


def test_completed_phase_card_shows_its_verification_verdict(tmp_path: Path):
    trace = MissionTraceLedger(tmp_path, run_id="run-1", phases=["data_map"])
    trace.refresh_phase_artifacts("data_map", tmp_path / "data_map", verification={"phase": "data_map", "status": "pass"}, judge={"pass": True})
    state = read_mission_trace(tmp_path)
    steps = (state.get("trace") or state)["steps"]
    assert steps[0]["verification"]["pass"] is True
    assert steps[0]["judge"]["pass"] is True


def test_document_type_completes_under_the_live_watchdog_with_slow_model_decisions(tmp_path: Path, monkeypatch):
    # Every per-action model decision takes a second and Operation's options
    # arrive late, so Operation needs retries that revisit known DOM states.
    payload = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    obj = dict(payload["objects"]["source_document_type"])
    obj["attributes_to_configure"] = obj["attributes_to_configure"][:2]
    data = {"objects": {"source_document_type": obj}}
    real_gate = dds._autowebglm_primary_gate

    async def slow_gate(*args, **kwargs):
        await asyncio.sleep(1.0)
        return await real_gate(*args, **kwargs)

    monkeypatch.setattr(dds, "_autowebglm_primary_gate", slow_gate)

    async def run():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            html = (ROOT / "tests" / "fixtures" / "document_type_full_dds.html").read_text(encoding="utf-8").replace(
                "<script>", "<script>window.__deferOperationOptions = true; window.__attributeRows = 2;</script><script>", 1)
            await page.set_content(html)
            attach_broker_session(page, tmp_path, "source_document_type")
            session = page._hip_browser_session
            try:
                result = await run_with_progress_watchdog(
                    execute_autonomous_phase_goal(
                        page=page, graph=compile_phase_state_graph(data, "source_document_type"),
                        phase="source_document_type", input_data=data, config=None,
                        output_dir=tmp_path / "out", max_cycles=3, executor=execute_document_type_state_graph,
                    ),
                    phase="source_document_type",
                    marker_provider=lambda: session.capture_phase_progress_marker("source_document_type"),
                    checkpoint_provider=lambda: {"pass": False},
                    evidence_path=tmp_path / "watchdog.json", no_progress_seconds=25.0, poll_seconds=2.0,
                )
                filled = await page.evaluate("() => Array.from(document.querySelectorAll('input')).filter(e => e.value).map(e => e.placeholder)")
                return result, filled
            finally:
                await browser.close()

    result, filled = asyncio.run(run())
    assert result["status"] == "pass"
    assert {"Operation", "Derived From", "Value", "Validation Type"} <= set(filled)
