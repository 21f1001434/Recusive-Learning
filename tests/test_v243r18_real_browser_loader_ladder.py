"""V243R18 with a real browser: refresh, then close/reopen the browser, then finish.

``loader_portal_support.LoaderPortal`` serves the Document Type replica; after
Transaction Type a DDS loading overlay appears and the form is disabled while
the "portal request" runs.  For the first ``stuck_loads`` page loads that
request never finishes.  A real ``BrowserSession`` (no MCP servers), the real
watchdog, the real self-healer and the real Document Type executor run a
mission-style attempt loop.  Vision is off, as with the text-only gpt-oss-120b.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.phase_progress import run_with_progress_watchdog
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph
from loader_portal_support import LoaderPortal, patch_navigation, real_session_config
from phase_replica_support import ROOT, chromium_path

PHASE = "source_document_type"


def _data() -> Dict[str, Any]:
    payload = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    obj = dict(payload["objects"][PHASE])
    obj["attributes_to_configure"] = obj["attributes_to_configure"][:2]
    return {"objects": {PHASE: obj}}


async def _start(tmp_path: Path, portal: LoaderPortal):
    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")
    session = BrowserSession(real_session_config(tmp_path), tmp_path / "run")
    await session.start()
    session._active_phase_name = PHASE
    patch_navigation(session)
    await session.goto_base_and_complete_sso(portal.url)
    return session


def test_restart_is_allowed_during_a_mission_and_keeps_the_profile(tmp_path: Path):
    async def run():
        with LoaderPortal(stuck_loads=0) as portal:
            session = await _start(tmp_path, portal)
            try:
                assert session._mission_browser_locked is True
                page = await session._ensure_active_page(portal.url)
                await page.evaluate("() => { document.cookie = 'hip_sso=kept; max-age=3600; path=/'; }")
                info = await session.restart(reason="test")
                await session.goto_base_and_complete_sso(portal.url)
                page = await session._ensure_active_page(portal.url)
                return info, session._start_count, await page.evaluate("() => document.cookie")
            finally:
                await session.close()

    info, starts, cookie = asyncio.run(run())
    assert info["status"] == "restarted" and info["mode"] == "relaunched_same_browser_and_profile"
    assert starts == 2
    assert "hip_sso=kept" in cookie  # same persistent profile: SSO cookies survive


def test_stuck_loader_is_refreshed_then_the_browser_restarted_then_the_form_completes(tmp_path: Path):
    async def run():
        # Stuck on the first load, after the refresh, and after re-opening the
        # phase; only a browser restart clears it.
        with LoaderPortal(stuck_loads=3) as portal:
            session = await _start(tmp_path, portal)
            heal = RuntimeSelfHealController(config=session.config, root_dir=tmp_path / "heal", browser=session, run_id="t")
            log: List[Dict[str, Any]] = []
            filled: List[str] = []
            try:
                for attempt in range(1, 6):
                    page = await session._ensure_active_page(portal.url)
                    try:
                        result = await run_with_progress_watchdog(
                            execute_autonomous_phase_goal(
                                page=page, graph=compile_phase_state_graph(_data(), PHASE), phase=PHASE,
                                input_data=_data(), config=None, output_dir=tmp_path / f"out{attempt}", max_cycles=2,
                                executor=execute_document_type_state_graph,
                            ),
                            phase=PHASE, marker_provider=lambda: session.capture_phase_progress_marker(PHASE),
                            checkpoint_provider=lambda: {"pass": False}, no_progress_seconds=20, poll_seconds=0.5,
                            blocking_wait_seconds=heal.watchdog_blocking_wait_seconds(),
                        )
                        log.append({"attempt": attempt, "status": result.get("status")})
                        if result.get("pass"):
                            heal.finalize_phase(PHASE, judge_pass=True)
                            page = await session._ensure_active_page(portal.url)
                            filled = await page.evaluate(
                                "() => Array.from(document.querySelectorAll('input')).filter(e => e.value).map(e => e.placeholder)")
                            break
                        raise RuntimeError(f"phase goal not met: {result.get('status')}")
                    except Exception as exc:
                        decision = await heal.handle_failure(phase=PHASE, target_url=portal.url, attempt=attempt, message=str(exc))
                        log.append({"attempt": attempt, "class": decision.classification, "action": decision.action,
                                    "retry": decision.retry, "starts": session._start_count})
                        if not decision.retry:
                            break
            finally:
                await session.close()
            return log, filled

    log, filled = asyncio.run(run())
    actions = [row.get("action") for row in log if row.get("action")]
    assert actions == ["refresh_page_and_reopen", "restart_browser_session"], log
    assert log[-1]["status"] == "pass", log
    assert {"Operation", "Derived From", "Value", "Validation Type"} <= set(filled)
    memory = json.loads((tmp_path / "memory" / "runtime_recovery_ladder.json").read_text(encoding="utf-8"))
    rows = {r["action"]: r for r in memory["ladders"][f"{PHASE}|loader"]}
    assert rows["refresh_page_and_reopen"]["not_resolved"] == 1
    assert rows["restart_browser_session"]["resolved"] == 1
