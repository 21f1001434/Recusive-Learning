from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data

SCHEMA = "hip.deterministic-recipe-library.v1"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _words(value: Any) -> set[str]:
    return {x for x in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(x) > 1}


def _stable(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(x or "") for x in parts).encode("utf-8", errors="ignore")).hexdigest()[:24]


class DeterministicRecipeLibrary:
    """Promotes repeatedly successful adaptive trajectories into script-like recipes.

    Recipes contain only semantic action order + input sources. They never persist
    customer values, CSS/XPath or coordinates. Every step is live-reproved before
    execution and drift immediately falls back to adaptive discovery.
    """

    def __init__(self, root: str | Path, config: Any = None) -> None:
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.config = config; self.path = self.root / "deterministic_recipes.json"; self.data = self._load()

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    def _load(self) -> Dict[str, Any]:
        try:
            row = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(row, dict) and row.get("schema_version") == SCHEMA: return row
        except Exception: pass
        return {"schema_version": SCHEMA, "recipes": {}, "updated_at": utc_now(), "values_stored": False, "selectors_stored": False, "coordinates_stored": False}

    def _save(self) -> None:
        self.data["updated_at"] = utc_now(); safe_write_json(self.path, self.data)

    @staticmethod
    def _signature(task: str, actions: Sequence[str], target_area: str, input_root: str) -> Dict[str, Any]:
        return {"task_tokens": sorted(_words(task)), "actions": sorted({_norm(x) for x in actions if _norm(x)}), "target_area": _norm(target_area), "input_root": str(input_root or "")}

    @staticmethod
    def _sanitize_steps(steps: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for raw in steps:
            if not isinstance(raw, Mapping): continue
            typ = str(raw.get("type") or "")
            if typ in {"post_action_learning", "live_goal", "adaptive_goal"}: continue
            row = {
                "type": typ, "action": str(raw.get("action") or ""),
                "label": str(raw.get("label") or ""), "risk": str(raw.get("risk") or ""),
                "role": str(raw.get("role") or ""), "section": str(raw.get("section") or ""),
                "input_path": str(raw.get("input_path") or ""),
                "semantic_control_id": str(raw.get("semantic_control_id") or ""),
            }
            if typ == "fill_from_input": row.update({"input_root": str(raw.get("input_root") or ""), "value_source": "current_runtime_input_json"})
            elif typ == "search": row["value_source"] = "current_task.entity"
            elif typ in {"navigate", "navigate_observed"}:
                row["target_source"] = "configured_portal_base_or_current_task_start_url"
                row["target_route"] = str(raw.get("target_route") or "")
            elif typ == "semantic_click":
                row["human_demonstrated"] = bool(raw.get("human_demonstrated"))
                row["value_source"] = "none"
            out.append(mask_sensitive_data(row))
        return out

    def record_success(self, *, task: str, actions: Sequence[str], target_area: str, input_root: str, steps: Sequence[Mapping[str, Any]], form_blueprints: Sequence[Mapping[str, Any]], reward: float, run_id: str) -> Dict[str, Any]:
        signature = self._signature(task, actions, target_area, input_root)
        recipe_id = _stable(json.dumps(signature, sort_keys=True))
        recipes = self.data.setdefault("recipes", {})
        row = recipes.setdefault(recipe_id, {"schema_version": "hip.deterministic-recipe.v1", "recipe_id": recipe_id, "signature": signature, "success_count": 0, "failure_count": 0, "reward_sum": 0.0, "status": "learning", "created_at": utc_now()})
        row["success_count"] = int(row.get("success_count") or 0) + 1
        row["reward_sum"] = float(row.get("reward_sum") or 0.0) + max(0.0, min(1.0, float(reward or 0.0)))
        row["last_success_at"] = utc_now(); row["last_run_id"] = str(run_id or "")
        sanitized = self._sanitize_steps(steps)
        if sanitized: row["steps"] = sanitized
        clean_blueprints = []
        for bp in form_blueprints:
            if not isinstance(bp, Mapping): continue
            clean_blueprints.append(mask_sensitive_data({"input_root": bp.get("input_root"), "fields": bp.get("fields") or [], "values_stored": False, "selectors_stored": False, "coordinates_stored": False}))
        if clean_blueprints: row["form_blueprints"] = clean_blueprints
        n = int(row.get("success_count") or 0); avg = float(row.get("reward_sum") or 0.0) / max(1, n)
        row["average_reward"] = avg
        if n >= int(self._cfg("min_verified_successes", 2) or 2) and avg >= float(self._cfg("min_average_reward", 0.90) or 0.90): row["status"] = "validated"
        row.update({"values_stored": False, "selectors_stored": False, "coordinates_stored": False, "live_reproof_required": True})
        self._save(); return mask_sensitive_data(dict(row))

    def record_supervised_phase_success(self, *, phase: str, run_id: str, semantic_steps: Sequence[Mapping[str, Any]], exact_verified: bool, human_confirmed: bool, judge_pass: bool, golden_aligned: bool = False) -> Dict[str, Any]:
        """Atomically promote a human-supervised, exact-verified HIP phase recipe.

        Unlike autonomous promotion, a final human confirmation plus exact live
        proof is sufficient for immediate deterministic use.  The recipe remains
        semantic/value-free and is always live-reproved on replay.
        """
        signature = self._signature(f"HIP phase {phase}", ["navigate", "fill", "verify"], phase, phase)
        recipe_id = _stable(json.dumps(signature, sort_keys=True))
        row = self.data.setdefault("recipes", {}).setdefault(recipe_id, {
            "schema_version": "hip.deterministic-recipe.v1", "recipe_id": recipe_id,
            "signature": signature, "success_count": 0, "failure_count": 0,
            "reward_sum": 0.0, "status": "learning", "created_at": utc_now(),
        })
        trusted = bool(exact_verified and human_confirmed and judge_pass)
        if trusted:
            row["success_count"] = int(row.get("success_count") or 0) + 1
            row["reward_sum"] = float(row.get("reward_sum") or 0.0) + 1.0
            row["average_reward"] = float(row["reward_sum"]) / max(1, int(row["success_count"]))
            row["status"] = "validated"
            row["supervised_promotion"] = True
            row["human_confirmed"] = True
            row["exact_verified"] = True
            row["golden_aligned"] = bool(golden_aligned)
            row["last_success_at"] = utc_now()
            row["last_run_id"] = str(run_id or "")
            sanitized = self._sanitize_steps(semantic_steps)
            # Interactive teaching uses semantic_click/navigate_observed; preserve
            # these value-free steps in a bounded normalized form.
            if not sanitized:
                sanitized = []
                for raw in semantic_steps:
                    if not isinstance(raw, Mapping):
                        continue
                    typ = str(raw.get("type") or "")
                    if typ not in {"semantic_click", "navigate_observed", "fill_from_input", "click", "navigate"}:
                        continue
                    sanitized.append(mask_sensitive_data({
                        "type": typ, "action": str(raw.get("action") or ("click" if typ == "semantic_click" else "navigate")),
                        "label": str(raw.get("label") or ""), "role": str(raw.get("role") or ""),
                        "tag": str(raw.get("tag") or ""), "target_route": str(raw.get("target_route") or ""),
                        "value_source": str(raw.get("value_source") or "current_runtime_input_json"),
                        "human_demonstrated": bool(raw.get("human_demonstrated")),
                    }))
            if sanitized:
                row["steps"] = sanitized[:300]
        row.update({
            "live_reproof_required": True, "values_stored": False,
            "selectors_stored": False, "coordinates_stored": False,
        })
        self._save()
        return mask_sensitive_data(dict(row))

    def record_failure(self, recipe_id: str, reason: str = "drift") -> Dict[str, Any]:
        row = self.data.setdefault("recipes", {}).get(str(recipe_id or ""))
        if not isinstance(row, dict): return {"status": "not_found"}
        row["failure_count"] = int(row.get("failure_count") or 0) + 1; row["last_failure_at"] = utc_now(); row["last_failure_reason"] = str(reason or "")[:500]
        if int(row["failure_count"]) >= int(self._cfg("demote_after_failures", 2) or 2): row["status"] = "drifted"
        self._save(); return mask_sensitive_data(dict(row))

    def match(self, *, task: str, actions: Sequence[str], target_area: str = "", input_root: str = "") -> Dict[str, Any]:
        current = self._signature(task, actions, target_area, input_root); ct = set(current["task_tokens"]); ca = set(current["actions"])
        best: Dict[str, Any] = {}; best_score = 0.0
        for row in (self.data.get("recipes") or {}).values():
            if not isinstance(row, Mapping) or row.get("status") != "validated": continue
            sig = row.get("signature") if isinstance(row.get("signature"), Mapping) else {}; st = set(sig.get("task_tokens") or []); sa = set(sig.get("actions") or [])
            token_score = len(ct & st)/max(1,len(ct|st)) if (ct or st) else 1.0; action_score = len(ca & sa)/max(1,len(ca|sa)) if (ca or sa) else 1.0
            root_score = 1.0 if input_root and str(sig.get("input_root") or "") == input_root else (0.5 if not input_root else 0.0)
            score = 0.55*token_score + 0.30*action_score + 0.15*root_score
            if score > best_score: best_score = score; best = dict(row)
        if not best or best_score < float(self._cfg("min_match_score", 0.72) or 0.72): return {"active": False, "reason": "no_validated_recipe", "match_score": best_score}
        return {"active": True, "reason": "validated_deterministic_recipe", "match_score": round(best_score, 4), "recipe": mask_sensitive_data(best), "live_reproof_required": True}

    def manifest(self) -> Dict[str, Any]:
        rows = list((self.data.get("recipes") or {}).values())
        return {"schema_version": SCHEMA, "enabled": bool(self._cfg("enabled", True)), "recipe_count": len(rows), "validated_count": sum(1 for x in rows if isinstance(x, Mapping) and x.get("status") == "validated"), "path": str(self.path), "values_stored": False, "selectors_stored": False, "coordinates_stored": False}


def deterministic_recipe_from_config(app_config: Any) -> DeterministicRecipeLibrary:
    root = Path(app_config.reporting.memory_dir) / str(getattr(app_config.deterministic_recipe, "memory_subdir", "deterministic_recipes") or "deterministic_recipes")
    return DeterministicRecipeLibrary(root, app_config.deterministic_recipe)
