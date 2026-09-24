from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.autonomous_transition_runtime import (
    AutonomousPortalTransitionPlanner,
    choose_dynamic_portal_option,
    operational_world_model_contract,
)
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from hip_id_agent.future_task_agent import HIPFutureTaskExecutor, HIPFutureTaskPlanner, MUTATION_CONFIRMATION, infer_actions
from hip_id_agent.website_world_model import WebsiteWorldModelMemory


def _world(tmp_path: Path) -> WebsiteWorldModelMemory:
    return WebsiteWorldModelMemory(tmp_path / "world", config=AppConfig().brain)


def test_stage4_explicit_dynamic_option_uses_current_portal_label(tmp_path: Path):
    decision = choose_dynamic_portal_option(
        phase="source_transport_profile", label="Interface Type", section="Interface",
        live_options=["FTP", "SFTP HAFT", "HTTPS AS2"], desired_value="SFTP_HAFT",
        world_model=_world(tmp_path),
    )
    assert decision["pass"] is True
    assert decision["selected_option"] == "SFTP HAFT"
    assert decision["source"] == "mission_expected_value"
    assert decision["requires_live_reproof"] is True


def test_stage4_process_step_is_inferred_only_from_unique_live_mission_option(tmp_path: Path):
    decision = choose_dynamic_portal_option(
        phase="biz_flow", label="Process Step Type", section="Configure Target(s)",
        live_options=["Translation", "Passthrough", "Split"], desired_value="",
        mission_context={"process_steps": [{"step_type": "Translation"}]}, world_model=_world(tmp_path),
    )
    assert decision["pass"] is True
    assert decision["selected_option"] == "Translation"
    assert decision["source"] == "mission_context_exact_option"


def test_stage4_ambiguous_dynamic_option_fails_closed(tmp_path: Path):
    decision = choose_dynamic_portal_option(
        phase="biz_flow", label="Process Step Type", section="Configure Target(s)",
        live_options=["Translation", "Passthrough", "Split"], desired_value="",
        mission_context={"notes": "Can use Translation or Passthrough"}, world_model=_world(tmp_path),
    )
    assert decision["pass"] is False
    assert decision["status"] in {"ambiguous_mission_context", "needs_input"}
    assert not decision.get("selected_option")


def test_stage4_validated_memory_branch_reused_only_when_still_live(tmp_path: Path):
    mem = _world(tmp_path)
    promoted = mem.record_verified_portal_choice(
        phase="biz_flow", action="select",
        control={"label": "Process Step Type", "section": "Configure Target(s)", "role": "combobox"},
        choice="Passthrough", available_options=["Translation", "Passthrough"],
        effect_type="dependent_controls_revealed", effect_confidence=0.99,
    )
    assert promoted["choice_branch"]["trust"] == "validated"
    ok = choose_dynamic_portal_option(
        phase="biz_flow", label="Process Step Type", section="Configure Target(s)",
        live_options=["Translation", "Passthrough", "Split"], desired_value="", mission_context={}, world_model=mem,
    )
    assert ok["pass"] is True and ok["selected_option"] == "Passthrough"
    gone = choose_dynamic_portal_option(
        phase="biz_flow", label="Process Step Type", section="Configure Target(s)",
        live_options=["Translation", "Split"], desired_value="", mission_context={}, world_model=mem,
    )
    assert gone["pass"] is False


def test_stage4_transition_planner_uses_live_catalog_and_memory_only_as_bounded_prior(tmp_path: Path):
    mem = _world(tmp_path)
    planner = AutonomousPortalTransitionPlanner(world_model=mem)
    model = {
        "action_catalog": [
            {"semantic_control_key": "toolbar.deploy", "label": "Deploy", "section": "Flow Details", "role": "button", "affordances": ["deploy"]},
            {"semantic_control_key": "background.deploy", "label": "Deployment History", "section": "History", "role": "button", "affordances": ["click"]},
        ]
    }
    plan = planner.plan_next(phase="biz_flow", goal_action="deploy", website_model=model, goal_context="Deploy the flow")
    assert plan["pass"] is True
    assert plan["selected"]["semantic_control_key"] == "toolbar.deploy"
    assert plan["mutation"] is True
    assert plan["requires_governance_for_mutation"] is True
    assert plan["requires_live_reproof"] is True
    assert plan["memory_is_advisory"] is True


def test_stage4_future_task_actions_preserve_requested_order():
    actions = infer_actions("Edit the BizFlow then validate it then save it and deploy it")
    assert actions.index("edit") < actions.index("validate") < actions.index("save") < actions.index("deploy")


def test_stage4_future_task_planner_emits_live_semantic_fallback_when_capability_missing(tmp_path: Path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    graph = HIPCapabilityGraph(tmp_path / "graph")
    planner = HIPFutureTaskPlanner(cfg, graph)
    plan = planner.plan('Deploy bizflow "FLOW_A"', family_hint="bizflows")
    semantic = [x for x in plan["steps"] if x.get("type") == "semantic_action"]
    assert semantic and semantic[0]["action"] == "deploy"
    assert semantic[0]["risk"] == "mutation"
    assert semantic[0]["requires_live_reproof"] is True
    assert plan["world_model_operational"] is True
    assert plan["memory_is_advisory"] is True


def test_stage4_semantic_mutation_still_requires_existing_three_key_gate(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    graph = HIPCapabilityGraph(tmp_path / "graph")
    executor = HIPFutureTaskExecutor(cfg, graph)
    plan = {"steps": [{"type": "semantic_action", "action": "deploy", "risk": "mutation"}]}
    blocked = executor._mutation_gate(plan, allow_portal_mutation=False, confirmation="")
    assert blocked["pass"] is False
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    allowed = executor._mutation_gate(plan, allow_portal_mutation=True, confirmation=MUTATION_CONFIRMATION)
    assert allowed["pass"] is True


def test_stage4_contract_covers_full_operational_workflow():
    contract = operational_world_model_contract()
    for action in ["create", "edit", "save", "validate", "clone", "migrate", "deploy", "add_row"]:
        assert action in contract["supported_goal_actions"]
    assert contract["memory_proposes_live_evidence_authorizes"] is True
    assert contract["mutation_governance_preserved"] is True


def test_stage4_source_contract_operationalizes_dynamic_options_and_memory():
    dds = Path("hip_id_agent/dds_control_driver.py").read_text(encoding="utf-8")
    auto = Path("hip_id_agent/autonomous_form_runtime.py").read_text(encoding="utf-8")
    future = Path("hip_id_agent/future_task_agent.py").read_text(encoding="utf-8")
    assert "choose_dynamic_portal_option" in dds
    assert "record_verified_portal_choice" in dds
    assert "dynamic_option_decisions" in auto
    assert "WebsiteUnderstandingEngine" in auto
    assert 'typ == "semantic_action"' in future
    assert "world_model_hints" in future


def test_stage4_config_enables_operational_model_in_all_shipped_profiles():
    from hip_id_agent.config import load_config
    for path in ["config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"]:
        cfg = load_config(path)
        assert cfg.autonomous_form.operational_world_model_enabled is True
        assert cfg.autonomous_form.dynamic_option_resolution_enabled is True
        assert cfg.autonomous_form.transition_planning_enabled is True
        assert cfg.autonomous_form.transition_plan_requires_live_reproof is True
        assert cfg.autonomous_form.mutation_governance_always_required is True


def test_stage4_certified_planner_preserves_semantic_action_entity(tmp_path: Path):
    from hip_id_agent.certified_future_task_agent import CertifiedHIPFutureTaskPlanner
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    graph = HIPCapabilityGraph(tmp_path / "graph")
    plan = CertifiedHIPFutureTaskPlanner(cfg, graph).plan('Deploy bizflow "FLOW_STAGE4"')
    semantic = [s for sub in plan.get("subtasks", []) for s in sub.get("steps", []) if s.get("type") == "semantic_action"]
    assert semantic
    assert semantic[0].get("entity") == "FLOW_STAGE4"
    assert semantic[0].get("action") == "deploy"


def test_stage4_certified_executor_has_explicit_semantic_action_path():
    source = Path("hip_id_agent/certified_future_task_agent.py").read_text(encoding="utf-8")
    assert 'semantic_direct = typ == "semantic_action"' in source
    assert "click_semantic_affordance" in source
    assert "last_click_dispatch_evidence" in source
    assert "never replayed through a second physical executor" in source
