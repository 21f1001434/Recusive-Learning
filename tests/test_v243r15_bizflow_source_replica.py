"""V243R15: BizFlow Flow Details and Configure Source on the golden wizard.

Reproduced failures (golden BizFlow-FD.png / BizFlow-CS.png):

* the final one-to-one form model was computed over all 58 BizFlow nodes while
  only one tab is on screen, so every tab failed;
* "Current Flow version : 1.0" is plain text, yet input.json's
  current_flow_version had to bind to a control;
* Source "Document Type Name" tied with the first Flow Identifier row's
  "Document Type Name (Version)", and the unlabelled second row had no kind;
* a row's identity upgrade (position -> semantic anchor) was reported as an
  unintended mutation.
"""
from __future__ import annotations

from pathlib import Path

from phase_replica_support import dom_value, failed_fields, run_phase_replica


def test_flow_details_accepts_the_displayed_flow_version(tmp_path: Path):
    result, final, dom = run_phase_replica(
        tmp_path, phase="biz_flow", fixture="bizflow_wizard_dds.html", section="Flow Details",
    )
    assert result["status"] == "pass", failed_fields(final)
    ledger = result["cycles"][-1]["runtime_input_leaf_ledger_after"]
    assert ledger["pass"] is True and ledger["unresolved_input_leaves"] == []
    assert dom_value(dom, "Business Flow Name")["value"] == "U-HAUL_PC_856_ANS_MAPPING_OB"


def test_configure_source_fills_system_details_and_both_identifier_rows(tmp_path: Path):
    result, final, dom = run_phase_replica(
        tmp_path, phase="biz_flow", fixture="bizflow_wizard_dds.html", section="Configure Source",
        prepare_js="document.getElementById('tab-1').click()",
    )
    assert result["status"] == "pass", failed_fields(final)
    assert dom_value(dom, "Source Application *")["value"] == "AIC - DCE"
    assert dom_value(dom, "Source Transport Profile *")["chips"] == ["SFTP_U-HAUL_ASN_PC_SRC_IB"]
    assert dom_value(dom, "Document Type Name *")["chips"] == ["XML_DellAutoASN_10_U-HAUL_ANS_IB (1.0)"]
    assert [dom_value(dom, "Attribute Name *", i)["value"] for i in (0, 1)] == ["Receiver", "Sender"]
    assert [dom_value(dom, "Operator *", i)["value"] for i in (0, 1)] == ["Equals", "Contains"]
