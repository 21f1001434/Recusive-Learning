from __future__ import annotations

"""HIP-specific semantic website intelligence exposed through HIP Intelligence MCP.

This module is deliberately browser-free.  Callers pass the current, value-bounded
surface snapshot captured by the existing Playwright/DOM runtime.  The helpers
interpret that snapshot and return deterministic website-specific knowledge without
becoming a second browser executor.
"""

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, Mapping, Sequence

from .security import mask_sensitive_data
from .semantic_control import (
    control_fingerprint,
    rank_semantic_candidates,
    semantic_control_id,
    verify_semantic_effect,
)


def _norm(value: Any) -> str:
    text = re.sub(r"[_\-/]+", " ", str(value or "").strip().lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


SAFE_ACTION_WORDS = {
    "search", "filter", "view", "details", "detail", "expand", "collapse", "next",
    "continue", "back", "previous", "cancel", "tab", "open", "close", "add row",
    "add condition", "add attribute", "add routing", "choose", "select", "edit field",
}
CONDITIONAL_ACTION_WORDS = {
    "save", "create", "submit", "update", "finish", "deploy", "confirm", "enable",
    "disable", "clone", "migrate", "publish",
}
DANGEROUS_ACTION_WORDS = {
    "delete", "remove", "overwrite", "purge", "destroy", "reset", "revoke",
}


def classify_action_risk(label: Any, *, role: Any = "", structural_opener: bool = False) -> Dict[str, Any]:
    """Classify a control into SAFE / CONDITIONAL / DANGEROUS.

    The classification does not itself authorize the action.  Existing BrowserSession
    mutation governance remains the authority.  This is semantic evidence consumed by
    that authorization layer and exposed through HIP Intelligence MCP.
    """
    text = _norm(label)
    if structural_opener and any(word in text for word in ("create", "add", "new")):
        return {"class": "SAFE", "reason": "explicit non-committing structural opener", "requires_authorization": False}
    if any(word in text for word in DANGEROUS_ACTION_WORDS):
        return {"class": "DANGEROUS", "reason": "destructive mutation label", "requires_authorization": True}
    if any(word in text for word in CONDITIONAL_ACTION_WORDS):
        return {"class": "CONDITIONAL", "reason": "committing/mutating action label", "requires_authorization": True}
    if any(word in text for word in SAFE_ACTION_WORDS):
        return {"class": "SAFE", "reason": "read/form-navigation action", "requires_authorization": False}
    role_norm = _norm(role)
    if role_norm in {"textbox", "combobox", "checkbox", "radio", "option", "tab", "searchbox"}:
        return {"class": "SAFE", "reason": "non-committing form control", "requires_authorization": False}
    return {"class": "SAFE", "reason": "no destructive/committing semantic detected", "requires_authorization": False}


def _surface(args: Mapping[str, Any]) -> Dict[str, Any]:
    raw = args.get("surface") or args.get("state") or args.get("current_surface") or {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _controls(surface: Mapping[str, Any]) -> list[Dict[str, Any]]:
    rows = surface.get("controls") or []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _compact_control(control: Mapping[str, Any], *, include_current: bool = False) -> Dict[str, Any]:
    row = {
        "semantic_id": str(control.get("semantic_control_id") or semantic_control_id(control)),
        "control_fingerprint": str(control.get("control_fingerprint") or control_fingerprint(control)),
        "role": control.get("role") or control.get("tag") or "",
        "label": control.get("label") or control.get("aria_label") or "",
        "section": control.get("section") or "",
        "required": bool(control.get("required")),
        "enabled": bool(control.get("enabled", True)),
        "readonly": bool(control.get("readonly")),
        "visible": bool(control.get("visible", True)),
        "name": control.get("name") or "",
        "framework_key": control.get("framework_key") or "",
        "testid": control.get("testid") or "",
        "aria_controls": control.get("aria_controls") or "",
        "aria_owns": control.get("aria_owns") or "",
        "aria_haspopup": control.get("aria_haspopup") or "",
        "row_kind": control.get("row_kind") or "",
        "row_index": control.get("row_index"),
        "has_value": bool(control.get("has_value")),
        "selector_generation_volatile": bool(control.get("selector_generation_volatile")),
    }
    if include_current:
        # Values are ephemeral and masked by the standard security layer.  They are
        # never written by this module or the HIP MCP memory service.
        row["current_value"] = control.get("value") if "value" in control else None
        row["selected_values"] = control.get("selected_values") if "selected_values" in control else None
    return mask_sensitive_data(row)


def hip_get_current_surface(args: Mapping[str, Any]) -> Dict[str, Any]:
    surface = _surface(args)
    controls = _controls(surface)
    return mask_sensitive_data({
        "status": "ok" if surface else "unavailable",
        "page": args.get("page") or args.get("phase") or "",
        "url_path": surface.get("url_path") or "",
        "dom_generation": int(surface.get("dom_generation") or 0),
        "control_count": len(controls),
        "visible_dialog_count": int(surface.get("visible_dialog_count") or 0),
        "visible_drawer_count": int(surface.get("visible_drawer_count") or 0),
        "visible_listbox_count": int(surface.get("visible_listbox_count") or 0),
        "state_fingerprint": surface.get("state_fingerprint") or "",
        "sections": sorted({str(c.get("section") or "") for c in controls if c.get("section")}),
        "values_stored": False,
    })


def hip_get_form_schema(args: Mapping[str, Any]) -> Dict[str, Any]:
    surface = _surface(args)
    controls = _controls(surface)
    schema = [_compact_control(c) for c in controls]
    return {
        "status": "ok" if controls else "empty",
        "page": args.get("page") or args.get("phase") or "",
        "form": args.get("form") or "",
        "dom_generation": int(surface.get("dom_generation") or 0),
        "controls": schema,
        "required_count": sum(1 for c in schema if c.get("required")),
        "values_stored": False,
    }


def hip_find_control(args: Mapping[str, Any], *, memory: Any = None) -> Dict[str, Any]:
    surface = _surface(args)
    candidates = args.get("candidates") or _controls(surface)
    expected_label = str(args.get("field") or args.get("expected_label") or args.get("label") or "")
    expected_section = str(args.get("section") or args.get("expected_section") or "")
    expected_type = _norm(args.get("expected_type") or "")
    action = str(args.get("action") or ("select" if "select" in expected_type or "combo" in expected_type else "fill"))
    ranked = rank_semantic_candidates(
        candidates=[dict(x) for x in candidates if isinstance(x, Mapping)],
        action=action,
        expected_label=expected_label,
        expected_section=expected_section,
        accessibility_text=str(args.get("accessibility_text") or ""),
        devtools_text=str(args.get("devtools_text") or ""),
        memory=memory,
        phase=str(args.get("phase") or ""),
    )
    compact = []
    for row in ranked[:8]:
        cand = dict(row.get("candidate") or {})
        compact.append({
            "score": float(row.get("score") or 0.0),
            "semantic_id": semantic_control_id(cand) if cand else "",
            "control_fingerprint": control_fingerprint(cand) if cand else "",
            "control": _compact_control(cand),
            "reasons": list(row.get("reasons") or []),
            "source_scores": row.get("source_scores") or {},
        })
    top = compact[0] if compact else {}
    second = float(compact[1].get("score") or 0.0) if len(compact) > 1 else 0.0
    score = float(top.get("score") or 0.0)
    margin = score - second
    if not top:
        status = "no_match"
    elif margin < 0.08:
        status = "ambiguous"
    elif score >= 0.90:
        status = "unique_match"
    elif score >= 0.75:
        status = "reobserve"
    elif score >= 0.55:
        status = "rediscover"
    else:
        status = "low_confidence"
    return {
        "status": status,
        "confidence": round(score, 4),
        "margin": round(margin, 4),
        "semantic_control_id": top.get("semantic_id") or "",
        "control_fingerprint": top.get("control_fingerprint") or "",
        "candidate": (ranked[0].get("candidate") if ranked else {}) or {},
        "control": top.get("control") or {},
        "ranked": compact,
        "values_stored": False,
    }


def hip_find_owned_popup(args: Mapping[str, Any]) -> Dict[str, Any]:
    control = dict(args.get("control") or args.get("candidate") or {}) if isinstance(args.get("control") or args.get("candidate") or {}, Mapping) else {}
    surfaces = [dict(x) for x in (args.get("popups") or args.get("surfaces") or []) if isinstance(x, Mapping)]
    owner_ids = [str(control.get("aria_controls") or ""), str(control.get("aria_owns") or "")]
    owner_ids = [x for x in owner_ids if x]
    matched = []
    for row in surfaces:
        rid = str(row.get("id") or row.get("surface_id") or "")
        if rid and rid in owner_ids:
            matched.append(row)
    status = "unique_match" if len(matched) == 1 else ("ambiguous" if len(matched) > 1 else ("owner_declared" if owner_ids else "not_found"))
    return mask_sensitive_data({
        "status": status,
        "owner_ids": owner_ids,
        "owned_surface_visible": bool(control.get("owned_surface_visible")),
        "owned_surface_role": control.get("owned_surface_role") or "",
        "matches": matched[:8],
        "values_stored": False,
    })


def hip_get_repeatable_rows(args: Mapping[str, Any]) -> Dict[str, Any]:
    controls = _controls(_surface(args))
    groups: Dict[tuple[str, int], list[Dict[str, Any]]] = defaultdict(list)
    for c in controls:
        if c.get("row_index") is None:
            continue
        groups[(str(c.get("row_kind") or "row"), int(c.get("row_index") or 0))].append(_compact_control(c))
    rows = [
        {"row_kind": kind, "row_index": index, "controls": items, "control_count": len(items)}
        for (kind, index), items in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1]))
    ]
    return {"status": "ok", "rows": rows, "row_count": len(rows), "values_stored": False}


def hip_get_required_fields(args: Mapping[str, Any]) -> Dict[str, Any]:
    controls = [_compact_control(c) for c in _controls(_surface(args)) if bool(c.get("required"))]
    return {"status": "ok", "required_fields": controls, "count": len(controls), "values_stored": False}


def hip_get_current_values(args: Mapping[str, Any]) -> Dict[str, Any]:
    controls = [_compact_control(c, include_current=True) for c in _controls(_surface(args))]
    return {
        "status": "ok",
        "fields": controls,
        "count": len(controls),
        "values_stored": False,
        "persistence": "none; values are returned ephemerally and security-masked",
    }


def hip_compare_expected_actual(args: Mapping[str, Any]) -> Dict[str, Any]:
    expected = args.get("expected") or {}
    actual = args.get("actual") or {}
    if not isinstance(expected, Mapping):
        expected = {}
    if not isinstance(actual, Mapping):
        actual = {}
    keys = sorted(set(map(str, expected.keys())) | set(map(str, actual.keys())))
    comparisons = []
    for key in keys:
        ev = expected.get(key)
        av = actual.get(key)
        equal = _norm(ev) == _norm(av)
        comparisons.append({"field": key, "match": equal, "expected_present": ev not in (None, ""), "actual_present": av not in (None, "")})
    return {
        "status": "match" if comparisons and all(x["match"] for x in comparisons) else ("empty" if not comparisons else "mismatch"),
        "comparisons": comparisons,
        "match_count": sum(1 for x in comparisons if x["match"]),
        "mismatch_count": sum(1 for x in comparisons if not x["match"]),
        "raw_values_stored": False,
    }


def hip_get_safe_actions(args: Mapping[str, Any]) -> Dict[str, Any]:
    controls = _controls(_surface(args))
    rows = []
    for c in controls:
        label = c.get("label") or c.get("aria_label") or c.get("title") or ""
        structural = bool(c.get("safety_structural_opener"))
        risk = classify_action_risk(label, role=c.get("role"), structural_opener=structural)
        rows.append({
            "semantic_id": c.get("semantic_control_id") or semantic_control_id(c),
            "label": label,
            "role": c.get("role") or c.get("tag") or "",
            "risk": risk,
        })
    return {
        "status": "ok",
        "safe": [r for r in rows if r["risk"]["class"] == "SAFE"],
        "conditional": [r for r in rows if r["risk"]["class"] == "CONDITIONAL"],
        "dangerous": [r for r in rows if r["risk"]["class"] == "DANGEROUS"],
        "authorization_is_external": True,
        "values_stored": False,
    }


def hip_verify_action_effect(args: Mapping[str, Any]) -> Dict[str, Any]:
    return verify_semantic_effect(
        before=args.get("before") or {},
        after=args.get("after") or {},
        action=str(args.get("action") or "click"),
        target_before=args.get("target_before") or {},
        target_after=args.get("target_after") or {},
        exact_value_verified=bool(args.get("exact_value_verified")),
    )


def hip_get_route_identity(args: Mapping[str, Any]) -> Dict[str, Any]:
    surface = _surface(args)
    path = str(surface.get("url_path") or args.get("url_path") or "")
    text = _norm(" ".join(map(str, args.get("headings") or [])))
    path_norm = _norm(path)
    families = [
        ("data_maps", ("data maps", "datamaps", "data map")),
        ("document_types", ("document types", "doctype", "document type")),
        ("rules", ("rules", "mapping rules", "rule")),
        ("transport_profiles", ("transport profiles", "transport profile")),
        ("bizflow", ("biz flows", "bizflow", "biz exchange", "business flow")),
    ]
    matches = []
    combined = f"{path_norm} {text}"
    for family, terms in families:
        if any(_norm(term) in combined for term in terms):
            matches.append(family)
    return {
        "status": "unique_match" if len(matches) == 1 else ("ambiguous" if len(matches) > 1 else "unknown"),
        "route_identity": matches[0] if len(matches) == 1 else "",
        "url_path": path,
        "matches": matches,
        "values_stored": False,
    }


def hip_get_form_generation(args: Mapping[str, Any]) -> Dict[str, Any]:
    surface = _surface(args)
    return {
        "status": "ok" if surface else "unavailable",
        "dom_generation": int(surface.get("dom_generation") or 0),
        "state_fingerprint": surface.get("state_fingerprint") or "",
        "values_stored": False,
    }


HIP_SEMANTIC_TOOL_NAMES = [
    "hip_get_current_surface",
    "hip_get_form_schema",
    "hip_find_control",
    "hip_find_owned_popup",
    "hip_get_repeatable_rows",
    "hip_get_required_fields",
    "hip_get_current_values",
    "hip_compare_expected_actual",
    "hip_get_safe_actions",
    "hip_verify_action_effect",
    "hip_get_route_identity",
    "hip_get_form_generation",
]
