import json
from pathlib import Path

from hip_id_agent.dummy_fill_e2e import (
    PHASE_SEQUENCE,
    build_phase_verification,
    build_vision_prompts,
    make_phase_input,
    write_phase_inputs,
)


def sample_input():
    return {
        "objects": {
            "data_map": {"map_identifier": "MAP_A"},
            "source_document_type": {"name": "SRC_DOC", "version": "1"},
            "target_document_type": {"name": "TGT_DOC", "version": "1"},
            "rule": {"name": "RULE_A"},
            "source_transport_profile": {"profile_name": "SRC_TP"},
            "target_transport_profile": {"profile_name": "TGT_TP"},
            "biz_flow": {"flow_details": {"business_flow_name": "FLOW_A"}},
        }
    }


def test_phase_input_separates_source_and_target_document_types():
    src = make_phase_input(sample_input(), "source_document_type")
    tgt = make_phase_input(sample_input(), "target_document_type")
    assert src["objects"]["document_type"]["name"] == "SRC_DOC"
    assert "target_document_type" not in src["objects"]
    assert tgt["objects"]["document_type"]["name"] == "TGT_DOC"
    assert "source_document_type" not in tgt["objects"]


def test_phase_input_separates_source_and_target_transport_profiles():
    src = make_phase_input(sample_input(), "source_transport_profile")
    tgt = make_phase_input(sample_input(), "target_transport_profile")
    assert src["objects"]["transport_profile"]["profile_name"] == "SRC_TP"
    assert "target_transport_profile" not in src["objects"]
    assert tgt["objects"]["transport_profile"]["profile_name"] == "TGT_TP"
    assert "source_transport_profile" not in tgt["objects"]


def test_write_phase_inputs_creates_all_expected_files(tmp_path):
    paths = write_phase_inputs(sample_input(), tmp_path, PHASE_SEQUENCE)
    assert set(paths) == set(PHASE_SEQUENCE)
    assert all(Path(p).exists() for p in paths.values())


def test_build_phase_verification_counts_attempts_dropdowns_and_screenshots(tmp_path):
    phase_dir = tmp_path / "phase"
    kb = phase_dir / "doctype_kb"
    kb.mkdir(parents=True)
    (kb / "doctype_dummy_fill_plan.json").write_text(json.dumps({"attempts": [{"field": "Name", "success": True}, {"field": "Version", "success": False}]}))
    (kb / "doctype_required_fields.json").write_text(json.dumps([{"label": "Name"}, {"label": "Version"}]))
    (kb / "doctype_dropdowns.json").write_text(json.dumps([{"label": "Status", "options": ["Enable"]}, {"label": "Format", "options": []}]))
    (kb / "doctype_add_form_after_dummy_fill_no_save.png").write_bytes(b"fakepng")
    verification = build_phase_verification("source_document_type", phase_dir, {"status": "completed", "form_controls": 4}, vision_enabled=True)
    assert verification["counts"]["dummy_fill_attempts"] == 2
    assert verification["counts"]["required_fields"] == 2
    assert verification["counts"]["dropdowns"] == 2
    assert verification["counts"]["dropdowns_with_options"] == 1
    assert verification["failed_attempts"][0]["field"] == "Version"
    assert verification["screenshots"]
    assert verification["status"] == "pass_with_warnings"


def test_build_vision_prompts_uses_screenshots_and_expected_evidence(tmp_path):
    shot = tmp_path / "filled.png"
    shot.write_bytes(b"fake")
    prompts = build_vision_prompts([
        {"phase": "rule", "phase_label": "Rule", "screenshots": [str(shot)], "required_fields_sample": [{"label": "Name"}], "dummy_fill_attempts_sample": [], "dropdowns_sample": []}
    ])
    assert len(prompts) == 1
    assert prompts[0]["phase"] == "rule"
    assert "visible required fields" in prompts[0]["prompt"].lower() or "required fields" in prompts[0]["prompt"].lower()

from hip_id_agent.dummy_fill_e2e import build_flash_replay_package, build_phase_flash_blueprint


def test_build_flash_replay_package_saves_manifest_and_phase_blueprint(tmp_path):
    root = tmp_path
    phase = "rule"
    phase_dir = root / phase
    kb = phase_dir / "rules_kb"
    kb.mkdir(parents=True)
    phase_input = root / "phase_inputs" / "rule.json"
    phase_input.parent.mkdir(parents=True)
    phase_input.write_text(json.dumps({"objects": {"rule": {"name": "RULE_A"}}}))
    (kb / "rules_dummy_fill_plan.json").write_text(json.dumps({"attempts": [{"label": "Rule Name", "key": "rule_name", "selector": "input#rule-name", "value_used": "RULE_A", "success": True}]}))
    (kb / "rules_required_fields.json").write_text(json.dumps([{"label": "Rule Name", "selector": "input#rule-name", "required": True}]))
    (kb / "rules_dropdowns.json").write_text(json.dumps([{"label": "Rule Type", "selector": "input#rule-type", "options": ["Mapping"]}]))
    shot = kb / "rule_after_dummy_fill_no_save.png"
    shot.write_bytes(b"fakepng")
    ver = build_phase_verification(phase, phase_dir, {"status": "completed", "form_controls": 2}, vision_enabled=True)
    manifest = build_flash_replay_package(
        root_dir=root,
        base_input=sample_input(),
        phase_input_paths={phase: str(phase_input)},
        phase_summaries={phase: {"status": "completed", "form_controls": 2}},
        phase_verifications=[ver],
        phases=[phase],
    )
    assert Path(manifest["manifest_file"]).exists()
    bp_path = Path(manifest["phase_blueprints"][phase]["blueprint_file"])
    assert bp_path.exists()
    bp = json.loads(bp_path.read_text())
    assert bp["field_steps"][0]["selector"] == "input#rule-name"
    assert bp["field_steps"][0]["value"] == "RULE_A"
    assert "save" in json.dumps(bp["replay_contract"])
    assert (root / "FAST_FILL_AGENT_PLAYBOOK.md").exists()

from hip_id_agent.dummy_fill_e2e import prepare_golden_screenshot_references


def test_prepare_golden_screenshot_references_maps_user_images_to_phases(tmp_path):
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "Data Map.png").write_bytes(b"png1")
    (golden / "BizFlow-CT-1.png").write_bytes(b"png2")
    refs = prepare_golden_screenshot_references(golden, tmp_path / "run")
    assert "data_map" in refs
    assert "biz_flow" in refs
    assert refs["data_map"][0]["role"] == "golden_correctly_filled_reference"
    assert Path(refs["biz_flow"][0]["path"]).exists()


def test_build_phase_verification_attaches_golden_references(tmp_path):
    phase_dir = tmp_path / "rule"
    kb = phase_dir / "rule_kb"
    kb.mkdir(parents=True)
    (kb / "rule_add_form_after_dummy_fill_no_save.png").write_bytes(b"actual")
    golden_ref = {"file": "Rules.png", "path": str(tmp_path / "Rules.png"), "role": "golden_correctly_filled_reference"}
    verification = build_phase_verification("rule", phase_dir, {"status": "completed", "form_controls": 1}, vision_enabled=True, golden_references=[golden_ref])
    assert verification["golden_replication"]["enabled"] is True
    assert verification["golden_reference_screenshots"][0]["file"] == "Rules.png"
    prompts = build_vision_prompts([verification])
    assert prompts[0]["golden_screenshots"] == [golden_ref["path"]]
    assert "golden reference" in prompts[0]["prompt"].lower()
