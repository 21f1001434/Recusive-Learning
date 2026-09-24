from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig, load_config
from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow
from hip_id_agent.mission_trace import MissionTraceLedger, read_mission_trace
from hip_id_agent.phase_progress import PhaseNoProgressError, run_with_progress_watchdog


class _EvalPage:
    def __init__(self, url: str, basis: dict):
        self.url = url
        self.basis = basis
    def is_closed(self):
        return False
    async def evaluate(self, _script):
        return self.basis


@pytest.mark.asyncio
async def test_structural_progress_marker_is_value_free_and_stable(tmp_path: Path):
    cfg = AppConfig()
    session = BrowserSession(cfg, tmp_path / "run")
    page = _EvalPage("https://developer.dell.com/hip/datamaps", {
        "route": "/hip/datamaps",
        "ready": "complete",
        "controls": [["input", "", "text", "map identifier", 0, 0, "", "", 1, 0]],
        "surfaces": [["div", "dialog", "create map", "true"]],
        "active": ["input", "", "map identifier"],
    })
    session.page = page
    first = await session.capture_phase_progress_marker("data_map")
    second = await session.capture_phase_progress_marker("data_map")
    assert first["signature"] == second["signature"]
    assert first["route"] == "/hip/datamaps"
    assert first["values_stored"] is False
    assert "controls" not in first
    assert "map identifier" not in str(first).lower()

    # Occupancy/state changes alter the signature without storing the value itself.
    page.basis["controls"][0][8] = 0
    third = await session.capture_phase_progress_marker("data_map")
    assert third["signature"] != first["signature"]


@pytest.mark.asyncio
async def test_watchdog_interrupts_repeating_ui_cycle_instead_of_reasoning_forever(tmp_path: Path):
    states = ["A", "B", "A", "B"]
    index = {"n": 0}
    cancelled = {"yes": False}

    async def operation():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled["yes"] = True
            raise

    async def marker():
        value = states[index["n"] % len(states)]
        index["n"] += 1
        return {"signature": value, "route": "/hip/datamaps"}

    with pytest.raises(PhaseNoProgressError) as caught:
        await run_with_progress_watchdog(
            operation(), phase="data_map", marker_provider=marker,
            checkpoint_provider=lambda: {"pass": False},
            evidence_path=tmp_path / "watchdog.json",
            no_progress_seconds=0.05, poll_seconds=0.01, recent_signature_limit=4,
        )
    assert caught.value.code == "HIP_PHASE_NO_PROGRESS_WATCHDOG"
    assert cancelled["yes"] is True
    assert (tmp_path / "watchdog.json").is_file()


@pytest.mark.asyncio
async def test_watchdog_after_exact_state_skips_phase_replay(tmp_path: Path):
    async def operation():
        await asyncio.sleep(10)
    async def marker():
        return {"signature": "EXACT", "route": "/hip/datamaps"}

    with pytest.raises(PhaseNoProgressError) as caught:
        await run_with_progress_watchdog(
            operation(), phase="data_map", marker_provider=marker,
            checkpoint_provider=lambda: {"pass": True},
            evidence_path=tmp_path / "watchdog_exact.json",
            no_progress_seconds=0.04, poll_seconds=0.01,
        )
    assert caught.value.code == "HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL"
    assert caught.value.payload["exact_completion_checkpoint_pass"] is True


@pytest.mark.asyncio
async def test_verified_phase_handoff_navigates_and_requires_dual_mcp_target_proof(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    session = BrowserSession(cfg, tmp_path / "run")
    session.page = _EvalPage("https://developer.dell.com/hip/datamaps", {})
    calls = []

    async def cleanup(*, next_phase=""):
        calls.append(("cleanup", next_phase)); return {"status": "ok"}
    async def navigate(url):
        calls.append(("navigate", url)); session.page.url = url
    async def usable(url):
        calls.append(("usable", url)); return True
    async def dual(url, **kwargs):
        calls.append(("dual", url)); return {"pass": True, "playwright_mcp_matches_actual": True}

    monkeypatch.setattr(session, "_dismiss_transient_ui", cleanup)
    monkeypatch.setattr(session, "navigate", navigate)
    monkeypatch.setattr(session, "_navigation_page_is_usable", usable)
    monkeypatch.setattr(session, "_verify_dual_mcp_same_surface", dual)

    result = await session.handoff_to_next_phase(
        from_phase="data_map", to_phase="source_document_type",
        to_url="https://developer.dell.com/hip/doctypes",
        exact_checkpoint_passed=True,
    )
    assert result["pass"] is True
    assert result["code"] == "HIP_PHASE_HANDOFF_OK"
    assert result["primary_navigation_executor"] == "Playwright MCP"
    assert result["browser_switched"] is False
    assert [row[0] for row in calls] == ["cleanup", "navigate", "usable", "usable", "dual"]


@pytest.mark.asyncio
async def test_handoff_fails_closed_without_source_completion_proof(tmp_path: Path, monkeypatch):
    session = BrowserSession(AppConfig(), tmp_path / "run")
    called = {"navigate": 0}
    async def navigate(_url): called["navigate"] += 1
    monkeypatch.setattr(session, "navigate", navigate)
    result = await session.handoff_to_next_phase(
        from_phase="data_map", to_phase="source_document_type",
        to_url="https://developer.dell.com/hip/doctypes",
        exact_checkpoint_passed=False, allow_from_blocked=False,
    )
    assert result["pass"] is False
    assert result["code"] == "HIP_PHASE_HANDOFF_SOURCE_NOT_VERIFIED"
    assert called["navigate"] == 0


def test_mission_trace_records_p01_to_p02_transition(tmp_path: Path):
    trace = MissionTraceLedger(tmp_path, run_id="run-handoff", phases=["data_map", "source_document_type"])
    trace.mark_phase("data_map", status="completed", attempt=1)
    trace.record_transition("data_map", "source_document_type", status="starting", details={"browser_switched": False})
    trace.record_transition("data_map", "source_document_type", status="complete", details={"code": "HIP_PHASE_HANDOFF_OK"})
    data = read_mission_trace(tmp_path)
    dm = data["steps"][0]
    sdt = data["steps"][1]
    assert dm["step_id"] == "P01-DM"
    assert dm["handoff"]["to_step_id"] == "P02-SDT"
    assert dm["handoff"]["status"] == "complete"
    assert "P02-SDT" in dm["current_activity"]
    assert "P01-DM" in sdt["current_activity"]
    assert len(data["transitions"]) == 2


def test_full_mission_wraps_phase_execution_with_watchdog_and_handoffs_before_next_phase():
    source = inspect.getsource(FullDummyFillE2EFlow.run)
    assert "run_with_progress_watchdog" in source
    assert "phase_no_progress_watchdog.json" in source
    assert "HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL" in source
    complete = source.index("mission.mark_phase_complete")
    handoff = source.index("shared_browser.handoff_to_next_phase", complete)
    phase_done = source.index("phase_completed = True", handoff)
    assert complete < handoff < phase_done
    assert "allow_from_blocked=True" in source


def test_shipped_config_has_active_no_progress_watchdog():
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    assert cfg.runtime_self_heal.no_progress_watchdog_seconds == 90
    assert cfg.runtime_self_heal.no_progress_poll_seconds == 5
    assert cfg.runtime_self_heal.no_progress_recent_signature_limit == 12


def test_mission_ui_displays_phase_handoff_status():
    js = (Path(__file__).resolve().parents[1] / "webui" / "app.js").read_text(encoding="utf-8")
    assert "Phase handoff" in js
    assert "handoff.to_step_id" in js
