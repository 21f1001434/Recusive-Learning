"""V243R13: a correct Data Map must not loop on "Looks correct".

Live symptom (run UHAUL-POASN-20260924-181014): exact checkpoint PASS, but the
section judge reported Status {"expected": "Enabled", "actual_candidates": []}.
Clicking Looks correct only re-checked the phase, so attempts kept increasing
and the same review reappeared.

Causes: a DDS switch's DOM value is "on" whether checked or not, the judge's
live capture ignored role=switch, and a recovery "Looks correct" was always
rewritten to "recheck_live_phase" even for an exact-completed phase.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.autonomous_form_runtime import autonomous_target_execution, execute_autonomous_phase_goal
from hip_id_agent.config import AppConfig
from hip_id_agent.dummy_fill_e2e import _artifact_actual_state, accepted_human_override
from hip_id_agent.human_phase_review import HumanPhaseReviewStore
from hip_id_agent.section_judge import (
    DualModelSectionJudge,
    _reconcile_text_judge,
    _reconcile_vision_judge,
    _values_equal,
    build_phase_expectation,
)
from hip_id_agent.stateful_form_runtime import attempt_actual_value, compile_phase_state_graph

INPUT = {"objects": {"data_map": {
    "map_identifier": "DELLCoXMLASNXX08C_U-HAUL", "map_identifier_version": "1", "status": "Enable",
    "map_name": "DELLCoXMLASNXX08C", "map_class": "Transform_DELLCoXMLASNXX08C",
}}}


def _chromium_path() -> str | None:
    for candidate in (os.environ.get("HIP_TEST_CHROMIUM"), "/opt/pw-browsers/chromium", "/usr/bin/chromium"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _status_node() -> dict:
    graph = compile_phase_state_graph(INPUT, "data_map")
    return next(n for n in graph["nodes"] if n["field_key"] == "status")


def test_switch_evidence_records_checked_state_not_dom_value():
    node = _status_node()
    assert attempt_actual_value(node, {"role": "switch", "type": "checkbox", "checked": True, "value": "on"}) == "Enabled"
    assert attempt_actual_value(node, {"role": "switch", "type": "checkbox", "checked": False, "value": "on"}) == "Disabled"
    text = {"field_key": "map_name", "action": "fill_text", "expected_value": "X"}
    assert attempt_actual_value(text, {"value": "X"}) == "X"


def test_values_equal_switch_and_version_equivalence():
    assert _values_equal("Enabled", "checked")
    assert _values_equal("Enable", "Enabled")
    assert _values_equal("Yes", "on")
    assert not _values_equal("Enabled", "Disabled")
    assert not _values_equal("Enabled", "status")
    assert _values_equal("1", "1.0")


def _attempts(status_checked: bool) -> list:
    node = _status_node()
    switch = {"role": "switch", "type": "checkbox", "checked": status_checked, "value": "on"}
    rows = [
        ("map_identifier", "DELLCoXMLASNXX08C_U-HAUL"), ("map_identifier_version", "1.0"),
        ("map_name", "DELLCoXMLASNXX08C"), ("map_class", "Transform_DELLCoXMLASNXX08C"),
        ("status", attempt_actual_value(node, switch)),
    ]
    return [{"field": f, "success": True, "exact_verified": True, "actual_value": v, "section": "Create Map"} for f, v in rows]


def test_deterministic_judge_proves_enabled_status_switch(tmp_path: Path):
    judge = object.__new__(DualModelSectionJudge)
    expected = build_phase_expectation(INPUT, "data_map")
    attempts = _attempts(status_checked=True)
    result = judge.deterministic_judge(expected=expected, actual_state=_artifact_actual_state(tmp_path, attempts, []), attempts=attempts)
    assert result["pass"] is True, result["missing_values"]
    assert any(m["field"] == "status" and m["actual"] == "Enabled" for m in result["matched_values"])

    # A genuinely disabled switch is still caught.
    attempts = _attempts(status_checked=False)
    blocked = judge.deterministic_judge(expected=expected, actual_state=_artifact_actual_state(tmp_path, attempts, []), attempts=attempts)
    assert any(m["field"] == "status" for m in blocked["missing_values"])


def test_model_status_and_version_claims_are_reconciled_by_exact_evidence(tmp_path: Path):
    judge = object.__new__(DualModelSectionJudge)
    expected = build_phase_expectation(INPUT, "data_map")
    attempts = _attempts(status_checked=True)
    deterministic = judge.deterministic_judge(expected=expected, actual_state=_artifact_actual_state(tmp_path, attempts, []), attempts=attempts)
    text = _reconcile_text_judge({"status": "ok", "pass": False, "missing_or_wrong": [
        {"field": "status", "expected": "Enabled", "actual": "", "reason": "required field missing or empty"},
        {"field": "map_identifier_version", "expected": "1", "actual": "1.0", "reason": "version mismatch"},
    ]}, deterministic)
    assert text["pass"] is True and text["missing_or_wrong"] == []
    vision = _reconcile_vision_judge({"status": "ok", "pass": False, "visible_issues": [
        {"field": "status", "expected": "Enabled", "observed": "Disabled"},
    ]}, deterministic)
    assert vision["pass"] is True and vision["visible_issues"] == []


def test_live_judge_capture_reads_switch_checked_state():
    html = """<!doctype html><html><body><form>
      <label for="st">Status</label><input id="st" type="checkbox" role="switch" checked>
      <label for="xr">Cross Reference Table details</label><input id="xr" type="checkbox" role="switch">
      <label for="bs">Enabled</label><button id="bs" role="switch" aria-checked="true">Enabled</button>
    </form></body></html>"""

    async def run():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=_chromium_path())
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page()
            await page.set_content(html)
            try:
                return await object.__new__(DualModelSectionJudge).capture_live_state(page)
            finally:
                await browser.close()

    state = asyncio.run(run())
    by_id = {c["selector"]: c["value"] for c in state["controls"]}
    assert by_id["input#st"] == "checked"
    assert by_id["input#xr"] == ""
    assert by_id["button#bs"] == "checked"


def test_looks_correct_on_exact_completed_phase_is_accepted_once(tmp_path: Path):
    cfg = AppConfig()
    store = HumanPhaseReviewStore(tmp_path / "reviews", cfg.human_in_the_loop)
    request = store.create_recovery_request(
        run_id="RUN-1", phase="data_map", phase_display="Data Map", recovery_round=2,
        reason="Exact phase execution is complete but judge evidence still disagrees",
        exact_checkpoint={"pass": True}, automated_judge={"pass": False}, verification={},
    )
    resolved = store.resolve(request_id=request["request_id"], verdict="looks_correct")
    assert resolved["effective_human_verdict"] == "pass"
    assert accepted_human_override(resolved, {"pass": True})["request_id"] == request["request_id"]
    # The accepted request is immutable: asking again returns the same resolution.
    again = store.create_recovery_request(
        run_id="RUN-1", phase="data_map", phase_display="Data Map", recovery_round=2,
        reason="same", exact_checkpoint={"pass": True},
    )
    assert again["status"] == "resolved"


def test_looks_correct_without_exact_proof_still_only_rechecks(tmp_path: Path):
    store = HumanPhaseReviewStore(tmp_path / "reviews", AppConfig().human_in_the_loop)
    request = store.create_recovery_request(
        run_id="RUN-1", phase="data_map", phase_display="Data Map", recovery_round=1,
        reason="upload not committed", exact_checkpoint={"pass": False},
    )
    resolved = store.resolve(request_id=request["request_id"], verdict="looks_correct")
    assert resolved["effective_human_verdict"] == "recheck_live_phase"
    assert accepted_human_override(resolved, {"pass": False}) == {}
    assert accepted_human_override(resolved, {"pass": True}) == {}
    rejected = store.create_recovery_request(
        run_id="RUN-1", phase="data_map", phase_display="Data Map", recovery_round=3,
        reason="x", exact_checkpoint={"pass": True},
    )
    rej = store.resolve(request_id=rejected["request_id"], verdict="needs_correction")
    assert accepted_human_override(rej, {"pass": True}) == {}


def test_executor_records_enabled_for_status_switch_on_replica(tmp_path: Path):
    html = (Path(__file__).parent / "fixtures" / "create_map_duplicate_identifier.html").read_text(encoding="utf-8")
    html = html.replace("__VERSION_ATTR__", 'placeholder="1"')

    async def run():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=_chromium_path())
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.set_content(html)
            try:
                return await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(INPUT, "data_map"), phase="data_map",
                    input_data=INPUT, config=None, output_dir=tmp_path, max_cycles=1,
                )
            finally:
                await browser.close()

    final = autonomous_target_execution(asyncio.run(run()))
    status = next(a for a in final["attempts"] if a.get("field") == "status")
    assert status["success"] is True and status["actual_value"] == "Enabled"
