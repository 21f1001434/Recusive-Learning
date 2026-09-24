# V238 MLflow Async Observability

Version: **2.3.8**

## Implemented

- `mlflow-skinny==3.16.1` tracking dependency.
- Native asynchronous MLflow metric/param/tag batches (`synchronous=False`).
- Mission-level run with phase sequence, version, mode, environment, browser channel and safe runtime metadata.
- Per-phase start, attempts, completion/block status, duration and judge results.
- Input-owned coverage metrics when present in phase verification evidence.
- Redacted local event ledger (`mlflow_async_events.jsonl`).
- Optional background artifact uploads; disabled by default because HIP evidence may contain customer data.
- Fail-open behavior: tracking outages never change browser action authorization or mission correctness.
- `mlflow_status.json` for each mission and MLflow runtime readiness in the FastAPI/JavaScript Control Center.

## Safety contract

MLflow is **observability only**. It is not a planner, executor, judge, mutation gate, replay authority or source of customer values. `input.json` remains value authority and the live HIP page remains action authority.

## Configuration

```yaml
mlflow:
  enabled: true
  tracking_uri: ""
  experiment_name: "HIP Portal Agent"
  run_name_prefix: "hip"
  async_logging: true
  fail_open: true
  system_metrics: false
  log_artifacts: false
  log_event_artifact: true
  flush_timeout_seconds: 8.0
  max_pending_operations: 2048
```

Set `MLFLOW_TRACKING_URI` if `tracking_uri` is empty and a remote server should be used.


## Tracking URI fallback
If `tracking_uri` and `MLFLOW_TRACKING_URI` are both blank, HIP explicitly selects a local MLflow FileStore under the run root (`runs/mlruns`) instead of relying on MLflow's process default. This keeps `mlflow-skinny` usable without SQL backend extras.
