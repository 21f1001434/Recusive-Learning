from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

from .security import mask_sensitive_data


MUTATION_OUTCOME_SCHEMA = "hip.mutation-outcome-reconciliation.v1"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _status(row: Mapping[str, Any]) -> int | None:
    try:
        value = row.get("status")
        return int(value) if value is not None else None
    except Exception:
        return None


def classify_mutation_outcome(
    *,
    dispatch: Mapping[str, Any] | None,
    network_rows: Sequence[Mapping[str, Any]] | None,
    ui_signal: Mapping[str, Any] | None = None,
    structural_change: bool = False,
    action_error: str = "",
) -> Dict[str, Any]:
    """Classify a mutation without ever recommending a second backend dispatch.

    A single exception from a browser tool is not evidence that a mutation failed:
    the click may already have reached HIP.  This classifier therefore separates
    pre-dispatch failures from verified responses and indeterminate outcomes.
    """
    dispatch = dict(dispatch or {})
    ui_signal = dict(ui_signal or {})
    rows = [dict(row) for row in (network_rows or []) if isinstance(row, Mapping)]
    uncorrelated_writes = [
        row for row in rows
        if str(row.get("method") or "").upper() in WRITE_METHODS and row.get("caused_by_dispatch") is False
    ]
    writes = [
        row for row in rows
        if str(row.get("method") or "").upper() in WRITE_METHODS and row.get("caused_by_dispatch") is not False
    ]
    terminal = [row for row in writes if _status(row) is not None]
    successes = [row for row in terminal if 200 <= int(_status(row) or 0) < 300]
    failures = [row for row in terminal if int(_status(row) or 0) >= 400]
    redirects = [row for row in terminal if 300 <= int(_status(row) or 0) < 400]
    pending = [row for row in writes if _status(row) is None]

    dispatch_attempted = bool(dispatch.get("dispatch_attempted"))
    dispatch_returned = bool(dispatch.get("dispatch_returned"))
    ui_success = bool(ui_signal.get("success"))
    ui_error = bool(ui_signal.get("error"))

    classification = "indeterminate_backend_outcome"
    passed = False
    safe_to_continue = False
    safe_rebind_before_dispatch = False
    manual_review = True
    reason = "A mutation may have reached HIP but no authoritative final outcome was observed."

    if successes and failures:
        classification = "partial_or_mixed_write_outcome"
        reason = "The action produced both successful and failed write responses; automatic retry could duplicate a partial change."
    elif successes:
        classification = "committed_verified_after_transport_or_ui_error" if action_error else "committed_verified"
        passed = True
        safe_to_continue = True
        manual_review = False
        reason = "At least one authoritative 2xx HIP write response was observed and no failed write response was observed."
    elif failures:
        classification = "rejected_verified"
        manual_review = False
        reason = "HIP returned a terminal non-2xx write response. The mutation is treated as rejected and is not automatically retried."
    elif not dispatch_attempted and not writes:
        classification = "not_dispatched"
        manual_review = False
        safe_rebind_before_dispatch = True
        reason = "The action failed before any physical mutation click dispatch and no HIP write request was observed."
    elif ui_success and not ui_error and (dispatch_attempted or writes or structural_change):
        classification = "visible_success_network_unconfirmed"
        reason = "The portal shows a success signal, but no authoritative 2xx write response was captured; do not repeat the mutation."
    elif redirects:
        classification = "redirected_write_outcome_unconfirmed"
        reason = "A write request received only a redirect response; final mutation state is unconfirmed and must not be retried automatically."
    elif pending:
        classification = "write_request_in_flight_or_response_lost"
        reason = "A HIP write request was observed without a terminal response; the backend outcome may be committed even if the UI timed out."
    elif dispatch_attempted:
        classification = "dispatch_attempted_no_write_response_observed"
        reason = "The physical click was attempted, but no terminal HIP write response was captured; automatic retry is prohibited."

    return mask_sensitive_data({
        "schema_version": MUTATION_OUTCOME_SCHEMA,
        "pass": bool(passed),
        "classification": classification,
        "safe_to_continue": bool(safe_to_continue),
        "safe_rebind_before_dispatch": bool(safe_rebind_before_dispatch),
        "automatic_mutation_retry_allowed": False,
        "manual_review_required": bool(manual_review),
        "reason": reason,
        "dispatch_attempted": dispatch_attempted,
        "dispatch_returned": dispatch_returned,
        "dispatch_count": int(dispatch.get("dispatch_count") or 0),
        "executor": str(dispatch.get("executor") or ""),
        "write_request_count": len(writes),
        "uncorrelated_write_request_count": len(uncorrelated_writes),
        "causality_fence_applied": any("caused_by_dispatch" in row for row in rows),
        "terminal_write_response_count": len(terminal),
        "successful_2xx_write_response_count": len(successes),
        "failed_write_response_count": len(failures),
        "pending_write_request_count": len(pending),
        "ui_success_signal": ui_success,
        "ui_error_signal": ui_error,
        "structural_change": bool(structural_change),
        "action_error_present": bool(action_error),
        "values_stored": False,
    })
