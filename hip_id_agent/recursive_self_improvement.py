from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data

SCHEMA = "hip.recursive-self-improvement.v1"


class RecursiveSelfImprovementEngine:
    """Bounded recursive improvement of policies/models/skills from verified outcomes.

    Improves behavioral policy and model routing, never source code. Browser actions
    remain governed by current input.json, live-page proof and mutation authorization.
    """

    def __init__(self, root: str | Path, config: Any, *, replay_policy: Any, model_portfolio: Any, skill_library: Any = None) -> None:
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.config = config; self.replay_policy = replay_policy; self.model_portfolio = model_portfolio; self.skill_library = skill_library
        self.state_path = self.root / "recursive_improvement_state.json"; self.cycles_path = self.root / "recursive_improvement_cycles.jsonl"; self.state = self._load()

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    def _load(self) -> Dict[str, Any]:
        try:
            row = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(row, dict) and row.get("schema_version") == SCHEMA: return row
        except Exception: pass
        return {"schema_version": SCHEMA, "cycle": 0, "best_reward": 0.0, "plateau_count": 0, "updated_at": utc_now(), "code_self_modification": False}

    def _append(self, row: Mapping[str, Any]) -> None:
        self.cycles_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cycles_path.open("a", encoding="utf-8") as handle: handle.write(json.dumps(mask_sensitive_data(dict(row)), ensure_ascii=False, sort_keys=True, default=str) + "\n")

    def improve(self, *, run_reward: float, success: bool, reason: str = "run_completed", model_trace: Optional[Mapping[str, Any]] = None, skill_feedback: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        if not bool(self._cfg("enabled", True)):
            return {"status": "disabled"}
        reward = max(0.0, min(1.0, float(run_reward or 0.0)))
        previous_best = float(self.state.get("best_reward") or 0.0)
        min_gain = float(self._cfg("minimum_improvement", 0.005) or 0.005)
        configured_depth = int(self._cfg("max_recursive_cycles", 3) or 0)
        # 0 means the enclosing persistent-goal loop is open-ended. Each call still
        # performs a small, auditable set of policy/model dreaming micro-cycles.
        max_depth = 3 if configured_depth <= 0 else max(1, min(configured_depth, 8))
        update_replay = bool(self._cfg("update_replay_policy", True))
        update_models = bool(self._cfg("update_model_portfolio", True))
        update_skills = bool(self._cfg("update_skill_confidence", True))
        cycles = []
        last_best = previous_best
        for depth in range(1, max_depth + 1):
            replay_dream = self.replay_policy.dream(reason=f"recursive_self_improvement:{reason}:depth{depth}") if (update_replay and self.replay_policy is not None) else {}
            model_dream = self.model_portfolio.dream(reason=f"recursive_self_improvement:{reason}:depth{depth}") if (update_models and self.model_portfolio is not None) else {}
            candidate_best = max(last_best, reward)
            gain = candidate_best - last_best
            cycle = {
                "depth": depth,
                "recorded_at": utc_now(),
                "run_reward": reward,
                "success": bool(success),
                "previous_best": last_best,
                "candidate_best": candidate_best,
                "gain": gain,
                "replay_policy_updates": int((replay_dream or {}).get("policy_updates") or 0),
                "model_champion_changes": len((model_dream or {}).get("champion_changes") or {}),
                "replay_update_enabled": update_replay,
                "model_update_enabled": update_models,
                "skill_update_enabled": update_skills,
            }
            cycles.append(cycle)
            self._append(cycle)
            last_best = candidate_best
            if depth > 1 and gain < min_gain and cycle["replay_policy_updates"] == 0 and cycle["model_champion_changes"] == 0:
                break

        skill_review: Dict[str, Any] = {"enabled": update_skills, "performed": False}
        if update_skills and self.skill_library is not None:
            # Normal live execution already records the matched skill outcome exactly
            # once. Recursive improvement therefore reviews/persists that evidence
            # rather than double-counting the same success/failure.
            feedback = dict(skill_feedback or {})
            skill_review.update({
                "skill_id": str(feedback.get("skill_id") or ""),
                "outcome_already_recorded": bool(feedback.get("outcome_already_recorded", True)),
            })
            if feedback.get("skill_id") and not bool(feedback.get("outcome_already_recorded", True)):
                try:
                    row = self.skill_library.record_outcome(
                        str(feedback.get("skill_id")),
                        success=bool(feedback.get("success", success)),
                        reason=str(feedback.get("reason") or reason),
                        run_id=str(feedback.get("run_id") or ""),
                    )
                    skill_review.update({"performed": True, "status": row.get("status"), "confidence": row.get("confidence")})
                except Exception as exc:
                    skill_review.update({"error": str(exc)[:300]})
            else:
                try:
                    self.skill_library.save()
                    skill_review["reviewed_manifest"] = self.skill_library.manifest()
                except Exception as exc:
                    skill_review["error"] = str(exc)[:300]

        self.state["cycle"] = int(self.state.get("cycle") or 0) + len(cycles)
        self.state["best_reward"] = max(previous_best, reward)
        self.state["plateau_count"] = 0 if reward > previous_best + min_gain else int(self.state.get("plateau_count") or 0) + 1
        self.state["last_success"] = bool(success)
        self.state["last_reward"] = reward
        self.state["updated_at"] = utc_now()
        self.state["code_self_modification"] = False
        self.state["last_update_flags"] = {"replay_policy": update_replay, "model_portfolio": update_models, "skill_confidence": update_skills}
        safe_write_json(self.state_path, self.state)
        return {"status": "complete", "cycles": cycles, "skill_review": skill_review, "state": dict(self.state), "bounded": configured_depth > 0, "open_ended_across_goal_cycles": configured_depth <= 0, "live_page_still_authoritative": True}

    def manifest(self) -> Dict[str, Any]:
        configured = int(self._cfg("max_recursive_cycles", 3) or 0)
        return {**self.state, "enabled": bool(self._cfg("enabled", True)), "max_recursive_cycles": configured, "open_ended_across_goal_cycles": configured <= 0, "state_path": str(self.state_path), "cycles_path": str(self.cycles_path), "code_self_modification": False}


def recursive_improvement_from_config(app_config: Any, *, replay_policy: Any, model_portfolio: Any, skill_library: Any = None) -> RecursiveSelfImprovementEngine:
    root = Path(app_config.reporting.memory_dir) / str(getattr(app_config.recursive_self_improvement, "memory_subdir", "recursive_self_improvement") or "recursive_self_improvement")
    return RecursiveSelfImprovementEngine(root, app_config.recursive_self_improvement, replay_policy=replay_policy, model_portfolio=model_portfolio, skill_library=skill_library)
