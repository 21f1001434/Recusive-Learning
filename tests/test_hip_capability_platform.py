from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.capability_graph import HIPCapabilityGraph, classify_risk
from hip_id_agent.config import AppConfig
from hip_id_agent.future_task_agent import HIPFutureTaskPlanner, HIPFutureTaskExecutor, MUTATION_CONFIRMATION
from hip_id_agent.portal_discovery_flow import _search_candidate, _expand_candidate, _add_candidate, _example_queries


def seeded_graph(tmp_path: Path) -> HIPCapabilityGraph:
    graph = HIPCapabilityGraph(tmp_path)
    graph.observe_page(page_family="data_maps", url="https://developer.dell.com/hybrid-integrations/bizlink/data-map", title="Data Maps", run_id="r1")
    graph.observe_capability(page_family="data_maps", kind="search", label="Search", selector='input[placeholder="Search"]', role="searchbox", placeholder="Search")
    graph.observe_capability(page_family="data_maps", kind="expand", label="Expand", selector='button[aria-expanded="false"]')
    graph.observe_capability(page_family="data_maps", kind="row_action", label="Edit", selector='button[aria-label="Edit"]')
    graph.observe_capability(page_family="data_maps", kind="row_action", label="Deploy", selector='button[aria-label="Deploy"]')
    graph.save()
    return graph


def test_capability_graph_persists_semantic_actions_and_api_contracts(tmp_path):
    graph = seeded_graph(tmp_path)
    edit = graph.query_capabilities(page_family="data_maps", text="edit")[0]
    graph.observe_api_contract(page_family="data_maps", method="GET", url="https://host/api/maps/123?x=1", response_status=200, caused_by_capability_id=edit["capability_id"])
    graph.save()
    reload = HIPCapabilityGraph(tmp_path)
    assert reload.manifest()["capability_count"] == 4
    assert reload.manifest()["api_contract_count"] == 1
    assert reload.query_capabilities(page_family="data_maps", text="deploy")[0]["risk"] == "mutation"
    assert reload.data["values_stored"] is False


def test_risk_classification_matches_portal_action_intent():
    assert classify_risk("Deploy") == "mutation"
    assert classify_risk("Delete") == "mutation"
    assert classify_risk("Edit") == "draft"
    assert classify_risk("Clone") == "draft"
    assert classify_risk("View Details") == "read"
    assert classify_risk("anything", method="POST") == "mutation"


def test_future_task_plan_uses_search_expand_then_edit(tmp_path):
    graph = seeded_graph(tmp_path)
    cfg = AppConfig(); cfg.aia.enabled = False
    plan = HIPFutureTaskPlanner(cfg, graph).plan('Search data map "MAP_ABC", expand it, then Edit')
    assert plan["pass"] is True
    assert [s["type"] for s in plan["steps"]] == ["navigate", "search", "expand", "action"]
    assert plan["entity"] == "MAP_ABC"
    assert plan["steps"][-1]["risk"] == "draft"


def test_future_task_plan_marks_deploy_mutation(tmp_path):
    graph = seeded_graph(tmp_path)
    cfg = AppConfig(); cfg.aia.enabled = False
    plan = HIPFutureTaskPlanner(cfg, graph).plan('Search data map "MAP_ABC", expand it, then Deploy')
    assert plan["pass"] is True
    assert plan["steps"][-1]["risk"] == "mutation"


def test_mutation_gate_requires_all_three_factors(tmp_path, monkeypatch):
    graph = seeded_graph(tmp_path)
    cfg = AppConfig(); cfg.aia.enabled = False
    plan = HIPFutureTaskPlanner(cfg, graph).plan('Search data map "MAP_ABC", expand it, then Deploy')
    executor = HIPFutureTaskExecutor(cfg, graph)
    monkeypatch.delenv("HIP_ALLOW_PORTAL_MUTATION", raising=False)
    assert executor._mutation_gate(plan, allow_portal_mutation=True, confirmation=MUTATION_CONFIRMATION)["pass"] is False
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    assert executor._mutation_gate(plan, allow_portal_mutation=False, confirmation=MUTATION_CONFIRMATION)["pass"] is False
    assert executor._mutation_gate(plan, allow_portal_mutation=True, confirmation="wrong")["pass"] is False
    assert executor._mutation_gate(plan, allow_portal_mutation=True, confirmation=MUTATION_CONFIRMATION)["pass"] is True


def test_discovery_surface_candidate_detection():
    surface = {
        "controls": [
            {"tag":"input","role":"","type":"text","label":"Name","placeholder":"","selector":"#name","disabled":False},
            {"tag":"input","role":"searchbox","type":"search","label":"Search","placeholder":"Search Data Maps","selector":"#search","disabled":False},
        ],
        "actions": [
            {"text":"","ariaLabel":"","title":"","expanded":"false","scope":"MAP_ABC","selector":"#expand","disabled":False,"role":"button"},
            {"text":"+ Add","ariaLabel":"","title":"","expanded":"","scope":"","selector":"#add","disabled":False,"role":"button"},
        ],
        "rows": [{"text":"MAP_ABC"}],
    }
    assert _search_candidate(surface)["selector"] == "#search"
    assert _expand_candidate(surface, "MAP_ABC")["selector"] == "#expand"
    assert _add_candidate(surface)["selector"] == "#add"


def test_discovery_examples_cover_five_portal_families():
    payload = json.loads(Path("examples/uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    queries = _example_queries(payload)
    assert set(queries) == {"data_maps","document_types","rules","transport_profiles","bizflows"}
    assert queries["data_maps"]
    assert len(queries["document_types"]) == 2
    assert len(queries["transport_profiles"]) == 2


def test_capability_memory_stores_no_customer_values(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    graph.observe_capability(page_family="data_maps", kind="search", label="Search", selector="#search", evidence={"url":"https://host/maps"})
    graph.save()
    text = graph.path.read_text(encoding="utf-8")
    assert "U-HAUL" not in text
    assert '"values_stored": false' in text


def test_validated_phase_trajectory_promotes_form_dependency_and_api(tmp_path):
    from hip_id_agent.capability_graph import promote_validated_phase_to_capability_graph
    phase_dir = tmp_path / "rule"
    phase_dir.mkdir()
    (phase_dir / "form_api_transactions.json").write_text(json.dumps({"transactions":[{"method":"GET","url":"https://host/api/rules/1","status":200}]}), encoding="utf-8")
    trajectory = {
        "trust":"validated", "page_fingerprint":"fp", "url_template":"https://host/rules", "trajectory_fingerprint":"traj",
        "ordered_steps":[
            {"node_id":"parent","field_key":"action_type","section":"Actions","selector_profile":{"label":"Type","role":"combobox","framework_key":"actionType","candidates":[{"strategy":"framework_key","selector":"[formcontrolname=\"actionType\"]"}]},"parent_node_ids":[]},
            {"node_id":"child","field_key":"mapping_identifier","section":"Actions","selector_profile":{"label":"Mapping Identifier Name (Version)","role":"combobox","framework_key":"target","candidates":[{"strategy":"framework_key","selector":"[formcontrolname=\"target\"]"}]},"parent_node_ids":["parent"]},
        ]
    }
    graph = HIPCapabilityGraph(tmp_path / "memory")
    result = promote_validated_phase_to_capability_graph(graph=graph, phase="rule", phase_dir=phase_dir, trajectory=trajectory, run_id="r2")
    assert result["form_capability_count"] == 2
    assert result["api_observation_count"] == 1
    assert any(r["relation"] == "parent_before_child" for r in graph.data["relations"])


def test_frontend_is_backend_client_not_browser_engine():
    source = Path("frontend/app.py").read_text(encoding="utf-8")
    assert "requests" in source
    assert "BrowserSession" not in source
    assert "from playwright" not in source.lower()
    assert "import playwright" not in source.lower()
    assert "/api/discovery/start" in source
    assert "/api/certified-task/plan" in source


def test_discovery_persistent_scope_is_structural_not_entity_text(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    cap = graph.observe_capability(page_family="data_maps", kind="row_action", label="Edit", scope="entity_row")
    graph.save()
    text = graph.path.read_text(encoding="utf-8")
    assert "entity_row" in text
    assert "DELLCoXMLASNXX08C_U-HAUL" not in text
