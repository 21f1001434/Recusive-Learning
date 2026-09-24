"""Live, human-readable mission step trace for HIP configuration runs.

This ledger is deliberately separate from planner reasoning.  It records only
observable execution facts: phase/attempt status, semantic controls observed,
values filled (with secret masking), clicks performed, and deterministic/judge
verification outcomes.  Business reviewers can therefore see where a mission is
without reading model chain-of-thought or raw DOM/network dumps.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .safe_io import safe_write_json
from .security import is_secret_target, mask_sensitive_data, mask_sensitive_string

TRACE_FILENAME = "mission_trace.json"

STEP_DEFINITIONS: Dict[str, tuple[str, str]] = {
    "data_map": ("P01-DM", "Data Map"),
    "source_document_type": ("P02-SDT", "Source Document Type"),
    "target_document_type": ("P03-TDT", "Target Document Type"),
    "rule": ("P04-RULE", "Rule"),
    "source_transport_profile": ("P05-STP", "Source Transport Profile"),
    "target_transport_profile": ("P06-TTP", "Target Transport Profile"),
    "biz_flow": ("P07-BF", "BizFlow"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _append_unique(rows: list[dict[str, Any]], row: Mapping[str, Any], *, key: tuple[str, ...], limit: int = 80) -> None:
    clean = mask_sensitive_data(dict(row))
    signature = tuple(str(clean.get(k) or "") for k in key)
    for existing in rows:
        if tuple(str(existing.get(k) or "") for k in key) == signature:
            existing.update(clean)
            return
    rows.append(clean)
    if len(rows) > limit:
        del rows[:-limit]


def _safe_fill_value(target: str, value: Any, *, was_secret: bool = False) -> str:
    if was_secret or is_secret_target(target):
        return "***MASKED***"
    return mask_sensitive_string(str(value if value is not None else ""))[:500]


class MissionTraceLedger:
    SCHEMA = "hip.mission-trace.v1"

    def __init__(self, root_dir: str | Path, *, run_id: str, phases: Sequence[str]) -> None:
        self.root_dir = Path(root_dir)
        self.path = self.root_dir / TRACE_FILENAME
        self.run_id = str(run_id)
        self.phases = [str(p) for p in phases]
        self.active_phase = ""
        existing = _read_json(self.path, {})
        if isinstance(existing, dict) and existing.get("schema_version") == self.SCHEMA:
            self.state = existing
        else:
            steps = []
            for order, phase in enumerate(self.phases, start=1):
                sid, label = STEP_DEFINITIONS.get(phase, (f"P{order:02d}", phase.replace("_", " ").title()))
                steps.append({
                    "step_id": sid,
                    "order": order,
                    "phase": phase,
                    "label": label,
                    "status": "queued",
                    "attempt": 0,
                    "current_activity": "Waiting",
                    "observed": [],
                    "observed_controls": [],
                    "filled": [],
                    "clicked": [],
                    "verification": {},
                    "judge": {},
                    "warnings": [],
                    "blocker": "",
                    "started_at": "",
                    "completed_at": "",
                    "updated_at": _utc_now(),
                })
            self.state: Dict[str, Any] = {
                "schema_version": self.SCHEMA,
                "run_id": self.run_id,
                "status": "in_progress",
                "current_step_id": "",
                "completed_step_ids": [],
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "steps": steps,
                "transitions": [],
                "evidence_policy": {
                    "model_reasoning_stored": False,
                    "raw_dom_stored_here": False,
                    "secret_values_masked": True,
                    "purpose": "show what the browser observed, filled, clicked, and verified",
                },
            }
            self._persist()

    def _persist(self) -> None:
        self.state["updated_at"] = _utc_now()
        safe_write_json(self.path, self.state, mask=False)

    def _step(self, phase: str) -> Dict[str, Any]:
        for step in self.state.get("steps") or []:
            if str(step.get("phase")) == str(phase):
                return step
        raise KeyError(f"mission trace phase not registered: {phase}")

    def mark_phase(self, phase: str, *, status: str, attempt: int = 0, activity: str = "", blocker: str = "") -> None:
        step = self._step(phase)
        self.active_phase = str(phase) if status in {"running", "in_progress"} else self.active_phase
        step["status"] = str(status)
        step["attempt"] = max(int(step.get("attempt") or 0), int(attempt or 0))
        if status in {"running", "in_progress"}:
            if not step.get("started_at"):
                step["started_at"] = _utc_now()
            self.state["current_step_id"] = step.get("step_id") or ""
        if status in {"completed", "resumed"}:
            step["completed_at"] = _utc_now()
            completed = list(self.state.get("completed_step_ids") or [])
            if step.get("step_id") not in completed:
                completed.append(step.get("step_id"))
            self.state["completed_step_ids"] = completed
            if self.active_phase == phase:
                self.active_phase = ""
        if status == "blocked":
            self.state["status"] = "blocked"
            if self.active_phase == phase:
                self.active_phase = ""
        if blocker:
            step["blocker"] = mask_sensitive_string(str(blocker))[:1600]
        if activity:
            step["current_activity"] = mask_sensitive_string(str(activity))[:500]
        step["updated_at"] = _utc_now()
        self._persist()

    def record_action(self, event: Any, *, phase: str = "") -> None:
        phase_name = str(phase or self.active_phase or "")
        if not phase_name or phase_name not in self.phases:
            return
        step = self._step(phase_name)
        raw = asdict(event) if is_dataclass(event) else dict(event) if isinstance(event, Mapping) else {}
        typ = str(raw.get("type") or "")
        target = mask_sensitive_string(str(raw.get("target") or ""))[:500]
        execution = raw.get("execution_provenance") if isinstance(raw.get("execution_provenance"), Mapping) else {}
        common = {
            "action_id": str(raw.get("action_id") or ""),
            "type": typ,
            "target": target,
            "success": bool(raw.get("success", True)),
            "stage": mask_sensitive_string(str(raw.get("stage") or ""))[:240],
            "backend": str(raw.get("backend") or ""),
            "page_url": mask_sensitive_string(str(raw.get("page_url_after") or raw.get("page_url_before") or ""))[:700],
            "execution": mask_sensitive_data({
                "planner": execution.get("planner") or "",
                "planner_status": execution.get("planner_status") or "",
                "planner_aligned": execution.get("planner_aligned"),
                "primary_executor": execution.get("primary_executor") or "",
                "actual_executor": execution.get("actual_executor") or "",
                "playwright_mcp_available": bool(execution.get("playwright_mcp_available")),
                "playwright_mcp_attempted": bool(execution.get("playwright_mcp_attempted")),
                "playwright_mcp_succeeded": bool(execution.get("playwright_mcp_succeeded")),
                "fallback_reason": mask_sensitive_string(str(execution.get("fallback_reason") or ""))[:700],
                "semantic_control_id": execution.get("semantic_control_id") or "",
                "semantic_confidence": execution.get("semantic_confidence"),
                "semantic_margin": execution.get("semantic_margin"),
                "semantic_gate_status": execution.get("semantic_gate_status") or "",
                "semantic_reobserved": bool(execution.get("semantic_reobserved")),
                "semantic_revalidation": execution.get("semantic_revalidation") or "",
                "semantic_effect_pass": execution.get("semantic_effect_pass"),
                "semantic_effect_type": execution.get("semantic_effect_type") or "",
                "semantic_effect_confidence": execution.get("semantic_effect_confidence"),
            }),
        }
        if typ == "fill":
            _append_unique(step["filled"], {
                **common,
                "value": _safe_fill_value(target, raw.get("value_redacted"), was_secret=bool(raw.get("was_secret"))),
            }, key=("action_id",), limit=100)
            if target:
                _append_unique(step["observed_controls"], {"control": target, "source": "browser_action"}, key=("control",), limit=120)
        elif typ in {"click", "press", "search"}:
            _append_unique(step["clicked"], common, key=("action_id",), limit=100)
        elif typ in {"navigate", "extract", "verify", "screenshot"}:
            _append_unique(step["observed"], common, key=("action_id",), limit=100)
        # Every action still updates the human-readable current activity; this is
        # useful when a long-running dropdown/row phase is otherwise quiet.
        if target:
            step["current_activity"] = f"{typ or 'action'}: {target}"[:500]
        step["updated_at"] = _utc_now()
        self._persist()

    def record_observation(self, phase: str, *, summary: str, source: str = "runtime", details: Optional[Mapping[str, Any]] = None) -> None:
        step = self._step(phase)
        row = {
            "summary": mask_sensitive_string(str(summary))[:900],
            "source": mask_sensitive_string(str(source))[:240],
            "details": mask_sensitive_data(dict(details or {})),
            "timestamp": _utc_now(),
        }
        _append_unique(step["observed"], row, key=("summary", "source"), limit=120)
        step["current_activity"] = row["summary"][:500]
        step["updated_at"] = _utc_now()
        self._persist()

    def record_transition(self, from_phase: str, to_phase: str, *, status: str, details: Optional[Mapping[str, Any]] = None) -> None:
        """Record an explicit phase-to-phase handoff without planner reasoning."""
        from_step = self._step(from_phase)
        to_step = self._step(to_phase)
        row = {
            "from_phase": str(from_phase),
            "from_step_id": from_step.get("step_id") or "",
            "to_phase": str(to_phase),
            "to_step_id": to_step.get("step_id") or "",
            "status": str(status),
            "details": mask_sensitive_data(dict(details or {})),
            "timestamp": _utc_now(),
        }
        rows = self.state.setdefault("transitions", [])
        _append_unique(rows, row, key=("from_step_id", "to_step_id", "status"), limit=40)
        from_step["handoff"] = mask_sensitive_data({
            "to_step_id": row["to_step_id"],
            "status": row["status"],
            "details": row["details"],
            "timestamp": row["timestamp"],
        })
        if status == "starting":
            from_step["current_activity"] = f"Handing off to {row['to_step_id']}"
        elif status == "complete":
            from_step["current_activity"] = f"Handoff to {row['to_step_id']} verified"
            if to_step.get("status") == "queued":
                to_step["current_activity"] = f"Route handoff from {row['from_step_id']} verified; waiting to execute"
        elif status == "continued_from_blocked":
            from_step["current_activity"] = f"Route continued to {row['to_step_id']} for diagnostics; phase still blocked"
            if to_step.get("status") == "queued":
                to_step["current_activity"] = f"Route reached from blocked {row['from_step_id']}; executing independently"
        elif status == "blocked":
            from_step["current_activity"] = f"Handoff to {row['to_step_id']} blocked"
            clean = mask_sensitive_string(str((details or {}).get("code") or "HIP_PHASE_HANDOFF_FAILED"))[:500]
            if clean and clean not in from_step["warnings"]:
                from_step["warnings"].append(clean)
        from_step["updated_at"] = _utc_now()
        to_step["updated_at"] = _utc_now()
        self._persist()

    def record_warning(self, phase: str, message: str) -> None:
        step = self._step(phase)
        clean = mask_sensitive_string(str(message))[:1200]
        if clean and clean not in step["warnings"]:
            step["warnings"].append(clean)
            step["warnings"] = step["warnings"][-40:]
        step["updated_at"] = _utc_now()
        self._persist()

    def set_runtime_contract(self, *, autowebglm_primary: bool, playwright_mcp_required: bool, playwright_mcp_available: bool = False, live_witness_mode: bool = False, semantic_understanding_enabled: bool = True, autonomous_all_form_phases: bool = True) -> None:
        self.state["runtime_contract"] = {
            "planner": "AutoWebGLM" if autowebglm_primary else "deterministic",
            "primary_safe_action_executor": "PyAutoGUI MCP",
            "deterministic_browser_fallback": "Playwright MCP" if playwright_mcp_required else "Python Playwright",
            "playwright_mcp_required": bool(playwright_mcp_required),
            "playwright_mcp_available": bool(playwright_mcp_available),
            "semantic_understanding_enabled": bool(semantic_understanding_enabled),
            "autonomous_all_form_phases": bool(autonomous_all_form_phases),
            "autonomous_goal_loop": "observe -> understand -> plan -> act -> verify -> learn/rebind -> continue" if autonomous_all_form_phases else "phase-specific",
            "semantic_brain": "HIP Intelligence MCP + Playwright accessibility + Chrome DevTools DOM + Gemma ambiguity confirmation" if semantic_understanding_enabled else "disabled",
            "semantic_gate_policy": "fail-closed unique semantic target -> rerender revalidation -> exact post-action effect proof" if semantic_understanding_enabled else "disabled",
            "live_witness_mode": bool(live_witness_mode),
            "mutation_controls_allowed": not bool(live_witness_mode),
            "policy": (
                "LIVE WITNESS: AutoWebGLM proves vetted intent; PyAutoGUI MCP performs permitted physical interactions on the same authenticated browser, Playwright MCP verifies/falls back; Create/Save/Submit/Finish/Deploy/Delete and mutating API requests are prohibited."
                if live_witness_mode else
                "AutoWebGLM plans vetted intent; PyAutoGUI MCP physically clicks/types/presses after semantic proof; Playwright MCP is deterministic fallback/verification and Python Playwright is final compatibility fallback."
            ),
        }
        self._persist()

    def refresh_phase_artifacts(
        self,
        phase: str,
        phase_dir: str | Path,
        *,
        verification: Optional[Mapping[str, Any]] = None,
        judge: Optional[Mapping[str, Any]] = None,
    ) -> None:
        step = self._step(phase)
        phase_path = Path(phase_dir)

        # v2.2.4: surface the bounded ReAct form-entry transaction in the live
        # mission card.  This makes the mandatory listing -> + Add -> form (and
        # BizFlow template-card hop) visible instead of leaving the operator with
        # only a later section-judge BLOCK badge.
        for entry_path in sorted(phase_path.glob("**/phase_form_entry_react.json")):
            entry = _read_json(entry_path, {})
            if not isinstance(entry, dict):
                continue
            for row in entry.get("steps") or []:
                if not isinstance(row, dict):
                    continue
                obs = row.get("observe") if isinstance(row.get("observe"), dict) else {}
                _append_unique(step["observed"], {
                    "source": "phase_form_entry_react",
                    "summary": f"form-entry observe: form_open={bool(obs.get('form_open'))}, intermediate={bool(obs.get('intermediate_surface'))}",
                    "url": mask_sensitive_string(str(obs.get("url") or ""))[:500],
                    "dom_generation": obs.get("dom_generation"),
                }, key=("summary", "url", "dom_generation"), limit=120)
                action = row.get("action") if isinstance(row.get("action"), dict) else {}
                if action:
                    _append_unique(step["clicked"], {
                        "action_id": f"form-entry:{entry_path.name}:{row.get('step')}",
                        "type": str(action.get("kind") or "structural_entry"),
                        "target": "+ Add" if action.get("kind") == "click_add" else "BizFlow template/card launcher",
                        "success": bool(action.get("clicked", True) and not action.get("error")),
                        "stage": "phase_form_entry_react",
                        "backend": "AutoWebGLM -> PyAutoGUI MCP / Playwright MCP hybrid broker",
                    }, key=("action_id",), limit=120)
                after_add = row.get("after_add") if isinstance(row.get("after_add"), dict) else {}
                if after_add:
                    _append_unique(step["clicked"], {
                        "action_id": f"form-entry-template:{entry_path.name}:{row.get('step')}",
                        "type": "structural_entry",
                        "target": mask_sensitive_string(str(after_add.get("label") or after_add.get("method") or "BizFlow card link"))[:500],
                        "success": bool(after_add.get("clicked", True) and not after_add.get("error")),
                        "stage": "phase_form_entry_react",
                        "backend": "AutoWebGLM -> PyAutoGUI MCP / Playwright MCP hybrid broker",
                    }, key=("action_id",), limit=120)
            if entry.get("pass") is False:
                step["blocker"] = mask_sensitive_string(str(entry.get("reason") or entry.get("error_code") or "HIP_FORM_ENTRY_NOT_OPENED"))[:1600]

        # Form drivers all publish a *_dummy_fill_plan.json.  This is a safer and
        # more complete control-level source than raw DOM text, including DDS
        # controls that may not emit a normal fill ActionEvent.
        for plan_path in sorted(phase_path.glob("**/*_dummy_fill_plan.json")):
            plan = _read_json(plan_path, {})
            attempts = plan.get("attempts") if isinstance(plan, dict) else []
            for attempt in attempts or []:
                if not isinstance(attempt, dict) or attempt.get("planner_only"):
                    continue
                field = str(attempt.get("field") or "")
                label = str(attempt.get("label") or "")
                control = mask_sensitive_string(label or field)[:500]
                if control:
                    _append_unique(step["observed_controls"], {
                        "control": control,
                        "field": mask_sensitive_string(field)[:300],
                        "source": plan_path.name,
                    }, key=("control", "field"), limit=160)
                if attempt.get("filled") is True:
                    target = " ".join(x for x in (field, label) if x).strip()
                    value = attempt.get("value_redacted", attempt.get("value", ""))
                    _append_unique(step["filled"], {
                        "action_id": f"plan:{plan_path.name}:{field or label}",
                        "type": "fill",
                        "target": control,
                        "field": mask_sensitive_string(field)[:300],
                        "value": _safe_fill_value(target, value),
                        "success": True,
                        "stage": str(attempt.get("execution_stage") or "deterministic_form_fill"),
                        "backend": "deterministic_driver",
                    }, key=("field", "target", "value"), limit=160)

        # v2.2.5: expose the authoritative execution-stage contract directly in
        # the mission trace.  This distinguishes "form opened" from "controls
        # discovered", "controls bound", "fields filled/verified" and final exact
        # execution.  Operators no longer have to infer the earliest failed stage
        # from a later section-judge BLOCK.
        execution_candidates = list(phase_path.glob("**/*target_branch_execution.json"))
        execution_candidates.extend(phase_path.glob("**/bizflow_tab_form_kb.json"))
        for execution_path in sorted(set(execution_candidates)):
            payload = _read_json(execution_path, {})
            if not isinstance(payload, dict):
                continue
            execution = payload
            if isinstance(payload.get("stateful_target_branch_execution"), dict):
                execution = payload.get("stateful_target_branch_execution") or {}
            elif isinstance(payload.get("bizflow_tab_form"), dict):
                execution = (payload.get("bizflow_tab_form") or {}).get("stateful_target_branch_execution") or {}
            stage_audit = execution.get("execution_stage_audit") if isinstance(execution, dict) else {}
            if not isinstance(stage_audit, dict) or not stage_audit:
                continue
            stage_labels = [
                ("form_opened", "form opened"),
                ("controls_discovered", "controls discovered"),
                ("controls_bound", "controls bound"),
                ("fields_filled_or_verified", "fields filled/verified"),
                ("exact_execution_verified", "exact execution verified"),
            ]
            for key_name, label in stage_labels:
                if key_name not in stage_audit:
                    continue
                _append_unique(step["observed"], {
                    "source": "live_execution_stage_audit",
                    "summary": f"{label}: {'PASS' if stage_audit.get(key_name) else 'BLOCK'}",
                    "stage": key_name,
                    "artifact": execution_path.name,
                }, key=("source", "stage", "artifact"), limit=160)
            if stage_audit.get("exact_execution_verified") is False:
                failed_stage = next((label for key_name, label in stage_labels if stage_audit.get(key_name) is False), "exact execution")
                step["blocker"] = mask_sensitive_string(f"Execution blocked before judge: {failed_stage}")[:1600]

        lock = _read_json(phase_path / "phase_exact_state_lock.json", {})
        checkpoint = lock.get("exact_completion_checkpoint") if isinstance(lock, dict) else {}
        effective_verification = dict(verification or {})
        if not effective_verification:
            effective_verification = _read_json(phase_path / "phase_verification.json", {}) or {}
        if checkpoint:
            effective_verification.setdefault("exact_completion_checkpoint_pass", bool(checkpoint.get("pass")))
        if effective_verification:
            step["verification"] = mask_sensitive_data({
                "pass": effective_verification.get("pass"),
                "status": effective_verification.get("status") or effective_verification.get("verdict") or "",
                "exact_completion_checkpoint_pass": effective_verification.get("exact_completion_checkpoint_pass"),
                "issues": (effective_verification.get("issues") or effective_verification.get("reasons") or [])[:20] if isinstance((effective_verification.get("issues") or effective_verification.get("reasons") or []), list) else [],
            })

        effective_judge = dict(judge or {})
        if not effective_judge:
            effective_judge = _read_json(phase_path / "phase_judge_result.json", {}) or _read_json(phase_path / "section_judge_gate.json", {}) or {}
        if effective_judge:
            deterministic = effective_judge.get("deterministic_judge") if isinstance(effective_judge.get("deterministic_judge"), dict) else {}
            text_model = effective_judge.get("text_model_judge") if isinstance(effective_judge.get("text_model_judge"), dict) else {}
            vision_model = effective_judge.get("vision_model_judge") if isinstance(effective_judge.get("vision_model_judge"), dict) else {}
            model_consensus = effective_judge.get("multi_model_consensus") if isinstance(effective_judge.get("multi_model_consensus"), dict) else {}
            human_review = effective_judge.get("human_phase_review") if isinstance(effective_judge.get("human_phase_review"), dict) else {}
            missing = deterministic.get("missing_values") if isinstance(deterministic.get("missing_values"), list) else []
            row_issues = deterministic.get("row_issues") if isinstance(deterministic.get("row_issues"), list) else []
            failed_attempts = deterministic.get("failed_attempts") if isinstance(deterministic.get("failed_attempts"), list) else []
            concise_missing = []
            for item in missing[:12]:
                if not isinstance(item, Mapping):
                    continue
                concise_missing.append({
                    "field": mask_sensitive_string(str(item.get("field") or ""))[:240],
                    "reason": mask_sensitive_string(str(item.get("reason") or ""))[:500],
                    "expected": mask_sensitive_string(str(item.get("expected") or ""))[:300],
                })
            step["judge"] = mask_sensitive_data({
                "pass": effective_judge.get("pass"),
                "status": effective_judge.get("status") or "",
                "reason_count": len(effective_judge.get("reasons") or []) if isinstance(effective_judge.get("reasons"), list) else len(missing) + len(row_issues) + len(failed_attempts),
                "deterministic_pass": deterministic.get("pass"),
                "missing_fields": concise_missing,
                "row_issue_count": len(row_issues),
                "failed_attempt_count": len(failed_attempts),
                "text_model_status": text_model.get("status") or "",
                "text_model_pass": text_model.get("pass"),
                "vision_model_status": vision_model.get("status") or "",
                "vision_model_pass": vision_model.get("pass"),
                "multi_model_consensus_used": bool(model_consensus.get("used")),
                "multi_model_pass_votes": model_consensus.get("pass_votes"),
                "multi_model_fail_votes": model_consensus.get("fail_votes"),
                "multi_model_rescued": bool(model_consensus.get("rescued_model_only_block")),
                "human_review_status": human_review.get("status") or "",
                "human_verdict": human_review.get("effective_human_verdict") or human_review.get("human_verdict") or "",
            })

        attempts = _read_json(phase_path / "phase_execution_attempts.json", [])
        if isinstance(attempts, list) and attempts:
            last = attempts[-1] if isinstance(attempts[-1], dict) else {}
            step["attempt"] = max(int(step.get("attempt") or 0), int(last.get("attempt") or 0))
            if last.get("failure_stage"):
                step["current_activity"] = f"Last attempt: {last.get('status')} at {last.get('failure_stage')}"[:500]
            if last.get("error"):
                step["blocker"] = mask_sensitive_string(str(last.get("error")))[:1600]

        stall = _read_json(phase_path / "phase_stall_guard.json", {})
        if isinstance(stall, dict) and stall.get("code"):
            step["blocker"] = str(stall.get("code"))

        step["updated_at"] = _utc_now()
        self._persist()

    def finalize(self, *, complete: bool) -> None:
        self.state["status"] = "complete" if complete else (self.state.get("status") or "blocked")
        if complete:
            self.state["current_step_id"] = ""
        self._persist()


def read_mission_trace(run_dir: str | Path) -> Dict[str, Any]:
    path = Path(run_dir) / TRACE_FILENAME
    data = _read_json(path, {})
    return data if isinstance(data, dict) else {}
