from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.replay-policy-cache.v1"
EPISODE_SCHEMA = "hip.replay-policy-episode.v1"
DREAM_SCHEMA = "hip.dreaming-policy-improvement.v1"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _tokens(value: Any) -> set[str]:
    return {x for x in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(x) > 1}


def _stable(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(mask_sensitive_data(dict(payload)), ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _workflow(steps: Sequence[Mapping[str, Any]] | None) -> List[Dict[str, Any]]:
    """Persist only value-free workflow structure suitable for live re-proof."""
    out: List[Dict[str, Any]] = []
    for raw in steps or []:
        if not isinstance(raw, Mapping):
            continue
        typ = str(raw.get("type") or "")
        if not typ or typ == "post_action_learning":
            continue
        row: Dict[str, Any] = {
            "type": typ,
            "action": str(raw.get("action") or ""),
            "label": str(raw.get("label") or ""),
            "risk": str(raw.get("risk") or ""),
        }
        if typ == "fill_from_input":
            row["input_root"] = str(raw.get("input_root") or "")
            row["value_source"] = "current_runtime_input_json"
        elif typ == "search":
            row["value_source"] = "current_task.entity"
        elif typ == "navigate":
            row["target_source"] = "configured_portal_base_or_current_task_start_url"
        elif typ == "live_goal":
            # Learned live-goal actions should be replayed as explicit semantic
            # actions when available, not as a free-form stale prompt.
            continue
        out.append(mask_sensitive_data(row))
    return out


def _fill_metrics(steps: Sequence[Mapping[str, Any]] | None) -> Dict[str, float]:
    leaf_total = 0
    leaf_mapped = 0
    fill_count = 0
    exact_fill_count = 0
    unresolved = 0
    cycles = 0
    for row in steps or []:
        if not isinstance(row, Mapping) or str(row.get("type") or "") != "fill_from_input":
            continue
        fill_count += 1
        total = int(row.get("input_leaf_count") or 0)
        mapped = int(row.get("mapped_input_leaf_count") or 0)
        leaf_total += max(0, total)
        leaf_mapped += max(0, mapped)
        unresolved += len(row.get("unresolved_input_leaves") or [])
        cycles += len(row.get("cycles") or [])
        if bool(row.get("pass")) and str(row.get("verification") or "") == "100_percent_runtime_input_exact_readback":
            exact_fill_count += 1
    coverage = 1.0 if fill_count == 0 else (leaf_mapped / leaf_total if leaf_total else (1.0 if exact_fill_count == fill_count else 0.0))
    return {
        "fill_count": float(fill_count),
        "exact_fill_count": float(exact_fill_count),
        "input_leaf_count": float(leaf_total),
        "mapped_input_leaf_count": float(leaf_mapped),
        "unresolved_leaf_count": float(unresolved),
        "fill_cycle_count": float(cycles),
        "input_coverage": max(0.0, min(1.0, coverage)),
    }


def _repeatable_row_metrics_from_paths(paths: Iterable[str]) -> Dict[str, float]:
    row_tokens: set[str] = set()
    for path in paths:
        matches = re.findall(r"([^\.\[]+)\[(\d+)\]", str(path or ""))
        for name, index in matches:
            row_tokens.add(f"{_norm(name)}[{index}]")
    return {"repeatable_row_identity_count": float(len(row_tokens))}


class ReplayPolicyEngine:
    """Exploration/exploitation + replay-based policy improvement on disk.

    Historical executions become a value-free replay world.  The engine scores
    goal achievement, exact input coverage, repeatable-row completion, verification,
    efficiency and recovery cost.  It then evaluates a small family of policy
    candidates offline ("dreaming") and caches the best policy for future runs.

    The policy never authorizes a browser action.  It only chooses *how to search*.
    The live page, current input.json and mutation gate remain authoritative.
    """

    def __init__(self, root: str | Path, config: Any = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.episodes_path = self.root / "replay_episodes.jsonl"
        self.dreams_path = self.root / "dream_cycles.jsonl"
        self.cache_path = self.root / "policy_cache.json"
        self.old_runs_path = self.root / "old_run_index.json"
        self.cache = self._load_cache()

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    def _load_cache(self) -> Dict[str, Any]:
        raw = _read_json(self.cache_path)
        if raw.get("schema_version") == SCHEMA and isinstance(raw.get("policies"), dict):
            return raw
        return {
            "schema_version": SCHEMA,
            "policies": {},
            "active_policy_version": 0,
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
            "updated_at": utc_now(),
        }

    def _save_cache(self) -> None:
        self.cache["updated_at"] = utc_now()
        safe_write_json(self.cache_path, self.cache)

    @staticmethod
    def signature(task: str, *, actions: Sequence[str] = (), target_area: str = "", input_root: str = "", page_families: Sequence[str] = ()) -> Dict[str, Any]:
        tokens = sorted(_tokens(task) | {_norm(x) for x in actions if _norm(x)} | _tokens(target_area))
        return {
            "signature_id": _stable(tokens, sorted(_norm(x) for x in actions), _norm(target_area), input_root, sorted(_norm(x) for x in page_families)),
            "task_tokens": tokens,
            "actions": sorted(set(_norm(x) for x in actions if _norm(x))),
            "target_area": _norm(target_area),
            "input_root": str(input_root or ""),
            "page_families": sorted(set(_norm(x) for x in page_families if _norm(x))),
        }

    @staticmethod
    def _score_components(*, success: bool, steps: Sequence[Mapping[str, Any]] | None, mutation_required: bool = False, mutation_verified: bool = True, blocked: bool = False, recovery_count: int = 0) -> Dict[str, float]:
        steps = list(steps or [])
        user_steps = [x for x in steps if isinstance(x, Mapping) and str(x.get("type") or "") != "post_action_learning"]
        passed = sum(1 for x in user_steps if bool(x.get("pass")))
        step_completion = (passed / len(user_steps)) if user_steps else (1.0 if success else 0.0)
        fill = _fill_metrics(user_steps)
        goal = 1.0 if success else 0.0
        exact_fill = 1.0 if fill["fill_count"] == 0 else (fill["exact_fill_count"] / max(1.0, fill["fill_count"]))
        mutation = 1.0 if (not mutation_required or mutation_verified) else 0.0
        # Efficiency is relative and bounded: fewer redundant cycles/recoveries score higher.
        cycle_cost = max(0.0, fill["fill_cycle_count"] - fill["fill_count"])
        efficiency = 1.0 / (1.0 + 0.08 * max(0, len(user_steps) - passed) + 0.06 * cycle_cost + 0.12 * max(0, recovery_count))
        score = (
            0.30 * goal
            + 0.24 * fill["input_coverage"]
            + 0.14 * exact_fill
            + 0.12 * step_completion
            + 0.08 * mutation
            + 0.12 * efficiency
        )
        if blocked:
            score -= 0.18
        if not success:
            score -= 0.12
        return {
            "goal_success": goal,
            "input_coverage": fill["input_coverage"],
            "exact_fill_ratio": exact_fill,
            "step_completion": step_completion,
            "mutation_verified": mutation,
            "efficiency": efficiency,
            "recovery_count": float(max(0, recovery_count)),
            "score": round(max(0.0, min(1.0, score)), 6),
            **fill,
        }

    def record_episode(
        self,
        *,
        task: str,
        actions: Sequence[str] = (),
        target_area: str = "",
        input_root: str = "",
        page_families: Sequence[str] = (),
        steps: Sequence[Mapping[str, Any]] | None = None,
        success: bool,
        run_id: str = "",
        source: str = "live_run",
        mutation_required: bool = False,
        mutation_verified: bool = True,
        blocked: bool = False,
        recovery_count: int = 0,
        input_paths: Sequence[str] = (),
        evidence: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        sig = self.signature(task, actions=actions, target_area=target_area, input_root=input_root, page_families=page_families)
        components = self._score_components(
            success=success, steps=steps, mutation_required=mutation_required,
            mutation_verified=mutation_verified, blocked=blocked, recovery_count=recovery_count,
        )
        rows = _repeatable_row_metrics_from_paths(input_paths)
        repeatable_count = float(rows.get("repeatable_row_identity_count") or 0.0)
        repeatable_success = float(components.get("exact_fill_ratio") or 0.0) if repeatable_count > 0 else 1.0
        if repeatable_count > 0:
            # Repeatable rows are a first-class success condition.  A trajectory
            # that missed/overwrote a row cannot become the fastest exploitation
            # policy merely because navigation itself succeeded.
            components["score"] = round(
                max(0.0, min(1.0, 0.90 * float(components.get("score") or 0.0) + 0.10 * repeatable_success)), 6
            )
        rows["repeatable_row_success"] = repeatable_success
        episode = {
            "schema_version": EPISODE_SCHEMA,
            "episode_id": _stable(run_id, sig["signature_id"], utc_now()),
            "recorded_at": utc_now(),
            "run_id": str(run_id or ""),
            "source": str(source or "live_run"),
            "signature": sig,
            "success": bool(success),
            "score": components["score"],
            "score_components": {**components, **rows},
            "workflow": _workflow(steps),
            "evidence": mask_sensitive_data(dict(evidence or {})),
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }
        _append_jsonl(self.episodes_path, episode)
        return mask_sensitive_data(episode)

    def _episodes(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if not self.episodes_path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with self.episodes_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
        except Exception:
            return []
        max_records = int(self._cfg("max_replay_episodes", 5000) or 5000)
        rows = rows[-max(100, max_records):]
        return rows[-limit:] if limit else rows

    def ingest_old_runs(self, runs_dir: str | Path, *, exclude_run: str | Path | None = None) -> Dict[str, Any]:
        if not bool(self._cfg("import_old_runs", True)):
            return {"status": "disabled", "imported": 0}
        root = Path(runs_dir)
        if not root.is_dir():
            return {"status": "not_found", "imported": 0, "runs_dir": str(root)}
        excluded = Path(exclude_run).resolve() if exclude_run else None
        seen_index = _read_json(self.old_runs_path)
        seen = set(str(x) for x in seen_index.get("ingested_runs") or [])
        candidates = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
        candidates = candidates[: max(1, int(self._cfg("max_old_runs_to_import", 200) or 200))]
        imported = 0
        for run in candidates:
            try:
                if excluded is not None and run.resolve() == excluded:
                    continue
            except Exception:
                pass
            key = str(run.resolve())
            if key in seen:
                continue
            universal = _read_json(run / "universal_portal_task_execution.json")
            if universal:
                task = str(universal.get("task") or "historical portal task")
                steps = [x for x in universal.get("steps") or [] if isinstance(x, Mapping)]
                self.record_episode(
                    task=task,
                    actions=[str(x.get("action") or x.get("label") or "") for x in steps],
                    input_root=str((universal.get("plan") or {}).get("input_root") or ""),
                    page_families=[], steps=steps, success=bool(universal.get("pass")), run_id=str(universal.get("task_id") or run.name), source="old_run_import",
                    blocked=str(universal.get("status") or "").lower().startswith("blocked"),
                    mutation_required=bool((universal.get("mutation_gate") or {}).get("mutation_required")),
                    mutation_verified=bool(universal.get("pass")),
                    evidence={"imported_from": run.name},
                )
                imported += 1
            else:
                summary = _read_json(run / "full_dummy_fill_summary.json")
                mission = summary.get("autonomous_mission") if isinstance(summary.get("autonomous_mission"), Mapping) else {}
                phase_summaries = summary.get("phase_summaries") if isinstance(summary.get("phase_summaries"), Mapping) else {}
                if summary and phase_summaries:
                    synthetic_steps: List[Dict[str, Any]] = []
                    for phase, row in phase_summaries.items():
                        if not isinstance(row, Mapping):
                            continue
                        synthetic_steps.append({"type": "phase", "label": str(phase), "pass": str(row.get("status") or "").lower() in {"pass", "complete", "completed"}})
                    success = bool(mission.get("application_complete")) or bool(summary.get("overall_pass"))
                    self.record_episode(
                        task="fill all HIP phases from input json", actions=["fill", "verify"], target_area="all_phases",
                        page_families=list(phase_summaries.keys()), steps=synthetic_steps, success=success,
                        run_id=str((summary.get("run_id") or run.name)), source="old_run_import", blocked=not success,
                        evidence={"imported_from": run.name, "phase_count": len(phase_summaries)},
                    )
                    imported += 1
            seen.add(key)
        safe_write_json(self.old_runs_path, {"schema_version": "hip.old-run-policy-index.v1", "ingested_runs": sorted(seen), "updated_at": utc_now()})
        if imported:
            self.dream(reason="old_run_import")
        return {"status": "complete", "imported": imported, "indexed": len(seen), "runs_dir": str(root)}

    @staticmethod
    def _policy_candidates() -> List[Dict[str, Any]]:
        return [
            {"name": "deployed_baseline", "explore_weight": 0.20, "exploit_weight": 0.80, "early_stop_score": 0.92, "prefer_fast_replay": True},
            {"name": "balanced_adaptive", "explore_weight": 0.45, "exploit_weight": 0.55, "early_stop_score": 0.94, "prefer_fast_replay": True},
            {"name": "novelty_first", "explore_weight": 0.72, "exploit_weight": 0.28, "early_stop_score": 0.97, "prefer_fast_replay": False},
            {"name": "high_confidence_fast", "explore_weight": 0.10, "exploit_weight": 0.90, "early_stop_score": 0.90, "prefer_fast_replay": True},
            {"name": "drift_cautious", "explore_weight": 0.58, "exploit_weight": 0.42, "early_stop_score": 0.96, "prefer_fast_replay": False},
        ]

    @staticmethod
    def _evaluate_candidate(candidate: Mapping[str, Any], episodes: Sequence[Mapping[str, Any]]) -> float:
        if not episodes:
            return 0.0
        total = 0.0
        weight_sum = 0.0
        for idx, ep in enumerate(reversed(list(episodes))):
            score = float(ep.get("score") or 0.0)
            success = 1.0 if ep.get("success") else 0.0
            comp = ep.get("score_components") if isinstance(ep.get("score_components"), Mapping) else {}
            coverage = float(comp.get("input_coverage") or 0.0)
            efficiency = float(comp.get("efficiency") or 0.0)
            recency = math.pow(0.985, idx)
            explore = float(candidate.get("explore_weight") or 0.0)
            exploit = float(candidate.get("exploit_weight") or 0.0)
            # Exploration policies earn credit when prior episodes were incomplete,
            # because those histories contain unresolved states worth rediscovering.
            novelty_need = 1.0 - min(1.0, 0.55 * success + 0.45 * coverage)
            value = exploit * (0.52 * score + 0.28 * success + 0.20 * efficiency) + explore * (0.55 * novelty_need + 0.45 * score)
            if bool(candidate.get("prefer_fast_replay")) and success and coverage >= 0.999:
                value += 0.08 * efficiency
            total += value * recency
            weight_sum += recency
        return total / max(1e-9, weight_sum)

    def dream(self, *, reason: str = "post_run") -> Dict[str, Any]:
        if not bool(self._cfg("dreaming_enabled", True)):
            return {"status": "disabled", "reason": "dreaming_disabled"}
        episodes = self._episodes()
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for ep in episodes:
            sig = ep.get("signature") if isinstance(ep.get("signature"), Mapping) else {}
            sid = str(sig.get("signature_id") or "")
            if sid:
                groups.setdefault(sid, []).append(ep)
        policies = self.cache.setdefault("policies", {})
        updates = 0
        dream_rows: List[Dict[str, Any]] = []
        min_support = max(1, int(self._cfg("min_replay_support", 2) or 2))
        for sid, eps in groups.items():
            candidates = self._policy_candidates()
            previous = policies.get(sid) if isinstance(policies.get(sid), Mapping) else {}
            if previous:
                deployed = previous.get("policy") if isinstance(previous.get("policy"), Mapping) else {}
                if deployed:
                    candidates[0] = {**candidates[0], **dict(deployed), "name": "deployed_baseline"}
            scored = [{**c, "replay_value": round(self._evaluate_candidate(c, eps), 6)} for c in candidates]
            scored.sort(key=lambda x: float(x.get("replay_value") or 0), reverse=True)
            winner = scored[0]
            successes = [x for x in eps if bool(x.get("success"))]
            best_episode = max(successes or eps, key=lambda x: (float(x.get("score") or 0), -len(x.get("workflow") or [])))
            support = len(eps)
            success_rate = sum(1 for x in eps if x.get("success")) / max(1, support)
            avg_score = sum(float(x.get("score") or 0) for x in eps) / max(1, support)
            confidence = min(1.0, (0.45 * success_rate + 0.45 * avg_score + 0.10 * min(1.0, support / max(1, min_support))))
            mode = "exploration"
            if support >= min_support and confidence >= float(self._cfg("exploitation_min_confidence", 0.78) or 0.78) and success_rate >= float(self._cfg("exploitation_min_success_rate", 0.80) or 0.80):
                mode = "exploitation"
            elif support >= 1 and confidence >= float(self._cfg("hybrid_min_confidence", 0.50) or 0.50):
                mode = "hybrid"
            previous_version = int(previous.get("policy_version") or 0)
            policies[sid] = {
                "signature": dict(best_episode.get("signature") or {}),
                "policy_version": previous_version + 1,
                "policy": {k: v for k, v in winner.items() if k != "replay_value"},
                "replay_value": winner["replay_value"],
                "mode": mode,
                "support": support,
                "success_count": len(successes),
                "success_rate": round(success_rate, 6),
                "average_score": round(avg_score, 6),
                "confidence": round(confidence, 6),
                "best_episode_id": best_episode.get("episode_id"),
                "best_workflow": list(best_episode.get("workflow") or []),
                "updated_at": utc_now(),
                "live_reproof_required": True,
                "values_stored": False,
            }
            dream_rows.append({"signature_id": sid, "winner": winner["name"], "mode": mode, "support": support, "confidence": round(confidence, 6), "candidate_scores": scored})
            updates += 1
        self.cache["active_policy_version"] = int(self.cache.get("active_policy_version") or 0) + (1 if updates else 0)
        self._save_cache()
        dream = {
            "schema_version": DREAM_SCHEMA,
            "dreamed_at": utc_now(),
            "reason": reason,
            "episode_count": len(episodes),
            "policy_updates": updates,
            "policies": dream_rows,
            "history_is_replay_world": True,
            "online_execution_performed": False,
            "values_stored": False,
        }
        _append_jsonl(self.dreams_path, dream)
        return mask_sensitive_data(dream)

    def decide(self, *, task: str, actions: Sequence[str] = (), target_area: str = "", input_root: str = "", page_families: Sequence[str] = ()) -> Dict[str, Any]:
        sig = self.signature(task, actions=actions, target_area=target_area, input_root=input_root, page_families=page_families)
        exact = self.cache.get("policies", {}).get(sig["signature_id"]) if isinstance(self.cache.get("policies"), dict) else None
        best = dict(exact) if isinstance(exact, Mapping) else {}
        if not best:
            # Semantic nearest policy: useful when the same activity uses a new entity.
            current = set(sig["task_tokens"])
            ranked: List[tuple[float, Dict[str, Any]]] = []
            for row in (self.cache.get("policies") or {}).values():
                if not isinstance(row, Mapping):
                    continue
                rsig = row.get("signature") if isinstance(row.get("signature"), Mapping) else {}
                other = set(str(x) for x in rsig.get("task_tokens") or [])
                union = current | other
                sim = len(current & other) / len(union) if union else 0.0
                if input_root and str(rsig.get("input_root") or "") == str(input_root):
                    sim += 0.15
                ranked.append((sim, dict(row)))
            ranked.sort(key=lambda x: (x[0], float(x[1].get("confidence") or 0)), reverse=True)
            if ranked and ranked[0][0] >= float(self._cfg("semantic_policy_match_threshold", 0.55) or 0.55):
                best = ranked[0][1]
                best["semantic_match_score"] = round(ranked[0][0], 6)
        if not best:
            return {"mode": "exploration", "reason": "no_replay_policy", "signature": sig, "confidence": 0.0, "workflow": [], "live_reproof_required": True}
        confidence = float(best.get("confidence") or 0.0)
        mode = str(best.get("mode") or "exploration")
        return mask_sensitive_data({
            "mode": mode,
            "reason": "replay_policy_cache",
            "signature": sig,
            "confidence": confidence,
            "support": int(best.get("support") or 0),
            "success_rate": float(best.get("success_rate") or 0.0),
            "average_score": float(best.get("average_score") or 0.0),
            "policy_version": int(best.get("policy_version") or 0),
            "policy": dict(best.get("policy") or {}),
            "workflow": list(best.get("best_workflow") or []),
            "semantic_match_score": best.get("semantic_match_score"),
            "live_reproof_required": True,
            "values_reused": False,
        })

    def action_policy(self, *, memory_matches: Sequence[Mapping[str, Any]], node_signature: str = "") -> Dict[str, Any]:
        """Derive lower-level exploration/exploitation hints from historical action outcomes."""
        visits: Dict[str, Dict[str, float]] = {}
        for row in memory_matches or []:
            action = str(row.get("selected_recovery") or "execute_bound_action")
            item = visits.setdefault(action, {"visits": 0.0, "success": 0.0, "reward": 0.0})
            item["visits"] += 1.0
            item["success"] += 1.0 if row.get("success") else 0.0
            item["reward"] += float(row.get("reward") or 0.0)
        total = sum(x["visits"] for x in visits.values())
        if total < float(self._cfg("min_action_replay_support", 2) or 2):
            return {"mode": "exploration", "preferred_actions": [], "support": int(total), "reason": "insufficient_action_replay"}
        scored: List[tuple[float, str]] = []
        for action, row in visits.items():
            avg = row["reward"] / max(1.0, row["visits"])
            sr = row["success"] / max(1.0, row["visits"])
            scored.append((0.65 * sr + 0.35 * max(-1.0, min(1.0, avg)), action))
        scored.sort(reverse=True)
        top_score = scored[0][0] if scored else 0.0
        mode = "exploitation" if top_score >= float(self._cfg("action_exploitation_threshold", 0.70) or 0.70) else "hybrid"
        return {"mode": mode, "preferred_actions": [x[1] for x in scored[:3]], "support": int(total), "top_score": round(top_score, 6), "node_signature": node_signature}

    def manifest(self) -> Dict[str, Any]:
        episodes = self._episodes()
        policies = self.cache.get("policies") if isinstance(self.cache.get("policies"), dict) else {}
        modes: Dict[str, int] = {"exploration": 0, "hybrid": 0, "exploitation": 0}
        for row in policies.values():
            if isinstance(row, Mapping):
                mode = str(row.get("mode") or "exploration")
                modes[mode] = modes.get(mode, 0) + 1
        recent = sorted([x for x in episodes if isinstance(x, Mapping)], key=lambda x: str(x.get("recorded_at") or ""), reverse=True)[:10]
        return mask_sensitive_data({
            "schema_version": SCHEMA,
            "enabled": bool(self._cfg("enabled", True)),
            "episode_count": len(episodes),
            "policy_count": len(policies),
            "active_policy_version": int(self.cache.get("active_policy_version") or 0),
            "exploration_policy_count": modes.get("exploration", 0),
            "hybrid_policy_count": modes.get("hybrid", 0),
            "exploitation_policy_count": modes.get("exploitation", 0),
            "episodes_path": str(self.episodes_path),
            "policy_cache_path": str(self.cache_path),
            "dream_cycles_path": str(self.dreams_path),
            "recent_runs": [
                {"run_id": x.get("run_id"), "source": x.get("source"), "score": x.get("score"), "success": x.get("success"), "signature_id": (x.get("signature") or {}).get("signature_id")}
                for x in recent
            ],
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
            "live_reproof_required": True,
        })

    def list_policies(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for sid, raw in (self.cache.get("policies") or {}).items():
            if not isinstance(raw, Mapping):
                continue
            sig = raw.get("signature") if isinstance(raw.get("signature"), Mapping) else {}
            rows.append({
                "signature_id": sid,
                "mode": raw.get("mode"),
                "policy_version": raw.get("policy_version"),
                "confidence": raw.get("confidence"),
                "support": raw.get("support"),
                "success_count": raw.get("success_count"),
                "success_rate": raw.get("success_rate"),
                "average_score": raw.get("average_score"),
                "winner": (raw.get("policy") or {}).get("name") if isinstance(raw.get("policy"), Mapping) else "",
                "task_tokens": list(sig.get("task_tokens") or []),
                "target_area": sig.get("target_area"),
                "input_root": sig.get("input_root"),
                "workflow_step_count": len(raw.get("best_workflow") or []),
                "updated_at": raw.get("updated_at"),
                "live_reproof_required": True,
            })
        rows.sort(key=lambda x: (float(x.get("confidence") or 0), int(x.get("support") or 0), str(x.get("updated_at") or "")), reverse=True)
        return mask_sensitive_data(rows[: max(1, int(limit))])


def replay_policy_engine_from_config(config: Any) -> ReplayPolicyEngine:
    cfg = getattr(config, "replay_policy", None)
    memory_dir = Path(getattr(getattr(config, "reporting", None), "memory_dir", "./data/hip_memory"))
    brain_dir = str(getattr(getattr(config, "brain", None), "directory", "portal_brain") or "portal_brain")
    subdir = str(getattr(cfg, "memory_subdir", "replay_policy") or "replay_policy")
    return ReplayPolicyEngine(memory_dir / brain_dir / subdir, cfg)


__all__ = ["ReplayPolicyEngine", "replay_policy_engine_from_config"]
