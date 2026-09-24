from pathlib import Path

from hip_id_agent.memory import HipMemory
from hip_id_agent.models import ExtractedID


def test_save_partner_id_uses_nested_object_type(tmp_path: Path):
    mem = HipMemory(tmp_path)
    mem.save_extracted_id("CUSTOMER", ExtractedID("partner", "UHAL", "10483", "UHAL", "network", 0.95))
    data = mem.objects.read()
    assert data["CUSTOMER"]["partner"]["UHAL"]["id"] == "10483"
    assert "UHAL" not in {k for k in data["CUSTOMER"].keys() if k != "partner"}


def test_save_system_id_uses_nested_object_type(tmp_path: Path):
    mem = HipMemory(tmp_path)
    mem.save_extracted_id("CUSTOMER", ExtractedID("system", "UHAL-POASN", "98765", "UHAL-POASN", "network", 0.96))
    data = mem.objects.read()
    assert data["CUSTOMER"]["system"]["UHAL-POASN"]["id"] == "98765"


def test_get_id_is_object_type_scoped(tmp_path: Path):
    mem = HipMemory(tmp_path)
    mem.save_extracted_id("CUSTOMER", ExtractedID("partner", "UHAL", "10483", "UHAL", "network", 0.95))
    mem.save_extracted_id("CUSTOMER", ExtractedID("system", "UHAL", "98765", "UHAL", "network", 0.95))
    assert mem.get_id("CUSTOMER", "partner", "UHAL") == "10483"
    assert mem.get_id("CUSTOMER", "system", "UHAL") == "98765"


def test_legacy_registry_still_works_when_typed(tmp_path: Path):
    mem = HipMemory(tmp_path)
    mem.objects.write({"CUSTOMER": {"UHAL": {"id": "10483", "object_type": "partner"}}})
    assert mem.get_id("CUSTOMER", "partner", "UHAL") == "10483"
    assert mem.get_id("CUSTOMER", "system", "UHAL") is None


def test_untyped_legacy_registry_does_not_cross_object_types(tmp_path: Path):
    mem = HipMemory(tmp_path)
    mem.objects.write({
        "CUSTOMER": {
            "UHAL": {
                "id": "10483",
                "name": "UHAL"
            }
        }
    })

    assert mem.get_id("CUSTOMER", "system", "UHAL") is None
    assert mem.get_id("CUSTOMER", "partner", "UHAL") is None


def test_typed_legacy_registry_partner_lookup_allowed(tmp_path: Path):
    mem = HipMemory(tmp_path)
    mem.objects.write({
        "CUSTOMER": {
            "UHAL": {
                "id": "10483",
                "name": "UHAL",
                "object_type": "partner"
            }
        }
    })

    assert mem.get_id("CUSTOMER", "partner", "UHAL") == "10483"
    assert mem.get_id("CUSTOMER", "system", "UHAL") is None


def test_typed_legacy_registry_system_lookup_allowed(tmp_path: Path):
    mem = HipMemory(tmp_path)
    mem.objects.write({
        "CUSTOMER": {
            "UHAL-POASN": {
                "id": "98765",
                "name": "UHAL-POASN",
                "object_type": "system"
            }
        }
    })

    assert mem.get_id("CUSTOMER", "system", "UHAL-POASN") == "98765"
    assert mem.get_id("CUSTOMER", "partner", "UHAL-POASN") is None
