from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import backend.app as backend_app
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.certified_future_task_agent import CertifiedHIPFutureTaskPlanner, CertifiedHIPFutureTaskExecutor
from hip_id_agent.cli import app
from hip_id_agent.config import AppConfig
from hip_id_agent.future_task_agent import MUTATION_CONFIRMATION


def _seed_family(graph: HIPCapabilityGraph, family: str, *, required_replay: str = ""):
    graph.observe_page(page_family=family, url=f"https://developer.dell.com/{family}", title=family)
    search = graph.observe_capability(page_family=family, kind="search", label="Search", selector=f"#{family}-search", role="searchbox")
    expand = graph.observe_capability(page_family=family, kind="expand", label="Expand", selector=f"#{family}-expand")
    edit = graph.observe_capability(page_family=family, kind="row_action", label="Edit", selector=f"#{family}-edit")
    graph.observe_capability(page_family=family, kind="row_action", label="Clone", selector=f"#{family}-clone")
    graph.observe_capability(page_family=family, kind="row_action", label="Migrate", selector=f"#{family}-migrate")
    graph.observe_capability(page_family=family, kind="row_action", label="Deploy", selector=f"#{family}-deploy")
    graph.observe_capability(page_family=family, kind="row_action", label="Delete", selector=f"#{family}-delete")
    if family != "data_maps":
        graph.observe_capability(page_family=family, kind="form_field", label="Name", selector=f"#{family}-name")
    graph.observe_api_contract(page_family=family, method="GET", url=f"https://host/api/{family}", response_status=200, caused_by_capability_id=edit["capability_id"])
    if family == "data_maps":
        graph.observe_replay_profile(
            page_family=family,
            name="entity_edit",
            verified=True,
            steps=[
                {"type":"search","capability_id":search["capability_id"],"value_source":"current_task.entity"},
                {"type":"expand","capability_id":expand["capability_id"],"value_source":"current_task.entity"},
                {"type":"action","action":"edit","capability_id":edit["capability_id"],"value_source":"current_task.entity"},
            ],
        )
    elif required_replay:
        graph.observe_replay_profile(page_family=family, name=required_replay, verified=True, steps=[{"type":"action","capability_id":edit["capability_id"]}])
    return search, expand, edit


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.aia.enabled = False
    return cfg


def test_capability_graph_v7_persists_value_free_cross_family_task_replay(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    graph.observe_task_replay_profile(
        name="search_edit_then_rule_view",
        families=["data_maps", "rules"],
        verified=True,
        steps=[
            {"type":"search","page_family":"data_maps","capability_id":"cap-a","action":"search","value_source":"current_task.entity"},
            {"type":"action","page_family":"rules","capability_id":"cap-b","action":"view","value_source":"current_task.entity"},
        ],
    )
    graph.save()
    loaded = HIPCapabilityGraph(tmp_path)
    assert loaded.SCHEMA_VERSION == "hip.capability-graph.v7"
    assert loaded.manifest()["task_replay_profile_count"] == 1
    text = loaded.path.read_text(encoding="utf-8")
    assert "current_task.entity" in text
    assert "MAP_CUSTOMER_SECRET" not in text
    assert '"values_stored": false' in text


def test_certified_planner_uses_certified_datamap_replay(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    _seed_family(graph, "data_maps")
    graph.save()
    plan = CertifiedHIPFutureTaskPlanner(_cfg(), graph).plan('Search data map "MAP_A", expand it, then Edit')
    assert plan["pass"] is True
    assert plan["families"] == ["data_maps"]
    sub = plan["subtasks"][0]
    assert sub["certified_family_ready"] is True
    assert sub["execution_mode"] == "certified_deterministic_replay"
    assert sub["entity"] == "MAP_A"


def test_certified_planner_can_decompose_cross_family_task(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    _seed_family(graph, "data_maps")
    _seed_family(graph, "rules", required_replay="create_rule")
    graph.save()
    task = 'Search data map "MAP_A", expand it, then Edit; then search rule "RULE_A", expand it, then Edit'
    plan = CertifiedHIPFutureTaskPlanner(_cfg(), graph).plan(task)
    assert plan["pass"] is True
    assert plan["families"] == ["data_maps", "rules"]
    assert [s["entity"] for s in plan["subtasks"]] == ["MAP_A", "RULE_A"]
    assert all(s["certified_family_ready"] for s in plan["subtasks"])


def test_unresolved_action_is_executable_only_with_adaptive_recovery(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    _seed_family(graph, "data_maps")
    graph.save()
    planner = CertifiedHIPFutureTaskPlanner(_cfg(), graph)
    yes = planner.plan('Search data map "MAP_A", expand it, then History', allow_adaptive_exploration=True)
    no = planner.plan('Search data map "MAP_A", expand it, then History', allow_adaptive_exploration=False)
    assert yes["pass"] is True
    assert yes["unresolved_step_count"] >= 1
    assert yes["subtasks"][0]["execution_mode"] == "adaptive_exploration_required"
    assert no["pass"] is False


def test_unresolved_mutation_still_requires_three_part_gate(tmp_path, monkeypatch):
    graph = HIPCapabilityGraph(tmp_path)
    # Search/expand only: Deploy remains unresolved but must still be recognized as mutation.
    graph.observe_page(page_family="data_maps", url="https://developer.dell.com/data_maps")
    graph.observe_capability(page_family="data_maps", kind="search", label="Search", selector="#search")
    graph.observe_capability(page_family="data_maps", kind="expand", label="Expand", selector="#expand")
    plan = CertifiedHIPFutureTaskPlanner(_cfg(), graph).plan('Search data map "MAP_A", expand it, then Deploy')
    assert plan["mutation_required"] is True
    executor = CertifiedHIPFutureTaskExecutor(_cfg(), graph)
    monkeypatch.delenv("HIP_ALLOW_PORTAL_MUTATION", raising=False)
    assert executor._aggregate_mutation_gate(plan, allow_portal_mutation=True, confirmation=MUTATION_CONFIRMATION)["pass"] is False
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    assert executor._aggregate_mutation_gate(plan, allow_portal_mutation=True, confirmation=MUTATION_CONFIRMATION)["pass"] is True


def test_verified_task_replay_is_reused_by_certified_planner(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    search, expand, edit = _seed_family(graph, "data_maps")
    graph.observe_task_replay_profile(
        name="future_search_then_expand_then_edit",
        families=["data_maps"],
        verified=True,
        steps=[
            {"type":"search","page_family":"data_maps","capability_id":search["capability_id"],"action":"search","value_source":"current_task.entity"},
            {"type":"expand","page_family":"data_maps","capability_id":expand["capability_id"],"action":"expand","value_source":"current_task.entity"},
            {"type":"action","page_family":"data_maps","capability_id":edit["capability_id"],"action":"edit","value_source":"current_task.entity"},
        ],
    )
    graph.save()
    plan = CertifiedHIPFutureTaskPlanner(_cfg(), graph).plan('Search data map "MAP_B", expand it, then Edit')
    assert plan["execution_mode"] == "certified_task_replay"
    assert plan["task_replay_profile_id"].startswith("task-replay-")


def test_backend_exposes_certified_plan_and_replay_registry(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    mem = tmp_path / "memory"
    cfg_path.write_text(
        "reporting:\n  memory_dir: '%s'\n  runs_dir: '%s'\nbrain:\n  directory: portal_brain\naia:\n  enabled: false\n"
        % (str(mem).replace("\\", "/"), str(tmp_path / "runs").replace("\\", "/")),
        encoding="utf-8",
    )
    graph = HIPCapabilityGraph(mem / "portal_brain")
    _seed_family(graph, "data_maps")
    graph.observe_task_replay_profile(name="future_edit", families=["data_maps"], verified=True, steps=[{"type":"action","page_family":"data_maps","action":"edit"}])
    graph.save()
    client = TestClient(backend_app.app)
    resp = client.post("/api/certified-task/plan", json={"task":'Search data map "MAP_A", expand it, then Edit',"config":str(cfg_path),"runs_dir":str(tmp_path/"runs")})
    assert resp.status_code == 200
    assert resp.json()["schema_version"] == "hip.certified-future-task.v2"
    replays = client.get("/api/certified-task/replays", params={"config":str(cfg_path),"verified_only":True})
    assert replays.status_code == 200 and replays.json()["count"] == 1


def test_frontend_certified_task_is_backend_client_only():
    source = Path("frontend/app.py").read_text(encoding="utf-8")
    assert "/api/certified-task/plan" in source
    assert "/api/certified-task/run" in source
    assert "/api/certified-task/replays" in source
    assert "BrowserSession" not in source
    assert "from playwright" not in source.lower()


def test_cli_exposes_certified_task_commands():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "plan-certified-task" in result.stdout
    assert "run-certified-task" in result.stdout


def test_mutation_confirmation_phrase_is_unchanged():
    assert MUTATION_CONFIRMATION == "ALLOW HIP MUTATION"
