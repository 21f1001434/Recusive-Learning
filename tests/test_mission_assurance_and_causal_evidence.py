from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.form_api_agent import (
    build_action_api_causal_trace,
    build_api_error_ledger,
    build_redacted_har,
)
from hip_id_agent.mission_assurance import (
    build_phase_assurance_report,
    capture_mcp_evidence_quorum,
)
from hip_id_agent.models import ActionEvent


def _assurance_kwargs():
    return dict(
        phase="rule",
        attempt=2,
        verification={"status": "pass"},
        judge_result={"pass": True},
        maximum_observability={"coverage_pass": True, "replay_ready": True},
        api_result={"pass": True},
        trajectory_result={
            "trust": "validated",
            "trajectory_fingerprint": "abc123",
            "judge_pass": True,
            "parent_child_contract": {"contract_fingerprint": "dep123"},
            "golden_references": [{"file": "Rules.png"}],
        },
        mcp_quorum={"pass": True},
        strict=True,
    )


def test_phase_assurance_exact_current_form_and_learning_promotion_both_pass() -> None:
    report = build_phase_assurance_report(**_assurance_kwargs())
    assert report["pass"] is True
    assert report["promotion_allowed"] is True
    assert report["missing_assurance"] == []


def test_phase_assurance_stale_mcp_or_missing_api_disables_learning_not_current_completion() -> None:
    kwargs = _assurance_kwargs()
    kwargs["mcp_quorum"] = {"pass": False}
    kwargs["api_result"] = {"pass": False}
    report = build_phase_assurance_report(**kwargs)
    assert report["pass"] is True
    assert report["completion_pass"] is True
    assert report["promotion_allowed"] is False
    assert report["missing_assurance"] == []
    assert "mcp_evidence_quorum" in report["missing_learning_promotion"]
    assert "ui_api_capture" in report["missing_learning_promotion"]


def test_phase_assurance_still_blocks_incomplete_live_form() -> None:
    kwargs = _assurance_kwargs()
    kwargs["maximum_observability"] = {"coverage_pass": False, "replay_ready": True}
    report = build_phase_assurance_report(**kwargs)
    assert report["pass"] is False
    assert "input_control_coverage" in report["missing_assurance"]


def test_ui_api_causal_trace_uses_exact_action_request_ids() -> None:
    action = ActionEvent(
        action_id="act-00001",
        type="fill",
        target="Mapping Identifier",
        network_events_triggered=["req-1", "req-2"],
        network_event_start_index=10,
        network_event_end_index=12,
    )
    transactions = [
        {
            "transaction_id": "req-1",
            "method": "GET",
            "endpoint_template": "https://hip/api/maps",
            "endpoint_kind": "reference_or_read",
            "response": {"status": 200, "payload_captured": True},
            "request": {"payload_captured": False},
        },
        {
            "transaction_id": "req-2",
            "method": "POST",
            "endpoint_template": "https://hip/api/validate",
            "endpoint_kind": "validation",
            "response": {"status": 200, "payload_captured": True},
            "request": {"payload_captured": True},
        },
        {
            "transaction_id": "orphan",
            "method": "GET",
            "endpoint_template": "https://hip/api/background",
            "endpoint_kind": "read",
            "response": {"status": 200},
            "request": {},
        },
    ]
    trace = build_action_api_causal_trace([action], transactions)
    assert trace["linked_transaction_count"] == 2
    assert trace["actions"][0]["triggered_api_count"] == 2
    assert len(trace["unlinked_transactions"]) == 1


def test_redacted_har_and_error_ledger_keep_forensic_response_data() -> None:
    transactions = [{
        "transaction_id": "req-500",
        "timestamp": "2026-08-12T00:00:00Z",
        "stage": "rule_mapping",
        "method": "POST",
        "url_redacted": "https://hip/api/validate?q=%3Credacted%3E",
        "endpoint_template": "https://hip/api/validate",
        "endpoint_kind": "validation",
        "resource_type": "xhr",
        "mutation_capable": False,
        "request": {"headers_redacted": {"Authorization": "***MASKED***"}, "payload_redacted": {"name": "x"}},
        "response": {"status": 500, "headers_redacted": {}, "mime_type": "application/json", "payload_redacted": {"error": "bad"}, "capture_status": "captured", "truncated": False},
        "error": "",
    }]
    har = build_redacted_har(transactions, title="test")
    assert har["log"]["entries"][0]["response"]["status"] == 500
    assert har["log"]["entries"][0]["request"]["headers"]["Authorization"] == "***MASKED***"
    ledger = build_api_error_ledger(transactions)
    assert ledger["error_count"] == 1
    assert ledger["errors"][0]["status"] == 500


class _PW:
    async def get_current_url(self):
        return "https://developer.dell.com/hip/rules?x=1"

    async def snapshot(self, **kwargs):
        return {"tree": ["Rule", "Actions", "Conditions"]}


class _CDP:
    async def get_current_url(self):
        return "https://developer.dell.com/hip/rules#drawer"

    async def get_dom_snapshot(self):
        return {"nodes": [1, 2, 3]}


class _HIP:
    async def build_representation(self, payload):
        return {"structural_fingerprint": "hip-abc", "phase": payload["phase"]}


def test_fresh_three_mcp_quorum_passes_same_surface(tmp_path: Path) -> None:
    browser = SimpleNamespace(
        page=SimpleNamespace(url="https://developer.dell.com/hip/rules"),
        playwright_mcp_backend=_PW(),
        mcp_backend=_CDP(),
    )
    agentq = SimpleNamespace(mcp_backend=_HIP())
    result = asyncio.run(capture_mcp_evidence_quorum(
        browser=browser,
        agentq_controller=agentq,
        phase="rule",
        attempt=1,
        phase_dir=tmp_path,
        required=True,
    ))
    assert result["pass"] is True
    assert all(row["pass"] for row in result["channels"].values())
    assert (tmp_path / "mcp_evidence_quorum.json").exists()


def test_autonomous_cli_profile_enables_complete_agentq_ui_api_stack() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    block = source[source.index("if autonomous_mission:"):source.index("if all_phases_until_complete:")]
    for required in (
        "agentq_crawler_fusion = True",
        "dual_ui_api = True",
        "capture_submit_api = True",
    ):
        assert required in block
    # V232 autonomous execution uses a healthy executor quorum by default.
    # Strict all-MCP enforcement remains opt-in via --require-mcp.
    assert "require_mcp = True" not in block
    # Completion-first operational missions still capture UI/API evidence, but
    # missing capture is not allowed to reopen a deterministically complete form.
    assert "require_api_capture = True" not in block
    assert "strict_mission_assurance=bool(autonomous_mission and require_api_capture)" in source


def test_assurance_gate_runs_before_flow_memory_promotion() -> None:
    root = Path(__file__).resolve().parents[1]
    flow = (root / "hip_id_agent" / "dummy_fill_e2e.py").read_text(encoding="utf-8")
    assurance = flow.index('safe_write_json(phase_dir / "phase_mission_assurance.json"')
    promotion = flow.index("flow_pattern_memory.promote_from_phase_dir", assurance)
    assert assurance < promotion
    assert "capture_mcp_evidence_quorum" in flow


def test_browser_action_network_slice_is_not_last_ten_heuristic() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "hip_id_agent" / "browser_session.py").read_text(encoding="utf-8")
    assert "network_event_start_index=len(self.network_tab_events)" in source
    assert "self.network_tab_events[start:ev.network_event_end_index]" in source
    assert "self.network_tab_events[-10:]" not in source[source.index("async def _finish_action"):source.index("@staticmethod", source.index("async def _finish_action"))]


def test_strict_assured_prior_run_requires_phase_assurance_for_resume(tmp_path: Path) -> None:
    from hip_id_agent.mission_controller import MissionController, MISSION_STATE_FILENAME, PHASE_VERIFICATION_FILENAME
    import json
    run = tmp_path / "prior"
    phase = run / "rule"
    phase.mkdir(parents=True)
    (run / MISSION_STATE_FILENAME).write_text(json.dumps({"mode": "autonomous_assured_all_phases"}), encoding="utf-8")
    (phase / "phase_exact_state_lock.json").write_text(json.dumps({"exact_completion_checkpoint": {"pass": True}}), encoding="utf-8")
    (phase / "section_judge_gate.json").write_text(json.dumps({"pass": True}), encoding="utf-8")
    (phase / PHASE_VERIFICATION_FILENAME).write_text(json.dumps({"phase": "rule", "status": "pass"}), encoding="utf-8")
    assert MissionController.phase_completion_proof(phase)["adoptable"] is False
    (phase / "phase_mission_assurance.json").write_text(json.dumps({"pass": True}), encoding="utf-8")
    proof = MissionController.phase_completion_proof(phase)
    assert proof["mission_assurance_required"] is True
    assert proof["mission_assurance_pass"] is True
    assert proof["adoptable"] is True


def test_config_yaml_uses_real_api_config_field_names() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "build_ui_api_crosswalk: true" in text
    assert "replay_observed_validation_requests: true" in text
    assert "build_input_ui_api_crosswalk" not in text
    assert "replay_validation_endpoints" not in text
