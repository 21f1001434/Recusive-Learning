"""V243R15: Data Map and Rule complete on full-length golden replicas.

Drives the real autonomous goal on replicas of golden_screenshots/UHAUL-POASN
(Data Map.png, Rules.png): DDS dropdowns with owned popups, the disabled
portal-owned Version / Rule Type / Rule Scope, Status/Execute Always switches,
two Conditions rows (the second without visible labels, as DDS renders them),
the Actions mapping dropdown that appears only after Type, and the Map Data JAR.
"""
from __future__ import annotations

from pathlib import Path

from phase_replica_support import dom_value, failed_fields, run_phase_replica


def test_data_map_fills_every_field_and_uploads_the_jar(tmp_path: Path):
    result, final, dom = run_phase_replica(tmp_path, phase="data_map", fixture="data_map_full_dds.html")
    assert result["status"] == "pass", failed_fields(final)
    assert dom_value(dom, "Map Identifier")["value"] == "DELLCoXMLASNXX08C_U-HAUL"
    assert dom_value(dom, "Contivo version *")["value"] == "6.7"
    assert dom_value(dom, "mapData")["value"] == "Transform_DELLCoXMLASNXX08C.jar"


def test_rule_fills_rule_conditions_and_actions(tmp_path: Path):
    result, final, dom = run_phase_replica(tmp_path, phase="rule", fixture="rule_full_dds.html")
    assert result["status"] == "pass", failed_fields(final)
    assert [c.get("status") for c in result["cycles"]] == ["goal_achieved"]
    assert dom_value(dom, "Document Type Name (Version) *")["value"] == "XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)"
    # Both condition rows, including the unlabelled second row.
    assert [dom_value(dom, "Operator", i)["value"] for i in (0, 1)] == ["Equals", "Contains"]
    assert [dom_value(dom, "Value", i)["value"] for i in (0, 1)] == ["uhaul", "DELL"]
    assert [dom_value(dom, "Attribute Name/Unit", i)["value"] for i in (0, 1)] == ["Receiver", "Sender"]
    assert dom_value(dom, "Mapping Identifier Name (Version) *")["value"] == "DELLCoXMLASNXX08C_U-HAUL(1.0)"
