from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as backend_app
from hip_id_agent.capability_graph import HIPCapabilityGraph


def test_backend_health_and_runtime_status(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("reporting:\n  memory_dir: '%s'\n  runs_dir: '%s'\nbrain:\n  directory: portal_brain\n" % (str(tmp_path / "memory").replace('\\','/'), str(tmp_path / "runs").replace('\\','/')), encoding="utf-8")
    client = TestClient(backend_app.app)
    assert client.get("/health").status_code == 200
    resp = client.get("/api/runtime/status", params={"config": str(cfg_path)})
    assert resp.status_code == 200
    data = resp.json()
    assert "autogen" in data and "capability_graph" in data


def test_backend_capability_and_api_endpoints(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    mem = tmp_path / "memory"
    cfg_path.write_text("reporting:\n  memory_dir: '%s'\n  runs_dir: '%s'\nbrain:\n  directory: portal_brain\n" % (str(mem).replace('\\','/'), str(tmp_path / "runs").replace('\\','/')), encoding="utf-8")
    graph = HIPCapabilityGraph(mem / "portal_brain")
    cap = graph.observe_capability(page_family="data_maps", kind="row_action", label="Edit", selector="#edit")
    graph.observe_api_contract(page_family="data_maps", method="GET", url="https://host/api/maps", response_status=200, caused_by_capability_id=cap["capability_id"])
    graph.save()
    client = TestClient(backend_app.app)
    caps = client.get("/api/capabilities", params={"config": str(cfg_path), "page_family":"data_maps"})
    assert caps.status_code == 200 and caps.json()["count"] >= 1
    assert any(row.get("label") == "Edit" for row in caps.json()["capabilities"])
    apis = client.get("/api/apis", params={"config": str(cfg_path), "page_family":"data_maps"})
    assert apis.status_code == 200 and apis.json()["count"] >= 1
    assert any(row.get("endpoint") == "https://host/api/maps" for row in apis.json()["api_contracts"])
    detail = client.get(f"/api/capabilities/{cap['capability_id']}", params={"config": str(cfg_path)})
    assert detail.status_code == 200 and len(detail.json()["api_contracts"]) == 1
