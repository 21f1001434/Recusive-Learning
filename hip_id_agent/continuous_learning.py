from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .capability_graph import HIPCapabilityGraph, PHASE_TO_CAPABILITY_FAMILY
from .models import utc_now
from .portal_brain import PortalBrain
from .replay_policy import replay_policy_engine_from_config
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .trajectory_memory import TrajectoryMemory

_SCHEMA = "hip.continuous-portal-learning.v1"
_SELECTORISH = re.compile(r"(^[#.\[]|css=|xpath=|nth-child|data-hip-|formcontrolname=|input\[|button\[)", re.I)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _event_dict(event: Any) -> Dict[str, Any]:
    if isinstance(event, Mapping):
        return dict(event)
    if is_dataclass(event):
        return asdict(event)
    return {k: getattr(event, k) for k in (
        "action_id", "type", "target", "page_url_before", "page_url_after", "success",
        "error", "stage", "backend", "execution_provenance", "was_secret",
    ) if hasattr(event, k)}


def _semantic_label(row: Mapping[str, Any]) -> str:
    prov = row.get("execution_provenance") if isinstance(row.get("execution_provenance"), Mapping) else {}
    semantic_id = str(prov.get("semantic_control_id") or "").strip()
    if semantic_id:
        return semantic_id[:500]
    hint = str(prov.get("selector_hint") or "").strip()
    if hint and not _SELECTORISH.search(hint):
        return re.sub(r"\s+", " ", hint)[:500]
    target = re.sub(r"\s*\[(?:framework|executor|decision)=[^\]]+\]", "", str(row.get("target") or "")).strip()
    if target and not _SELECTORISH.search(target):
        return re.sub(r"\s+", " ", target)[:500]
    typ = str(row.get("type") or "action")
    return f"{typ}_control"


def _kind(action_type: str) -> str:
    return {
        "fill": "form_field_interaction",
        "search": "search_interaction",
        "click": "action",
        "press": "keyboard_action",
        "navigate": "navigation",
        "verify": "verification",
    }.get(str(action_type or "").lower(), "interaction")


class ContinuousPortalLearningEngine:
    """Turn real browser interaction into reusable, value-free portal experience.

    Filling/navigation is treated as experience collection.  Successful actions are
    written to the shared capability graph and trajectory memory immediately after
    a judged phase.  Failed actions become negative evidence; customer values,
    selectors and screen coordinates are intentionally not persisted.
    """

    def __init__(self, app_config: Any, *, capability_graph: HIPCapabilityGraph, replay_policy: Any = None) -> None:
        self.config = getattr(app_config, "continuous_learning", None)
        self.app_config = app_config
        self.graph = capability_graph
        self.replay = replay_policy or replay_policy_engine_from_config(app_config)
        root = Path(app_config.reporting.memory_dir) / str(getattr(self.config, "memory_subdir", "continuous_learning") or "continuous_learning")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.experience_path = root / "interaction_experiences.jsonl"
        self.summary_path = root / "summary.json"
        self.trajectory = TrajectoryMemory(root / "trajectory_memory")
        try:
            self.portal_brain = PortalBrain.from_config(app_config)
        except Exception:
            self.portal_brain = None

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    def enabled(self) -> bool:
        return bool(self._cfg("enabled", True))

    def _action_learning_enabled(self, action_type: str, *, success: bool) -> bool:
        """Honor the public learning switches instead of treating them as documentation only."""
        typ = str(action_type or "interaction").strip().lower()
        if typ == "fill" and not bool(self._cfg("learn_from_fill", True)):
            return False
        if typ == "click" and not bool(self._cfg("learn_from_click", True)):
            return False
        if typ == "navigate" and not bool(self._cfg("learn_from_navigation", True)):
            return False
        if typ == "search" and not bool(self._cfg("learn_from_search", True)):
            return False
        if not success and not bool(self._cfg("learn_failed_actions_as_negative_evidence", True)):
            return False
        return True

    def _append(self, row: Mapping[str, Any]) -> None:
        self.experience_path.parent.mkdir(parents=True, exist_ok=True)
        with self.experience_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(mask_sensitive_data(dict(row)), ensure_ascii=False, sort_keys=True, default=str) + "\n")

    def learn_phase(
        self,
        *,
        browser: Any,
        phase: str,
        run_id: str,
        task: str,
        action_start_index: int = 0,
        exact_verified: bool,
        judge_pass: bool,
        human_pass: Optional[bool] = None,
        input_root: str = "",
        page_families: Sequence[str] = (),
    ) -> Dict[str, Any]:
        if not self.enabled():
            return {"status": "disabled"}
        family = PHASE_TO_CAPABILITY_FAMILY.get(str(phase), _norm(phase))
        promotion_requires_human = bool(self._cfg("promote_only_after_exact_judge_human_pass", True))
        human_review_supplied = human_pass is not None
        human_gate = (human_pass is True) if promotion_requires_human else (human_pass is not False)
        safe_success = bool(exact_verified and judge_pass and human_gate)

        events = list(getattr(browser, "action_events", []) or [])[max(0, int(action_start_index or 0)):]
        rows: List[Dict[str, Any]] = []
        cap_ids: List[str] = []
        sequence_cap_ids: List[str] = []
        successful = 0
        failed = 0
        negative_evidence = 0
        skipped_by_policy = 0
        for event in events[-int(self._cfg("max_actions_per_phase", 1000) or 1000):]:
            raw = _event_dict(event)
            typ = str(raw.get("type") or "interaction").lower()
            success = bool(raw.get("success"))
            if not self._action_learning_enabled(typ, success=success):
                skipped_by_policy += 1
                continue
            label = _semantic_label(raw)
            if success:
                successful += 1
            else:
                failed += 1
                negative_evidence += 1
            prov = raw.get("execution_provenance") if isinstance(raw.get("execution_provenance"), Mapping) else {}
            effect_pass = bool(prov.get("semantic_effect_pass"))
            exact_value_pass = bool(prov.get("exact_value_commit_verified"))
            # Fill/click/press must prove the intended semantic effect. Navigation and
            # search may be verified by their successful route/query transition.
            verified = bool(
                success and (
                    effect_pass
                    or typ in {"navigate", "search"}
                    or (typ == "fill" and exact_value_pass)
                )
            )
            trusted_action = bool(verified and safe_success)
            cap = self.graph.observe_capability(
                page_family=family,
                kind=_kind(typ),
                label=label,
                role=str(prov.get("semantic_role") or ""),
                scope=str(raw.get("stage") or phase),
                section=str(raw.get("stage") or phase),
                risk="read" if typ in {"navigate", "search", "verify"} else "draft",
                evidence={
                    "url": str(raw.get("page_url_after") or raw.get("page_url_before") or ""),
                    "phase": phase,
                    "action_type": typ,
                    "actual_executor": str(prov.get("actual_executor") or raw.get("backend") or ""),
                    "semantic_effect_pass": effect_pass,
                    "exact_value_commit_verified": exact_value_pass,
                    "run_id": run_id,
                    "values_stored": False,
                    "negative_evidence": bool(not success),
                },
                run_id=run_id,
                knowledge_source="live_interaction_experience",
                trust=("validated_live" if trusted_action else ("negative_evidence" if not success else "observed")),
                verified=trusted_action,
            )
            cap_id = str(cap.get("capability_id") or "")
            if cap_id:
                cap_ids.append(cap_id)
                # Failed or semantically-unverified actions remain evidence only; they
                # can never become a trusted followed_by route.
                if verified:
                    sequence_cap_ids.append(cap_id)
                if trusted_action:
                    self.graph.mark_success(cap_id)
            experience = {
                "schema_version": _SCHEMA,
                "recorded_at": utc_now(),
                "run_id": run_id,
                "phase": phase,
                "page_family": family,
                "action_id": str(raw.get("action_id") or ""),
                "action_type": typ,
                "semantic_label": label,
                "semantic_control_id": str(prov.get("semantic_control_id") or ""),
                "success": success,
                "semantic_effect_pass": effect_pass,
                "action_verified": verified,
                "negative_evidence": bool(not success),
                "exact_phase_verified": bool(exact_verified),
                "judge_pass": bool(judge_pass),
                "human_pass": human_pass,
                "human_review_supplied": human_review_supplied,
                "trusted_action": trusted_action,
                "actual_executor": str(prov.get("actual_executor") or raw.get("backend") or ""),
                "url_changed": bool(str(raw.get("page_url_before") or "") != str(raw.get("page_url_after") or "")),
                "values_stored": False,
                "selectors_stored": False,
                "coordinates_stored": False,
            }
            rows.append(experience)
            self._append(experience)
            try:
                self.trajectory.record(
                    phase=phase,
                    family=family,
                    node={"section": raw.get("stage") or phase, "key": label, "label": label},
                    representation_before={"state_fingerprint": str(raw.get("page_url_before") or "")},
                    action_plan={"type": typ, "semantic_label": label},
                    outcome={"success": success, "verified": verified, "negative_evidence": bool(not success), "reason": str(raw.get("error") or "")[:500]},
                    representation_after={"state_fingerprint": str(raw.get("page_url_after") or "")},
                )
            except Exception:
                pass

        for left, right in zip(sequence_cap_ids, sequence_cap_ids[1:]):
            if left and right and left != right:
                self.graph.add_relation(
                    source=left, relation="followed_by", target=right,
                    evidence={"phase": phase, "run_id": run_id, "trusted_promotion": safe_success},
                )
        self.graph.save()

        # Teach Portal Brain only the verified-success route. Wrong clicks/fills stay
        # negative trajectory/capability evidence and are intentionally excluded from
        # the route that may later become deterministic.
        verified_rows = [r for r in rows if r.get("success") and r.get("action_verified")]
        brain_learning: Dict[str, Any] = {"status": "unavailable"}
        if self.portal_brain is not None and rows:
            navigation_edges: List[Dict[str, Any]] = []
            for left, right in zip(verified_rows, verified_rows[1:]):
                navigation_edges.append({
                    "from": f"{left.get('action_type')}:{left.get('semantic_label')}",
                    "to": f"{right.get('action_type')}:{right.get('semantic_label')}",
                    "relation": "followed_by",
                    "phase": phase,
                    "source": "continuous_fill_navigation_learning",
                })
            try:
                brain_learning = self.portal_brain.ingest_autonomous_learning(
                    phase=phase,
                    summary={
                        "attempt": 1,
                        "page_model": {
                            "fingerprint": "",
                            "url_template": "",
                            "headings": [], "tabs": [],
                            "control_count_after": len(verified_rows),
                        },
                        "navigation_edges": navigation_edges,
                        "coverage": {
                            "interaction_count": len(rows),
                            "verified_route_actions": len(verified_rows),
                            "successful_actions": successful,
                            "failed_actions": failed,
                            "negative_evidence_count": negative_evidence,
                            "unexplored_safe_controls": [],
                        },
                    },
                    trust="validated" if safe_success else ("candidate" if verified_rows else "negative"),
                    run_id=run_id,
                )
            except Exception as exc:
                brain_learning = {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))[:500]}

        workflow = [
            {"type": r["action_type"], "label": r["semantic_label"], "risk": "read" if r["action_type"] in {"navigate", "search", "verify"} else "draft"}
            for r in verified_rows
        ]
        try:
            episode = self.replay.record_episode(
                task=task,
                actions=[str(r.get("action_type") or "") for r in verified_rows],
                target_area=family,
                input_root=input_root,
                page_families=list(page_families or [family]),
                steps=workflow,
                success=safe_success,
                run_id=run_id,
                source="continuous_fill_navigation_learning",
                mutation_required=False,
                mutation_verified=True,
                blocked=not safe_success,
                recovery_count=failed,
                evidence={
                    "phase": phase,
                    "experience_count": len(rows),
                    "verified_route_actions": len(verified_rows),
                    "negative_evidence_count": negative_evidence,
                    "values_stored": False,
                },
            )
        except Exception as exc:
            episode = {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))[:500]}

        if safe_success and verified_rows:
            learning_status = "trusted_promoted"
        elif verified_rows:
            learning_status = "candidate_learned"
        elif negative_evidence:
            learning_status = "negative_evidence_learned"
        elif rows:
            learning_status = "observed_unverified"
        else:
            learning_status = "no_actions"

        summary = {
            "schema_version": _SCHEMA,
            "recorded_at": utc_now(),
            "status": learning_status,
            "phase": phase,
            "page_family": family,
            "run_id": run_id,
            "experience_count": len(rows),
            "successful_actions": successful,
            "failed_actions": failed,
            "unique_capabilities": len(set(cap_ids)),
            "verified_route_actions": len(verified_rows),
            "negative_evidence_count": negative_evidence,
            "skipped_by_policy": skipped_by_policy,
            "promotion_requires_human": promotion_requires_human,
            "human_review_supplied": human_review_supplied,
            "trusted_promotion": safe_success,
            "replay_episode": episode,
            "portal_brain_learning": brain_learning,
            "memory_root": str(self.root),
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }
        safe_write_json(self.summary_path, summary)
        return mask_sensitive_data(summary)

    def manifest(self) -> Dict[str, Any]:
        count = 0
        if self.experience_path.is_file():
            try:
                with self.experience_path.open("r", encoding="utf-8") as handle:
                    count = sum(1 for line in handle if line.strip())
            except Exception:
                count = 0
        return {
            "enabled": self.enabled(),
            "root": str(self.root),
            "experience_count": count,
            "experience_path": str(self.experience_path),
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }


def continuous_learning_from_config(app_config: Any, *, capability_graph: HIPCapabilityGraph, replay_policy: Any = None) -> ContinuousPortalLearningEngine:
    return ContinuousPortalLearningEngine(app_config, capability_graph=capability_graph, replay_policy=replay_policy)
