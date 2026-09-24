from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
import traceback
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from . import __version__
from .autogen_runtime import autogen_runtime_status
from .capability_graph import HIPCapabilityGraph
from .change_governance import ChangeAuditLedger
from .config import AppConfig, load_config
from .future_task_agent import MUTATION_CONFIRMATION
from .live_runtime_certification import verify_latest_live_runtime_certificate
from .mlflow_async import mlflow_runtime_probe
from .native_hip_phase_mission import NativeHIPPhaseMissionCoordinator
from .model_portfolio import model_portfolio_from_config
from .replay_policy import replay_policy_engine_from_config
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .skill_induction import InducedSkillLibrary
from .universal_portal_operator import (
    UniversalPortalTaskExecutor,
    UniversalPortalTaskPlanner,
    flatten_nonblank_leaves,
)


PRODUCTION_SCHEMA = "hip.production-e2e.v1"
DOCTOR_SCHEMA = "hip.production-doctor.v1"
JOURNAL_SCHEMA = "hip.production-journal-event.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return _sha_bytes(raw.encode("utf-8"))


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _safe_path(value: str | Path, *, root: Path) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (root / p).resolve()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _task_needs_runtime_input(task: str) -> bool:
    text = str(task or "")
    return bool(re.search(r"\b(fill|create|edit|update|configure|change|modify|clone|input\s*json|form|add\s+row)\b", text, flags=re.I))


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return str(pid) in (out.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class ProductionExecutionLease:
    """Cross-process single-browser lease with stale-process reclamation."""

    def __init__(self, path: Path, *, stale_seconds: int = 14400, fail_on_stale: bool = True):
        self.path = Path(path)
        self.stale_seconds = max(60, int(stale_seconds))
        self.fail_on_stale = bool(fail_on_stale)
        self.acquired = False

    def inspect(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return {"present": False, "active": False, "stale": False, "path": str(self.path)}
        row = _read_json(self.path)
        row = row if isinstance(row, dict) else {}
        pid = int(row.get("pid") or 0)
        try:
            age = max(0.0, time.time() - self.path.stat().st_mtime)
        except Exception:
            age = 0.0
        active = _is_pid_alive(pid)
        stale = (not active) or age > self.stale_seconds
        return {
            "present": True, "active": active, "stale": stale, "path": str(self.path),
            "pid": pid, "age_seconds": round(age, 3), "metadata": mask_sensitive_data(row),
        }

    def acquire(self, *, run_id: str, task_hash: str) -> Dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = {
                    "schema_version": "hip.production-execution-lease.v1",
                    "pid": os.getpid(), "run_id": run_id, "task_hash": task_hash,
                    "created_at": utc_now(), "created_at_epoch": time.time(),
                }
                os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
                os.close(fd)
                self.acquired = True
                return {"pass": True, "status": "acquired", **mask_sensitive_data(payload), "path": str(self.path)}
            except FileExistsError:
                current = self.inspect()
                if current.get("stale"):
                    if self.fail_on_stale and current.get("active"):
                        return {"pass": False, "status": "stale_but_active", **current}
                    try:
                        self.path.unlink()
                        continue
                    except Exception as exc:
                        return {"pass": False, "status": "stale_lock_remove_failed", "error": mask_sensitive_string(str(exc)), **current}
                return {"pass": False, "status": "busy", **current}

    def heartbeat(self) -> Dict[str, Any]:
        """Refresh the active lease timestamp without changing its ownership metadata."""
        if not self.acquired or not self.path.is_file():
            return {"pass": False, "status": "not_acquired", "path": str(self.path)}
        row = _read_json(self.path)
        if not isinstance(row, dict) or int(row.get("pid") or 0) != os.getpid():
            return {"pass": False, "status": "ownership_lost", "path": str(self.path)}
        try:
            os.utime(self.path, None)
            return {"pass": True, "status": "heartbeat", "path": str(self.path), "pid": os.getpid()}
        except Exception as exc:
            return {"pass": False, "status": "heartbeat_failed", "path": str(self.path), "error": mask_sensitive_string(str(exc))}

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            row = _read_json(self.path)
            if not isinstance(row, dict) or int(row.get("pid") or 0) == os.getpid():
                self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


class HashChainedJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.is_file():
            return "GENESIS"
        try:
            last = ""
            with self.path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.strip():
                        last = line
            if not last:
                return "GENESIS"
            row = json.loads(last)
            return str(row.get("event_hash") or "GENESIS")
        except Exception:
            return "GENESIS"

    def append(self, event_type: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        previous = self._last_hash()
        row: Dict[str, Any] = {
            "schema_version": JOURNAL_SCHEMA,
            "event_type": str(event_type),
            "timestamp": utc_now(),
            "previous_hash": previous,
            **mask_sensitive_data(dict(payload)),
        }
        row["event_hash"] = _sha_json({k: v for k, v in row.items() if k != "event_hash"})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
        return row

    def verify(self) -> Dict[str, Any]:
        previous = "GENESIS"
        count = 0
        if not self.path.is_file():
            return {"pass": True, "event_count": 0, "path": str(self.path)}
        for idx, line in enumerate(self.path.read_text(encoding="utf-8", errors="ignore").splitlines()):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                return {"pass": False, "event_count": count, "invalid_index": idx, "reason": f"invalid_json: {exc}"}
            claimed = str(row.get("event_hash") or "")
            computed = _sha_json({k: v for k, v in row.items() if k != "event_hash"})
            if str(row.get("previous_hash") or "") != previous or claimed != computed:
                return {"pass": False, "event_count": count, "invalid_index": idx, "reason": "hash_chain_mismatch"}
            previous = claimed
            count += 1
        return {"pass": True, "event_count": count, "last_event_hash": previous, "path": str(self.path)}


@dataclass
class ProductionRunRequest:
    task: str
    config_path: Path
    input_json: Path
    input_root: str = ""
    start_url: str = ""
    runs_dir: Optional[Path] = None
    golden_dir: Optional[Path] = None
    uploads_dir: Optional[Path] = None
    deep_learn: bool = True
    allow_portal_mutation: bool = False
    confirmation: str = ""
    operator_role: str = ""
    approval_id: str = ""
    force_repeat_mutation: bool = False


class ProductionDoctor:
    """Fast, non-mutating production readiness audit."""

    def __init__(self, *, root: Path, config: AppConfig):
        self.root = Path(root)
        self.config = config

    @staticmethod
    def _row(check_id: str, passed: bool, detail: str, *, severity: str = "blocker", evidence: Any = None) -> Dict[str, Any]:
        return {
            "id": check_id, "pass": bool(passed), "severity": severity,
            "detail": mask_sensitive_string(str(detail))[:2000], "evidence": mask_sensitive_data(evidence),
        }

    def run(
        self, *, input_json: Path, golden_dir: Optional[Path], uploads_dir: Optional[Path],
        task: str = "", mutation_expected: bool = False, runs_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        cfg = self.config
        prod = cfg.production_e2e
        checks: list[Dict[str, Any]] = []
        checks.append(self._row("python_version", sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}", evidence={"required": ">=3.11"}))
        checks.append(self._row("package_version", bool(__version__), f"hip-portal-id-agent {__version__}"))

        autogen = autogen_runtime_status(verify_imports=True)
        checks.append(self._row(
            "autogen_075", bool(autogen.get("pass")) or not bool(prod.require_autogen_075),
            str(autogen.get("detail") or autogen.get("reason") or ("ready" if autogen.get("pass") else "unavailable")),
            evidence=autogen,
        ))

        payload = _read_json(input_json) if input_json.is_file() else None
        valid_input = isinstance(payload, Mapping)
        leaf_count = len(flatten_nonblank_leaves(payload)) if valid_input else 0
        input_required = bool(prod.require_input_json_when_fill_requested and _task_needs_runtime_input(task))
        input_pass = valid_input or not input_required
        checks.append(self._row(
            "input_json", input_pass,
            f"{input_json} • {leaf_count} nonblank scalar/list leaves" if valid_input else (f"Required runtime input is missing/invalid: {input_json}" if input_required else f"No runtime input required for this task; optional file not available: {input_json}"),
            severity="blocker" if input_required else "warning",
            evidence={"leaf_count": leaf_count, "required_for_task": input_required, "sha256": _file_sha(input_json) if input_json.is_file() else ""},
        ))

        if golden_dir:
            golden_count = len([p for p in golden_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]) if golden_dir.is_dir() else 0
            required = bool(prod.require_golden_reference_for_known_phase_tasks)
            checks.append(self._row("golden_references", golden_count > 0 or not required, f"{golden_count} golden reference images", severity="blocker" if required else "warning", evidence={"path": str(golden_dir), "count": golden_count}))

        if uploads_dir:
            checks.append(self._row("upload_assets_dir", uploads_dir.is_dir(), f"Upload assets directory {'ready' if uploads_dir.is_dir() else 'missing'}: {uploads_dir}", severity="warning"))

        runs_root = Path(runs_dir or cfg.reporting.runs_dir)
        if not runs_root.is_absolute():
            runs_root = (self.root / runs_root).resolve()
        try:
            runs_root.mkdir(parents=True, exist_ok=True)
            probe = runs_root / ".hip_write_probe"
            probe.write_text("ok", encoding="utf-8"); probe.unlink(missing_ok=True)
            writable = True
        except Exception:
            writable = False
        try:
            free_mb = shutil.disk_usage(runs_root).free / (1024 * 1024)
        except Exception:
            free_mb = 0.0
        disk_ok = writable and free_mb >= int(prod.min_free_disk_mb)
        checks.append(self._row("runs_storage", disk_ok, f"Writable={writable}, free={free_mb:.0f} MB, required>={int(prod.min_free_disk_mb)} MB", evidence={"path": str(runs_root), "free_mb": round(free_mb, 2)}))

        lock_path = runs_root / str(prod.lock_filename)
        lease = ProductionExecutionLease(lock_path, stale_seconds=prod.lock_stale_seconds, fail_on_stale=prod.fail_on_stale_execution_lock)
        lock = lease.inspect()
        lock_ok = (not bool(lock.get("active"))) or (not bool(prod.single_active_browser_session))
        lock_detail = ("Single-session lock disabled by configuration" if not prod.single_active_browser_session else ("No competing active production browser session" if not lock.get("active") else f"Active run lock held by PID {lock.get('pid')}"))
        checks.append(self._row("single_session_lock", lock_ok, lock_detail, severity="warning" if not prod.single_active_browser_session else "blocker", evidence=lock))

        if mutation_expected and bool(prod.require_governance_ledger_integrity_for_mutation):
            memory_root = Path(cfg.reporting.memory_dir)
            if not memory_root.is_absolute():
                memory_root = (self.root / memory_root).resolve()
            memory_root = memory_root / str(cfg.brain.directory or "portal_brain")
            ledger = ChangeAuditLedger(memory_root / str(cfg.governance.ledger_filename or "change_audit_ledger.jsonl"))
            ledger_status = ledger.status(limit=10)
            checks.append(self._row(
                "governance_ledger_integrity", bool(ledger_status.get("chain_valid")),
                f"Governance ledger chain valid={bool(ledger_status.get('chain_valid'))}, events={int(ledger_status.get('event_count') or 0)}",
                evidence={k: v for k, v in ledger_status.items() if k != "recent_events"},
            ))

        mlflow = mlflow_runtime_probe(cfg.mlflow)
        checks.append(self._row("mlflow_async", bool(mlflow.get("package_available")) or not bool(cfg.mlflow.enabled) or bool(cfg.mlflow.fail_open), f"MLflow status={mlflow.get('status')}", severity="warning" if cfg.mlflow.fail_open else "blocker", evidence=mlflow))

        try:
            router = model_portfolio_from_config(cfg)
            probe = {}
            if bool(cfg.model_portfolio.enabled) and bool(cfg.aia.enabled) and bool(getattr(cfg.model_portfolio, "availability_probe_enabled", True)):
                try:
                    probe = router.probe_text_models(force=False)
                except Exception as probe_exc:
                    probe = {"status": "probe_error", "error": str(probe_exc)}
            portfolio = router.manifest()
            configured_count = len(portfolio.get("text_models") or [])
            available_count = len(portfolio.get("available_text_models") or [])
            portfolio_ok = (not cfg.model_portfolio.enabled) or configured_count > 0
            required_distinct = max(2, int(getattr(cfg.model_portfolio, "min_distinct_models_during_learning", 2) or 2))
            multimodel_ready = (not cfg.model_portfolio.enabled) or (not cfg.aia.enabled) or available_count >= required_distinct
            portfolio["availability_probe"] = probe
            portfolio["required_distinct_models_during_learning"] = required_distinct
        except Exception as exc:
            portfolio, portfolio_ok, multimodel_ready, configured_count, available_count, required_distinct = {"error": str(exc)}, False, False, 0, 0, 2
        checks.append(self._row("model_portfolio", portfolio_ok, f"On-Prem text models configured={configured_count}, available={available_count}", evidence=portfolio))
        checks.append(self._row(
            "multi_model_learning_readiness",
            multimodel_ready,
            f"Available Dell text models={available_count}; learning target>={required_distinct}",
            severity="warning",
            evidence={
                "configured": portfolio.get("text_models", []),
                "available": portfolio.get("available_text_models", []),
                "role_roster": portfolio.get("role_roster", {}),
                "recent_distinct_models": portfolio.get("recent_distinct_models", []),
            },
        ))

        try:
            replay = replay_policy_engine_from_config(cfg).manifest()
            replay_ok = True
        except Exception as exc:
            replay, replay_ok = {"error": str(exc)}, False
        checks.append(self._row("replay_policy", replay_ok, "Replay/Dreaming policy cache available" if replay_ok else "Replay policy unavailable", severity="warning", evidence=replay))

        if mutation_expected and bool(prod.require_live_runtime_certificate_for_mutation):
            cert = verify_latest_live_runtime_certificate(cfg, runs_root)
            checks.append(self._row("live_runtime_certificate", bool(cert.get("pass")), str(cert.get("reason") or cert.get("status") or "certificate"), evidence=cert))

        blockers = [x for x in checks if not x.get("pass") and x.get("severity") == "blocker"]
        warnings = [x for x in checks if not x.get("pass") and x.get("severity") != "blocker"]
        return {
            "schema_version": DOCTOR_SCHEMA, "pass": not blockers,
            "decision": "GO" if not blockers else "NO-GO",
            "task_hash": _sha_bytes(str(task).encode("utf-8"))[:16] if task else "",
            "checks": checks, "blocker_count": len(blockers), "warning_count": len(warnings),
            "created_at": utc_now(), "production_config": mask_sensitive_data(prod.model_dump()),
        }


class ProductionE2EOrchestrator:
    def __init__(self, *, root: Path, config: AppConfig, config_path: Path):
        self.root = Path(root)
        self.config = config
        self.config_path = Path(config_path)

    @staticmethod
    def from_path(config_path: str | Path, *, root: str | Path | None = None) -> "ProductionE2EOrchestrator":
        config_path = Path(config_path)
        root_path = Path(root) if root else (config_path.parent if config_path.parent != Path("") else Path.cwd())
        if not config_path.is_absolute():
            config_path = (root_path / config_path).resolve()
        return ProductionE2EOrchestrator(root=root_path.resolve(), config=load_config(config_path), config_path=config_path)

    def _runs_root(self, override: Optional[Path] = None) -> Path:
        root = Path(override or self.config.reporting.runs_dir)
        return root if root.is_absolute() else (self.root / root).resolve()

    def doctor(self, request: ProductionRunRequest, *, mutation_expected: bool = False) -> Dict[str, Any]:
        return ProductionDoctor(root=self.root, config=self.config).run(
            input_json=request.input_json, golden_dir=request.golden_dir, uploads_dir=request.uploads_dir,
            task=request.task, mutation_expected=mutation_expected, runs_dir=request.runs_dir,
        )

    def _request_manifest(self, request: ProductionRunRequest, *, run_id: str, plan: Mapping[str, Any]) -> Dict[str, Any]:
        input_hash = _file_sha(request.input_json) if request.input_json.is_file() else ""
        config_hash = _file_sha(self.config_path) if self.config_path.is_file() else ""
        golden = []
        if request.golden_dir and request.golden_dir.is_dir():
            for p in sorted(request.golden_dir.rglob("*")):
                if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    golden.append({"name": p.name, "relative_path": str(p.relative_to(request.golden_dir)), "sha256": _file_sha(p)})
        return mask_sensitive_data({
            "schema_version": "hip.production-request-manifest.v1",
            "run_id": run_id, "created_at": utc_now(), "app_version": __version__,
            "task_hash": _sha_bytes(request.task.encode("utf-8")),
            "input_json": {"path": str(request.input_json), "sha256": input_hash},
            "config": {"path": str(self.config_path), "sha256": config_hash},
            "golden_references": golden, "golden_reference_count": len(golden),
            "input_root": request.input_root, "deep_learn": request.deep_learn,
            "mutation_required": bool(plan.get("mutation_required")),
            "mutation_actions": list(plan.get("mutation_actions") or []),
            "plan_sha256": _sha_json(plan), "values_stored": False,
        })

    def _execution_integrity(self, request: ProductionRunRequest, *, manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> Dict[str, Any]:
        expected_input = str((manifest.get("input_json") or {}).get("sha256") or "")
        current_input = _file_sha(request.input_json) if request.input_json.is_file() else ""
        expected_config = str((manifest.get("config") or {}).get("sha256") or "")
        current_config = _file_sha(self.config_path) if self.config_path.is_file() else ""
        expected_golden = {
            str(row.get("relative_path") or row.get("name") or ""): str(row.get("sha256") or "")
            for row in (manifest.get("golden_references") or []) if isinstance(row, Mapping)
        }
        current_golden: Dict[str, str] = {}
        if request.golden_dir and request.golden_dir.is_dir():
            for path in sorted(request.golden_dir.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    current_golden[str(path.relative_to(request.golden_dir))] = _file_sha(path)
        expected_plan = str(manifest.get("plan_sha256") or "")
        current_plan = _sha_json(plan)
        checks = {
            "input_json_unchanged": expected_input == current_input,
            "config_unchanged": expected_config == current_config,
            "golden_references_unchanged": expected_golden == current_golden,
            "plan_unchanged": expected_plan == current_plan,
        }
        return {
            "schema_version": "hip.production-execution-integrity.v1",
            "pass": all(checks.values()),
            "created_at": utc_now(),
            "checks": checks,
            "expected": {"input_sha256": expected_input, "config_sha256": expected_config, "golden_references": expected_golden, "plan_sha256": expected_plan},
            "current": {"input_sha256": current_input, "config_sha256": current_config, "golden_references": current_golden, "plan_sha256": current_plan},
        }

    @staticmethod
    def _failure_diagnostics(exc: BaseException) -> Dict[str, Any]:
        frames = []
        for frame in traceback.extract_tb(exc.__traceback__):
            frames.append({
                "file": Path(frame.filename).name,
                "line": int(frame.lineno),
                "function": str(frame.name),
                "code": mask_sensitive_string(str(frame.line or ""))[:500],
            })
        return {
            "schema_version": "hip.production-failure-diagnostics.v1",
            "created_at": utc_now(),
            "exception_type": type(exc).__name__,
            "exception_module": type(exc).__module__,
            "error": mask_sensitive_string(str(exc))[:2000],
            "stack": frames[-40:],
            "locals_captured": False,
        }

    def _role_and_approval_gate(self, request: ProductionRunRequest, *, mutation_required: bool) -> Dict[str, Any]:
        if not mutation_required:
            return {"pass": True, "mutation_required": False}
        gov = self.config.governance
        prod = self.config.production_e2e
        role = (request.operator_role or os.getenv(str(gov.operator_role_env_var or "HIP_OPERATOR_ROLE"), "") or gov.default_operator_role or "viewer").strip().lower()
        allowed = {str(x).strip().lower() for x in gov.mutation_roles}
        role_pass = (role in allowed) if prod.require_operator_role_for_mutation else True
        approval = request.approval_id or os.getenv(str(gov.approval_id_env_var or "HIP_CHANGE_APPROVAL_ID"), "")
        approval_required = bool(gov.require_approval_id_for_mutation or prod.require_approval_id_for_mutation)
        approval_pass = (not approval_required) or bool(str(approval).strip())
        three_key = bool(request.allow_portal_mutation and os.getenv("HIP_ALLOW_PORTAL_MUTATION", "").strip().upper() == "YES" and request.confirmation.strip() == MUTATION_CONFIRMATION)
        return mask_sensitive_data({
            "pass": bool(role_pass and approval_pass and three_key), "mutation_required": True,
            "operator_role": role, "role_pass": role_pass, "allowed_roles": sorted(allowed),
            "approval_required": approval_required, "approval_present": bool(str(approval).strip()),
            "approval_id_hash": _sha_bytes(str(approval).encode("utf-8"))[:16] if approval else "",
            "three_key_mutation_gate": three_key, "confirmation_required": MUTATION_CONFIRMATION,
        })

    def _governance_ledger(self) -> ChangeAuditLedger:
        memory_root = Path(self.config.reporting.memory_dir)
        if not memory_root.is_absolute():
            memory_root = (self.root / memory_root).resolve()
        memory_root = memory_root / str(self.config.brain.directory or "portal_brain")
        return ChangeAuditLedger(memory_root / str(self.config.governance.ledger_filename or "change_audit_ledger.jsonl"))

    def _idempotency_key(self, request: ProductionRunRequest, plan: Mapping[str, Any]) -> str:
        return _sha_json({
            "task": request.task,
            "input_sha256": _file_sha(request.input_json) if request.input_json.is_file() else "",
            "input_root": request.input_root,
            "mutation_actions": sorted(str(x) for x in (plan.get("mutation_actions") or [])),
            "start_url_host_only": str(request.start_url).split("/", 3)[:3],
        })

    @staticmethod
    def _rollback_guidance(plan: Mapping[str, Any]) -> list[Dict[str, Any]]:
        rows: list[Dict[str, Any]] = []
        for action in plan.get("mutation_actions") or []:
            label = str(action or "").strip()
            norm = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
            if any(x in norm for x in ("save", "update", "edit", "configure")):
                guidance = "Use the captured pre-mutation evidence and run a separately approved Edit/Save task restoring the prior values; automatic rollback is intentionally disabled."
            elif "deploy" in norm or "publish" in norm:
                guidance = "Verify the deployed/runtime version. Reversal requires a separately approved prior-version or undeploy capability if the tenant exposes one."
            elif any(x in norm for x in ("create", "clone", "copy")):
                guidance = "Verify the new object's references. Removal, if required, must be a separately approved delete task; do not auto-delete on a partial failure."
            elif "delete" in norm or "remove" in norm:
                guidance = "Deletion is not automatically reversible. Restore from an approved prior export/configuration or recreate only after dependency review."
            elif "migrate" in norm:
                guidance = "Verify both source and destination state. Reverse migration is not assumed and requires a separately approved capability."
            else:
                guidance = "No automatic rollback. Use captured before/after evidence and a separately approved compensating task if reversal is needed."
            rows.append({"action": label, "automatic_rollback": False, "guidance": guidance})
        return rows

    @staticmethod
    def _write_summary_markdown(run_dir: Path, final: Mapping[str, Any]) -> Path:
        path = Path(run_dir) / "PRODUCTION_FINAL_SUMMARY.md"
        doctor = final.get("doctor") if isinstance(final.get("doctor"), Mapping) else {}
        governance = final.get("governance_gate") if isinstance(final.get("governance_gate"), Mapping) else {}
        journal = final.get("journal") if isinstance(final.get("journal"), Mapping) else {}
        lines = [
            "# HIP Production E2E Run", "",
            f"- Run ID: `{final.get('run_id','')}`",
            f"- Status: **{str(final.get('status','unknown')).upper()}**",
            f"- Pass: **{bool(final.get('pass'))}**",
            f"- Duration: {final.get('duration_seconds','—')} seconds",
            f"- Production doctor: {doctor.get('decision','—')} ({doctor.get('blocker_count',0)} blockers, {doctor.get('warning_count',0)} warnings)",
            f"- Mutation required: {governance.get('mutation_required', False)}",
            f"- Operator role: `{governance.get('operator_role','')}`",
            f"- Journal integrity: {journal.get('pass','—')} ({journal.get('event_count',0)} events)",
            "", "## Safety and recovery",
            "- Current `input.json` is the business-value authority.",
            "- Live portal evidence authorizes every browser action.",
            "- Automatic rollback of external portal mutations is disabled; use a separately approved compensating task.",
            "- Screenshots/raw network/DOM evidence are intentionally excluded from the safe review bundle.",
        ]
        rollback = final.get("rollback_guidance") if isinstance(final.get("rollback_guidance"), list) else []
        if rollback:
            lines += ["", "## Rollback guidance"]
            for row in rollback:
                lines.append(f"- **{row.get('action','mutation')}** — {row.get('guidance','')}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _safe_review_files(self, run_dir: Path) -> Iterable[Path]:
        preferred = {
            "production_request_manifest.json", "production_final_summary.json",
            "production_execution_journal.jsonl", "production_doctor.json",
            "production_governance_gate.json", "production_execution_integrity.json",
            "production_failure_diagnostics.json", "PRODUCTION_FINAL_SUMMARY.md",
        }
        legacy_extra = {
            "universal_portal_task_plan.json", "universal_portal_task_execution.json",
            "universal_mutation_gate.json", "recursive_self_improvement.json",
        }
        strict = bool(self.config.production_e2e.safe_review_strict_allowlist)
        for p in run_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(run_dir)
            if p.name == str(self.config.production_e2e.safe_review_bundle_name or "SAFE_REVIEW_BUNDLE.zip"):
                continue
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            if p.name in preferred:
                yield p
            elif (not strict) and (p.name in legacy_extra or str(rel).startswith("upload_summary/") or p.suffix.lower() == ".md"):
                yield p

    def _build_safe_review_bundle(self, run_dir: Path) -> str:
        name = str(self.config.production_e2e.safe_review_bundle_name or "SAFE_REVIEW_BUNDLE.zip")
        out = run_dir / name
        files = list(self._safe_review_files(run_dir))
        manifest_name = str(self.config.production_e2e.safe_review_manifest_filename or "production_safe_review_manifest.json")
        bundle_manifest = {
            "schema_version": "hip.production-safe-review-manifest.v1",
            "created_at": utc_now(),
            "strict_allowlist": bool(self.config.production_e2e.safe_review_strict_allowlist),
            "content_policy": "strict structural production artifacts; raw input.json and raw browser evidence are excluded",
            "screenshots_included": False,
            "files": [{"path": str(p.relative_to(run_dir)), "sha256": _file_sha(p), "size_bytes": p.stat().st_size} for p in files],
        }
        manifest_path = run_dir / manifest_name
        safe_write_json(manifest_path, bundle_manifest)
        files.append(manifest_path)
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in files:
                try:
                    zf.write(p, p.relative_to(run_dir))
                except Exception:
                    continue
        return str(out)

    async def execute(self, request: ProductionRunRequest) -> Dict[str, Any]:
        if not self.config.production_e2e.enabled:
            return {"schema_version": PRODUCTION_SCHEMA, "pass": False, "status": "disabled", "reason": "production_e2e.enabled is false"}

        runs_root = self._runs_root(request.runs_dir)
        runs_root.mkdir(parents=True, exist_ok=True)
        # Plan first so the doctor knows whether a valid live certificate is mandatory.
        graph_root = Path(self.config.reporting.memory_dir)
        if not graph_root.is_absolute():
            graph_root = (self.root / graph_root).resolve()
        graph = HIPCapabilityGraph(graph_root / str(self.config.brain.directory or "portal_brain"))
        planner = UniversalPortalTaskPlanner(self.config, graph)
        plan = planner.plan(
            request.task, input_json=str(request.input_json), input_root=request.input_root,
            start_url=request.start_url, deep_learn=request.deep_learn,
        )
        mutation_required = bool(plan.get("mutation_required"))
        doctor = self.doctor(request, mutation_expected=mutation_required)
        if not plan.get("pass"):
            doctor = {**doctor, "pass": False, "decision": "NO-GO", "blocker_count": int(doctor.get("blocker_count") or 0) + 1, "plan_blocker": mask_sensitive_data(plan)}

        run_id = f"HIP-PROD-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_sha_bytes(request.task.encode('utf-8'))[:6]}-{uuid.uuid4().hex[:6]}"
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        safe_write_json(run_dir / "production_doctor.json", doctor)
        safe_write_json(run_dir / "universal_portal_task_plan.json", plan)

        journal = HashChainedJournal(run_dir / str(self.config.production_e2e.journal_filename or "production_execution_journal.jsonl"))
        journal.append("run_created", {"run_id": run_id, "task_hash": _sha_bytes(request.task.encode("utf-8")), "mutation_required": mutation_required})

        manifest = self._request_manifest(request, run_id=run_id, plan=plan)
        safe_write_json(run_dir / str(self.config.production_e2e.request_manifest_filename or "production_request_manifest.json"), manifest)
        journal.append("request_manifested", {"manifest_sha256": _sha_json(manifest), "input_sha256": manifest.get("input_json", {}).get("sha256")})

        governance_gate = self._role_and_approval_gate(request, mutation_required=mutation_required)
        safe_write_json(run_dir / "production_governance_gate.json", governance_gate)
        if mutation_required and not governance_gate.get("pass"):
            doctor = {**doctor, "pass": False, "decision": "NO-GO", "governance_gate": governance_gate}
        if not doctor.get("pass"):
            journal.append("run_blocked", {"reason": "production_preflight_or_governance_failed", "doctor": doctor, "governance": governance_gate})
            final = {
                "schema_version": PRODUCTION_SCHEMA, "pass": False, "status": "blocked_preflight",
                "run_id": run_id, "run_dir": str(run_dir), "doctor": doctor,
                "governance_gate": governance_gate, "journal": journal.verify(),
            }
            safe_write_json(run_dir / str(self.config.production_e2e.final_summary_filename), final)
            if self.config.production_e2e.create_safe_review_bundle:
                final["safe_review_bundle"] = self._build_safe_review_bundle(run_dir)
                safe_write_json(run_dir / str(self.config.production_e2e.final_summary_filename), final)
            return final

        lock_path = runs_root / str(self.config.production_e2e.lock_filename)
        lease = ProductionExecutionLease(lock_path, stale_seconds=self.config.production_e2e.lock_stale_seconds, fail_on_stale=self.config.production_e2e.fail_on_stale_execution_lock)
        if self.config.production_e2e.single_active_browser_session:
            lease_result = lease.acquire(run_id=run_id, task_hash=manifest.get("task_hash", ""))
        else:
            lease_result = {"pass": True, "status": "disabled_by_configuration", "path": str(lock_path)}
        safe_write_json(run_dir / "production_execution_lease.json", lease_result)
        if not lease_result.get("pass"):
            journal.append("run_blocked", {"reason": "production_execution_lock_busy", "lease": lease_result})
            final = {"schema_version": PRODUCTION_SCHEMA, "pass": False, "status": "blocked_concurrent_session", "run_id": run_id, "run_dir": str(run_dir), "lease": lease_result, "journal": journal.verify()}
            safe_write_json(run_dir / str(self.config.production_e2e.final_summary_filename), final)
            return final

        integrity = self._execution_integrity(request, manifest=manifest, plan=plan)
        if self.config.production_e2e.write_execution_integrity_receipt:
            safe_write_json(run_dir / str(self.config.production_e2e.execution_integrity_filename), integrity)
        if self.config.production_e2e.require_execution_input_immutability and not integrity.get("pass"):
            lease.release()
            journal.append("run_blocked", {"reason": "execution_input_drift", "integrity": integrity})
            final = {"schema_version": PRODUCTION_SCHEMA, "pass": False, "status": "blocked_execution_input_drift", "run_id": run_id, "run_dir": str(run_dir), "integrity": integrity, "journal": journal.verify()}
            safe_write_json(run_dir / str(self.config.production_e2e.final_summary_filename), final)
            if self.config.production_e2e.create_safe_review_bundle:
                final["safe_review_bundle"] = self._build_safe_review_bundle(run_dir)
                safe_write_json(run_dir / str(self.config.production_e2e.final_summary_filename), final)
            return final

        ledger = self._governance_ledger()
        idempotency_key = self._idempotency_key(request, plan)
        if mutation_required:
            ledger_status = ledger.status(limit=10)
            if self.config.production_e2e.require_governance_ledger_integrity_for_mutation and not ledger_status.get("chain_valid"):
                lease.release()
                journal.append("run_blocked", {"reason": "governance_ledger_integrity_failed", "ledger": {k: v for k, v in ledger_status.items() if k != "recent_events"}})
                final = {"schema_version": PRODUCTION_SCHEMA, "pass": False, "status": "blocked_governance_ledger_integrity", "run_id": run_id, "run_dir": str(run_dir), "manual_review_required": True, "ledger": {k: v for k, v in ledger_status.items() if k != "recent_events"}, "journal": journal.verify()}
                safe_write_json(run_dir / str(self.config.production_e2e.final_summary_filename), final)
                return final
            prior = ledger.duplicate_success(idempotency_key, window_hours=int(self.config.governance.duplicate_window_hours))
            unresolved = ledger.unresolved_mutation(idempotency_key, window_hours=int(self.config.governance.duplicate_window_hours))
            if unresolved:
                lease.release()
                journal.append("run_blocked", {"reason": "unresolved_prior_mutation", "idempotency_key": idempotency_key})
                final = {"schema_version": PRODUCTION_SCHEMA, "pass": False, "status": "blocked_unresolved_prior_mutation", "run_id": run_id, "run_dir": str(run_dir), "manual_review_required": True, "journal": journal.verify()}
                safe_write_json(run_dir / str(self.config.production_e2e.final_summary_filename), final)
                return final
            if prior and not request.force_repeat_mutation:
                lease.release()
                journal.append("run_blocked", {"reason": "duplicate_mutation", "idempotency_key": idempotency_key})
                final = {"schema_version": PRODUCTION_SCHEMA, "pass": False, "status": "blocked_duplicate_mutation", "run_id": run_id, "run_dir": str(run_dir), "prior_event_hash": prior.get("event_hash"), "journal": journal.verify()}
                safe_write_json(run_dir / str(self.config.production_e2e.final_summary_filename), final)
                return final
            ledger.append("change_execution_started", {"idempotency_key": idempotency_key, "run_id": run_id, "task_hash": manifest.get("task_hash"), "source": "production_e2e"})

        started = time.monotonic()
        heartbeat_stop = asyncio.Event()
        heartbeat_task = None

        async def _lease_heartbeat_loop() -> None:
            interval = max(5, int(self.config.production_e2e.lock_heartbeat_seconds or 30))
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    lease.heartbeat()

        if lease.acquired:
            lease.heartbeat()
            heartbeat_task = asyncio.create_task(_lease_heartbeat_loop())
        try:
            journal.append("browser_execution_started", {"plan_sha256": manifest.get("plan_sha256"), "step_count": len(plan.get("steps") or []), "integrity_sha256": _sha_json(integrity)})
            native_recognition = NativeHIPPhaseMissionCoordinator.recognize(request.input_json)
            native_result: Dict[str, Any] = {}
            if bool(native_recognition.get("recognized")):
                journal.append("native_hip_phase_mission_started", {"selected_phases": native_recognition.get("selected_phases") or [], "deep_learn": bool(request.deep_learn)})
                native_result = await NativeHIPPhaseMissionCoordinator(self.config, graph).run(
                    parent_run_id=run_id, run_dir=run_dir / "native_hip_phase_mission",
                    input_json=request.input_json, deep_learn=bool(request.deep_learn),
                    selected_phases=native_recognition.get("selected_phases") or [],
                )
                safe_write_json(run_dir / "native_hip_phase_mission.json", native_result)
                journal.append("native_hip_phase_mission_finished", {"pass": bool(native_result.get("pass")), "status": native_result.get("status")})

            # For fill/verify/configuration missions without an authorized mutation,
            # the phase-native engine is authoritative. Unknown portal tasks and
            # explicit governed mutations continue through the universal executor.
            if native_result and not mutation_required:
                result = {
                    "schema_version": "hip.universal-portal-task-execution.v1",
                    "pass": bool(native_result.get("pass")),
                    "status": native_result.get("status"),
                    "execution_mode": "phase_native_authoritative",
                    "native_hip_phase_mission": native_result,
                    "plan": plan,
                }
            else:
                if native_result and mutation_required and not native_result.get("pass"):
                    raise RuntimeError("Phase-native qualification did not complete; refusing to attempt governed mutation on an incompletely filled configuration")
                result = await UniversalPortalTaskExecutor(self.config, graph).execute(
                    task=request.task, plan=plan, run_dir=run_dir,
                    allow_portal_mutation=request.allow_portal_mutation, confirmation=request.confirmation,
                )
                if native_result:
                    result["native_hip_phase_qualification"] = native_result
            elapsed = max(0.0, time.monotonic() - started)
            journal.append("browser_execution_finished", {"pass": bool(result.get("pass")), "status": result.get("status"), "duration_seconds": round(elapsed, 3)})
            if mutation_required:
                ledger.append("change_committed" if result.get("pass") else "change_failed", {
                    "idempotency_key": idempotency_key, "run_id": run_id,
                    "post_status": str(result.get("status") or ("verified" if result.get("pass") else "failed")),
                    "manual_review_required": bool(not result.get("pass")), "source": "production_e2e",
                })
            final = {
                "schema_version": PRODUCTION_SCHEMA, "pass": bool(result.get("pass")),
                "status": "complete" if result.get("pass") else "failed",
                "run_id": run_id, "run_dir": str(run_dir), "duration_seconds": round(elapsed, 3),
                "doctor": doctor, "governance_gate": governance_gate,
                "execution": mask_sensitive_data(result), "idempotency_key": idempotency_key if mutation_required else "",
            }
        except Exception as exc:
            elapsed = max(0.0, time.monotonic() - started)
            diagnostics = self._failure_diagnostics(exc)
            if self.config.production_e2e.write_failure_diagnostics:
                safe_write_json(run_dir / str(self.config.production_e2e.failure_diagnostics_filename), diagnostics)
            if mutation_required:
                ledger.append("change_failed", {"idempotency_key": idempotency_key, "run_id": run_id, "post_status": "ambiguous_or_partial_change_manual_review_required", "manual_review_required": True, "source": "production_e2e"})
            journal.append("browser_execution_exception", {"error": mask_sensitive_string(str(exc))[:2000], "duration_seconds": round(elapsed, 3)})
            final = {
                "schema_version": PRODUCTION_SCHEMA, "pass": False, "status": "execution_exception",
                "run_id": run_id, "run_dir": str(run_dir), "duration_seconds": round(elapsed, 3),
                "error": mask_sensitive_string(str(exc))[:2000], "manual_review_required": bool(mutation_required),
                "doctor": doctor, "governance_gate": governance_gate, "failure_diagnostics": diagnostics,
            }
        finally:
            heartbeat_stop.set()
            if heartbeat_task is not None:
                try:
                    await asyncio.wait_for(heartbeat_task, timeout=2.0)
                except Exception:
                    heartbeat_task.cancel()
            lease.release()

        if mutation_required:
            final["rollback_guidance"] = self._rollback_guidance(plan)
        journal.append("run_finalized", {"pass": bool(final.get("pass")), "status": final.get("status")})
        final["journal"] = journal.verify()
        final_summary_path = run_dir / str(self.config.production_e2e.final_summary_filename)
        safe_write_json(final_summary_path, final)
        final["markdown_summary"] = str(self._write_summary_markdown(run_dir, final))
        safe_write_json(final_summary_path, final)
        if self.config.production_e2e.create_safe_review_bundle:
            final["safe_review_bundle"] = self._build_safe_review_bundle(run_dir)
            safe_write_json(final_summary_path, final)
        return mask_sensitive_data(final)


def build_request(
    *, root: Path, task: str, config_path: str | Path, input_json: str | Path,
    input_root: str = "", start_url: str = "", runs_dir: str | Path | None = None,
    golden_dir: str | Path | None = None, uploads_dir: str | Path | None = None,
    deep_learn: bool = True, allow_portal_mutation: bool = False, confirmation: str = "",
    operator_role: str = "", approval_id: str = "", force_repeat_mutation: bool = False,
) -> ProductionRunRequest:
    return ProductionRunRequest(
        task=task, config_path=_safe_path(config_path, root=root), input_json=_safe_path(input_json, root=root),
        input_root=input_root, start_url=start_url,
        runs_dir=_safe_path(runs_dir, root=root) if runs_dir else None,
        golden_dir=_safe_path(golden_dir, root=root) if golden_dir else None,
        uploads_dir=_safe_path(uploads_dir, root=root) if uploads_dir else None,
        deep_learn=deep_learn, allow_portal_mutation=allow_portal_mutation, confirmation=confirmation,
        operator_role=operator_role, approval_id=approval_id, force_repeat_mutation=force_repeat_mutation,
    )


__all__ = [
    "ProductionExecutionLease", "HashChainedJournal", "ProductionDoctor",
    "ProductionRunRequest", "ProductionE2EOrchestrator", "build_request",
]
