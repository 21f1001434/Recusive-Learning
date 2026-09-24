from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.adaptive_kb_repair import AdaptiveKBRepairEngine
from hip_id_agent.deterministic_plan_runtime import apply_live_exploration_overlay
from hip_id_agent.portal_brain import PortalBrain, PortalBrainPolicy


def _knowledge(relation: str = "PARENT_VALUE_REVEALS_CHILD"):
    return {
        "phase": "biz_flow",
        "section": "Configure Target",
        "control_registry": [
            {"key": "process_step_type", "section": "Configure Target", "label": "Process Step Type", "role": "combobox", "options": ["Mapping Transformer", "Enricher"]},
        ],
        "dependency_edges": [
            {
                "from": "biz_flow.configure_target.process_step_type",
                "to": "biz_flow.configure_target.rule",
                "relation": relation,
                "when_parent_value": "Mapping Transformer",
                "confidence": 1.0,
            }
        ],
        "parents": [
            {
                "key": "process_step_type",
                "branches": [
                    {"value": "Mapping Transformer", "observed": True, "selection": {"success": True}}
                ],
            }
        ],
    }


def test_validated_live_edge_repairs_missing_canonical_fact(tmp_path: Path):
    brain = PortalBrain(tmp_path / "brain", PortalBrainPolicy(self_heal_kb=True, kb_repair_min_confirmations=1))
    data = brain._load_phase("biz_flow")
    engine = AdaptiveKBRepairEngine(brain)
    result = engine.reconcile_phase(
        phase="biz_flow",
        data=data,
        knowledge_docs=[_knowledge()],
        trust="validated",
        trust_evidence={"verification_status": "pass", "judge_pass": True},
        run_id="RUN-1",
    )
    brain._save_phase("biz_flow", data)
    assert result["applied_repairs"]
    bundle = brain.phase_bundle("biz_flow")
    assert any(e.get("when_parent_value") == "Mapping Transformer" and str(e.get("to", "")).endswith(".rule") for e in bundle["knowledge"]["dependency_edges"])
    assert bundle["knowledge"]["adaptive_kb"]["kb_revision"] == 1


def test_candidate_observation_cannot_modify_effective_kb(tmp_path: Path):
    brain = PortalBrain(tmp_path / "brain", PortalBrainPolicy(self_heal_kb=True, kb_repair_min_confirmations=1))
    data = brain._load_phase("biz_flow")
    engine = AdaptiveKBRepairEngine(brain)
    result = engine.reconcile_phase(
        phase="biz_flow",
        data=data,
        knowledge_docs=[_knowledge()],
        trust="candidate",
        trust_evidence={"verification_status": "pass_with_warnings", "judge_pass": False},
        run_id="RUN-CANDIDATE",
    )
    brain._save_phase("biz_flow", data)
    assert not result["applied_repairs"]
    assert not brain.phase_bundle("biz_flow")["knowledge"]["dependency_edges"]


def test_canonical_fact_requires_repeated_explicit_opposite_evidence_to_supersede(tmp_path: Path):
    brain = PortalBrain(tmp_path / "brain", PortalBrainPolicy(self_heal_kb=True, kb_supersede_min_confirmations=2))
    data = brain._load_phase("biz_flow")
    edge_id = "canonical-edge"
    data["canonical_dependency_edges"][edge_id] = {
        "edge_id": edge_id,
        "from": "biz_flow.configure_target.process_step_type",
        "to": "biz_flow.configure_target.rule",
        "relation": "REVEALS_OR_ENABLES",
        "when_parent_value": "Mapping Transformer",
        "canonical_count": 1,
        "trust_class": "canonical",
    }
    engine = AdaptiveKBRepairEngine(brain)
    opposite = _knowledge("PARENT_VALUE_HIDES_CHILD")
    first = engine.reconcile_phase(
        phase="biz_flow", data=data, knowledge_docs=[opposite], trust="validated",
        trust_evidence={"verification_status": "pass", "judge_pass": True}, run_id="RUN-1"
    )
    assert not first["applied_repairs"]
    second = engine.reconcile_phase(
        phase="biz_flow", data=data, knowledge_docs=[opposite], trust="validated",
        trust_evidence={"verification_status": "pass", "judge_pass": True}, run_id="RUN-2"
    )
    brain._save_phase("biz_flow", data)
    assert second["applied_repairs"]
    assert data["superseded_canonical_edges"]
    effective = brain.phase_bundle("biz_flow")["knowledge"]["dependency_edges"]
    assert not any(e.get("relation") == "REVEALS_OR_ENABLES" and str(e.get("to", "")).endswith(".rule") for e in effective)
    assert any(e.get("relation") == "HIDES_OR_DISABLES" for e in effective)


def test_live_exploration_overlay_updates_same_run_plan_order():
    input_data = {
        "_deterministic_plan": {
            "actions": [
                {"order": 1, "operation": "select", "field_key": "rule", "knowledge_node": "biz_flow.target.rule"},
                {"order": 2, "operation": "select", "field_key": "process_step_type", "knowledge_node": "biz_flow.target.process_step_type"},
            ]
        }
    }
    result = apply_live_exploration_overlay(input_data, _knowledge())
    assert result["status"] == "applied"
    actions = input_data["_deterministic_plan"]["actions"]
    assert actions[0]["field_key"] == "process_step_type"
    assert actions[1]["field_key"] == "rule"
    assert actions[1]["preconditions"]


def test_self_healed_export_preserves_original_kb(tmp_path: Path):
    original = tmp_path / "kb.json"
    graph = tmp_path / "kg.json"
    original.write_text(json.dumps({"metadata": {"version": "original"}, "portal_page_models": {}}), encoding="utf-8")
    graph.write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
    brain = PortalBrain(
        tmp_path / "brain",
        PortalBrainPolicy(unified_kb_path=str(original), unified_kg_path=str(graph), self_heal_kb=True),
    )
    before = original.read_bytes()
    result = brain.export_corrected_kb(tmp_path / "out")
    assert Path(result["corrected_kb"]).is_file()
    assert Path(result["corrected_graph"]).is_file()
    assert original.read_bytes() == before
