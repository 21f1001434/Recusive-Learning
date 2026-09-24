from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

ALLOWED_REPAIRS = {
    "adaptive_rediscovery", "rebind_controls", "refresh_surface", "retry_dropdown_live",
    "expand_repeatable_rows", "human_teach", "stop_and_report",
}


class TraceSelfRepairEngine:
    """Reads bounded run traces and asks the on-prem model portfolio for recovery.

    The LLM can choose only an allowlisted repair policy. It cannot edit source code,
    invent selectors, authorize mutations or persist customer values. The real portal
    result supplies the reward that later promotes/demotes model and replay policy.
    """

    def __init__(self, root: str | Path, config: Any, *, model_portfolio: Any) -> None:
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.config = config; self.model_portfolio = model_portfolio

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    @staticmethod
    def _read_json(path: Path) -> Any:
        try: return json.loads(path.read_text(encoding="utf-8"))
        except Exception: return None

    def collect(self, run_dir: str | Path, *, error: str = "", failed_step: Optional[Mapping[str, Any]] = None, max_chars: Optional[int] = None) -> Dict[str, Any]:
        run_dir = Path(run_dir); budget = int(max_chars or self._cfg("max_trace_chars", 42000) or 42000)
        names = [
            "mission_trace.json", "universal_portal_task_execution.json", "universal_form_fill.json",
            "production_failure_diagnostics.json", "final_report.json", "agent_live_view.json",
        ]
        evidence: Dict[str, Any] = {"error": mask_sensitive_string(str(error or ""))[:2000], "failed_step": mask_sensitive_data(dict(failed_step or {})), "files": {}}
        used = 0
        for name in names:
            path = run_dir / name
            if not path.is_file(): continue
            row = self._read_json(path)
            if row is None: continue
            text = json.dumps(mask_sensitive_data(row), ensure_ascii=False, default=str)
            if used + len(text) > budget: text = text[:max(0,budget-used)]
            evidence["files"][name] = text; used += len(text)
            if used >= budget: break
        # Include only compact, value-masked recent json artifacts from nested dirs.
        if used < budget:
            try:
                candidates = sorted((p for p in run_dir.rglob("*.json") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
            except Exception: candidates = []
            for path in candidates:
                rel = str(path.relative_to(run_dir))
                if rel in evidence["files"]: continue
                row = self._read_json(path)
                if row is None: continue
                text = json.dumps(mask_sensitive_data(row), ensure_ascii=False, default=str)
                if len(text) > 6000: text = text[:6000]
                if used + len(text) > budget: break
                evidence["files"][rel] = text; used += len(text)
        evidence["trace_chars"] = used; evidence["values_stored"] = False
        return evidence

    def analyze(self, *, run_dir: str | Path, task: str, error: str, failed_step: Optional[Mapping[str, Any]] = None, mutation_risk: bool = False) -> Dict[str, Any]:
        evidence = self.collect(run_dir, error=error, failed_step=failed_step)
        default = {"used": False, "repair_strategy": "human_teach" if "ambig" in error.lower() else "adaptive_rediscovery", "confidence": 0.0, "human_required": "ambig" in error.lower(), "reason": "deterministic_fallback"}
        if not bool(self._cfg("enabled", True)) or self.model_portfolio is None:
            return {**default, "trace_summary": evidence}
        prompt = {
            "task": task,
            "error": mask_sensitive_string(error)[:2000],
            "mutation_risk": bool(mutation_risk),
            "allowed_repairs": sorted(ALLOWED_REPAIRS),
            "trace": evidence,
            "rules": [
                "Choose only one allowed repair_strategy.",
                "Never invent CSS/XPath/coordinates or business values.",
                "Never authorize a mutation.",
                "Use human_teach when field/control mapping remains ambiguous.",
                "Use adaptive_rediscovery/rebind_controls for stale semantic memory.",
            ],
        }
        try:
            trace = self.model_portfolio.tournament_text(
                system="You are the recovery judge for a Dell HIP browser agent. Read the masked execution trace and return strict JSON: repair_strategy, confidence (0..1), human_required (bool), root_cause, repair_reason. Use only the supplied allowlist.",
                task=json.dumps(prompt, ensure_ascii=False, default=str), role="recovery", expected_json=True,
                require_keys=["repair_strategy", "confidence", "human_required"], exploration=True,
                learning=True, complex_task=True, force_multi_model=True,
            )
            parsed = dict(trace.get("parsed") or {}) if trace.get("used") else {}
            strategy = str(parsed.get("repair_strategy") or "")
            if strategy not in ALLOWED_REPAIRS: strategy = default["repair_strategy"]
            confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0.0)))
            human = bool(parsed.get("human_required")) or confidence < float(self._cfg("auto_repair_min_confidence", 0.72) or 0.72)
            if mutation_risk: human = True
            result = {
                "schema_version": "hip.trace-self-repair.v1", "used": bool(trace.get("used")), "recorded_at": utc_now(),
                "repair_strategy": strategy, "confidence": confidence, "human_required": human,
                "root_cause": mask_sensitive_string(str(parsed.get("root_cause") or ""))[:1000],
                "repair_reason": mask_sensitive_string(str(parsed.get("repair_reason") or ""))[:1000],
                "model_routing": trace, "trace_summary": evidence, "source_code_self_modification": False,
            }
        except Exception as exc:
            result = {**default, "error": mask_sensitive_string(str(exc))[:800], "trace_summary": evidence, "source_code_self_modification": False}
        out = Path(run_dir) / "trace_self_repair.json"; safe_write_json(out, result)
        return mask_sensitive_data(result)

    def manifest(self) -> Dict[str, Any]:
        return {"enabled": bool(self._cfg("enabled", True)), "allowed_repairs": sorted(ALLOWED_REPAIRS), "source_code_self_modification": False, "root": str(self.root)}


def trace_self_repair_from_config(app_config: Any, *, model_portfolio: Any) -> TraceSelfRepairEngine:
    root = Path(app_config.reporting.memory_dir) / str(getattr(app_config.trace_self_repair, "memory_subdir", "trace_self_repair") or "trace_self_repair")
    return TraceSelfRepairEngine(root, app_config.trace_self_repair, model_portfolio=model_portfolio)
