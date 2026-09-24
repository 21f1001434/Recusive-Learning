from pathlib import Path

from hip_id_agent.datamap_kb import (
    extract_data_map_seed,
    find_existing_data_map_match,
)
from hip_id_agent.dummy_fill_e2e import _classify_validation_messages
from hip_id_agent.section_judge import (
    _reconcile_text_judge,
    _reconcile_vision_judge,
)
from hip_id_agent.upload_assets import (
    find_upload_asset,
    parse_accept_extensions,
)


def test_optional_schema_files_are_not_injected_from_defaults():
    data = {"objects": {"data_map": {"map_identifier": "MAP-1", "map_data_file": "map.jar"}}}
    seed = extract_data_map_seed(data)
    assert seed["input_schema_file"] == ""
    assert seed["output_schema_file"] == ""
    assert seed["map_data_file"] == "map.jar"


def test_accept_contract_blocks_xml_for_schema_controls(tmp_path: Path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "sample.xml").write_text("<x/>", encoding="utf-8")
    data = {"_upload_assets_dir": str(uploads)}
    accepted = parse_accept_extensions(".xsd,.json,.edi,.txt")
    assert accepted == [".xsd", ".json", ".edi", ".txt"]
    asset = find_upload_asset(
        data,
        "sample.xml",
        field_key="input_schema_file",
        phase="data_map",
        accepted_extensions=accepted,
        require_explicit=True,
    )
    assert asset is None


def test_exact_existing_map_is_resolved_for_reuse():
    rows = [{
        "map_identifier": "DELLCoXMLASNXX08C_U-HAUL",
        "map_identifier_version": "1.0",
        "map_name": "DELLCoXMLASNXX08C",
        "map_class": "Transform_DELLCoXMLASNXX08C",
        "available_environments": ["DEV"],
    }]
    seed = {
        "map_identifier": "DELLCoXMLASNXX08C_U-HAUL",
        "map_identifier_version": "1",
        "map_name": "DELLCoXMLASNXX08C",
        "map_class": "Transform_DELLCoXMLASNXX08C",
    }
    result = find_existing_data_map_match(rows, seed)
    assert result["found"] is True
    assert result["mode"] == "reuse_existing"


def test_duplicate_is_nonblocking_unless_inventory_shows_conflicting_object():
    msgs = [{"message": "Map Identifier already exists"}]
    accepted = _classify_validation_messages(
        "data_map",
        msgs,
        {"existing_object_resolution": {"found": True, "mode": "reuse_existing"}},
    )
    assert not accepted["blocking"]
    assert accepted["accepted_nonblocking"][0]["classification"] == "existing_object_reuse"

    # The live portal validator is authoritative that the key exists even when
    # the read-only inventory did not page to it (golden screenshot shows it).
    portal_reported = _classify_validation_messages(
        "data_map",
        msgs,
        {"existing_object_resolution": {"found": False, "mode": "create_no_save"}},
    )
    assert not portal_reported["blocking"]
    assert portal_reported["accepted_nonblocking"][0]["classification"] == "existing_object_reported_by_portal"

    blocked = _classify_validation_messages(
        "data_map",
        msgs,
        {"existing_object_resolution": {"found": False, "mode": "conflicting_existing_object"}},
    )
    assert blocked["blocking"]
    assert not blocked["accepted_nonblocking"]


def test_file_type_error_remains_blocking_even_when_map_exists():
    msgs = [
        {"message": "Map Identifier already exists"},
        {"message": "The following file types are not allowed: sample.xml. Error: File type not supported."},
    ]
    result = _classify_validation_messages(
        "data_map",
        msgs,
        {"existing_object_resolution": {"found": True, "mode": "reuse_existing"}},
    )
    assert len(result["accepted_nonblocking"]) == 1
    assert len(result["blocking"]) == 1


def test_text_judge_cannot_veto_exact_field_bound_evidence():
    deterministic = {
        "pass": True,
        "matched_values": [{"field": "input_value[1]", "expected": "1", "actual": "1.0"}],
        "validation_gate": {"accepted_nonblocking": []},
    }
    model = {
        "status": "ok",
        "pass": False,
        "missing_or_wrong": [{"field": "input_value[1]", "expected": "1", "actual": "", "reason": "not present"}],
        "repair_steps": [{"field": "input_value[1]", "action": "refill"}],
    }
    reconciled = _reconcile_text_judge(model, deterministic)
    assert reconciled["pass"] is True
    assert reconciled["status"] == "reconciled"
    assert reconciled["missing_or_wrong"] == []


def test_vision_judge_internal_equal_value_contradiction_is_reconciled():
    deterministic = {"pass": True, "matched_values": [], "validation_gate": {"accepted_nonblocking": []}}
    model = {
        "status": "ok",
        "pass": False,
        "visible_issues": [{"field": "map_name", "expected": "ABC", "observed": "ABC"}],
    }
    reconciled = _reconcile_vision_judge(model, deterministic)
    assert reconciled["pass"] is True
    assert reconciled["status"] == "reconciled"
    assert reconciled["visible_issues"] == []


def test_block_diagnosis_names_real_validation_reason():
    from hip_id_agent.dummy_fill_e2e import build_section_block_diagnosis

    verification = {
        "phase": "data_map",
        "validation_gate": {"blocking": [{"message": "File type not supported"}]},
        "object_resolution": {"found": True, "mode": "reuse_existing"},
    }
    judge = {
        "phase": "data_map",
        "section": "Data Map",
        "pass": False,
        "deterministic_judge": {"pass": False, "missing_values": [], "row_issues": []},
        "text_model_judge": {"pass": False, "missing_or_wrong": [], "summary": "blocked"},
        "vision_model_judge": {"pass": False, "visible_issues": [], "summary": "blocked"},
    }
    diagnosis = build_section_block_diagnosis(judge, verification)
    codes = {x["code"] for x in diagnosis["reasons"]}
    assert "blocking_portal_validation" in codes


def test_vision_generic_checkbox_cannot_veto_exact_deterministic_pass():
    deterministic = {
        "pass": True,
        "matched_values": [
            {"field": "input_value[0]", "expected": "ABC", "actual": "ABC"},
        ],
        "missing_values": [],
        "row_issues": [],
        "failed_attempts": [],
        "validation_gate": {"accepted_nonblocking": []},
    }
    model = {
        "status": "ok",
        "pass": False,
        "summary": "missing checkbox selection",
        "visible_issues": [{"field": "checkbox", "expected": "selected", "observed": "not selected"}],
    }
    reconciled = _reconcile_vision_judge(model, deterministic)
    assert reconciled["pass"] is True
    assert reconciled["status"] == "reconciled"
    assert reconciled["visible_issues"] == []
    assert reconciled["unsupported_model_issues"][0]["field"] == "checkbox"


def test_vision_concrete_row_scoped_issue_remains_blocking():
    deterministic = {
        "pass": True,
        "matched_values": [],
        "missing_values": [],
        "row_issues": [],
        "failed_attempts": [],
        "validation_gate": {"accepted_nonblocking": []},
    }
    model = {
        "status": "ok",
        "pass": False,
        "visible_issues": [{
            "field": "attribute_usage",
            "input_path": "$.objects.source_document_type.attributes_to_configure[0].usage",
            "row_kind": "attribute",
            "row_index": 0,
            "expected": ["Logging", "Mapping"],
            "observed": ["Logging"],
        }],
    }
    reconciled = _reconcile_vision_judge(model, deterministic)
    assert reconciled["pass"] is False
    assert len(reconciled["visible_issues"]) == 1
