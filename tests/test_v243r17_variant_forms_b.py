"""V243R17: Rule and Transport Profile variants (see test_v243r17_variant_forms_a)."""
from __future__ import annotations

from pathlib import Path

from phase_replica_support import dom_checked, dom_values, run_variant_replica


def test_rule_variant_adds_condition_rows_and_sets_button_radio(tmp_path: Path):
    result, dom = run_variant_replica(tmp_path, "rule")
    assert result["pass"] is True, result.get("failure_summary")
    # The form starts with one condition; "+ Add Condition" creates rows 2 and 3.
    assert dom_values(dom, "Value") == ["uhaul", "DELL", "856"]
    assert dom_values(dom, "Operator") == ["Equals", "Contains", "Starts With"]
    assert dom_checked(dom, "executeAlways") == ["on"]
    # A button-style radio group inside the collapsed "Advanced" section.
    assert dom_checked(dom, "High") == ["High"]


def test_transport_profile_variant_branches_rows_and_notifications_through_broker(tmp_path: Path):
    result, dom = run_variant_replica(tmp_path, "target_transport_profile", broker=True)
    assert result["pass"] is True, result.get("failure_summary")
    # "No" reveals Account Name; "Yes" reveals Existing Folder.
    assert dom_checked(dom, "existingAccount") == ["false"]
    assert dom_values(dom, "Account Name *") == ["haftnew0001"]
    assert dom_checked(dom, "useExistingFolder") == ["true"]
    assert dom_values(dom, "Existing Folder *") == ["/Outbound"]
    assert dom_values(dom, "Key") == ["env", "owner"]
    assert dom_checked(dom, "notifyOn") == ["Failure"]
    assert dom_checked(dom, "Webhook") == ["Webhook"]
    assert dom_values(dom, "Notification Email") == ["b2b@example.com"]
