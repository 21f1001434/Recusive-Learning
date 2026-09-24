from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .capability_graph import HIPCapabilityGraph, classify_risk
from .certified_future_task_agent import CertifiedHIPFutureTaskExecutor
from .config import AppConfig
from .future_task_agent import MUTATION_CONFIRMATION
from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


GOVERNANCE_PREVIEW_SCHEMA = "hip.change-governance-preview.v1"
GOVERNANCE_EXECUTION_SCHEMA = "hip.change-governance-execution.v1"
GOVERNANCE_RECEIPT_SCHEMA = "hip.change-receipt.v1"
GOVERNANCE_LEDGER_SCHEMA = "hip.change-audit-ledger-event.v1"


def _norm(value: Any) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _sha(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> Optional[datetime]:
    try:
        text = str(value or "").replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _highest_risk(operations: Sequence[Mapping[str, Any]]) -> str:
    order = {"read": 0, "draft": 1, "mutation": 2}
    risk = "read"
    for row in operations:
        candidate = str(row.get("risk") or "read")
        if order.get(candidate, 0) > order.get(risk, 0):
            risk = candidate
    return risk


def _rollback_guidance(action: str, family: str) -> Dict[str, Any]:
    action_n = _norm(action)
    common = {
        "automatic_rollback": False,
        "reason": "HIP browser mutations are treated as non-atomic external changes; automatic rollback is not attempted without a separately certified inverse capability.",
        "page_family": family,
        "action": action_n,
    }
    if action_n in {"delete", "remove"}:
        common["guidance"] = "No automatic rollback. Restore from an approved prior export/configuration or recreate the object only after validating references and dependencies."
    elif action_n in {"deploy", "publish", "enable", "disable"}:
        common["guidance"] = "Verify the resulting deployed/runtime version. If reversal is required, use a separately approved prior-version/undeploy capability only if the tenant exposes and the agent has certified it."
    elif action_n in {"migrate"}:
        common["guidance"] = "Verify both source and destination states. Reverse migration is not assumed; use a separately approved restore/migration path if the portal exposes one."
    elif action_n in {"create", "clone", "copy", "duplicate"}:
        common["guidance"] = "Verify the created object and its references. If removal is required, execute a separately approved Delete task; do not auto-delete on failure."
    elif action_n in {"save", "update", "edit", "modify", "configure"}:
        common["guidance"] = "Use captured pre-change evidence to restore the prior values through a separately approved Edit/Save task if rollback is required."
    else:
        common["guidance"] = "No rollback is required for read-only navigation."
    return common


@contextmanager
def _exclusive_ledger_lock(path: Path, *, timeout_seconds: float = 10.0, stale_seconds: float = 120.0):
    """Cross-process best-effort lock for append/hash-chain consistency.

    The lock file contains no business data. Stale locks are reclaimed after a
    bounded interval so a crashed process cannot permanently block governance.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    fd = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"pid={os.getpid()}\n".encode("utf-8"))
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > max(10.0, float(stale_seconds)):
                    lock_path.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Governance ledger is busy: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                pass


class ChangeAuditLedger:
    """Append-only, hash-chained audit ledger that stores structural metadata only."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _events(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        out: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    out.append(row)
            except Exception:
                continue
        return out

    def append(self, event_type: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        with _exclusive_ledger_lock(self.path):
            # Re-read only after acquiring the lock so two parallel agents cannot
            # derive the same previous_hash and fork the audit chain.
            events = self._events()
            previous_hash = str(events[-1].get("event_hash") or "") if events else "GENESIS"
            structural_payload = mask_sensitive_data(dict(payload))
            row: Dict[str, Any] = {
                "schema_version": GOVERNANCE_LEDGER_SCHEMA,
                "event_type": str(event_type),
                "timestamp": utc_now(),
                "previous_hash": previous_hash,
                **structural_payload,
            }
            row["event_hash"] = _sha({k: v for k, v in row.items() if k != "event_hash"})
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:
                    pass
            return row

    def duplicate_success(self, idempotency_key: str, *, window_hours: int) -> Optional[Dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(window_hours)))
        for row in reversed(self._events()):
            if row.get("event_type") != "change_committed":
                continue
            if str(row.get("idempotency_key") or "") != str(idempotency_key or ""):
                continue
            ts = _parse_utc(str(row.get("timestamp") or ""))
            if ts is None or ts >= cutoff:
                return row
        return None

    def unresolved_mutation(self, idempotency_key: str, *, window_hours: int) -> Optional[Dict[str, Any]]:
        """Return the latest unresolved execution hazard for this mutation.

        A process can die after dispatch but before writing ``change_failed`` or
        ``change_committed``. The persisted ``change_execution_started`` marker
        therefore quarantines an identical cross-run replay until a terminal
        result proves that no unknown change remains.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(window_hours)))
        key = str(idempotency_key or "")
        for row in reversed(self._events()):
            if str(row.get("idempotency_key") or "") != key:
                continue
            ts = _parse_utc(str(row.get("timestamp") or ""))
            if ts is not None and ts < cutoff:
                continue
            event_type = str(row.get("event_type") or "")
            if event_type == "change_committed":
                return None
            if event_type == "change_failed":
                if bool(row.get("manual_review_required")) or str(row.get("post_status") or "") == "ambiguous_or_partial_change_manual_review_required":
                    return row
                return None
            if event_type == "change_execution_started":
                return row
            # planned/blocked attempts did not execute and do not resolve an older
            # execution hazard; keep scanning until an execution terminal is found.
        return None

    def status(self, *, limit: int = 50) -> Dict[str, Any]:
        events: List[Dict[str, Any]] = []
        invalid_json_indices: List[int] = []
        if self.path.is_file():
            for idx, line in enumerate(self.path.read_text(encoding="utf-8", errors="ignore").splitlines()):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        invalid_json_indices.append(idx)
                    else:
                        events.append(row)
                except Exception:
                    invalid_json_indices.append(idx)
        chain_valid = not invalid_json_indices
        first_invalid_index = invalid_json_indices[0] if invalid_json_indices else None
        previous = "GENESIS"
        if chain_valid:
            for idx, row in enumerate(events):
                stored_hash = str(row.get("event_hash") or "")
                expected_previous = str(row.get("previous_hash") or "")
                computed_hash = _sha({k: v for k, v in row.items() if k != "event_hash"})
                if expected_previous != previous or stored_hash != computed_hash:
                    chain_valid = False
                    first_invalid_index = idx
                    break
                previous = stored_hash
        return {
            "schema_version": "hip.change-audit-ledger-status.v1",
            "path": str(self.path),
            "event_count": len(events),
            "raw_invalid_line_count": len(invalid_json_indices),
            "last_event_hash": str(events[-1].get("event_hash") or "") if events else "",
            "chain_valid": chain_valid,
            "first_invalid_index": first_invalid_index,
            "recent_events": mask_sensitive_data(events[-max(1, int(limit)):]),
        }


class HIPChangeGovernance:
    def __init__(self, config: AppConfig, graph: HIPCapabilityGraph):
        self.config = config
        self.graph = graph
        gov = getattr(config, "governance", None)
        ledger_name = str(getattr(gov, "ledger_filename", "change_audit_ledger.jsonl") or "change_audit_ledger.jsonl")
        memory_root = Path(config.reporting.memory_dir) / str(config.brain.directory or "portal_brain")
        self.ledger = ChangeAuditLedger(memory_root / ledger_name)

    def _operator_role(self, explicit: str = "") -> str:
        gov = self.config.governance
        if explicit:
            return _norm(explicit)
        env_name = str(gov.operator_role_env_var or "HIP_OPERATOR_ROLE")
        value = os.getenv(env_name, "")
        return _norm(value or gov.default_operator_role or "viewer")

    def _approval_id(self, explicit: str = "") -> str:
        gov = self.config.governance
        if explicit:
            return str(explicit).strip()
        return str(os.getenv(str(gov.approval_id_env_var or "HIP_CHANGE_APPROVAL_ID"), "") or "").strip()

    def _operations(self, plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for sub in plan.get("subtasks") or []:
            if not isinstance(sub, Mapping):
                continue
            family = str(sub.get("page_family") or "")
            for step in sub.get("steps") or []:
                if not isinstance(step, Mapping):
                    continue
                typ = str(step.get("type") or "")
                if typ == "navigate":
                    continue
                action = str(step.get("action") or typ)
                cid = str(step.get("capability_id") or "")
                cap = self.graph.data.get("capabilities", {}).get(cid, {}) if cid else {}
                risk = str(step.get("risk") or cap.get("risk") or classify_risk(action))
                api_contracts = []
                for aid in (cap.get("api_contract_ids") or []):
                    api = self.graph.data.get("api_contracts", {}).get(aid)
                    if isinstance(api, Mapping):
                        api_contracts.append({
                            "api_contract_id": aid,
                            "method": api.get("method"),
                            "endpoint": api.get("endpoint"),
                            "risk": api.get("risk"),
                            "observed_statuses": api.get("response_statuses") or [],
                        })
                rows.append(mask_sensitive_data({
                    "page_family": family,
                    "type": typ,
                    "action": action,
                    "risk": risk,
                    "capability_id": cid,
                    "capability_label": cap.get("label"),
                    "scope": step.get("scope") or (cap.get("scopes") or [""])[0],
                    "entity_present": bool(sub.get("entity")),
                    "observed_api_contracts": api_contracts,
                    "before": "live current state captured immediately before execution",
                    "after": "requested capability effect; verified from UI state and network evidence",
                }))
        return rows

    def _role_gate(self, role: str, risk: str) -> Dict[str, Any]:
        gov = self.config.governance
        role_n = _norm(role)
        allowed = {
            "read": {_norm(x) for x in gov.read_roles},
            "draft": {_norm(x) for x in gov.draft_roles},
            "mutation": {_norm(x) for x in gov.mutation_roles},
        }.get(risk, set())
        passed = role_n in allowed
        return {
            "pass": bool(passed),
            "operator_role": role_n,
            "required_risk_level": risk,
            "allowed_roles": sorted(allowed),
        }

    def preview(
        self,
        *,
        task: str,
        plan: Mapping[str, Any],
        operator_role: str = "",
        approval_id: str = "",
        force_repeat_mutation: bool = False,
    ) -> Dict[str, Any]:
        gov = self.config.governance
        operations = self._operations(plan)
        highest = _highest_risk(operations)
        mutation_required = highest == "mutation" or bool(plan.get("mutation_required"))
        role = self._operator_role(operator_role)
        approval = self._approval_id(approval_id)
        role_gate = self._role_gate(role, highest)

        family_cert_pass = True
        uncertified_families: List[str] = []
        if bool(gov.require_certified_family_for_mutation) and mutation_required:
            for sub in plan.get("subtasks") or []:
                if isinstance(sub, Mapping) and not bool(sub.get("certified_family_ready")):
                    family_cert_pass = False
                    uncertified_families.append(str(sub.get("page_family") or ""))

        idempotency_payload = {
            "task": str(task),
            "families": plan.get("families") or [],
            "operations": [
                {
                    "page_family": r.get("page_family"),
                    "action": _norm(r.get("action")),
                    "risk": r.get("risk"),
                    "capability_id": r.get("capability_id"),
                    "entity_present": r.get("entity_present"),
                }
                for r in operations
            ],
        }
        idempotency_key = _sha(idempotency_payload)
        duplicate = self.ledger.duplicate_success(idempotency_key, window_hours=int(gov.duplicate_window_hours)) if mutation_required else None
        unresolved = self.ledger.unresolved_mutation(idempotency_key, window_hours=int(gov.duplicate_window_hours)) if mutation_required else None
        duplicate_blocked = bool(duplicate) and not bool(force_repeat_mutation)
        unresolved_blocked = bool(unresolved) and bool(getattr(gov, "block_unresolved_mutation_replay", True))
        approval_pass = (not mutation_required) or (not bool(gov.require_approval_id_for_mutation)) or bool(approval)
        preview_pass = bool(plan.get("pass")) and bool(role_gate.get("pass")) and family_cert_pass and approval_pass and not duplicate_blocked and not unresolved_blocked
        change_id = f"chg-{idempotency_key[:12]}"

        rollback = [_rollback_guidance(str(r.get("action") or ""), str(r.get("page_family") or "")) for r in operations if r.get("risk") != "read"]
        return mask_sensitive_data({
            "schema_version": GOVERNANCE_PREVIEW_SCHEMA,
            "pass": bool(preview_pass),
            "change_id": change_id,
            "task_hash": _sha(str(task)),
            "plan_schema": plan.get("schema_version"),
            "execution_mode": plan.get("execution_mode"),
            "families": plan.get("families") or [],
            "operations": operations,
            "operation_count": len(operations),
            "risk": highest,
            "mutation_required": mutation_required,
            "operator_role": role,
            "role_gate": role_gate,
            "approval": {
                "required": bool(mutation_required and gov.require_approval_id_for_mutation),
                "present": bool(approval),
                "approval_id_hash": _sha(approval)[:16] if approval else "",
            },
            "certification_gate": {
                "pass": family_cert_pass,
                "required_for_mutation": bool(gov.require_certified_family_for_mutation),
                "uncertified_families": uncertified_families,
            },
            "idempotency": {
                "idempotency_key": idempotency_key,
                "duplicate_window_hours": int(gov.duplicate_window_hours),
                "prior_success_found": bool(duplicate),
                "force_repeat_mutation": bool(force_repeat_mutation),
                "pass": not duplicate_blocked and not unresolved_blocked,
                "prior_success_event_hash": str((duplicate or {}).get("event_hash") or ""),
                "unresolved_prior_execution": bool(unresolved),
                "unresolved_event_hash": str((unresolved or {}).get("event_hash") or ""),
                "unresolved_replay_blocked": bool(unresolved_blocked),
            },
            "required_mutation_confirmation": MUTATION_CONFIRMATION if mutation_required else "",
            "rollback_guidance": rollback,
            "policy": {
                "governance_enabled": bool(gov.enabled),
                "require_preview": bool(gov.require_preview_for_mutation),
                "require_post_change_api_success": bool(gov.require_post_change_api_success),
                "require_post_change_mcp_assurance": bool(gov.require_post_change_mcp_assurance),
                "mutation_auto_retry": False,
                "automatic_rollback": False,
            },
            "blocking_reasons": [
                msg for ok, msg in [
                    (bool(plan.get("pass")), "Certified task plan is not executable."),
                    (bool(role_gate.get("pass")), f"Operator role '{role}' is not authorized for {highest} operations."),
                    (family_cert_pass, "One or more mutation families are not certified ready."),
                    (approval_pass, "A change approval ID is required for mutation."),
                    (not duplicate_blocked, "An identical successful mutation exists inside the duplicate-protection window."),
                    (not unresolved_blocked, "An identical prior mutation execution has an unresolved/indeterminate outcome; cross-run replay is quarantined."),
                ] if not ok
            ],
            "values_stored": False,
        })

    def post_change_verdict(self, *, preview: Mapping[str, Any], execution: Mapping[str, Any]) -> Dict[str, Any]:
        gov = self.config.governance
        mutation_required = bool(preview.get("mutation_required"))
        mutation_steps = [s for s in execution.get("steps") or [] if isinstance(s, Mapping) and str(s.get("risk") or "") == "mutation"]
        successful_write_responses: List[Dict[str, Any]] = []
        observed_write_responses: List[Dict[str, Any]] = []
        for step in mutation_steps:
            for tx in step.get("network_transactions") or []:
                if not isinstance(tx, Mapping):
                    continue
                method = str(tx.get("method") or "").upper()
                if method not in {"POST", "PUT", "PATCH", "DELETE"}:
                    continue
                status = tx.get("status")
                if status is not None:
                    observed_write_responses.append(dict(tx))
                    try:
                        if 200 <= int(status) < 300:
                            successful_write_responses.append(dict(tx))
                    except Exception:
                        pass
        mcp_pass = all(bool((s.get("mcp_assurance") or {}).get("pass")) for s in execution.get("subtasks") or [] if isinstance(s, Mapping))
        api_pass = (not mutation_required) or bool(successful_write_responses) or not bool(gov.require_post_change_api_success)
        mcp_required_pass = (not bool(gov.require_post_change_mcp_assurance)) or mcp_pass
        execution_pass = bool(execution.get("pass"))
        passed = execution_pass and api_pass and mcp_required_pass
        reconciliation_rows = [
            dict(step.get("mutation_reconciliation") or {})
            for step in mutation_steps
            if isinstance(step.get("mutation_reconciliation"), Mapping)
        ]
        reconciliation_classes = [str(row.get("classification") or "") for row in reconciliation_rows]
        any_mutation_attempted = any(
            bool(step.get("mutation_dispatched"))
            or any(
                isinstance(tx, Mapping) and str(tx.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}
                for tx in step.get("network_transactions") or []
            )
            for step in mutation_steps
        )
        rejected_verified = bool(reconciliation_classes) and all(
            cls in {"rejected_verified", "not_dispatched"} for cls in reconciliation_classes if cls
        ) and "rejected_verified" in reconciliation_classes
        if passed:
            status = "committed_verified" if mutation_required else "completed_verified"
        elif rejected_verified:
            status = "mutation_rejected_verified"
        elif any_mutation_attempted:
            status = "ambiguous_or_partial_change_manual_review_required"
        else:
            status = "failed_before_mutation"
        manual_review_required = bool(any_mutation_attempted and not passed and not rejected_verified)
        return mask_sensitive_data({
            "schema_version": "hip.change-post-verification.v1",
            "pass": bool(passed),
            "status": status,
            "execution_pass": execution_pass,
            "mutation_required": mutation_required,
            "mutation_step_count": len(mutation_steps),
            "write_response_count": len(observed_write_responses),
            "successful_2xx_write_response_count": len(successful_write_responses),
            "api_success_gate": bool(api_pass),
            "fresh_mcp_assurance_gate": bool(mcp_required_pass),
            "manual_review_required": bool(any_mutation_attempted and not passed),
            "mutation_dispatched": bool(any_mutation_attempted),
            "mutation_outcome_classes": reconciliation_classes,
            "automatic_retry_allowed": False if mutation_required else True,
            "automatic_rollback_attempted": False,
            "rollback_guidance": preview.get("rollback_guidance") or [],
        })


class GovernedCertifiedTaskExecutor:
    def __init__(self, config: AppConfig, graph: HIPCapabilityGraph):
        self.config = config
        self.graph = graph
        self.governance = HIPChangeGovernance(config, graph)
        self.executor = CertifiedHIPFutureTaskExecutor(config, graph)

    async def execute(
        self,
        *,
        task: str,
        plan: Mapping[str, Any],
        run_dir: str | Path,
        allow_portal_mutation: bool = False,
        confirmation: str = "",
        allow_adaptive_exploration: bool = True,
        operator_role: str = "",
        approval_id: str = "",
        force_repeat_mutation: bool = False,
    ) -> Dict[str, Any]:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        preview = self.governance.preview(
            task=task,
            plan=plan,
            operator_role=operator_role,
            approval_id=approval_id,
            force_repeat_mutation=force_repeat_mutation,
        )
        safe_write_json(run_dir / "change_preview.json", preview)
        audit_common = {
            "change_id": preview.get("change_id"),
            "task_hash": preview.get("task_hash"),
            "idempotency_key": (preview.get("idempotency") or {}).get("idempotency_key"),
            "families": preview.get("families") or [],
            "risk": preview.get("risk"),
            "mutation_required": preview.get("mutation_required"),
            "operator_role": preview.get("operator_role"),
            "approval_present": bool((preview.get("approval") or {}).get("present")),
            "approval_id_hash": (preview.get("approval") or {}).get("approval_id_hash"),
        }
        planned_event = self.governance.ledger.append("change_planned", audit_common)
        if not preview.get("pass"):
            blocked = {
                "schema_version": GOVERNANCE_EXECUTION_SCHEMA,
                "pass": False,
                "status": "blocked_by_governance",
                "preview": preview,
                "audit_event_hash": planned_event.get("event_hash"),
                "values_stored": False,
            }
            safe_write_json(run_dir / "governed_change_execution.json", blocked)
            self.governance.ledger.append("change_blocked", {**audit_common, "blocking_reasons": preview.get("blocking_reasons") or []})
            return mask_sensitive_data(blocked)

        execution_started_event = self.governance.ledger.append("change_execution_started", {
            **audit_common,
            "manual_review_required": False,
            "values_stored": False,
        })
        execution = await self.executor.execute(
            task=task,
            plan=plan,
            run_dir=run_dir,
            allow_portal_mutation=allow_portal_mutation,
            confirmation=confirmation,
            allow_adaptive_exploration=allow_adaptive_exploration,
        )
        post = self.governance.post_change_verdict(preview=preview, execution=execution)
        event_type = "change_committed" if post.get("pass") else "change_failed"
        final_event = self.governance.ledger.append(event_type, {
            **audit_common,
            "post_status": post.get("status"),
            "verified": bool(post.get("pass")),
            "successful_2xx_write_response_count": post.get("successful_2xx_write_response_count"),
            "manual_review_required": post.get("manual_review_required"),
            "mutation_dispatched": post.get("mutation_dispatched"),
            "mutation_outcome_classes": post.get("mutation_outcome_classes") or [],
            "execution_started_event_hash": execution_started_event.get("event_hash"),
        })
        receipt = mask_sensitive_data({
            "schema_version": GOVERNANCE_RECEIPT_SCHEMA,
            "change_id": preview.get("change_id"),
            "task_hash": preview.get("task_hash"),
            "idempotency_key": (preview.get("idempotency") or {}).get("idempotency_key"),
            "risk": preview.get("risk"),
            "mutation_required": preview.get("mutation_required"),
            "operator_role": preview.get("operator_role"),
            "approval": preview.get("approval"),
            "execution_pass": execution.get("pass"),
            "post_verification": post,
            "audit_event_hash": final_event.get("event_hash"),
            "previous_audit_event_hash": final_event.get("previous_hash"),
            "rollback_guidance": preview.get("rollback_guidance") or [],
            "created_at": utc_now(),
            "values_stored": False,
        })
        safe_write_json(run_dir / "change_receipt.json", receipt)
        result = mask_sensitive_data({
            "schema_version": GOVERNANCE_EXECUTION_SCHEMA,
            "pass": bool(post.get("pass")),
            "status": post.get("status"),
            "preview": preview,
            "certified_execution": execution,
            "post_verification": post,
            "change_receipt": receipt,
            "values_stored": False,
        })
        safe_write_json(run_dir / "governed_change_execution.json", result)
        return result
