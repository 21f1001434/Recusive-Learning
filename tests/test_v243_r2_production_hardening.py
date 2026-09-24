from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from hip_id_agent.change_governance import ChangeAuditLedger
from hip_id_agent.production_e2e import ProductionE2EOrchestrator, ProductionExecutionLease, build_request

ROOT = Path(__file__).resolve().parents[1]


def _orch(tmp_path: Path) -> ProductionE2EOrchestrator:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text((ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    orch = ProductionE2EOrchestrator.from_path(cfg_path, root=tmp_path)
    orch.config.production_e2e.require_autogen_075 = False
    orch.config.production_e2e.require_live_runtime_certificate_for_mutation = False
    orch.config.reporting.runs_dir = str(tmp_path / "runs")
    orch.config.reporting.memory_dir = str(tmp_path / "memory")
    return orch


def test_r2_lease_heartbeat_refreshes_active_lock(tmp_path: Path):
    path = tmp_path / "lease.json"
    lease = ProductionExecutionLease(path, stale_seconds=60)
    assert lease.acquire(run_id="r", task_hash="h")["pass"] is True
    before = path.stat().st_mtime_ns
    time.sleep(0.01)
    beat = lease.heartbeat()
    after = path.stat().st_mtime_ns
    assert beat["pass"] is True and after > before
    lease.release()


def test_r2_integrity_detects_input_drift(tmp_path: Path):
    orch = _orch(tmp_path)
    input_path = tmp_path / "input.json"
    input_path.write_text('{"objects":{"x":{"name":"A"}}}', encoding="utf-8")
    req = build_request(root=tmp_path, task="fill form", config_path=tmp_path / "config.yaml", input_json=input_path)
    plan = {"pass": True, "steps": [], "mutation_required": False, "mutation_actions": []}
    manifest = orch._request_manifest(req, run_id="r", plan=plan)
    assert orch._execution_integrity(req, manifest=manifest, plan=plan)["pass"] is True
    input_path.write_text('{"objects":{"x":{"name":"B"}}}', encoding="utf-8")
    receipt = orch._execution_integrity(req, manifest=manifest, plan=plan)
    assert receipt["pass"] is False
    assert receipt["checks"]["input_json_unchanged"] is False


def test_r2_integrity_detects_golden_reference_drift(tmp_path: Path):
    orch = _orch(tmp_path)
    input_path = tmp_path / "input.json"; input_path.write_text('{}', encoding='utf-8')
    golden = tmp_path / "golden"; golden.mkdir(); (golden / "a.png").write_bytes(b"one")
    req = build_request(root=tmp_path, task="view", config_path=tmp_path / "config.yaml", input_json=input_path, golden_dir=golden)
    plan = {"pass": True, "steps": [], "mutation_required": False, "mutation_actions": []}
    manifest = orch._request_manifest(req, run_id="r", plan=plan)
    (golden / "a.png").write_bytes(b"two")
    receipt = orch._execution_integrity(req, manifest=manifest, plan=plan)
    assert receipt["checks"]["golden_references_unchanged"] is False


def test_r2_mutation_doctor_blocks_tampered_governance_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    orch = _orch(tmp_path)
    input_path = tmp_path / "input.json"; input_path.write_text('{"x":1}', encoding='utf-8')
    ledger = orch._governance_ledger()
    ledger.append("change_committed", {"idempotency_key": "x", "run_id": "r"})
    rows = ledger.path.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0]); row["run_id"] = "tampered"; rows[0] = json.dumps(row)
    ledger.path.write_text("\n".join(rows)+"\n", encoding="utf-8")
    req = build_request(root=tmp_path, task="save object", config_path=tmp_path / "config.yaml", input_json=input_path)
    monkeypatch.setattr("hip_id_agent.production_e2e.autogen_runtime_status", lambda verify_imports=True: {"pass": True, "detail": "ok"})
    monkeypatch.setattr("hip_id_agent.production_e2e.mlflow_runtime_probe", lambda cfg: {"status":"ok","package_available":True})
    monkeypatch.setattr("hip_id_agent.production_e2e.verify_latest_live_runtime_certificate", lambda cfg, runs_root: {"pass":True})
    doctor = orch.doctor(req, mutation_expected=True)
    check = next(x for x in doctor["checks"] if x["id"] == "governance_ledger_integrity")
    assert check["pass"] is False
    assert doctor["decision"] == "NO-GO"


def test_r2_single_session_switch_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    orch = _orch(tmp_path)
    orch.config.production_e2e.single_active_browser_session = False
    input_path = tmp_path / "input.json"; input_path.write_text('{"x":1}', encoding='utf-8')
    req = build_request(root=tmp_path, task="view object", config_path=tmp_path / "config.yaml", input_json=input_path, runs_dir=tmp_path / "runs")

    class Planner:
        def __init__(self, *a, **k): pass
        def plan(self, *a, **k): return {"pass": True, "steps": [{"type":"navigate","risk":"read"}], "mutation_required": False, "mutation_actions": []}
    class Executor:
        def __init__(self, *a, **k): pass
        async def execute(self, **k): return {"pass": True, "status": "complete", "steps": []}
    monkeypatch.setattr("hip_id_agent.production_e2e.UniversalPortalTaskPlanner", Planner)
    monkeypatch.setattr("hip_id_agent.production_e2e.UniversalPortalTaskExecutor", Executor)
    monkeypatch.setattr(orch, "doctor", lambda request, mutation_expected=False: {"pass": True, "decision": "GO", "blocker_count": 0, "warning_count": 0, "checks": []})
    result = asyncio.run(orch.execute(req))
    assert result["pass"] is True
    lease = json.loads((Path(result["run_dir"]) / "production_execution_lease.json").read_text(encoding="utf-8"))
    assert lease["status"] == "disabled_by_configuration"


def test_r2_exception_writes_sanitized_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    orch = _orch(tmp_path)
    input_path = tmp_path / "input.json"; input_path.write_text('{"x":1}', encoding='utf-8')
    req = build_request(root=tmp_path, task="view object", config_path=tmp_path / "config.yaml", input_json=input_path, runs_dir=tmp_path / "runs")
    class Planner:
        def __init__(self, *a, **k): pass
        def plan(self, *a, **k): return {"pass": True, "steps": [{"type":"navigate","risk":"read"}], "mutation_required": False, "mutation_actions": []}
    class Executor:
        def __init__(self, *a, **k): pass
        async def execute(self, **k): raise RuntimeError("token=SUPERSECRET simulated failure")
    monkeypatch.setattr("hip_id_agent.production_e2e.UniversalPortalTaskPlanner", Planner)
    monkeypatch.setattr("hip_id_agent.production_e2e.UniversalPortalTaskExecutor", Executor)
    monkeypatch.setattr(orch, "doctor", lambda request, mutation_expected=False: {"pass": True, "decision": "GO", "blocker_count": 0, "warning_count": 0, "checks": []})
    result = asyncio.run(orch.execute(req))
    assert result["status"] == "execution_exception"
    diag = json.loads((Path(result["run_dir"]) / "production_failure_diagnostics.json").read_text(encoding="utf-8"))
    assert diag["exception_type"] == "RuntimeError"
    assert "SUPERSECRET" not in json.dumps(diag)
    assert diag["locals_captured"] is False


def test_r2_ledger_status_rejects_malformed_json_line(tmp_path: Path):
    ledger = ChangeAuditLedger(tmp_path / "ledger.jsonl")
    ledger.path.write_text('{not-json}\n', encoding='utf-8')
    status = ledger.status()
    assert status["chain_valid"] is False
    assert status["raw_invalid_line_count"] == 1
