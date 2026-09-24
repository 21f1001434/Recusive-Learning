from __future__ import annotations

import json
from pathlib import Path

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.portal_brain import PortalBrain
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


TARGET = "https://developer.dell.com/hybrid-integrations/securelink/doctypes"


class _Body:
    async def inner_text(self, timeout=0):
        return "Developer SecureLink Document Type Document Identifier Create Document Type"


class _Page:
    def __init__(self):
        self.url = TARGET

    def locator(self, selector):
        assert selector == "body"
        return _Body()


class _Browser:
    def __init__(self):
        self.page = _Page()
        self.calls = []
        self.console_messages = []
        self.network_tab_events = []
        self.action_events = []

    async def _observe_react_navigation_state(self, target_url):
        return {
            "current_url": self.page.url,
            "target_url": target_url,
            "target_match": self.page.url == target_url,
            "target_usable": True,
            "same_actual_surface": True,
            "logged_in": True,
        }

    async def screenshot(self, path, full_page=True):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")
        return str(path)

    async def save_dom_snapshot(self, name):
        return {"html": f"{name}.html", "text": f"{name}.txt"}

    async def _ensure_active_page(self, target_url=""):
        self.calls.append("ensure_page")
        return self.page

    async def _react_ensure_target_surface(self, target_url, max_steps=4):
        self.calls.append("route_target")
        self.page.url = target_url
        return {"pass": True}

    async def _consolidate_session_pages(self, target_url=""):
        self.calls.append("consolidate")
        return {"pass": True}

    async def _verify_dual_mcp_same_surface(self, *args, **kwargs):
        self.calls.append("resync_mcp")
        return {"pass": True}

    async def wait_for_blocking_overlays_gone(self, timeout_ms=0):
        self.calls.append("wait_overlay")
        return True

    async def _dismiss_transient_ui(self, next_phase=""):
        self.calls.append("dismiss")
        return {"pass": True}

    async def _ensure_page_observers(self):
        self.calls.append("observers")

    async def goto_base_and_complete_sso(self, target_url):
        self.calls.append("goto")
        self.page.url = target_url


@pytest.mark.asyncio
async def test_route_failure_uses_safe_react_repair_and_retries(tmp_path: Path):
    cfg = AppConfig()
    cfg.aia.enabled = False
    browser = _Browser()
    controller = RuntimeSelfHealController(
        config=cfg,
        root_dir=tmp_path,
        browser=browser,
        run_id="run-1",
    )
    decision = await controller.handle_failure(
        phase="source_document_type",
        target_url=TARGET,
        attempt=1,
        message="HIP_ROUTE_NOT_COMMITTED: browser stayed on Data Maps",
    )
    assert decision.classification == "route_not_committed"
    assert decision.action == "route_target"
    assert decision.retry is True
    assert "route_target" in browser.calls
    assert (tmp_path / "runtime_self_heal" / "runtime_self_heal_summary.json").exists()


@pytest.mark.asyncio
async def test_repeated_identical_failure_is_bounded_and_fail_closed(tmp_path: Path):
    cfg = AppConfig()
    cfg.aia.enabled = False
    cfg.runtime_self_heal.max_repeated_failure_signature = 2
    browser = _Browser()
    controller = RuntimeSelfHealController(config=cfg, root_dir=tmp_path, browser=browser)
    results = []
    for attempt in (1, 2, 3):
        results.append(await controller.handle_failure(
            phase="source_document_type",
            target_url=TARGET,
            attempt=attempt,
            message="required semantic control not found for attribute_usage",
        ))
    assert results[0].retry is True
    assert results[1].retry is True
    assert results[2].retry is False
    assert results[2].action == "stop_fail_closed"


def test_unsafe_final_mutation_never_enters_repair_loop():
    classification = RuntimeSelfHealController.classify_failure(
        "unsafe final action blocked: Submit/Create mutation requested"
    )
    assert classification == "unsafe_or_mutating"


def test_portal_brain_promotes_recovery_only_after_judge_pass(tmp_path: Path):
    brain = PortalBrain(tmp_path / "brain")
    recovery_id = brain.record_runtime_recovery(
        phase="rule",
        classification="repeatable_row_mismatch",
        signature="abc123",
        action="reopen_phase_from_input",
        outcome="candidate",
        run_id="run-1",
        evidence_dir="evidence/run-1",
    )
    before = brain.phase_bundle("rule")
    row = next(x for x in before["blueprint"]["failure_recovery"] if x["recovery_id"] == recovery_id)
    assert row["candidate_count"] == 1
    assert row["validated_count"] == 0

    result = brain.promote_runtime_recovery(
        phase="rule",
        recovery_id=recovery_id,
        judge_pass=True,
        run_id="run-1",
    )
    assert result["status"] == "validated"
    after = brain.phase_bundle("rule")
    row = next(x for x in after["blueprint"]["failure_recovery"] if x["recovery_id"] == recovery_id)
    assert row["validated_count"] == 1
    assert row["trust"] == "validated_recovery"


def test_runtime_self_heal_defaults_are_enabled_and_bounded():
    cfg = AppConfig()
    assert cfg.runtime_self_heal.enabled is True
    assert cfg.runtime_self_heal.max_phase_attempts == 5
    assert cfg.runtime_self_heal.max_total_repairs == 20
    assert cfg.runtime_self_heal.fail_closed is True


def test_full_run_wires_self_heal_around_execution_and_judge():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert "RuntimeSelfHealController" in source
    assert 'failure_kind="execution_exception"' in source
    assert 'failure_kind="judge"' in source
    assert "runtime_self_healer.finalize_phase(phase, judge_pass=True)" in source
    assert "runtime_self_heal_summary" in source

@pytest.mark.asyncio
async def test_until_complete_extends_attempt_budget_but_still_stops_on_no_progress(tmp_path: Path):
    cfg = AppConfig()
    cfg.aia.enabled = False
    cfg.runtime_self_heal.until_complete = True
    cfg.runtime_self_heal.max_phase_attempts = 1
    cfg.runtime_self_heal.max_total_repairs = 1
    cfg.runtime_self_heal.max_repeated_failure_signature = 1
    cfg.runtime_self_heal.max_no_progress_repeats = 3
    browser = _Browser()
    controller = RuntimeSelfHealController(
        config=cfg,
        root_dir=tmp_path,
        browser=browser,
        until_complete=True,
    )

    decisions = []
    for attempt in range(1, 5):
        decisions.append(await controller.handle_failure(
            phase="rule",
            target_url=TARGET,
            attempt=attempt,
            message="required semantic control not found for Mapping Identifier Name (Version)",
        ))

    assert [row.retry for row in decisions] == [True, True, True, False]
    assert [row.action for row in decisions[:3]] == [
        "refresh_evidence_and_reopen",
        "reopen_phase_from_input",
        "refresh_evidence_and_reopen",
    ]
    assert decisions[3].action == "stop_fail_closed"
    assert "NO_PROGRESS_STALL_GUARD" in decisions[3].reason
    summary = controller.summary()
    assert summary["until_complete"] is True
    assert summary["total_repairs_executed"] == 3


class _VisualFeedbackAgent:
    def __init__(self):
        self.calls = []

    def diagnose_visual_state(self, *, screenshot, golden_screenshots, expected):
        self.calls.append((screenshot, list(golden_screenshots), expected))
        return {
            "status": "ok",
            "pass": False,
            "visible_issues": [{
                "field": "Mapping Identifier Name (Version)",
                "expected": "DELLCoXMLASNXX08C_U-HAUL(1.0)",
                "observed": "Select",
            }],
        }


@pytest.mark.asyncio
async def test_failure_evidence_includes_golden_image_vision_feedback(tmp_path: Path):
    cfg = AppConfig()
    cfg.aia.enabled = False
    browser = _Browser()
    golden = tmp_path / "Rules.png"
    golden.write_bytes(b"golden")
    agent = _VisualFeedbackAgent()
    controller = RuntimeSelfHealController(
        config=cfg,
        root_dir=tmp_path,
        browser=browser,
        golden_references_by_phase={"rule": [{"path": str(golden)}]},
        expected_inputs_by_phase={"rule": {"objects": {"rule": {"name": "RULE"}}}},
        visual_feedback_agent=agent,
    )

    decision = await controller.handle_failure(
        phase="rule",
        target_url=TARGET,
        attempt=1,
        message="Mapping Identifier Name (Version) not found",
    )
    evidence = json.loads((Path(decision.evidence_dir) / "failure_evidence.json").read_text())
    assert evidence["golden_screenshots"] == [str(golden)]
    assert evidence["golden_visual_feedback"]["status"] == "ok"
    assert evidence["golden_visual_feedback"]["visible_issues"][0]["field"] == "Mapping Identifier Name (Version)"
    assert len(agent.calls) == 1


def test_rules_only_and_until_complete_cli_flags_are_wired():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    flow_source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert '"--rules-only"' in source
    assert '"--runtime-self-heal-until-complete/--bounded-runtime-self-heal"' in source
    assert 'selected_phases = ["rule"]' in source
    assert "while runtime_self_healer.until_complete or attempt_index < max_phase_attempts" in flow_source
