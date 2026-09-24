from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import yaml

from hip_id_agent import __version__
from hip_id_agent.config import MLflowConfig, load_config
from hip_id_agent.mission_controller import MissionController, PHASE_JUDGE_RESULT_FILENAME, PHASE_VERIFICATION_FILENAME
from hip_id_agent.mlflow_async import AsyncMLflowTracker, mlflow_runtime_probe


class _Op:
    pass


class _FakeClient:
    instances = []

    def __init__(self, tracking_uri=None):
        self.tracking_uri = tracking_uri
        self.batches = []
        self.artifacts = []
        self.terminated = []
        self.__class__.instances.append(self)

    def get_experiment_by_name(self, name):
        return None

    def create_experiment(self, name):
        self.experiment_name = name
        return "exp-1"

    def create_run(self, experiment_id, tags=None):
        self.create_run_args = (experiment_id, dict(tags or {}))
        return types.SimpleNamespace(info=types.SimpleNamespace(run_id="mlflow-run-1"))

    def log_batch(self, run_id, metrics=(), params=(), tags=(), synchronous=None):
        self.batches.append({
            "run_id": run_id,
            "metrics": list(metrics),
            "params": list(params),
            "tags": list(tags),
            "synchronous": synchronous,
        })
        return _Op()

    def log_artifact(self, run_id, local_path, artifact_path=None):
        self.artifacts.append((run_id, local_path, artifact_path))

    def set_terminated(self, run_id, status="FINISHED"):
        self.terminated.append((run_id, status))


def _fake_mlflow(monkeypatch):
    _FakeClient.instances.clear()
    module = types.ModuleType("mlflow")
    module.__version__ = "3.16.1"
    module._tracking_uri = ""
    module._async_enabled = False
    module._flushed = False
    module.MlflowClient = _FakeClient
    module.set_tracking_uri = lambda uri: setattr(module, "_tracking_uri", uri)
    module.flush_async_logging = lambda: setattr(module, "_flushed", True)
    module.config = types.SimpleNamespace(
        enable_async_logging=lambda value=True: setattr(module, "_async_enabled", bool(value)),
        enable_system_metrics_logging=lambda: None,
    )

    entities = types.ModuleType("mlflow.entities")

    class Metric:
        def __init__(self, key, value, timestamp, step):
            self.key, self.value, self.timestamp, self.step = key, value, timestamp, step

    class Param:
        def __init__(self, key, value):
            self.key, self.value = key, value

    class RunTag:
        def __init__(self, key, value):
            self.key, self.value = key, value

    entities.Metric, entities.Param, entities.RunTag = Metric, Param, RunTag
    monkeypatch.setitem(sys.modules, "mlflow", module)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    return module


def test_v238_version_and_all_shipped_configs_enable_async_mlflow():
    assert __version__ == "2.4.3"
    root = Path(__file__).resolve().parents[1]
    for name in ("config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"):
        raw = yaml.safe_load((root / name).read_text(encoding="utf-8"))
        assert raw["mlflow"]["enabled"] is True
        assert raw["mlflow"]["async_logging"] is True
        assert raw["mlflow"]["fail_open"] is True
        assert raw["mlflow"]["log_artifacts"] is False
        cfg = load_config(root / name)
        assert cfg.mlflow.experiment_name == "HIP Portal Agent"


def test_mlflow_async_tracker_uses_native_nonblocking_batches_and_coverage_metrics(tmp_path, monkeypatch):
    fake = _fake_mlflow(monkeypatch)
    cfg = MLflowConfig(
        enabled=True,
        tracking_uri="http://mlflow.internal:5000",
        async_logging=True,
        fail_open=True,
        log_artifacts=False,
    )
    tracker = AsyncMLflowTracker(cfg, run_id="HIP-1", run_dir=tmp_path, phases=["data_map"], app_version="2.3.8")
    status = tracker.start(tags={"hip.mode": "test"})
    assert status["available"] is True
    assert fake._tracking_uri == "http://mlflow.internal:5000"
    assert fake._async_enabled is True

    tracker.phase_started("data_map", attempt=1)
    tracker.phase_completed(
        "data_map",
        attempt=1,
        judge_pass=True,
        verification={
            "exact_state_lock": {
                "exact_completion_checkpoint": {
                    "execution_stage_audit": {
                        "input_owned_coverage_percent": 100.0,
                        "input_owned_expected_node_count": 7,
                        "input_owned_exact_node_count": 7,
                        "non_authoritative_mutation_attempt_count": 0,
                    }
                }
            }
        },
    )
    tracker.log_judge(
        "data_map",
        {
            "pass": True,
            "deterministic_judge": {"pass": True},
            "text_model_judge": {"pass": True},
            "vision_model_judge": {"pass": True},
        },
        attempt=1,
    )
    final = tracker.finish(status="complete", application_complete=True, final_report={"phases": {"data_map": {"status": "complete"}}})
    tracker.close()

    client = _FakeClient.instances[-1]
    assert client.batches
    assert all(batch["synchronous"] is False for batch in client.batches)
    metric_map = {m.key: m.value for batch in client.batches for m in batch["metrics"]}
    assert metric_map["phase/data_map/input_coverage_percent"] == 100.0
    assert metric_map["phase/data_map/input_expected_count"] == 7.0
    assert metric_map["phase/data_map/input_exact_count"] == 7.0
    assert metric_map["judge/data_map/pass"] == 1.0
    assert metric_map["mission.completed"] == 1.0
    assert fake._flushed is True
    assert client.terminated[-1] == ("mlflow-run-1", "FINISHED")
    assert final["error_count"] == 0
    assert (tmp_path / "mlflow_async_events.jsonl").is_file()


def test_mlflow_tracker_is_fail_open_when_backend_logging_fails(tmp_path, monkeypatch):
    _fake_mlflow(monkeypatch)
    cfg = MLflowConfig(enabled=True, async_logging=True, fail_open=True)
    tracker = AsyncMLflowTracker(cfg, run_id="HIP-2", run_dir=tmp_path, phases=["rule"], app_version="2.3.8")
    tracker.start()
    client = _FakeClient.instances[-1]

    def boom(*args, **kwargs):
        raise RuntimeError("tracking server unavailable token=super-secret")

    client.log_batch = boom
    tracker.log_metrics({"phase/rule/attempt": 2})
    status = tracker.status()
    assert status["error_count"] >= 1
    assert "super-secret" not in status["last_error"]


def test_mission_controller_emits_phase_telemetry_without_affecting_mission_truth(tmp_path):
    class Telemetry:
        def __init__(self):
            self.calls = []
        def phase_started(self, phase, *, attempt): self.calls.append(("start", phase, attempt))
        def phase_completed(self, phase, *, attempt, judge_pass, verification): self.calls.append(("complete", phase, attempt, judge_pass, verification))
        def log_judge(self, phase, result, *, attempt=0): self.calls.append(("judge", phase, attempt, result.get("pass")))
        def phase_blocked(self, phase, *, attempt, reason): self.calls.append(("blocked", phase, attempt, reason))

    mission = MissionController(tmp_path, run_id="R", phases=["data_map"])
    telemetry = Telemetry()
    mission.telemetry = telemetry
    phase_dir = tmp_path / "data_map"
    phase_dir.mkdir()
    (phase_dir / PHASE_VERIFICATION_FILENAME).write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (phase_dir / PHASE_JUDGE_RESULT_FILENAME).write_text(json.dumps({"pass": True}), encoding="utf-8")
    (phase_dir / "phase_exact_state_lock.json").write_text(json.dumps({"exact_completion_checkpoint": {"pass": True}}), encoding="utf-8")
    mission.mark_phase_started("data_map", attempt=1)
    mission.mark_phase_complete("data_map", attempt=1, judge_pass=True)
    assert mission.phase_status("data_map") == "complete"
    assert [c[0] for c in telemetry.calls] == ["start", "complete", "judge"]


def test_runtime_probe_reports_package_and_async_policy(monkeypatch):
    fake = _fake_mlflow(monkeypatch)
    result = mlflow_runtime_probe(MLflowConfig(enabled=True, async_logging=True))
    assert result["status"] == "ready"
    assert result["version"] == "3.16.1"
    assert result["async_logging"] is True


def test_mlflow_blank_tracking_uri_uses_local_file_store(tmp_path, monkeypatch):
    fake = _fake_mlflow(monkeypatch)
    cfg = MLflowConfig(enabled=True, tracking_uri="", async_logging=True, fail_open=True)
    tracker = AsyncMLflowTracker(cfg, run_id="run-local", run_dir=tmp_path / "runs" / "run-local", phases=["Data Map"], app_version="2.3.8")
    assert tracker.tracking_uri.startswith("file:")
    assert tracker.tracking_uri_source == "local_file_fallback"
    tracker.start()
    assert fake._tracking_uri == tracker.tracking_uri
    assert (tmp_path / "runs" / "mlruns").is_dir()
    tracker.close()
