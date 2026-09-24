from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import backend.app as backend_app
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.change_governance import ChangeAuditLedger, HIPChangeGovernance
from hip_id_agent.certified_future_task_agent import CertifiedHIPFutureTaskPlanner
from hip_id_agent.cli import app
from hip_id_agent.config import AppConfig


def _cfg(tmp_path: Path) -> AppConfig:
    cfg = AppConfig()
    cfg.aia.enabled = False
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.reporting.runs_dir = str(tmp_path / "runs")
    cfg.brain.directory = "portal_brain"
    return cfg


def _seed_mutation_graph(tmp_path: Path):
    cfg = _cfg(tmp_path)
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / cfg.brain.directory)
    graph.observe_page(page_family="data_maps", url="https://developer.dell.com/data_maps")
    search = graph.observe_capability(page_family="data_maps", kind="search", label="Search", selector="#search")
    expand = graph.observe_capability(page_family="data_maps", kind="expand", label="Expand", selector="#expand")
    deploy = graph.observe_capability(page_family="data_maps", kind="row_action", label="Deploy", selector="#deploy")
    graph.observe_capability(page_family="data_maps", kind="row_action", label="Edit", selector="#edit")
    graph.observe_capability(page_family="data_maps", kind="row_action", label="Clone", selector="#clone")
    graph.observe_capability(page_family="data_maps", kind="row_action", label="Migrate", selector="#migrate")
    graph.observe_capability(page_family="data_maps", kind="row_action", label="Delete", selector="#delete")
    graph.observe_api_contract(page_family="data_maps", method="POST", url="https://host/api/datamaps/deploy", response_status=200, caused_by_capability_id=deploy["capability_id"])
    graph.observe_replay_profile(page_family="data_maps", name="entity_deploy", verified=True, steps=[
        {"type":"search","capability_id":search["capability_id"],"value_source":"current_task.entity"},
        {"type":"expand","capability_id":expand["capability_id"],"value_source":"current_task.entity"},
        {"type":"action","action":"deploy","capability_id":deploy["capability_id"],"value_source":"current_task.entity"},
    ])
    graph.save()
    return cfg, graph


def _mutation_plan(cfg: AppConfig, graph: HIPCapabilityGraph):
    return CertifiedHIPFutureTaskPlanner(cfg, graph).plan('Search data map "MAP_A", expand it, then Deploy')


def test_governance_defaults_fail_closed_for_mutation_role(tmp_path):
    cfg, graph = _seed_mutation_graph(tmp_path)
    preview = HIPChangeGovernance(cfg, graph).preview(task='Search data map "MAP_A", expand it, then Deploy', plan=_mutation_plan(cfg, graph))
    assert preview["mutation_required"] is True
    assert preview["operator_role"] == "viewer"
    assert preview["pass"] is False
    assert any("not authorized" in x for x in preview["blocking_reasons"])


def test_technical_role_can_pass_mutation_governance_preview(tmp_path):
    cfg, graph = _seed_mutation_graph(tmp_path)
    preview = HIPChangeGovernance(cfg, graph).preview(task='Search data map "MAP_A", expand it, then Deploy', plan=_mutation_plan(cfg, graph), operator_role="technical")
    assert preview["pass"] is True
    assert preview["role_gate"]["pass"] is True
    assert preview["risk"] == "mutation"
    assert preview["required_mutation_confirmation"] == "ALLOW HIP MUTATION"
    assert preview["operations"][-1]["observed_api_contracts"]


def test_change_approval_can_be_required_by_policy(tmp_path):
    cfg, graph = _seed_mutation_graph(tmp_path)
    cfg.governance.require_approval_id_for_mutation = True
    gov = HIPChangeGovernance(cfg, graph)
    blocked = gov.preview(task='Search data map "MAP_A", expand it, then Deploy', plan=_mutation_plan(cfg, graph), operator_role="admin")
    passed = gov.preview(task='Search data map "MAP_A", expand it, then Deploy', plan=_mutation_plan(cfg, graph), operator_role="admin", approval_id="CHG-123")
    assert blocked["pass"] is False
    assert passed["pass"] is True
    assert passed["approval"]["present"] is True
    assert "CHG-123" not in json.dumps(passed)


def test_duplicate_successful_mutation_is_blocked_unless_forced(tmp_path):
    cfg, graph = _seed_mutation_graph(tmp_path)
    gov = HIPChangeGovernance(cfg, graph)
    first = gov.preview(task='Search data map "MAP_A", expand it, then Deploy', plan=_mutation_plan(cfg, graph), operator_role="technical")
    gov.ledger.append("change_committed", {
        "idempotency_key": first["idempotency"]["idempotency_key"], "change_id": first["change_id"], "verified": True,
    })
    blocked = gov.preview(task='Search data map "MAP_A", expand it, then Deploy', plan=_mutation_plan(cfg, graph), operator_role="technical")
    forced = gov.preview(task='Search data map "MAP_A", expand it, then Deploy', plan=_mutation_plan(cfg, graph), operator_role="technical", force_repeat_mutation=True)
    assert blocked["pass"] is False and blocked["idempotency"]["prior_success_found"] is True
    assert forced["pass"] is True and forced["idempotency"]["force_repeat_mutation"] is True


def test_audit_ledger_is_hash_chained(tmp_path):
    ledger = ChangeAuditLedger(tmp_path / "audit.jsonl")
    one = ledger.append("change_planned", {"change_id":"chg-a", "task_hash":"abc"})
    two = ledger.append("change_committed", {"change_id":"chg-a", "task_hash":"abc", "verified":True})
    assert one["previous_hash"] == "GENESIS"
    assert two["previous_hash"] == one["event_hash"]
    assert two["event_hash"] != one["event_hash"]
    status = ledger.status()
    assert status["event_count"] == 2
    assert status["chain_valid"] is True


def test_post_change_verdict_requires_2xx_mutation_response(tmp_path):
    cfg, graph = _seed_mutation_graph(tmp_path)
    gov = HIPChangeGovernance(cfg, graph)
    preview = gov.preview(task='Search data map "MAP_A", expand it, then Deploy', plan=_mutation_plan(cfg, graph), operator_role="technical")
    base = {"pass": True, "subtasks":[{"mcp_assurance":{"pass":True}}]}
    bad = {**base, "steps":[{"risk":"mutation","network_transactions":[{"method":"POST","status":500}]}]}
    good = {**base, "steps":[{"risk":"mutation","network_transactions":[{"method":"POST","status":204}]}]}
    assert gov.post_change_verdict(preview=preview, execution=bad)["pass"] is False
    assert gov.post_change_verdict(preview=preview, execution=bad)["manual_review_required"] is True
    assert gov.post_change_verdict(preview=preview, execution=good)["pass"] is True


def test_post_change_verdict_requires_fresh_mcp_when_enabled(tmp_path):
    cfg, graph = _seed_mutation_graph(tmp_path)
    gov = HIPChangeGovernance(cfg, graph)
    preview = gov.preview(task='Search data map "MAP_A", expand it, then Deploy', plan=_mutation_plan(cfg, graph), operator_role="technical")
    execution = {"pass":True,"steps":[{"risk":"mutation","network_transactions":[{"method":"POST","status":200}]}],"subtasks":[{"mcp_assurance":{"pass":False}}]}
    verdict = gov.post_change_verdict(preview=preview, execution=execution)
    assert verdict["pass"] is False
    assert verdict["fresh_mcp_assurance_gate"] is False


def test_preview_is_value_free_in_structural_persistent_fields(tmp_path):
    cfg, graph = _seed_mutation_graph(tmp_path)
    preview = HIPChangeGovernance(cfg, graph).preview(task='Search data map "MAP_CUSTOMER_SECRET", expand it, then Deploy', plan=CertifiedHIPFutureTaskPlanner(cfg, graph).plan('Search data map "MAP_CUSTOMER_SECRET", expand it, then Deploy'), operator_role="technical")
    assert preview["values_stored"] is False
    assert "MAP_CUSTOMER_SECRET" not in json.dumps(preview)


def test_backend_exposes_governance_preview_and_audit(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    mem = tmp_path / "memory"
    cfg_path.write_text(
        "reporting:\n  memory_dir: '%s'\n  runs_dir: '%s'\nbrain:\n  directory: portal_brain\naia:\n  enabled: false\n"
        % (str(mem).replace("\\", "/"), str(tmp_path / "runs").replace("\\", "/")), encoding="utf-8")
    graph = HIPCapabilityGraph(mem / "portal_brain")
    graph.observe_page(page_family="data_maps", url="https://developer.dell.com/data_maps")
    s=graph.observe_capability(page_family="data_maps", kind="search", label="Search", selector="#s")
    e=graph.observe_capability(page_family="data_maps", kind="expand", label="Expand", selector="#e")
    d=graph.observe_capability(page_family="data_maps", kind="row_action", label="Deploy", selector="#d")
    for label in ["Edit","Clone","Migrate","Delete"]: graph.observe_capability(page_family="data_maps", kind="row_action", label=label, selector="#"+label.lower())
    graph.observe_api_contract(page_family="data_maps", method="POST", url="https://host/deploy", response_status=200, caused_by_capability_id=d["capability_id"])
    graph.observe_replay_profile(page_family="data_maps", name="entity_deploy", verified=True, steps=[{"type":"search","capability_id":s["capability_id"]},{"type":"expand","capability_id":e["capability_id"]},{"type":"action","action":"deploy","capability_id":d["capability_id"]}])
    graph.save()
    client = TestClient(backend_app.app)
    resp = client.post("/api/governed-change/preview", json={"task":'Search data map "MAP_A", expand it, then Deploy',"config":str(cfg_path),"operator_role":"technical"})
    assert resp.status_code == 200
    assert resp.json()["change_preview"]["schema_version"] == "hip.change-governance-preview.v1"
    audit = client.get("/api/governed-change/audit", params={"config":str(cfg_path)})
    assert audit.status_code == 200
    assert audit.json()["schema_version"] == "hip.change-audit-ledger-status.v1"


def test_frontend_governance_is_backend_client_only():
    source = Path("frontend/app.py").read_text(encoding="utf-8")
    assert "/api/governed-change/preview" in source
    assert "/api/governed-change/run" in source
    assert "/api/governed-change/audit" in source
    assert "BrowserSession" not in source
    assert "from playwright" not in source.lower()


def test_cli_exposes_governance_commands():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "preview-governed-change" in result.stdout
    assert "run-governed-change" in result.stdout
    assert "change-audit-status" in result.stdout


def test_governance_config_has_production_defaults():
    cfg = AppConfig()
    assert cfg.governance.enabled is True
    assert cfg.governance.default_operator_role == "viewer"
    assert "technical" in cfg.governance.mutation_roles
    assert cfg.governance.require_post_change_api_success is True
    assert cfg.governance.require_post_change_mcp_assurance is True
