"""V243R15: Source and Target Transport Profile complete on a golden replica.

Reproduced failures (golden_screenshots/UHAUL-POASN/Source Transport Profile.png):

* System Name was bound to System Type: System Type sits directly under the
  page heading and outscored the right control on section alone;
* opening System Type re-rendered its option flags, which was reported as an
  "unintended mutation" although the value never changed;
* every field was chained to the previous one, so that one failure skipped all
  13 later fields;
* Existing Account = Yes and Use Existing Folder = No could not be told apart
  (both options are labelled Yes/No) and the radio driver clicked the first
  matching option in the section, not in the field's own group;
* the portal shows "Name (1.0)" while input.json says "Name(1.0)".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from phase_replica_support import dom_value, failed_fields, run_phase_replica


@pytest.mark.parametrize("phase,system,account,folder", [
    ("source_transport_profile", "AIC - DCE", "haftatap10251108", "/SFTP_U-HAUL_ASN_PC_SRC_IB"),
    ("target_transport_profile", "dce-test-partner", "haftattp10251114", "/Inbound/ASN"),
])
def test_transport_profile_fills_every_field(tmp_path: Path, phase: str, system: str, account: str, folder: str):
    result, final, dom = run_phase_replica(tmp_path, phase=phase, fixture="transport_profile_full_dds.html")
    assert result["status"] == "pass", failed_fields(final)
    assert [c.get("status") for c in result["cycles"]] == ["goal_achieved"]
    assert dom_value(dom, "System Name *")["value"] == system
    radios = {(f["name"], f["value"]): f["checked"] for f in dom if f["type"] == "radio"}
    # Yes in the Existing Account group, No in the Use Existing Folder group.
    assert radios == {
        ("existingAccount", "true"): True, ("existingAccount", "false"): False,
        ("useExistingFolder", "true"): False, ("useExistingFolder", "false"): True,
    }
    assert dom_value(dom, "Existing Account Name *")["value"] == account
    assert dom_value(dom, "Subscription Folder")["value"] == folder
    assert dom_value(dom, "Document Type")["value"].replace(" ", "").startswith("XML_")
