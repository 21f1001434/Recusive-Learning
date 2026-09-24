from __future__ import annotations

"""Final mission consolidation gate for the V233 HIP autonomous agent.

The existing phase runtime already proves each form independently.  This module
adds one final, fail-closed mission gate that verifies the *current run* has a
complete proof chain across every selected phase before the run may be reported
as application-complete.

The consolidator is deliberately browser-independent.  It consumes the evidence
already produced by the live browser runtime and therefore cannot accidentally
replay a form or a mutation while deciding whether the mission is complete.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .safe_io import safe_write_json
from .security import mask_sensitive_data

FINAL_MISSION_CONSOLIDATION_JSON = "final_mission_consolidation.json"
FINAL_MISSION_CONSOLIDATION_MD = "final_mission_consolidation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _verification_map(rows: Any) -> Dict[str, Mapping[str, Any]]:
    out: Dict[str, Mapping[str, Any]] = {}
    if isinstance(rows, Mapping):
        for key, value in rows.items():
            if isinstance(value, Mapping):
                out[str(key)] = value
        return out
    if isinstance(rows, list):
        for value in rows:
            if not isinstance(value, Mapping):
                continue
            phase = str(value.get("phase") or value.get("phase_name") or "")
            if phase:
                out[phase] = value
    return out


class FinalMissionConsolidator:
    """Fail-closed cross-phase proof gate.

    A mission passes only when:
    * every selected phase is complete in the mission ledger;
    * every phase has a current-run exact-state + judge + verification proof;
    * every supplied runtime verification is pass/complete;
    * the mission transition terminal gate passes and no handoff is pending;
    * witness safety passes when witness mode was enabled;
    * assured mission mode has a passing phase-assurance artifact for each phase.

    No selectors, coordinates or customer values are persisted here.
    """

    SCHEMA = "hip.final-mission-consolidation.v1"

    def __init__(self, root_dir: str | Path, *, run_id: str, phases: Sequence[str], mode: str = "") -> None:
        self.root_dir = Path(root_dir)
        self.run_id = str(run_id)
        self.phases = [str(p) for p in phases]
        self.mode = str(mode or "")

    def evaluate(
        self,
        *,
        mission: Any,
        terminal_gate: Mapping[str, Any],
        phase_verifications: Any = None,
        witness_report: Optional[Mapping[str, Any]] = None,
        transition_state: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        supplied = _verification_map(phase_verifications)
        phase_rows = []
        all_phases_pass = True
        assurance_required = self.mode.startswith("autonomous_assured")

        for phase in self.phases:
            status = str(mission.phase_status(phase))
            proof = mission.current_run_completion_proof(phase)
            phase_dir = self.root_dir / phase
            exact_lock = _read_json(phase_dir / "phase_exact_state_lock.json")
            judge = _read_json(phase_dir / "section_judge_gate.json")
            persisted_verification = _read_json(phase_dir / "phase_verification.json")
            assurance = _read_json(phase_dir / "phase_mission_assurance.json")
            runtime_verification = supplied.get(phase)

            checkpoint = (exact_lock or {}).get("exact_completion_checkpoint") if isinstance(exact_lock, Mapping) else None
            exact_pass = bool(isinstance(checkpoint, Mapping) and checkpoint.get("pass") is True)
            judge_pass = bool(isinstance(judge, Mapping) and judge.get("pass") is True)
            persisted_ok = bool(isinstance(persisted_verification, Mapping) and persisted_verification)
            runtime_ok = True
            runtime_status = "not_supplied"
            if isinstance(runtime_verification, Mapping):
                runtime_status = str(runtime_verification.get("status") or "").lower()
                runtime_ok = runtime_status in {"pass", "passed", "complete", "completed", "resumed"}
                if "pass" in runtime_verification:
                    runtime_ok = runtime_ok and bool(runtime_verification.get("pass"))
            assurance_pass = bool(isinstance(assurance, Mapping) and assurance.get("pass") is True)
            if not assurance_required:
                assurance_pass = True

            passed = bool(
                status == "complete"
                and proof.get("adoptable") is True
                and exact_pass
                and judge_pass
                and persisted_ok
                and runtime_ok
                and assurance_pass
            )
            all_phases_pass = all_phases_pass and passed
            phase_rows.append({
                "phase": phase,
                "pass": passed,
                "mission_status": status,
                "current_run_proof_pass": bool(proof.get("adoptable")),
                "exact_state_pass": exact_pass,
                "judge_pass": judge_pass,
                "persisted_verification_present": persisted_ok,
                "runtime_verification_status": runtime_status,
                "runtime_verification_pass": runtime_ok,
                "assurance_required": assurance_required,
                "assurance_pass": assurance_pass,
                "resumed_adoption": bool(proof.get("resumed_adoption")),
            })

        terminal_pass = bool(terminal_gate.get("pass"))
        pending_transition = None
        if isinstance(transition_state, Mapping):
            pending_transition = transition_state.get("pending")
        no_pending_transition = not isinstance(pending_transition, Mapping)

        witness_required = bool(isinstance(witness_report, Mapping) and witness_report.get("status") not in {None, "", "disabled"})
        witness_pass = True
        if witness_required:
            witness_pass = bool(witness_report.get("pass") and witness_report.get("safety_pass", True))

        passed = bool(all_phases_pass and terminal_pass and no_pending_transition and witness_pass)
        result = {
            "schema_version": self.SCHEMA,
            "run_id": self.run_id,
            "mode": self.mode,
            "pass": passed,
            "code": "HIP_FINAL_MISSION_CONSOLIDATION_OK" if passed else "HIP_FINAL_MISSION_CONSOLIDATION_BLOCKED",
            "application_complete": passed,
            "phase_sequence": list(self.phases),
            "all_phase_proofs_pass": bool(all_phases_pass),
            "terminal_gate_pass": terminal_pass,
            "no_pending_transition": no_pending_transition,
            "witness_required": witness_required,
            "witness_pass": witness_pass,
            "phases": phase_rows,
            "policy": {
                "current_run_exact_proof_required": True,
                "independent_section_judge_required": True,
                "persisted_phase_verification_required": True,
                "assurance_required_for_assured_mode": True,
                "terminal_handoff_gate_required": True,
                "unacknowledged_transition_blocks_completion": True,
                "memory_never_overrides_live_proof": True,
            },
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
            "generated_at": _utc_now(),
        }
        result = mask_sensitive_data(result)
        self.write(result)
        return result

    def write(self, result: Mapping[str, Any]) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        safe_write_json(self.root_dir / FINAL_MISSION_CONSOLIDATION_JSON, dict(result), mask=False)
        lines = [
            "# Final HIP Mission Consolidation",
            "",
            f"- Run: `{self.run_id}`",
            f"- Pass: **{'YES' if result.get('pass') else 'NO'}**",
            f"- Terminal gate: **{'PASS' if result.get('terminal_gate_pass') else 'BLOCK'}**",
            f"- Pending transition: **{'NO' if result.get('no_pending_transition') else 'YES'}**",
            "",
            "| Phase | Proof | Exact | Judge | Runtime | Assurance | Final |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in result.get("phases") or []:
            lines.append(
                f"| {row.get('phase')} | {'PASS' if row.get('current_run_proof_pass') else 'FAIL'} "
                f"| {'PASS' if row.get('exact_state_pass') else 'FAIL'} "
                f"| {'PASS' if row.get('judge_pass') else 'FAIL'} "
                f"| {'PASS' if row.get('runtime_verification_pass') else 'FAIL'} "
                f"| {'PASS' if row.get('assurance_pass') else 'FAIL'} "
                f"| {'PASS' if row.get('pass') else 'FAIL'} |"
            )
        (self.root_dir / FINAL_MISSION_CONSOLIDATION_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


__all__ = [
    "FinalMissionConsolidator",
    "FINAL_MISSION_CONSOLIDATION_JSON",
    "FINAL_MISSION_CONSOLIDATION_MD",
]
