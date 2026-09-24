from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _stable_selector_profile(attempt: Dict[str, Any]) -> Dict[str, Any]:
    diag = attempt.get("binding_diagnostics") if isinstance(attempt.get("binding_diagnostics"), dict) else {}
    control = diag.get("control") if isinstance(diag.get("control"), dict) else {}
    framework_key = (
        control.get("framework_key")
        or control.get("form_control_name")
        or control.get("formControlName")
        or control.get("name")
        or attempt.get("framework_key")
        or ""
    )
    label = attempt.get("label") or control.get("label") or ""
    role = control.get("role") or (attempt.get("control_kind") or {}).get("role") or ""
    tag = control.get("tag") or (attempt.get("control_kind") or {}).get("tag") or ""
    occurrence = attempt.get("occurrence")
    raw_selector = str(attempt.get("selector") or control.get("selector") or "")
    # Dynamic DDS IDs are useful as evidence but must never be the primary replay path.
    dynamic_id = bool(re.search(r"dds-form-field-\d+", raw_selector, flags=re.I))
    candidates: List[Dict[str, Any]] = []
    if framework_key:
        candidates.append({"strategy": "framework_key", "selector": f'[formcontrolname="{framework_key}"]', "key": framework_key})
        candidates.append({"strategy": "name", "selector": f'[name="{framework_key}"]', "key": framework_key})
    if role and label:
        candidates.append({"strategy": "role_and_accessible_name", "role": role, "name": label, "occurrence": occurrence})
    if label:
        candidates.append({"strategy": "label_occurrence", "label": label, "occurrence": occurrence})
    if raw_selector:
        candidates.append({"strategy": "last_known_selector", "selector": raw_selector, "dynamic": dynamic_id})
    return mask_sensitive_data({
        "framework_key": framework_key,
        "label": label,
        "role": role,
        "tag": tag,
        "occurrence": occurrence,
        "section": attempt.get("section") or control.get("section") or "",
        "row_kind": attempt.get("row_kind") or "",
        "row_index": attempt.get("row_index"),
        "semantic_identity": diag.get("selected_identity") or control.get("semantic_identity") or "",
        "candidates": candidates,
        "values_stored": False,
    })


def _interaction_profile(attempt: Dict[str, Any]) -> Dict[str, Any]:
    proof = attempt.get("transaction_proof") if isinstance(attempt.get("transaction_proof"), dict) else {}
    control_kind = attempt.get("control_kind") if isinstance(attempt.get("control_kind"), dict) else {}
    return mask_sensitive_data({
        "executor": attempt.get("executor") or attempt.get("method") or "",
        "dom_events": list(attempt.get("dom_events") or []),
        "control_kind": control_kind,
        "rebound_after_parent": bool(attempt.get("rebound_after_action_type") or attempt.get("rebound_after_parent")),
        "explicit_event_proof": bool(proof.get("event_proof") or proof.get("events") or attempt.get("clicked_event_seen")),
        "protected_state_check": bool(proof.get("protected_state_checked") or proof.get("protected_state_changes") is not None),
        "wait_profile": {
            "conditional_child_visibility": bool(attempt.get("conditional_child_wait") or attempt.get("rebound_after_action_type") or attempt.get("rebound_after_parent")),
            "overlay_settle": bool(attempt.get("settle_before") or attempt.get("settle_after")),
            "exact_value_verification": bool(attempt.get("exact_verified") or attempt.get("success")),
        },
        "values_stored": False,
    })


def _execution_candidates(phase_dir: Path) -> Iterable[Path]:
    preferred = []
    for path in phase_dir.rglob("*.json"):
        name = path.name.lower()
        if "target_branch_execution" in name or "state_graph_execution" in name:
            preferred.append(path)
    return sorted(preferred, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def _find_execution(phase_dir: Path) -> tuple[Dict[str, Any], str]:
    for path in _execution_candidates(phase_dir):
        data = _read_json(path)
        if isinstance(data.get("attempts"), list):
            return data, str(path)
        nested = data.get("stateful_target_branch_execution")
        if isinstance(nested, dict) and isinstance(nested.get("attempts"), list):
            return nested, str(path)
    return {}, ""


def build_validated_phase_trajectory(
    *,
    phase: str,
    phase_dir: str | Path,
    attempt: int,
    verification: Dict[str, Any],
    judge_result: Dict[str, Any],
    golden_references: Optional[List[Dict[str, Any]]] = None,
    portal_learning_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write a judge-gated, value-free deterministic replay trajectory."""
    phase_dir = Path(phase_dir)
    execution, execution_path = _find_execution(phase_dir)
    attempts = execution.get("attempts") if isinstance(execution.get("attempts"), list) else []
    dependency_contract = execution.get("dependency_execution_contract") if isinstance(execution.get("dependency_execution_contract"), dict) else {}
    dependency_map = dependency_contract.get("dependency_map") if isinstance(dependency_contract.get("dependency_map"), dict) else {}
    level_map = dependency_contract.get("dependency_levels") if isinstance(dependency_contract.get("dependency_levels"), dict) else {}
    wait_map = dependency_contract.get("wait_profiles") if isinstance(dependency_contract.get("wait_profiles"), dict) else {}
    steps: List[Dict[str, Any]] = []
    for row in attempts:
        if not isinstance(row, dict) or not bool(row.get("success")):
            continue
        field = row.get("field") or row.get("field_key") or ""
        step = {
            "order": len(steps) + 1,
            "node_id": row.get("node_id") or "",
            "field_key": field,
            "section": row.get("section") or "",
            "row_kind": row.get("row_kind") or "",
            "row_index": row.get("row_index"),
            "action": row.get("action") or row.get("executor") or "",
            "selector_profile": _stable_selector_profile(row),
            "interaction_profile": _interaction_profile(row),
            "parent_node_ids": list(dependency_map.get(str(row.get("node_id") or "")) or row.get("dependencies") or []),
            "dependency_level": level_map.get(str(row.get("node_id") or ""), 0),
            "dependency_wait_profile": wait_map.get(str(row.get("node_id") or ""), {}),
            "execution_mode": dependency_contract.get("mode") or "exploration",
            "transition_proof": {
                "dom_transition": row.get("dom_transition") or {},
                "conditional_child_visibility": ((row.get("transaction_proof") or {}).get("conditional_child_visibility") if isinstance(row.get("transaction_proof"), dict) else {}),
                "protected_state_changes": ((row.get("transaction_proof") or {}).get("protected_state_changes") if isinstance(row.get("transaction_proof"), dict) else []),
            },
            "success": True,
            "values_stored": False,
        }
        steps.append(mask_sensitive_data(step))

    learning = portal_learning_summary or {}
    page_model = learning.get("page_model") if isinstance(learning.get("page_model"), dict) else {}
    payload = mask_sensitive_data({
        "schema_version": "hip.validated-deterministic-trajectory.v1",
        "phase": phase,
        "attempt": int(attempt),
        "trust": "validated" if bool(judge_result.get("pass", True)) else "candidate",
        "judge_pass": bool(judge_result.get("pass", True)),
        "deterministic_verification_status": verification.get("status"),
        "execution_artifact": execution_path,
        "execution_pass": bool(execution.get("pass") or execution.get("status") == "pass"),
        "one_to_one_pass": bool((execution.get("final_form_state_model") or {}).get("one_to_one_pass", True)) if isinstance(execution.get("final_form_state_model"), dict) else True,
        "page_fingerprint": page_model.get("fingerprint") or "",
        "url_template": page_model.get("url_template") or "",
        "parent_child_contract": {
            "schema_version": dependency_contract.get("schema_version"),
            "contract_fingerprint": dependency_contract.get("contract_fingerprint"),
            "mode": dependency_contract.get("mode"),
            "ordered_node_ids": dependency_contract.get("ordered_node_ids") or [],
            "dependency_map": dependency_contract.get("dependency_map") or {},
            "section_sequence": dependency_contract.get("section_sequence") or [],
            "scheduler_policy": dependency_contract.get("scheduler_policy") or {},
            "values_stored": False,
        },
        "ordered_steps": steps,
        "golden_references": [
            {"file": Path(str(g.get("path") or g.get("file") or "")).name, "role": g.get("role") or "golden_correctly_filled_reference"}
            for g in (golden_references or []) if isinstance(g, dict)
        ],
        "evidence_channels": [
            "local_playwright_dom",
            "playwright_mcp_accessibility_network_console",
            "chrome_devtools_mcp_dom_network_console",
            "action_state_transitions",
            "deterministic_exact_state",
            "text_judge",
            "vision_judge",
            "golden_state_comparison",
        ],
        "replay_policy": {
            "exploit_validated_path_first": True,
            "explore_only_on_drift_or_failure": True,
            "rebind_after_angular_rerender": True,
            "verify_each_committed_field": True,
            "final_mutations_prohibited": True,
            "values_reloaded_from_current_input": True,
            "parent_child_sequence_is_authoritative": True,
            "parallel_field_mutation_prohibited": True,
            "state_proof_accepts_committed_success_after_transport_timeout": True,
        },
        "values_stored": False,
        "captured_at": utc_now(),
    })
    material = json.dumps(
        {"steps": payload.get("ordered_steps") or [], "contract": payload.get("parent_child_contract") or {}},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    payload["trajectory_fingerprint"] = hashlib.sha256(material.encode("utf-8", errors="ignore")).hexdigest()[:24]
    out = phase_dir / "validated_deterministic_trajectory.json"
    safe_write_json(out, payload)
    return {**payload, "path": str(out)}
