from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.action_model import HIPActionModel, build_phase_task_plan, course_architecture_manifest
from hip_id_agent.config import AppConfig
from hip_id_agent.hip_intelligence_mcp import HIPIntelligenceMCPBackend
from hip_id_agent.stateful_form_runtime import resolve_stateful_control_diagnostics
from hip_id_agent.trajectory_memory import TrajectoryMemory
from hip_id_agent.web_representation import build_web_representation, representation_drift


def _controls():
    return [
        {
            "semantic_key": "map_identifier",
            "label": "Map Identifier",
            "section": "Map Identifier",
            "role": "textbox",
            "type": "text",
            "framework_key": "mapIdentifier",
            "selector": "#map-id",
            "visible": True,
            "interactable": True,
            "value": "SECRET-CUSTOMER-VALUE",
        },
        {
            "semantic_key": "table_search",
            "label": "Table search",
            "section": "",
            "role": "searchbox",
            "type": "search",
            "framework_key": "tableSearch",
            "selector": "#search",
            "visible": True,
            "interactable": True,
            "value": "",
        },
    ]


def _node():
    return {
        "node_id": "data_map.create_map.map_identifier",
        "phase": "data_map",
        "section": "Create Map",
        "field_key": "map_identifier",
        "action": "fill_text",
        "semantic_locator": {"labels": ["Map Identifier"], "names": ["mapIdentifier"]},
        "required": True,
    }


def test_web_representation_is_value_free_and_stable():
    rep = build_web_representation(
        phase="data_map", url="https://developer.dell.com/hybrid-integrations/bizlink/map?x=1",
        controls=_controls(), surface_gate={"pass": True},
    )
    serialized = json.dumps(rep)
    assert "SECRET-CUSTOMER-VALUE" not in serialized
    assert rep["values_stored"] is False
    assert len(rep["embedding"]) == 256
    same = build_web_representation(
        phase="data_map", url="https://developer.dell.com/hybrid-integrations/bizlink/map?y=2",
        controls=_controls(), surface_gate={"pass": True},
    )
    assert rep["structural_fingerprint"] == same["structural_fingerprint"]
    assert representation_drift(same, rep)["drift_detected"] is False


def test_action_model_actor_critic_and_safe_search():
    model = HIPActionModel()
    rep = build_web_representation(phase="data_map", url="https://x/map", controls=_controls())
    binding = {
        "resolved": True,
        "reason": "resolved",
        "selected_identity": "map_identifier|||map_identifier|mapidentifier||textbox|map_identifier",
        "best_score": 210,
        "score_margin": 100,
        "control": _controls()[0],
    }
    plan = model.plan(node=_node(), representation=rep, binding=binding, surface_gate={"pass": True})
    assert plan["selected_action"] == "execute_bound_action"
    assert plan["destructive_actions_considered"] is False
    critic = model.critique(
        node=_node(), action_plan=plan,
        outcome={"success": True, "reason": "exact", "transaction_proof": {"stability": {"stable": True}, "protected_state_changes": []}},
    )
    assert critic["pass"] is True
    lost = model.plan(node=_node(), representation=rep, binding={"resolved": False}, surface_gate={"fatal": ["no form"]})
    assert lost["selected_action"] == "stop_surface_lost"


def test_trajectory_memory_learns_success_and_failure_without_values(tmp_path: Path):
    memory = TrajectoryMemory(tmp_path)
    rep = build_web_representation(phase="data_map", url="https://x/map", controls=_controls())
    action_plan = {
        "selected_action": "execute_bound_action", "selected_control_identity": "stable-id",
        "binding_score": 200, "binding_margin": 80,
        "candidate_actions": [
            {"action": "execute_bound_action", "score": 1.0, "allowed": True},
            {"action": "wait_for_rerender", "score": 0.5, "allowed": True},
        ],
    }
    memory.record(
        phase="data_map", family="data_map", node=_node(), representation_before=rep,
        action_plan=action_plan, outcome={"success": True, "reason": "exact"}, representation_after=rep,
    )
    memory.record(
        phase="data_map", family="data_map", node=_node(), representation_before=rep,
        action_plan={"selected_action": "wait_for_rerender", "selected_control_identity": "bad-id"},
        outcome={"success": False, "reason": "HIP_PHASE_AMBIGUOUS_CONTROL_BINDING"}, representation_after=rep,
    )
    matches = memory.retrieve(phase="data_map", family="data_map", node=_node(), representation=rep)
    assert matches
    assert memory.preferred_control_identities(matches)[0] == "stable-id"
    text = (tmp_path / "trajectories.jsonl").read_text()
    assert "SECRET-CUSTOMER-VALUE" not in text
    assert '"values_stored": false' in text
    preference_text = (tmp_path / "preference_pairs.jsonl").read_text()
    assert '"winning_action": "execute_bound_action"' in preference_text
    assert "SECRET-CUSTOMER-VALUE" not in preference_text


def test_memory_prior_can_break_a_semantic_tie_safely():
    controls = [
        {"semantic_key": "map_identifier", "label": "Select", "section": "Map Identifier", "role": "textbox", "type": "text", "framework_key": "a", "selector": "#a", "interactable": True},
        {"semantic_key": "map_identifier", "label": "Select", "section": "Map Identifier", "role": "textbox", "type": "text", "framework_key": "b", "selector": "#b", "interactable": True},
    ]
    base = resolve_stateful_control_diagnostics(controls, _node(), min_score=0, min_margin=14)
    assert base["resolved"] is False
    preferred_identity = base["candidates"][1]["identity"]
    node = {**_node(), "_preferred_control_identities": [preferred_identity]}
    learned = resolve_stateful_control_diagnostics(controls, node, min_score=0, min_margin=14)
    assert learned["resolved"] is True
    assert learned["selected_identity"] == preferred_identity


@pytest.mark.asyncio
async def test_hip_intelligence_mcp_roundtrip(tmp_path: Path):
    cfg = AppConfig()
    cfg.mcp.hip_intelligence_mcp_command = sys.executable
    cfg.mcp.hip_intelligence_mcp_args = [
        "-m", "hip_id_agent.hip_intelligence_mcp_server", "--memory-dir", str(tmp_path / "memory")
    ]
    backend = HIPIntelligenceMCPBackend.from_config(cfg, run_dir=tmp_path / "run", memory_dir=tmp_path / "memory")
    await backend.start()
    try:
        rep = await backend.build_representation({
            "phase": "data_map", "url": "https://x/map", "controls": _controls(), "surface_gate": {"pass": True}
        })
        assert rep["schema_version"] == "hip.web-representation.v1"
        plan = await backend.plan_action({
            "node": _node(), "representation": rep,
            "binding": {"resolved": False, "reason": "ambiguous candidate margin"},
            "surface_gate": {"pass": True}, "memory_matches": [],
        })
        assert plan["selected_action"] == "stop_ambiguous_binding"
    finally:
        await backend.close()


def test_course_manifest_maps_architecture_without_multion():
    manifest = course_architecture_manifest()
    assert manifest["web_representation_model"]["implemented"] is True
    assert manifest["planner_actor_critic"]["implemented"] is True
    assert manifest["memory_personalization_engine"]["stores_customer_values"] is False
    assert manifest["multion_dependency"] is False
    assert "HIP Intelligence MCP" in manifest["browser_tools"]


def test_hierarchical_phase_plan_separates_actions_and_verification():
    graph = {
        "phase": "data_map",
        "graph_id": "g1",
        "nodes": [
            {**_node(), "expected_value": "value", "depends_on": []},
            {"node_id": "n2", "field_key": "map_name", "section": "Create Map", "action": "fill_text", "expected_value": "x", "depends_on": [_node()["node_id"]]},
        ],
    }
    plan = build_phase_task_plan(graph)
    assert plan["task_count"] == 2
    assert plan["parallel_execution"] is False
    assert plan["tasks"][1]["depends_on"] == [_node()["node_id"]]
    assert "exact target value" in plan["tasks"][0]["verification"]


def test_require_mcp_cli_profile_enforces_all_three_services():
    from hip_id_agent.cli import enforce_required_mcp_profile

    cfg = AppConfig()
    cfg.mcp.use_playwright_mcp = False
    cfg.mcp.use_chrome_devtools_mcp = False
    cfg.mcp.use_hip_intelligence_mcp = False
    cfg.mcp.hip_intelligence_mcp_required = False
    enforce_required_mcp_profile(cfg)
    assert cfg.mcp.browser_backend == "mcp"
    assert cfg.mcp.use_playwright_mcp is True
    assert cfg.mcp.use_chrome_devtools_mcp is True
    assert cfg.mcp.use_hip_intelligence_mcp is True
    assert cfg.mcp.hip_intelligence_mcp_required is True
