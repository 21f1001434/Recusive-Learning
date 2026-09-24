from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from hip_id_agent.mission_controller import (
    ENTITY_REGISTRY_FILENAME,
    MISSION_REPORT_JSON,
    MISSION_REPORT_MD,
    MISSION_STATE_FILENAME,
    PHASE_JUDGE_RESULT_FILENAME,
    PHASE_VERIFICATION_FILENAME,
    RESUME_ADOPTION_FILENAME,
    MissionController,
)
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController

PHASES = [
    "data_map",
    "source_document_type",
    "target_document_type",
    "rule",
    "source_transport_profile",
    "target_transport_profile",
    "biz_flow",
]


def _read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _seed_completed_phase(run_dir: Path, phase: str) -> None:
    """Write the full fail-closed adoption proof for one phase."""
    phase_dir = run_dir / phase
    _write(phase_dir / "phase_exact_state_lock.json", {
        "schema_version": "hip.all-phase-exact-state-lock.v1",
        "phase": phase,
        "exact_completion_checkpoint": {"pass": True, "status": "exact_live_execution_completed"},
    })
    _write(phase_dir / "section_judge_gate.json", {"pass": True, "phase": phase})
    _write(phase_dir / PHASE_VERIFICATION_FILENAME, {
        "phase": phase,
        "status": "pass",
        "screenshots": [str(phase_dir / "screenshots" / "final.png")],
    })
    _write(phase_dir / PHASE_JUDGE_RESULT_FILENAME, {"pass": True, "phase": phase})


def _seed_prior_run(runs_root: Path, name: str, completed: list[str]) -> Path:
    run_dir = runs_root / name
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": MissionController.SCHEMA,
        "mission_status": "in_progress",
        "phase_sequence": PHASES,
        "phases": {p: {"status": "complete" if p in completed else "pending"} for p in PHASES},
    }
    _write(run_dir / MISSION_STATE_FILENAME, state)
    for phase in completed:
        _seed_completed_phase(run_dir, phase)
    return run_dir


# ---------------------------------------------------------------- mission ledger

def test_mission_state_lifecycle_and_final_verdict(tmp_path: Path):
    mission = MissionController(tmp_path, run_id="run-1", phases=PHASES)
    assert (tmp_path / MISSION_STATE_FILENAME).is_file()
    assert mission.incomplete_phases() == PHASES

    for phase in PHASES:
        mission.mark_phase_started(phase, attempt=1)
        assert mission.phase_status(phase) == "in_progress"
        mission.mark_phase_complete(phase, attempt=1, judge_pass=True)
        assert mission.phase_status(phase) == "complete"

    report = mission.write_final_verdict(overall_status="pass")
    assert report["application_complete"] is True
    assert report["mission_status"] == "complete"
    on_disk = _read(tmp_path / MISSION_REPORT_JSON)
    assert on_disk["application_complete"] is True
    md = (tmp_path / MISSION_REPORT_MD).read_text(encoding="utf-8")
    assert "Application complete: **yes**" in md
    state = _read(tmp_path / MISSION_STATE_FILENAME)
    assert state["mission_status"] == "complete"


def test_mission_blocked_phase_prevents_application_complete(tmp_path: Path):
    mission = MissionController(tmp_path, run_id="run-2", phases=PHASES)
    mission.mark_phase_complete("data_map", attempt=1, judge_pass=True)
    mission.mark_phase_blocked("source_document_type", attempt=3, reason="section judge blocked")
    report = mission.write_final_verdict(overall_status="blocked_by_section_judge", blocked_phase="source_document_type")
    assert report["application_complete"] is False
    assert report["mission_status"] == "blocked"
    assert report["phases"]["source_document_type"]["status"] == "blocked"
    # Blocked reason must survive masking and truncation.
    assert "section judge blocked" in report["phases"]["source_document_type"]["blocked_reason"]


# -------------------------------------------------------------- resume adoption

def test_phase_completion_proof_is_fail_closed(tmp_path: Path):
    phase_dir = tmp_path / "rule"
    # Nothing on disk -> not adoptable.
    assert MissionController.phase_completion_proof(phase_dir)["adoptable"] is False

    # Lock alone is not enough.
    _write(phase_dir / "phase_exact_state_lock.json", {"exact_completion_checkpoint": {"pass": True}})
    assert MissionController.phase_completion_proof(phase_dir)["adoptable"] is False

    # Lock + judge but no persisted verification payload -> still not adoptable.
    _write(phase_dir / "section_judge_gate.json", {"pass": True})
    assert MissionController.phase_completion_proof(phase_dir)["adoptable"] is False

    # Full proof -> adoptable.
    _write(phase_dir / PHASE_VERIFICATION_FILENAME, {"phase": "rule", "status": "pass"})
    assert MissionController.phase_completion_proof(phase_dir)["adoptable"] is True

    # A failing judge gate must reject adoption.
    _write(phase_dir / "section_judge_gate.json", {"pass": False})
    assert MissionController.phase_completion_proof(phase_dir)["adoptable"] is False

    # An unresolved block diagnosis must reject adoption even with passing gates.
    _write(phase_dir / "section_judge_gate.json", {"pass": True})
    _write(phase_dir / "section_judge_block_diagnosis.json", {"reasons": [{"code": "X"}]})
    assert MissionController.phase_completion_proof(phase_dir)["adoptable"] is False


def test_adopt_completed_phases_skips_unproven(tmp_path: Path):
    runs = tmp_path / "runs"
    prior = _seed_prior_run(runs, "20260721-010101-old", completed=["data_map", "source_document_type"])
    # Corrupt one phase's judge gate so only data_map is adoptable.
    _write(prior / "source_document_type" / "section_judge_gate.json", {"pass": False})

    new_run = runs / "20260721-020202-new"
    new_run.mkdir(parents=True)
    mission = MissionController(new_run, run_id="new", phases=PHASES)
    result = mission.adopt_completed_phases(prior)

    assert result["resumed"] is True
    assert result["adopted_phases"] == ["data_map"]
    skipped_phases = {row["phase"] for row in result["skipped"]}
    assert "source_document_type" in skipped_phases
    assert mission.phase_status("data_map") == "complete"
    assert mission.phase_status("source_document_type") == "pending"

    adoption = _read(new_run / "data_map" / RESUME_ADOPTION_FILENAME)
    assert adoption["browser_replay_performed"] is False
    assert adoption["source_run_dir"] == str(prior)
    # Verification and judge payloads are materialized for the aggregate report.
    assert _read(new_run / "data_map" / PHASE_VERIFICATION_FILENAME)["status"] == "pass"
    assert _read(new_run / "data_map" / PHASE_JUDGE_RESULT_FILENAME)["pass"] is True
    state = _read(new_run / MISSION_STATE_FILENAME)
    assert state["resume"]["adopted_phases"] == ["data_map"]


def test_find_resumable_run_prefers_newest_and_ignores_complete(tmp_path: Path):
    runs = tmp_path / "runs"
    older = _seed_prior_run(runs, "a-older", completed=["data_map"])
    newer = _seed_prior_run(runs, "b-newer", completed=["data_map", "rule"])
    finished = _seed_prior_run(runs, "c-finished", completed=PHASES)
    finished_state = _read(finished / MISSION_STATE_FILENAME)
    finished_state["mission_status"] = "complete"
    _write(finished / MISSION_STATE_FILENAME, finished_state)
    # Make mtimes deterministic: newer must be newest resumable.
    import os, time
    now = time.time()
    os.utime(older, (now - 200, now - 200))
    os.utime(newer, (now - 10, now - 10))
    os.utime(finished, (now, now))

    found = MissionController.find_resumable_run(runs)
    assert found is not None and found.name == "b-newer"

    # Excluding the current run and the newest leaves the older one.
    found2 = MissionController.find_resumable_run(runs, exclude_run=newer)
    assert found2 is not None and found2.name == "a-older"

    # No resumable run at all -> None.
    empty = tmp_path / "empty_runs"
    empty.mkdir()
    assert MissionController.find_resumable_run(empty) is None


def test_adoption_carries_entity_registry_forward(tmp_path: Path):
    runs = tmp_path / "runs"
    prior = _seed_prior_run(runs, "prior", completed=["data_map"])
    _write(prior / ENTITY_REGISTRY_FILENAME, {
        "entities": {"data_map": {"phase": "data_map", "display_names": ["UHAUL_POASN_DM"]}},
    })
    new_run = runs / "resumed"
    new_run.mkdir(parents=True)
    mission = MissionController(new_run, run_id="r", phases=PHASES)
    mission.adopt_completed_phases(prior)
    registry = _read(new_run / ENTITY_REGISTRY_FILENAME)
    assert registry["entities"]["data_map"]["display_names"] == ["UHAUL_POASN_DM"]
    assert registry["entities"]["data_map"]["adopted_from"] == str(prior)
    # Later phases see earlier entities only.
    prior_for_rule = mission.prior_entities_for("rule")
    assert "data_map" in prior_for_rule
    assert mission.prior_entities_for("data_map") == {}


# ----------------------------------------------------------- entity extraction

def test_entity_name_extraction_handles_key_styles(tmp_path: Path):
    mission = MissionController(tmp_path, run_id="e", phases=["data_map", "rule"])
    entry = mission.record_phase_entities(
        "data_map",
        phase_input={
            "data_map": {"dataMapName": "DM_850_TO_POASN", "description": "x"},
            "items": [{"name": "DM_850_TO_POASN"}, {"rule-name": "RL_QTY_CHECK"}],
            "password": "must-not-appear",
        },
        summary={"form_url": "https://developer.dell.com/hybrid-integrations/bizlink/datamaps"},
    )
    assert "DM_850_TO_POASN" in entry["display_names"]
    assert "RL_QTY_CHECK" in entry["display_names"]
    assert entry["display_names"].count("DM_850_TO_POASN") == 1
    registry = _read(tmp_path / ENTITY_REGISTRY_FILENAME)
    assert registry["promoted_to_portal_brain"] is False
    assert "must-not-appear" not in json.dumps(registry)


# ------------------------------------------- browser-disconnect self-heal logic

def test_classify_failure_detects_browser_disconnect():
    for message in [
        "Target page, context or browser has been closed",
        "Browser has been closed",
        "browser has disconnected unexpectedly",
        "Connection closed while reading from the driver",
        "chrome crashed with SIGSEGV",
    ]:
        assert RuntimeSelfHealController.classify_failure(message) == "browser_disconnected", message


def test_browser_disconnect_action_ladder_is_safe():
    ladder = RuntimeSelfHealController.CLASS_ACTIONS["browser_disconnected"]
    assert ladder[0] == "restart_browser_session"
    assert set(ladder) <= RuntimeSelfHealController.SAFE_ACTIONS
    assert "restart_browser_session" in RuntimeSelfHealController.SAFE_ACTIONS


def test_restart_browser_session_action_restarts_and_reroutes(tmp_path: Path):
    calls: list[str] = []

    class _Browser:
        async def restart(self, *, reason: str = ""):
            calls.append(f"restart:{reason}")
            return {"status": "restarted", "start_count": 2}

        async def goto_base_and_complete_sso(self, url: str):
            calls.append(f"sso:{url}")

    class _Policy:
        enabled = True
        until_complete = True
        exploration_exploitation = True
        max_phase_attempts = 5
        max_total_repairs = 20
        max_repeated_failure_signature = 2
        use_aia_advisor = False
        aia_advisor_timeout_seconds = 5
        capture_evidence = False
        forensic_evidence = False
        forensic_event_window = 50
        retry_unknown_once = True
        fail_closed = True

    class _Cfg:
        runtime_self_heal = _Policy()

    controller = RuntimeSelfHealController(
        config=_Cfg(),
        root_dir=tmp_path,
        browser=_Browser(),
        enabled=True,
        until_complete=True,
    )
    result = asyncio.run(
        controller._execute_action(
            "restart_browser_session",
            phase="rule",
            target_url="https://developer.dell.com/hybrid-integrations/bizlink/rules",
        )
    )
    assert result["success"] is True
    assert result["browser_restart"]["status"] == "restarted"
    assert calls[0].startswith("restart:")
    assert calls[1].startswith("sso:https://developer.dell.com")


def test_restart_action_fails_closed_without_restart_support(tmp_path: Path):
    class _Policy:
        enabled = True
        until_complete = False
        exploration_exploitation = True
        max_phase_attempts = 5
        max_total_repairs = 20
        max_repeated_failure_signature = 2
        use_aia_advisor = False
        aia_advisor_timeout_seconds = 5
        capture_evidence = False
        forensic_evidence = False
        forensic_event_window = 50
        retry_unknown_once = True
        fail_closed = True

    class _Cfg:
        runtime_self_heal = _Policy()

    class _LegacyBrowser:
        pass

    controller = RuntimeSelfHealController(
        config=_Cfg(),
        root_dir=tmp_path,
        browser=_LegacyBrowser(),
        enabled=True,
    )
    result = asyncio.run(
        controller._execute_action(
            "restart_browser_session",
            phase="rule",
            target_url="https://example.invalid",
        )
    )
    assert result["success"] is False
    assert "restart" in str(result.get("error", "")).lower()


# --------------------------------------------------- flow options and CLI wiring

def test_full_dummy_fill_options_expose_resume_fields():
    from hip_id_agent.dummy_fill_e2e import FullDummyFillOptions

    opts = FullDummyFillOptions()
    assert opts.resume_run_dir is None
    assert opts.auto_resume is False
    opts2 = FullDummyFillOptions(resume_run_dir="C:/hip_runs/prior", auto_resume=False)
    assert opts2.resume_run_dir == "C:/hip_runs/prior"


def test_cli_exposes_resume_flags():
    cli_path = Path(__file__).resolve().parents[1] / "hip_id_agent" / "cli.py"
    source = cli_path.read_text(encoding="utf-8")
    assert "--resume-run" in source
    assert "--auto-resume" in source
    assert "resume_run_dir=resume_run" in source
    assert "--resume-run and --auto-resume cannot be combined" in source


def test_flow_wires_mission_controller():
    import inspect

    from hip_id_agent import dummy_fill_e2e

    source = inspect.getsource(dummy_fill_e2e.FullDummyFillE2EFlow.run)
    assert "MissionController(" in source
    assert "adopt_completed_phases" in source
    assert "mark_phase_complete" in source
    assert "write_final_verdict" in source
    assert "_mission_prior_entities" in source


def test_autonomous_mission_one_switch_profile_is_completion_first_and_bounded():
    cli_path = Path(__file__).resolve().parents[1] / "hip_id_agent" / "cli.py"
    source = cli_path.read_text(encoding="utf-8")
    assert '"--autonomous-mission"' in source
    block = source[source.index("if autonomous_mission:"):source.index("if all_phases_until_complete:")]
    assert "all_phases_until_complete = False" in block
    assert "runtime_self_heal_until_complete = True" in block
    assert "runtime_self_heal = True" in block
    assert "forensic_evidence = True" in block
    assert "require_api_capture = True" not in block
    assert "if not resume_run:\n            auto_resume = True" not in source
    assert "cannot be combined with --rules-only or --phases" in source
