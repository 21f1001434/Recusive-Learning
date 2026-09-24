from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.human-phase-review.v1"


def _stable(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class HumanPhaseReviewStore:
    """One supervised verdict checkpoint per newly learned phase/run.

    This is deliberately separate from field-mapping teaching. A human reviewer can
    confirm that a completed form *looks correct* or reject it with a note. A human
    approval can reconcile model-only disagreement only when deterministic/exact
    browser evidence already proves completion; it can never override missing exact
    values, failed required rows, or a blocking validation error.
    """

    def __init__(self, root: str | Path, config: Any = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.pending_dir = self.root / "pending"
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.root / "reviews.jsonl"
        self.config = config

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    def request_id(self, *, run_id: str, phase: str) -> str:
        # One request per run+phase, regardless of how many model retries occur.
        return _stable("phase-review", run_id, phase)

    def get(self, request_id: str) -> Dict[str, Any]:
        return _read_json(self.pending_dir / f"{request_id}.json")

    def get_for_phase(self, *, run_id: str, phase: str) -> Dict[str, Any]:
        return self.get(self.request_id(run_id=run_id, phase=phase))

    def create_or_update(
        self,
        *,
        run_id: str,
        phase: str,
        phase_display: str,
        attempt: int,
        automated_judge: Mapping[str, Any],
        verification: Mapping[str, Any],
        exact_checkpoint: Mapping[str, Any],
        model_consensus: Mapping[str, Any],
        screenshot_path: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        request_id = self.request_id(run_id=run_id, phase=phase)
        path = self.pending_dir / f"{request_id}.json"
        existing = _read_json(path)
        # Resolved requests are immutable teaching evidence. Do not ask again.
        if existing.get("status") == "resolved":
            return existing

        deterministic = automated_judge.get("deterministic_judge") if isinstance(automated_judge.get("deterministic_judge"), Mapping) else {}
        payload = {
            "schema_version": SCHEMA,
            "request_id": request_id,
            "run_id": str(run_id or ""),
            "phase": str(phase or ""),
            "phase_display": str(phase_display or phase),
            "created_at": existing.get("created_at") or utc_now(),
            "updated_at": utc_now(),
            "status": "needs_review",
            "attempt": int(attempt or 0),
            "automated_verdict": "pass" if bool(automated_judge.get("pass")) else "blocked",
            "automated_status": str(automated_judge.get("status") or ""),
            "deterministic_pass": bool(deterministic.get("pass")),
            "verification_status": str(verification.get("status") or ""),
            "exact_checkpoint_pass": bool(exact_checkpoint.get("pass")),
            "model_consensus": mask_sensitive_data(dict(model_consensus or {})),
            "judge_summary": mask_sensitive_data({
                "text_status": ((automated_judge.get("text_model_judge") or {}).get("status") if isinstance(automated_judge.get("text_model_judge"), Mapping) else ""),
                "text_pass": ((automated_judge.get("text_model_judge") or {}).get("pass") if isinstance(automated_judge.get("text_model_judge"), Mapping) else None),
                "vision_status": ((automated_judge.get("vision_model_judge") or {}).get("status") if isinstance(automated_judge.get("vision_model_judge"), Mapping) else ""),
                "vision_pass": ((automated_judge.get("vision_model_judge") or {}).get("pass") if isinstance(automated_judge.get("vision_model_judge"), Mapping) else None),
                "missing_fields": list(deterministic.get("missing_values") or [])[:12],
                "row_issues": list(deterministic.get("row_issues") or [])[:12],
                "blocking_validation": list(((deterministic.get("validation_gate") or {}).get("blocking") if isinstance(deterministic.get("validation_gate"), Mapping) else []) or [])[:12],
            }),
            "reason": mask_sensitive_string(str(reason or ""))[:1200],
            "screenshot_path": str(screenshot_path or ""),
            "instruction": "Review the filled phase once. Choose Looks correct if the form is correct, or Needs correction and add a note. Human approval cannot override failed exact browser evidence.",
            "review_once_per_phase": True,
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }
        safe_write_json(path, payload)
        return payload


    def recovery_request_id(self, *, run_id: str, phase: str, recovery_round: int) -> str:
        return _stable("phase-recovery", run_id, phase, int(recovery_round or 0))

    def create_recovery_request(
        self,
        *,
        run_id: str,
        phase: str,
        phase_display: str,
        recovery_round: int,
        reason: str,
        screenshot_path: str = "",
        exact_checkpoint: Optional[Mapping[str, Any]] = None,
        automated_judge: Optional[Mapping[str, Any]] = None,
        verification: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a resumable operator checkpoint for an incomplete phase.

        Unlike the once-per-learning review, recovery requests may recur after a
        later retry. Resolving either button means "resume and re-prove the live
        phase"; it never grants completion by itself. The browser remains open
        while the controller waits on this request.
        """
        request_id = self.recovery_request_id(run_id=run_id, phase=phase, recovery_round=recovery_round)
        path = self.pending_dir / f"{request_id}.json"
        existing = _read_json(path)
        if existing.get("status") == "resolved":
            return existing
        checkpoint = dict(exact_checkpoint or {})
        judge = dict(automated_judge or {})
        verify = dict(verification or {})
        payload = {
            "schema_version": SCHEMA,
            "request_type": "incomplete_phase_recovery",
            "request_id": request_id,
            "run_id": str(run_id or ""),
            "phase": str(phase or ""),
            "phase_display": str(phase_display or phase),
            "created_at": existing.get("created_at") or utc_now(),
            "updated_at": utc_now(),
            "status": "needs_review",
            "recovery_round": int(recovery_round or 0),
            "automated_verdict": "pass" if bool(judge.get("pass")) else "blocked",
            "automated_status": str(judge.get("status") or "incomplete_execution"),
            "deterministic_pass": bool(((judge.get("deterministic_judge") or {}).get("pass")) if isinstance(judge.get("deterministic_judge"), Mapping) else False),
            "verification_status": str(verify.get("status") or "incomplete"),
            "exact_checkpoint_pass": bool(checkpoint.get("pass")),
            "model_consensus": {},
            "judge_summary": {},
            "reason": mask_sensitive_string(str(reason or ""))[:2000],
            "screenshot_path": str(screenshot_path or ""),
            "instruction": (
                "The phase is incomplete and the browser is intentionally being kept open. "
                "Inspect or manually correct the live form if needed, then choose Looks correct "
                "or Needs correction to resume. The agent will re-prove every required value; "
                "this button never bypasses exact verification."
            ),
            "review_once_per_phase": False,
            "resume_requires_live_reproof": True,
            "browser_must_remain_open": True,
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }
        safe_write_json(path, payload)
        return payload

    def pending(self, *, run_id: str = "") -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path in sorted(self.pending_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            row = _read_json(path)
            if row.get("status") != "needs_review":
                continue
            if run_id and str(row.get("run_id") or "") != str(run_id):
                continue
            rows.append(row)
        return rows

    def resolve(self, *, request_id: str, verdict: str, note: str = "", reviewer: str = "human") -> Dict[str, Any]:
        path = self.pending_dir / f"{request_id}.json"
        row = _read_json(path)
        if not row:
            raise ValueError(f"Unknown human phase review request: {request_id}")
        verdict_norm = str(verdict or "").strip().lower()
        if verdict_norm in {"approve", "approved", "pass", "correct", "looks_correct"}:
            verdict_norm = "pass"
        elif verdict_norm in {"reject", "rejected", "fail", "failed", "needs_fix", "needs_correction", "incorrect"}:
            verdict_norm = "needs_correction"
        else:
            raise ValueError("verdict must be pass/looks_correct or needs_correction")

        exact_safe = bool(row.get("deterministic_pass") or row.get("exact_checkpoint_pass"))
        effective = verdict_norm
        if row.get("request_type") == "incomplete_phase_recovery":
            # Without exact proof a recovery click only means "resume and re-prove".
            # With exact proof (every input-owned value committed and verified) the
            # only remaining blocker is judge/evidence disagreement, which a human
            # "Looks correct" is allowed to reconcile. Treating that approval as a
            # recheck re-ran the same judge and re-asked forever.
            effective = "pass" if (verdict_norm == "pass" and row.get("exact_checkpoint_pass")) else "recheck_live_phase"
        elif verdict_norm == "pass" and not exact_safe:
            # R9: do not silently turn an explicit human "Looks correct" into a
            # rejection.  The controller must first perform a read-only live reproof
            # of the same phase.  Only that reproof may promote the phase.  This
            # prevents the UI from appearing to ignore the human while still
            # preserving fail-closed exact completion semantics.
            effective = "pass_pending_live_reproof"
            note = (str(note or "") + " Human approval recorded; controller must re-prove the current live phase before handoff.").strip()

        row["status"] = "resolved"
        row["resolved_at"] = utc_now()
        row["human_verdict"] = verdict_norm
        row["effective_human_verdict"] = effective
        row["reviewer"] = mask_sensitive_string(str(reviewer or "human"))[:200]
        row["note"] = mask_sensitive_string(str(note or ""))[:1200]
        row["human_can_reconcile_model_only_disagreement"] = exact_safe
        safe_write_json(path, row)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(mask_sensitive_data(row), ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return row

    def mark_model_feedback(self, *, request_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        path = self.pending_dir / f"{request_id}.json"
        row = _read_json(path)
        if not row:
            return {}
        if row.get("model_feedback_recorded_at"):
            return row
        row["model_feedback_recorded_at"] = utc_now()
        row["model_feedback"] = mask_sensitive_data(dict(payload or {}))
        safe_write_json(path, row)
        return row

    def manifest(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA,
            "enabled": bool(self._cfg("enabled", True)),
            "pending_count": len(self.pending()),
            "root": str(self.root),
            "review_once_per_phase": True,
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }


def human_phase_review_from_config(app_config: Any) -> HumanPhaseReviewStore:
    base = Path(app_config.reporting.memory_dir) / str(getattr(app_config.human_in_the_loop, "memory_subdir", "human_teaching") or "human_teaching")
    return HumanPhaseReviewStore(base / "phase_reviews", app_config.human_in_the_loop)
