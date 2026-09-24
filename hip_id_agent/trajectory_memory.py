from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .web_representation import cosine_similarity


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _safe_jsonl_append(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(mask_sensitive_data(payload), ensure_ascii=False, sort_keys=True, default=str)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


class TrajectoryMemory:
    """Value-free memory of successful and failed web action transitions."""

    def __init__(self, root: str | Path, *, max_records: int = 10000) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.records_path = self.root / "trajectories.jsonl"
        self.manifest_path = self.root / "manifest.json"
        self.preferences_path = self.root / "preference_pairs.jsonl"
        self.max_records = max(100, int(max_records))
        self._write_manifest()

    def _write_manifest(self) -> None:
        count = 0
        if self.records_path.exists():
            try:
                count = sum(1 for _ in self.records_path.open("r", encoding="utf-8"))
            except Exception:
                count = 0
        preference_count = 0
        if self.preferences_path.exists():
            try:
                preference_count = sum(1 for _ in self.preferences_path.open("r", encoding="utf-8"))
            except Exception:
                preference_count = 0
        safe_write_json(self.manifest_path, {
            "schema_version": "hip.trajectory-memory.v1",
            "record_count": count,
            "preference_pair_count": preference_count,
            "values_stored": False,
            "stores_success_and_failure": True,
            "updated_at": utc_now(),
        })

    def _read_records(self) -> List[Dict[str, Any]]:
        if not self.records_path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with self.records_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
        except Exception:
            return []
        return rows[-self.max_records :]

    def record(
        self,
        *,
        phase: str,
        family: str,
        node: Dict[str, Any],
        representation_before: Dict[str, Any],
        action_plan: Dict[str, Any],
        outcome: Dict[str, Any],
        representation_after: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        success = bool(outcome.get("success"))
        reason = str(outcome.get("reason") or "")
        state_changed = bool(
            representation_before.get("state_fingerprint")
            and (representation_after or {}).get("state_fingerprint")
            and representation_before.get("state_fingerprint") != (representation_after or {}).get("state_fingerprint")
        )
        reward = (1.2 if state_changed else 1.0) if success else -0.4
        if "NO_PROGRESS" in reason or "STUCK" in reason:
            reward = -0.85
        if "UNINTENDED_MUTATION" in reason:
            reward = -1.0
        elif "SURFACE_LOST" in reason or "WRONG_SURFACE" in reason:
            reward = -0.9
        elif "AMBIGUOUS" in reason:
            reward = -0.6
        elif "VALIDATION" in reason:
            reward = -0.7
        payload = {
            "schema_version": "hip.trajectory-memory-record.v1",
            "recorded_at": utc_now(),
            "phase": phase,
            "family": family,
            "node_signature": "|".join([
                _norm(node.get("section")),
                _norm(node.get("row_kind")),
                str(node.get("row_index") if node.get("row_index") is not None else ""),
                _norm(node.get("field_key")),
                _norm(node.get("action")),
            ]),
            "field_key": _norm(node.get("field_key")),
            "action": _norm(node.get("action")),
            "state_fingerprint_before": representation_before.get("state_fingerprint"),
            "structural_fingerprint_before": representation_before.get("structural_fingerprint"),
            "embedding_before": representation_before.get("embedding") or [],
            "state_fingerprint_after": (representation_after or {}).get("state_fingerprint"),
            "selected_control_identity": action_plan.get("selected_control_identity") or "",
            "selected_recovery": action_plan.get("selected_action") or "",
            "success": success,
            "reward": reward,
            "state_changed": state_changed,
            "reward_policy": "agentq_transition_reward_v2",
            "reason_code": _norm(reason)[:160],
            "binding_score": action_plan.get("binding_score"),
            "binding_margin": action_plan.get("binding_margin"),
            "values_stored": False,
        }
        _safe_jsonl_append(self.records_path, payload)
        candidates = [
            item for item in action_plan.get("candidate_actions", [])
            if isinstance(item, dict) and item.get("allowed", True) and item.get("action")
        ]
        selected = str(action_plan.get("selected_action") or "")
        alternatives = [item for item in candidates if str(item.get("action")) != selected]
        if selected and alternatives:
            alternatives.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
            if success:
                winner, loser = selected, str(alternatives[0].get("action"))
            else:
                winner, loser = str(alternatives[0].get("action")), selected
            _safe_jsonl_append(self.preferences_path, {
                "schema_version": "hip.action-preference-pair.v1",
                "recorded_at": utc_now(),
                "phase": phase,
                "family": family,
                "node_signature": payload["node_signature"],
                "state_fingerprint": payload["state_fingerprint_before"],
                "winning_action": winner,
                "losing_action": loser,
                "observed_selected_action_success": success,
                "values_stored": False,
                "training_status": "dataset_only_no_online_finetuning",
            })
        self._write_manifest()
        return mask_sensitive_data(payload)

    def retrieve(
        self,
        *,
        phase: str,
        family: str,
        node: Dict[str, Any],
        representation: Dict[str, Any],
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        signature = "|".join([
            _norm(node.get("section")),
            _norm(node.get("row_kind")),
            str(node.get("row_index") if node.get("row_index") is not None else ""),
            _norm(node.get("field_key")),
            _norm(node.get("action")),
        ])
        ranked: List[Dict[str, Any]] = []
        for row in self._read_records():
            if _norm(row.get("family")) != _norm(family):
                continue
            signature_match = row.get("node_signature") == signature
            field_match = _norm(row.get("field_key")) == _norm(node.get("field_key"))
            similarity = cosine_similarity(representation.get("embedding") or [], row.get("embedding_before") or [])
            score = similarity + (0.25 if signature_match else 0.0) + (0.10 if field_match else 0.0)
            if _norm(row.get("phase")) == _norm(phase):
                score += 0.05
            ranked.append({**row, "retrieval_score": round(score, 6)})
        ranked.sort(key=lambda item: (float(item.get("retrieval_score") or 0), float(item.get("reward") or 0)), reverse=True)
        return mask_sensitive_data(ranked[: max(1, int(limit))])

    def preferred_control_identities(self, matches: Sequence[Dict[str, Any]]) -> List[str]:
        scores: Dict[str, float] = {}
        for row in matches or []:
            identity = str(row.get("selected_control_identity") or "")
            if not identity:
                continue
            weight = max(0.0, float(row.get("retrieval_score") or 0)) * max(0.0, float(row.get("reward") or 0))
            scores[identity] = scores.get(identity, 0.0) + weight
        return [key for key, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]
