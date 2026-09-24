from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

import hip_id_agent
from hip_id_agent.config import load_config
from hip_id_agent.production_e2e import (
    HashChainedJournal,
    ProductionE2EOrchestrator,
    ProductionExecutionLease,
    build_request,
)

ROOT = Path(__file__).resolve().parents[1]


def test_v243_version_and_scripts():
    assert hip_id_agent.__version__ == "2.4.3"
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "2.4.3"' in text
    assert 'hip-agent = "hip_id_agent.cli:app"' in text
    assert 'hip-portal = "backend.__main__:main"' in text


def test_v243_all_shipped_configs_have_production_lifecycle():
    for name in ["config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"]:
        cfg = load_config(ROOT / name)
        assert cfg.production_e2e.enabled is True
        assert cfg.production_e2e.single_active_browser_session is True
        assert cfg.production_e2e.require_live_runtime_certificate_for_mutation is True
        assert cfg.production_e2e.capture_mutation_before_after_evidence is True
        assert cfg.production_e2e.create_safe_review_bundle is True
        assert cfg.production_e2e.require_execution_input_immutability is True
        assert cfg.production_e2e.require_governance_ledger_integrity_for_mutation is True
        assert cfg.production_e2e.safe_review_strict_allowlist is True


def test_v243_hash_chained_journal_detects_tampering(tmp_path: Path):
    path = tmp_path / "journal.jsonl"
    journal = HashChainedJournal(path)
    journal.append("one", {"a": 1})
    journal.append("two", {"b": 2})
    assert journal.verify()["pass"] is True
    rows = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0]); first["a"] = 9
    rows[0] = json.dumps(first)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert journal.verify()["pass"] is False


def test_v243_single_browser_lease_blocks_live_owner_and_releases(tmp_path: Path):
    path = tmp_path / "lock.json"
    a = ProductionExecutionLease(path, stale_seconds=3600)
    got = a.acquire(run_id="r1", task_hash="h1")
    assert got["pass"] is True
    b = ProductionExecutionLease(path, stale_seconds=3600)
    assert b.acquire(run_id="r2", task_hash="h2")["pass"] is False
    a.release()
    assert b.acquire(run_id="r2", task_hash="h2")["pass"] is True
    b.release()
    assert not path.exists()


def test_v243_dead_stale_lease_is_reclaimed(tmp_path: Path):
    path = tmp_path / "lock.json"
    path.write_text(json.dumps({"pid": 99999999, "run_id": "dead"}), encoding="utf-8")
    lease = ProductionExecutionLease(path, stale_seconds=60)
    result = lease.acquire(run_id="fresh", task_hash="h")
    assert result["pass"] is True
    lease.release()


def _orchestrator(tmp_path: Path) -> ProductionE2EOrchestrator:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text((ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    # copy the minimum paths used by config-relative runtime
    return ProductionE2EOrchestrator.from_path(cfg_path, root=tmp_path)


def test_v243_governance_gate_requires_role_three_key_and_optional_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    orch = _orchestrator(tmp_path)
    orch.config.governance.default_operator_role = "viewer"
    orch.config.production_e2e.require_approval_id_for_mutation = True
    input_path = tmp_path / "input.json"; input_path.write_text('{"objects":{"x":{"name":"A"}}}', encoding="utf-8")
    req = build_request(root=tmp_path, task="save object", config_path=tmp_path / "config.yaml", input_json=input_path)
    gate = orch._role_and_approval_gate(req, mutation_required=True)
    assert gate["pass"] is False
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    req.operator_role = "admin"; req.approval_id = "CHG-1"; req.allow_portal_mutation = True; req.confirmation = "ALLOW HIP MUTATION"
    gate = orch._role_and_approval_gate(req, mutation_required=True)
    assert gate["pass"] is True
    assert gate["approval_id_hash"] and "CHG-1" not in json.dumps(gate)


def test_v243_request_manifest_hashes_business_input_not_values(tmp_path: Path):
    orch = _orchestrator(tmp_path)
    input_path = tmp_path / "input.json"; input_path.write_text('{"objects":{"x":{"password":"TOPSECRET","name":"ABC"}}}', encoding="utf-8")
    req = build_request(root=tmp_path, task="fill form", config_path=tmp_path / "config.yaml", input_json=input_path)
    manifest = orch._request_manifest(req, run_id="r1", plan={"mutation_required": False, "steps": []})
    text = json.dumps(manifest)
    assert "TOPSECRET" not in text and '"ABC"' not in text
    assert manifest["input_json"]["sha256"]


def test_v243_readonly_orchestration_creates_final_summary_and_safe_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    orch = _orchestrator(tmp_path)
    orch.config.production_e2e.require_autogen_075 = False
    orch.config.reporting.runs_dir = str(tmp_path / "runs")
    orch.config.reporting.memory_dir = str(tmp_path / "memory")
    input_path = tmp_path / "input.json"; input_path.write_text('{"objects":{"x":{"name":"A"}}}', encoding="utf-8")
    req = build_request(root=tmp_path, task="view object", config_path=tmp_path / "config.yaml", input_json=input_path, runs_dir=tmp_path / "runs")

    class Planner:
        def __init__(self, *args, **kwargs): pass
        def plan(self, *args, **kwargs):
            return {"pass": True, "steps": [{"type":"navigate","risk":"read"}], "mutation_required": False, "mutation_actions": []}
    class Executor:
        def __init__(self, *args, **kwargs): pass
        async def execute(self, **kwargs):
            Path(kwargs["run_dir"]).joinpath("universal_portal_task_execution.json").write_text('{"pass":true}', encoding="utf-8")
            return {"pass": True, "status": "complete", "steps": [{"pass": True, "type": "navigate"}]}

    monkeypatch.setattr("hip_id_agent.production_e2e.UniversalPortalTaskPlanner", Planner)
    monkeypatch.setattr("hip_id_agent.production_e2e.UniversalPortalTaskExecutor", Executor)
    monkeypatch.setattr(orch, "doctor", lambda request, mutation_expected=False: {"pass": True, "decision": "GO", "blocker_count": 0, "warning_count": 0, "checks": []})
    result = asyncio.run(orch.execute(req))
    assert result["pass"] is True and result["status"] == "complete"
    run_dir = Path(result["run_dir"])
    assert (run_dir / "production_final_summary.json").is_file()
    assert (run_dir / "production_execution_journal.jsonl").is_file()
    assert Path(result["safe_review_bundle"]).is_file()
    assert HashChainedJournal(run_dir / "production_execution_journal.jsonl").verify()["pass"] is True


def test_v243_duplicate_mutation_is_quarantined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    orch = _orchestrator(tmp_path)
    orch.config.production_e2e.require_autogen_075 = False
    orch.config.production_e2e.require_live_runtime_certificate_for_mutation = False
    orch.config.reporting.runs_dir = str(tmp_path / "runs")
    orch.config.reporting.memory_dir = str(tmp_path / "memory")
    orch.config.governance.default_operator_role = "admin"
    input_path = tmp_path / "input.json"; input_path.write_text('{"objects":{"x":{"name":"A"}}}', encoding="utf-8")
    req = build_request(root=tmp_path, task="save object", config_path=tmp_path / "config.yaml", input_json=input_path, runs_dir=tmp_path / "runs", allow_portal_mutation=True, confirmation="ALLOW HIP MUTATION", operator_role="admin")
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")

    class Planner:
        def __init__(self, *args, **kwargs): pass
        def plan(self, *args, **kwargs):
            return {"pass": True, "steps": [{"type":"semantic_action","action":"save","label":"Save","risk":"mutation"}], "mutation_required": True, "mutation_actions": ["save", "Save"]}
    class Executor:
        def __init__(self, *args, **kwargs): pass
        async def execute(self, **kwargs): return {"pass": True, "status": "complete", "steps": [{"pass": True, "risk": "mutation"}]}

    monkeypatch.setattr("hip_id_agent.production_e2e.UniversalPortalTaskPlanner", Planner)
    monkeypatch.setattr("hip_id_agent.production_e2e.UniversalPortalTaskExecutor", Executor)
    monkeypatch.setattr(orch, "doctor", lambda request, mutation_expected=False: {"pass": True, "decision": "GO", "blocker_count": 0, "warning_count": 0, "checks": []})
    first = asyncio.run(orch.execute(req)); assert first["pass"] is True
    second = asyncio.run(orch.execute(req)); assert second["pass"] is False
    assert second["status"] == "blocked_duplicate_mutation"


def test_v243_safe_review_bundle_excludes_screenshots(tmp_path: Path):
    orch = _orchestrator(tmp_path)
    run = tmp_path / "run"; run.mkdir()
    (run / "production_final_summary.json").write_text('{"pass":true}', encoding="utf-8")
    (run / "notes.md").write_text("ok", encoding="utf-8")
    (run / "screen.png").write_bytes(b"png")
    bundle = Path(orch._build_safe_review_bundle(run))
    import zipfile
    with zipfile.ZipFile(bundle) as zf:
        names = set(zf.namelist())
    assert "production_final_summary.json" in names
    assert "notes.md" not in names
    assert "screen.png" not in names
    assert "production_safe_review_manifest.json" in names


def test_v243_backend_serves_webui_and_production_routes():
    from backend.app import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/production/doctor" in paths
    assert "/api/production/start" in paths
    assert "/api/production/journal/verify" in paths
    assert any(getattr(r, "name", "") == "webui" for r in app.routes)


def test_v243_runtime_status_exposes_production_lifecycle():
    from backend.app import runtime_status
    status = runtime_status("config.yaml")
    prod = status["production_e2e"]
    assert prod["enabled"] is True
    assert prod["single_active_browser_session"] is True
    assert prod["same_process_control_center"] is True
    assert prod["one_command_entrypoint"] == "hip-agent run-production-e2e"


def test_v243_packaged_webui_fallback_is_kept_in_sync():
    for name in ["index.html", "app.js", "platform.js", "styles.css"]:
        assert (ROOT / "backend" / "webui" / name).read_bytes() == (ROOT / "webui" / name).read_bytes()
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.setuptools.package-data]' in text
    assert 'backend = ["webui/*.html", "webui/*.js", "webui/*.css"]' in text


def test_v243_fastapi_serves_control_center_from_same_process():
    from fastapi.testclient import TestClient
    from backend.app import app
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    home = client.get("/")
    assert home.status_code == 200
    assert "HIP" in home.text and "Control" in home.text


def test_v243_runtime_status_is_setup_safe_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import backend.app as backend_app
    monkeypatch.setattr(backend_app, "ROOT", tmp_path)
    status = backend_app.runtime_status("config.yaml")
    assert status["status"] == "setup_required"
    assert status["setup_required"] is True
    assert status["config"]["found"] is False
    assert status["production_e2e"]["same_process_control_center"] is True
    assert status["control_center"]["served_by_backend"] is True
