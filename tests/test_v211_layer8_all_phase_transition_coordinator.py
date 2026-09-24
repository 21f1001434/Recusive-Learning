import json
from pathlib import Path

from hip_id_agent.mission_controller import MissionController
from hip_id_agent.phase_transition import MissionTransitionCoordinator, next_executable_phase


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _proof(run_dir: Path, phase: str, *, assurance=False):
    phase_dir = run_dir / phase
    _write(phase_dir / "phase_exact_state_lock.json", {"exact_completion_checkpoint": {"pass": True}})
    _write(phase_dir / "section_judge_gate.json", {"pass": True})
    _write(phase_dir / "phase_verification.json", {"status": "pass", "phase": phase})
    if assurance:
        _write(phase_dir / "phase_mission_assurance.json", {"pass": True, "missing_assurance": []})


def test_next_executable_skips_resumed_complete_phases():
    statuses = {"data_map": "complete", "source_document_type": "complete", "target_document_type": "complete", "rule": "pending"}
    plan = next_executable_phase(list(statuses), "data_map", statuses.__getitem__)
    assert plan["next_phase"] == "rule"
    assert plan["skipped_completed_phases"] == ["source_document_type", "target_document_type"]
    assert plan["terminal"] is False


def test_next_executable_terminal_when_only_completed_phases_remain():
    phases = ["data_map", "source_document_type", "target_document_type"]
    statuses = {p: "complete" for p in phases}
    plan = next_executable_phase(phases, "data_map", statuses.__getitem__)
    assert plan["next_phase"] == ""
    assert plan["terminal"] is True
    assert plan["skipped_completed_phases"] == ["source_document_type", "target_document_type"]


def test_resume_adoption_materializes_current_run_proof_snapshot(tmp_path: Path):
    source = tmp_path / "source"
    current = tmp_path / "current"
    src = MissionController(source, run_id="src", phases=["data_map"], mode="bounded_self_heal")
    _proof(source, "data_map")
    src.mark_phase_complete("data_map", attempt=1, judge_pass=True)

    mission = MissionController(current, run_id="dst", phases=["data_map"], mode="bounded_self_heal")
    adopted = mission.adopt_completed_phases(source)
    assert adopted["adopted_phases"] == ["data_map"]
    phase_dir = current / "data_map"
    assert (phase_dir / "phase_exact_state_lock.json").is_file()
    assert (phase_dir / "section_judge_gate.json").is_file()
    assert mission.current_run_completion_proof("data_map")["adoptable"] is True

    # Certification remains local even if the old run is later removed.
    for child in sorted(source.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    source.rmdir()
    assert mission.current_run_completion_proof("data_map")["adoptable"] is True


def test_assured_current_mission_rejects_non_assured_source(tmp_path: Path):
    source = tmp_path / "source"
    current = tmp_path / "current"
    src = MissionController(source, run_id="src", phases=["data_map"], mode="bounded_self_heal")
    _proof(source, "data_map", assurance=False)
    src.mark_phase_complete("data_map", attempt=1, judge_pass=True)

    mission = MissionController(current, run_id="dst", phases=["data_map"], mode="autonomous_assured_all_phases")
    adopted = mission.adopt_completed_phases(source)
    assert adopted["adopted_phases"] == []
    assert "assured" in adopted["skipped"][0]["reason"]
    assert mission.phase_status("data_map") == "pending"


def test_transition_requires_exact_destination_ack(tmp_path: Path):
    c = MissionTransitionCoordinator(tmp_path, run_id="r", phases=["data_map", "rule"])
    c.arm(from_phase="data_map", to_phase="rule", source_status="complete", source_exact_verified=True)
    bad = c.acknowledge(phase="rule", attempt=1, destination_route_verified=True, dual_mcp_verified=False)
    assert bad["pass"] is False
    assert c.state["pending"] is not None
    good = c.acknowledge(phase="rule", attempt=2, destination_route_verified=True, dual_mcp_verified=True)
    assert good["pass"] is True
    assert c.state["pending"] is None


def test_transition_ack_fails_closed_on_wrong_destination(tmp_path: Path):
    c = MissionTransitionCoordinator(tmp_path, run_id="r", phases=["data_map", "rule", "biz_flow"])
    c.arm(from_phase="data_map", to_phase="rule", source_status="complete", source_exact_verified=True)
    result = c.acknowledge(phase="biz_flow", attempt=1, destination_route_verified=True, dual_mcp_verified=True)
    assert result["pass"] is False
    assert result["code"] == "HIP_PHASE_TRANSITION_DESTINATION_MISMATCH"


def test_terminal_gate_requires_local_proof_and_no_pending_transition(tmp_path: Path):
    phases = ["data_map", "source_document_type"]
    mission = MissionController(tmp_path, run_id="r", phases=phases, mode="bounded_self_heal")
    for phase in phases:
        _proof(tmp_path, phase)
        mission.mark_phase_complete(phase, attempt=1, judge_pass=True)
    c = MissionTransitionCoordinator(tmp_path, run_id="r", phases=phases)
    assert c.terminal_gate(mission)["pass"] is True
    c.arm(from_phase="data_map", to_phase="source_document_type", source_status="complete", source_exact_verified=True)
    blocked = c.terminal_gate(mission)
    assert blocked["pass"] is False
    assert blocked["unacknowledged_transition"] is not None


def test_final_verdict_cannot_claim_complete_when_terminal_gate_fails(tmp_path: Path):
    mission = MissionController(tmp_path, run_id="r", phases=["data_map"], mode="bounded_self_heal")
    mission.mark_phase_complete("data_map", attempt=1, judge_pass=True)
    report = mission.write_final_verdict(overall_status="pass", terminal_gate_pass=False)
    assert report["application_complete"] is False
    assert report["mission_status"] == "blocked"
    assert report["phase_status_complete"] is True
    assert report["terminal_gate_pass"] is False


def test_live_controller_uses_transition_coordinator_and_destination_ack():
    source = Path("hip_id_agent/dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert "MissionTransitionCoordinator" in source
    assert "transition_coordinator.plan_after(phase, mission.phase_status)" in source
    assert "transition_coordinator.acknowledge(" in source
    assert "phase_transition_ack_attempt_" in source
    assert "phases.index(phase)" not in source[source.index("for phase_index, phase in enumerate(phases):"):]
