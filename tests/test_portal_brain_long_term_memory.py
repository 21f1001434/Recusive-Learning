from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.form_knowledge_plan import compile_and_attach_plans
from hip_id_agent.portal_brain import PortalBrain, PortalBrainPolicy


def _make_run(root: Path, name: str, *, status: str, judge_pass: bool, selector: str, value: str) -> Path:
    run = root / name
    (run / "fast_replay_blueprints").mkdir(parents=True)
    (run / "portal_form_knowledge").mkdir(parents=True)
    (run / "deterministic_plans").mkdir(parents=True)
    blueprint = {
        "phase": "biz_flow",
        "verification_status": status,
        "field_steps": [
            {
                "order": 1,
                "key": "process_step_type",
                "label": "Process Step Type",
                "selector": selector,
                "fallback_label": "Process Step Type",
                "tab": "Configure Target",
                "value": value,
                "fill_strategy": "select_or_type",
                "required": True,
                "dropdown_options_sample": ["Mapping Transformer", "Enricher"],
            }
        ],
        "repeatable_section_plan": [],
    }
    (run / "fast_replay_blueprints" / "biz_flow_fast_fill_blueprint.json").write_text(json.dumps(blueprint), encoding="utf-8")
    knowledge = {
        "phase": "biz_flow",
        "section": "Configure Target",
        "status": "complete",
        "control_registry": [],
        "dependency_edges": [
            {
                "from": "biz_flow.configure_target.process_step_type",
                "to": "biz_flow.configure_target.target_document_type",
                "relation": "PARENT_VALUE_REVEALS_CHILD",
                "when_parent_value": "Mapping Transformer",
                "child_label": "Target Document Type",
                "evidence": "live before/after DOM state delta",
            }
        ],
        "parents": [],
        "repeatable_rows": [],
        "unexplored_branches": [],
    }
    (run / "portal_form_knowledge" / "biz_flow_form_knowledge.json").write_text(json.dumps(knowledge), encoding="utf-8")
    summary = {
        "phase_sequence": ["biz_flow"],
        "phase_verifications": [{"phase": "biz_flow", "status": status}],
        "section_judge_results": [{"phase": "biz_flow", "pass": judge_pass}],
        "phase_summaries": {},
    }
    (run / "full_dummy_fill_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run


def test_brain_promotes_judged_pass_and_does_not_make_dynamic_selector_primary(tmp_path: Path):
    runs = tmp_path / "runs"
    _make_run(runs, "RUN-1", status="pass", judge_pass=True, selector="input#dds-form-field-123456789", value="Mapping Transformer")
    brain = PortalBrain(tmp_path / "memory" / "portal_brain", PortalBrainPolicy())
    result = brain.bootstrap(runs)
    assert result["scanned_runs"] == 1
    bundle = brain.phase_bundle("biz_flow")
    assert bundle["blueprint"]["verification_status"] == "pass"
    step = bundle["blueprint"]["field_steps"][0]
    assert step["brain_validated_count"] >= 1
    assert step["fallback_label"] == "Process Step Type"
    assert step["selector"] == ""  # generated DDS IDs remain evidence, not primary memory
    assert bundle["knowledge"]["dependency_edges"]


def test_failed_run_is_negative_evidence_and_cannot_replace_validated_value(tmp_path: Path):
    runs = tmp_path / "runs"
    good = _make_run(runs, "RUN-GOOD", status="pass", judge_pass=True, selector="input[name='stepType']", value="Mapping Transformer")
    bad = _make_run(runs, "RUN-BAD", status="failed", judge_pass=False, selector="input#wrong", value="Wrong Value")
    brain = PortalBrain(tmp_path / "memory" / "portal_brain", PortalBrainPolicy())
    brain.ingest_run(good)
    brain.ingest_run(bad)
    bundle = brain.phase_bundle("biz_flow")
    step = bundle["blueprint"]["field_steps"][0]
    assert step["selector"] == "input[name='stepType']"
    assert step["brain_validated_count"] >= 1
    assert bundle["stats"]["failed_runs"] >= 1


def test_deterministic_plan_uses_persistent_brain_and_current_input(tmp_path: Path):
    runs = tmp_path / "runs"
    _make_run(runs, "RUN-1", status="pass", judge_pass=True, selector="input[name='stepType']", value="Mapping Transformer")
    brain = PortalBrain(tmp_path / "memory" / "portal_brain", PortalBrainPolicy())
    brain.bootstrap(runs)

    current = runs / "CURRENT"
    phase_inputs = current / "phase_inputs"
    phase_inputs.mkdir(parents=True)
    input_path = phase_inputs / "biz_flow_input.json"
    input_path.write_text(json.dumps({"objects": {"biz_flow": {"configure_targets": [{"process_steps": [{"type": "Enricher"}]}]}}}), encoding="utf-8")
    manifest = compile_and_attach_plans(
        phase_input_paths={"biz_flow": str(input_path)},
        runs_root=runs,
        current_run_dir=current,
        brain=brain,
    )
    plan = json.loads((current / "deterministic_plans" / "biz_flow_deterministic_plan.json").read_text(encoding="utf-8"))
    action = next(a for a in plan["actions"] if a.get("field_key") == "process_step_type")
    assert action["expected_value"] == "Enricher"
    assert action["executor"] == "playwright-mcp"
    assert action["brain_node_id"].endswith("process_step_type")
    assert plan["long_term_memory"]["enabled"] is True
    assert manifest["long_term_memory"]["enabled"] is True

from hip_id_agent.deterministic_plan_runtime import ordered_keys, sort_controls, annotate_attempt


def test_runtime_consumes_plan_order_not_only_writes_plan_file():
    input_data = {
        "_deterministic_plan": {
            "actions": [
                {"order": 1, "phase": "data_map", "section": "Create Data Map", "operation": "select", "field_key": "map_class", "knowledge_node": "data_map.create_data_map.map_class", "brain_node_id": "brain.map_class", "brain_confidence": 1.0},
                {"order": 2, "phase": "data_map", "section": "Create Data Map", "operation": "fill", "field_key": "map_name", "knowledge_node": "data_map.create_data_map.map_name"},
            ]
        }
    }
    result = ordered_keys(input_data, ["map_name", "map_identifier", "map_class"], phase="data_map")
    assert result == ["map_class", "map_name", "map_identifier"]
    attempt = annotate_attempt({"field": "map_class"}, input_data, "map_class", phase="data_map")
    assert attempt["deterministic_plan_used"] is True
    assert attempt["brain_node_id"] == "brain.map_class"


def test_runtime_sorts_controls_by_brain_plan():
    input_data = {
        "_deterministic_plan": {
            "actions": [
                {"order": 1, "phase": "rule", "section": "Create Rule", "operation": "fill", "field_key": "rule_name"},
                {"order": 2, "phase": "rule", "section": "Create Rule", "operation": "select", "field_key": "rule_type"},
            ]
        }
    }
    controls = [
        {"mapped_rule_key": "rule_type", "label": "Rule Type"},
        {"mapped_rule_key": "description", "label": "Description"},
        {"mapped_rule_key": "rule_name", "label": "Rule Name"},
    ]
    ordered = sort_controls(controls, input_data, key_fields=("mapped_rule_key",), phase="rule")
    assert [row["mapped_rule_key"] for row in ordered] == ["rule_name", "rule_type", "description"]


def test_dds_driver_has_playwright_mcp_primary_path():
    import inspect
    import hip_id_agent.dds_control_driver as driver
    text = inspect.getsource(driver.select_dds_combobox)
    assert "_hip_playwright_mcp_backend" in text
    assert "_broker_click" in text
    assert "_broker_press" in text or "_broker_fill" in text
    assert "_broker_fill" in text or "select_dds_combobox" in text


def test_phase_bundle_migrates_legacy_string_selector_values_without_losing_memory(tmp_path: Path):
    brain = PortalBrain(tmp_path / "memory" / "portal_brain", PortalBrainPolicy())
    phase_path = brain._phase_path("rule")
    phase_path.parent.mkdir(parents=True, exist_ok=True)
    phase_path.write_text(
        json.dumps(
            {
                "schema_version": "hip.portal-brain.v1",
                "phase": "rule",
                "field_nodes": {
                    "rule.create_rule.rule_name": {
                        "node_id": "rule.create_rule.rule_name",
                        "phase": "rule",
                        "section": "Create Rule",
                        "key": "rule_name",
                        "labels": ["Rule Name"],
                        "names": [],
                        "roles": ["textbox"],
                        "control_types": ["input"],
                        # Exact legacy shape that caused AttributeError: values
                        # were selector strings, not metadata dictionaries.
                        "selectors": {"legacy_rule_name": "input[name='ruleName']"},
                        "options": [],
                        "observed_values": ["DELLCoXMLASNXX08C_U-HAUL_RULE"],
                        "input_paths": ["$.objects.rule.name"],
                        "preconditions": [],
                        "expected_effects": [],
                        "order_samples": [1],
                        "required": True,
                        "fill_strategies": ["type_or_set_value"],
                        "validated_count": 0,
                        "candidate_count": 1,
                        "failure_count": 0,
                    }
                },
                "canonical_field_nodes": {},
                "dependency_edges": {},
                "canonical_dependency_edges": {},
                "repeatable_rows": {},
                "hard_gates": {},
                "failure_recovery": {},
                "negative_evidence": {},
                "kb_repairs": {},
                "validated_field_overrides": {},
                "superseded_canonical_edges": {},
                "kb_repair_history": [],
                "kb_revision": 0,
                "page_identity": {},
                "canonical_sources": [],
                "parent_branches": {},
                "unresolved_gaps": {},
                "run_history": [],
                "stats": {},
            }
        ),
        encoding="utf-8",
    )

    bundle = brain.phase_bundle("rule")
    step = bundle["blueprint"]["field_steps"][0]
    assert step["key"] == "rule_name"
    assert step["selector"] == "input[name='ruleName']"
    assert step["fallback_label"] == "Rule Name"


def test_phase_bundle_migrates_selector_lists_and_direct_records(tmp_path: Path):
    brain = PortalBrain(tmp_path / "memory" / "portal_brain", PortalBrainPolicy())
    base = brain._blank_phase("data_map")
    base["field_nodes"] = {
        "data_map.create_data_map.map_name": {
            "node_id": "data_map.create_data_map.map_name",
            "phase": "data_map",
            "section": "Create Data Map",
            "key": "map_name",
            "labels": ["Map Name"],
            "selectors": ["input[name='mapName']", "input#dds-form-field-123456"],
            "observed_values": ["DELLCoXMLASNXX08C_U-HAUL"],
            "candidate_count": 1,
        },
        "data_map.create_data_map.map_identifier": {
            "node_id": "data_map.create_data_map.map_identifier",
            "phase": "data_map",
            "section": "Create Data Map",
            "key": "map_identifier",
            "labels": ["Map Identifier"],
            "selectors": {
                "selector": "input[name='mapIdentifier']",
                "validated_count": "2",
                "candidate_count": "bad-count",
            },
            "observed_values": ["DELLCoXMLASNXX08C"],
            "validated_count": 1,
        },
    }
    brain._phase_path("data_map").write_text(json.dumps(base), encoding="utf-8")

    bundle = brain.phase_bundle("data_map")
    steps = {step["key"]: step for step in bundle["blueprint"]["field_steps"]}
    assert steps["map_name"]["selector"] == "input[name='mapName']"
    assert steps["map_identifier"]["selector"] == "input[name='mapIdentifier']"
