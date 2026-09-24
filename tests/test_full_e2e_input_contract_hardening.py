from __future__ import annotations

import copy
import json
from pathlib import Path

from hip_id_agent.dummy_fill_e2e import PHASE_SEQUENCE, validate_live_input_contract
from hip_id_agent.maximum_observability import build_input_coverage_contract
from hip_id_agent.stateful_form_runtime import (
    _stateful_value_equal,
    apply_dependency_execution_contract,
    compile_phase_state_graph,
)
from hip_id_agent.streamlit_dashboard import build_mission_preflight_report


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "examples" / "uhaul_poasn_full_dummy_input.json"
GOLDEN = ROOT / "golden_screenshots" / "UHAUL-POASN"
UPLOADS = ROOT / "uploads"


def _payload() -> dict:
    return json.loads(INPUT.read_text(encoding="utf-8"))


def test_all_seven_phase_input_leaves_are_accounted_and_dependency_graphs_are_acyclic() -> None:
    payload = _payload()
    assert len(PHASE_SEQUENCE) == 7
    for phase in PHASE_SEQUENCE:
        graph = apply_dependency_execution_contract(compile_phase_state_graph(payload, phase), phase=phase)
        contract = graph.get("dependency_execution_contract") or {}
        assert contract.get("pass") is True, phase
        assert contract.get("cycle_node_ids") == [], phase
        coverage = build_input_coverage_contract(
            phase=phase,
            input_payload=payload,
            state_graph=graph,
            verification={"status": "preflight"},
        )
        assert coverage.get("pass") is True, (phase, coverage.get("unmapped_input_leaf_paths"))
        assert coverage.get("complete_phase_input_pass") is True, phase
        assert coverage.get("exact_leaf_coverage_percent") == 100.0, phase
        assert coverage.get("unmapped_input_leaf_paths") == [], phase


def test_bizflow_graph_executes_or_verifies_previously_unmodelled_real_form_fields() -> None:
    payload = _payload()
    graph = compile_phase_state_graph(payload, "biz_flow")
    paths = {str(node.get("input_path")): node for node in graph.get("nodes", [])}
    expected = {
        "$.objects.biz_flow.flow_identifiers.conditions[0].document_type_name_version": "select_single",
        "$.objects.biz_flow.flow_identifiers.conditions[1].document_type_name_version": "select_single",
        "$.objects.biz_flow.process_steps[0].configuration.source_document_type": "verify_only",
        "$.objects.biz_flow.process_steps[0].configuration.add_rule_if_not_listed": "toggle",
        "$.objects.biz_flow.process_steps[1].configuration.document_type_version": "verify_only",
        "$.objects.biz_flow.process_steps[1].configuration.target_file_name_config": "toggle",
        "$.objects.biz_flow.process_steps[1].configuration.file_name_parts[0].part_number": "select_single",
        "$.objects.biz_flow.process_steps[1].configuration.file_name_parts[1].part_number": "select_single",
        "$.objects.biz_flow.configure_routing.rule.status": "select_radio",
        "$.objects.biz_flow.configure_routing.rule.execute_always": "toggle",
    }
    for path, action in expected.items():
        assert path in paths, path
        assert paths[path].get("action") == action, path
        assert paths[path].get("depends_on"), path

    accounting = {str(row.get("input_path")): row for row in graph.get("input_accounting", [])}
    assert accounting["$.objects.biz_flow.flow_details.current_flow_version"]["disposition"] == "read_only_display"
    assert accounting["$.objects.biz_flow.process_steps[0].step_number"]["disposition"] == "repeatable_row_position"
    assert accounting["$.objects.biz_flow.process_steps[1].step_number"]["disposition"] == "repeatable_row_position"


def test_document_type_operation_alias_and_blank_conditional_expression_are_accounted() -> None:
    payload = _payload()
    for phase in ("source_document_type", "target_document_type"):
        graph = compile_phase_state_graph(payload, phase)
        accounting = {str(row.get("input_path")): row for row in graph.get("input_accounting", [])}
        assert f"$.objects.{phase}.operation" in accounting
        expression_rows = [
            row for path, row in accounting.items()
            if path.startswith(f"$.objects.{phase}.attributes_to_configure[") and path.endswith(".expression")
        ]
        assert expression_rows
        assert any(row.get("disposition") == "conditional_child_absent_or_blank" for row in expression_rows)


def test_live_input_contract_fails_closed_on_cross_object_reference_drift() -> None:
    payload = _payload()
    payload["objects"]["biz_flow"]["configure_targets"]["target_transport_profile"] = "WRONG_TP"
    report = validate_live_input_contract(payload)
    assert report.get("pass") is False
    issues = report.get("issues") or []
    assert any(issue.get("path") == "objects.biz_flow.configure_targets.target_transport_profile" for issue in issues)


def test_live_input_contract_current_input_is_full_and_cross_object_consistent() -> None:
    report = validate_live_input_contract(_payload())
    assert report.get("pass") is True, report.get("issues")
    assert report.get("cross_object_references_checked") is True
    assert report.get("complete_phase_leaf_accounting_required") is True
    for phase in PHASE_SEQUENCE:
        coverage = (report.get("phase_coverage") or {}).get(phase) or {}
        assert coverage.get("pass") is True, phase
        assert coverage.get("exact_leaf_coverage_percent") == 100.0, phase


def test_streamlit_preflight_passes_current_package_and_blocks_missing_golden(tmp_path: Path) -> None:
    report = build_mission_preflight_report(
        input_json=INPUT,
        golden_screenshot_dir=GOLDEN,
        upload_assets_dir=UPLOADS,
    )
    assert report.get("pass") is True, report.get("issues")
    assert report.get("golden_screenshots_pass") is True
    assert report.get("upload_assets_pass") is True
    assert all(row.get("leaf_coverage") == 100.0 for row in report.get("phase_rows") or [])

    golden = tmp_path / "golden"
    golden.mkdir()
    # Intentionally provide no required golden states.
    bad = build_mission_preflight_report(
        input_json=INPUT,
        golden_screenshot_dir=golden,
        upload_assets_dir=UPLOADS,
    )
    assert bad.get("pass") is False
    assert bad.get("golden_screenshots_pass") is False
    assert any(issue.get("reason") == "required golden screenshot(s) missing" for issue in bad.get("issues") or [])


def test_streamlit_preflight_blocks_missing_required_datamap_upload(tmp_path: Path) -> None:
    empty_uploads = tmp_path / "uploads"
    empty_uploads.mkdir()
    report = build_mission_preflight_report(
        input_json=INPUT,
        golden_screenshot_dir=GOLDEN,
        upload_assets_dir=empty_uploads,
    )
    assert report.get("pass") is False
    assert report.get("upload_assets_pass") is False
    assert any(issue.get("reason") == "required Data Map upload asset is missing" for issue in report.get("issues") or [])


def test_verify_only_disabled_all_other_matches_portal_default_all_other() -> None:
    assert _stateful_value_equal(
        {"action": "verify_only", "field_key": "process_source_document_type", "expected_value": "Disabled(All/Other)"},
        {"value": "Default (All/Other)"},
    )
    assert _stateful_value_equal(
        {"action": "verify_only", "field_key": "process_document_type_version", "expected_value": "Disabled(All/Other)"},
        {"value": "Default (All/Other)"},
    )
