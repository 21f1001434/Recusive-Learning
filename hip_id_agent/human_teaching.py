from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.human-teaching.v1"


def _norm(value: Any) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _stable(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(x or "") for x in parts).encode("utf-8", errors="ignore")).hexdigest()[:24]


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(mask_sensitive_data(dict(row)), ensure_ascii=False, sort_keys=True, default=str) + "\n")


class HumanTeachingStore:
    """Value-free human teaching memory for supervised portal recovery.

    The operator teaches *where* an input path belongs by selecting a semantic live
    control. Customer values, selectors, XPath and coordinates are never persisted.
    On the next retry the mapping is treated as a strong semantic hint, but the live
    page and exact readback remain authoritative.
    """

    def __init__(self, root: str | Path, config: Any = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.teachings_path = self.root / "teachings.jsonl"
        self.pending_dir = self.root / "pending"
        self.pending_dir.mkdir(parents=True, exist_ok=True)

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    @staticmethod
    def _control_identity(control: Mapping[str, Any]) -> Dict[str, Any]:
        return mask_sensitive_data({
            "semantic_control_id": str(control.get("semantic_control_id") or ""),
            "label": str(control.get("label") or ""),
            "section": str(control.get("section") or ""),
            "role": str(control.get("role") or control.get("type") or ""),
            "name": str(control.get("name") or control.get("framework_key") or control.get("form_control_name") or ""),
            "placeholder": str(control.get("placeholder") or ""),
            "row_index": control.get("row_index"),
            "row_kind": str(control.get("row_kind") or ""),
        })

    def create_request(
        self,
        *,
        run_id: str,
        task: str,
        input_root: str,
        unresolved_input_paths: Sequence[str],
        controls: Sequence[Mapping[str, Any]],
        reason: str,
        screenshot_path: str = "",
    ) -> Dict[str, Any]:
        request_id = _stable(run_id, task, input_root, *sorted(str(x) for x in unresolved_input_paths))
        payload = {
            "schema_version": SCHEMA,
            "request_id": request_id,
            "run_id": str(run_id or ""),
            "created_at": utc_now(),
            "status": "needs_assistance",
            "task": str(task or ""),
            "input_root": str(input_root or ""),
            "unresolved_input_paths": [str(x) for x in unresolved_input_paths if str(x)],
            "candidate_controls": [self._control_identity(x) for x in controls if isinstance(x, Mapping)],
            "reason": mask_sensitive_string(str(reason or ""))[:1000],
            "screenshot_path": str(screenshot_path or ""),
            "instruction": "Choose the unresolved input path, then click/select the live semantic control where that value belongs.",
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }
        safe_write_json(self.pending_dir / f"{request_id}.json", payload)
        return payload

    def pending(self, *, run_id: str = "") -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path in sorted(self.pending_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(row, dict) or row.get("status") != "needs_assistance":
                continue
            if run_id and str(row.get("run_id") or "") != str(run_id):
                continue
            rows.append(row)
        return rows

    def submit(
        self,
        *,
        request_id: str,
        input_path: str,
        semantic_control_id: str = "",
        control_label: str = "",
        section: str = "",
        role: str = "",
        note: str = "",
        verified_by_human: bool = True,
    ) -> Dict[str, Any]:
        pending_path = self.pending_dir / f"{request_id}.json"
        try:
            request = json.loads(pending_path.read_text(encoding="utf-8"))
        except Exception:
            request = {}
        if not request:
            raise ValueError(f"Unknown human-assistance request: {request_id}")
        if str(input_path or "") not in {str(x) for x in request.get("unresolved_input_paths") or []}:
            raise ValueError("input_path is not part of this assistance request")
        candidate = None
        for row in request.get("candidate_controls") or []:
            if not isinstance(row, Mapping):
                continue
            if semantic_control_id and str(row.get("semantic_control_id") or "") == str(semantic_control_id):
                candidate = dict(row); break
            if control_label and _norm(row.get("label")) == _norm(control_label):
                candidate = dict(row); break
        candidate = candidate or {
            "semantic_control_id": semantic_control_id,
            "label": control_label,
            "section": section,
            "role": role,
        }
        teaching = {
            "schema_version": SCHEMA,
            "teaching_id": _stable(request.get("task"), request.get("input_root"), input_path, candidate.get("semantic_control_id"), candidate.get("label")),
            "request_id": request_id,
            "run_id": request.get("run_id"),
            "recorded_at": utc_now(),
            "task_terms": sorted({_norm(x) for x in str(request.get("task") or "").split() if _norm(x)}),
            "input_root": str(request.get("input_root") or ""),
            "input_path": str(input_path or ""),
            "control": self._control_identity(candidate),
            "note": mask_sensitive_string(str(note or ""))[:500],
            "verified_by_human": bool(verified_by_human),
            "success_count": 0,
            "failure_count": 0,
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }
        _append_jsonl(self.teachings_path, teaching)
        request["status"] = "resolved"
        request["resolved_at"] = utc_now()
        request["resolution"] = {"input_path": input_path, "control": teaching["control"], "verified_by_human": bool(verified_by_human)}
        safe_write_json(pending_path, request)
        return teaching

    def _all_teachings(self) -> List[Dict[str, Any]]:
        if not self.teachings_path.is_file():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            for line in self.teachings_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict): rows.append(row)
        except Exception:
            return []
        return rows

    def blueprint(self, *, task: str, input_root: str = "") -> Dict[str, Any]:
        task_tokens = {_norm(x) for x in str(task or "").split() if _norm(x)}
        fields: List[Dict[str, Any]] = []
        for row in self._all_teachings():
            if not row.get("verified_by_human"):
                continue
            root = str(row.get("input_root") or "")
            if input_root and root and root != input_root:
                continue
            learned = set(str(x) for x in row.get("task_terms") or [])
            overlap = len(task_tokens & learned) / max(1, len(task_tokens | learned)) if (task_tokens or learned) else 1.0
            if overlap < float(self._cfg("min_task_similarity", 0.35) or 0.35):
                continue
            control = row.get("control") if isinstance(row.get("control"), Mapping) else {}
            fields.append({
                "input_path": str(row.get("input_path") or ""),
                "field_key": str(row.get("input_path") or "").rsplit(".", 1)[-1],
                "action": "",
                "section": str(control.get("section") or ""),
                "row_kind": str(control.get("row_kind") or ""),
                "row_index": control.get("row_index"),
                "semantic_locator": {
                    "names": [str(control.get("name") or "")] if control.get("name") else [],
                    "labels": [str(control.get("label") or "")] if control.get("label") else [],
                    "placeholders": [str(control.get("placeholder") or "")] if control.get("placeholder") else [],
                    "roles": [str(control.get("role") or "")] if control.get("role") else [],
                    "section_aliases": [str(control.get("section") or "")] if control.get("section") else [],
                    "row_kind": str(control.get("row_kind") or ""),
                    "row_index": control.get("row_index"),
                    "semantic_control_id": str(control.get("semantic_control_id") or ""),
                },
                "verification": "human_taught_then_exact_live_readback",
                "teaching_id": row.get("teaching_id"),
            })
        return {
            "schema_version": "hip.human-taught-form-blueprint.v1",
            "input_root": input_root,
            "field_count": len(fields),
            "fields": fields,
            "human_taught": True,
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
            "live_reproof_required": True,
        }

    def record_outcome(self, *, input_paths: Iterable[str], success: bool) -> None:
        # Append-only audit feedback: no in-place secret-bearing state.
        _append_jsonl(self.root / "teaching_outcomes.jsonl", {
            "schema_version": "hip.human-teaching-outcome.v1",
            "recorded_at": utc_now(),
            "input_paths": [str(x) for x in input_paths],
            "success": bool(success),
        })

    def manifest(self) -> Dict[str, Any]:
        teachings = self._all_teachings()
        return {
            "schema_version": SCHEMA,
            "enabled": bool(self._cfg("enabled", True)),
            "teaching_count": len(teachings),
            "pending_count": len(self.pending()),
            "root": str(self.root),
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }


def human_teaching_from_config(app_config: Any) -> HumanTeachingStore:
    root = Path(app_config.reporting.memory_dir) / str(getattr(app_config.human_in_the_loop, "memory_subdir", "human_teaching") or "human_teaching")
    return HumanTeachingStore(root, app_config.human_in_the_loop)
