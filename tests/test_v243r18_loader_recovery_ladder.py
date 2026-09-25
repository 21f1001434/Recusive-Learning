"""V243R18: a stuck portal loader is refreshed, then the browser is restarted.

Live symptom: after Name and Transaction Type the Dell portal kept its loading
indicator up.  The no-progress watchdog stopped the attempt at 90 s (field
retry heartbeats had even hidden the stall), the failure was classed
"unknown" and retried once, and the phase went to human review.  A browser
restart was never possible: ``BrowserSession.restart`` hit the mission's
browser lock.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import hip_id_agent.dds_control_driver as driver
from hip_id_agent.aia_client import extract_json_object
from hip_id_agent.config import AppConfig
from hip_id_agent.phase_progress import PhaseNoProgressError, run_with_progress_watchdog
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


# ---------------------------------------------------------------- watchdog
def _markers(rows):
    it = iter(rows)
    last = {}

    async def provider():
        nonlocal last
        last = next(it, last)
        return dict(last)

    return provider


async def _forever():
    await asyncio.sleep(3600)


def test_blocking_loader_trips_loading_stuck_even_with_retry_heartbeats():
    # Behind the loader the executor keeps retrying fields (new heartbeat each
    # sample) but nothing can be filled.
    rows = [{"signature": "A", "blocking_loader": True, "executor_progress": f"1|n{i}|attempt|0"} for i in range(400)]
    with pytest.raises(PhaseNoProgressError) as info:
        asyncio.run(run_with_progress_watchdog(
            _forever(), phase="p", marker_provider=_markers(rows), checkpoint_provider=lambda: {"pass": False},
            no_progress_seconds=0.2, poll_seconds=0.05, blocking_wait_seconds=0.5,
        ))
    assert info.value.code == "HIP_PORTAL_LOADING_STUCK"
    assert info.value.payload["blocking_loader"] is True
    assert info.value.payload["no_progress_seconds"] >= 0.5  # the loading budget, not the 0.2 s stall limit


def test_a_loader_is_given_the_loading_budget_before_the_stall_limit():
    rows = [{"signature": "A", "blocking_loader": True} for _ in range(400)]

    async def finishes():
        await asyncio.sleep(0.45)
        return "loaded"

    assert asyncio.run(run_with_progress_watchdog(
        finishes(), phase="p", marker_provider=_markers(rows), checkpoint_provider=lambda: {"pass": False},
        no_progress_seconds=0.15, poll_seconds=0.03, blocking_wait_seconds=1.0,
    )) == "loaded"


def test_without_a_loader_heartbeats_still_count_as_progress():
    rows = [{"signature": "A", "executor_progress": f"1|n{i}|attempt|0"} for i in range(400)]

    async def finishes():
        await asyncio.sleep(0.5)
        return "done"

    assert asyncio.run(run_with_progress_watchdog(
        finishes(), phase="p", marker_provider=_markers(rows), checkpoint_provider=lambda: {"pass": False},
        no_progress_seconds=0.2, poll_seconds=0.05, blocking_wait_seconds=0.3,
    )) == "done"


# ---------------------------------------------------------------- ladder
class _Browser:
    def __init__(self, *, loader: bool = False, refreshes: int = 0):
        self.calls: list = []
        self.loader = loader
        self.refreshes = refreshes

    async def _current_loading_state(self, target_selector: str = ""):
        return {"active": self.loader}

    def loading_recovery_counts(self, phase: str = ""):
        return {"refreshes": self.refreshes}

    async def refresh_current_page_preserving_session(self, *, reason: str = ""):
        self.calls.append("refresh")
        return {"status": "refreshed"}

    async def restart(self, *, reason: str = ""):
        self.calls.append("restart")
        return {"status": "restarted"}

    async def _dismiss_transient_ui(self, *, next_phase: str = ""):
        return None

    async def goto_base_and_complete_sso(self, url: str = ""):
        self.calls.append("goto")

    async def _consolidate_session_pages(self, url: str = ""):
        return None

    async def _ensure_page_observers(self):
        return None


def _controller(tmp_path: Path, browser, *, restarts: int = 1) -> RuntimeSelfHealController:
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.runtime_self_heal.capture_evidence = False
    cfg.runtime_self_heal.forensic_evidence = False
    cfg.runtime_self_heal.use_aia_advisor = False
    cfg.runtime_self_heal.max_browser_restarts_per_phase = restarts
    return RuntimeSelfHealController(config=cfg, root_dir=tmp_path / "heal", browser=browser, run_id="t")


def _fail(controller, attempt, message, phase="source_document_type"):
    return asyncio.run(controller.handle_failure(phase=phase, target_url="https://x/doctypes", attempt=attempt, message=message))


def test_stuck_loader_ladder_is_refresh_then_browser_restart_then_human(tmp_path: Path):
    browser = _Browser()
    heal = _controller(tmp_path, browser)
    stuck = "HIP_PORTAL_LOADING_STUCK: The Dell portal's loading indicator stayed blocking for 360s"
    first, second, third = _fail(heal, 1, stuck), _fail(heal, 2, stuck), _fail(heal, 3, stuck)
    assert (first.classification, first.action, first.retry) == ("portal_loading_stuck", "refresh_page_and_reopen", True)
    assert (second.action, second.retry) == ("restart_browser_session", True)
    assert (third.action, third.retry) == ("stop_fail_closed", False)
    assert browser.calls == ["refresh", "goto", "restart", "goto"]
    # The review names what was already tried.
    assert third.reason.startswith("HIP_PORTAL_LOADING_STUCK_AFTER_RECOVERY")
    assert "refreshed the page" in third.reason and "closed and reopened the browser" in third.reason


def test_a_refresh_already_done_by_the_loading_watchdog_goes_straight_to_restart(tmp_path: Path):
    heal = _controller(tmp_path, _Browser(refreshes=1))
    decision = _fail(heal, 1, "HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH: the portal's blocking loading indicator stayed up")
    assert (decision.classification, decision.action) == ("blocking_overlay", "restart_browser_session")


def test_stalled_phase_ladder_reopens_refreshes_then_restarts(tmp_path: Path):
    heal = _controller(tmp_path, _Browser())
    msg = "HIP_PHASE_NO_PROGRESS_WATCHDOG: The live HIP phase produced no new structural browser state"
    actions = [_fail(heal, i, msg).action for i in range(1, 5)]
    assert actions == ["reopen_phase_from_input", "refresh_page_and_reopen", "restart_browser_session", "stop_fail_closed"]


def test_any_failure_with_a_loader_still_blocking_uses_the_loader_ladder(tmp_path: Path):
    heal = _controller(tmp_path, _Browser(loader=True))
    decision = _fail(heal, 1, "phase goal not met: failed_closed")
    assert (decision.classification, decision.action) == ("portal_loading_stuck", "refresh_page_and_reopen")


def test_the_ladder_learns_which_step_fixes_the_portal(tmp_path: Path):
    for run in range(2):  # refresh never helped, the restart did
        heal = _controller(tmp_path, _Browser())
        stuck = "HIP_PORTAL_LOADING_STUCK: x"
        assert _fail(heal, 1, stuck).action == "refresh_page_and_reopen"
        assert _fail(heal, 2, stuck).action == "restart_browser_session"
        heal.finalize_phase("source_document_type", judge_pass=True)
    memory = json.loads((tmp_path / "memory" / "runtime_recovery_ladder.json").read_text(encoding="utf-8"))
    rows = {r["action"]: r for r in memory["ladders"]["source_document_type|loader"]}
    assert rows["refresh_page_and_reopen"] == {"action": "refresh_page_and_reopen", "resolved": 0, "not_resolved": 2}
    assert rows["restart_browser_session"]["resolved"] == 2
    # Next run: restart first, refresh kept as the last resort.
    heal = _controller(tmp_path, _Browser())
    assert _fail(heal, 1, "HIP_PORTAL_LOADING_STUCK: x").action == "restart_browser_session"
    assert _fail(heal, 2, "HIP_PORTAL_LOADING_STUCK: x").action == "refresh_page_and_reopen"


def test_human_resume_gives_the_phase_a_fresh_ladder_and_wall_budget(tmp_path: Path):
    heal = _controller(tmp_path, _Browser())
    base = heal.wall_budget_seconds("rule")
    for i in (1, 2, 3):
        _fail(heal, i, "HIP_PORTAL_LOADING_STUCK: x", phase="rule")
    assert heal.wall_budget_seconds("rule") > base  # each recovery step earned its time
    assert _fail(heal, 4, "HIP_PORTAL_LOADING_STUCK: x", phase="rule").action == "stop_fail_closed"
    heal.reset_phase_ladder("rule")
    assert heal.wall_budget_seconds("rule") == base
    assert _fail(heal, 5, "HIP_PORTAL_LOADING_STUCK: x", phase="rule").action == "refresh_page_and_reopen"


def test_watchdog_waits_the_configured_loading_budget(tmp_path: Path):
    heal = _controller(tmp_path, _Browser())
    heal.config.vision_runtime.use_for_loading_watchdog = False
    heal.config.portal.loading_watchdog_timeout_seconds = 300
    assert heal.watchdog_blocking_wait_seconds() == 300 + heal.loader_grace_seconds


# ---------------------------------------------------------------- driver
class _Session:
    def __init__(self, error: str):
        self.error = error
        self.action_events = []
        self._active_phase_name = "p"

    async def click_and_wait(self, **kwargs):
        raise RuntimeError(self.error)


class _Page:
    def __init__(self, session):
        self._hip_browser_session = session

    def locator(self, selector):
        class _L:
            first = None
        return _L()


def test_broker_click_ends_the_attempt_on_portal_errors_but_not_on_field_errors():
    page = _Page(_Session("HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH: loader stayed up"))
    with pytest.raises(RuntimeError, match="HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH"):
        asyncio.run(driver._broker_click(page, "#x", label="x"))
    page = _Page(_Session("element is not visible"))
    assert asyncio.run(driver._broker_click(page, "#x", label="x")) is False


# ---------------------------------------------------------------- gpt-oss
def test_gpt_oss_final_channel_is_parsed_not_its_reasoning():
    unsplit = 'analysisWe could answer {"action":"stop_fail_closed"} but no.assistantfinal{"action":"refresh_page_and_reopen"}'
    assert extract_json_object(unsplit) == {"action": "refresh_page_and_reopen"}
    tagged = '<|channel|>analysis<|message|>{"a":1}<|end|><|start|>assistant<|channel|>final<|message|>{"a":2}<|return|>'
    assert extract_json_object(tagged) == {"a": 2}
    assert extract_json_object('{"plain": true}') == {"plain": True}


def test_a_failed_refresh_falls_through_to_a_browser_restart(tmp_path: Path):
    browser = _Browser()

    async def broken_refresh(*, reason: str = ""):
        raise RuntimeError("refresh timed out")

    browser.refresh_current_page_preserving_session = broken_refresh
    heal = _controller(tmp_path, browser)
    decision = _fail(heal, 1, "HIP_PORTAL_LOADING_STUCK: x")
    assert (decision.action, decision.retry) == ("restart_browser_session", True)
    assert "restart" in browser.calls
