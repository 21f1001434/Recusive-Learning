from __future__ import annotations

import json
import hashlib
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .upload_assets import parse_accept_extensions


_DYNAMIC_SELECTOR_RE = re.compile(
    r"(?:dds-form-field-|mat-input-|react-select-|ng-tns-c|cdk-overlay-|\b\d{5,}\b)",
    re.IGNORECASE,
)


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
        key = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
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


def _dynamic_selector(selector: str) -> bool:
    selector = str(selector or "")
    return bool(_DYNAMIC_SELECTOR_RE.search(selector))


def _safe_count(value: Any) -> int:
    """Return a non-negative selector evidence count from legacy JSON values."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _selector_text_from_entry(key: Any, value: Any) -> str:
    """Recover a selector string from current and historical brain schemas.

    Historical Portal Brain files used more than one representation:
    ``{selector: metadata}``, ``{name: selector_string}``, a list of selector
    strings, and occasionally a single selector string.  Runtime planning must
    treat those files as migratable evidence instead of crashing.
    """
    if isinstance(value, dict):
        candidate = value.get("selector") or value.get("css") or value.get("xpath") or value.get("value")
        if candidate:
            return str(candidate).strip()
    elif isinstance(value, str) and value.strip():
        return value.strip()

    key_text = str(key or "").strip()
    if key_text and not key_text.isdigit():
        return key_text
    return ""


def _normalize_selector_map(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize all known selector-memory shapes to ``selector -> record``.

    The function is deliberately tolerant because long-term memory survives code
    upgrades.  Invalid entries are ignored, valid legacy strings are preserved,
    and evidence counters are converted safely.
    """
    if isinstance(raw, dict) and any(raw.get(key) for key in ("selector", "css", "xpath")):
        # A few pre-v1 snapshots stored one selector record directly instead of
        # wrapping it in a selector-keyed mapping.
        entries = [(raw.get("selector") or raw.get("css") or raw.get("xpath"), raw)]
    elif isinstance(raw, dict):
        entries = list(raw.items())
    elif isinstance(raw, (list, tuple, set)):
        entries = list(enumerate(raw))
    elif isinstance(raw, str):
        entries = [(raw, raw)]
    else:
        entries = []

    normalized: Dict[str, Dict[str, Any]] = {}
    for key, value in entries:
        selector = _selector_text_from_entry(key, value)
        if not selector:
            continue
        source = dict(value) if isinstance(value, dict) else {}
        record = {
            **source,
            "selector": selector,
            "dynamic": bool(source.get("dynamic", _dynamic_selector(selector))),
            "canonical": bool(source.get("canonical", False)),
            "canonical_count": _safe_count(source.get("canonical_count")),
            "validated_count": _safe_count(source.get("validated_count")),
            "candidate_count": _safe_count(source.get("candidate_count")),
            "failure_count": _safe_count(source.get("failure_count")),
            "last_seen_at": source.get("last_seen_at"),
        }
        existing = normalized.get(selector)
        if existing is None:
            normalized[selector] = record
            continue
        for count_key in ("canonical_count", "validated_count", "candidate_count", "failure_count"):
            existing[count_key] = _safe_count(existing.get(count_key)) + _safe_count(record.get(count_key))
        existing["canonical"] = bool(existing.get("canonical") or record.get("canonical"))
        existing["dynamic"] = bool(existing.get("dynamic") or record.get("dynamic"))
        if str(record.get("last_seen_at") or "") > str(existing.get("last_seen_at") or ""):
            existing["last_seen_at"] = record.get("last_seen_at")
    return normalized


def _merge_selector_maps(*values: Any) -> Dict[str, Dict[str, Any]]:
    """Merge canonical/live selector maps without losing evidence counts."""
    merged: Dict[str, Dict[str, Any]] = {}
    for value in values:
        for selector, record in _normalize_selector_map(value).items():
            if selector not in merged:
                merged[selector] = dict(record)
                continue
            current = merged[selector]
            for count_key in ("canonical_count", "validated_count", "candidate_count", "failure_count"):
                current[count_key] = _safe_count(current.get(count_key)) + _safe_count(record.get(count_key))
            current["canonical"] = bool(current.get("canonical") or record.get("canonical"))
            current["dynamic"] = bool(current.get("dynamic") or record.get("dynamic"))
            if str(record.get("last_seen_at") or "") > str(current.get("last_seen_at") or ""):
                current["last_seen_at"] = record.get("last_seen_at")
    return merged


def _semantic_field_id(phase: str, section: str, key: str, label: str) -> str:
    semantic = _norm(key) or _norm(label) or "unknown_field"
    return f"{_norm(phase)}.{_norm(section or phase)}.{semantic}"


def _edge_id(edge: Dict[str, Any]) -> str:
    return "|".join(
        [
            _norm(edge.get("from")),
            _norm(edge.get("when_parent_value")),
            _norm(edge.get("relation")),
            _norm(edge.get("to")),
        ]
    )


def _repeatable_id(phase: str, row: Dict[str, Any]) -> str:
    return "|".join(
        [
            _norm(phase),
            _norm(row.get("tab") or row.get("section")),
            _norm(row.get("section")),
            str(row.get("input_path") or ""),
        ]
    )


@dataclass
class PortalBrainPolicy:
    enabled: bool = True
    bootstrap_from_runs: bool = True
    require_judge_pass_for_promotion: bool = True
    learn_warning_runs_as_candidates: bool = True
    min_validated_edge_confidence: float = 0.60
    max_source_runs: int = 300
    prefer_semantic_locators: bool = True
    reject_dynamic_selectors_as_primary: bool = True
    auto_import_unified_kb: bool = True
    unified_kb_path: str = "./knowledge_base/HIP_Unified_Deep_KB.json"
    unified_kg_path: str = "./knowledge_base/HIP_Unified_Knowledge_Graph.json"
    unified_kb_required: bool = True
    force_unified_kb_reimport: bool = False
    self_heal_kb: bool = True
    auto_apply_validated_kb_repairs: bool = True
    kb_repair_min_confirmations: int = 1
    kb_supersede_min_confirmations: int = 2
    preserve_original_kb: bool = True
    export_corrected_kb_after_run: bool = True
    revalidate_known_parent_branches: bool = True


class PortalBrain:
    """Persistent, inspectable long-term memory for HIP Portal forms.

    The brain is deliberately separate from an individual run folder. It merges
    learned controls, parent-value-child transitions, repeatable rows, successful
    action order and judge outcomes across runs. Failed runs are retained as
    negative evidence but cannot overwrite validated knowledge.
    """

    SCHEMA_VERSION = "hip.portal-brain.v1"

    def __init__(self, root: str | Path, policy: Optional[PortalBrainPolicy] = None):
        self.root = Path(root)
        self.policy = policy or PortalBrainPolicy()
        self.root.mkdir(parents=True, exist_ok=True)
        self.phase_dir = self.root / "phases"
        self.phase_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self.run_index_path = self.root / "run_index.json"
        if not self.manifest_path.exists():
            safe_write_json(
                self.manifest_path,
                {
                    "schema_version": self.SCHEMA_VERSION,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                    "phase_files": {},
                    "policy": self.policy.__dict__,
                },
            )
        if not self.run_index_path.exists():
            safe_write_json(self.run_index_path, {"schema_version": self.SCHEMA_VERSION, "runs": {}})

    @classmethod
    def from_config(cls, config: Any) -> "PortalBrain":
        cfg = getattr(config, "brain", None)
        reporting = getattr(config, "reporting", None)
        memory_root = Path(getattr(reporting, "memory_dir", "./data/hip_memory"))
        brain_dir = getattr(cfg, "directory", "portal_brain") if cfg is not None else "portal_brain"
        root = Path(brain_dir)
        if not root.is_absolute():
            root = memory_root / root
        policy = PortalBrainPolicy(
            enabled=bool(getattr(cfg, "enabled", True)),
            bootstrap_from_runs=bool(getattr(cfg, "bootstrap_from_runs", True)),
            require_judge_pass_for_promotion=bool(getattr(cfg, "require_judge_pass_for_promotion", True)),
            learn_warning_runs_as_candidates=bool(getattr(cfg, "learn_warning_runs_as_candidates", True)),
            min_validated_edge_confidence=float(getattr(cfg, "min_validated_edge_confidence", 0.60)),
            max_source_runs=int(getattr(cfg, "max_source_runs", 300)),
            prefer_semantic_locators=bool(getattr(cfg, "prefer_semantic_locators", True)),
            reject_dynamic_selectors_as_primary=bool(getattr(cfg, "reject_dynamic_selectors_as_primary", True)),
            auto_import_unified_kb=bool(getattr(cfg, "auto_import_unified_kb", True)),
            unified_kb_path=str(getattr(cfg, "unified_kb_path", "./knowledge_base/HIP_Unified_Deep_KB.json")),
            unified_kg_path=str(getattr(cfg, "unified_kg_path", "./knowledge_base/HIP_Unified_Knowledge_Graph.json")),
            unified_kb_required=bool(getattr(cfg, "unified_kb_required", True)),
            force_unified_kb_reimport=bool(getattr(cfg, "force_unified_kb_reimport", False)),
            self_heal_kb=bool(getattr(cfg, "self_heal_kb", True)),
            auto_apply_validated_kb_repairs=bool(getattr(cfg, "auto_apply_validated_kb_repairs", True)),
            kb_repair_min_confirmations=int(getattr(cfg, "kb_repair_min_confirmations", 1)),
            kb_supersede_min_confirmations=int(getattr(cfg, "kb_supersede_min_confirmations", 2)),
            preserve_original_kb=bool(getattr(cfg, "preserve_original_kb", True)),
            export_corrected_kb_after_run=bool(getattr(cfg, "export_corrected_kb_after_run", True)),
            revalidate_known_parent_branches=bool(getattr(cfg, "revalidate_known_parent_branches", True)),
        )
        return cls(root, policy)

    def _phase_path(self, phase: str) -> Path:
        return self.phase_dir / f"{_norm(phase)}.json"

    def _blank_phase(self, phase: str) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "phase": phase,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "field_nodes": {},
            "canonical_field_nodes": {},
            "dependency_edges": {},
            "canonical_dependency_edges": {},
            "repeatable_rows": {},
            "hard_gates": {},
            "failure_recovery": {},
            "negative_evidence": {},
            "kb_repairs": {},
            "validated_field_overrides": {},
            "superseded_canonical_edges": {},
            "kb_repair_history": [],
            "kb_revision": 0,
            "page_identity": {},
            "canonical_sources": [],
            "parent_branches": {},
            "unresolved_gaps": {},
            "page_fingerprints": {},
            "api_contracts": {},
            "validation_rules": {},
            "state_transitions": {},
            "console_signatures": {},
            "mcp_learning_capabilities": {},
            "coverage_history": [],
            "drift_history": [],
            "autonomous_learning_runs": [],
            "run_history": [],
            "stats": {
                "validated_runs": 0,
                "candidate_runs": 0,
                "failed_runs": 0,
                "field_nodes": 0,
                "dependency_edges": 0,
                "repeatable_rows": 0,
            },
        }

    def _load_phase(self, phase: str) -> Dict[str, Any]:
        path = self._phase_path(phase)
        data = _read_json(path, self._blank_phase(phase)) if path.exists() else self._blank_phase(phase)
        if not isinstance(data, dict):
            data = self._blank_phase(phase)
        for key, default in (
            ("field_nodes", {}),
            ("canonical_field_nodes", {}),
            ("dependency_edges", {}),
            ("canonical_dependency_edges", {}),
            ("repeatable_rows", {}),
            ("hard_gates", {}),
            ("failure_recovery", {}),
            ("negative_evidence", {}),
            ("kb_repairs", {}),
            ("validated_field_overrides", {}),
            ("superseded_canonical_edges", {}),
            ("kb_repair_history", []),
            ("kb_revision", 0),
            ("page_identity", {}),
            ("canonical_sources", []),
            ("parent_branches", {}),
            ("unresolved_gaps", {}),
            ("page_fingerprints", {}),
            ("api_contracts", {}),
            ("validation_rules", {}),
            ("state_transitions", {}),
            ("console_signatures", {}),
            ("mcp_learning_capabilities", {}),
            ("coverage_history", []),
            ("drift_history", []),
            ("autonomous_learning_runs", []),
            ("run_history", []),
            ("stats", {}),
        ):
            data.setdefault(key, default)

        # Backward-compatible in-memory migration. Older Portal Brain versions
        # sometimes persisted selector values as strings/lists rather than
        # metadata dictionaries. Normalize both live and canonical nodes before
        # any ingestion, merge, sort, or deterministic-plan compilation.
        for collection_key in ("field_nodes", "canonical_field_nodes"):
            collection = data.get(collection_key)
            if not isinstance(collection, dict):
                data[collection_key] = {}
                continue
            for node_id, node in list(collection.items()):
                if not isinstance(node, dict):
                    continue
                node["selectors"] = _normalize_selector_map(node.get("selectors"))
                node["preferred_locator"] = self._preferred_locator(node)
                collection[node_id] = node
        return data

    def _save_phase(self, phase: str, data: Dict[str, Any]) -> None:
        data["updated_at"] = utc_now()
        data["stats"].update(
            {
                "field_nodes": len(data.get("field_nodes") or {}),
                "canonical_field_nodes": len(data.get("canonical_field_nodes") or {}),
                "dependency_edges": len(data.get("dependency_edges") or {}),
                "canonical_dependency_edges": len(data.get("canonical_dependency_edges") or {}),
                "repeatable_rows": len(data.get("repeatable_rows") or {}),
                "hard_gates": len(data.get("hard_gates") or {}),
                "negative_evidence": len(data.get("negative_evidence") or {}),
                "kb_repairs": len(data.get("kb_repairs") or {}),
                "kb_repairs_applied": sum(1 for r in (data.get("kb_repairs") or {}).values() if isinstance(r, dict) and r.get("status") == "applied"),
                "kb_revision": int(data.get("kb_revision", 0) or 0),
                "page_fingerprints": len(data.get("page_fingerprints") or {}),
                "api_contracts": len(data.get("api_contracts") or {}),
                "validation_rules": len(data.get("validation_rules") or {}),
                "state_transitions": len(data.get("state_transitions") or {}),
                "console_signatures": len(data.get("console_signatures") or {}),
                "autonomous_learning_runs": len(data.get("autonomous_learning_runs") or []),
            }
        )
        path = self._phase_path(phase)
        safe_write_json(path, data)
        manifest = _read_json(self.manifest_path, {})
        manifest.setdefault("phase_files", {})[_norm(phase)] = str(path)
        manifest["updated_at"] = utc_now()
        manifest["policy"] = self.policy.__dict__
        safe_write_json(self.manifest_path, manifest)

    def ingest_autonomous_learning(
        self,
        *,
        phase: str,
        summary: Dict[str, Any],
        trust: str,
        run_id: str,
    ) -> Dict[str, Any]:
        """Merge deep portal-learning evidence into the persistent phase model.

        Validated evidence receives stronger weight. Candidate and negative evidence
        remain useful for drift/failure avoidance but cannot replace validated facts.
        """
        data = self._load_phase(phase)
        trust = str(trust or "negative").lower()
        weight = 3 if trust == "validated" else 1 if trust == "candidate" else 0

        def merge_record(collection: str, key: str, payload: Dict[str, Any]) -> None:
            if not key:
                return
            bucket = data.setdefault(collection, {})
            row = bucket.setdefault(key, {
                **mask_sensitive_data(payload),
                "validated_count": 0,
                "candidate_count": 0,
                "failure_count": 0,
                "source_runs": [],
                "first_seen_at": utc_now(),
                "last_seen_at": None,
            })
            # Refresh non-counter descriptive fields with the latest evidence.
            for name, value in mask_sensitive_data(payload).items():
                if value not in (None, "", [], {}):
                    row[name] = value
            if trust == "validated":
                row["validated_count"] = int(row.get("validated_count", 0)) + 1
            elif trust == "candidate":
                row["candidate_count"] = int(row.get("candidate_count", 0)) + 1
            else:
                row["failure_count"] = int(row.get("failure_count", 0)) + 1
            row["source_runs"] = _unique([*row.get("source_runs", []), run_id])[-100:]
            row["last_seen_at"] = utc_now()
            denom = int(row.get("validated_count", 0)) * 3 + int(row.get("candidate_count", 0)) + int(row.get("failure_count", 0))
            row["memory_confidence"] = round((int(row.get("validated_count", 0)) * 3 + int(row.get("candidate_count", 0))) / max(1, denom), 4)
            row["trust"] = "validated" if int(row.get("validated_count", 0)) > 0 else "candidate" if int(row.get("candidate_count", 0)) > 0 else "negative"

        page_model = summary.get("page_model") if isinstance(summary.get("page_model"), dict) else {}
        fingerprint = str(page_model.get("fingerprint") or "")
        previously_validated_fingerprints = {
            str(key) for key, row in (data.get("page_fingerprints") or {}).items()
            if isinstance(row, dict) and int(row.get("validated_count", 0)) > 0
        }
        if fingerprint:
            merge_record("page_fingerprints", fingerprint, {
                "fingerprint": fingerprint,
                "url_template": page_model.get("url_template"),
                "headings": page_model.get("headings") or [],
                "tabs": page_model.get("tabs") or [],
                "surface_marker_coverage": page_model.get("surface_marker_coverage") or {},
                "control_count_after": page_model.get("control_count_after"),
            })
            if previously_validated_fingerprints and fingerprint not in previously_validated_fingerprints:
                data["drift_history"].append({
                    "run_id": run_id,
                    "trust": trust,
                    "captured_at": utc_now(),
                    "type": "new_page_fingerprint",
                    "previous_validated_fingerprints": sorted(previously_validated_fingerprints)[-10:],
                    "observed_fingerprint": fingerprint,
                    "requires_revalidation": True,
                })

        for row in summary.get("api_contracts") or []:
            if isinstance(row, dict):
                merge_record("api_contracts", str(row.get("contract_id") or ""), row)
        for row in summary.get("validation_rules") or []:
            if isinstance(row, dict):
                merge_record("validation_rules", str(row.get("control_id") or ""), row)
        for row in summary.get("navigation_edges") or []:
            if isinstance(row, dict):
                key = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:20]
                merge_record("state_transitions", key, row)
        for row in summary.get("console_signatures") or []:
            if isinstance(row, dict):
                merge_record("console_signatures", str(row.get("signature") or ""), row)

        capabilities = summary.get("mcp_capabilities") if isinstance(summary.get("mcp_capabilities"), dict) else {}
        if capabilities:
            data["mcp_learning_capabilities"] = mask_sensitive_data(capabilities)
        coverage = summary.get("coverage") if isinstance(summary.get("coverage"), dict) else {}
        if coverage:
            data["coverage_history"].append({"run_id": run_id, "trust": trust, "captured_at": utc_now(), **mask_sensitive_data(coverage)})
            data["coverage_history"] = data["coverage_history"][-100:]
        drift = summary.get("drift") if isinstance(summary.get("drift"), dict) else {}
        if drift:
            data["drift_history"].append({"run_id": run_id, "trust": trust, "captured_at": utc_now(), **mask_sensitive_data(drift)})
            data["drift_history"] = data["drift_history"][-100:]
        for item in ((coverage.get("unexplored_safe_controls") or []) if isinstance(coverage, dict) else []):
            if not isinstance(item, dict):
                continue
            gid = "autonomous|" + _norm(item.get("semantic")) + "|" + _norm(item.get("role") or item.get("tag"))
            data["unresolved_gaps"][gid] = {
                **mask_sensitive_data(item),
                "source": "autonomous_portal_learning",
                "reason": "safe control observed but not exercised in the judged phase path",
                "last_seen_run": run_id,
            }

        data["autonomous_learning_runs"].append({
            "run_id": run_id,
            "trust": trust,
            "weight": weight,
            "attempt": summary.get("attempt"),
            "page_fingerprint": fingerprint,
            "api_contract_count": len(summary.get("api_contracts") or []),
            "validation_rule_count": len(summary.get("validation_rules") or []),
            "captured_at": utc_now(),
        })
        data["autonomous_learning_runs"] = data["autonomous_learning_runs"][-200:]
        self._save_phase(phase, data)
        return {
            "status": "promoted" if trust == "validated" else "stored_as_candidate" if trust == "candidate" else "stored_as_negative",
            "phase": phase,
            "trust": trust,
            "page_fingerprint": fingerprint,
            "api_contracts": len(data.get("api_contracts") or {}),
            "validation_rules": len(data.get("validation_rules") or {}),
            "state_transitions": len(data.get("state_transitions") or {}),
            "phase_memory": str(self._phase_path(phase)),
        }

    def record_runtime_recovery(
        self,
        *,
        phase: str,
        classification: str,
        signature: str,
        action: str,
        outcome: str,
        run_id: str,
        evidence_dir: str = "",
    ) -> str:
        """Store a runtime repair as candidate/negative evidence.

        A repair is never treated as deterministic truth at action time. The
        orchestrator must call ``promote_runtime_recovery`` after the independent
        deterministic/text/vision gate passes.
        """
        data = self._load_phase(phase)
        recovery_id = "runtime_recovery|" + "|".join([
            _norm(classification),
            _norm(action),
            _norm(signature),
        ])
        stored = data["failure_recovery"].setdefault(
            recovery_id,
            {
                "recovery_id": recovery_id,
                "classification": classification,
                "signature": signature,
                "action": action,
                "candidate_count": 0,
                "validated_count": 0,
                "failure_count": 0,
                "source_runs": [],
                "evidence_dirs": [],
                "trust": "candidate",
            },
        )
        if outcome == "candidate":
            stored["candidate_count"] = int(stored.get("candidate_count", 0)) + 1
            stored["trust"] = "candidate"
        else:
            stored["failure_count"] = int(stored.get("failure_count", 0)) + 1
            stored["trust"] = "negative_evidence"
        stored["source_runs"] = _unique([*stored.get("source_runs", []), run_id])[-50:]
        stored["evidence_dirs"] = _unique([*stored.get("evidence_dirs", []), evidence_dir])[-50:]
        stored["last_seen_at"] = utc_now()
        self._save_phase(phase, data)
        return recovery_id

    def promote_runtime_recovery(
        self,
        *,
        phase: str,
        recovery_id: str,
        judge_pass: bool,
        run_id: str,
    ) -> Dict[str, Any]:
        data = self._load_phase(phase)
        stored = (data.get("failure_recovery") or {}).get(recovery_id)
        if not isinstance(stored, dict):
            return {"status": "missing", "recovery_id": recovery_id}
        if judge_pass:
            stored["validated_count"] = int(stored.get("validated_count", 0)) + 1
            stored["trust"] = "validated_recovery"
            stored["last_validated_run"] = run_id
        else:
            stored["failure_count"] = int(stored.get("failure_count", 0)) + 1
            stored["trust"] = "negative_evidence"
        stored["last_seen_at"] = utc_now()
        data["failure_recovery"][recovery_id] = stored
        self._save_phase(phase, data)
        return {
            "status": "validated" if judge_pass else "failed",
            "recovery_id": recovery_id,
            "validated_count": int(stored.get("validated_count", 0)),
            "failure_count": int(stored.get("failure_count", 0)),
        }

    def reset(self) -> None:
        for path in self.phase_dir.glob("*.json"):
            try:
                path.unlink()
            except Exception:
                pass
        safe_write_json(self.run_index_path, {"schema_version": self.SCHEMA_VERSION, "runs": {}})
        safe_write_json(
            self.manifest_path,
            {
                "schema_version": self.SCHEMA_VERSION,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "phase_files": {},
                "policy": self.policy.__dict__,
            },
        )

    def bootstrap(self, runs_root: str | Path, *, exclude_run: str | Path | None = None, rebuild: bool = False) -> Dict[str, Any]:
        if not self.policy.enabled or not self.policy.bootstrap_from_runs:
            return {"status": "disabled", "brain_dir": str(self.root)}
        if rebuild:
            self.reset()
        runs_root = Path(runs_root)
        exclude = Path(exclude_run).resolve() if exclude_run else None
        index = _read_json(self.run_index_path, {"runs": {}})
        scanned = 0
        skipped = 0
        errors: List[Dict[str, Any]] = []
        candidates = [p.parent for p in runs_root.glob("*/full_dummy_fill_summary.json")]
        candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
        if self.policy.max_source_runs > 0:
            candidates = candidates[-self.policy.max_source_runs :]
        for run_dir in candidates:
            try:
                if exclude is not None and run_dir.resolve() == exclude:
                    continue
            except Exception:
                pass
            summary_path = run_dir / "full_dummy_fill_summary.json"
            stamp = f"{summary_path.stat().st_mtime_ns}:{summary_path.stat().st_size}" if summary_path.exists() else ""
            prior = (index.get("runs") or {}).get(run_dir.name, {})
            if prior.get("stamp") == stamp and not rebuild:
                skipped += 1
                continue
            try:
                result = self.ingest_run(run_dir, source="bootstrap")
                scanned += 1
                index.setdefault("runs", {})[run_dir.name] = {
                    "path": str(run_dir),
                    "stamp": stamp,
                    "ingested_at": utc_now(),
                    "result": result,
                }
            except Exception as exc:
                errors.append({"run": str(run_dir), "error": mask_sensitive_string(str(exc))})
        safe_write_json(self.run_index_path, index)
        return {
            "status": "ok" if not errors else "completed_with_errors",
            "brain_dir": str(self.root),
            "scanned_runs": scanned,
            "skipped_unchanged_runs": skipped,
            "errors": errors,
            "manifest": str(self.manifest_path),
        }

    def _trust_for_phase(self, phase: str, aggregate: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        verifications = aggregate.get("phase_verifications") if isinstance(aggregate.get("phase_verifications"), list) else []
        verification = next((v for v in verifications if isinstance(v, dict) and v.get("phase") == phase), {})
        status = str(verification.get("status") or "unknown")
        judges = aggregate.get("section_judge_results") if isinstance(aggregate.get("section_judge_results"), list) else []
        judge_rows = [j for j in judges if isinstance(j, dict) and (j.get("phase") == phase or _norm(j.get("phase")) == _norm(phase))]
        judge_pass = any(j.get("pass") is True for j in judge_rows)
        # BizFlow performs section-level live gates inside the phase summary.
        phase_summary = (aggregate.get("phase_summaries") or {}).get(phase, {}) if isinstance(aggregate.get("phase_summaries"), dict) else {}
        nested = phase_summary.get("section_judge_results") if isinstance(phase_summary, dict) else None
        if isinstance(nested, list) and nested:
            judge_pass = all(bool(j.get("pass")) for j in nested if isinstance(j, dict))
        if status == "pass" and (judge_pass or not self.policy.require_judge_pass_for_promotion):
            return "validated", {"verification_status": status, "judge_pass": judge_pass}
        if status in {"pass", "pass_with_warnings"} and self.policy.learn_warning_runs_as_candidates:
            return "candidate", {"verification_status": status, "judge_pass": judge_pass}
        return "failed", {"verification_status": status, "judge_pass": judge_pass}

    def ingest_run(self, run_dir: str | Path, *, aggregate: Optional[Dict[str, Any]] = None, source: str = "live") -> Dict[str, Any]:
        if not self.policy.enabled:
            return {"status": "disabled"}
        run_dir = Path(run_dir)
        if aggregate is None:
            aggregate = _read_json(run_dir / "full_dummy_fill_summary.json", {})
        if not isinstance(aggregate, dict):
            aggregate = {}
        phases = _unique(
            list(aggregate.get("phase_sequence") or [])
            + [p.stem.replace("_fast_fill_blueprint", "") for p in (run_dir / "fast_replay_blueprints").glob("*_fast_fill_blueprint.json")]
            + [p.stem.replace("_form_knowledge", "") for p in (run_dir / "portal_form_knowledge").glob("*_form_knowledge.json")]
        )
        results: Dict[str, Any] = {}
        for phase in phases:
            trust, trust_evidence = self._trust_for_phase(str(phase), aggregate)
            results[str(phase)] = self.ingest_phase(
                phase=str(phase),
                run_dir=run_dir,
                trust=trust,
                trust_evidence=trust_evidence,
                source=source,
            )
        return {"status": "ok", "run": str(run_dir), "phases": results}

    def ingest_phase(
        self,
        *,
        phase: str,
        run_dir: Path,
        trust: str,
        trust_evidence: Dict[str, Any],
        source: str,
    ) -> Dict[str, Any]:
        data = self._load_phase(phase)
        blueprint_path = run_dir / "fast_replay_blueprints" / f"{phase}_fast_fill_blueprint.json"
        plan_path = run_dir / "deterministic_plans" / f"{phase}_deterministic_plan.json"
        knowledge_paths = list((run_dir / "portal_form_knowledge").glob(f"{phase}*_form_knowledge.json"))
        blueprint = _read_json(blueprint_path, {}) if blueprint_path.exists() else {}
        plan = _read_json(plan_path, {}) if plan_path.exists() else {}
        knowledge_docs = [_read_json(path, {}) for path in knowledge_paths]
        weight = 3 if trust == "validated" else 1 if trust == "candidate" else 0

        # Field/action knowledge from successful blueprints and deterministic plans.
        steps: List[Dict[str, Any]] = []
        if isinstance(blueprint.get("field_steps"), list):
            steps.extend([dict(s) for s in blueprint["field_steps"] if isinstance(s, dict)])
        for action in plan.get("actions", []) if isinstance(plan.get("actions"), list) else []:
            if not isinstance(action, dict) or action.get("operation") not in {"fill", "select", "select_multi", "radio", "upload_file"}:
                continue
            steps.append(
                {
                    "key": action.get("field_key") or str(action.get("knowledge_node") or "").split(".")[-1],
                    "label": action.get("label") or action.get("fallback_label"),
                    "selector": action.get("selector"),
                    "fallback_label": action.get("fallback_label"),
                    "fallback_name": action.get("fallback_name"),
                    "tab": action.get("section"),
                    "value": action.get("expected_value"),
                    "fill_strategy": ("select_multiple" if action.get("operation") == "select_multi" else "select_radio" if action.get("operation") == "radio" else "upload_file" if action.get("operation") == "upload_file" else "select_or_type" if action.get("operation") == "select" else "type_or_set_value"),
                    "row_kind": action.get("row_kind"),
                    "row_index": action.get("row_index"),
                    "semantic_locator": action.get("semantic_locator"),
                    "required": action.get("required", True),
                    "input_path": action.get("input_path"),
                    "preconditions": action.get("preconditions", []),
                    "expected_effects": action.get("expected_effects", []),
                    "order": action.get("order"),
                }
            )
        for step in steps:
            section = str(step.get("tab") or step.get("section") or phase)
            key = str(step.get("key") or "")
            label = str(step.get("label") or step.get("fallback_label") or key)
            node_id = _semantic_field_id(phase, section, key, label)
            node = data["field_nodes"].setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "phase": phase,
                    "section": section,
                    "key": _norm(key) or _norm(label),
                    "labels": [],
                    "names": [],
                    "roles": [],
                    "control_types": [],
                    "selectors": {},
                    "options": [],
                    "observed_values": [],
                    "input_paths": [],
                    "preconditions": [],
                    "expected_effects": [],
                    "order_samples": [],
                    "required": False,
                    "fill_strategies": [],
                    "accepted_extensions": [],
                    "file_accept_raw": [],
                    "validated_count": 0,
                    "candidate_count": 0,
                    "failure_count": 0,
                    "source_runs": [],
                    "last_seen_at": None,
                },
            )
            node["labels"] = _unique([*node.get("labels", []), label, step.get("fallback_label")])
            node["names"] = _unique([*node.get("names", []), step.get("fallback_name")])
            node["roles"] = _unique([*node.get("roles", []), step.get("role")])
            node["control_types"] = _unique([*node.get("control_types", []), step.get("control_type")])
            selector = str(step.get("selector") or "")
            if selector:
                sel = node["selectors"].setdefault(
                    selector,
                    {
                        "selector": selector,
                        "dynamic": _dynamic_selector(selector),
                        "validated_count": 0,
                        "candidate_count": 0,
                        "failure_count": 0,
                        "last_seen_at": None,
                    },
                )
                sel[f"{trust}_count" if trust in {"validated", "candidate"} else "failure_count"] = int(
                    sel.get(f"{trust}_count" if trust in {"validated", "candidate"} else "failure_count", 0)
                ) + 1
                sel["last_seen_at"] = utc_now()
            node["options"] = _unique([*node.get("options", []), *(step.get("dropdown_options_sample") or step.get("options") or [])])
            node["observed_values"] = _unique([*node.get("observed_values", []), step.get("value")])
            node["input_paths"] = _unique([*node.get("input_paths", []), step.get("input_path")])
            node["preconditions"] = _unique([*node.get("preconditions", []), *(step.get("preconditions") or [])])
            node["expected_effects"] = _unique([*node.get("expected_effects", []), *(step.get("expected_effects") or [])])
            if step.get("order") is not None:
                try:
                    node["order_samples"].append(int(step.get("order")))
                except Exception:
                    pass
            node["required"] = bool(node.get("required") or step.get("required"))
            node["fill_strategies"] = _unique([*node.get("fill_strategies", []), step.get("fill_strategy")])
            node["row_kinds"] = _unique([*node.get("row_kinds", []), step.get("row_kind")])
            if step.get("row_index") is not None:
                node["row_indices"] = _unique([*node.get("row_indices", []), step.get("row_index")])
            if isinstance(step.get("semantic_locator"), dict):
                node["semantic_locators"] = [*node.get("semantic_locators", []), mask_sensitive_data(step.get("semantic_locator"))][-20:]
            if trust == "validated":
                node["validated_count"] += 1
            elif trust == "candidate":
                node["candidate_count"] += 1
            else:
                node["failure_count"] += 1
            node["source_runs"] = _unique([*node.get("source_runs", []), run_dir.name])[-50:]
            node["last_seen_at"] = utc_now()
            node["confidence"] = round(
                (node["validated_count"] * 3 + node["candidate_count"])
                / max(1, node["validated_count"] * 3 + node["candidate_count"] + node["failure_count"]),
                4,
            )
            node["preferred_locator"] = self._preferred_locator(node)

        # Exploration graph observations. Only live-observed edges can become plan truth.
        for knowledge in knowledge_docs:
            if not isinstance(knowledge, dict):
                continue
            for control in knowledge.get("control_registry", []) if isinstance(knowledge.get("control_registry"), list) else []:
                if not isinstance(control, dict):
                    continue
                section = str(control.get("section") or knowledge.get("section") or phase)
                key = str(control.get("key") or "")
                label = str(control.get("label") or key)
                node_id = _semantic_field_id(phase, section, key, label)
                node = data["field_nodes"].setdefault(
                    node_id,
                    {
                        "node_id": node_id,
                        "phase": phase,
                        "section": section,
                        "key": _norm(key) or _norm(label),
                        "labels": [],
                        "names": [],
                        "roles": [],
                        "control_types": [],
                        "selectors": {},
                        "options": [],
                        "observed_values": [],
                        "input_paths": [],
                        "preconditions": [],
                        "expected_effects": [],
                        "order_samples": [],
                        "required": False,
                        "fill_strategies": [],
                        "validated_count": 0,
                        "candidate_count": 0,
                        "failure_count": 0,
                        "source_runs": [],
                        "last_seen_at": None,
                    },
                )
                node["labels"] = _unique([*node.get("labels", []), label])
                node["roles"] = _unique([*node.get("roles", []), control.get("role")])
                node["control_types"] = _unique([*node.get("control_types", []), control.get("type")])
                node["options"] = _unique([*node.get("options", []), *(control.get("options") or [])])
                node["file_accept_raw"] = _unique([*node.get("file_accept_raw", []), control.get("accept")])
                node["accepted_extensions"] = _unique([*node.get("accepted_extensions", []), *parse_accept_extensions(control.get("accept"))])
                selector = str(control.get("selector") or "")
                if selector:
                    sel = node["selectors"].setdefault(
                        selector,
                        {
                            "selector": selector,
                            "dynamic": _dynamic_selector(selector),
                            "validated_count": 0,
                            "candidate_count": 0,
                            "failure_count": 0,
                            "last_seen_at": None,
                        },
                    )
                    if trust == "validated":
                        sel["validated_count"] += 1
                    elif trust == "candidate":
                        sel["candidate_count"] += 1
                    else:
                        sel["failure_count"] += 1
                    sel["last_seen_at"] = utc_now()
                node["source_runs"] = _unique([*node.get("source_runs", []), run_dir.name])[-50:]
                node["last_seen_at"] = utc_now()
                node["preferred_locator"] = self._preferred_locator(node)

            for edge in knowledge.get("dependency_edges", []) if isinstance(knowledge.get("dependency_edges"), list) else []:
                if not isinstance(edge, dict) or not str(edge.get("evidence") or "").startswith("live"):
                    continue
                eid = _edge_id(edge)
                stored = data["dependency_edges"].setdefault(
                    eid,
                    {
                        **mask_sensitive_data(edge),
                        "edge_id": eid,
                        "validated_count": 0,
                        "candidate_count": 0,
                        "failure_count": 0,
                        "source_runs": [],
                        "last_seen_at": None,
                    },
                )
                if trust == "validated":
                    stored["validated_count"] += 1
                elif trust == "candidate":
                    stored["candidate_count"] += 1
                else:
                    stored["failure_count"] += 1
                stored["source_runs"] = _unique([*stored.get("source_runs", []), run_dir.name])[-50:]
                stored["last_seen_at"] = utc_now()
                denom = stored["validated_count"] * 3 + stored["candidate_count"] + stored["failure_count"]
                stored["memory_confidence"] = round((stored["validated_count"] * 3 + stored["candidate_count"]) / max(1, denom), 4)

            for parent in knowledge.get("parents", []) if isinstance(knowledge.get("parents"), list) else []:
                if not isinstance(parent, dict):
                    continue
                pid = f"{_norm(phase)}.{_norm(parent.get('section') or knowledge.get('section') or phase)}.{_norm(parent.get('key') or parent.get('label'))}"
                stored = data["parent_branches"].setdefault(
                    pid,
                    {
                        "parent_id": pid,
                        "label": parent.get("label"),
                        "key": parent.get("key"),
                        "known_values": [],
                        "observed_values": [],
                        "input_paths": [],
                        "source_runs": [],
                    },
                )
                stored["known_values"] = _unique([*stored.get("known_values", []), *(parent.get("all_known_values") or [])])
                observed_values = [b.get("value") for b in parent.get("branches", []) if isinstance(b, dict) and b.get("observed")]
                stored["observed_values"] = _unique([*stored.get("observed_values", []), *observed_values])
                stored["input_paths"] = _unique([*stored.get("input_paths", []), parent.get("input_path")])
                stored["source_runs"] = _unique([*stored.get("source_runs", []), run_dir.name])[-50:]

            for gap in knowledge.get("unexplored_branches", []) if isinstance(knowledge.get("unexplored_branches"), list) else []:
                if not isinstance(gap, dict):
                    continue
                gid = "|".join([_norm(gap.get("parent_key")), _norm(gap.get("value")), _norm(gap.get("reason"))])
                data["unresolved_gaps"][gid] = {**mask_sensitive_data(gap), "last_seen_run": run_dir.name}

            for row in knowledge.get("repeatable_rows", []) if isinstance(knowledge.get("repeatable_rows"), list) else []:
                if not isinstance(row, dict):
                    continue
                rid = _repeatable_id(phase, row)
                stored = data["repeatable_rows"].setdefault(
                    rid,
                    {
                        **mask_sensitive_data(row),
                        "repeatable_id": rid,
                        "validated_count": 0,
                        "candidate_count": 0,
                        "failure_count": 0,
                        "source_runs": [],
                    },
                )
                if trust == "validated":
                    stored["validated_count"] += 1
                elif trust == "candidate":
                    stored["candidate_count"] += 1
                else:
                    stored["failure_count"] += 1
                stored["source_runs"] = _unique([*stored.get("source_runs", []), run_dir.name])[-50:]

        # Blueprint repeatable row plan is useful even when exploration could not branch live.
        for row in blueprint.get("repeatable_section_plan", []) if isinstance(blueprint.get("repeatable_section_plan"), list) else []:
            if not isinstance(row, dict):
                continue
            rid = _repeatable_id(phase, row)
            stored = data["repeatable_rows"].setdefault(
                rid,
                {
                    **mask_sensitive_data(row),
                    "repeatable_id": rid,
                    "validated_count": 0,
                    "candidate_count": 0,
                    "failure_count": 0,
                    "source_runs": [],
                },
            )
            if trust == "validated":
                stored["validated_count"] += 1
            elif trust == "candidate":
                stored["candidate_count"] += 1
            else:
                stored["failure_count"] += 1
            stored["source_runs"] = _unique([*stored.get("source_runs", []), run_dir.name])[-50:]

        # Reconcile the reviewed Unified KB with judge-approved live exploration.
        # Missing live facts may be added immediately after a validated dual-model
        # gate; canonical facts are superseded only after repeated explicit conflict.
        kb_repair_result = {"status": "disabled"}
        if self.policy.self_heal_kb:
            try:
                from .adaptive_kb_repair import AdaptiveKBRepairEngine
                kb_repair_result = AdaptiveKBRepairEngine(self).reconcile_phase(
                    phase=phase,
                    data=data,
                    knowledge_docs=knowledge_docs,
                    trust=trust,
                    trust_evidence=trust_evidence,
                    run_id=run_dir.name,
                    knowledge_files=[str(p) for p in knowledge_paths],
                )
            except Exception as exc:
                kb_repair_result = {"status": "error", "error": mask_sensitive_string(str(exc))}

        data["run_history"].append(
            {
                "run_id": run_dir.name,
                "path": str(run_dir),
                "trust": trust,
                "trust_evidence": trust_evidence,
                "source": source,
                "blueprint": str(blueprint_path) if blueprint_path.exists() else "",
                "knowledge_files": [str(p) for p in knowledge_paths],
                "plan": str(plan_path) if plan_path.exists() else "",
                "ingested_at": utc_now(),
            }
        )
        data["run_history"] = data["run_history"][-100:]
        if trust == "validated":
            data["stats"]["validated_runs"] = int(data["stats"].get("validated_runs", 0)) + 1
        elif trust == "candidate":
            data["stats"]["candidate_runs"] = int(data["stats"].get("candidate_runs", 0)) + 1
        else:
            data["stats"]["failed_runs"] = int(data["stats"].get("failed_runs", 0)) + 1
        self._save_phase(phase, data)
        return {
            "trust": trust,
            "field_nodes": len(data["field_nodes"]),
            "dependency_edges": len(data["dependency_edges"]),
            "repeatable_rows": len(data["repeatable_rows"]),
            "phase_memory": str(self._phase_path(phase)),
            "kb_repair": kb_repair_result,
        }

    def _preferred_locator(self, node: Dict[str, Any]) -> Dict[str, Any]:
        labels = node.get("labels") or []
        names = node.get("names") or []
        roles = node.get("roles") or []
        selectors = list(_normalize_selector_map(node.get("selectors")).values())
        selectors.sort(
            key=lambda s: (
                1 if s.get("dynamic") else 0,
                -_safe_count(s.get("validated_count")),
                -_safe_count(s.get("canonical_count")),
                -_safe_count(s.get("candidate_count")),
                _safe_count(s.get("failure_count")),
                str(s.get("selector") or ""),
            )
        )
        selected = next(
            (
                s
                for s in selectors
                if not (self.policy.reject_dynamic_selectors_as_primary and s.get("dynamic"))
                and (_safe_count(s.get("validated_count")) > 0 or _safe_count(s.get("canonical_count")) > 0)
            ),
            None,
        )
        if selected is None:
            selected = next((s for s in selectors if not s.get("dynamic")), None)
        return {
            "strategy": "semantic-first" if self.policy.prefer_semantic_locators else "selector-first",
            "label": labels[0] if labels else "",
            "name": names[0] if names else "",
            "role": roles[0] if roles else "",
            "selector": selected.get("selector") if selected else "",
            "dynamic_selectors_retained_as_evidence": [s.get("selector") for s in selectors if s.get("dynamic")][:10],
        }

    def import_unified_kb(
        self,
        kb_path: str | Path | None = None,
        graph_path: str | Path | None = None,
        *,
        force: bool | None = None,
    ) -> Dict[str, Any]:
        """Seed canonical reviewed knowledge into the persistent brain.

        Canonical facts remain distinct from live-validated observations. They
        guide planning and gates, but live MCP/DOM/text/vision evidence is still
        mandatory for section completion.
        """
        from .unified_kb import import_unified_kb

        kb = Path(kb_path or self.policy.unified_kb_path)
        graph = Path(graph_path or self.policy.unified_kg_path) if (graph_path or self.policy.unified_kg_path) else None
        return import_unified_kb(
            self,
            kb,
            graph,
            force=self.policy.force_unified_kb_reimport if force is None else bool(force),
        )

    def unified_kb_status(self) -> Dict[str, Any]:
        from .unified_kb import UnifiedKBImporter

        return UnifiedKBImporter(self).status()

    def phase_bundle(self, phase: str) -> Dict[str, Any]:
        data = self._load_phase(phase)
        live_nodes = list((data.get("field_nodes") or {}).values())
        canonical_nodes = list((data.get("canonical_field_nodes") or {}).values())
        by_id: Dict[str, Dict[str, Any]] = {}
        # Canonical node supplies stable semantics/input mapping. Live judged
        # observations overlay current options, successful labels and locators.
        for node in canonical_nodes:
            if isinstance(node, dict):
                by_id[str(node.get("node_id"))] = dict(node)
        for node in live_nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("node_id"))
            prior = by_id.get(node_id, {})
            merged = {**prior, **node}
            for list_key in ("labels", "names", "roles", "control_types", "options", "input_paths", "preconditions", "expected_effects", "order_samples", "fill_strategies", "portal_actions", "do_not_use", "per_row_fields"):
                merged[list_key] = _unique([*(prior.get(list_key) or []), *(node.get(list_key) or [])])
            merged["selectors"] = _merge_selector_maps(prior.get("selectors"), node.get("selectors"))
            merged["preferred_locator"] = self._preferred_locator(merged)
            by_id[node_id] = merged
        if self.policy.self_heal_kb:
            try:
                from .adaptive_kb_repair import AdaptiveKBRepairEngine
                by_id = AdaptiveKBRepairEngine(self).apply_field_overrides(data, by_id)
            except Exception:
                pass
        nodes = list(by_id.values())
        nodes = [
            n for n in nodes
            if int(n.get("canonical_count", 0)) > 0
            or int(n.get("validated_count", 0)) > 0
            or int(n.get("candidate_count", 0)) > 0
        ]

        def order(node: Dict[str, Any]) -> Tuple[float, str]:
            samples = [int(x) for x in node.get("order_samples", []) if isinstance(x, int) or str(x).isdigit()]
            return (statistics.median(samples) if samples else 100000.0, str(node.get("node_id")))

        nodes.sort(key=order)
        field_steps: List[Dict[str, Any]] = []
        for idx, node in enumerate(nodes, start=1):
            locator = node.get("preferred_locator") or self._preferred_locator(node)
            options = node.get("options") or []
            field_steps.append(
                {
                    "order": idx,
                    "key": node.get("key"),
                    "label": locator.get("label") or (node.get("labels") or [node.get("key")])[0],
                    "selector": locator.get("selector") or "",
                    "fallback_label": locator.get("label") or "",
                    "fallback_name": locator.get("name") or "",
                    "role": locator.get("role") or "",
                    "tab": node.get("section"),
                    "value": (node.get("observed_values") or [""])[-1],
                    "fill_strategy": (node.get("fill_strategies") or ["select_or_type" if options else "type_or_set_value"])[0],
                    "required": bool(node.get("required")),
                    "dropdown_options_sample": options,
                    "dropdown_option_count": len(options),
                    "input_path": (node.get("input_paths") or [None])[0],
                    "preconditions": node.get("preconditions") or [],
                    "expected_effects": node.get("expected_effects") or [],
                    "brain_node_id": node.get("node_id"),
                    "brain_confidence": node.get("confidence", 0.0),
                    "brain_validated_count": node.get("validated_count", 0),
                    "canonical_count": node.get("canonical_count", 0),
                    "canonical_trust": node.get("trust_class"),
                    "portal_action": (node.get("portal_actions") or [None])[0],
                    "condition": node.get("condition"),
                    "notes": node.get("notes"),
                    "aliases": node.get("aliases") or node.get("labels") or [],
                    "do_not_use": node.get("do_not_use") or [],
                    "per_row_fields": node.get("per_row_fields") or [],
                    "step_types_known": node.get("step_types_known") or [],
                    "locator_strategy": locator.get("strategy"),
                    "selector_stability": "semantic" if not locator.get("selector") else "stable" if not _dynamic_selector(locator.get("selector")) else "dynamic-evidence-only",
                }
            )

        repeatable = []
        for row in (data.get("repeatable_rows") or {}).values():
            if int(row.get("validated_count", 0)) <= 0 and int(row.get("candidate_count", 0)) <= 0:
                continue
            item = {k: v for k, v in row.items() if k not in {"repeatable_id", "validated_count", "candidate_count", "failure_count", "source_runs"}}
            repeatable.append(item)

        edges = []
        if self.policy.self_heal_kb:
            try:
                from .adaptive_kb_repair import AdaptiveKBRepairEngine
                edge_sources = AdaptiveKBRepairEngine(self).effective_edges(data)
            except Exception:
                edge_sources = [*((data.get("canonical_dependency_edges") or {}).values()), *((data.get("dependency_edges") or {}).values())]
        else:
            edge_sources = [*((data.get("canonical_dependency_edges") or {}).values()), *((data.get("dependency_edges") or {}).values())]
        seen_edges: set[str] = set()
        for edge in edge_sources:
            if not isinstance(edge, dict):
                continue
            canonical = int(edge.get("canonical_count", 0)) > 0 or edge.get("trust_class") == "canonical"
            validated = int(edge.get("validated_count", 0))
            confidence = float(edge.get("memory_confidence", 1.0 if canonical else 0.0))
            if not canonical and (validated <= 0 or confidence < self.policy.min_validated_edge_confidence):
                continue
            eid = _edge_id(edge)
            if eid in seen_edges:
                continue
            seen_edges.add(eid)
            edges.append({k: v for k, v in edge.items() if k not in {"edge_id", "validated_count", "candidate_count", "failure_count"}})

        validated_runs = int((data.get("stats") or {}).get("validated_runs", 0))
        candidate_runs = int((data.get("stats") or {}).get("candidate_runs", 0))

        def trusted_learning_rows(name: str, limit: int = 200) -> List[Dict[str, Any]]:
            rows = []
            for row in (data.get(name) or {}).values():
                if not isinstance(row, dict):
                    continue
                if int(row.get("validated_count", 0)) <= 0 and int(row.get("candidate_count", 0)) <= 0:
                    continue
                rows.append(dict(row))
            rows.sort(key=lambda row: (
                -int(row.get("validated_count", 0)),
                -int(row.get("candidate_count", 0)),
                str(row.get("last_seen_at") or ""),
            ))
            return rows[:limit]

        autonomous_learning = {
            "schema_version": "hip.portal-learning-memory.v1",
            "page_fingerprints": trusted_learning_rows("page_fingerprints", 20),
            "api_contracts": trusted_learning_rows("api_contracts", 250),
            "validation_rules": trusted_learning_rows("validation_rules", 500),
            "state_transitions": trusted_learning_rows("state_transitions", 300),
            "console_signatures": trusted_learning_rows("console_signatures", 120),
            "mcp_capabilities": data.get("mcp_learning_capabilities") or {},
            "latest_coverage": (data.get("coverage_history") or [])[-1] if data.get("coverage_history") else {},
            "recent_drift": (data.get("drift_history") or [])[-10:],
            "learning_runs": (data.get("autonomous_learning_runs") or [])[-50:],
        }
        blueprint = {
            "schema_version": "hip.flash-fill-blueprint.brain.v1",
            "phase": phase,
            "verification_status": "pass" if validated_runs > 0 else "candidate" if candidate_runs > 0 else "canonical_seed" if canonical_nodes else "no_memory",
            "field_steps": field_steps,
            "repeatable_section_plan": repeatable,
            "evidence_files": [str(self._phase_path(phase)), str(self.manifest_path)],
            "brain_stats": data.get("stats") or {},
            "page_identity": data.get("page_identity") or {},
            "hard_gates": list((data.get("hard_gates") or {}).values()),
            "failure_recovery": list((data.get("failure_recovery") or {}).values()),
            "negative_evidence": list((data.get("negative_evidence") or {}).values())[-100:],
            "canonical_sources": data.get("canonical_sources") or [],
            "autonomous_learning": autonomous_learning,
            "adaptive_kb": {
                "enabled": bool(self.policy.self_heal_kb),
                "kb_revision": int(data.get("kb_revision", 0) or 0),
                "applied_repairs": [r for r in (data.get("kb_repairs") or {}).values() if isinstance(r, dict) and r.get("status") == "applied"],
                "suspect_repairs": [r for r in (data.get("kb_repairs") or {}).values() if isinstance(r, dict) and r.get("status") == "suspect"],
            },
        }
        knowledge = {
            "schema_version": "hip.portal-form-knowledge.brain.v1",
            "phase": phase,
            "status": "validated_long_term_memory" if validated_runs > 0 else "candidate_long_term_memory",
            "dependency_edges": edges,
            "repeatable_rows": repeatable,
            "unexplored_branches": list((data.get("unresolved_gaps") or {}).values()),
            "control_registry": [
                {
                    "key": node.get("key"),
                    "label": (node.get("labels") or [""])[0],
                    "section": node.get("section"),
                    "role": (node.get("roles") or [""])[0],
                    "selector": (node.get("preferred_locator") or {}).get("selector", ""),
                    "options": node.get("options") or [],
                    "brain_node_id": node.get("node_id"),
                    "brain_confidence": node.get("confidence", 0.0),
                }
                for node in nodes
            ],
            "brain_file": str(self._phase_path(phase)),
            "brain_stats": data.get("stats") or {},
            "page_identity": data.get("page_identity") or {},
            "hard_gates": list((data.get("hard_gates") or {}).values()),
            "failure_recovery": list((data.get("failure_recovery") or {}).values()),
            "negative_evidence": list((data.get("negative_evidence") or {}).values())[-100:],
            "canonical_sources": data.get("canonical_sources") or [],
            "autonomous_learning": autonomous_learning,
            "adaptive_kb": {
                "enabled": bool(self.policy.self_heal_kb),
                "kb_revision": int(data.get("kb_revision", 0) or 0),
                "applied_repairs": [r for r in (data.get("kb_repairs") or {}).values() if isinstance(r, dict) and r.get("status") == "applied"],
                "suspect_repairs": [r for r in (data.get("kb_repairs") or {}).values() if isinstance(r, dict) and r.get("status") == "suspect"],
            },
        }
        return {
            "phase": phase,
            "blueprint": blueprint,
            "knowledge": knowledge,
            "source_path": self._phase_path(phase),
            "manifest": self.manifest_path,
            "stats": data.get("stats") or {},
        }

    def kb_repair_status(self) -> Dict[str, Any]:
        from .adaptive_kb_repair import AdaptiveKBRepairEngine
        return AdaptiveKBRepairEngine(self).status()

    def export_corrected_kb(self, output_dir: str | Path | None = None) -> Dict[str, Any]:
        from .adaptive_kb_repair import AdaptiveKBRepairEngine
        return AdaptiveKBRepairEngine(self).export_corrected_kb(output_dir)

    def snapshot(self, output_path: str | Path) -> Dict[str, Any]:
        manifest = _read_json(self.manifest_path, {})
        phases: Dict[str, Any] = {}
        for path in sorted(self.phase_dir.glob("*.json")):
            data = _read_json(path, {})
            phases[path.stem] = {
                "file": str(path),
                "stats": data.get("stats") or {},
                "updated_at": data.get("updated_at"),
            }
        result = {
            "schema_version": self.SCHEMA_VERSION,
            "brain_dir": str(self.root),
            "manifest": str(self.manifest_path),
            "updated_at": manifest.get("updated_at"),
            "policy": self.policy.__dict__,
            "phases": phases,
            "unified_kb": self.unified_kb_status(),
            "adaptive_kb_repair": self.kb_repair_status() if self.policy.self_heal_kb else {"status": "disabled"},
        }
        safe_write_json(Path(output_path), result)
        return result

    def status(self) -> Dict[str, Any]:
        return self.snapshot(self.root / "status_snapshot.json")
