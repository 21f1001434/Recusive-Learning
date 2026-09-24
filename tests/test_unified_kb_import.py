from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.form_knowledge_plan import compile_phase_plan
from hip_id_agent.portal_brain import PortalBrain, PortalBrainPolicy


ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "knowledge_base" / "HIP_Unified_Deep_KB.json"
KG = ROOT / "knowledge_base" / "HIP_Unified_Knowledge_Graph.json"
INPUT = ROOT / "examples" / "uhaul_poasn_full_dummy_input.json"


def make_brain(tmp_path: Path) -> PortalBrain:
    return PortalBrain(
        tmp_path / "portal_brain",
        PortalBrainPolicy(
            unified_kb_path=str(KB),
            unified_kg_path=str(KG),
            unified_kb_required=True,
        ),
    )


def test_imports_all_canonical_page_models_and_is_idempotent(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    first = brain.import_unified_kb()
    assert first["status"] == "ok"
    assert set(first["phases"]) == {
        "data_map",
        "source_document_type",
        "target_document_type",
        "rule",
        "source_transport_profile",
        "target_transport_profile",
        "biz_flow",
    }
    assert sum(v["canonical_field_nodes"] for v in first["phases"].values()) == 80
    second = brain.import_unified_kb()
    assert second["status"] == "unchanged"
    assert second["digest"] == first["digest"]


def test_canonical_dependencies_identity_and_negative_evidence_are_preserved(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    brain.import_unified_kb()

    source = brain.phase_bundle("source_transport_profile")
    assert source["blueprint"]["page_identity"]["url_pattern"] == "/securelink/transportprofiles"
    edges = source["knowledge"]["dependency_edges"]
    assert any(e.get("when_parent_value") == "Yes" and "existing_account_name" in str(e.get("to")) for e in edges)
    assert any(e.get("when_parent_value") == "No" and "subscription_folder" in str(e.get("to")) for e in edges)

    rule = brain.phase_bundle("rule")
    assert rule["blueprint"]["hard_gates"]
    assert rule["blueprint"]["negative_evidence"]


def test_plan_uses_exact_input_path_and_starts_with_active_surface_gate(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    brain.import_unified_kb()
    payload = json.loads(INPUT.read_text(encoding="utf-8"))

    bundle = brain.phase_bundle("source_transport_profile")
    plan = compile_phase_plan(
        phase="source_transport_profile",
        payload=payload,
        learned_blueprint=bundle["blueprint"],
        blueprint_path=Path(bundle["source_path"]),
        exploration_knowledge=bundle["knowledge"],
        exploration_path=Path(bundle["source_path"]),
    )
    assert plan["actions"][0]["operation"] == "verify_active_surface"
    profile = next(a for a in plan["actions"] if a.get("field_key") == "profile_name")
    assert profile["expected_value"] == "SFTP_U-HAUL_ASN_PC_SRC_IB"
    assert profile["input_path"] == "$.objects.source_transport_profile.profile_name"
    assert plan["canonical_hard_gates"]
    assert any(a["operation"] == "enforce_canonical_gate" for a in plan["actions"])


def test_bizflow_canonical_composite_actions_map_to_runtime_schema(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    brain.import_unified_kb()
    payload = json.loads(INPUT.read_text(encoding="utf-8"))
    bundle = brain.phase_bundle("biz_flow")
    plan = compile_phase_plan(
        phase="biz_flow",
        payload=payload,
        learned_blueprint=bundle["blueprint"],
        blueprint_path=Path(bundle["source_path"]),
        exploration_knowledge=bundle["knowledge"],
        exploration_path=Path(bundle["source_path"]),
    )
    attributes = next(a for a in plan["actions"] if a.get("operation") == "fill_flow_attributes")
    assert len(attributes["expected_value"]) == 2
    assert attributes["input_path"] == "$.objects.biz_flow.flow_identifiers.conditions"
    process_steps = next(a for a in plan["actions"] if a.get("operation") == "fill_process_steps")
    assert len(process_steps["expected_value"]) == 2
    assert process_steps["input_path"] == "$.objects.biz_flow.process_steps"
