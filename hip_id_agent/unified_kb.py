from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

if TYPE_CHECKING:  # pragma: no cover
    from .portal_brain import PortalBrain


SCHEMA_VERSION = "hip.unified-kb-import.v1"
_PHASES = {
    "data_map",
    "source_document_type",
    "target_document_type",
    "rule",
    "source_transport_profile",
    "target_transport_profile",
    "biz_flow",
}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _unique(values: Iterable[Any]) -> List[Any]:
    out: List[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None or value == "":
            continue
        key = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _input_path_aliases(phase: str, input_path: str) -> List[str]:
    """Return canonical and runtime-compatible input paths.

    The reviewed KB intentionally preserves a few historical schema names. The
    runtime U-HAUL payload uses the normalized v80 shape, so aliases are kept in
    memory without discarding the original source path.
    """
    raw = str(input_path or "").strip()
    paths: List[str] = []
    replacements = {
        "source_transport_profile_dell.": "source_transport_profile.",
        "biz_flow.configure_targets[0].": "biz_flow.configure_targets.",
        "biz_flow.configure_source.attributes": "biz_flow.flow_identifiers.conditions",
        "biz_flow.configure_targets.process_steps": "biz_flow.process_steps",
        "biz_flow.configure_routing.rule_name": "biz_flow.configure_routing.rule.name",
        "biz_flow.configure_routing.rule_type": "biz_flow.configure_routing.rule.rule_type",
        "biz_flow.configure_routing.rule_scope": "biz_flow.configure_routing.rule.rule_scope",
        "biz_flow.configure_routing.actions.action_name": "biz_flow.configure_routing.actions.name",
        "biz_flow.configure_routing.actions.action_type": "biz_flow.configure_routing.actions.type",
    }
    for old, new in replacements.items():
        if raw.startswith(old) or raw == old:
            paths.append(raw.replace(old, new, 1))
    if raw:
        paths.append(raw)
    # Target TP historical KB uses document_types while current input uses one
    # document_type value for the U-HAUL profile.
    if raw == "target_transport_profile.document_types":
        paths.insert(0, "target_transport_profile.document_type")
    if raw == "biz_flow.configure_targets[0].process_steps":
        paths.insert(0, "biz_flow.process_steps")
    return _unique(paths)


def _field_key(input_path: str, label: str) -> str:
    clean = re.sub(r"\[\d+\]", "", str(input_path or ""))
    parts = [_norm(x) for x in clean.split(".") if _norm(x)]
    generic = {"objects", "rows", "attributes", "conditions", "actions", "process_steps", "configure_targets"}
    for part in reversed(parts):
        if part not in generic:
            return part
    return _norm(label) or "unknown_field"


def _section_for_field(phase: str, field: Dict[str, Any]) -> str:
    tab = str(field.get("tab") or "").strip()
    if tab:
        return tab
    path = str(field.get("input_json_key") or "")
    if phase == "rule":
        if ".conditions" in path:
            return "Conditions"
        if ".actions" in path:
            return "Actions"
        return "Rule Details"
    if "transport_profile" in phase:
        if any(token in path for token in ("existing_account", "folder", "file_filter", "post_transfer")):
            return "Interface Details"
        return "Transport Profile Details"
    if "document_type" in phase:
        if "document_identifier" in path:
            return "Document Identifier"
        if "attributes_to_configure" in path:
            return "Attributes to Configure"
        return "Document Type Details"
    if phase == "data_map":
        return "Data Map Details"
    return phase.replace("_", " ").title()


def _role_for_action(action: str) -> Tuple[str, str, str]:
    action = _norm(action)
    if action in {"select_option", "fill_doc_id_rows", "fill_attributes", "fill_condition_rows", "fill_flow_attributes", "fill_process_steps"}:
        return "combobox", "select", "select_or_type"
    if action == "radio":
        return "radio", "radio", "radio_select"
    if action == "toggle":
        return "checkbox", "checkbox", "toggle"
    if action == "upload_file":
        return "file", "file", "upload_file"
    return "textbox", "text", "type_or_set_value"


def _parse_selector_hints(field: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    labels: List[str] = []
    names: List[str] = []
    selectors: List[str] = []
    for hint in field.get("selector_hints", []) if isinstance(field.get("selector_hints"), list) else []:
        text = str(hint or "").strip()
        low = text.lower()
        if low.startswith("label="):
            labels.append(text.split("=", 1)[1].strip())
        elif low.startswith("placeholder="):
            names.append(text.split("=", 1)[1].strip())
        elif low.startswith("css="):
            selectors.append(text.split("=", 1)[1].strip())
        else:
            selectors.append(text)
    return labels, names, selectors


def _parse_condition(condition: Any) -> Optional[Tuple[str, str]]:
    text = str(condition or "").strip()
    if not text or "=" not in text:
        return None
    parent, value = text.split("=", 1)
    parent = _norm(parent)
    value = value.strip()
    return (parent, value) if parent and value else None


def _phase_from_node(node: Dict[str, Any]) -> str:
    phase = _norm(node.get("page") or node.get("page_key"))
    return phase if phase in _PHASES else ""


def _phase_from_text(text: Any) -> str:
    value = _norm_text(text)
    if "bizflow" in value or "biz flow" in value or "routing" in value or "process step" in value:
        return "biz_flow"
    if "source transport" in value or "source tp" in value:
        return "source_transport_profile"
    if "target transport" in value or "target tp" in value:
        return "target_transport_profile"
    if "transport profile" in value or "system type" in value or "partner name" in value:
        return "source_transport_profile"
    if "source document" in value:
        return "source_document_type"
    if "target document" in value:
        return "target_document_type"
    if "document type" in value:
        return "source_document_type"
    if "data map" in value or "mapping identifier" in value:
        return "data_map"
    if "rule" in value or "condition" in value:
        return "rule"
    return ""


def _gate_section(phase: str, label: str) -> str:
    low = _norm_text(label)
    if phase == "biz_flow":
        if "routing" in low or "condition" in low or "action" in low:
            return "Configure Routing"
        if "process" in low or "target" in low:
            return "Configure Target(s)"
        if "source" in low:
            return "Configure Source"
        return "Flow Details"
    if phase == "rule":
        if "condition" in low:
            return "Conditions"
        if "mapping" in low or "action" in low:
            return "Actions"
        return "Rule Details"
    return phase.replace("_", " ").title()


def _status_is_negative(status: Any) -> bool:
    low = _norm_text(status)
    return any(token in low for token in ("failed", "rejected", "false positive", "not a valid", "partial evidence"))


class UnifiedKBImporter:
    """Import the reviewed unified HIP KB into the persistent PortalBrain.

    Canonical KB facts are stored separately from live-validated observations.
    They guide identity, field mapping, safe ordering and gates, while live MCP
    evidence is still required before a section can be promoted as successful.
    """

    def __init__(self, brain: "PortalBrain"):
        self.brain = brain
        self.import_index_path = brain.root / "unified_kb_imports.json"
        self.global_path = brain.root / "unified_kb_global.json"

    def import_files(
        self,
        kb_path: str | Path,
        graph_path: str | Path | None = None,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        kb_path = Path(kb_path)
        graph_path = Path(graph_path) if graph_path else None
        if not kb_path.is_file():
            return {"status": "missing", "kb_path": str(kb_path), "error": "Unified KB JSON not found"}
        kb = _read_json(kb_path)
        graph = _read_json(graph_path) if graph_path and graph_path.is_file() else {}
        digest = _sha256(kb_path)
        if graph_path and graph_path.is_file():
            digest = hashlib.sha256((digest + _sha256(graph_path)).encode("utf-8")).hexdigest()
        index = _read_json(self.import_index_path)
        imports = index.setdefault("imports", {})
        if not force and imports.get(digest, {}).get("status") == "ok":
            return {**imports[digest], "status": "unchanged", "digest": digest}

        global_doc = self._build_global_doc(kb, graph, kb_path, graph_path, digest)
        safe_write_json(self.global_path, global_doc)

        phase_results: Dict[str, Any] = {}
        page_models = kb.get("portal_page_models") if isinstance(kb.get("portal_page_models"), dict) else {}
        for phase, model in page_models.items():
            phase = _norm(phase)
            if phase not in _PHASES or not isinstance(model, dict):
                continue
            phase_results[phase] = self._import_phase(phase, model, kb, graph, digest)

        # Negative evidence and reviewed run verdicts are imported as negative
        # knowledge only. They cannot promote selectors or action sequences.
        negative_results = self._import_negative_evidence(kb, graph, digest)
        record = {
            "status": "ok",
            "digest": digest,
            "kb_path": str(kb_path),
            "graph_path": str(graph_path or ""),
            "kb_version": (kb.get("metadata") or {}).get("version"),
            "graph_id": graph.get("graph_id"),
            "imported_at": utc_now(),
            "global_file": str(self.global_path),
            "phases": phase_results,
            "negative_evidence": negative_results,
        }
        imports[digest] = record
        index.update({"schema_version": SCHEMA_VERSION, "latest_digest": digest, "latest": record})
        safe_write_json(self.import_index_path, index)
        return record

    def _build_global_doc(
        self,
        kb: Dict[str, Any],
        graph: Dict[str, Any],
        kb_path: Path,
        graph_path: Optional[Path],
        digest: str,
    ) -> Dict[str, Any]:
        return mask_sensitive_data({
            "schema_version": SCHEMA_VERSION,
            "digest": digest,
            "imported_at": utc_now(),
            "source": {
                "kb": str(kb_path),
                "graph": str(graph_path or ""),
                "metadata": kb.get("metadata") or {},
                "graph_id": graph.get("graph_id"),
            },
            "knowledge_priority_order": kb.get("authority_order") or [],
            "canonical_corrections": kb.get("canonical_corrections") or [],
            "canonical_rules": kb.get("canonical_rules") or {},
            "global_form_rules": kb.get("global_form_rules") or {},
            "lifecycle": kb.get("lifecycle") or {},
            "configuration_object_chain": kb.get("configuration_object_chain") or {},
            "architecture": kb.get("architecture") or {},
            "known_portal_bugs": kb.get("known_portal_bugs") or [],
            "known_gaps": kb.get("known_gaps") or [],
            "latest_verification": kb.get("latest_verification") or {},
            "run_evidence_addendum": kb.get("run_evidence_addendum") or {},
            "selector_strategy": (kb.get("canonical_rules") or {}).get("selector_strategy"),
            "blocking_validation_patterns": [
                "already exists",
                "required",
                "invalid file type",
                "no options found",
                "content not found",
                "selection required",
            ],
        })

    def _import_phase(
        self,
        phase: str,
        model: Dict[str, Any],
        kb: Dict[str, Any],
        graph: Dict[str, Any],
        digest: str,
    ) -> Dict[str, Any]:
        data = self.brain._load_phase(phase)
        data.setdefault("canonical_field_nodes", {})
        data.setdefault("canonical_dependency_edges", {})
        data.setdefault("hard_gates", {})
        data.setdefault("failure_recovery", {})
        data.setdefault("page_identity", {})
        data.setdefault("canonical_sources", [])

        data["page_identity"] = {
            "phase": phase,
            "url_pattern": model.get("url_pattern"),
            "portal_path": model.get("portal_path"),
            "tabs_in_order": model.get("tabs_in_order") or [],
            "active_surface_required": True,
            "expected_form_family": phase,
            "source": "HIP Unified Deep KB",
            "trust_class": "canonical",
        }
        fields = model.get("fields") if isinstance(model.get("fields"), list) else []
        for order, field in enumerate(fields, start=1):
            if not isinstance(field, dict):
                continue
            input_path = str(field.get("input_json_key") or "")
            label = str(field.get("label") or _field_key(input_path, ""))
            key = _field_key(input_path, label)
            section = _section_for_field(phase, field)
            node_id = f"{phase}.{_norm(section)}.{key}"
            hint_labels, hint_names, selectors = _parse_selector_hints(field)
            role, control_type, fill_strategy = _role_for_action(str(field.get("action") or "fill"))
            aliases = _unique([label, *(field.get("aliases") or []), *hint_labels])
            node = data["canonical_field_nodes"].setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "phase": phase,
                    "section": section,
                    "key": key,
                    "labels": [],
                    "names": [],
                    "roles": [],
                    "control_types": [],
                    "selectors": {},
                    "options": [],
                    "input_paths": [],
                    "preconditions": [],
                    "expected_effects": [],
                    "order_samples": [],
                    "required": True,
                    "fill_strategies": [],
                    "portal_actions": [],
                    "canonical_count": 0,
                    "trust_class": "canonical",
                    "source": "HIP Unified Deep KB",
                },
            )
            node["labels"] = _unique([*node.get("labels", []), *aliases])
            node["names"] = _unique([*node.get("names", []), *hint_names])
            node["roles"] = _unique([*node.get("roles", []), role])
            node["control_types"] = _unique([*node.get("control_types", []), control_type])
            node["input_paths"] = _unique([*node.get("input_paths", []), *_input_path_aliases(phase, input_path)])
            node["order_samples"] = _unique([*node.get("order_samples", []), order])
            node["fill_strategies"] = _unique([*node.get("fill_strategies", []), fill_strategy])
            node["portal_actions"] = _unique([*node.get("portal_actions", []), field.get("action")])
            node["aliases"] = aliases
            node["condition"] = field.get("condition")
            node["notes"] = field.get("notes")
            node["selector_priority"] = field.get("selector_priority")
            node["do_not_use"] = _unique([*node.get("do_not_use", []), *(field.get("do_not_use") or [])])
            node["per_row_fields"] = _unique([*node.get("per_row_fields", []), *(field.get("per_row_fields") or [])])
            node["step_types_known"] = field.get("step_types_known") or node.get("step_types_known") or []
            for selector in selectors:
                node["selectors"].setdefault(selector, {
                    "selector": selector,
                    "dynamic": False,
                    "canonical": True,
                    "canonical_count": 1,
                    "validated_count": 0,
                    "candidate_count": 0,
                    "failure_count": 0,
                    "last_seen_at": utc_now(),
                })
            node["canonical_count"] = int(node.get("canonical_count", 0)) + 1
            node["canonical_confidence"] = 1.0
            node["preferred_locator"] = self.brain._preferred_locator(node)

            condition = _parse_condition(field.get("condition"))
            if condition:
                parent_key, parent_value = condition
                edge_id = f"canonical|{phase}|{parent_key}|{_norm(parent_value)}|{key}"
                data["canonical_dependency_edges"][edge_id] = {
                    "edge_id": edge_id,
                    "from": f"{phase}.{_norm(section)}.{parent_key}",
                    "to": node_id,
                    "relation": "REVEALS_OR_ENABLES",
                    "when_parent_value": parent_value,
                    "child_label": label,
                    "evidence": "canonical-kb-condition",
                    "trust_class": "canonical",
                    "canonical_count": 1,
                    "memory_confidence": 1.0,
                    "source": "HIP Unified Deep KB",
                }

        self._import_graph_dependencies(phase, data, graph)
        self._import_gates(phase, data, graph)
        self._import_failure_recovery(phase, data, graph, kb)
        data["canonical_sources"] = _unique([
            *data.get("canonical_sources", []),
            {"digest": digest, "source": "HIP Unified Deep KB", "imported_at": utc_now()},
        ])
        data.setdefault("stats", {})["canonical_field_nodes"] = len(data["canonical_field_nodes"])
        data["stats"]["canonical_dependency_edges"] = len(data["canonical_dependency_edges"])
        data["stats"]["hard_gates"] = len(data["hard_gates"])
        self.brain._save_phase(phase, data)
        return {
            "phase_memory": str(self.brain._phase_path(phase)),
            "canonical_field_nodes": len(data["canonical_field_nodes"]),
            "canonical_dependency_edges": len(data["canonical_dependency_edges"]),
            "hard_gates": len(data["hard_gates"]),
            "page_identity": data["page_identity"],
        }

    def _import_graph_dependencies(self, phase: str, data: Dict[str, Any], graph: Dict[str, Any]) -> None:
        nodes = {str(n.get("id")): n for n in graph.get("nodes", []) if isinstance(n, dict) and n.get("id")}
        for edge in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
            if not isinstance(edge, dict) or edge.get("relation") != "REVEALS_OR_ENABLES":
                continue
            parent = nodes.get(str(edge.get("from")), {})
            child = nodes.get(str(edge.get("to")), {})
            child_phase = _phase_from_node(child)
            if child_phase != phase:
                continue
            condition = _parse_condition(parent.get("label"))
            if not condition:
                continue
            parent_key, parent_value = condition
            child_path = str(child.get("input_json_key") or "")
            child_key = _field_key(child_path, str(child.get("label") or ""))
            child_section = _section_for_field(phase, child)
            to_id = f"{phase}.{_norm(child_section)}.{child_key}"
            edge_id = f"canonical-kg|{phase}|{parent_key}|{_norm(parent_value)}|{child_key}"
            data["canonical_dependency_edges"][edge_id] = {
                "edge_id": edge_id,
                "from": f"{phase}.{_norm(child_section)}.{parent_key}",
                "to": to_id,
                "relation": "REVEALS_OR_ENABLES",
                "when_parent_value": parent_value,
                "child_label": child.get("label"),
                "evidence": "canonical-knowledge-graph",
                "trust_class": "canonical",
                "canonical_count": 1,
                "memory_confidence": 1.0,
                "source": graph.get("graph_id") or "HIP Unified Knowledge Graph",
            }

    def _import_gates(self, phase: str, data: Dict[str, Any], graph: Dict[str, Any]) -> None:
        nodes = {str(n.get("id")): n for n in graph.get("nodes", []) if isinstance(n, dict) and n.get("id")}
        for node_id, node in nodes.items():
            if node.get("type") != "Gate":
                continue
            inferred = _phase_from_text(node.get("label"))
            if inferred and inferred != phase:
                continue
            # Generic exact-value and identity gates are useful for every phase.
            label = str(node.get("label") or "")
            if not inferred and phase not in {"rule", "source_transport_profile", "target_transport_profile", "biz_flow"}:
                continue
            gid = f"canonical-gate|{phase}|{_norm(node_id)}"
            blocked_until = []
            for edge in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
                if isinstance(edge, dict) and edge.get("from") == node_id and edge.get("relation") == "BLOCKS_UNTIL":
                    target = nodes.get(str(edge.get("to")), {})
                    blocked_until.append(target.get("label") or edge.get("to"))
            data["hard_gates"][gid] = {
                "gate_id": gid,
                "label": label,
                "phase": phase,
                "section": _gate_section(phase, label),
                "blocks_until": _unique(blocked_until),
                "trust_class": "canonical",
                "source": node.get("source"),
                "required": True,
            }

    def _import_failure_recovery(self, phase: str, data: Dict[str, Any], graph: Dict[str, Any], kb: Dict[str, Any]) -> None:
        nodes = {str(n.get("id")): n for n in graph.get("nodes", []) if isinstance(n, dict) and n.get("id")}
        for node_id, node in nodes.items():
            if node.get("type") != "FailureMode":
                continue
            inferred = _phase_from_text(node.get("label"))
            if inferred and inferred != phase:
                continue
            recovery_labels: List[str] = []
            for edge in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
                if isinstance(edge, dict) and edge.get("from") == node_id and edge.get("relation") == "RECOVERED_BY":
                    target = nodes.get(str(edge.get("to")), {})
                    recovery_labels.append(str(target.get("label") or edge.get("to")))
            fid = f"failure|{phase}|{_norm(node_id)}"
            data["failure_recovery"][fid] = {
                "failure_id": fid,
                "label": node.get("label"),
                "phase": phase,
                "recoveries": _unique(recovery_labels),
                "trust_class": "canonical-reviewed",
                "source": node.get("source"),
            }
        for bug in kb.get("known_portal_bugs", []) if isinstance(kb.get("known_portal_bugs"), list) else []:
            if not isinstance(bug, dict):
                continue
            inferred = _phase_from_text(" ".join(str(bug.get(k) or "") for k in ("page", "symptom", "root_cause", "fix")))
            if inferred and inferred != phase:
                continue
            if not inferred:
                continue
            bid = f"portal-bug|{phase}|{_norm(bug.get('id') or bug.get('symptom'))}"
            data["failure_recovery"][bid] = {
                "failure_id": bid,
                "label": bug.get("symptom"),
                "phase": phase,
                "root_cause": bug.get("root_cause"),
                "recoveries": _unique([bug.get("fix")]),
                "observed": bug.get("observed"),
                "trust_class": "canonical-reviewed",
                "source": "HIP Unified Deep KB",
            }

    def _import_negative_evidence(self, kb: Dict[str, Any], graph: Dict[str, Any], digest: str) -> Dict[str, Any]:
        phase_counts: Dict[str, int] = {p: 0 for p in _PHASES}
        addendum = kb.get("run_evidence_addendum") if isinstance(kb.get("run_evidence_addendum"), dict) else {}
        verdicts = addendum.get("phase_verdicts") if isinstance(addendum.get("phase_verdicts"), list) else []
        for row in verdicts:
            verdict_text = row.get("accepted_verdict") or row.get("status") or row.get("verdict") if isinstance(row, dict) else ""
            if not isinstance(row, dict) or not _status_is_negative(verdict_text):
                continue
            phase = _norm(row.get("phase"))
            if phase not in _PHASES:
                continue
            data = self.brain._load_phase(phase)
            data.setdefault("negative_evidence", {})
            nid = f"reviewed-run|{_norm(row.get('run_id') or row.get('run'))}|{phase}"
            data["negative_evidence"][nid] = {
                **mask_sensitive_data(row),
                "evidence_id": nid,
                "trust_class": "reviewed-negative-evidence",
                "digest": digest,
                "imported_at": utc_now(),
            }
            self.brain._save_phase(phase, data)
            phase_counts[phase] += 1

        # Graph phase-run nodes provide an additional normalized source.
        for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
            if not isinstance(node, dict) or node.get("type") != "PhaseRun" or not _status_is_negative(node.get("status")):
                continue
            label = str(node.get("label") or "")
            phase = ""
            for candidate in _PHASES:
                if candidate.replace("_", " ") in _norm_text(label) or candidate in _norm(label):
                    phase = candidate
                    break
            if not phase:
                phase = _phase_from_text(label)
            if phase not in _PHASES:
                continue
            data = self.brain._load_phase(phase)
            data.setdefault("negative_evidence", {})
            nid = f"graph|{_norm(node.get('id'))}"
            data["negative_evidence"][nid] = {
                **mask_sensitive_data(node),
                "evidence_id": nid,
                "trust_class": "reviewed-negative-evidence",
                "digest": digest,
                "imported_at": utc_now(),
            }
            self.brain._save_phase(phase, data)
            phase_counts[phase] += 1
        return {k: v for k, v in phase_counts.items() if v}

    def status(self) -> Dict[str, Any]:
        index = _read_json(self.import_index_path)
        return {
            "schema_version": SCHEMA_VERSION,
            "brain_dir": str(self.brain.root),
            "import_index": str(self.import_index_path),
            "global_file": str(self.global_path),
            "latest": index.get("latest") or {},
        }


def import_unified_kb(
    brain: "PortalBrain",
    kb_path: str | Path,
    graph_path: str | Path | None = None,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    try:
        return UnifiedKBImporter(brain).import_files(kb_path, graph_path, force=force)
    except Exception as exc:
        return {
            "status": "error",
            "kb_path": str(kb_path),
            "graph_path": str(graph_path or ""),
            "error": mask_sensitive_string(str(exc)),
        }
