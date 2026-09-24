from __future__ import annotations

import json
from pathlib import Path

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


TARGET = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"


class _StartupSsoBrowser:
    def __init__(self):
        self.authenticated = False
        self.react_calls = 0
        self.sso_calls = 0
        self.calls = []

    async def _ensure_active_page(self, target_url=""):
        self.calls.append("ensure_page")
        return object()

    async def _consolidate_session_pages(self, target_url=""):
        self.calls.append("consolidate")
        return {"pass": True}

    async def _ensure_page_observers(self):
        self.calls.append("observers")

    async def assess_autonomous_page_health(self, reason=""):
        self.calls.append("health")
        return {"decision": "observe", "reason": reason}

    async def _react_ensure_target_surface(self, target_url, max_steps=5):
        self.react_calls += 1
        if not self.authenticated:
            return {
                "pass": False,
                "status": "sso_required",
                "target_url": target_url,
                "steps": [{"plan": {"action": "await_sso"}}],
            }
        return {"pass": True, "status": "ready", "target_url": target_url}

    async def goto_base_and_complete_sso(self, target_url):
        self.sso_calls += 1
        self.authenticated = True
        self.calls.append("sso")

    async def _verify_dual_mcp_same_surface(self, *args, **kwargs):
        self.calls.append("dual_mcp")
        return {"pass": True}


@pytest.mark.asyncio
async def test_first_phase_preflight_resumes_after_sso_in_same_attempt(tmp_path: Path):
    cfg = AppConfig()
    cfg.aia.enabled = False
    browser = _StartupSsoBrowser()
    controller = RuntimeSelfHealController(
        config=cfg,
        root_dir=tmp_path,
        browser=browser,
        enabled=True,
    )

    result = await controller.prepare_phase_attempt(
        phase="data_map",
        target_url=TARGET,
        attempt=1,
        contract={"family": "data_map"},
        phase_dir=tmp_path / "data_map",
    )

    assert result["pass"] is True
    assert result["status"] == "ready"
    assert result["route"]["status"] == "authenticated_after_sso"
    assert browser.sso_calls == 1
    assert browser.react_calls == 2
    assert browser.calls[-1] == "dual_mcp"

    saved = json.loads((tmp_path / "data_map" / "phase_attempt_01_agentic_preflight.json").read_text())
    assert saved["pass"] is True
    assert saved["route"]["status"] == "authenticated_after_sso"


def test_agentic_preflight_is_inside_phase_self_heal_exception_boundary():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "dummy_fill_e2e.py").read_text(encoding="utf-8")
    bounded = "for attempt_index in range(max_phase_attempts):"
    until_complete = "while runtime_self_healer.until_complete or attempt_index < max_phase_attempts:"
    loop = source.index(until_complete if until_complete in source else bounded)
    try_pos = source.index("try:", loop)
    preflight = source.index("await runtime_self_healer.prepare_phase_attempt(", loop)
    except_pos = source.index("except Exception as exc:", preflight)
    execute = source.index("summary = await _execute_phase_once", preflight)
    assert try_pos < preflight < execute < except_pos
    assert '"failure_stage": attempt_stage' in source
