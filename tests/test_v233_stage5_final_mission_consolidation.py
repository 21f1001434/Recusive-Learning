from __future__ import annotations

from pathlib import Path

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.final_mission import FinalMissionConsolidator
from hip_id_agent.final_mission_uat import PHASES, run_final_mission_local_uat
from hip_id_agent.mission_controller import MissionController
from hip_id_agent.safe_io import safe_write_json


def _proof_run(tmp_path: Path, *, missing_judge: str = ""):
    mission = MissionController(tmp_path, run_id="T", phases=PHASES, mode="all_phases_until_complete")
    verifications = []
    for phase in PHASES:
        d = tmp_path / phase
        d.mkdir(parents=True, exist_ok=True)
        v = {"phase": phase, "status": "pass", "pass": True}
        safe_write_json(d / "phase_verification.json", v, mask=False)
        safe_write_json(d / "phase_exact_state_lock.json", {"exact_completion_checkpoint": {"pass": True}}, mask=False)
        if phase != missing_judge:
            safe_write_json(d / "section_judge_gate.json", {"pass": True}, mask=False)
        mission.mark_phase_complete(phase, attempt=1, judge_pass=True)
        verifications.append(v)
    return mission, verifications


def test_stage5_final_consolidator_passes_only_with_complete_current_run_proof(tmp_path: Path):
    mission, rows = _proof_run(tmp_path)
    result = FinalMissionConsolidator(tmp_path, run_id="T", phases=PHASES, mode="all_phases_until_complete").evaluate(
        mission=mission, terminal_gate={"pass": True}, phase_verifications=rows,
        witness_report={"status": "disabled", "pass": True}, transition_state={"pending": None},
    )
    assert result["pass"] is True
    assert result["application_complete"] is True
    assert all(x["pass"] for x in result["phases"])
    assert (tmp_path / "final_mission_consolidation.json").is_file()


def test_stage5_final_consolidator_fails_closed_on_missing_judge(tmp_path: Path):
    mission, rows = _proof_run(tmp_path, missing_judge="rule")
    result = FinalMissionConsolidator(tmp_path, run_id="T", phases=PHASES).evaluate(
        mission=mission, terminal_gate={"pass": True}, phase_verifications=rows, transition_state={"pending": None},
    )
    assert result["pass"] is False
    rule = next(x for x in result["phases"] if x["phase"] == "rule")
    assert rule["judge_pass"] is False


def test_stage5_final_consolidator_blocks_pending_handoff(tmp_path: Path):
    mission, rows = _proof_run(tmp_path)
    result = FinalMissionConsolidator(tmp_path, run_id="T", phases=PHASES).evaluate(
        mission=mission, terminal_gate={"pass": True}, phase_verifications=rows,
        transition_state={"pending": {"from_phase": "rule", "to_phase": "source_transport_profile"}},
    )
    assert result["pass"] is False
    assert result["no_pending_transition"] is False


def test_stage5_final_consolidator_respects_witness_safety(tmp_path: Path):
    mission, rows = _proof_run(tmp_path)
    result = FinalMissionConsolidator(tmp_path, run_id="T", phases=PHASES).evaluate(
        mission=mission, terminal_gate={"pass": True}, phase_verifications=rows,
        witness_report={"status": "enabled", "pass": False, "safety_pass": False}, transition_state={"pending": None},
    )
    assert result["pass"] is False
    assert result["witness_pass"] is False


def test_stage5_real_runner_invokes_final_consolidator_before_final_verdict():
    source = Path("hip_id_agent/dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert "FinalMissionConsolidator" in source
    assert source.index("final_consolidation = FinalMissionConsolidator") < source.index("mission_report = mission.write_final_verdict")
    assert '"final_mission_consolidation": final_consolidation' in source


def test_stage5_cli_exposes_final_mission_uat_command():
    source = Path("hip_id_agent/cli.py").read_text(encoding="utf-8")
    assert '@app.command("certify-final-mission")' in source
    assert "run_final_mission_local_uat" in source


@pytest.mark.asyncio
async def test_stage5_browser_uat_covers_7_phases_and_lifecycle(tmp_path: Path):
    cfg = AppConfig()
    result = await run_final_mission_local_uat(config=cfg, output_dir=tmp_path / "uat", headless=True)
    assert result["pass"] is True
    assert result["phase_count"] == 7
    assert all(x["pass"] for x in result["phases"])
    assert result["lifecycle"] == {"edit": True, "save": True, "validate": True, "deploy": True, "final_status": "Deployed"}
    assert result["pyautogui_mcp_contract_used"] is True
    assert result["final_mission_consolidation"]["pass"] is True
    src = next(x for x in result["phases"] if x["phase"] == "source_transport_profile")
    tgt = next(x for x in result["phases"] if x["phase"] == "target_transport_profile")
    src_choices = [d.get("selected_option") for d in src["dynamic_option_decisions"]]
    tgt_choices = [d.get("selected_option") for d in tgt["dynamic_option_decisions"]]
    assert "da-sender-sftphaft-dce-shared" in src_choices
    assert "pt-receiver-sftphaft-dce-shared" in tgt_choices
