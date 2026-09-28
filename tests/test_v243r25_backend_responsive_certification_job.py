"""V243R25: "Backend offline" and a NO_GO certificate that was never real.

Live report (2026-09-28): the Control Center kept flipping to "Backend offline",
and "Certify Windows runtime" showed NO_GO, 1 blocker, no certificate and "No rows
available" -- the page's own placeholder for a request that failed.

Root causes:

* the certification ran *inside* the backend: it waits for Dell SSO, probes the
  MCP servers and Dell AIA with blocking calls and (R24) asks every model the
  qualification questions -- the event loop froze, every status poll hung, and
  the page showed "Backend offline";
* the Bun proxy (webui/server.js) gave up on the backend after ~300 s (measured:
  a 320 s request came back as HTTP 503 after 288 s, "HIP backend is
  unavailable"); the page turned that into its NO_GO placeholder;
* the status polls were expensive (the whole mission log re-read every 3 s, a
  ``tasklist`` process per poll on Windows, the model portfolio built three
  times, the whole usage ledger parsed) and stacked up every 3 s;
* one failing manifest made ``/api/runtime/status`` answer 500, which the page
  also shows as "Backend offline".
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app as appmod

ROOT = Path(__file__).resolve().parents[1]


def _fake_certification(tmp_path: Path, monkeypatch, *, seconds: float = 3.0, result: bool = True, exit_code: int = 0):
    """A stand-in for ``hip_id_agent.cli certify-live-runtime``: writes progress, then the result."""
    script = tmp_path / "fake_cert.py"
    script.write_text(f"""
import json, sys, time
from pathlib import Path
result, progress = Path(sys.argv[1]), Path(sys.argv[2])
checks = []
for i, label in enumerate(["Windows interactive desktop", "Dell AIA text model", "Dell SSO authenticated HIP session"]):
    checks.append({{"id": f"c{{i}}", "label": label, "pass": True, "severity": "blocker", "detail": ""}})
    progress.write_text(json.dumps({{"checks": checks, "last_check": label}}))
    time.sleep({seconds} / 3)
print("certification log line", flush=True)
if {result!r}:
    result.write_text(json.dumps({{"pass": True, "decision": "GO", "checks": checks, "blocker_count": 0, "warning_count": 0}}))
sys.exit({exit_code})
""", encoding="utf-8")

    def command(req, cfg, runs_root, job_dir):
        return [sys.executable, str(script), str(job_dir / "result.json"), str(job_dir / "progress.json")]

    from hip_id_agent.config import AppConfig

    monkeypatch.setattr(appmod, "_certification_command", command)
    monkeypatch.setattr(appmod, "CERT_JOB_FILE", tmp_path / "job.json")
    monkeypatch.setattr(appmod, "CERT_JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(appmod, "_cfg", lambda *_a, **_k: AppConfig())
    monkeypatch.setattr(appmod, "_resolve_runs_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(appmod, "_process_state", lambda: {"running": False})
    return script


def _wait(client: TestClient, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get("/api/mission/live-runtime-certification/status").json()
        if state["status"] != "running":
            return state
        time.sleep(0.3)
    raise AssertionError("certification job did not finish")


# ------------------------------------------------------- certification job
def test_the_certification_runs_in_its_own_process_and_the_backend_answers_meanwhile(tmp_path, monkeypatch):
    _fake_certification(tmp_path, monkeypatch, seconds=3.0)
    client = TestClient(appmod.app)
    started = time.monotonic()
    job = client.post("/api/mission/live-runtime-certification", json={"config": "config.yaml", "runs_dir": str(tmp_path)}).json()
    assert time.monotonic() - started < 2.0 and job["status"] == "running"  # returns at once
    t = time.monotonic()
    assert client.get("/health").json()["status"] == "ok" and time.monotonic() - t < 1.0  # not frozen
    seen_progress = False
    deadline = time.time() + 20
    while time.time() < deadline:
        state = client.get("/api/mission/live-runtime-certification/status").json()
        seen_progress |= state["status"] == "running" and bool(state["checks"])
        if state["status"] != "running":
            break
        time.sleep(0.3)
    assert seen_progress  # checks appear while it runs
    assert state["status"] == "done" and state["result"]["decision"] == "GO" and len(state["result"]["checks"]) == 3


def test_a_second_start_follows_the_running_certification_instead_of_starting_another(tmp_path, monkeypatch):
    _fake_certification(tmp_path, monkeypatch, seconds=3.0)
    client = TestClient(appmod.app)
    first = client.post("/api/mission/live-runtime-certification", json={"config": "config.yaml", "runs_dir": str(tmp_path)}).json()
    second = client.post("/api/mission/live-runtime-certification", json={"config": "config.yaml", "runs_dir": str(tmp_path)}).json()
    assert second["already_running"] is True and second["job_id"] == first["job_id"]
    _wait(client)


def test_a_certification_that_ends_without_a_result_reports_the_real_reason(tmp_path, monkeypatch):
    _fake_certification(tmp_path, monkeypatch, seconds=0.3, result=False, exit_code=3)
    client = TestClient(appmod.app)
    client.post("/api/mission/live-runtime-certification", json={"config": "config.yaml", "runs_dir": str(tmp_path)})
    state = _wait(client)
    assert state["status"] == "failed" and "exit code 3" in state["error"]
    assert "certification log line" in state["log_tail"]
    assert "blocker_count" not in state  # no invented NO_GO


def test_a_certification_that_hangs_is_stopped_at_its_time_limit(tmp_path, monkeypatch):
    _fake_certification(tmp_path, monkeypatch, seconds=60.0)
    monkeypatch.setattr(appmod, "_certification_job_timeout", lambda cfg=None: 1)
    client = TestClient(appmod.app)
    client.post("/api/mission/live-runtime-certification", json={"config": "config.yaml", "runs_dir": str(tmp_path)})
    time.sleep(1.5)
    state = _wait(client, timeout=10)
    assert state["status"] == "failed" and "did not finish within 1 s" in state["error"]


def test_wait_true_returns_the_certificate_without_blocking_the_backend(tmp_path, monkeypatch):
    _fake_certification(tmp_path, monkeypatch, seconds=1.0)
    state = TestClient(appmod.app).post("/api/mission/live-runtime-certification?wait=true",
                                        json={"config": "config.yaml", "runs_dir": str(tmp_path)}).json()
    assert state["status"] == "done" and state["result"]["pass"] is True
    assert "await asyncio.sleep(" in inspect.getsource(appmod.mission_live_runtime_certification)


def test_live_readiness_renews_the_certificate_through_the_child_process_and_keeps_the_loop_free():
    source = inspect.getsource(appmod._run_live_readiness)
    assert "await run_in_threadpool(test_text_model" in source  # the Dell AIA text call left the event loop
    assert "await run_in_threadpool(_preflight_for_phases" in source
    assert "certify_live_runtime" in source and appmod.certify_live_runtime.__module__ == "backend.app"
    assert "_start_certification_job" in inspect.getsource(appmod.certify_live_runtime)


def test_the_cli_writes_its_result_and_progress_for_the_job(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from hip_id_agent import cli

    async def boom(**kwargs):
        raise RuntimeError("Playwright could not start Chrome")

    monkeypatch.setattr(cli, "certify_live_runtime", boom)
    out = tmp_path / "result.json"
    CliRunner().invoke(cli.app, ["certify-live-runtime", "--config", str(ROOT / "config.yaml"), "--result-json", str(out),
                                 "--progress-json", str(tmp_path / "p.json")])
    data = json.loads(out.read_text())
    assert data["pass"] is False and "Playwright could not start Chrome" in data["error"]


def test_every_finished_check_is_written_as_progress_and_failures_say_why(tmp_path):
    from hip_id_agent.live_runtime_certification import _ProgressChecks, _check

    checks = _ProgressChecks(tmp_path / "progress.json")
    checks.append(_check("dell_aia_text", "Dell AIA text model", False, evidence={"pass": False, "error": "AIA endpoint/token missing"}))
    data = json.loads((tmp_path / "progress.json").read_text())
    assert data["last_check"] == "Dell AIA text model" and data["checks"][0]["detail"] == "AIA endpoint/token missing"


# ------------------------------------------------------------ status polls
def test_the_status_poll_reads_only_the_end_of_a_huge_mission_log(tmp_path, monkeypatch):
    log = tmp_path / "mission.log"
    with log.open("w", encoding="utf-8") as handle:
        for i in range(400_000):  # ~30 MB
            handle.write(f"line {i:07d} " + "x" * 60 + "\n")
        handle.write("THE LAST LINE\n")
    monkeypatch.setattr(appmod, "_process_state", lambda: {"running": True, "log_path": str(log)})
    started = time.monotonic()
    tail = appmod.discovery_status()["console_tail"]
    assert time.monotonic() - started < 0.2 and tail.endswith("THE LAST LINE\n") and len(tail) <= 30000


def test_a_failing_manifest_gives_a_degraded_status_not_a_500(monkeypatch):
    monkeypatch.setattr(appmod, "_runtime_status_payload", lambda config="config.yaml": (_ for _ in ()).throw(ValueError("corrupt replay_policy.json")))
    appmod._RUNTIME_STATUS_LAST.pop("broken.yaml", None)
    response = TestClient(appmod.app).get("/api/runtime/status?config=broken.yaml")
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True and "corrupt replay_policy.json" in body["status_error"] and "process" in body


def test_overlapping_status_polls_share_one_computation(monkeypatch):
    import threading

    calls = {"n": 0}
    gate = threading.Event()

    def slow(config="config.yaml"):
        calls["n"] += 1
        gate.wait(5)
        return {"model_portfolio": {}, "n": calls["n"]}

    appmod._RUNTIME_STATUS_LAST["shared.yaml"] = {"at": time.monotonic(), "payload": {"n": 0}}
    monkeypatch.setattr(appmod, "_runtime_status_payload", slow)
    worker = threading.Thread(target=appmod.runtime_status, args=("shared.yaml",))
    worker.start()
    time.sleep(0.2)
    started = time.monotonic()
    second = appmod.runtime_status("shared.yaml")  # while the first is still computing
    assert time.monotonic() - started < 0.5 and second["shared_with_running_poll"] is True and calls["n"] == 1
    gate.set()
    worker.join()


def test_recent_model_usage_reads_only_the_end_of_the_ledger(tmp_path):
    from types import SimpleNamespace

    from hip_id_agent.config import AppConfig
    from hip_id_agent.model_portfolio import OnPremModelPortfolioRouter

    router = OnPremModelPortfolioRouter(tmp_path, AppConfig().model_portfolio, aia_config=SimpleNamespace(model="gpt-oss-120b"))
    with router.usage_path.open("w", encoding="utf-8") as handle:
        for i in range(200_000):
            handle.write(json.dumps({"i": i, "candidate_models": ["gpt-oss-120b"]}) + "\n")
    started = time.monotonic()
    rows = router.recent_usage(25)
    assert time.monotonic() - started < 0.2 and [r["i"] for r in rows] == list(range(199_975, 200_000))


def test_the_windows_process_check_no_longer_spawns_tasklist_first():
    source = inspect.getsource(appmod._is_running)
    assert source.index("GetExitCodeProcess") < source.index('["tasklist"')


# ----------------------------------------------------------- web UI and proxy
def test_the_control_center_follows_the_job_and_never_invents_a_no_go():
    for base in ("webui", "backend/webui"):
        js = (ROOT / base / "app.js").read_text(encoding="utf-8")
        assert "/api/mission/live-runtime-certification/status" in js and "followCertificationJob" in js
        assert "blocker_count:1" not in js  # the old placeholder
        assert "if (state.refreshing) return;" in js and "state.statusFailures >= 3" in js
        assert "timeoutMs" in js


def test_the_bun_proxy_never_cuts_a_request_short_and_the_backend_is_supervised():
    for base in ("webui", "backend/webui"):
        server = (ROOT / base / "server.js").read_text(encoding="utf-8")
        assert "timeout: false" in server and "idleTimeout: 255" in server and "status: timedOut ? 504 : 503" in server
        platform = (ROOT / base / "platform.js").read_text(encoding="utf-8")
        assert "superviseBackend()" in platform and "await backend.exited" in platform
