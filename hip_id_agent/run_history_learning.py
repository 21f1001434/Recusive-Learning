"""Learn from past runs -- run folders and MLflow -- and apply it to the next mission (V243R27).

Before R27 the agent learned *within* a mission (replay policy, recovery ladder,
skills, portal brain, model champion), but the history of earlier missions was
used only as whole-mission pass/fail episodes, and MLflow was write-only. The
facts that decide whether the next mission finishes -- how long each phase and
attempt really took, what stopped it, how long each HIP module needed to render,
which fields failed -- were never read back.

This module:

* harvests every past run **once** (a ledger keeps what was read) from
  - the run folders (``mission_state.json``, ``mlflow_async_events.jsonl``,
    ``<phase>/phase_execution_attempts.json``, the phase-budget and watchdog
    evidence, ``mcp_runtime/navigation_render_waits.jsonl``, the self-heal
    trace and the form runtime's failure summary), and
  - MLflow (the configured tracking server, or the local store beside the runs):
    runs from other machines, or whose folders were deleted, are learned from
    their metrics and tags;
* derives bounded **lessons**: phase and attempt time budgets, the no-progress
  watchdog per phase, the render wait per HIP module, and reports of failure
  codes, recoveries that resolved them and fields that failed;
* applies them at mission start. A lesson only ever **lengthens** a time
  budget, within a hard ceiling; it never shortens one, never skips a check, and
  never touches the mutation gate. The live page stays authoritative.

Nothing value-bearing is stored: phase names, field *labels*, failure codes,
durations and counts only.
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.run-history-learning.v1"
FACTS_SCHEMA = "hip.run-history-facts.v1"
_CODE = re.compile(r"\bHIP_[A-Z][A-Z0-9_]{3,}\b")
_BUDGET_STOPS = ("HIP_PHASE_NO_PROGRESS_WATCHDOG", "HIP_PHASE_WALLCLOCK_STALL_GUARD", "HIP_PHASE_STALL_AFTER_RECOVERY")
_ROUTE_CODES = ("HIP_ROUTE_NOT_COMMITTED", "HIP_MCP_SURFACE_DRIFT")
_PHASE_MODULE = {
    "source_document_type": "doctypes", "target_document_type": "doctypes", "data_map": "datamaps", "rule": "rules",
    "source_transport_profile": "transportprofiles", "target_transport_profile": "transportprofiles", "biz_flow": "bizflows",
}
_MAX_READ_BYTES = 8 * 1024 * 1024


def _cfg(config: Any, name: str, default: Any) -> Any:
    section = getattr(config, "run_history_learning", None)
    value = getattr(section, name, default) if section is not None else default
    return default if value is None else value


def _read_json(path: Path) -> Any:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_READ_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl(path: Path, *, limit: int = 20000) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        if not path.is_file() or path.stat().st_size > _MAX_READ_BYTES:
            return rows
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                if len(rows) >= limit:
                    break
    except Exception:
        pass
    return rows


def _codes(text: Any) -> List[str]:
    return sorted(set(_CODE.findall(str(text or ""))))


def _seconds_between(start: Any, end: Any) -> Optional[float]:
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        value = (b - a).total_seconds()
        return value if value >= 0 else None
    except Exception:
        return None


def _quantile(values: Sequence[float], q: float) -> float:
    data = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not data:
        return 0.0
    index = min(len(data) - 1, max(0, int(math.ceil(q * len(data))) - 1))
    return data[index]


def _count(bucket: Dict[str, int], key: str, n: int = 1) -> None:
    if key:
        bucket[key] = int(bucket.get(key) or 0) + n


def _merge_max(bucket: Dict[str, int], other: Mapping[str, int]) -> None:
    """The same stop is recorded by several files; count it once per run (the largest count)."""
    for key, value in other.items():
        bucket[key] = max(int(bucket.get(key) or 0), int(value or 0))


def _new_phase() -> Dict[str, Any]:
    return {"status": "", "attempts": 0, "completed_seconds": None, "attempt_seconds": [], "completed_attempt_seconds": None,
            "failure_codes": {}, "budget_decisions": {}, "watchdog_fired": 0, "field_failures": {},
            "parent_restorations": 0, "completed_after_failure": False}


# --------------------------------------------------------------- run folders
def _run_signature(run: Path) -> str:
    parts = []
    for name in ("mission_state.json", "mlflow_async_events.jsonl"):
        try:
            st = (run / name).stat()
            parts.append(f"{name}:{int(st.st_mtime)}:{st.st_size}")
        except Exception:
            continue
    return "|".join(parts)


def harvest_run_dir(run: Path) -> Optional[Dict[str, Any]]:
    """Value-free facts of one past run folder, or None when it is not a mission run."""
    state = _read_json(run / "mission_state.json")
    events = _read_jsonl(run / "mlflow_async_events.jsonl")
    if not isinstance(state, dict) and not events:
        return None
    state = state if isinstance(state, dict) else {}
    phases: Dict[str, Dict[str, Any]] = {}

    def phase_row(name: str) -> Dict[str, Any]:
        return phases.setdefault(str(name), _new_phase())

    for name, row in (state.get("phases") or {}).items():
        if not isinstance(row, dict):
            continue
        item = phase_row(name)
        item["status"] = str(row.get("status") or "")
        item["attempts"] = int(row.get("attempts") or 0)
        if item["status"] == "complete":
            item["completed_seconds"] = _seconds_between(row.get("started_at"), row.get("completed_at"))
        _merge_max(item["failure_codes"], {code: 1 for code in _codes(row.get("blocked_reason"))})

    finished = False
    mission_seconds = None
    blocked_codes: Dict[str, Dict[str, int]] = {}
    for event in events:
        kind = str(event.get("event") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        phase = str(payload.get("phase") or "")
        if kind == "phase_completed" and phase:
            duration = payload.get("duration_seconds")
            if isinstance(duration, (int, float)):
                item = phase_row(phase)
                item["attempt_seconds"].append(round(float(duration), 1))
                if payload.get("judge_pass"):
                    item["completed_attempt_seconds"] = round(float(duration), 1)
        elif kind == "phase_blocked" and phase:
            for code in _codes(payload.get("reason")):
                _count(blocked_codes.setdefault(phase, {}), code)
        elif kind == "mission_finished":
            finished = True
            if isinstance(payload.get("duration_seconds"), (int, float)):
                mission_seconds = round(float(payload["duration_seconds"]), 1)

    for phase, codes in blocked_codes.items():
        _merge_max(phase_row(phase)["failure_codes"], codes)

    for name in dict.fromkeys(list(phases) + [p.name for p in run.iterdir() if p.is_dir() and p.name in _PHASE_MODULE]):
        phase_dir = run / name
        if not phase_dir.is_dir():
            continue
        item = phase_row(name)
        attempts = _read_json(phase_dir / "phase_execution_attempts.json")
        failed_attempt = False
        attempt_codes: Dict[str, int] = {}
        for attempt in attempts if isinstance(attempts, list) else []:
            if not isinstance(attempt, dict):
                continue
            codes = _codes(attempt.get("error"))
            if codes or str(attempt.get("status") or "").startswith("fail"):
                failed_attempt = True
            for code in codes:
                _count(attempt_codes, code)
            if attempt.get("classification"):
                _count(attempt_codes, f"class:{attempt.get('classification')}")
        _merge_max(item["failure_codes"], attempt_codes)
        for budget in sorted(phase_dir.glob("phase_progress_budget_attempt_*.json"))[:20]:
            row = _read_json(budget)
            if isinstance(row, dict) and row.get("decision"):
                _count(item["budget_decisions"], str(row.get("decision")))
        watchdog = _read_json(phase_dir / "phase_no_progress_watchdog.json")
        if isinstance(watchdog, dict) and any(code == "HIP_PHASE_NO_PROGRESS_WATCHDOG" for code in _codes(json.dumps(watchdog)[:20000])):
            item["watchdog_fired"] += 1
        if item["failure_codes"].get("HIP_PHASE_NO_PROGRESS_WATCHDOG"):
            item["watchdog_fired"] = max(item["watchdog_fired"], 1)
        if item["status"] == "complete" and (failed_attempt or item["attempts"] > 1):
            item["completed_after_failure"] = True
        # Field-level failures matter only where the phase had trouble; a clean
        # first-attempt pass is not walked (its folder can be large).
        troubled = item["status"] != "complete" or failed_attempt or item["attempts"] > 1
        for runtime in (list(phase_dir.glob("**/autonomous_form_runtime.json"))[:6] if troubled else []):
            result = _read_json(runtime)
            if not isinstance(result, dict):
                continue
            summary = result.get("failure_summary") if isinstance(result.get("failure_summary"), dict) else {}
            for failure in summary.get("failed_attempts") or []:
                if isinstance(failure, dict) and failure.get("field"):
                    _count(item["field_failures"], str(failure.get("field"))[:120])
            execution = result.get("final_execution") if isinstance(result.get("final_execution"), dict) else {}
            item["parent_restorations"] += len(execution.get("parent_restorations") or [])

    navigation = []
    for row in _read_jsonl(run / "mcp_runtime" / "navigation_render_waits.jsonl", limit=2000):
        navigation.append({k: row.get(k) for k in ("module_key", "phase", "seconds", "render_waited_seconds", "usable", "reloaded")})

    recoveries = []
    heal = _read_json(run / "runtime_self_heal" / "runtime_self_heal_summary.json")
    open_actions: Dict[str, List[Dict[str, Any]]] = {}
    for row in (heal.get("trace") or []) if isinstance(heal, dict) else []:
        if not isinstance(row, dict):
            continue
        phase = str(row.get("phase") or "")
        if row.get("action") and row.get("classification"):
            open_actions.setdefault(phase, []).append({"classification": row.get("classification"), "action": row.get("action")})
        elif row.get("event") == "phase_finalized":
            for action in open_actions.pop(phase, []):
                recoveries.append({"phase": phase, **action, "phase_completed": bool(row.get("judge_pass"))})
    for phase, actions in open_actions.items():
        for action in actions:
            recoveries.append({"phase": phase, **action, "phase_completed": phases.get(phase, {}).get("status") == "complete"})

    status = str(state.get("mission_status") or "")
    complete = bool(phases) and all(p.get("status") == "complete" for p in phases.values())
    return {
        "schema_version": FACTS_SCHEMA, "run": run.name, "source": "run_folder", "signature": _run_signature(run),
        "finished": bool(finished or status in {"complete", "blocked", "failed"} or complete),
        "application_complete": complete, "mission_status": status, "mission_seconds": mission_seconds,
        "phases": phases, "navigation": navigation[:500], "recoveries": recoveries[:200], "harvested_at": utc_now(),
    }


# ------------------------------------------------------------------- MLflow
def _mlflow_uri(config: Any) -> tuple:
    """(tracking URI, local fallback store or None) -- the tracker's own choice."""
    import os

    from .mlflow_async import local_tracking_uri

    cfg = getattr(config, "mlflow", None)
    configured = str(getattr(cfg, "tracking_uri", "") or os.getenv("MLFLOW_TRACKING_URI", "") or "").strip()
    if configured:
        return configured, None
    return local_tracking_uri(config.reporting.runs_dir), Path(config.reporting.runs_dir) / "mlruns"


def harvest_mlflow(config: Any, *, known_runs: Iterable[str], max_runs: int = 100) -> Dict[str, Any]:
    """Facts of MLflow runs not already read from a run folder."""
    cfg = getattr(config, "mlflow", None)
    if cfg is not None and not bool(getattr(cfg, "enabled", True)):
        return {"status": "disabled", "runs": {}}
    uri, local_store = _mlflow_uri(config)
    if local_store is not None and not local_store.is_dir():
        return {"status": "no_store", "tracking_uri": uri, "runs": {}}
    try:
        from mlflow import MlflowClient
    except Exception as exc:
        return {"status": "mlflow_not_installed", "error": mask_sensitive_string(str(exc))[:200], "runs": {}}
    from .mlflow_async import allow_local_file_store

    allow_local_file_store(uri)
    known = set(str(x) for x in known_runs)
    client = MlflowClient(tracking_uri=uri)
    experiment = client.get_experiment_by_name(str(getattr(cfg, "experiment_name", "HIP Portal Agent") or "HIP Portal Agent"))
    if experiment is None:
        return {"status": "no_experiment", "tracking_uri": uri, "runs": {}}
    found = client.search_runs([experiment.experiment_id], max_results=max(1, int(max_runs)), order_by=["attributes.start_time DESC"])
    runs: Dict[str, Dict[str, Any]] = {}
    for run in found:
        tags = dict(run.data.tags or {})
        name = str(tags.get("hip.run_id") or run.info.run_id)
        if name in known:
            continue
        metrics = dict(run.data.metrics or {})
        phases: Dict[str, Dict[str, Any]] = {}
        for key, value in metrics.items():
            parts = key.split("/")
            if len(parts) < 3 or parts[0] != "phase":
                continue
            item = phases.setdefault(parts[1], _new_phase())
            metric = "/".join(parts[2:])
            if metric == "attempt":
                item["attempts"] = int(value)
            elif metric == "complete" and value >= 1:
                item["status"] = "complete"
            elif metric == "blocked" and value >= 1 and item["status"] != "complete":
                item["status"] = "blocked"
            elif metric.startswith("failure/"):
                _count(item["failure_codes"], metric.split("/", 1)[1], int(value) or 1)
        for phase, item in phases.items():
            try:
                history = client.get_metric_history(run.info.run_id, f"phase/{phase}/duration_seconds")
            except Exception:
                history = []
            item["attempt_seconds"] = [round(float(m.value), 1) for m in history][:50]
            if item["status"] == "complete" and item["attempt_seconds"]:
                item["completed_attempt_seconds"] = item["attempt_seconds"][-1]
                item["completed_after_failure"] = item["attempts"] > 1
        blocked = str(tags.get("hip.blocked_phase") or "")
        for code in _codes(tags.get("hip.last_block_reason")):
            if blocked:
                _count(phases.setdefault(blocked, _new_phase())["failure_codes"], code)
        for phase, item in phases.items():
            if item["failure_codes"].get("HIP_PHASE_NO_PROGRESS_WATCHDOG"):
                item["watchdog_fired"] = 1
        navigation = []
        for key, value in metrics.items():
            parts = key.split("/")
            if len(parts) == 3 and parts[0] == "navigation" and parts[2] == "render_seconds":
                navigation.append({"module_key": parts[1], "seconds": float(value), "usable": True})
        status = str(tags.get("hip.mission_status") or "")
        runs[name] = {
            "schema_version": FACTS_SCHEMA, "run": name, "source": "mlflow", "mlflow_run_id": run.info.run_id,
            "signature": f"mlflow:{run.info.status}:{run.info.end_time}", "finished": run.info.status in {"FINISHED", "FAILED", "KILLED"},
            "application_complete": str(tags.get("hip.application_complete") or "").lower() == "true",
            "mission_status": status, "mission_seconds": metrics.get("mission.duration_seconds"),
            "phases": phases, "navigation": navigation, "recoveries": [], "harvested_at": utc_now(),
        }
    return {"status": "ok", "tracking_uri": uri, "runs": runs, "searched": len(found)}


# ------------------------------------------------------------------ lessons
def derive_lessons(facts: Mapping[str, Mapping[str, Any]], config: Any) -> Dict[str, Any]:
    heal = getattr(config, "runtime_self_heal", None)
    portal = getattr(config, "portal", None)
    base_wall = float(getattr(heal, "max_phase_wall_seconds", 3600) or 3600)
    base_attempt = float(getattr(heal, "min_attempt_seconds", 900.0) or 900.0)
    base_watchdog = float(getattr(heal, "no_progress_watchdog_seconds", 90.0) or 90.0)
    base_render = float(getattr(portal, "navigation_render_wait_seconds", 90.0) or 90.0)
    max_render = max(base_render, float(getattr(portal, "navigation_render_wait_max_seconds", 300.0) or 300.0))
    margin = float(_cfg(config, "time_margin", 1.25) or 1.25)
    ceiling = float(_cfg(config, "max_budget_multiplier", 3.0) or 3.0)

    phase_stats: Dict[str, Dict[str, Any]] = {}
    modules: Dict[str, Dict[str, Any]] = {}
    recoveries: Dict[str, Dict[str, int]] = {}
    for run in facts.values():
        for phase, row in (run.get("phases") or {}).items():
            stat = phase_stats.setdefault(phase, {"runs": 0, "completed": 0, "completed_seconds": [], "completed_attempt_seconds": [],
                                                  "attempt_seconds": [], "failure_codes": {}, "watchdog_runs": 0,
                                                  "watchdog_then_completed": 0, "budget_stops": 0, "field_failures": {},
                                                  "parent_restorations": 0, "route_failures": 0})
            stat["runs"] += 1
            if row.get("status") == "complete":
                stat["completed"] += 1
                if row.get("completed_seconds"):
                    stat["completed_seconds"].append(float(row["completed_seconds"]))
                if row.get("completed_attempt_seconds"):
                    stat["completed_attempt_seconds"].append(float(row["completed_attempt_seconds"]))
            stat["attempt_seconds"].extend(float(x) for x in row.get("attempt_seconds") or [])
            for code, n in (row.get("failure_codes") or {}).items():
                _count(stat["failure_codes"], code, int(n or 0))
                if code in _ROUTE_CODES:
                    stat["route_failures"] += int(n or 0)
            if row.get("watchdog_fired"):
                stat["watchdog_runs"] += 1
                if row.get("status") == "complete":
                    stat["watchdog_then_completed"] += 1
            if any(code in (row.get("failure_codes") or {}) for code in _BUDGET_STOPS) or any(
                    "stop" in str(k) for k in (row.get("budget_decisions") or {})):
                stat["budget_stops"] += 1
            for field, n in (row.get("field_failures") or {}).items():
                _count(stat["field_failures"], field, int(n or 0))
            stat["parent_restorations"] += int(row.get("parent_restorations") or 0)
        for nav in run.get("navigation") or []:
            key = str(nav.get("module_key") or "")
            if not key:
                continue
            module = modules.setdefault(key, {"navigations": 0, "usable_seconds": [], "not_usable": 0, "reloaded": 0})
            module["navigations"] += 1
            if nav.get("usable"):
                module["usable_seconds"].append(float(nav.get("seconds") or 0.0))
            else:
                module["not_usable"] += 1
            if nav.get("reloaded"):
                module["reloaded"] += 1
        for rec in run.get("recoveries") or []:
            key = f"{rec.get('phase')}|{rec.get('classification')}|{rec.get('action')}"
            bucket = recoveries.setdefault(key, {"used": 0, "phase_completed": 0})
            bucket["used"] += 1
            if rec.get("phase_completed"):
                bucket["phase_completed"] += 1

    lessons: Dict[str, Any] = {"phase_wall_seconds": {}, "min_attempt_seconds": {}, "no_progress_watchdog_seconds": {},
                               "navigation_render_wait_seconds": {}}
    reasons: List[Dict[str, Any]] = []
    for phase, stat in sorted(phase_stats.items()):
        if stat["completed_seconds"]:
            learned = min(base_wall * ceiling, _quantile(stat["completed_seconds"], 0.9) * margin)
            if learned > base_wall:
                lessons["phase_wall_seconds"][phase] = round(learned)
                reasons.append({"phase": phase, "lesson": "phase_wall_seconds", "value": round(learned),
                                "why": f"completed phases took up to {round(_quantile(stat['completed_seconds'], 0.9))} s (p90 of {len(stat['completed_seconds'])})"})
        attempt_evidence = stat["completed_attempt_seconds"] or []
        if attempt_evidence:
            learned = min(base_attempt * ceiling, _quantile(attempt_evidence, 0.9) * margin)
            if stat["budget_stops"]:
                learned = max(learned, min(base_attempt * ceiling, base_attempt * (1.0 + 0.5 * stat["budget_stops"])))
            if learned > base_attempt:
                lessons["min_attempt_seconds"][phase] = round(learned)
                reasons.append({"phase": phase, "lesson": "min_attempt_seconds", "value": round(learned),
                                "why": f"a successful attempt took up to {round(_quantile(attempt_evidence, 0.9))} s"
                                       + (f"; stopped by the budget in {stat['budget_stops']} run(s)" if stat["budget_stops"] else "")})
        elif stat["budget_stops"]:
            learned = min(base_attempt * ceiling, base_attempt * (1.0 + 0.5 * stat["budget_stops"]))
            lessons["min_attempt_seconds"][phase] = round(learned)
            reasons.append({"phase": phase, "lesson": "min_attempt_seconds", "value": round(learned),
                            "why": f"stopped by the phase budget / watchdog in {stat['budget_stops']} run(s)"})
        if stat["watchdog_then_completed"]:
            # The watchdog stopped an attempt of a phase that then completed: the
            # portal was slow, not stuck. Give its quiet periods more time.
            learned = min(base_watchdog * ceiling, base_watchdog * (1.0 + 0.5 * stat["watchdog_then_completed"]))
            lessons["no_progress_watchdog_seconds"][phase] = round(learned)
            reasons.append({"phase": phase, "lesson": "no_progress_watchdog_seconds", "value": round(learned),
                            "why": f"the no-progress watchdog fired in {stat['watchdog_then_completed']} run(s) where the phase then completed"})
        if stat["route_failures"]:
            module = _PHASE_MODULE.get(phase, "")
            if module:
                modules.setdefault(module, {"navigations": 0, "usable_seconds": [], "not_usable": 0, "reloaded": 0})
                modules[module]["route_failures"] = int(modules[module].get("route_failures") or 0) + stat["route_failures"]
    for key, module in sorted(modules.items()):
        learned = 0.0
        why = []
        if module["usable_seconds"]:
            learned = _quantile(module["usable_seconds"], 0.95) * 1.5
            why.append(f"rendered in up to {round(_quantile(module['usable_seconds'], 0.95), 1)} s (p95 of {len(module['usable_seconds'])})")
        failures = int(module.get("not_usable") or 0) + int(module.get("route_failures") or 0)
        if failures:
            learned = max(learned, base_render * (1.0 + 0.5 * min(failures, 4)))
            why.append(f"did not render in time {failures} time(s)")
        learned = min(max_render, learned)
        if learned > base_render:
            lessons["navigation_render_wait_seconds"][key] = round(learned)
            reasons.append({"module": key, "lesson": "navigation_render_wait_seconds", "value": round(learned), "why": "; ".join(why)})

    report_phases = {}
    for phase, stat in sorted(phase_stats.items()):
        report_phases[phase] = {
            "runs": stat["runs"], "completed": stat["completed"],
            "p90_completed_seconds": round(_quantile(stat["completed_seconds"], 0.9)) if stat["completed_seconds"] else None,
            "p90_completed_attempt_seconds": round(_quantile(stat["completed_attempt_seconds"], 0.9)) if stat["completed_attempt_seconds"] else None,
            "top_failure_codes": sorted(stat["failure_codes"].items(), key=lambda kv: -kv[1])[:6],
            "watchdog_runs": stat["watchdog_runs"], "budget_stops": stat["budget_stops"],
            "fields_that_failed": sorted(stat["field_failures"].items(), key=lambda kv: -kv[1])[:8],
            "parent_restorations": stat["parent_restorations"],
        }
    best_recoveries = sorted(
        ({"phase": k.split("|")[0], "classification": k.split("|")[1], "action": k.split("|")[2], **v} for k, v in recoveries.items()),
        key=lambda r: (-r["phase_completed"], -r["used"]))[:20]
    runs = list(facts.values())
    return mask_sensitive_data({
        "schema_version": SCHEMA, "derived_at": utc_now(),
        "runs_learned": len(runs),
        "runs_from_mlflow": sum(1 for r in runs if r.get("source") == "mlflow"),
        "runs_from_folders": sum(1 for r in runs if r.get("source") == "run_folder"),
        "missions_completed": sum(1 for r in runs if r.get("application_complete")),
        "lessons": lessons, "reasons": reasons, "phases": report_phases,
        "modules": {k: {"navigations": v["navigations"], "not_usable": v["not_usable"], "reloaded": v["reloaded"],
                        "p95_render_seconds": round(_quantile(v["usable_seconds"], 0.95), 1) if v["usable_seconds"] else None}
                    for k, v in sorted(modules.items())},
        "recoveries": best_recoveries,
        "policy": "lessons only lengthen time budgets (bounded); they never shorten one, skip a check or relax the mutation gate",
        "values_stored": False,
    })


# ------------------------------------------------------------------ learner
class RunHistoryLearner:
    """Harvest past runs once, keep their facts, derive and persist lessons."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.root = Path(config.reporting.memory_dir) / str(_cfg(config, "memory_subdir", "run_history") or "run_history")
        self.facts_path = self.root / "run_facts.json"
        self.lessons_path = self.root / "lessons.json"

    def _facts(self) -> Dict[str, Dict[str, Any]]:
        data = _read_json(self.facts_path)
        runs = data.get("runs") if isinstance(data, dict) else None
        return {str(k): v for k, v in (runs or {}).items() if isinstance(v, dict)}

    def load_lessons(self) -> Dict[str, Any]:
        data = _read_json(self.lessons_path)
        return data if isinstance(data, dict) else {}

    def learn(self, *, runs_dir: Optional[str | Path] = None, exclude_run: Optional[str | Path] = None,
              include_mlflow: Optional[bool] = None) -> Dict[str, Any]:
        started = time.monotonic()
        if not bool(_cfg(self.config, "enabled", True)):
            return {"schema_version": SCHEMA, "status": "disabled"}
        runs_root = Path(runs_dir or self.config.reporting.runs_dir)
        facts = self._facts()
        excluded = None
        try:
            excluded = Path(exclude_run).resolve() if exclude_run else None
        except Exception:
            excluded = None
        new_runs, refreshed = [], []
        max_runs = int(_cfg(self.config, "max_runs", 300) or 300)
        if runs_root.is_dir():
            folders = sorted((p for p in runs_root.iterdir() if p.is_dir() and p.name != "mlruns"),
                             key=lambda p: p.stat().st_mtime, reverse=True)[:max_runs]
            for run in folders:
                try:
                    if excluded is not None and run.resolve() == excluded:
                        continue
                except Exception:
                    pass
                known = facts.get(run.name)
                if known and (known.get("finished") or known.get("signature") == _run_signature(run)):
                    continue  # each run is read once (again only if it was still running)
                row = harvest_run_dir(run)
                if row is None:
                    continue
                (refreshed if known else new_runs).append(run.name)
                facts[run.name] = row
        mlflow_status: Dict[str, Any] = {"status": "skipped"}
        use_mlflow = bool(_cfg(self.config, "include_mlflow", True)) if include_mlflow is None else bool(include_mlflow)
        if use_mlflow:
            timeout = float(_cfg(self.config, "mlflow_timeout_seconds", 30.0) or 30.0)
            # A finished run (or one read from its folder) is not read again; an MLflow
            # run that was still running is.
            known = {k for k, v in facts.items() if v.get("finished") or v.get("source") == "run_folder"}
            known |= ({excluded.name} if excluded is not None else set())
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hip-run-history-mlflow")
            try:
                future = pool.submit(harvest_mlflow, self.config, known_runs=known,
                                     max_runs=int(_cfg(self.config, "max_mlflow_runs", 100) or 100))
                mlflow_status = future.result(timeout=timeout)
            except FutureTimeout:
                mlflow_status = {"status": "timeout", "runs": {}}
            except Exception as exc:
                mlflow_status = {"status": "error", "error": mask_sensitive_string(str(exc))[:300], "runs": {}}
            finally:
                pool.shutdown(wait=False)
            for name, row in (mlflow_status.get("runs") or {}).items():
                if name in facts and facts[name].get("signature") == row.get("signature"):
                    continue
                (refreshed if name in facts else new_runs).append(name)
                facts[name] = row
        if len(facts) > max_runs:
            keep = sorted(facts.items(), key=lambda kv: str(kv[1].get("harvested_at") or ""), reverse=True)[:max_runs]
            facts = dict(keep)
        self.root.mkdir(parents=True, exist_ok=True)
        safe_write_json(self.facts_path, {"schema_version": FACTS_SCHEMA, "updated_at": utc_now(), "runs": facts}, mask=False)
        lessons = derive_lessons(facts, self.config)
        lessons.update({
            "new_runs_this_time": new_runs[:50], "new_run_count": len(new_runs), "refreshed_runs": refreshed[:50],
            "mlflow": {k: v for k, v in mlflow_status.items() if k != "runs"} | {"new_runs": len(mlflow_status.get("runs") or {})},
            "learn_seconds": round(time.monotonic() - started, 2), "status": "complete",
        })
        safe_write_json(self.lessons_path, lessons, mask=False)
        return lessons


def run_history_learner_from_config(config: Any) -> RunHistoryLearner:
    return RunHistoryLearner(config)


# ------------------------------------------------------------------- apply
def lesson_value(lessons: Mapping[str, Any], name: str, key: str, default: float) -> float:
    """The configured value, or the learned one when past runs needed more."""
    try:
        learned = float(((lessons.get("lessons") or {}).get(name) or {}).get(key) or 0.0)
    except Exception:
        learned = 0.0
    return max(float(default), learned)


def apply_lessons(lessons: Mapping[str, Any], *, healer: Any = None, browser: Any = None) -> Dict[str, Any]:
    """Apply the lessons to this mission's self-healer and browser session."""
    learned = lessons.get("lessons") if isinstance(lessons.get("lessons"), Mapping) else {}
    applied: Dict[str, Any] = {}
    wall = {str(k): float(v) for k, v in (learned.get("phase_wall_seconds") or {}).items()}
    if healer is not None and wall and hasattr(healer, "apply_learned_wall_budgets"):
        applied["phase_wall_seconds"] = healer.apply_learned_wall_budgets(wall)
    render = {str(k): float(v) for k, v in (learned.get("navigation_render_wait_seconds") or {}).items()}
    if browser is not None and render:
        try:
            current = dict(getattr(browser, "_learned_render_wait_seconds", {}) or {})
            current.update(render)
            browser._learned_render_wait_seconds = current
            applied["navigation_render_wait_seconds"] = render
        except Exception:
            pass
    for name in ("min_attempt_seconds", "no_progress_watchdog_seconds"):
        if learned.get(name):
            applied[name] = dict(learned.get(name) or {})
    return applied


def summary_line(lessons: Mapping[str, Any]) -> str:
    if not lessons:
        return "not learned yet"
    count = len(lessons.get("reasons") or [])
    return (f"{lessons.get('runs_learned', 0)} past run(s) learned ({lessons.get('runs_from_mlflow', 0)} from MLflow); "
            f"{count} lesson(s) in use")


__all__ = [
    "RunHistoryLearner", "apply_lessons", "derive_lessons", "harvest_mlflow", "harvest_run_dir", "lesson_value",
    "run_history_learner_from_config", "summary_line",
]
