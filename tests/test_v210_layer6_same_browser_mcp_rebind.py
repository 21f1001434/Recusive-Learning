from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig, load_config
from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow


class _Page:
    def __init__(self, url: str):
        self.url = url
    def is_closed(self):
        return False


class _Context:
    def __init__(self, pages):
        self.pages = list(pages)


def test_disconnect_classifier_is_transport_specific():
    assert BrowserSession.is_executor_transport_disconnect("CDP WebSocket handler exited unexpectedly") is True
    assert BrowserSession.is_executor_transport_disconnect("TimeoutError: timed out during opening handshake") is True
    assert BrowserSession.is_executor_transport_disconnect("MCP transport connection closed") is True
    assert BrowserSession.is_executor_transport_disconnect("Data Map Submit button disabled") is False
    assert BrowserSession.is_executor_transport_disconnect("generic locator timeout") is False


def test_shipped_config_enables_bounded_same_browser_rebind():
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    assert cfg.mcp.executor_rebind_enabled is True
    assert cfg.mcp.executor_rebind_max_attempts_per_phase == 2
    assert cfg.mcp.executor_rebind_fail_on_ambiguous_tabs is True
    assert cfg.mcp.use_playwright_mcp is True
    assert cfg.mcp.playwright_mcp_primary_for_safe_actions is True


@pytest.mark.asyncio
async def test_rebind_restarts_only_mcp_clients_on_same_authenticated_tab(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    session = BrowserSession(cfg, tmp_path / "run")
    page = _Page("https://developer.dell.com/hip/datamaps")
    session.context = _Context([page])
    session.page = page
    session._cdp_endpoint = "http://127.0.0.1:9237"
    session._mission_browser_locked = True
    session._authenticated_once = True
    session._selected_browser = {"browser": "edge", "profile": "edge-profile"}

    async def cdp_ok(timeout_seconds=8.0):
        return {"ok": True, "browser": "Edge"}
    async def logged_in(_page):
        return True
    async def restart():
        return {
            "pass": True,
            "cdp_endpoint": session._cdp_endpoint,
            "playwright_mcp_attached": True,
            "chrome_devtools_mcp_attached": True,
            "browser_restarted": False,
            "browser_switched": False,
        }
    async def same_surface(expected_url, **kwargs):
        return {"pass": True, "python_playwright_url": expected_url, "playwright_mcp_matches_actual": True}

    monkeypatch.setattr(session, "_wait_for_cdp_ready", cdp_ok)
    monkeypatch.setattr(session, "_looks_logged_in", logged_in)
    monkeypatch.setattr(session, "_restart_mcp_clients_on_same_cdp", restart)
    monkeypatch.setattr(session, "_verify_dual_mcp_same_surface", same_surface)

    result = await session.recover_same_browser_executor_bindings(
        "https://developer.dell.com/hip/datamaps", phase="data_map", checkpoint_passed=False
    )
    assert result["pass"] is True
    assert result["code"] == "HIP_EXECUTOR_REBIND_OK"
    assert result["browser_restarted"] is False
    assert result["browser_switched"] is False
    assert result["selected_browser"]["browser"] == "edge"
    assert result["mcp_restart"]["playwright_mcp_attached"] is True
    assert result["resume_policy"] == "resume_same_phase_from_deterministic_state"


@pytest.mark.asyncio
async def test_rebind_fails_closed_when_two_tabs_match_same_phase(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    session = BrowserSession(cfg, tmp_path / "run")
    p1 = _Page("https://developer.dell.com/hip/datamaps")
    p2 = _Page("https://developer.dell.com/hip/datamaps?copy=1")
    session.context = _Context([p1, p2])
    session.page = p1
    session._cdp_endpoint = "http://127.0.0.1:9237"
    session._mission_browser_locked = True
    session._authenticated_once = True

    async def cdp_ok(timeout_seconds=8.0): return {"ok": True}
    monkeypatch.setattr(session, "_wait_for_cdp_ready", cdp_ok)
    called = {"restart": 0}
    async def restart():
        called["restart"] += 1
        return {"pass": True}
    monkeypatch.setattr(session, "_restart_mcp_clients_on_same_cdp", restart)

    result = await session.recover_same_browser_executor_bindings(
        "https://developer.dell.com/hip/datamaps", phase="data_map"
    )
    assert result["pass"] is False
    assert result["code"] == "HIP_RECONNECT_AMBIGUOUS_TABS"
    assert called["restart"] == 0
    assert result["browser_switched"] is False


@pytest.mark.asyncio
async def test_rebind_budget_prevents_connection_recovery_loop(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.mcp.executor_rebind_max_attempts_per_phase = 1
    session = BrowserSession(cfg, tmp_path / "run")
    page = _Page("https://developer.dell.com/hip/datamaps")
    session.context = _Context([page]); session.page = page
    session._cdp_endpoint = "http://127.0.0.1:9237"
    async def cdp_bad(timeout_seconds=8.0): return {"ok": False, "error": "closed"}
    monkeypatch.setattr(session, "_wait_for_cdp_ready", cdp_bad)

    first = await session.recover_same_browser_executor_bindings("https://developer.dell.com/hip/datamaps", phase="data_map")
    second = await session.recover_same_browser_executor_bindings("https://developer.dell.com/hip/datamaps", phase="data_map")
    assert first["pass"] is False
    assert second["code"] == "HIP_EXECUTOR_REBIND_BUDGET_EXHAUSTED"
    assert second["attempts_used"] == 1


def test_full_mission_handles_transport_disconnect_before_generic_react():
    source = inspect.getsource(FullDummyFillE2EFlow.run)
    transport = source.index("executor_disconnect = shared_browser.is_executor_transport_disconnect")
    rebind = source.index("recover_same_browser_executor_bindings", transport)
    react = source.index("runtime_self_healer.handle_failure", rebind)
    assert transport < rebind < react
    assert '"executor_rebound_after_exact_state"' in source
    assert '"phase_replay_required": False' in source[source.index('"executor_rebound_after_exact_state"'):]
    assert '"browser_switched": False' in inspect.getsource(BrowserSession.recover_same_browser_executor_bindings)


def test_operational_mission_continues_later_no_save_phases_but_api_capture_is_best_effort():
    root = Path(__file__).resolve().parents[1]
    cli = (root / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    dashboard = (root / "hip_id_agent" / "streamlit_dashboard.py").read_text(encoding="utf-8")
    assert "continue_after_phase_block=bool(autonomous_mission)" in cli
    start = dashboard.index("def build_mission_command")
    end = dashboard.index("def build_section_mission_command", start)
    block = dashboard[start:end]
    assert '"--api-capture-best-effort"' in block
    assert '"--require-api-capture"' not in block
    assert '"--bounded-runtime-self-heal"' in block
