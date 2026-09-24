from __future__ import annotations

import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .security import mask_sensitive_string


_SECRET_KEYWORDS = ("token", "password", "secret", "authorization", "cookie", "api_key", "apikey")


def _safe_key(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip().replace("\\", "/")
    allowed = []
    for ch in text:
        allowed.append(ch if (ch.isalnum() or ch in "_-. /:") else "_")
    out = "".join(allowed).strip() or "unknown"
    return out[:limit]


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_s = str(key)
            if any(part in key_s.lower() for part in _SECRET_KEYWORDS):
                result[key_s] = "***REDACTED***"
            else:
                result[key_s] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return mask_sensitive_string(value)
    return value




def _deep_find(mapping: Any, key: str, *, max_depth: int = 8) -> Any:
    def walk(node: Any, depth: int) -> Any:
        if depth > max_depth:
            return None
        if isinstance(node, dict):
            if key in node:
                return node.get(key)
            for value in node.values():
                found = walk(value, depth + 1)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for value in node[:100]:
                found = walk(value, depth + 1)
                if found is not None:
                    return found
        return None
    return walk(mapping, 0)


class AsyncMLflowTracker:
    """Fail-open, run-id based MLflow tracking for the HIP agent.

    Design rules:
    - Browser execution never waits for MLflow metric/param/tag writes.
    - MLflow failures never authorize/block a HIP action or change mission truth.
    - Customer field values/selectors/coordinates are not emitted as metrics/tags.
    - Artifacts are off by default and, when enabled, are uploaded on a single
      background worker after redaction/copying.
    - The native MLflow async queue is used for batch metrics/params/tags.
    """

    SCHEMA = "hip.mlflow-async-observability.v1"

    def __init__(
        self,
        config: Any,
        *,
        run_id: str,
        run_dir: str | Path,
        phases: Sequence[str] = (),
        app_version: str = "",
    ) -> None:
        self.config = config
        self.run_id = str(run_id)
        self.run_dir = Path(run_dir)
        self.phases = [str(p) for p in phases]
        self.app_version = str(app_version or "")
        self.enabled = bool(getattr(config, "enabled", False))
        self.fail_open = bool(getattr(config, "fail_open", True))
        self.async_logging = bool(getattr(config, "async_logging", True))
        self.log_artifacts = bool(getattr(config, "log_artifacts", False))
        self.log_event_artifact = bool(getattr(config, "log_event_artifact", True))
        self.max_pending_operations = max(32, int(getattr(config, "max_pending_operations", 2048) or 2048))
        configured_tracking_uri = str(
            getattr(config, "tracking_uri", "") or os.getenv("MLFLOW_TRACKING_URI", "") or ""
        ).strip()
        if configured_tracking_uri:
            self.tracking_uri = configured_tracking_uri
            self.tracking_uri_source = "configured"
        else:
            # mlflow-skinny intentionally omits SQL backend dependencies.  Never
            # rely on MLflow's process-wide default (which may resolve to SQLite
            # on newer MLflow releases).  Use a deterministic local FileStore
            # beside the HIP run roots unless the operator supplies a remote URI.
            local_store = (self.run_dir.parent / "mlruns").resolve()
            local_store.mkdir(parents=True, exist_ok=True)
            self.tracking_uri = local_store.as_uri()
            self.tracking_uri_source = "local_file_fallback"
        self.experiment_name = str(getattr(config, "experiment_name", "HIP Portal Agent") or "HIP Portal Agent")
        self.run_name_prefix = str(getattr(config, "run_name_prefix", "hip") or "hip")
        self._client = None
        self._mlflow = None
        self._mlflow_run_id = ""
        self._experiment_id = ""
        self._pending: list[Any] = []
        self._errors = 0
        self._dropped = 0
        self._last_error = ""
        self._started_at = 0.0
        self._phase_start: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._artifact_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hip-mlflow-artifact")
        self._artifact_futures: list[Any] = []
        self._events_path = self.run_dir / "mlflow_async_events.jsonl"
        self._status_path = self.run_dir / "mlflow_status.json"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @property
    def mlflow_run_id(self) -> str:
        return self._mlflow_run_id

    def _remember_error(self, exc: Exception | str) -> None:
        with self._lock:
            self._errors += 1
            self._last_error = mask_sensitive_string(str(exc))[:1200]
        self._persist_status()

    def _track_op(self, op: Any) -> None:
        if op is None:
            return
        with self._lock:
            if len(self._pending) >= self.max_pending_operations:
                self._pending = self._pending[-(self.max_pending_operations // 2):]
                self._dropped += 1
            self._pending.append(op)

    def _persist_status(self) -> None:
        payload = self.status()
        try:
            tmp = self._status_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(self._status_path)
        except Exception:
            pass

    def start(self, *, tags: Optional[Mapping[str, Any]] = None, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        if not self.enabled:
            self._persist_status()
            return self.status()
        if self._client is not None and self._mlflow_run_id:
            return self.status()
        try:
            import mlflow
            from mlflow import MlflowClient

            self._mlflow = mlflow
            if self.tracking_uri:
                mlflow.set_tracking_uri(self.tracking_uri)
            if self.async_logging and hasattr(mlflow, "config") and hasattr(mlflow.config, "enable_async_logging"):
                mlflow.config.enable_async_logging(True)
            if bool(getattr(self.config, "system_metrics", False)) and hasattr(mlflow.config, "enable_system_metrics_logging"):
                mlflow.config.enable_system_metrics_logging()
            self._client = MlflowClient(tracking_uri=self.tracking_uri or None)
            exp = self._client.get_experiment_by_name(self.experiment_name)
            if exp is None:
                try:
                    experiment_id = self._client.create_experiment(self.experiment_name)
                except Exception:
                    exp = self._client.get_experiment_by_name(self.experiment_name)
                    if exp is None:
                        raise
                    experiment_id = exp.experiment_id
            else:
                experiment_id = exp.experiment_id
            self._experiment_id = str(experiment_id)
            run_name = f"{self.run_name_prefix}-{self.run_id}"
            base_tags = {
                "mlflow.runName": run_name,
                "hip.schema": self.SCHEMA,
                "hip.run_id": self.run_id,
                "hip.app_version": self.app_version or "unknown",
                "hip.async_logging": str(self.async_logging).lower(),
                "hip.phase_count": str(len(self.phases)),
                "hip.value_logging": "disabled",
            }
            for key, value in (tags or {}).items():
                base_tags[_safe_key(key)] = str(value)[:5000]
            run = self._client.create_run(experiment_id=self._experiment_id, tags=base_tags)
            self._mlflow_run_id = str(run.info.run_id)
            self._started_at = time.time()
            self.log_params({
                "hip.app_version": self.app_version or "unknown",
                "hip.phase_sequence": ",".join(self.phases),
                "hip.async_logging": self.async_logging,
                "hip.fail_open": self.fail_open,
                **dict(params or {}),
            })
            self.log_metrics({"mission.started": 1.0, "mission.phase_count": float(len(self.phases))}, step=0)
            self.log_event("mission_started", {"phase_count": len(self.phases)})
        except Exception as exc:
            self._remember_error(exc)
            self._client = None
            self._mlflow_run_id = ""
            if not self.fail_open:
                raise
        self._persist_status()
        return self.status()

    def log_batch(
        self,
        *,
        metrics: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        tags: Optional[Mapping[str, Any]] = None,
        step: int = 0,
    ) -> None:
        if not self.enabled or self._client is None or not self._mlflow_run_id:
            return
        try:
            from mlflow.entities import Metric, Param, RunTag

            now_ms = int(time.time() * 1000)
            m_entities = []
            for key, value in (metrics or {}).items():
                f = _safe_float(value)
                if f is not None:
                    m_entities.append(Metric(_safe_key(key), f, now_ms, int(step)))
            p_entities = [Param(_safe_key(k), str(v)[:6000]) for k, v in (params or {}).items() if v is not None]
            t_entities = [RunTag(_safe_key(k), str(v)[:5000]) for k, v in (tags or {}).items() if v is not None]
            if not (m_entities or p_entities or t_entities):
                return
            op = self._client.log_batch(
                self._mlflow_run_id,
                metrics=m_entities,
                params=p_entities,
                tags=t_entities,
                synchronous=not self.async_logging,
            )
            self._track_op(op)
        except Exception as exc:
            self._remember_error(exc)
            if not self.fail_open:
                raise

    def log_metrics(self, metrics: Mapping[str, Any], *, step: int = 0) -> None:
        self.log_batch(metrics=metrics, step=step)

    def log_params(self, params: Mapping[str, Any]) -> None:
        self.log_batch(params=params)

    def log_tags(self, tags: Mapping[str, Any]) -> None:
        self.log_batch(tags=tags)

    def log_event(self, event: str, payload: Optional[Mapping[str, Any]] = None) -> None:
        row = {
            "schema_version": self.SCHEMA,
            "ts_epoch": time.time(),
            "event": str(event),
            "run_id": self.run_id,
            "payload": _redact(dict(payload or {})),
        }
        try:
            with self._lock:
                with self._events_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            self._remember_error(exc)
        # Event identity only; payload remains in the local redacted event file.
        self.log_tags({"hip.last_event": str(event)[:240]})

    def phase_started(self, phase: str, *, attempt: int) -> None:
        phase = str(phase)
        self._phase_start[phase] = time.time()
        step = max(0, int(attempt))
        self.log_metrics({f"phase/{phase}/started": 1.0, f"phase/{phase}/attempt": float(step)}, step=step)
        self.log_event("phase_started", {"phase": phase, "attempt": step})

    def phase_completed(
        self,
        phase: str,
        *,
        attempt: int,
        judge_pass: bool,
        verification: Optional[Mapping[str, Any]] = None,
    ) -> None:
        phase = str(phase)
        duration = max(0.0, time.time() - self._phase_start.get(phase, time.time()))
        verification = dict(verification or {})
        metrics: Dict[str, Any] = {
            f"phase/{phase}/complete": 1.0,
            f"phase/{phase}/judge_pass": 1.0 if judge_pass else 0.0,
            f"phase/{phase}/duration_seconds": duration,
        }
        for source_key, metric_suffix in (
            ("input_owned_coverage_percent", "input_coverage_percent"),
            ("input_owned_expected_node_count", "input_expected_count"),
            ("input_owned_exact_node_count", "input_exact_count"),
            ("non_authoritative_mutation_attempt_count", "non_authoritative_mutation_attempts"),
        ):
            value = _deep_find(verification, source_key)
            if value is not None:
                metrics[f"phase/{phase}/{metric_suffix}"] = value
        self.log_metrics(metrics, step=max(0, int(attempt)))
        self.log_event("phase_completed", {"phase": phase, "attempt": attempt, "judge_pass": bool(judge_pass), "duration_seconds": duration})

    def phase_blocked(self, phase: str, *, attempt: int, reason: str) -> None:
        phase = str(phase)
        duration = max(0.0, time.time() - self._phase_start.get(phase, time.time()))
        self.log_metrics({f"phase/{phase}/blocked": 1.0, f"phase/{phase}/duration_seconds": duration}, step=max(0, int(attempt)))
        self.log_tags({"hip.last_blocked_phase": phase, "hip.last_block_reason": _safe_key(reason, limit=500)})
        self.log_event("phase_blocked", {"phase": phase, "attempt": attempt, "reason": reason})

    def log_judge(self, phase: str, judge_result: Mapping[str, Any], *, attempt: int = 0) -> None:
        phase = str(phase)
        result = dict(judge_result or {})
        deterministic = result.get("deterministic_judge") if isinstance(result.get("deterministic_judge"), dict) else {}
        text = result.get("text_model_judge") if isinstance(result.get("text_model_judge"), dict) else {}
        vision = result.get("vision_model_judge") if isinstance(result.get("vision_model_judge"), dict) else {}
        self.log_metrics({
            f"judge/{phase}/pass": 1.0 if result.get("pass") is True else 0.0,
            f"judge/{phase}/deterministic_pass": 1.0 if deterministic.get("pass") is True else 0.0,
            f"judge/{phase}/text_pass": 1.0 if text.get("pass") is True else 0.0,
            f"judge/{phase}/vision_pass": 1.0 if vision.get("pass") is True else 0.0,
        }, step=max(0, int(attempt)))
        self.log_event("section_judge", {"phase": phase, "attempt": attempt, "pass": result.get("pass")})

    def log_recovery(self, phase: str, *, recovery_type: str, success: Optional[bool] = None, attempt: int = 0) -> None:
        metrics = {f"recovery/{phase}/attempts": 1.0}
        if success is not None:
            metrics[f"recovery/{phase}/success"] = 1.0 if success else 0.0
        self.log_metrics(metrics, step=max(0, int(attempt)))
        self.log_event("recovery", {"phase": phase, "type": recovery_type, "success": success, "attempt": attempt})

    def log_artifact_async(self, local_path: str | Path, *, artifact_path: str = "hip_evidence") -> None:
        if not self.enabled or not self.log_artifacts or self._client is None or not self._mlflow_run_id:
            return
        path = Path(local_path)
        if not path.exists():
            return

        def _upload() -> None:
            try:
                self._client.log_artifact(self._mlflow_run_id, str(path), artifact_path=artifact_path)
            except Exception as exc:
                self._remember_error(exc)
                if not self.fail_open:
                    raise

        with self._lock:
            self._artifact_futures.append(self._artifact_pool.submit(_upload))

    def finish(self, *, status: str, application_complete: bool, blocked_phase: str = "", final_report: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        elapsed = max(0.0, time.time() - self._started_at) if self._started_at else 0.0
        complete_count = 0
        if isinstance(final_report, Mapping):
            phases = final_report.get("phases") if isinstance(final_report.get("phases"), dict) else {}
            complete_count = sum(1 for row in phases.values() if isinstance(row, dict) and row.get("status") == "complete")
        self.log_metrics({
            "mission.completed": 1.0 if application_complete else 0.0,
            "mission.duration_seconds": elapsed,
            "mission.completed_phase_count": float(complete_count),
            "mission.mlflow_errors": float(self._errors),
        }, step=len(self.phases) + 1)
        self.log_tags({
            "hip.mission_status": str(status),
            "hip.application_complete": str(bool(application_complete)).lower(),
            "hip.blocked_phase": str(blocked_phase or ""),
        })
        self.log_event("mission_finished", {"status": status, "application_complete": bool(application_complete), "blocked_phase": blocked_phase, "duration_seconds": elapsed})
        if self.log_event_artifact and self._events_path.is_file():
            self.log_artifact_async(self._events_path, artifact_path="telemetry")
        if self.log_artifacts:
            for name in (
                "mission_completion_report.json",
                "mission_completion_report.md",
                "mission_trace.json",
                "agent_live_view.json",
                "input_contract_preflight.json",
            ):
                path = self.run_dir / name
                if path.is_file():
                    self.log_artifact_async(path, artifact_path="mission")
        # Termination is intentionally non-authoritative and fail-open. Async
        # logging is flushed first so the server receives the final metrics.
        self.flush(timeout_seconds=float(getattr(self.config, "flush_timeout_seconds", 8.0) or 8.0))
        if self._client is not None and self._mlflow_run_id:
            try:
                self._client.set_terminated(self._mlflow_run_id, status="FINISHED" if application_complete else "FAILED")
            except Exception as exc:
                self._remember_error(exc)
                if not self.fail_open:
                    raise
        self._persist_status()
        return self.status()

    def flush(self, *, timeout_seconds: float = 8.0) -> None:
        deadline = time.time() + max(0.0, float(timeout_seconds))
        mlflow_obj = self._mlflow
        try:
            if mlflow_obj is not None and hasattr(mlflow_obj, "flush_async_logging"):
                mlflow_obj.flush_async_logging()
        except Exception as exc:
            self._remember_error(exc)
        with self._lock:
            futures = list(self._artifact_futures)
        for future in futures:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                future.result(timeout=remaining)
            except Exception as exc:
                self._remember_error(exc)
        self._persist_status()

    def close(self) -> None:
        try:
            self.flush(timeout_seconds=float(getattr(self.config, "flush_timeout_seconds", 8.0) or 8.0))
        finally:
            try:
                self._artifact_pool.shutdown(wait=False, cancel_futures=False)
            except Exception:
                pass

    def status(self) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "enabled": self.enabled,
            "available": bool(self._client is not None and self._mlflow_run_id),
            "async_logging": self.async_logging,
            "fail_open": self.fail_open,
            "tracking_uri": self.tracking_uri,
            "tracking_uri_source": self.tracking_uri_source,
            "experiment_name": self.experiment_name,
            "mlflow_run_id": self._mlflow_run_id,
            "hip_run_id": self.run_id,
            "pending_operation_count": len(self._pending),
            "artifact_operation_count": len(self._artifact_futures),
            "dropped_operation_count": self._dropped,
            "error_count": self._errors,
            "last_error": self._last_error,
            "event_log": str(self._events_path) if self._events_path.is_file() else "",
            "policy": "MLflow is observability-only, asynchronous, value-safe, and fail-open for portal execution.",
        }


def mlflow_runtime_probe(config: Any) -> Dict[str, Any]:
    """Non-mutating package/config probe for UI readiness."""
    enabled = bool(getattr(config, "enabled", False))
    result = {
        "enabled": enabled,
        "async_logging": bool(getattr(config, "async_logging", True)),
        "tracking_uri": str(getattr(config, "tracking_uri", "") or os.getenv("MLFLOW_TRACKING_URI", "") or "local file store (runs/mlruns)"),
        "tracking_uri_source": "configured" if str(getattr(config, "tracking_uri", "") or os.getenv("MLFLOW_TRACKING_URI", "") or "").strip() else "local_file_fallback",
        "experiment_name": str(getattr(config, "experiment_name", "HIP Portal Agent") or "HIP Portal Agent"),
        "fail_open": bool(getattr(config, "fail_open", True)),
        "package_available": False,
        "version": "",
        "status": "disabled" if not enabled else "unavailable",
    }
    if not enabled:
        return result
    try:
        import mlflow
        result["package_available"] = True
        result["version"] = str(getattr(mlflow, "__version__", ""))
        result["status"] = "ready"
    except Exception as exc:
        result["error"] = mask_sensitive_string(str(exc))[:1000]
    return result


__all__ = ["AsyncMLflowTracker", "mlflow_runtime_probe"]
