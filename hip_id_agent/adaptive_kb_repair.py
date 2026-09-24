from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _unique(values: Iterable[Any]) -> List[Any]:
    out: List[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None or value == "":
            continue
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _relation_family(value: Any) -> str:
    rel = _norm(value)
    if rel in {"parent_value_reveals_child", "parent_value_enables_child", "reveals_or_enables", "reveals_or_enables_child", "reveals_or_enables"}:
        return "REVEALS_OR_ENABLES"
    if rel in {"parent_value_hides_child", "parent_value_disables_child", "hides_or_disables", "hides_or_disables_child"}:
        return "HIDES_OR_DISABLES"
    if rel in {"parent_value_changes_child_options", "changes_child_options", "changes_options"}:
        return "CHANGES_OPTIONS"
    return str(value or "").upper()


def _field_key_from_node(node_id: Any) -> str:
    text = str(node_id or "")
    return _norm(text.split(".")[-1])


def _edge_signature(edge: Dict[str, Any]) -> str:
    raw = "|".join(
        [
            _field_key_from_node(edge.get("from")),
            _norm(edge.get("when_parent_value")),
            _relation_family(edge.get("relation")),
            _field_key_from_node(edge.get("to")),
        ]
    )
    return raw


def _repair_id(phase: str, repair_type: str, signature: str) -> str:
    digest = hashlib.sha256(f"{phase}|{repair_type}|{signature}".encode("utf-8", errors="ignore")).hexdigest()[:18]
    return f"kb-repair|{_norm(phase)}|{_norm(repair_type)}|{digest}"


def _judge_proof(trust: str, trust_evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "phase_trust": trust,
        "verification_status": trust_evidence.get("verification_status"),
        "section_judge_pass": bool(trust_evidence.get("judge_pass")),
        "deterministic_text_vision_gate": trust == "validated" and bool(trust_evidence.get("judge_pass")),
    }


@dataclass
class AdaptiveKBRepairPolicy:
    enabled: bool = True
    auto_apply_validated_repairs: bool = True
    min_add_confirmations: int = 1
    min_supersede_confirmations: int = 2
    preserve_original_kb: bool = True
    export_corrected_kb_after_run: bool = True
    revalidate_known_parent_branches: bool = True


class AdaptiveKBRepairEngine:
    """Self-healing layer for the reviewed HIP KB.

    Canonical knowledge is treated as a strong hypothesis, not immutable truth.
    Live observations are allowed to correct it only when the complete phase has
    passed the deterministic DOM judge, Dell AIA text judge and vision judge.
    Every change is versioned and reversible; the original KB remains untouched.
    """

    SCHEMA_VERSION = "hip.adaptive-kb-repair.v1"

    def __init__(self, brain: Any):
        self.brain = brain
        policy = getattr(brain, "policy", None)
        self.policy = AdaptiveKBRepairPolicy(
            enabled=bool(getattr(policy, "self_heal_kb", True)),
            auto_apply_validated_repairs=bool(getattr(policy, "auto_apply_validated_kb_repairs", True)),
            min_add_confirmations=max(1, int(getattr(policy, "kb_repair_min_confirmations", 1))),
            min_supersede_confirmations=max(1, int(getattr(policy, "kb_supersede_min_confirmations", 2))),
            preserve_original_kb=bool(getattr(policy, "preserve_original_kb", True)),
            export_corrected_kb_after_run=bool(getattr(policy, "export_corrected_kb_after_run", True)),
            revalidate_known_parent_branches=bool(getattr(policy, "revalidate_known_parent_branches", True)),
        )
        self.root = Path(brain.root) / "kb_repairs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "repair_index.json"
        if not self.index_path.exists():
            safe_write_json(self.index_path, {"schema_version": self.SCHEMA_VERSION, "repairs": {}, "updated_at": utc_now()})

    def _ensure_phase(self, data: Dict[str, Any]) -> None:
        data.setdefault("kb_repairs", {})
        data.setdefault("validated_field_overrides", {})
        data.setdefault("superseded_canonical_edges", {})
        data.setdefault("kb_repair_history", [])
        data.setdefault("kb_revision", 0)

    def _canonical_edges(self, data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for edge in (data.get("canonical_dependency_edges") or {}).values():
            if not isinstance(edge, dict):
                continue
            out[_edge_signature(edge)] = edge
        return out

    def _live_edges(self, knowledge_docs: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for doc in knowledge_docs:
            for edge in doc.get("dependency_edges", []) if isinstance(doc, dict) and isinstance(doc.get("dependency_edges"), list) else []:
                if not isinstance(edge, dict):
                    continue
                normalized = dict(edge)
                normalized["relation"] = _relation_family(edge.get("relation"))
                normalized["signature"] = _edge_signature(normalized)
                normalized["evidence"] = edge.get("evidence") or "live before/after DOM state delta"
                normalized["confidence"] = float(edge.get("confidence", 1.0) or 1.0)
                out[normalized["signature"]] = normalized
        return out

    def _branch_observations(self, knowledge_docs: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        out: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for doc in knowledge_docs:
            for parent in doc.get("parents", []) if isinstance(doc, dict) and isinstance(doc.get("parents"), list) else []:
                if not isinstance(parent, dict):
                    continue
                pkey = _norm(parent.get("key"))
                for branch in parent.get("branches", []) if isinstance(parent.get("branches"), list) else []:
                    if not isinstance(branch, dict) or not branch.get("observed"):
                        continue
                    value = _norm(branch.get("value"))
                    out[(pkey, value)] = branch
        return out

    def _upsert_repair(
        self,
        *,
        data: Dict[str, Any],
        phase: str,
        repair_type: str,
        signature: str,
        payload: Dict[str, Any],
        run_id: str,
        trust: str,
        trust_evidence: Dict[str, Any],
    ) -> Dict[str, Any]:
        rid = _repair_id(phase, repair_type, signature)
        repairs = data.setdefault("kb_repairs", {})
        row = repairs.setdefault(
            rid,
            {
                "repair_id": rid,
                "phase": phase,
                "repair_type": repair_type,
                "signature": signature,
                "status": "candidate",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "validated_confirmations": 0,
                "candidate_confirmations": 0,
                "failed_confirmations": 0,
                "source_runs": [],
                "evidence": [],
                "payload": {},
            },
        )
        counter = "validated_confirmations" if trust == "validated" else "candidate_confirmations" if trust == "candidate" else "failed_confirmations"
        row[counter] = int(row.get(counter, 0)) + 1
        row["source_runs"] = _unique([*row.get("source_runs", []), run_id])[-100:]
        row["payload"] = mask_sensitive_data({**(row.get("payload") or {}), **payload})
        row["evidence"] = [
            *row.get("evidence", []),
            mask_sensitive_data({"run_id": run_id, "observed_at": utc_now(), **_judge_proof(trust, trust_evidence)}),
        ][-100:]
        row["updated_at"] = utc_now()
        return row

    def reconcile_phase(
        self,
        *,
        phase: str,
        data: Dict[str, Any],
        knowledge_docs: Sequence[Dict[str, Any]],
        trust: str,
        trust_evidence: Dict[str, Any],
        run_id: str,
        knowledge_files: Sequence[str] = (),
    ) -> Dict[str, Any]:
        self._ensure_phase(data)
        if not self.policy.enabled:
            return {"status": "disabled", "phase": phase}

        canonical = self._canonical_edges(data)
        live = self._live_edges(knowledge_docs)
        branch_observations = self._branch_observations(knowledge_docs)
        created: List[str] = []
        applied: List[str] = []
        suspects: List[str] = []

        # Live-observed parent/value/child facts missing from the reviewed KB.
        # An opposite relation for the same parent/value/child is a conflict, not
        # a harmless addition; it is handled below with the stricter repeated-proof gate.
        canonical_tuples = {
            (
                _field_key_from_node(e.get("from")),
                _norm(e.get("when_parent_value")),
                _field_key_from_node(e.get("to")),
            ): e
            for e in canonical.values()
            if isinstance(e, dict)
        }
        for signature, edge in live.items():
            if edge.get("relation") not in {"REVEALS_OR_ENABLES", "CHANGES_OPTIONS", "HIDES_OR_DISABLES"}:
                continue
            if signature in canonical:
                continue
            live_tuple = (
                _field_key_from_node(edge.get("from")),
                _norm(edge.get("when_parent_value")),
                _field_key_from_node(edge.get("to")),
            )
            if live_tuple in canonical_tuples and _relation_family(canonical_tuples[live_tuple].get("relation")) != _relation_family(edge.get("relation")):
                continue
            repair = self._upsert_repair(
                data=data,
                phase=phase,
                repair_type="add_dependency_edge",
                signature=signature,
                payload={
                    "edge": edge,
                    "reason": "live judged portal state contains a dependency absent from the imported KB",
                    "knowledge_files": list(knowledge_files),
                },
                run_id=run_id,
                trust=trust,
                trust_evidence=trust_evidence,
            )
            created.append(repair["repair_id"])
            if (
                self.policy.auto_apply_validated_repairs
                and trust == "validated"
                and int(repair.get("validated_confirmations", 0)) >= self.policy.min_add_confirmations
            ):
                repair["status"] = "applied"
                repair["applied_at"] = utc_now()
                repair["authority"] = "judge-validated-live-portal-over-canonical-gap"
                applied.append(repair["repair_id"])

        # Explicit opposite live relation can supersede a wrong canonical edge.
        live_by_tuple: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for edge in live.values():
            live_by_tuple[
                (
                    _field_key_from_node(edge.get("from")),
                    _norm(edge.get("when_parent_value")),
                    _field_key_from_node(edge.get("to")),
                )
            ] = edge
        for signature, edge in canonical.items():
            tup = (
                _field_key_from_node(edge.get("from")),
                _norm(edge.get("when_parent_value")),
                _field_key_from_node(edge.get("to")),
            )
            opposite = live_by_tuple.get(tup)
            if not opposite:
                continue
            if _relation_family(edge.get("relation")) == _relation_family(opposite.get("relation")):
                continue
            # Only explicit, successfully explored parent branches may challenge canonical truth.
            if (tup[0], tup[1]) not in branch_observations:
                continue
            repair = self._upsert_repair(
                data=data,
                phase=phase,
                repair_type="supersede_canonical_dependency",
                signature=signature,
                payload={
                    "canonical_edge": edge,
                    "observed_edge": opposite,
                    "reason": "live judged portal state explicitly observed an opposite parent-child relation",
                    "knowledge_files": list(knowledge_files),
                },
                run_id=run_id,
                trust=trust,
                trust_evidence=trust_evidence,
            )
            suspects.append(repair["repair_id"])
            if (
                self.policy.auto_apply_validated_repairs
                and trust == "validated"
                and int(repair.get("validated_confirmations", 0)) >= self.policy.min_supersede_confirmations
            ):
                repair["status"] = "applied"
                repair["applied_at"] = utc_now()
                repair["authority"] = "repeated-judge-validated-live-portal-over-canonical-conflict"
                data["superseded_canonical_edges"][signature] = {
                    "signature": signature,
                    "repair_id": repair["repair_id"],
                    "superseded_at": utc_now(),
                    "reason": repair["payload"].get("reason"),
                }
                applied.append(repair["repair_id"])
            elif repair.get("status") != "applied":
                repair["status"] = "suspect"

        # Add current live labels, roles and options as non-destructive field corrections.
        canonical_nodes = data.get("canonical_field_nodes") or {}
        for doc in knowledge_docs:
            for control in doc.get("control_registry", []) if isinstance(doc, dict) and isinstance(doc.get("control_registry"), list) else []:
                if not isinstance(control, dict):
                    continue
                key = _norm(control.get("key"))
                section = _norm(control.get("section") or doc.get("section") or phase)
                matches = [
                    node for node in canonical_nodes.values()
                    if isinstance(node, dict)
                    and _norm(node.get("key")) == key
                    and (not section or _norm(node.get("section")) == section or section in _norm(node.get("section")) or _norm(node.get("section")) in section)
                ]
                if not matches:
                    continue
                for node in matches:
                    labels = _unique([control.get("label")])
                    roles = _unique([control.get("role") or control.get("type")])
                    options = _unique(control.get("options") or [])
                    new_labels = [x for x in labels if x not in (node.get("labels") or [])]
                    new_roles = [x for x in roles if x not in (node.get("roles") or [])]
                    new_options = [x for x in options if x not in (node.get("options") or [])]
                    if not (new_labels or new_roles or new_options):
                        continue
                    fsig = f"{node.get('node_id')}|{json.dumps([new_labels, new_roles, new_options], sort_keys=True, default=str)}"
                    repair = self._upsert_repair(
                        data=data,
                        phase=phase,
                        repair_type="extend_field_semantics",
                        signature=fsig,
                        payload={
                            "node_id": node.get("node_id"),
                            "labels": new_labels,
                            "roles": new_roles,
                            "options": new_options,
                            "reason": "judge-validated live portal control semantics differ from or extend the reviewed KB",
                            "knowledge_files": list(knowledge_files),
                        },
                        run_id=run_id,
                        trust=trust,
                        trust_evidence=trust_evidence,
                    )
                    created.append(repair["repair_id"])
                    if (
                        self.policy.auto_apply_validated_repairs
                        and trust == "validated"
                        and int(repair.get("validated_confirmations", 0)) >= self.policy.min_add_confirmations
                    ):
                        repair["status"] = "applied"
                        repair["applied_at"] = utc_now()
                        override = data["validated_field_overrides"].setdefault(
                            str(node.get("node_id")),
                            {"node_id": node.get("node_id"), "labels": [], "roles": [], "options": [], "repair_ids": [], "updated_at": utc_now()},
                        )
                        override["labels"] = _unique([*override.get("labels", []), *new_labels])
                        override["roles"] = _unique([*override.get("roles", []), *new_roles])
                        override["options"] = _unique([*override.get("options", []), *new_options])
                        override["repair_ids"] = _unique([*override.get("repair_ids", []), repair["repair_id"]])
                        override["updated_at"] = utc_now()
                        applied.append(repair["repair_id"])

        if applied:
            data["kb_revision"] = int(data.get("kb_revision", 0)) + 1
        history = {
            "run_id": run_id,
            "phase": phase,
            "trust": trust,
            "trust_evidence": trust_evidence,
            "created_repairs": _unique(created),
            "applied_repairs": _unique(applied),
            "suspect_repairs": _unique(suspects),
            "kb_revision": data.get("kb_revision", 0),
            "reconciled_at": utc_now(),
        }
        data["kb_repair_history"] = [*data.get("kb_repair_history", []), history][-200:]
        self._write_global_index(phase, data)
        return {"status": "ok", **history}

    def effective_edges(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        self._ensure_phase(data)
        superseded = set((data.get("superseded_canonical_edges") or {}).keys())
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for edge in (data.get("canonical_dependency_edges") or {}).values():
            if not isinstance(edge, dict):
                continue
            signature = _edge_signature(edge)
            if signature in superseded:
                continue
            if signature not in seen:
                seen.add(signature)
                out.append(edge)
        for edge in (data.get("dependency_edges") or {}).values():
            if not isinstance(edge, dict):
                continue
            signature = _edge_signature(edge)
            if signature not in seen:
                seen.add(signature)
                out.append(edge)
        for repair in (data.get("kb_repairs") or {}).values():
            if not isinstance(repair, dict) or repair.get("status") != "applied":
                continue
            edge = None
            if repair.get("repair_type") == "add_dependency_edge":
                edge = (repair.get("payload") or {}).get("edge")
            elif repair.get("repair_type") == "supersede_canonical_dependency":
                edge = (repair.get("payload") or {}).get("observed_edge")
            if not isinstance(edge, dict):
                continue
            signature = _edge_signature(edge)
            if signature not in seen:
                seen.add(signature)
                out.append({**edge, "trust_class": "live-validated-correction", "repair_id": repair.get("repair_id"), "memory_confidence": 1.0, "validated_count": repair.get("validated_confirmations", 1)})
        return out

    def apply_field_overrides(self, data: Dict[str, Any], nodes: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        for node_id, override in (data.get("validated_field_overrides") or {}).items():
            if node_id not in nodes or not isinstance(override, dict):
                continue
            node = nodes[node_id]
            node["labels"] = _unique([*node.get("labels", []), *override.get("labels", [])])
            node["roles"] = _unique([*node.get("roles", []), *override.get("roles", [])])
            node["options"] = _unique([*node.get("options", []), *override.get("options", [])])
            node["adaptive_kb_repair_ids"] = _unique([*node.get("adaptive_kb_repair_ids", []), *override.get("repair_ids", [])])
        return nodes

    def _write_global_index(self, phase: str, data: Dict[str, Any]) -> None:
        index = _read_json(self.index_path, {"schema_version": self.SCHEMA_VERSION, "repairs": {}})
        index.setdefault("phases", {})[phase] = {
            "kb_revision": data.get("kb_revision", 0),
            "repair_count": len(data.get("kb_repairs") or {}),
            "applied_count": sum(1 for r in (data.get("kb_repairs") or {}).values() if isinstance(r, dict) and r.get("status") == "applied"),
            "suspect_count": sum(1 for r in (data.get("kb_repairs") or {}).values() if isinstance(r, dict) and r.get("status") == "suspect"),
            "phase_file": str(self.brain._phase_path(phase)),
            "updated_at": utc_now(),
        }
        index["updated_at"] = utc_now()
        safe_write_json(self.index_path, index)

    def status(self) -> Dict[str, Any]:
        index = _read_json(self.index_path, {"schema_version": self.SCHEMA_VERSION, "phases": {}})
        return {"schema_version": self.SCHEMA_VERSION, "repair_dir": str(self.root), "policy": self.policy.__dict__, **index}

    def export_corrected_kb(self, output_dir: str | Path | None = None) -> Dict[str, Any]:
        output = Path(output_dir) if output_dir else self.root / "exports"
        output.mkdir(parents=True, exist_ok=True)
        kb_path = Path(getattr(self.brain.policy, "unified_kb_path", ""))
        kg_path = Path(getattr(self.brain.policy, "unified_kg_path", ""))
        kb = _read_json(kb_path, {}) if kb_path.is_file() else {}
        graph = _read_json(kg_path, {}) if kg_path.is_file() else {}
        corrected_kb = copy.deepcopy(kb)
        corrected_graph = copy.deepcopy(graph)
        all_repairs: List[Dict[str, Any]] = []
        superseded: List[Dict[str, Any]] = []
        for phase_file in sorted(Path(self.brain.phase_dir).glob("*.json")):
            data = _read_json(phase_file, {})
            for repair in (data.get("kb_repairs") or {}).values():
                if isinstance(repair, dict) and repair.get("status") == "applied":
                    all_repairs.append(repair)
            superseded.extend(list((data.get("superseded_canonical_edges") or {}).values()))
        corrected_kb.setdefault("adaptive_live_corrections", {})
        corrected_kb["adaptive_live_corrections"] = {
            "schema_version": self.SCHEMA_VERSION,
            "generated_at": utc_now(),
            "source_kb": str(kb_path),
            "source_kb_preserved": True,
            "authority": "only deterministic DOM + Dell AIA text + Dell AIA vision judged live observations are applied",
            "applied_repairs": mask_sensitive_data(all_repairs),
            "superseded_canonical_facts": mask_sensitive_data(superseded),
        }
        corrected_kb.setdefault("metadata", {})["adaptive_revision_generated_at"] = utc_now()
        corrected_kb["metadata"]["adaptive_repair_count"] = len(all_repairs)

        nodes = corrected_graph.setdefault("nodes", []) if isinstance(corrected_graph, dict) else []
        edges = corrected_graph.setdefault("edges", []) if isinstance(corrected_graph, dict) else []
        existing_edge_sigs = set()
        for edge in edges if isinstance(edges, list) else []:
            if isinstance(edge, dict):
                existing_edge_sigs.add(_edge_signature(edge))
        for repair in all_repairs:
            if repair.get("repair_type") != "add_dependency_edge":
                continue
            edge = (repair.get("payload") or {}).get("edge")
            if not isinstance(edge, dict) or _edge_signature(edge) in existing_edge_sigs:
                continue
            edge_id = f"adaptive:{repair.get('repair_id')}"
            edges.append({
                "id": edge_id,
                "from": edge.get("from"),
                "to": edge.get("to"),
                "relation": "REVEALS_OR_ENABLES" if edge.get("relation") == "REVEALS_OR_ENABLES" else edge.get("relation"),
                "when_parent_value": edge.get("when_parent_value"),
                "source": "judge-validated-live-portal-repair",
                "repair_id": repair.get("repair_id"),
                "status": "active",
            })
            existing_edge_sigs.add(_edge_signature(edge))
        corrected_graph["adaptive_revision"] = {
            "generated_at": utc_now(),
            "repair_count": len(all_repairs),
            "source_graph": str(kg_path),
        }

        kb_out = output / "HIP_Unified_Deep_KB.self_healed.json"
        kg_out = output / "HIP_Unified_Knowledge_Graph.self_healed.json"
        safe_write_json(kb_out, corrected_kb)
        safe_write_json(kg_out, corrected_graph)
        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "generated_at": utc_now(),
            "original_kb_preserved": True,
            "corrected_kb": str(kb_out),
            "corrected_graph": str(kg_out),
            "applied_repair_count": len(all_repairs),
            "superseded_fact_count": len(superseded),
            "repair_index": str(self.index_path),
        }
        manifest_path = output / "self_healed_kb_manifest.json"
        safe_write_json(manifest_path, manifest)
        manifest["manifest"] = str(manifest_path)
        return manifest
