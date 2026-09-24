from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as backend_app
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from hip_id_agent.datamap_deep_discovery import (
    MUTATION_PROBE_ACTIONS,
    _form_contract,
    _listing_filter_candidates,
    _pagination_candidates,
    _row_actions,
    _surface_delta,
)
from hip_id_agent.future_task_agent import HIPFutureTaskPlanner


def _surface():
    return {
        "url": "https://host/data-map",
        "title": "Data Maps",
        "controls": [
            {"tag":"input","role":"searchbox","type":"search","label":"Search","placeholder":"Search Data Maps","formControlName":"","selector":"#search","disabled":False,"required":False,"readOnly":False},
            {"tag":"input","role":"combobox","type":"text","label":"Status Filter","placeholder":"Status","formControlName":"","selector":"#status","disabled":False,"required":False,"readOnly":False},
        ],
        "actions": [
            {"text":"Next","ariaLabel":"Next page","title":"","selector":"#next","role":"button","disabled":False,"expanded":"","scope":""},
            {"text":"Previous","ariaLabel":"Previous page","title":"","selector":"#prev","role":"button","disabled":False,"expanded":"","scope":""},
            {"text":"Edit","selector":"#edit","role":"button","disabled":False,"scope":"MAP_ABC"},
            {"text":"Clone","selector":"#clone","role":"button","disabled":False,"scope":"MAP_ABC"},
            {"text":"Migrate","selector":"#migrate","role":"button","disabled":False,"scope":"MAP_ABC"},
            {"text":"Deploy","selector":"#deploy","role":"button","disabled":False,"scope":"MAP_ABC"},
            {"text":"Delete","selector":"#delete","role":"button","disabled":False,"scope":"MAP_ABC"},
        ],
        "dialogs": [],
        "rows": [{"text":"MAP_ABC"}],
    }


def test_datamap_deep_detects_pagination_filters_and_all_row_actions():
    surface = _surface()
    assert [x["selector"] for x in _pagination_candidates(surface)] == ["#next", "#prev"]
    assert _listing_filter_candidates(surface)[0]["selector"] == "#status"
    labels = [x["label"] for x in _row_actions(surface, "MAP_ABC")]
    assert {"Edit", "Clone", "Migrate", "Deploy", "Delete"}.issubset(set(labels))
    assert set(MUTATION_PROBE_ACTIONS) == {"migrate", "deploy", "delete"}


def test_datamap_form_contract_is_structural_and_tracks_required_fields():
    surface = _surface()
    surface["controls"].append({"tag":"input","role":"textbox","type":"text","label":"Map Name","formControlName":"mapName","selector":"[formcontrolname=mapName]","disabled":False,"required":True,"readOnly":False,"owns":"","controls":""})
    surface["dialogs"] = [{"tag":"div","role":"dialog","classes":"drawer","controlCount":1}]
    contract = _form_contract(surface)
    assert contract["required_control_count"] == 1
    assert contract["controls"][-1]["form_control_name"] == "mapName"
    assert contract["values_stored"] is False
    assert contract["structural_fingerprint"]


def test_surface_delta_detects_revealed_actions_without_values():
    before = _surface()
    after = _surface()
    after["actions"] = list(after["actions"]) + [{"text":"Confirm","selector":"#confirm","role":"button","disabled":False,"scope":""}]
    delta = _surface_delta(before, after)
    assert delta["changed"] is True
    assert any("Confirm" in row for row in delta["actions_added"])


def test_capability_graph_persists_verified_replay_profiles_value_free(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    search = graph.observe_capability(page_family="data_maps", kind="search", label="Search", selector="#search")
    expand = graph.observe_capability(page_family="data_maps", kind="expand", label="Expand", selector="#expand")
    edit = graph.observe_capability(page_family="data_maps", kind="row_action", label="Edit", selector="#edit")
    profile = graph.observe_replay_profile(
        page_family="data_maps", name="entity_edit", verified=True,
        preconditions=["authenticated_shared_browser", "entity_exists"],
        steps=[
            {"type":"navigate","action":"open_data_maps"},
            {"type":"search","capability_id":search["capability_id"],"value_source":"current_task.entity"},
            {"type":"expand","capability_id":expand["capability_id"],"scope":"matching_entity_row"},
            {"type":"action","action":"edit","capability_id":edit["capability_id"],"scope":"matching_entity_row"},
        ],
    )
    graph.save()
    assert profile["verified"] is True
    text = graph.path.read_text(encoding="utf-8")
    assert "current_task.entity" in text
    assert "MAP_ABC" not in text
    assert HIPCapabilityGraph(tmp_path).manifest()["replay_profile_count"] == 1


def test_future_task_planner_uses_verified_datamap_replay_profile(tmp_path):
    graph = HIPCapabilityGraph(tmp_path)
    graph.observe_page(page_family="data_maps", url="https://developer.dell.com/hybrid-integrations/bizlink/data-map")
    search = graph.observe_capability(page_family="data_maps", kind="search", label="Search", selector="#search")
    expand = graph.observe_capability(page_family="data_maps", kind="expand", label="Expand", selector="#expand")
    edit = graph.observe_capability(page_family="data_maps", kind="row_action", label="Edit", selector="#edit")
    graph.observe_replay_profile(page_family="data_maps", name="entity_edit", verified=True, steps=[
        {"type":"search","capability_id":search["capability_id"],"value_source":"current_task.entity"},
        {"type":"expand","capability_id":expand["capability_id"]},
        {"type":"action","capability_id":edit["capability_id"],"action":"edit"},
    ])
    cfg = AppConfig(); cfg.aia.enabled = False
    plan = HIPFutureTaskPlanner(cfg, graph).plan('Search data map "MAP_ABC", expand it, then Edit')
    assert plan["pass"] is True
    assert plan["execution_mode"] == "deterministic_replay"
    assert plan["replay_profile_id"].startswith("replay-")


def test_backend_exposes_datamap_deep_and_replay_routes(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    mem = tmp_path / "memory"; runs = tmp_path / "runs"
    cfg_path.write_text("reporting:\n  memory_dir: '%s'\n  runs_dir: '%s'\nbrain:\n  directory: portal_brain\n" % (str(mem).replace('\\','/'), str(runs).replace('\\','/')), encoding="utf-8")
    graph = HIPCapabilityGraph(mem / "portal_brain")
    graph.observe_replay_profile(page_family="data_maps", name="entity_edit", verified=True, steps=[])
    graph.save()
    client = TestClient(backend_app.app)
    replay = client.get("/api/replay-profiles", params={"config": str(cfg_path), "page_family":"data_maps"})
    assert replay.status_code == 200 and replay.json()["count"] == 1
    captured = {}
    monkeypatch.setattr(backend_app, "_start_cli", lambda command, runs_dir="": captured.update({"command":command}) or {"running":True,"command":command})
    resp = client.post("/api/datamaps/deep/start", json={"config":str(cfg_path),"input_json":"x.json","runs_dir":str(runs),"require_mcp":True})
    assert resp.status_code == 200
    assert "learn-datamaps-deep" in captured["command"]


def test_mutation_probe_has_hard_network_abort_and_frontend_button():
    source = Path("hip_id_agent/datamap_deep_discovery.py").read_text(encoding="utf-8")
    assert 'await page.route("**/*", handler)' in source
    assert 'await route.abort("blockedbyclient")' in source
    assert "MUTATING_METHODS" in source
    assert "dangerous_get" in source
    frontend = Path("frontend/app.py").read_text(encoding="utf-8")
    assert "Deep Learn Data Maps" in frontend
    assert "/api/datamaps/deep/start" in frontend
    assert "Deterministic replay profiles" in frontend
