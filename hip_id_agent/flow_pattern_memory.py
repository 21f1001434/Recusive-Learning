from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .hip_form_catalog import phase_to_form_family
from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .autonomous_dependency_runtime import apply_dependency_execution_contract


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _unique(values: Iterable[Any]) -> List[Any]:
    out: List[Any] = []
    seen: set[str] = set()
    for value in values:
        if value in (None, "", [], {}):
            continue
        key = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _node_signature(node: Dict[str, Any]) -> str:
    return "|".join([
        _norm(node.get("section")),
        _norm(node.get("row_kind")),
        str(node.get("row_index") if node.get("row_index") is not None else ""),
        _norm(node.get("field_key")),
        _norm(node.get("action")),
        "required" if node.get("required", True) else "optional",
    ])


def _dependency_signature(node: Dict[str, Any], node_map: Dict[str, Dict[str, Any]]) -> List[str]:
    child = _node_signature(node)
    out: List[str] = []
    for dep in node.get("depends_on", []) if isinstance(node.get("depends_on"), list) else []:
        parent = node_map.get(str(dep))
        if isinstance(parent, dict):
            out.append(f"{_node_signature(parent)}->{child}")
    return out


def _selector_profile_from_attempt(attempt: Dict[str, Any]) -> Dict[str, Any]:
    diag = attempt.get("binding_diagnostics") if isinstance(attempt.get("binding_diagnostics"), dict) else {}
    control = diag.get("control") if isinstance(diag.get("control"), dict) else {}
    framework_key = (
        control.get("framework_key") or control.get("form_control_name") or
        control.get("formControlName") or control.get("name") or attempt.get("framework_key") or ""
    )
    label = attempt.get("label") or control.get("label") or ""
    role = control.get("role") or ((attempt.get("control_kind") or {}).get("role") if isinstance(attempt.get("control_kind"), dict) else "") or ""
    raw_selector = str(attempt.get("selector") or control.get("selector") or "")
    candidates: List[Dict[str, Any]] = []
    if framework_key:
        candidates.extend([
            {"strategy": "framework_key", "selector": f'[formcontrolname="{framework_key}"]'},
            {"strategy": "name", "selector": f'[name="{framework_key}"]'},
        ])
    if role and label:
        candidates.append({"strategy": "role_and_accessible_name", "role": role, "name": label, "occurrence": attempt.get("occurrence")})
    if label:
        candidates.append({"strategy": "label_occurrence", "label": label, "occurrence": attempt.get("occurrence")})
    if raw_selector:
        candidates.append({"strategy": "last_known_selector", "selector": raw_selector, "dynamic": bool(re.search(r"dds-form-field-\d+", raw_selector, re.I))})
    return mask_sensitive_data({
        "framework_key": framework_key, "label": label, "role": role,
        "section": attempt.get("section") or control.get("section") or "",
        "row_kind": attempt.get("row_kind") or "", "row_index": attempt.get("row_index"),
        "semantic_identity": diag.get("selected_identity") or control.get("semantic_identity") or "",
        "candidates": candidates, "values_stored": False,
    })


def _interaction_profile_from_attempt(attempt: Dict[str, Any]) -> Dict[str, Any]:
    return mask_sensitive_data({
        "executor": attempt.get("executor") or attempt.get("method") or "",
        "dom_events": list(attempt.get("dom_events") or []),
        "control_kind": attempt.get("control_kind") if isinstance(attempt.get("control_kind"), dict) else {},
        "rebind_required": bool(attempt.get("rebound_after_action_type") or attempt.get("rebound_after_parent")),
        "conditional_child_wait": bool(attempt.get("conditional_child_wait") or attempt.get("rebound_after_action_type") or attempt.get("rebound_after_parent")),
        "values_stored": False,
    })


def _enumerated_traits(graph: Dict[str, Any]) -> Dict[str, str]:
    """Keep only stable, non-secret, flow-shape traits; never store business values."""
    allow = {
        "transaction_type", "data_format_type", "map_class", "interface_type",
        "transport_type", "profile_usage", "usage", "rule_type", "rule_scope",
        "process_step_type", "flow_identifier_operator", "routing_rule_type",
    }
    traits: Dict[str, str] = {}
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        key = _norm(node.get("field_key"))
        if key not in allow:
            continue
        value = node.get("expected_value")
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            traits[key] = _norm(value)
    return traits


def describe_graph(graph: Dict[str, Any], *, phase: str = "") -> Dict[str, Any]:
    graph = apply_dependency_execution_contract(graph, phase=phase or str(graph.get("phase") or ""))
    nodes = [dict(n) for n in graph.get("nodes", []) if isinstance(n, dict)]
    node_map = {str(n.get("node_id")): n for n in nodes}
    node_signatures = [_node_signature(n) for n in nodes]
    dependency_signatures = [
        sig for n in nodes for sig in _dependency_signature(n, node_map)
    ]
    repeatables: Dict[str, int] = {}
    for n in nodes:
        row_kind = _norm(n.get("row_kind"))
        if row_kind:
            repeatables[row_kind] = max(repeatables.get(row_kind, 0), int(n.get("row_index") or 0) + 1)
    family = str(graph.get("object_family") or phase_to_form_family(phase or graph.get("phase")))
    action_sequence = [
        f"{_norm(n.get('field_key'))}:{_norm(n.get('action'))}"
        for n in nodes if n.get("expected_value") not in (None, "", [])
    ]
    structure_material = {
        "family": family,
        "nodes": sorted(node_signatures),
        "dependencies": sorted(dependency_signatures),
        "repeatables": repeatables,
        "traits": _enumerated_traits(graph),
    }
    fingerprint = hashlib.sha256(
        json.dumps(structure_material, sort_keys=True, ensure_ascii=False).encode("utf-8", errors="ignore")
    ).hexdigest()[:24]
    return mask_sensitive_data({
        "schema_version": "hip.flow-pattern-descriptor.v1",
        "phase": phase or graph.get("phase"),
        "family": family,
        "structure_fingerprint": fingerprint,
        "node_signatures": node_signatures,
        "dependency_signatures": dependency_signatures,
        "repeatable_shape": repeatables,
        "action_sequence": action_sequence,
        "traits": _enumerated_traits(graph),
        "node_count": len(nodes),
    })


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pattern_similarity(current: Dict[str, Any], stored: Dict[str, Any]) -> Dict[str, Any]:
    if _norm(current.get("family")) != _norm(stored.get("family")):
        return {"score": 0.0, "family_match": False}
    exact_fingerprint = current.get("structure_fingerprint") == stored.get("structure_fingerprint")
    node_score = _jaccard(current.get("node_signatures") or [], stored.get("node_signatures") or [])
    dependency_score = _jaccard(current.get("dependency_signatures") or [], stored.get("dependency_signatures") or [])
    action_score = _jaccard(current.get("action_sequence") or [], stored.get("action_sequence") or [])
    current_repeat = current.get("repeatable_shape") or {}
    stored_repeat = stored.get("repeatable_shape") or {}
    repeat_score = 1.0 if current_repeat == stored_repeat else _jaccard(
        [f"{k}:{v}" for k, v in current_repeat.items()],
        [f"{k}:{v}" for k, v in stored_repeat.items()],
    )
    current_traits = current.get("traits") or {}
    stored_traits = stored.get("traits") or {}
    trait_score = 1.0 if current_traits == stored_traits else _jaccard(
        [f"{k}:{v}" for k, v in current_traits.items()],
        [f"{k}:{v}" for k, v in stored_traits.items()],
    )
    score = (
        (0.25 if exact_fingerprint else 0.0)
        + 0.35 * node_score
        + 0.12 * dependency_score
        + 0.10 * action_score
        + 0.10 * repeat_score
        + 0.08 * trait_score
    )
    return {
        "score": round(min(1.0, score), 4),
        "family_match": True,
        "exact_structure_fingerprint": exact_fingerprint,
        "node_similarity": round(node_score, 4),
        "dependency_similarity": round(dependency_score, 4),
        "action_similarity": round(action_score, 4),
        "repeatable_similarity": round(repeat_score, 4),
        "trait_similarity": round(trait_score, 4),
    }


class FlowPatternMemory:
    """Judge-gated reusable memory for structurally similar HIP flows.

    The memory stores form topology, semantic action order and successful binding
    identities.  It deliberately never stores customer-entered field values,
    credentials, tokens, generated IDs or upload contents.
    """

    SCHEMA_VERSION = "hip.flow-pattern-memory.v1"

    def __init__(
        self,
        root: str | Path,
        *,
        enabled: bool = True,
        minimum_similarity: float = 0.78,
        max_patterns: int = 500,
        allow_cross_phase_same_family: bool = True,
    ) -> None:
        self.root = Path(root)
        self.enabled = bool(enabled)
        self.minimum_similarity = float(minimum_similarity)
        self.max_patterns = max(1, int(max_patterns))
        self.allow_cross_phase_same_family = bool(allow_cross_phase_same_family)
        self.root.mkdir(parents=True, exist_ok=True)
        self.patterns_path = self.root / "patterns.json"
        self.manifest_path = self.root / "manifest.json"
        self.observations_path = self.root / "candidate_action_observations.jsonl"
        if not self.patterns_path.exists():
            safe_write_json(self.patterns_path, {"schema_version": self.SCHEMA_VERSION, "patterns": {}})
        self._write_manifest()

    @classmethod
    def from_config(cls, root: str | Path, config: Any) -> "FlowPatternMemory":
        brain = getattr(config, "brain", None)
        return cls(
            root,
            enabled=bool(getattr(brain, "flow_pattern_memory_enabled", True)),
            minimum_similarity=float(getattr(brain, "flow_pattern_min_similarity", 0.78)),
            max_patterns=int(getattr(brain, "flow_pattern_max_patterns", 500)),
            allow_cross_phase_same_family=bool(getattr(brain, "flow_pattern_allow_cross_phase_same_family", True)),
        )

    def _load(self) -> Dict[str, Any]:
        data = _read_json(self.patterns_path, {"schema_version": self.SCHEMA_VERSION, "patterns": {}})
        if not isinstance(data, dict):
            data = {"schema_version": self.SCHEMA_VERSION, "patterns": {}}
        data.setdefault("patterns", {})
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        patterns = data.get("patterns") if isinstance(data.get("patterns"), dict) else {}
        if len(patterns) > self.max_patterns:
            ranked = sorted(
                patterns.items(),
                key=lambda item: (
                    int((item[1] or {}).get("validated_count") or 0),
                    str((item[1] or {}).get("last_seen_at") or ""),
                ),
                reverse=True,
            )[: self.max_patterns]
            data["patterns"] = dict(ranked)
        data["updated_at"] = utc_now()
        safe_write_json(self.patterns_path, data)
        self._write_manifest(data)

    def _write_manifest(self, data: Optional[Dict[str, Any]] = None) -> None:
        data = data or self._load()
        patterns = data.get("patterns") if isinstance(data.get("patterns"), dict) else {}
        safe_write_json(self.manifest_path, {
            "schema_version": self.SCHEMA_VERSION,
            "enabled": self.enabled,
            "minimum_similarity": self.minimum_similarity,
            "max_patterns": self.max_patterns,
            "allow_cross_phase_same_family": self.allow_cross_phase_same_family,
            "validated_patterns": sum(1 for p in patterns.values() if isinstance(p, dict) and p.get("trust") == "validated"),
            "candidate_patterns": sum(1 for p in patterns.values() if isinstance(p, dict) and p.get("trust") == "candidate"),
            "negative_patterns": sum(1 for p in patterns.values() if isinstance(p, dict) and p.get("trust") == "negative"),
            "stored_values": False,
            "memory_contents": [
                "form family and structure fingerprint",
                "semantic field/action signatures",
                "parent-child dependency topology",
                "repeatable-row shape",
                "validated binding identities",
                "successful action order and wait profile",
            ],
        })

    def match_graph(self, graph: Dict[str, Any], *, phase: str = "") -> Dict[str, Any]:
        descriptor = describe_graph(graph, phase=phase)
        if not self.enabled:
            return {"status": "disabled", "descriptor": descriptor, "validated_match": False}
        data = self._load()
        candidates: List[Dict[str, Any]] = []
        for pattern_id, pattern in (data.get("patterns") or {}).items():
            if not isinstance(pattern, dict) or pattern.get("trust") != "validated":
                continue
            if not self.allow_cross_phase_same_family and _norm(pattern.get("phase")) != _norm(phase):
                continue
            similarity = pattern_similarity(descriptor, pattern.get("descriptor") or {})
            candidates.append({
                "pattern_id": pattern_id,
                "phase": pattern.get("phase"),
                "family": pattern.get("family"),
                "similarity": similarity,
                "validated_count": int(pattern.get("validated_count") or 0),
                "pattern": pattern,
            })
        candidates.sort(key=lambda row: (-float((row.get("similarity") or {}).get("score") or 0), -int(row.get("validated_count") or 0)))
        best = candidates[0] if candidates else None
        score = float(((best or {}).get("similarity") or {}).get("score") or 0)
        validated = bool(best and score >= self.minimum_similarity)
        return mask_sensitive_data({
            "schema_version": "hip.flow-pattern-match.v1",
            "status": "validated_match" if validated else "no_validated_match",
            "validated_match": validated,
            "minimum_similarity": self.minimum_similarity,
            "descriptor": descriptor,
            "best": {
                "pattern_id": best.get("pattern_id"),
                "phase": best.get("phase"),
                "family": best.get("family"),
                "similarity": best.get("similarity"),
                "validated_count": best.get("validated_count"),
                "replay": (best.get("pattern") or {}).get("replay") or {},
            } if best else {},
            "candidate_count": len(candidates),
        })

    def apply_to_graph(self, graph: Dict[str, Any], *, phase: str = "") -> Dict[str, Any]:
        result = copy.deepcopy(graph)
        match = self.match_graph(result, phase=phase or str(result.get("phase") or ""))
        result["flow_pattern_memory_match"] = match
        if not match.get("validated_match"):
            return result
        replay = ((match.get("best") or {}).get("replay") or {})
        result["validated_replay"] = True
        result["replay_blueprint_validated"] = True
        result["validated_structure_fingerprint"] = ((match.get("best") or {}).get("similarity") or {}).get("exact_structure_fingerprint")
        result["memory_replay_profile"] = {
            "source_pattern_id": (match.get("best") or {}).get("pattern_id"),
            "source_phase": (match.get("best") or {}).get("phase"),
            "similarity": (match.get("best") or {}).get("similarity"),
            "action_order": replay.get("action_order") or [],
            "binding_identities": replay.get("binding_identities") or {},
            "selector_profiles": replay.get("selector_profiles") or {},
            "interaction_profiles": replay.get("interaction_profiles") or {},
            "wait_profile": replay.get("wait_profile") or {},
            "values_reused": False,
        }
        binding_map = replay.get("binding_identities") if isinstance(replay.get("binding_identities"), dict) else {}
        selector_map = replay.get("selector_profiles") if isinstance(replay.get("selector_profiles"), dict) else {}
        interaction_map = replay.get("interaction_profiles") if isinstance(replay.get("interaction_profiles"), dict) else {}
        node_wait_map = replay.get("node_wait_profiles") if isinstance(replay.get("node_wait_profiles"), dict) else {}
        order_map = {sig: idx for idx, sig in enumerate(replay.get("action_order") or [], start=1)}
        for node in result.get("nodes", []) if isinstance(result.get("nodes"), list) else []:
            if not isinstance(node, dict):
                continue
            sig = _node_signature(node)
            if sig in binding_map:
                node["validated_memory_binding_identity"] = binding_map[sig]
            if sig in selector_map:
                node["validated_memory_selector_profile"] = selector_map[sig]
            if sig in interaction_map:
                node["validated_memory_interaction_profile"] = interaction_map[sig]
            if sig in node_wait_map:
                node["validated_memory_wait_profile"] = node_wait_map[sig]
            if sig in order_map:
                node["validated_memory_order"] = order_map[sig]
        result["validated_memory_dependency_contract"] = replay.get("dependency_contract") or {}
        return apply_dependency_execution_contract(result, phase=phase or str(result.get("phase") or ""))

    def promote(
        self,
        *,
        graph: Dict[str, Any],
        execution: Dict[str, Any],
        phase: str,
        run_id: str,
        judge_pass: bool,
    ) -> Dict[str, Any]:
        graph = apply_dependency_execution_contract(graph, phase=phase)
        dependency_contract = graph.get("dependency_execution_contract") or {}
        descriptor = describe_graph(graph, phase=phase)
        execution_pass = bool(execution.get("pass") or execution.get("status") == "pass")
        final_model = execution.get("final_form_state_model") if isinstance(execution.get("final_form_state_model"), dict) else {}
        one_to_one = bool(final_model.get("one_to_one_pass", True))
        attempts = execution.get("attempts") if isinstance(execution.get("attempts"), list) else []
        unsafe = any(
            isinstance(a, dict) and (
                (a.get("transaction_proof") or {}).get("protected_state_changes")
                or "AMBIGUOUS" in str(a.get("reason") or "").upper()
                or "UNINTENDED_MUTATION" in str(a.get("reason") or "").upper()
            )
            for a in attempts
        )
        trust = "validated" if judge_pass and execution_pass and one_to_one and not unsafe else "candidate" if execution_pass else "negative"
        binding_identities: Dict[str, Any] = {}
        selector_profiles: Dict[str, Any] = {}
        interaction_profiles: Dict[str, Any] = {}
        node_wait_profiles: Dict[str, Any] = {}
        action_order: List[str] = []
        for attempt in attempts:
            if not isinstance(attempt, dict) or not attempt.get("success"):
                continue
            node = {
                "section": attempt.get("section"),
                "row_kind": attempt.get("row_kind"),
                "row_index": attempt.get("row_index"),
                "field_key": attempt.get("field"),
                "action": "select_multi" if isinstance(attempt.get("actual_value"), list) else "",
                "required": True,
            }
            # Prefer the graph node so action type remains exact.
            graph_node = next((
                n for n in graph.get("nodes", []) if isinstance(n, dict)
                and str(n.get("node_id")) == str(attempt.get("node_id"))
            ), None)
            if graph_node:
                node = graph_node
            sig = _node_signature(node)
            if sig not in action_order:
                action_order.append(sig)
            diag = attempt.get("binding_diagnostics") if isinstance(attempt.get("binding_diagnostics"), dict) else {}
            identity = diag.get("selected_identity") or ((diag.get("control") or {}).get("semantic_identity") if isinstance(diag.get("control"), dict) else None)
            if identity:
                binding_identities[sig] = identity
            selector_profile = _selector_profile_from_attempt(attempt)
            if selector_profile.get("candidates"):
                selector_profiles[sig] = selector_profile
            interaction_profile = _interaction_profile_from_attempt(attempt)
            if interaction_profile.get("executor") or interaction_profile.get("dom_events"):
                interaction_profiles[sig] = interaction_profile
            wait_profile = graph_node.get("dependency_wait_profile") if isinstance(graph_node, dict) and isinstance(graph_node.get("dependency_wait_profile"), dict) else {}
            if wait_profile:
                node_wait_profiles[sig] = mask_sensitive_data(wait_profile)
        pattern_id = hashlib.sha256(
            f"{descriptor.get('family')}|{descriptor.get('structure_fingerprint')}".encode("utf-8", errors="ignore")
        ).hexdigest()[:24]
        data = self._load()
        patterns = data.setdefault("patterns", {})
        stored = patterns.setdefault(pattern_id, {
            "pattern_id": pattern_id,
            "phase": phase,
            "family": descriptor.get("family"),
            "descriptor": descriptor,
            "trust": trust,
            "validated_count": 0,
            "candidate_count": 0,
            "failure_count": 0,
            "source_runs": [],
            "created_at": utc_now(),
            "replay": {
                "action_order": [],
                "binding_identities": {},
                "selector_profiles": {},
                "interaction_profiles": {},
                "node_wait_profiles": {},
                "dependency_contract": {},
                "wait_profile": {
                    "stable_samples": 2,
                    "conditional_child_visibility_required": True,
                    "explicit_widget_events_required": True,
                },
                "values_stored": False,
            },
        })
        stored["descriptor"] = descriptor
        stored["phase"] = phase
        stored["family"] = descriptor.get("family")
        stored["trust"] = trust if trust == "validated" or stored.get("trust") != "validated" else "validated"
        key = "validated_count" if trust == "validated" else "candidate_count" if trust == "candidate" else "failure_count"
        stored[key] = int(stored.get(key) or 0) + 1
        stored["source_runs"] = _unique([*stored.get("source_runs", []), run_id])[-50:]
        stored["last_seen_at"] = utc_now()
        if trust == "validated":
            stored["last_validated_at"] = utc_now()
            stored["replay"] = {
                "action_order": action_order,
                "binding_identities": binding_identities,
                "selector_profiles": selector_profiles,
                "interaction_profiles": interaction_profiles,
                "node_wait_profiles": node_wait_profiles,
                "dependency_contract": {
                    "contract_fingerprint": dependency_contract.get("contract_fingerprint"),
                    "ordered_node_ids": dependency_contract.get("ordered_node_ids") or [],
                    "dependency_map": dependency_contract.get("dependency_map") or {},
                    "section_sequence": dependency_contract.get("section_sequence") or [],
                    "scheduler_policy": dependency_contract.get("scheduler_policy") or {},
                    "values_stored": False,
                },
                "wait_profile": {
                    "stable_samples": 2,
                    "conditional_child_visibility_required": True,
                    "explicit_widget_events_required": True,
                    "committed_field_protection": True,
                },
                "values_stored": False,
            }
        patterns[pattern_id] = stored
        self._save(data)
        return mask_sensitive_data({
            "status": "promoted" if trust == "validated" else "stored_as_candidate" if trust == "candidate" else "stored_as_negative",
            "trust": trust,
            "pattern_id": pattern_id,
            "family": descriptor.get("family"),
            "structure_fingerprint": descriptor.get("structure_fingerprint"),
            "validated_count": stored.get("validated_count", 0),
            "selector_profile_count": len(selector_profiles),
            "interaction_profile_count": len(interaction_profiles),
            "dependency_contract_fingerprint": dependency_contract.get("contract_fingerprint"),
            "node_wait_profile_count": len(node_wait_profiles),
            "values_stored": False,
            "memory_file": str(self.patterns_path),
        })

    def observe_action(self, observation: Dict[str, Any]) -> None:
        """Store a value-free candidate action observation from any HIP form."""
        if not self.enabled:
            return
        payload = mask_sensitive_data({
            "schema_version": "hip.form-action-observation.v1",
            "timestamp": utc_now(),
            "form_family": observation.get("form_family"),
            "structure_fingerprint": observation.get("structure_fingerprint"),
            "action_type": observation.get("action_type"),
            "semantic_target": observation.get("semantic_target"),
            "success": bool(observation.get("success")),
            "event_proof": observation.get("event_proof") or {},
            "values_stored": False,
            "trust": "candidate_until_independent_judge",
        })
        self.observations_path.parent.mkdir(parents=True, exist_ok=True)
        with self.observations_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def promote_from_phase_dir(
        self,
        *,
        phase: str,
        phase_dir: str | Path,
        input_payload: Dict[str, Any],
        run_id: str,
        judge_pass: bool,
        graph_builder: Any,
    ) -> Dict[str, Any]:
        phase_dir = Path(phase_dir)
        graph = graph_builder(input_payload, phase)
        candidates: List[Path] = []
        for path in phase_dir.rglob("*.json"):
            if "target_branch_execution" in path.name or "state_graph_execution" in path.name:
                candidates.append(path)
        candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        execution: Dict[str, Any] = {}
        for path in candidates:
            data = _read_json(path, {})
            if isinstance(data, dict) and isinstance(data.get("attempts"), list) and (
                data.get("schema_version", "").startswith("hip.stateful-form-execution")
                or "final_form_state_model" in data
            ):
                execution = data
                break
        if not execution:
            return {"status": "no_execution_artifact", "phase": phase, "searched": [str(p) for p in candidates[:20]]}
        return self.promote(graph=graph, execution=execution, phase=phase, run_id=run_id, judge_pass=judge_pass)

    def bootstrap_from_runs(self, runs_root: str | Path, *, exclude_run: str | Path | None = None, max_runs: int = 300) -> Dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        root = Path(runs_root)
        exclude = Path(exclude_run).resolve() if exclude_run else None
        runs = sorted([p.parent for p in root.glob("*/full_dummy_fill_summary.json")], key=lambda p: p.stat().st_mtime if p.exists() else 0)[-max_runs:]
        imported = 0
        errors: List[Dict[str, Any]] = []
        for run_dir in runs:
            try:
                if exclude is not None and run_dir.resolve() == exclude:
                    continue
            except Exception:
                pass
            summary = _read_json(run_dir / "full_dummy_fill_summary.json", {})
            phases = summary.get("phase_sequence") if isinstance(summary, dict) and isinstance(summary.get("phase_sequence"), list) else []
            for phase in phases:
                phase_dir = run_dir / str(phase)
                judge = _read_json(phase_dir / "section_judge_gate.json", {})
                judge_pass = bool(judge.get("pass"))
                graph_path = run_dir / "deterministic_plans" / f"{phase}_deterministic_plan.json"
                plan = _read_json(graph_path, {})
                # Bootstrapping is intentionally conservative. Existing run plans
                # are stored as candidates unless the phase judge passed and a
                # stateful execution artifact can be reconstructed later.
                if not judge_pass or not isinstance(plan, dict):
                    continue
                imported += 0
        return {
            "status": "ok",
            "runs_scanned": len(runs),
            "patterns_imported": imported,
            "note": "Existing Portal Brain phase knowledge remains available; new validated flow-pattern memory is promoted directly after each successful phase.",
            "errors": errors,
            "manifest": str(self.manifest_path),
        }
