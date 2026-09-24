from __future__ import annotations

import json
from pathlib import Path

import yaml

from hip_id_agent.config import load_config
from hip_id_agent.skill_induction import InducedSkillLibrary, SkillInductionEngine
from hip_id_agent.universal_portal_operator import UniversalPortalTaskPlanner
from hip_id_agent.capability_graph import HIPCapabilityGraph


def _workflow():
    return [
        {"type": "navigate", "target": "https://old.invalid/portal", "risk": "read"},
        {"type": "learn_surface", "deep": False, "risk": "read"},
        {"type": "navigate_label", "label": "Partner Settings", "risk": "read"},
        {"type": "search", "value": "OLD-CUSTOMER", "risk": "read"},
        {"type": "semantic_action", "action": "edit", "label": "Edit", "risk": "draft"},
        {"type": "fill_from_input", "input_json": "old.json", "input_root": "$.objects.partner", "risk": "draft"},
        {"type": "semantic_action", "action": "save", "label": "Save / Update", "risk": "mutation"},
        {"type": "semantic_action", "action": "deploy", "label": "Deploy", "risk": "mutation"},
    ]


def _blueprint():
    return {
        "schema_version": "hip.induced-form-skill.v1",
        "input_root": "$.objects.partner",
        "fields": [
            {
                "input_path": "$.objects.partner.name",
                "field_key": "name",
                "action": "fill_text",
                "section": "Details",
                "semantic_locator": {"labels": ["Partner Name"], "roles": ["textbox"]},
                "verification": "exact_committed_control_value",
            }
        ],
        "values_stored": False,
        "selectors_stored": False,
        "coordinates_stored": False,
        "live_reproof_required": True,
    }


def test_v239_shipped_config_and_version_contract():
    assert Path("pyproject.toml").read_text(encoding="utf-8").find('version = "2.4.3"') >= 0
    for name in ("config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"):
        raw = yaml.safe_load(Path(name).read_text(encoding="utf-8"))
        s = raw["skill_induction"]
        assert s["enabled"] is True
        assert s["fast_replay_enabled"] is True
        assert s["require_live_reproof_on_replay"] is True
        assert s["store_values"] is False
        assert s["store_selectors"] is False
        assert s["store_coordinates"] is False
        assert float(s["confidence_half_life_days"]) > 0
        assert float(s["stale_after_days"]) > float(s["confidence_half_life_days"])


def test_skill_induction_saves_only_value_free_workflow_and_blueprint(tmp_path):
    lib = InducedSkillLibrary(tmp_path, min_verified_successes=1, min_replay_confidence=0.1)
    row = lib.induce(
        task='Open Partner Settings, edit "ACME", fill from input json, save and deploy',
        actions=["edit", "save", "deploy"],
        target_area="Partner Settings",
        input_root="$.objects.partner",
        workflow_steps=_workflow(),
        form_blueprints=[_blueprint()],
        page_families=["partner_settings"],
        run_id="run-1",
        exact_verified=True,
        evidence={"pass": True},
    )
    assert row["status"] == "validated"
    stored = json.loads((tmp_path / "induced_skills.json").read_text(encoding="utf-8"))
    text = json.dumps(stored, sort_keys=True)
    assert "OLD-CUSTOMER" not in text
    assert "https://old.invalid/portal" not in text
    assert '"selector"' not in text
    assert '"xpath"' not in text.lower()
    assert '"coordinates"' not in text.lower()
    assert stored["values_stored"] is False


def test_skill_activation_reuses_workflow_but_current_values_and_url(tmp_path):
    lib = InducedSkillLibrary(tmp_path, min_verified_successes=1, min_replay_confidence=0.1)
    learned = lib.induce(
        task='Open Partner Settings, edit "ACME", fill from input json, save and deploy',
        actions=["edit", "save", "deploy"], target_area="Partner Settings",
        input_root="$.objects.partner", workflow_steps=_workflow(), form_blueprints=[_blueprint()],
        page_families=["partner_settings"], run_id="run-1", exact_verified=True,
    )
    engine = SkillInductionEngine(lib, min_match_score=0.1)
    activation = engine.activate(
        task='Open Partner Settings, edit "NEWCO", fill from input json, save and deploy',
        actions=["edit", "save", "deploy"], target_area="Partner Settings",
        input_root="$.objects.partner", input_json="new-input.json",
        start_url="https://current.portal/app", entity="NEWCO",
    )
    assert activation["active"] is True
    assert activation["skill"]["skill_id"] == learned["skill_id"]
    steps = activation["steps"]
    assert steps[0]["target"] == "https://current.portal/app"
    assert any(x.get("type") == "search" and x.get("value") == "NEWCO" for x in steps)
    assert any(x.get("type") == "fill_from_input" and x.get("input_json") == "new-input.json" for x in steps)
    assert any(x.get("action") == "deploy" for x in steps)
    dumped = json.dumps(steps)
    assert "OLD-CUSTOMER" not in dumped
    assert "old.json" not in dumped


def test_skill_activation_does_not_smuggle_unrequested_mutation(tmp_path):
    lib = InducedSkillLibrary(tmp_path, min_verified_successes=1, min_replay_confidence=0.1)
    lib.induce(
        task='edit partner, save and deploy', actions=["edit", "save", "deploy"], target_area="partner",
        input_root="$.objects.partner", workflow_steps=_workflow(), form_blueprints=[], page_families=["partner"],
        run_id="run-1", exact_verified=True,
    )
    engine = SkillInductionEngine(lib, min_match_score=0.0)
    active = engine.activate(
        task='edit partner from input json', actions=["edit"], target_area="partner",
        input_root="$.objects.partner", input_json="current.json", start_url="https://portal", entity="",
    )
    assert active["active"] is True
    labels = [str(x.get("action") or x.get("label") or "").lower() for x in active["steps"]]
    # Save is the commit implied by an edit+input request, but Deploy is not.
    assert any("save" in x for x in labels)
    assert not any("deploy" in x for x in labels)


def test_skill_drift_demotes_after_repeated_failures(tmp_path):
    lib = InducedSkillLibrary(tmp_path, min_verified_successes=1, min_replay_confidence=0.1, demote_after_failures=2)
    row = lib.induce(
        task="deploy partner", actions=["deploy"], target_area="partner", input_root="",
        workflow_steps=[{"type": "semantic_action", "action": "deploy", "label": "Deploy", "risk": "mutation"}],
        form_blueprints=[], page_families=["partner"], run_id="r1", exact_verified=True,
    )
    assert row["status"] == "validated"
    first = lib.record_outcome(row["skill_id"], success=False, reason="live label drift", run_id="r2")
    assert first["status"] == "validated"
    second = lib.record_outcome(row["skill_id"], success=False, reason="live label drift", run_id="r3")
    assert second["status"] == "drift_suspect"
    assert lib.match("deploy partner", actions=["deploy"], target_area="partner") == []


def test_universal_planner_actually_instantiates_validated_skill(tmp_path):
    cfg = load_config("config.yaml")
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.portal.base_url = "https://current.portal"
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    planner = UniversalPortalTaskPlanner(cfg, graph)
    planner.skills.induce(
        task='Open Partner Settings, edit "ACME", fill from input json, save and deploy',
        actions=["edit", "save", "deploy"], target_area="Partner Settings", input_root="$.objects.partner",
        workflow_steps=_workflow(), form_blueprints=[_blueprint()], page_families=["partner_settings"],
        run_id="r1", exact_verified=True,
    )
    plan = planner.plan(
        'Open Partner Settings, edit "NEWCO", fill from input json, save and deploy',
        input_json="./new-input.json", input_root="$.objects.partner", start_url="https://current.portal", deep_learn=True,
    )
    assert plan["execution_mode"] == "induced_skill_fast_replay_with_live_reproof"
    assert plan["skill_activation"]["active"] is True
    assert plan["skill_activation"]["values_reused"] is False
    assert plan["skill_activation"]["live_reproof_required"] is True
    assert any(x.get("skill_replay") for x in plan["steps"])
    assert plan["adaptive_fallback_steps"]


def test_unknown_explicit_webapp_activity_is_planned_as_guarded_live_goal(tmp_path):
    cfg = load_config("config.yaml")
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / str(cfg.brain.directory or "portal_brain"))
    plan = UniversalPortalTaskPlanner(cfg, graph).plan("Archive the currently selected configuration")
    live = [x for x in plan["steps"] if x.get("type") == "live_goal"]
    assert live and "archive" in live[0]["goal"].lower()
    assert plan["mutation_required"] is True
    assert any("archive" in str(x).lower() for x in plan["mutation_actions"])


def test_control_center_and_backend_expose_induced_skill_library():
    backend = Path("backend/app.py").read_text(encoding="utf-8")
    html = Path("webui/index.html").read_text(encoding="utf-8")
    js = Path("webui/app.js").read_text(encoding="utf-8")
    assert '@app.get("/api/skills")' in backend
    assert "Induced skill library" in html
    assert "skillLibraryTable" in html
    assert "loadSkillLibrary" in js
    assert "/api/skills?" in js


def test_executor_source_induces_from_actual_successful_execution_and_has_drift_fallback():
    text = Path("hip_id_agent/universal_portal_operator.py").read_text(encoding="utf-8")
    assert "actual_successful_execution" in text
    assert "induce_from_execution" in text
    assert "adaptive_live_discovery_after_skill_drift" in text
    assert "failed_risk != \"mutation\"" in text
    assert "_execute_adaptive_goal" in text
