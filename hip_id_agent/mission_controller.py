"""Autonomous mission controller for the all-phase HIP web agent.

The seven-phase until-complete run already self-heals inside each phase.  This
module adds the missing mission-level autonomy so the application is completed
as one continuous autonomous objective:

1. A persistent, crash-safe mission ledger (``mission_state.json``) records the
   authoritative status of every phase (pending / in_progress / complete /
   blocked) with attempt counts and judge outcomes.
2. Interrupted runs become resumable.  A new run can adopt a prior run's
   judge-approved completed phases fail-closed: a phase is adopted only when the
   prior run holds BOTH the deterministic exact-completion proof and a passing
   independent section-judge gate, plus the persisted verification payload.
   Anything less re-executes the phase live.
3. A per-run, value-scoped entity registry keeps the dummy display names used by
   completed phases so later phases and the final report reference one
   consistent set of entities.  The registry lives inside the run directory
   only; it is never promoted into the long-term portal brain, preserving the
   value-free memory policy.
4. A single final mission verdict (``mission_completion_report.json`` and
   ``mission_completion_report.md``) declares the application complete only
   when every selected phase passed deterministic verification and the
   independent judges.

No function in this module touches the browser; everything is deterministic
filesystem state so it stays fully unit-testable offline.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .safe_io import safe_write_json
from .security import mask_sensitive_string

MISSION_STATE_FILENAME = "mission_state.json"
MISSION_REPORT_JSON = "mission_completion_report.json"
MISSION_REPORT_MD = "mission_completion_report.md"
ENTITY_REGISTRY_FILENAME = "mission_entity_registry.json"
PHASE_VERIFICATION_FILENAME = "phase_verification.json"
PHASE_JUDGE_RESULT_FILENAME = "phase_judge_result.json"
RESUME_ADOPTION_FILENAME = "resumed_from_prior_run.json"

DEFAULT_PHASE_DEPENDENCIES: Dict[str, List[str]] = {
    "data_map": [],
    "source_document_type": [],
    "target_document_type": [],
    "rule": ["data_map", "source_document_type"],
    "source_transport_profile": ["source_document_type"],
    "target_transport_profile": ["target_document_type"],
    "biz_flow": [
        "source_document_type",
        "target_document_type",
        "rule",
        "source_transport_profile",
        "target_transport_profile",
    ],
}

_STATUS_ORDER = ("pending", "in_progress", "complete", "blocked")

# Input keys that carry the human-visible entity name for each phase family.
_ENTITY_NAME_KEYS = (
    "name",
    "data_map_name",
    "datamap_name",
    "document_type_name",
    "doctype_name",
    "rule_name",
    "transport_profile_name",
    "profile_name",
    "biz_flow_name",
    "bizflow_name",
    "flow_name",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return None


class MissionController:
    """Crash-safe mission ledger plus resume and final-verdict logic."""

    SCHEMA = "hip.autonomous-mission-state.v1"

    def __init__(self, root_dir: str | Path, *, run_id: str, phases: Sequence[str], mode: str = "all_phases_until_complete") -> None:
        self.root_dir = Path(root_dir)
        self.run_id = str(run_id)
        self.phases = [str(p) for p in phases]
        self.mode = str(mode)
        # Optional MissionTraceLedger attached by the live E2E flow. The mission
        # controller remains filesystem-deterministic when no trace is attached.
        self.trace = None
        self.telemetry = None
        self.state_path = self.root_dir / MISSION_STATE_FILENAME
        self.registry_path = self.root_dir / ENTITY_REGISTRY_FILENAME
        self.state: Dict[str, Any] = {
            "schema_version": self.SCHEMA,
            "run_id": self.run_id,
            "mode": self.mode,
            "mission_status": "in_progress",
            "started_at": _utc_now(),
            "updated_at": _utc_now(),
            "phase_sequence": list(self.phases),
            "phase_dependencies": {
                phase: [p for p in DEFAULT_PHASE_DEPENDENCIES.get(phase, []) if p in self.phases]
                for phase in self.phases
            },
            "phases": {
                p: {
                    "status": "pending",
                    "attempts": 0,
                    "judge_pass": None,
                    "resumed_from": "",
                    "started_at": "",
                    "completed_at": "",
                }
                for p in self.phases
            },
            "resume": {"resumed": False, "source_run_dir": "", "adopted_phases": []},
        }
        self.entity_registry: Dict[str, Any] = {
            "schema_version": "hip.mission-entity-registry.v1",
            "run_id": self.run_id,
            "value_scope": "current_run_only",
            "promoted_to_portal_brain": False,
            "entities": {},
        }
        self._persist()

    # ------------------------------------------------------------------ ledger

    def _persist(self) -> None:
        self.state["updated_at"] = _utc_now()
        safe_write_json(self.state_path, self.state, mask=False)

    def _phase_row(self, phase: str) -> Dict[str, Any]:
        return self.state["phases"].setdefault(
            str(phase),
            {"status": "pending", "attempts": 0, "judge_pass": None, "resumed_from": "", "started_at": "", "completed_at": ""},
        )

    def mark_phase_started(self, phase: str, *, attempt: int) -> None:
        row = self._phase_row(phase)
        if row["status"] not in {"complete"}:
            row["status"] = "in_progress"
        row["attempts"] = max(int(row.get("attempts") or 0), int(attempt))
        if not row.get("started_at"):
            row["started_at"] = _utc_now()
        self._persist()
        trace = getattr(self, "trace", None)
        if trace is not None:
            try:
                trace.mark_phase(phase, status="running", attempt=attempt, activity="Executing verified HIP form step")
            except Exception:
                pass
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            try:
                telemetry.phase_started(phase, attempt=attempt)
            except Exception:
                pass

    def mark_phase_complete(self, phase: str, *, attempt: int, judge_pass: bool) -> None:
        row = self._phase_row(phase)
        row.update({
            "status": "complete",
            "attempts": max(int(row.get("attempts") or 0), int(attempt)),
            "judge_pass": bool(judge_pass),
            "completed_at": _utc_now(),
        })
        self._persist()
        trace = getattr(self, "trace", None)
        if trace is not None:
            try:
                trace.mark_phase(phase, status="completed", attempt=attempt, activity="Exact state and independent verification passed")
            except Exception:
                pass
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            try:
                verification = _read_json(self.root_dir / str(phase) / PHASE_VERIFICATION_FILENAME)
                judge = _read_json(self.root_dir / str(phase) / PHASE_JUDGE_RESULT_FILENAME)
                exact_lock = _read_json(self.root_dir / str(phase) / "phase_exact_state_lock.json")
                telemetry_payload = dict(verification) if isinstance(verification, dict) else {}
                if isinstance(exact_lock, dict):
                    telemetry_payload["exact_state_lock"] = exact_lock
                telemetry.phase_completed(phase, attempt=attempt, judge_pass=judge_pass, verification=telemetry_payload)
                if isinstance(judge, dict):
                    telemetry.log_judge(phase, judge, attempt=attempt)
            except Exception:
                pass

    def mark_phase_blocked(self, phase: str, *, attempt: int, reason: str) -> None:
        row = self._phase_row(phase)
        row.update({
            "status": "blocked",
            "attempts": max(int(row.get("attempts") or 0), int(attempt)),
            "judge_pass": False,
            "blocked_reason": mask_sensitive_string(str(reason))[:2000],
            "blocked_at": _utc_now(),
        })
        self.state["mission_status"] = "blocked"
        self._persist()
        trace = getattr(self, "trace", None)
        if trace is not None:
            try:
                trace.mark_phase(phase, status="blocked", attempt=attempt, activity="Blocked", blocker=reason)
            except Exception:
                pass
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            try:
                telemetry.phase_blocked(phase, attempt=attempt, reason=reason)
            except Exception:
                pass

    def phase_status(self, phase: str) -> str:
        return str(self._phase_row(phase).get("status") or "pending")

    def phase_readiness(self, phase: str) -> Dict[str, Any]:
        parents = list((self.state.get("phase_dependencies") or {}).get(str(phase), []) or [])
        incomplete = [parent for parent in parents if self.phase_status(parent) != "complete"]
        return {
            "phase": str(phase),
            "parent_phases": parents,
            "completed_parent_phases": [p for p in parents if p not in incomplete],
            "incomplete_parent_phases": incomplete,
            "ready": not incomplete,
            "policy": "phase starts only after every required upstream entity/form phase is judge-approved",
        }

    def incomplete_phases(self) -> List[str]:
        return [p for p in self.phases if self.phase_status(p) != "complete"]

    # ------------------------------------------------------------------ resume

    @staticmethod
    def phase_completion_proof(phase_dir: Path) -> Dict[str, Any]:
        """Fail-closed proof that a prior run completed and judged this phase.

        All three artifacts are required:
        - ``phase_exact_state_lock.json`` with a passing exact-completion checkpoint,
        - ``section_judge_gate.json`` with ``pass: true``,
        - the persisted ``phase_verification.json`` payload (needed so a resumed
          run can rebuild its aggregate report without replaying the browser).
        """
        phase_dir = Path(phase_dir)
        lock = _read_json(phase_dir / "phase_exact_state_lock.json")
        judge = _read_json(phase_dir / "section_judge_gate.json")
        verification = _read_json(phase_dir / PHASE_VERIFICATION_FILENAME)
        checkpoint = (lock or {}).get("exact_completion_checkpoint") if isinstance(lock, dict) else None
        lock_pass = bool(isinstance(checkpoint, dict) and checkpoint.get("pass") is True)
        judge_pass = bool(isinstance(judge, dict) and judge.get("pass") is True)
        verification_ok = bool(isinstance(verification, dict) and verification)
        blocked = (phase_dir / "section_judge_block_diagnosis.json").is_file()
        parent_state = _read_json(phase_dir.parent / MISSION_STATE_FILENAME)
        assurance_required = bool(
            isinstance(parent_state, dict)
            and str(parent_state.get("mode") or "").startswith("autonomous_assured")
        )
        assurance = _read_json(phase_dir / "phase_mission_assurance.json")
        assurance_pass = bool(isinstance(assurance, dict) and assurance.get("pass") is True)
        adoptable = lock_pass and judge_pass and verification_ok and not blocked and (assurance_pass or not assurance_required)
        return {
            "phase_dir": str(phase_dir),
            "exact_state_lock_pass": lock_pass,
            "section_judge_pass": judge_pass,
            "verification_payload_present": verification_ok,
            "unresolved_block_diagnosis": blocked,
            "mission_assurance_required": assurance_required,
            "mission_assurance_pass": assurance_pass,
            "adoptable": adoptable,
            "verification": verification if adoptable else None,
            "judge_result": judge if adoptable else None,
        }

    @classmethod
    def find_resumable_run(cls, runs_dir: str | Path, *, exclude_run: str | Path | None = None) -> Optional[Path]:
        """Return the newest prior run directory whose mission is incomplete.

        A run is resumable when it has a mission ledger, its mission is not
        complete, and at least one phase carries a full adoption proof.
        """
        runs_root = Path(runs_dir)
        if not runs_root.is_dir():
            return None
        exclude = Path(exclude_run).resolve() if exclude_run else None
        candidates: List[Path] = []
        for child in runs_root.iterdir():
            if not child.is_dir():
                continue
            if exclude is not None and child.resolve() == exclude:
                continue
            if (child / MISSION_STATE_FILENAME).is_file():
                candidates.append(child)
        for run_dir in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
            state = _read_json(run_dir / MISSION_STATE_FILENAME)
            if not isinstance(state, dict):
                continue
            if str(state.get("mission_status") or "") == "complete":
                continue
            phases = state.get("phase_sequence") or []
            for phase in phases:
                if cls.phase_completion_proof(run_dir / str(phase)).get("adoptable"):
                    return run_dir
        return None

    def adopt_completed_phases(self, source_run_dir: str | Path) -> Dict[str, Any]:
        """Adopt fail-closed-proven completed phases from a prior run.

        For each adoptable phase the proof artifacts are copied into this run's
        phase directory under ``resumed_from_prior_run.json`` together with the
        prior verification and judge payloads, and the mission ledger marks the
        phase complete.  Non-adoptable phases stay pending and will re-execute
        live.
        """
        source = Path(source_run_dir)
        adopted: List[str] = []
        skipped: List[Dict[str, Any]] = []
        adoption_payloads: Dict[str, Dict[str, Any]] = {}
        for phase in self.phases:
            proof = self.phase_completion_proof(source / phase)
            current_requires_assurance = str(self.mode).startswith("autonomous_assured")
            if not proof.get("adoptable") or (current_requires_assurance and not proof.get("mission_assurance_pass")):
                skipped.append({
                    "phase": phase,
                    "reason": (
                        "current mission requires assured source evidence"
                        if current_requires_assurance and not proof.get("mission_assurance_pass")
                        else "missing exact-state lock, judge pass, or verification payload"
                    ),
                    "proof": {k: v for k, v in proof.items() if k not in {"verification", "judge_result"}},
                })
                continue
            phase_dir = self.root_dir / phase
            phase_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "schema_version": "hip.resumed-phase-adoption.v1",
                "phase": phase,
                "source_run_dir": str(source),
                "source_phase_dir": proof["phase_dir"],
                "adopted_at": _utc_now(),
                "adoption_policy": "fail_closed_exact_lock_plus_judge_plus_verification",
                "browser_replay_performed": False,
                "verification": proof.get("verification"),
                "judge_result": proof.get("judge_result"),
            }
            # Copy the authoritative completion proof into the new run.  The old
            # implementation copied only verification/judge summaries and left
            # the exact-state lock in the prior run, so deleting/moving that run
            # could make a resumed mission impossible to certify locally.
            source_phase_dir = source / phase
            exact_lock = _read_json(source_phase_dir / "phase_exact_state_lock.json") or {}
            section_judge = _read_json(source_phase_dir / "section_judge_gate.json") or proof.get("judge_result") or {}
            assurance = _read_json(source_phase_dir / "phase_mission_assurance.json") or {}
            record.update({
                "proof_snapshot_copied": True,
                "source_assurance_required": bool(proof.get("mission_assurance_required")),
                "source_assurance_pass": bool(proof.get("mission_assurance_pass")),
                "current_mission_assurance_required": bool(current_requires_assurance),
            })
            safe_write_json(phase_dir / RESUME_ADOPTION_FILENAME, record, mask=False)
            safe_write_json(phase_dir / PHASE_VERIFICATION_FILENAME, proof.get("verification") or {}, mask=False)
            safe_write_json(phase_dir / PHASE_JUDGE_RESULT_FILENAME, proof.get("judge_result") or {}, mask=False)
            safe_write_json(phase_dir / "phase_exact_state_lock.json", exact_lock, mask=False)
            safe_write_json(phase_dir / "section_judge_gate.json", section_judge, mask=False)
            if assurance:
                safe_write_json(phase_dir / "phase_mission_assurance.json", assurance, mask=False)
            row = self._phase_row(phase)
            row.update({
                "status": "complete",
                "judge_pass": True,
                "resumed_from": str(source),
                "completed_at": _utc_now(),
            })
            trace = getattr(self, "trace", None)
            if trace is not None:
                try:
                    trace.mark_phase(phase, status="resumed", attempt=int(row.get("attempts") or 0), activity="Adopted from prior exact-state + judge-approved run")
                except Exception:
                    pass
            adopted.append(phase)
            adoption_payloads[phase] = record
            # Carry entity names forward so later live phases stay consistent.
            source_registry = _read_json(source / ENTITY_REGISTRY_FILENAME)
            if isinstance(source_registry, dict):
                entity = (source_registry.get("entities") or {}).get(phase)
                if isinstance(entity, dict):
                    self.entity_registry["entities"][phase] = dict(entity, adopted_from=str(source))
        self.state["resume"] = {
            "resumed": bool(adopted),
            "source_run_dir": str(source),
            "adopted_phases": list(adopted),
            "skipped": skipped,
        }
        self._persist()
        if self.entity_registry["entities"]:
            safe_write_json(self.registry_path, self.entity_registry, mask=False)
        return {
            "resumed": bool(adopted),
            "source_run_dir": str(source),
            "adopted_phases": adopted,
            "skipped": skipped,
            "adoption_records": adoption_payloads,
        }


    def current_run_completion_proof(self, phase: str) -> Dict[str, Any]:
        """Verify completion using evidence materialized inside this run only.

        Resumed phases are accepted only when their exact-state/judge proof was
        snapshotted into the current run.  This prevents final certification from
        depending on a prior run directory that may later be moved or deleted.
        """
        phase_dir = self.root_dir / str(phase)
        proof = self.phase_completion_proof(phase_dir)
        resumed = _read_json(phase_dir / RESUME_ADOPTION_FILENAME)
        proof["resumed_adoption"] = bool(isinstance(resumed, dict))
        proof["proof_snapshot_copied"] = bool(
            isinstance(resumed, dict) and resumed.get("proof_snapshot_copied") is True
        ) if proof["resumed_adoption"] else True
        if proof["resumed_adoption"] and not proof["proof_snapshot_copied"]:
            proof["adoptable"] = False
        # A strict assured current run may not inherit a non-assured phase even if
        # the source run itself did not require assurance.
        if str(self.mode).startswith("autonomous_assured") and not proof.get("mission_assurance_pass"):
            proof["adoptable"] = False
            proof["current_mission_assurance_required"] = True
        return proof

    # ---------------------------------------------------------- entity registry

    @staticmethod
    def _extract_entity_names(payload: Dict[str, Any]) -> List[str]:
        normalized_targets = {re.sub(r"[^a-z0-9]", "", k) for k in _ENTITY_NAME_KEYS}
        found: List[str] = []

        def walk(node: Any, depth: int = 0) -> None:
            if depth > 6 or len(found) >= 8:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    key_norm = re.sub(r"[^a-z0-9]", "", str(key).lower())
                    if key_norm in normalized_targets and isinstance(value, str) and value.strip():
                        if value.strip() not in found:
                            found.append(value.strip())
                    else:
                        walk(value, depth + 1)
            elif isinstance(node, list):
                for item in node[:20]:
                    walk(item, depth + 1)

        walk(payload if isinstance(payload, dict) else {})
        return found

    def record_phase_entities(self, phase: str, *, phase_input: Dict[str, Any], summary: Dict[str, Any] | None = None) -> Dict[str, Any]:
        names = self._extract_entity_names(phase_input if isinstance(phase_input, dict) else {})
        entry = {
            "phase": phase,
            "display_names": names,
            "recorded_at": _utc_now(),
            "source": "phase_input_after_judged_completion",
        }
        if isinstance(summary, dict) and summary.get("form_url"):
            entry["form_url"] = str(summary.get("form_url"))
        self.entity_registry["entities"][str(phase)] = entry
        safe_write_json(self.registry_path, self.entity_registry, mask=False)
        return entry

    def prior_entities_for(self, phase: str) -> Dict[str, Any]:
        """Return entities from phases earlier in the sequence than ``phase``."""
        try:
            idx = self.phases.index(str(phase))
        except ValueError:
            idx = len(self.phases)
        earlier = self.phases[:idx]
        return {
            p: self.entity_registry["entities"][p]
            for p in earlier
            if p in self.entity_registry["entities"]
        }

    # ------------------------------------------------------------ final verdict

    def write_final_verdict(
        self,
        *,
        overall_status: str,
        blocked_phase: str = "",
        terminal_gate_pass: Optional[bool] = None,
    ) -> Dict[str, Any]:
        status_complete = all(self.phase_status(p) == "complete" for p in self.phases)
        complete = bool(status_complete and terminal_gate_pass is not False)
        mission_status = "complete" if complete else ("blocked" if blocked_phase or self.state.get("mission_status") == "blocked" or terminal_gate_pass is False else "incomplete")
        self.state["mission_status"] = mission_status
        self.state["finished_at"] = _utc_now()
        self._persist()
        assurance_by_phase: Dict[str, Any] = {}
        for phase in self.phases:
            assurance = _read_json(self.root_dir / phase / "phase_mission_assurance.json")
            if isinstance(assurance, dict):
                assurance_by_phase[phase] = {
                    "pass": bool(assurance.get("pass")),
                    "missing_assurance": assurance.get("missing_assurance") or [],
                }
        report = {
            "schema_version": "hip.mission-completion-report.v2",
            "run_id": self.run_id,
            "mode": self.mode,
            "application_complete": bool(complete),
            "mission_status": mission_status,
            "run_overall_status": str(overall_status),
            "blocked_phase": str(blocked_phase or ""),
            "phase_status_complete": bool(status_complete),
            "terminal_gate_pass": terminal_gate_pass,
            "phase_sequence": list(self.phases),
            "phases": {p: dict(self._phase_row(p)) for p in self.phases},
            "resume": dict(self.state.get("resume") or {}),
            "entity_registry": str(self.registry_path) if self.registry_path.is_file() else "",
            "phase_assurance": assurance_by_phase,
            "all_recorded_phase_assurance_pass": bool(assurance_by_phase) and all(bool(row.get("pass")) for row in assurance_by_phase.values()),
            "completion_definition": (
                "The application is complete only when every selected phase holds a "
                "deterministic exact-completion proof, a passing independent "
                "text+vision section judge, and (for assured autonomous runs) a "
                "fresh MCP/UI/API/replay mission-assurance gate, in this run or "
                "adopted fail-closed from a compatible resumed prior run."
            ),
            "generated_at": _utc_now(),
        }
        safe_write_json(self.root_dir / MISSION_REPORT_JSON, report, mask=False)
        lines = [
            "# Autonomous Mission Completion Report",
            "",
            f"- Run: `{self.run_id}`",
            f"- Mode: `{self.mode}`",
            f"- Application complete: **{'yes' if complete else 'no'}**",
            f"- Mission status: `{mission_status}`",
        ]
        if blocked_phase:
            lines.append(f"- Blocked phase: `{blocked_phase}`")
        resume = self.state.get("resume") or {}
        if resume.get("resumed"):
            lines.append(f"- Resumed from: `{resume.get('source_run_dir')}` (adopted: {', '.join(resume.get('adopted_phases') or [])})")
        lines.extend(["", "| Phase | Status | Attempts | Judge |", "|---|---|---|---|"])
        for p in self.phases:
            row = self._phase_row(p)
            judge = row.get("judge_pass")
            judge_txt = "pass" if judge is True else ("fail" if judge is False else "-")
            lines.append(f"| {p} | {row.get('status')} | {row.get('attempts')} | {judge_txt} |")
        (self.root_dir / MISSION_REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report


__all__ = [
    "MissionController",
    "MISSION_STATE_FILENAME",
    "MISSION_REPORT_JSON",
    "MISSION_REPORT_MD",
    "ENTITY_REGISTRY_FILENAME",
    "PHASE_VERIFICATION_FILENAME",
    "PHASE_JUDGE_RESULT_FILENAME",
    "RESUME_ADOPTION_FILENAME",
]
