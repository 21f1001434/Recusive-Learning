from __future__ import annotations

from hip_id_agent.mutation_outcome import classify_mutation_outcome


def test_predispatch_failure_is_distinct_from_backend_ambiguity():
    outcome = classify_mutation_outcome(
        dispatch={"dispatch_attempted": False, "dispatch_count": 0},
        network_rows=[], ui_signal={}, structural_change=False,
        action_error="locator detached before click",
    )
    assert outcome["classification"] == "not_dispatched"
    assert outcome["safe_rebind_before_dispatch"] is True
    assert outcome["automatic_mutation_retry_allowed"] is False
    assert outcome["manual_review_required"] is False


def test_2xx_write_recovers_commit_even_when_browser_click_reports_error():
    outcome = classify_mutation_outcome(
        dispatch={"dispatch_attempted": True, "dispatch_returned": False, "dispatch_count": 1, "executor": "playwright-mcp"},
        network_rows=[{"method": "POST", "status": 201, "url": "/deploy"}],
        ui_signal={}, structural_change=True,
        action_error="browser tool timed out after click",
    )
    assert outcome["classification"] == "committed_verified_after_transport_or_ui_error"
    assert outcome["pass"] is True
    assert outcome["safe_to_continue"] is True
    assert outcome["automatic_mutation_retry_allowed"] is False
    assert outcome["successful_2xx_write_response_count"] == 1


def test_visible_success_without_network_confirmation_never_retries_mutation():
    outcome = classify_mutation_outcome(
        dispatch={"dispatch_attempted": True, "dispatch_returned": True, "dispatch_count": 1, "executor": "python-playwright-fallback"},
        network_rows=[],
        ui_signal={"success": True, "error": False, "success_keywords": ["deployed"]},
        structural_change=True,
        action_error="post-action API verification missing",
    )
    assert outcome["classification"] == "visible_success_network_unconfirmed"
    assert outcome["pass"] is False
    assert outcome["manual_review_required"] is True
    assert outcome["automatic_mutation_retry_allowed"] is False


def test_mixed_write_outcome_fails_closed_as_partial_change():
    outcome = classify_mutation_outcome(
        dispatch={"dispatch_attempted": True, "dispatch_returned": True, "dispatch_count": 1},
        network_rows=[
            {"method": "POST", "status": 201, "url": "/object"},
            {"method": "PATCH", "status": 500, "url": "/object/deploy"},
        ],
        ui_signal={"success": True, "error": False},
        structural_change=True,
    )
    assert outcome["classification"] == "partial_or_mixed_write_outcome"
    assert outcome["pass"] is False
    assert outcome["manual_review_required"] is True
    assert outcome["automatic_mutation_retry_allowed"] is False
    assert outcome["successful_2xx_write_response_count"] == 1
    assert outcome["failed_write_response_count"] == 1
