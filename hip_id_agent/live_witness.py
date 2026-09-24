from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, Mapping

from .form_api_agent import MUTATING_METHODS, classify_endpoint, endpoint_template
from .security import mask_sensitive_string

# Controls that may commit or initiate a portal-side change.  '+ Add' row/form
# openers are intentionally not listed: witness mode must exercise real dynamic
# forms and repeatable rows while remaining no-save.
_MUTATION_CONTROL_RE = re.compile(
    r"\b(create|save|submit|finish|deploy|delete|remove|publish|update|migrate|clone)\b",
    re.IGNORECASE,
)


def _row(value: Any) -> Dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    return dict(value) if isinstance(value, Mapping) else {}


def build_live_witness_report(
    *,
    action_events: Iterable[Any],
    network_events: Iterable[Any],
    click_events: Iterable[Any] = (),
    mission_complete: bool,
    phase_count: int,
) -> Dict[str, Any]:
    """Build a fail-closed certificate for a non-mutating live witness run.

    The certificate stores only structural action labels and endpoint templates;
    request bodies, credentials, cookies and user-entered values are excluded.
    """
    click_violations = []
    click_count = 0
    for raw in action_events or []:
        event = _row(raw)
        if str(event.get("type") or "").lower() != "click":
            continue
        click_count += 1
        target = str(event.get("target") or "")
        if _MUTATION_CONTROL_RE.search(target):
            click_violations.append({
                "action_id": str(event.get("action_id") or ""),
                "target": mask_sensitive_string(target)[:500],
                "stage": mask_sensitive_string(str(event.get("stage") or ""))[:240],
                "backend": str(event.get("backend") or ""),
            })

    # Independent capture-phase DOM telemetry catches direct/helper clicks that
    # never emitted a governed ActionEvent. A blocked mutation attempt is still a
    # witness failure: preventing tenant damage is not the same as correct agent behavior.
    dom_click_violations = []
    dom_click_count = 0
    for raw in click_events or []:
        event = _row(raw)
        if str(event.get("source") or "").lower() != "dom":
            continue
        dom_click_count += 1
        extra = _row(event.get("extra") or {})
        action_text = str(extra.get("safety_action_text") or event.get("text") or "")
        blocked = bool(extra.get("safety_blocked"))
        structural = bool(extra.get("safety_structural_opener"))
        authorized = bool(extra.get("safety_authorized"))
        mutating = bool(_MUTATION_CONTROL_RE.search(action_text))
        if blocked or (mutating and not structural):
            dom_click_violations.append({
                "target": mask_sensitive_string(action_text)[:500],
                "stage": mask_sensitive_string(str(extra.get("stage") or ""))[:240],
                "selector": mask_sensitive_string(str(event.get("selector") or extra.get("safety_action_selector") or ""))[:500],
                "safety_blocked": blocked,
                "safety_authorized": authorized,
                "safety_structural_opener": structural,
                "authorization_task_id": mask_sensitive_string(str(extra.get("safety_authorization_task_id") or ""))[:240],
                "reason": "blocked_mutation_attempt" if blocked else "dom_mutation_control_attempt",
            })

    request_violations = []
    request_count = 0
    for raw in network_events or []:
        event = _row(raw)
        method = str(event.get("method") or "GET").upper()
        url = str(event.get("url") or "")
        if not url.lower().startswith(("http://", "https://")):
            continue
        request_count += 1
        if method not in MUTATING_METHODS:
            continue
        kind = classify_endpoint(method, url, event.get("request_body_redacted"))
        # HIP commonly uses POST for list/search/validate operations. These are
        # read-only observations and are not mutation violations.
        if kind in {"validation", "search"}:
            continue
        request_violations.append({
            "method": method,
            "endpoint_template": endpoint_template(url),
            "endpoint_kind": kind,
            "status": event.get("status"),
            "stage": mask_sensitive_string(str(event.get("stage") or ""))[:240],
        })

    safety_pass = not click_violations and not dom_click_violations and not request_violations
    passed = bool(mission_complete and safety_pass and int(phase_count or 0) > 0)
    return {
        "schema_version": "hip.live-witness-certificate.v1",
        "pass": passed,
        "decision": "WITNESS_PASS" if passed else "WITNESS_FAIL",
        "mission_complete": bool(mission_complete),
        "selected_phase_count": int(phase_count or 0),
        "safety_pass": safety_pass,
        "observed_click_count": click_count,
        "observed_request_count": request_count,
        "observed_dom_click_count": dom_click_count,
        "mutation_control_click_count": len(click_violations),
        "dom_mutation_control_click_count": len(dom_click_violations),
        "mutating_request_count": len(request_violations),
        "mutation_control_clicks": click_violations[:50],
        "dom_mutation_control_clicks": dom_click_violations[:50],
        "mutating_requests": request_violations[:50],
        "policy": {
            "submit_capture_enabled": False,
            "api_write_allowed": False,
            "portal_mutation_controls_allowed": False,
            "dynamic_add_openers_allowed": True,
            "repeatable_row_add_allowed": True,
            "request_bodies_stored_here": False,
            "credentials_stored_here": False,
        },
    }
