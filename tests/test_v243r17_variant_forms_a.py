"""V243R17: phases whose forms carry more than the golden layout are still filled exactly.

Each variant form adds what a HIP form can also contain -- radio groups (a
DDS radio whose real input is clipped, button-style radios), checkbox groups,
collapsed sections, "+ Add" row lists and branch fields -- and input.json
carries keys the phase compilers do not know.  Before R17 the agent left
these empty, chose nothing in radio groups, never opened collapsed sections
and typed row 2 over row 1.
"""
from __future__ import annotations

from pathlib import Path

from phase_replica_support import dom_checked, dom_values, run_variant_replica


def test_document_type_edi_variant_adds_rows_and_fills_edi_fields(tmp_path: Path):
    result, dom = run_variant_replica(tmp_path, "source_document_type")
    assert result["pass"] is True, result.get("failure_summary")
    assert len(result["cycles"]) == 1
    # Two identifier rows (the second created with the icon-only "+").
    assert dom_values(dom, "Value") == ["ISA", "urn:x12"]
    # Three attribute rows (two created with "+ Add Attribute").
    assert dom_values(dom, "Attribute Name") == ["Receiver", "Sender", "Transaction Type"]
    # Fields the compiler does not know, shown only for EDIX12.
    assert dom_values(dom, "Segment Separator") == ["~"]
    assert dom_values(dom, "Data Element Separator") == ["*"]
    assert dom_values(dom, "Sub Element Separator") == [">"]
    assert dom_checked(dom, "acknowledgementRequired") == ["yes"]


def test_data_map_variant_switch_rows_hidden_radio_and_checkbox_group(tmp_path: Path):
    result, dom = run_variant_replica(tmp_path, "data_map")
    assert result["pass"] is True, result.get("failure_summary")
    # Row 2 must not overwrite row 1: "+ Add Row" creates it first.
    assert dom_values(dom, "Source Value") == ["US", "CA"]
    assert dom_values(dom, "Target Value") == ["840", "124"]
    # Inside the collapsed "Advanced Options" section.
    assert dom_checked(dom, "mapEngine") == ["xslt"]
    assert dom_checked(dom, "notifyOn") == ["Failure", "Warning"]
    heal = result["cycles"][0]["structure_heal"]
    assert [e["title"] for e in heal["reveal"]["expanded"]] == ["Advanced Options"]


def test_bizflow_flow_details_variant_radio_collapsed_section_and_checkboxes(tmp_path: Path):
    result, dom = run_variant_replica(tmp_path, "biz_flow", section="Flow Details")
    assert result["pass"] is True, result.get("failure_summary")
    assert dom_checked(dom, "flowType") == ["passthrough"]
    assert dom_checked(dom, "alertsEnabled") == ["on"]
    # Exactly the listed options, and nothing else.
    assert dom_checked(dom, "alertOn") == ["Failure", "Delay"]
