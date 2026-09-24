"""Read-only terminal reproof for a stable HIP phase.

This module is intentionally value-minimal and never clicks/fills/saves.  It exists
for the case where a phase has already reached the requested live state but the
long-running KB/learning coroutine has not yet emitted its normal exact-execution
artifact.  The no-progress watchdog and human-review path can use this probe to
prove the current browser state and hand off without destructively replaying the
form.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .section_judge import build_phase_expectation, DualModelSectionJudge
from .stateful_form_runtime import capture_stateful_controls, compile_phase_state_graph
from .security import mask_sensitive_string


def _basename_any(value: Any) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    # Path on Linux does not split a Windows path, so normalize by syntax.
    if "\\" in text or (len(text) > 1 and text[1:2] == ":"):
        return PureWindowsPath(text).name
    return Path(text).name


def _row_counts(controls: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    seen: Dict[str, set[int]] = {}
    for row in controls:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("row_kind") or "").strip()
        index = row.get("row_index")
        if not kind or index is None:
            continue
        try:
            idx = int(index)
        except Exception:
            continue
        seen.setdefault(kind, set()).add(idx)
    counts = {f"{kind}_rows": len(indexes) for kind, indexes in seen.items()}
    # Historical Document Type exact-judge names.
    if "document_identifier" in seen:
        counts["document_identifier_rows"] = len(seen["document_identifier"])
    if "attribute" in seen:
        counts["attribute_rows"] = len(seen["attribute"])
    return counts


def _upload_nodes(phase_input: Dict[str, Any], phase: str) -> List[Dict[str, Any]]:
    try:
        graph = compile_phase_state_graph(phase_input if isinstance(phase_input, dict) else {}, phase)
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for node in (graph.get("nodes") or []) if isinstance(graph, dict) else []:
        if not isinstance(node, dict) or str(node.get("action") or "") != "upload_file":
            continue
        expected = node.get("expected_value")
        if expected in (None, "", []):
            continue
        rows.append({
            "field": str(node.get("field_key") or node.get("node_id") or "upload_file"),
            "input_path": str(node.get("input_path") or ""),
            "expected_basename": _basename_any(expected),
        })
    return rows


def _without_upload_facts(expected: Dict[str, Any], uploads: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not uploads:
        return expected
    upload_fields = {str(x.get("field") or "").strip().lower() for x in uploads}
    upload_paths = {str(x.get("input_path") or "").strip().lower() for x in uploads if x.get("input_path")}
    out = deepcopy(expected)
    facts = []
    for fact in out.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        field = str(fact.get("field") or "").strip().lower()
        path = str(fact.get("input_path") or "").strip().lower()
        if field in upload_fields or (path and path in upload_paths):
            continue
        facts.append(fact)
    out["facts"] = facts
    return out


async def _live_file_proof(page: Any, uploads: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not uploads:
        return {"required": False, "pass": True, "expected_upload_count": 0, "matched_upload_count": 0}
    try:
        rows = await page.evaluate(
            """
() => Array.from(document.querySelectorAll('input[type=file]')).map(el => ({
  files: Array.from(el.files || []).map(f => String(f.name || '').trim()).filter(Boolean),
  value: String(el.value || '').trim()
}))
"""
        )
    except Exception as exc:
        return {
            "required": True,
            "pass": False,
            "expected_upload_count": len(uploads),
            "matched_upload_count": 0,
            "capture_error": mask_sensitive_string(str(exc))[:500],
        }
    live_names: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for name in row.get("files") or []:
            if str(name or "").strip():
                live_names.add(str(name).strip().lower())
        value_name = _basename_any(row.get("value"))
        if value_name:
            live_names.add(value_name.lower())
    matched_fields: List[str] = []
    missing_fields: List[str] = []
    for upload in uploads:
        expected_name = str(upload.get("expected_basename") or "").lower()
        field = str(upload.get("field") or "upload_file")
        if expected_name and expected_name in live_names:
            matched_fields.append(field)
        else:
            missing_fields.append(field)
    return {
        "required": True,
        "pass": len(missing_fields) == 0,
        "expected_upload_count": len(uploads),
        "matched_upload_count": len(matched_fields),
        "matched_fields": matched_fields,
        "missing_fields": missing_fields,
        "values_stored": False,
    }


async def live_read_only_phase_reproof(
    *,
    page: Any,
    phase: str,
    phase_input: Dict[str, Any],
    judge: Optional[DualModelSectionJudge] = None,
) -> Dict[str, Any]:
    """Prove the *current* live form without taking any physical action.

    This is a terminal/checkpoint probe only. It never authorizes a mutation and
    never persists customer values, selectors, or coordinates.  Exact values are
    compared in-memory and only structural field names/counts are returned.
    """
    deterministic_judge = judge or DualModelSectionJudge()
    try:
        controls = await capture_stateful_controls(page, phase)
    except Exception as exc:
        return {
            "schema_version": "hip.live-read-only-phase-reproof.v1",
            "phase": phase,
            "pass": False,
            "status": "capture_failed",
            "read_only": True,
            "source": "current_live_browser",
            "error": mask_sensitive_string(str(exc))[:500],
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }

    expected = build_phase_expectation(phase_input if isinstance(phase_input, dict) else {}, phase)
    uploads = _upload_nodes(phase_input if isinstance(phase_input, dict) else {}, phase)
    expected_for_fields = _without_upload_facts(expected, uploads)
    actual_state = {
        "controls": controls,
        "row_counts": _row_counts(controls),
        "visible_text": "",
        "source": "current_live_browser_stateful_controls",
    }
    deterministic = deterministic_judge.deterministic_judge(
        expected=expected_for_fields,
        actual_state=actual_state,
        attempts=[],
    )
    upload_proof = await _live_file_proof(page, uploads)
    invalid_fields = sorted({
        str(c.get("label") or c.get("framework_key") or c.get("semantic_key") or "control")
        for c in controls
        if isinstance(c, dict) and str(c.get("aria_invalid") or "").strip().lower() == "true"
    })
    field_pass = bool(deterministic.get("pass"))
    passed = bool(field_pass and upload_proof.get("pass") and not invalid_fields)
    return {
        "schema_version": "hip.live-read-only-phase-reproof.v1",
        "phase": phase,
        "pass": passed,
        "status": "exact_live_state_reproved" if passed else "live_state_not_exact",
        "read_only": True,
        "source": "current_live_browser",
        "deterministic_pass": field_pass,
        "matched_fields": [str(x.get("field") or "") for x in (deterministic.get("matched_values") or []) if isinstance(x, dict)],
        "missing_fields": [str(x.get("field") or "") for x in (deterministic.get("missing_values") or []) if isinstance(x, dict)],
        "row_issue_fields": [str(x.get("field") or "") for x in (deterministic.get("row_issues") or []) if isinstance(x, dict)],
        "invalid_fields": invalid_fields,
        "actual_control_count": int(deterministic.get("actual_control_count") or len(controls)),
        "row_counts": actual_state["row_counts"],
        "required_upload_proof": upload_proof,
        "browser_replay_performed": False,
        "form_mutated": False,
        "values_stored": False,
        "selectors_stored": False,
        "coordinates_stored": False,
    }
