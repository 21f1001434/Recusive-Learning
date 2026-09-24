"""V243R15: BizFlow Configure Target(s) and Configure Routing on the golden wizard.

Reproduced failures (golden BizFlow-CT-1/CT-2/CR.png):

* Process Step 2 starts collapsed; the reveal helper found Step 1's visible
  "Step Type" and never opened Step 2;
* the File Name parts inside Step 2 were attributed to the Process Step row;
* the Date And Time part's Value is a dropdown in the portal but a text value in
  input.json: typing only filled the dropdown's search box, which is lost on
  blur, so the agent now drives the control that is actually on screen;
* process_steps[].step_number is shown only as the row ordinal ("1 ::: ...");
* Step 1's disabled Source Document Type briefly could not be re-resolved once
  Step 2 appeared and was reported as an unintended mutation.
"""
from __future__ import annotations

from pathlib import Path

from phase_replica_support import dom_value, failed_fields, run_phase_replica


def test_configure_targets_fills_both_process_steps_and_file_name_parts(tmp_path: Path):
    result, final, dom = run_phase_replica(
        tmp_path, phase="biz_flow", fixture="bizflow_wizard_dds.html", section="Configure Target(s)",
        prepare_js="document.getElementById('tab-2').click()",
    )
    assert result["status"] == "pass", failed_fields(final)
    assert [dom_value(dom, "Step Type", i)["value"] for i in (0, 1)] == ["Mapping Transformer", "Enricher"]
    assert dom_value(dom, "Rule (Version) *")["value"] == "DELLCoXMLASNXX08C_U-HAUL_RULE (1.0)"
    assert dom_value(dom, "targetFileNameConfig")["checked"] is True
    assert [dom_value(dom, "Derived From *", i)["value"] for i in (0, 1)] == ["Fixed Text", "Date And Time"]
    # Survives the blur above, so the option was committed, not just typed.
    assert dom_value(dom, "Value *")["value"] == "yyyyddMMhhmmss"
    adapted = {
        (a["field"], a["row_index"])
        for cycle in result["cycles"]
        for stage in ("non_file_execution", "full_goal_execution")
        for a in (cycle.get(stage) or {}).get("attempts", [])
        if (a.get("transaction_proof") or {}).get("adapted_action")
    }
    assert adapted == {("filename_part_value", 1)}


def test_configure_routing_fills_the_rule_drawer(tmp_path: Path):
    result, final, dom = run_phase_replica(
        tmp_path, phase="biz_flow", fixture="bizflow_wizard_dds.html", section="Configure Routing",
        prepare_js="document.getElementById('tab-3').click(); document.getElementById('routing-add').click()",
    )
    assert result["status"] == "pass", failed_fields(final)
    assert dom_value(dom, "Rule Name")["value"] == "FLOWROUTE_U-HAUL_PC_856_ANS_XMLASN_OB"
    assert dom_value(dom, "Rule Scope *")["value"] == "LOCAL"
    assert [dom_value(dom, "Value", i)["value"] for i in (0, 1)] == ["uhaul", "DELL"]
    assert dom_value(dom, "Target *")["value"] == "SFTP_U-HAUL_ASN_PC_TGT_OB"
