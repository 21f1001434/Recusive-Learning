from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .browser_session import BrowserSession
from .capability_graph import HIPCapabilityGraph
from .deterministic_recipe import deterministic_recipe_from_config
from .human_phase_review import human_phase_review_from_config
from .models import utc_now
from .model_portfolio import model_portfolio_from_config
from .recursive_self_improvement import recursive_improvement_from_config
from .replay_policy import replay_policy_engine_from_config
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .skill_induction import InducedSkillLibrary
from .universal_portal_operator import UniversalPortalTaskExecutor, UniversalPortalTaskPlanner

SCHEMA = "hip.persistent-operator.v1"


FAMILY_ALIASES = {
    "data_maps": ("data map", "datamap", "map"),
    "document_types": ("document type", "doctype", "doc type"),
    "rules": ("mapping rule", "rule"),
    "transport_profiles": ("transport profile", "transportprofile", " tp ", "source tp", "target tp"),
    "bizflows": ("bizflow", "biz flow", "business flow"),
}
FAMILY_LABELS = {
    "data_maps": "Data Maps",
    "document_types": "Document Types",
    "rules": "Rules",
    "transport_profiles": "Transport Profiles",
    "bizflows": "BizFlow",
    "unknown": "HIP Portal",
}


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def infer_family(task: str) -> str:
    text = f" {_norm(task)} "
    scored = []
    for family, aliases in FAMILY_ALIASES.items():
        score = 0
        for alias in aliases:
            if alias.strip() and alias.lower() in text:
                score += max(1, len(alias.split()))
        if score:
            scored.append((score, family))
    return sorted(scored, reverse=True)[0][1] if scored else "unknown"


def infer_operation(task: str) -> str:
    text = _norm(task)
    for op, pattern in (
        ("delete", r"\b(delete|remove)\b"),
        ("deploy", r"\bdeploy\b"),
        ("migrate", r"\bmigrat(?:e|ion)\b"),
        ("create", r"\b(create|add new|new )\b"),
        ("edit", r"\b(edit|change|update|set|modify|replace)\b"),
        ("search", r"\b(search|find|locate|look up|lookup)\b"),
        ("view", r"\b(view|show|open|inspect)\b"),
    ):
        if re.search(pattern, text, flags=re.I):
            return op
    return "operate"


def infer_entity_name(task: str, family: str = "unknown") -> str:
    text = str(task or "")
    for pattern in (r'"([^"\n]{2,})"', r"'([^'\n]{2,})'"):
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    family_terms = {
        "data_maps": r"(?:data\s*map|map)",
        "document_types": r"(?:document\s*type|doctype|doc\s*type)",
        "rules": r"(?:mapping\s*rule|rule)",
        "transport_profiles": r"(?:transport\s*profile|source\s*tp|target\s*tp|tp)",
        "bizflows": r"(?:biz\s*flow|bizflow|business\s*flow)",
    }
    term = family_terms.get(family)
    if term:
        m = re.search(term + r"\s+(?:named\s+|called\s+)?([A-Za-z0-9_.:/-]{2,})", text, flags=re.I)
        if m:
            candidate = m.group(1).strip(" .,:;")
            if candidate.lower() not in {"and", "to", "from", "with", "where", "section", "value"}:
                return candidate
    m = re.search(r"(?:search|find|locate|edit|update)\s+(?:for\s+)?([A-Za-z0-9_.:/-]{2,})", text, flags=re.I)
    return m.group(1).strip(" .,:;") if m else ""


def extract_inline_patch(task: str) -> Dict[str, Any]:
    """Extract only explicit user-provided field/value changes into run-local input.

    This is deliberately conservative. It never guesses missing values and the
    resulting patch is never persisted into long-term portal knowledge.
    """
    text = str(task or "")
    out: Dict[str, Any] = {}
    # field=value syntax, useful for operator/chat requests.
    for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9 _./-]{1,80}?)\s*=\s*([^,;\n]+)", text):
        raw_key = re.sub(r"^(?:change|set|update|modify|replace)\s+", "", m.group(1).strip(), flags=re.I)
        key = raw_key.replace(" ", "_")
        value = m.group(2).strip().strip('"\'')
        if key and value:
            out[key] = value
    # Natural-language change/set/update FIELD to VALUE. Stop at a clear clause.
    for m in re.finditer(
        r"\b(?:change|set|update|modify|replace)\s+(?:the\s+)?([A-Za-z][A-Za-z0-9 _./-]{1,80}?)\s+(?:to|as)\s+(.+?)(?=\s+(?:and\s+(?:change|set|update|modify|save|then)|then\b)|[,;\n]|$)",
        text, flags=re.I,
    ):
        raw_key = re.sub(r"\b(?:field|value)\b", "", m.group(1), flags=re.I).strip(" .")
        value = m.group(2).strip().strip('"\'').strip(" .")
        key = raw_key.replace(" ", "_")
        if key and value:
            out[key] = value
    return out


def normalize_task(task: str, family: str, operation: str, entity: str) -> str:
    label = FAMILY_LABELS.get(family, "HIP Portal")
    text = str(task or "").strip()
    if not re.search(r"\b(navigate|go|open|visit)\b", text, flags=re.I):
        text = f"Navigate to the {label} section and {text}"
    if operation == "edit" and "edit" not in _norm(text):
        text += " then edit"
    if entity and not re.search(r"\b(search|find|locate)\b", text, flags=re.I):
        text = f"Navigate to the {label} section, search for '{entity}', and {text}"
    return text


class PersistentHIPOperator:
    """Persistent goal-oriented HIP operator.

    One user goal owns one browser session. Failed attempts update replay/model/RSI
    memory and then retry; they are not terminal run outcomes. With max_goal_cycles=0
    there is no attempt-count termination. Repeated failure escalates to supervised
    recovery while keeping the browser open. Success requires final human acceptance
    when configured.
    """

    def __init__(self, config: Any, graph: Optional[HIPCapabilityGraph] = None) -> None:
        self.config = config
        root = Path(config.reporting.memory_dir) / str(config.brain.directory or "portal_brain")
        self.graph = graph or HIPCapabilityGraph(root)
        self.planner = UniversalPortalTaskPlanner(config, self.graph)
        self.executor = UniversalPortalTaskExecutor(config, self.graph)
        self.human_reviews = human_phase_review_from_config(config)
        self.replay = replay_policy_engine_from_config(config)
        self.models = model_portfolio_from_config(config)
        # Reuse the executor's induced skill library so recursive improvement sees
        # the same learned skills that live execution uses.
        self.rsi = recursive_improvement_from_config(
            config,
            replay_policy=self.replay,
            model_portfolio=self.models,
            skill_library=getattr(self.executor, "skills", None),
        )
        self.recipes = deterministic_recipe_from_config(config)

    def _cfg(self, name: str, default: Any) -> Any:
        cfg = getattr(self.config, "persistent_operator", None)
        return getattr(cfg, name, default) if cfg is not None else default

    def knowledge_snapshot(self, family: str) -> Dict[str, Any]:
        try:
            caps = self.graph.query_capabilities(page_family=None if family == "unknown" else family)
        except TypeError:
            caps = self.graph.query_capabilities()
            if family != "unknown":
                caps = [x for x in caps if str(x.get("page_family") or "") == family]
        except Exception:
            caps = []
        return mask_sensitive_data({
            "schema_version": "hip.operator-knowledge-snapshot.v1",
            "family": family,
            "capability_count": len(caps),
            "capability_kinds": sorted({str(x.get("kind") or "") for x in caps if x.get("kind")}),
            "graph_manifest": self.graph.manifest(),
            "skill_manifest": getattr(getattr(self.executor, "skills", None), "manifest", lambda: {})(),
            "recipe_manifest": self.recipes.manifest(),
            "model_portfolio": self.models.manifest(),
            "values_stored": False,
        })

    async def _wait_for_resolution(self, request_id: str, *, run_dir: Path) -> Dict[str, Any]:
        wait_seconds = int(self._cfg("human_wait_seconds", 0) or 0)
        poll = max(0.25, float(self._cfg("poll_seconds", 2.0) or 2.0))
        elapsed = 0.0
        stop_path = run_dir / str(self._cfg("stop_file_name", "STOP_HIP_OPERATOR") or "STOP_HIP_OPERATOR")
        while True:
            row = self.human_reviews.get(request_id)
            if row.get("status") == "resolved":
                return row
            if stop_path.exists():
                return {"status": "stopped", "effective_human_verdict": "stopped", "request_id": request_id}
            if wait_seconds > 0 and elapsed >= wait_seconds:
                return {"status": "timeout", "effective_human_verdict": "timeout", "request_id": request_id}
            await asyncio.sleep(poll)
            elapsed += poll

    def _record_cycle_improvement(self, *, success: bool, reward: float, reason: str, run_id: str) -> Dict[str, Any]:
        if not bool(self._cfg("update_rsi_after_every_cycle", True)):
            return {"status": "disabled_by_persistent_operator"}
        try:
            if bool(self._cfg("use_multi_model_after_failure", True)) and not success:
                try:
                    self.models.dream(reason=f"persistent_operator:{reason}")
                except Exception:
                    pass
            return self.rsi.improve(
                run_reward=reward,
                success=success,
                reason=reason,
                skill_feedback={"run_id": run_id, "outcome_already_recorded": True},
            )
        except Exception as exc:
            return {"status": "error", "error": mask_sensitive_string(str(exc))[:500]}

    async def execute(
        self,
        *,
        task: str,
        run_dir: str | Path,
        input_json: str = "",
        input_root: str = "",
        start_url: str = "",
        deep_learn: bool = True,
        allow_portal_mutation: bool = False,
        confirmation: str = "",
    ) -> Dict[str, Any]:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        family = infer_family(task)
        operation = infer_operation(task)
        entity = infer_entity_name(task, family)
        patch = extract_inline_patch(task) if bool(self._cfg("synthesize_inline_patch_input", True)) else {}
        normalized_task = normalize_task(task, family, operation, entity)

        runtime_input = str(input_json or "")
        if patch and (not runtime_input or operation == "edit"):
            patch_path = run_dir / "operator_inline_patch.json"
            safe_write_json(patch_path, patch)
            runtime_input = str(patch_path)
            input_root = "$"

        run_id = run_dir.name
        try:
            model_probe = self.models.probe_text_models(force=False)
        except Exception as exc:
            model_probe = {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))[:500]}
        safe_write_json(run_dir / "model_orchestra_startup.json", {
            "schema_version": "hip.model-orchestra-startup.v1",
            "probe": model_probe,
            "portfolio": self.models.manifest(),
            "policy": {
                "learning_requires_multiple_models": bool(getattr(self.config.model_portfolio, "force_multi_model_during_learning", True)),
                "complex_tasks_require_multiple_models": bool(getattr(self.config.model_portfolio, "force_multi_model_for_complex_tasks", True)),
                "single_model_allowed_after_proven_exploitation": bool(getattr(self.config.model_portfolio, "fast_exploitation_single_model", True)),
            },
        })
        state: Dict[str, Any] = {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "task": mask_sensitive_string(task),
            "normalized_task": mask_sensitive_string(normalized_task),
            "family": family,
            "operation": operation,
            "entity": mask_sensitive_string(entity),
            "started_at": utc_now(),
            "status": "running_until_verified_and_human_accepted",
            "cycles": [],
            "open_ended": int(self._cfg("max_goal_cycles", 0) or 0) == 0,
            "final_human_required": bool(self._cfg("require_final_human_confirmation", True)),
            "values_stored_in_memory": False,
        }
        safe_write_json(run_dir / "persistent_operator_state.json", state)
        safe_write_json(run_dir / "operator_knowledge_before.json", self.knowledge_snapshot(family))

        max_cycles = max(0, int(self._cfg("max_goal_cycles", 0) or 0))
        human_after = max(1, int(self._cfg("human_after_failed_cycles", 3) or 3))
        failed_streak = 0
        final_result: Dict[str, Any] = {}

        async with BrowserSession(self.config, run_dir) as browser:
            cycle = 0
            while True:
                cycle += 1
                if max_cycles and cycle > max_cycles:
                    state["status"] = "bounded_test_limit_reached"
                    break
                stop_path = run_dir / str(self._cfg("stop_file_name", "STOP_HIP_OPERATOR") or "STOP_HIP_OPERATOR")
                if stop_path.exists():
                    state["status"] = "stopped_by_operator"
                    break

                cycle_dir = run_dir / "cycles" / f"cycle_{cycle:04d}"
                cycle_dir.mkdir(parents=True, exist_ok=True)
                plan = self.planner.plan(
                    normalized_task,
                    input_json=runtime_input,
                    input_root=input_root,
                    start_url=start_url,
                    deep_learn=bool(deep_learn),
                )
                safe_write_json(cycle_dir / "plan.json", plan)
                if not plan.get("pass"):
                    result = {"pass": False, "error": str(plan.get("reason") or "planning_failed"), "plan": plan}
                else:
                    result = await self.executor.execute(
                        task=normalized_task,
                        plan=plan,
                        run_dir=cycle_dir,
                        allow_portal_mutation=allow_portal_mutation,
                        confirmation=confirmation,
                        browser_override=browser,
                    )
                final_result = result
                success = bool(result.get("pass"))
                reward = float((((result.get("replay_policy") or {}).get("episode") or {}).get("score")) or (1.0 if success else 0.0))
                improvement = self._record_cycle_improvement(
                    success=success, reward=reward, reason=f"goal_cycle_{cycle}", run_id=run_id,
                )
                cycle_row = {
                    "cycle": cycle,
                    "at": utc_now(),
                    "pass": success,
                    "reward": reward,
                    "error": mask_sensitive_string(str(result.get("error") or ""))[:1000],
                    "execution_mode": (plan.get("execution_mode") if isinstance(plan, Mapping) else ""),
                    "rsi": improvement,
                }
                state["cycles"].append(mask_sensitive_data(cycle_row))
                safe_write_json(run_dir / "persistent_operator_state.json", state)

                if success:
                    failed_streak = 0
                    if bool(self._cfg("require_final_human_confirmation", True)):
                        request = self.human_reviews.create_or_update(
                            run_id=run_id,
                            phase=f"operator_{family}_final_{cycle}",
                            phase_display=f"Final human review — {FAMILY_LABELS.get(family, 'HIP Portal')}",
                            attempt=cycle,
                            automated_judge={"pass": True, "status": "automated_goal_verified", "deterministic_judge": {"pass": True}},
                            verification={"status": "verified"},
                            exact_checkpoint={"pass": True},
                            model_consensus={},
                            reason="Automated goal completed. Final human acceptance is required before the goal is considered learned and complete.",
                        )
                        state["status"] = "waiting_for_final_human_review"
                        state["human_review_request_id"] = request.get("request_id")
                        safe_write_json(run_dir / "persistent_operator_state.json", state)
                        human = await self._wait_for_resolution(str(request.get("request_id") or ""), run_dir=run_dir)
                        cycle_row["human_final"] = mask_sensitive_data(human)
                        effective = str(human.get("effective_human_verdict") or human.get("human_verdict") or "")
                        if effective == "pass":
                            self._record_cycle_improvement(success=True, reward=1.0, reason="human_final_pass", run_id=run_id)
                            state["status"] = "complete"
                            state["completed_at"] = utc_now()
                            state["final_human_verdict"] = "pass"
                            break
                        if effective in {"stopped", "timeout"}:
                            state["status"] = effective
                            break
                        # Human says correction is still needed. Penalize any recipe
                        # that was just reinforced by the automated pass, then keep
                        # learning on the same goal/browser.
                        recipe_learning = result.get("deterministic_recipe_learning") if isinstance(result.get("deterministic_recipe_learning"), Mapping) else {}
                        recipe_id = str(recipe_learning.get("recipe_id") or "")
                        if recipe_id:
                            try:
                                self.recipes.record_failure(recipe_id, reason="human_final_needs_correction")
                            except Exception:
                                pass
                        self._record_cycle_improvement(success=False, reward=0.0, reason="human_final_needs_correction", run_id=run_id)
                        failed_streak = human_after
                        state["status"] = "human_requested_correction_reentering_learning"
                        safe_write_json(run_dir / "persistent_operator_state.json", state)
                        continue
                    state["status"] = "complete"
                    state["completed_at"] = utc_now()
                    break

                failed_streak += 1
                state["status"] = "learning_and_self_healing"
                if failed_streak >= human_after or bool(((result.get("trace_self_repair") or {}).get("human_required")) if isinstance(result.get("trace_self_repair"), Mapping) else False):
                    recovery = self.human_reviews.create_recovery_request(
                        run_id=run_id,
                        phase=f"operator_{family}",
                        phase_display=f"HIP Operator Recovery — {FAMILY_LABELS.get(family, 'HIP Portal')}",
                        recovery_round=cycle,
                        reason=str(result.get("error") or "Automated learning/self-heal has not yet reached the goal. Inspect/teach the live portal, then resume."),
                        exact_checkpoint={"pass": False},
                        automated_judge={"pass": False, "status": "goal_not_yet_verified"},
                        verification={"status": "incomplete"},
                    )
                    state["status"] = "waiting_for_human_recovery_with_browser_open"
                    state["human_recovery_request_id"] = recovery.get("request_id")
                    safe_write_json(run_dir / "persistent_operator_state.json", state)
                    resolved = await self._wait_for_resolution(str(recovery.get("request_id") or ""), run_dir=run_dir)
                    if str(resolved.get("effective_human_verdict") or "") in {"stopped", "timeout"}:
                        state["status"] = str(resolved.get("effective_human_verdict"))
                        break
                    failed_streak = 0
                    state["status"] = "human_recovery_resolved_rechecking_same_goal"
                    safe_write_json(run_dir / "persistent_operator_state.json", state)
                    continue

                # No terminal failure here: failed cycles are evidence for the next
                # policy/model/skill iteration and the same browser remains alive.
                await asyncio.sleep(0.2)

            try:
                await browser.flush_logs()
            except Exception:
                pass

        state["knowledge_after"] = self.knowledge_snapshot(family)
        state["final_result_pass"] = bool(final_result.get("pass"))
        safe_write_json(run_dir / "persistent_operator_state.json", state)
        safe_write_json(run_dir / "operator_knowledge_after.json", state["knowledge_after"])
        outcome = {
            "schema_version": SCHEMA,
            "pass": state.get("status") == "complete",
            "status": state.get("status"),
            "run_id": run_id,
            "task": mask_sensitive_string(task),
            "family": family,
            "operation": operation,
            "cycles": len(state.get("cycles") or []),
            "final_human_verdict": state.get("final_human_verdict", ""),
            "state_path": str(run_dir / "persistent_operator_state.json"),
            "knowledge_before": str(run_dir / "operator_knowledge_before.json"),
            "knowledge_after": str(run_dir / "operator_knowledge_after.json"),
            "open_ended_until_success_or_stop": max_cycles == 0,
            "browser_persistent_across_cycles": True,
            "values_stored_in_memory": False,
        }
        safe_write_json(run_dir / "persistent_operator_result.json", outcome)
        return mask_sensitive_data(outcome)
