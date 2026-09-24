"""Mission-level transition coordinator for the seven HIP configuration phases.

Layer 8 closes the gap between a verified single handoff and the real mission:
completed/resumed phases may be interleaved with pending ones, so the controller
must route to the next *executable* phase rather than the next list entry.  It
also provides a crash-safe pending/acknowledged transition ledger and a terminal
completion gate that verifies every selected phase has current-run proof.

No customer field values are stored here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .safe_io import safe_write_json
from .security import mask_sensitive_data

TRANSITION_STATE_FILENAME = "mission_transition_state.json"
TERMINAL_GATE_FILENAME = "mission_terminal_completion_gate.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_executable_phase(
    phases: Sequence[str],
    current_phase: str,
    status_provider: Callable[[str], str],
) -> Dict[str, Any]:
    """Return the next non-complete selected phase and all completed phases skipped.

    A resumed/adopted phase is already authoritative and must not cause a browser
    navigation merely because it is adjacent in ``phase_sequence``.
    """
    ordered = [str(p) for p in phases]
    try:
        idx = ordered.index(str(current_phase))
    except ValueError:
        return {
            "current_phase": str(current_phase),
            "next_phase": "",
            "skipped_completed_phases": [],
            "terminal": True,
            "code": "HIP_PHASE_NOT_IN_SEQUENCE",
        }
    skipped: List[str] = []
    for candidate in ordered[idx + 1 :]:
        if str(status_provider(candidate)) == "complete":
            skipped.append(candidate)
            continue
        return {
            "current_phase": str(current_phase),
            "next_phase": candidate,
            "skipped_completed_phases": skipped,
            "terminal": False,
            "code": "HIP_NEXT_EXECUTABLE_PHASE_FOUND",
        }
    return {
        "current_phase": str(current_phase),
        "next_phase": "",
        "skipped_completed_phases": skipped,
        "terminal": True,
        "code": "HIP_NO_REMAINING_EXECUTABLE_PHASE",
    }


class MissionTransitionCoordinator:
    SCHEMA = "hip.mission-transition-state.v1"

    def __init__(self, root_dir: str | Path, *, run_id: str, phases: Sequence[str]) -> None:
        self.root_dir = Path(root_dir)
        self.path = self.root_dir / TRANSITION_STATE_FILENAME
        self.run_id = str(run_id)
        self.phases = [str(p) for p in phases]
        self.state: Dict[str, Any] = {
            "schema_version": self.SCHEMA,
            "run_id": self.run_id,
            "phase_sequence": list(self.phases),
            "pending": None,
            "history": [],
            "updated_at": _utc_now(),
        }
        self._persist()

    def _persist(self) -> None:
        self.state["updated_at"] = _utc_now()
        safe_write_json(self.path, self.state, mask=False)

    def plan_after(self, current_phase: str, status_provider: Callable[[str], str]) -> Dict[str, Any]:
        return next_executable_phase(self.phases, current_phase, status_provider)

    def arm(
        self,
        *,
        from_phase: str,
        to_phase: str,
        source_status: str,
        skipped_completed_phases: Sequence[str] = (),
        source_exact_verified: bool = False,
    ) -> Dict[str, Any]:
        if str(to_phase) not in self.phases:
            raise ValueError(f"transition target not selected: {to_phase}")
        row = {
            "from_phase": str(from_phase),
            "to_phase": str(to_phase),
            "source_status": str(source_status),
            "source_exact_verified": bool(source_exact_verified),
            "skipped_completed_phases": [str(p) for p in skipped_completed_phases],
            "status": "pending_destination_ack",
            "armed_at": _utc_now(),
            "acknowledged_at": "",
            "destination_attempt": 0,
            "destination_route_verified": False,
            "dual_mcp_verified": False,
            "values_stored": False,
        }
        self.state["pending"] = row
        self.state.setdefault("history", []).append(dict(row))
        self.state["history"] = self.state["history"][-40:]
        self._persist()
        return dict(row)

    def acknowledge(
        self,
        *,
        phase: str,
        attempt: int,
        destination_route_verified: bool,
        dual_mcp_verified: bool,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pending = self.state.get("pending")
        if not isinstance(pending, dict):
            return {
                "pass": True,
                "code": "HIP_PHASE_TRANSITION_NO_PENDING_HANDOFF",
                "phase": str(phase),
                "ack_required": False,
            }
        if str(pending.get("to_phase") or "") != str(phase):
            return {
                "pass": False,
                "code": "HIP_PHASE_TRANSITION_DESTINATION_MISMATCH",
                "phase": str(phase),
                "expected_phase": str(pending.get("to_phase") or ""),
            }
        passed = bool(destination_route_verified and dual_mcp_verified)
        pending.update({
            "status": "acknowledged" if passed else "destination_not_proven",
            "destination_attempt": int(attempt),
            "destination_route_verified": bool(destination_route_verified),
            "dual_mcp_verified": bool(dual_mcp_verified),
            "acknowledged_at": _utc_now() if passed else "",
            "details": mask_sensitive_data(dict(details or {})),
        })
        if passed:
            self.state.setdefault("history", []).append(dict(pending))
            self.state["history"] = self.state["history"][-40:]
            self.state["pending"] = None
        else:
            self.state["pending"] = pending
        self._persist()
        return {
            "pass": passed,
            "code": "HIP_PHASE_TRANSITION_ACK_OK" if passed else "HIP_PHASE_TRANSITION_ACK_FAILED",
            "phase": str(phase),
            "from_phase": str(pending.get("from_phase") or ""),
        }

    def terminal_gate(self, mission: Any) -> Dict[str, Any]:
        """Fail closed unless every selected phase is complete with current-run proof."""
        rows: List[Dict[str, Any]] = []
        all_pass = True
        for phase in self.phases:
            status = str(mission.phase_status(phase))
            proof = mission.current_run_completion_proof(phase)
            passed = bool(status == "complete" and proof.get("adoptable"))
            rows.append({
                "phase": phase,
                "status": status,
                "proof_pass": bool(proof.get("adoptable")),
                "resumed_adoption": bool(proof.get("resumed_adoption")),
                "pass": passed,
            })
            all_pass = all_pass and passed
        pending = self.state.get("pending")
        if isinstance(pending, dict):
            all_pass = False
        result = {
            "schema_version": "hip.mission-terminal-completion-gate.v1",
            "run_id": self.run_id,
            "pass": bool(all_pass),
            "code": "HIP_MISSION_TERMINAL_GATE_OK" if all_pass else "HIP_MISSION_TERMINAL_GATE_BLOCKED",
            "phases": rows,
            "unacknowledged_transition": mask_sensitive_data(pending) if isinstance(pending, dict) else None,
            "values_stored": False,
            "generated_at": _utc_now(),
        }
        safe_write_json(self.root_dir / TERMINAL_GATE_FILENAME, result, mask=False)
        return result


__all__ = [
    "MissionTransitionCoordinator",
    "next_executable_phase",
    "TRANSITION_STATE_FILENAME",
    "TERMINAL_GATE_FILENAME",
]
