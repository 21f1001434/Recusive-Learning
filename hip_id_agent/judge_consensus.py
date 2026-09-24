from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Sequence

from .model_portfolio import model_portfolio_from_config
from .security import mask_sensitive_data

SYSTEM = """
You are one independent Dell HIP phase-verification judge in a champion/challenger panel.
Return ONLY JSON. You never click, fill, save, deploy or authorize mutation.
Exact field-bound browser evidence is authoritative for committed values. Visual/model claims may identify a
concrete mismatch only when they name an expected field/row and are supported by the supplied evidence.
If deterministic/exact browser evidence is missing or explicitly failed, do not mark pass.
Return exactly:
{
  "pass": true|false,
  "confidence": 0.0,
  "needs_human": true|false,
  "evidence_conflict": true|false,
  "reason_codes": ["..."],
  "summary": "..."
}
"""


def _vote_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "pass", "passed", "yes"}:
        return True
    if text in {"false", "fail", "failed", "blocked", "no"}:
        return False
    return None


class MultiModelJudgeConsensus:
    """Resolve model-only judge disagreement without weakening exact verification.

    The portfolio is consulted only when useful. A panel majority may reconcile a
    text/vision false negative *only* when deterministic or exact completion evidence
    already proves the phase. No model panel can turn failed exact evidence into PASS.
    """

    def __init__(self, app_config: Any) -> None:
        self.app_config = app_config
        self.router = model_portfolio_from_config(app_config)

    @staticmethod
    def _needs_panel(judge: Mapping[str, Any], exact_checkpoint: Mapping[str, Any]) -> bool:
        deterministic = judge.get("deterministic_judge") if isinstance(judge.get("deterministic_judge"), Mapping) else {}
        text = judge.get("text_model_judge") if isinstance(judge.get("text_model_judge"), Mapping) else {}
        vision = judge.get("vision_model_judge") if isinstance(judge.get("vision_model_judge"), Mapping) else {}
        exact_safe = bool(deterministic.get("pass") or exact_checkpoint.get("pass"))
        if not bool(judge.get("pass")) and exact_safe:
            return True
        if exact_safe and (text.get("pass") is False or vision.get("pass") is False):
            return True
        if exact_safe and str(text.get("status") or "") not in {"ok", "reconciled", "not_required"}:
            return True
        if exact_safe and str(vision.get("status") or "") not in {"ok", "reconciled", "not_required"}:
            return True
        return False

    def resolve(
        self,
        *,
        phase: str,
        automated_judge: Mapping[str, Any],
        verification: Mapping[str, Any],
        exact_checkpoint: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        checkpoint = dict(exact_checkpoint or {})
        deterministic = automated_judge.get("deterministic_judge") if isinstance(automated_judge.get("deterministic_judge"), Mapping) else {}
        exact_safe = bool(deterministic.get("pass") or checkpoint.get("pass"))
        if not force and not self._needs_panel(automated_judge, checkpoint):
            return {
                "used": False,
                "reason": "panel_not_required",
                "pass": bool(automated_judge.get("pass")),
                "exact_evidence_pass": exact_safe,
                "needs_human": False,
            }

        task_payload = mask_sensitive_data({
            "phase": phase,
            "automated_judge": {
                "pass": automated_judge.get("pass"),
                "status": automated_judge.get("status"),
                "deterministic_judge": deterministic,
                "text_model_judge": automated_judge.get("text_model_judge", {}),
                "vision_model_judge": automated_judge.get("vision_model_judge", {}),
            },
            "verification": {
                "status": verification.get("status"),
                "counts": verification.get("counts", {}),
                "failed_attempts": list(verification.get("failed_attempts") or [])[:20],
                "validation_gate": verification.get("validation_gate", {}),
            },
            "exact_completion_checkpoint": checkpoint,
        })
        trace = self.router.tournament_text(
            system=SYSTEM,
            task=json.dumps(task_payload, ensure_ascii=False, default=str)[:36000],
            role="judge",
            expected_json=True,
            require_keys=("pass", "confidence", "needs_human", "evidence_conflict", "reason_codes", "summary"),
            parallel_models=getattr(self.app_config.model_portfolio, "parallel_models", 3),
            exploration=True,
        )
        votes = []
        for row in trace.get("candidate_votes") or []:
            if not isinstance(row, Mapping) or not row.get("ok"):
                continue
            parsed = row.get("parsed") if isinstance(row.get("parsed"), Mapping) else {}
            verdict = _vote_bool(parsed.get("pass"))
            if verdict is None:
                continue
            votes.append({
                "model": str(row.get("model") or ""),
                "pass": verdict,
                "confidence": float(parsed.get("confidence") or 0.0),
                "needs_human": bool(parsed.get("needs_human")),
                "evidence_conflict": bool(parsed.get("evidence_conflict")),
                "reason_codes": list(parsed.get("reason_codes") or [])[:12],
                "summary": str(parsed.get("summary") or "")[:800],
            })
        pass_count = sum(1 for v in votes if v["pass"])
        fail_count = sum(1 for v in votes if not v["pass"])
        quorum = 2 if len(votes) >= 3 else 1 if votes else 99
        majority_pass = pass_count >= quorum and pass_count > fail_count
        majority_fail = fail_count >= quorum and fail_count >= pass_count

        automated_pass = bool(automated_judge.get("pass"))
        rescued = bool((not automated_pass) and exact_safe and majority_pass)
        # A panel can rescue model-only disagreement, never a deterministic failure.
        final_pass = bool(automated_pass or rescued)
        needs_human = bool(
            not votes
            or any(v.get("needs_human") for v in votes)
            or any(v.get("evidence_conflict") for v in votes)
            or (exact_safe and majority_fail)
            or (not exact_safe)
        )
        return mask_sensitive_data({
            "used": bool(trace.get("used")),
            "pass": final_pass,
            "rescued_model_only_block": rescued,
            "exact_evidence_pass": exact_safe,
            "automated_pass": automated_pass,
            "majority_pass": majority_pass,
            "majority_fail": majority_fail,
            "pass_votes": pass_count,
            "fail_votes": fail_count,
            "quorum": quorum,
            "vote_count": len(votes),
            "needs_human": needs_human,
            "votes": votes,
            "portfolio_trace": trace,
            "reason": "exact evidence plus multi-model majority reconciled a model-only false negative" if rescued else "panel advisory",
        })
