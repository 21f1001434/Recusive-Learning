"""V243R26: a completely filled Document Type is not thrown away; parents first.

Live report (2026-09-28, run UHAUL-POASN-20260928-130654): attempt 3 filled the
whole Document Type form but did not complete; attempt 4 reopened a blank form
and stopped with Data Format Type and every dropdown below it empty
(HIP_PHASE_STALL_AFTER_RECOVERY after HIP_PHASE_NO_PROGRESS_WATCHDOG, "0 progress
extension(s) used").

Root causes:

* the phase budget stopped an attempt that added no *new* verified field before
  its deadline -- also one whose form was already completely filled and was only
  finishing its read-back / evidence -- and recovery then reopened a blank form;
* the budget of a retry was only what earlier attempts left of the shared phase
  budget, so attempt 4 was stopped almost at once;
* progress was counted across attempts: fields verified in attempt 3 did not
  count again when attempt 4 re-verified them on a reopened form;
* the lower dropdowns (Operation, Derived From, Usage, Validation Type) list
  values only while Data Format Type holds its value; a parent the portal cleared
  after it was verified was not restored first, so every child opened empty and
  retried until the attempt's time ran out.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from hip_id_agent.phase_progress import run_with_progress_budget

PHASE = "source_document_type"


class _Healer:
    def __init__(self, left: int = 5, seconds: float = 0.3):
        self.left, self.seconds = left, seconds

    def extend(self, units):
        if self.left <= 0:
            return {"granted": False}
        self.left -= 1
        return {"granted": True, "seconds": self.seconds}


def _budget(work, marker, *, budget=0.3, checkpoint=None, finalize=0.5, tmp_path=None, healer=None):
    async def main():
        return await run_with_progress_budget(
            work(), phase=PHASE, budget_seconds=budget, marker_provider=marker, extend=(healer or _Healer()).extend,
            evidence_path=(tmp_path / "budget.json") if tmp_path else None, checkpoint_provider=checkpoint,
            finalize_seconds=finalize, max_finalize_extensions=2,
        )
    return asyncio.run(main())


# ------------------------------------------------------------ phase budget
def test_a_completely_filled_form_gets_time_to_finish_its_checks(tmp_path: Path):
    state = {"complete": False}

    async def attempt():
        await asyncio.sleep(0.1)
        state["complete"] = True  # every field filled and verified ...
        await asyncio.sleep(1.6)  # ... then the read-back / evidence outlasts the budget (minimum 1 s)
        return "phase complete"

    async def marker():
        return {"progress_units": 3, "fill_complete": state["complete"]}

    assert _budget(attempt, marker, budget=1.0, finalize=1.5, tmp_path=tmp_path) == "phase complete"
    assert json.loads((tmp_path / "budget.json").read_text())["decision"] == "extended_to_finish_verification"


def test_a_stopped_attempt_whose_live_form_is_exact_is_not_reopened(tmp_path: Path):
    async def stuck_after_fill():
        await asyncio.sleep(5)

    async def marker():
        return {"progress_units": 4, "fill_complete": False}

    async def exact():
        return {"pass": True}

    with pytest.raises(asyncio.TimeoutError, match="HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL"):
        _budget(stuck_after_fill, marker, budget=0.2, checkpoint=exact, tmp_path=tmp_path)
    assert json.loads((tmp_path / "budget.json").read_text())["exact_completion_checkpoint_pass"] is True


def test_an_attempt_that_is_neither_progressing_nor_exact_is_still_stopped(tmp_path: Path):
    async def stuck():
        await asyncio.sleep(5)

    async def marker():
        return {"progress_units": 2}

    with pytest.raises(asyncio.TimeoutError, match="HIP_PHASE_NO_PROGRESS_WATCHDOG"):
        _budget(stuck, marker, budget=0.2, checkpoint=lambda: {"pass": False}, tmp_path=tmp_path)


def test_finishing_time_is_bounded(tmp_path: Path):
    async def never_finishes():
        await asyncio.sleep(10)

    async def marker():
        return {"progress_units": 9, "fill_complete": True}

    with pytest.raises(asyncio.TimeoutError):
        _budget(never_finishes, marker, budget=0.2, finalize=0.2, tmp_path=tmp_path)
    assert len(json.loads((tmp_path / "budget.json").read_text()).get("finalize_extensions") or []) <= 2


def test_each_attempt_counts_its_own_progress():
    from hip_id_agent.browser_session import BrowserSession

    page = SimpleNamespace(_hip_verified_nodes={f"{PHASE}|a", f"{PHASE}|b", "data_map|x"}, _hip_fill_complete={PHASE: True})
    session = SimpleNamespace(page=page, context=SimpleNamespace(pages=[page]), action_events=[1, 2, 3], _active_phase_name=PHASE)
    info = BrowserSession.begin_phase_attempt_progress(session, PHASE)
    assert info["cleared_verified_nodes"] == 2 and page._hip_verified_nodes == {"data_map|x"}
    assert PHASE not in page._hip_fill_complete and session._progress_event_baseline[PHASE] == 3


def test_the_mission_gives_every_retry_a_fair_budget_and_keeps_an_exact_form():
    from hip_id_agent.config import AppConfig
    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow

    source = inspect.getsource(FullDummyFillE2EFlow.run)
    budget_at = source.index("remaining_phase_seconds = max(")
    assert "min_attempt_seconds" in source[budget_at:budget_at + 300]
    assert budget_at < source.index("shared_browser.begin_phase_attempt_progress(phase)") < source.index("summary = await run_with_progress_budget(")
    call = source[source.index("summary = await run_with_progress_budget("):]
    assert "checkpoint_provider=_watchdog_checkpoint_provider" in call[:3000] and "finalize_seconds=" in call[:3000]
    # An exact form stopped by the budget continues to the judges (existing no-replay path).
    assert '"HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL" in message' in source
    cfg = AppConfig().runtime_self_heal
    assert cfg.min_attempt_seconds == 900 and cfg.finalize_grace_seconds == 600 and cfg.max_finalize_extensions == 2


# ------------------------------------------------ real browser: parents first
def _live_like(tmp_path: Path, flags: str, rows: int = 1) -> Dict[str, Any]:
    from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
    from hip_id_agent.browser_session import BrowserSession
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph
    from loader_portal_support import LoaderPortal, patch_navigation, real_session_config
    from phase_replica_support import ROOT, chromium_path

    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")
    payload = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    obj = dict(payload["objects"][PHASE])
    obj["attributes_to_configure"] = obj["attributes_to_configure"][:rows]
    data = {"objects": {PHASE: obj}}

    async def run():
        with LoaderPortal(stuck_loads=0, attribute_rows=rows) as portal:
            portal.html = portal.html.replace("<script>", f"<script>{flags}</script><script>", 1)
            session = BrowserSession(real_session_config(tmp_path), tmp_path / "run")
            await session.start()
            session._active_phase_name = PHASE
            patch_navigation(session)
            try:
                await session.goto_base_and_complete_sso(portal.url)
                page = await session._ensure_active_page(portal.url)
                session.begin_phase_attempt_progress(PHASE)
                result = await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(data, PHASE), phase=PHASE, input_data=data, config=None,
                    output_dir=tmp_path / "out", max_cycles=3, executor=execute_document_type_state_graph)
                values = await page.evaluate("""() => Array.from(document.querySelectorAll('dds-dropdown')).map(d =>
                    [(d.querySelector('label') || d.querySelector('input')).textContent || d.querySelector('input').placeholder,
                     d.querySelector('input').value || Array.from(d.querySelectorAll('.dds__tag')).map(t => t.textContent).join('|')])""")
                counters = await page.evaluate("() => ({emptyOpens: window.__hipOpenedEmpty, resets: window.__hipFormatResets || 0})")
                marker = await session.capture_phase_progress_marker(PHASE)
                return {"result": result, "values": values, "counters": counters, "marker": marker}
            finally:
                await session.close()

    return asyncio.run(run())


def test_the_lower_dropdowns_list_values_only_after_data_format_type_and_are_filled_after_it(tmp_path: Path):
    out = _live_like(tmp_path, "window.__liveOptionsAfterFormat = true; window.__formatAfterTransaction = true; window.__transactionRequestMs = 2500;")
    assert out["result"]["pass"] is True
    values = dict((k.strip(), v) for k, v in out["values"][:2])
    assert values.get("Data Format Type") == "XML" and values.get("Operation") == "All conditions are satisfied"
    assert all(v for _, v in out["values"])  # every dropdown below holds its value
    assert out["marker"]["fill_complete"] is True  # the budget knows the form is complete


def test_a_parent_the_portal_cleared_is_selected_again_before_its_children(tmp_path: Path):
    out = _live_like(tmp_path, "window.__liveOptionsAfterFormat = true; window.__formatResetOnceAfterMs = 1500;")
    assert out["counters"]["resets"] == 1  # the portal really cleared Data Format Type
    assert out["counters"]["emptyOpens"] == 0  # no child dropdown was opened while it was empty
    assert out["result"]["pass"] is True and all(v for _, v in out["values"])
