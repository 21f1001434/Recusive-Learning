from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Sequence

from .security import mask_sensitive_data


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _memory_action_stats(matches: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    stats: Dict[str, Dict[str, float]] = {}
    for row in matches or []:
        action = str(row.get("selected_recovery") or "execute_bound_action")
        item = stats.setdefault(action, {"visits": 0.0, "reward": 0.0, "successes": 0.0, "failures": 0.0})
        item["visits"] += 1.0
        item["reward"] += float(row.get("reward") or 0.0)
        if bool(row.get("success")):
            item["successes"] += 1.0
        else:
            item["failures"] += 1.0
    return stats


def _ucb_score(base: float, action: str, stats: Dict[str, Dict[str, float]], total: float) -> float:
    item = stats.get(action) or {"visits": 0.0, "reward": 0.0, "successes": 0.0, "failures": 0.0}
    visits = float(item.get("visits") or 0.0)
    average = float(item.get("reward") or 0.0) / visits if visits else 0.0
    success_rate = float(item.get("successes") or 0.0) / visits if visits else 0.0
    exploration = math.sqrt(math.log(total + 2.0) / (visits + 1.0))
    # AgentQ-style reward exploitation with bounded UCB exploration. Successful
    # transitions are meaningfully preferred; repeatedly losing transitions are
    # suppressed instead of being selected forever just because their base score
    # is high.
    return base + 0.34 * average + 0.16 * success_rate + 0.08 * exploration


class HIPActionModel:
    """Deterministic actor/critic policy inspired by the course AgentQ architecture.

    It performs bounded candidate search, but never explores destructive actions on
    a live HIP form.  Candidate generation is constrained by the form policy.
    """

    SAFE_RECOVERY_ACTIONS = (
        "execute_bound_action",
        "verify_existing_value",
        "wait_for_rerender",
        "rebind_after_rerender",
        "reveal_structural_parent",
        "recommit_parent_event",
        "recover_stale_overlay",
        "stop_surface_lost",
        "stop_ambiguous_binding",
    )

    def plan(
        self,
        *,
        node: Dict[str, Any],
        representation: Dict[str, Any],
        binding: Dict[str, Any],
        surface_gate: Dict[str, Any] | None = None,
        memory_matches: Sequence[Dict[str, Any]] | None = None,
        interaction_state: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        surface_gate = surface_gate if isinstance(surface_gate, dict) else {}
        interaction_state = interaction_state if isinstance(interaction_state, dict) else {}
        matches = list(memory_matches or [])
        stats = _memory_action_stats(matches)
        total = sum(v.get("visits", 0.0) for v in stats.values())
        replay_mode = str(node.get("_replay_policy_mode") or "exploration")
        replay_preferred = {str(x) for x in node.get("_replay_preferred_actions") or [] if str(x)}
        candidates: List[Dict[str, Any]] = []

        def add(action: str, base: float, reason: str, *, risk: str = "low", allowed: bool = True) -> None:
            if action not in self.SAFE_RECOVERY_ACTIONS:
                return
            history = stats.get(action) or {}
            visits = float(history.get("visits") or 0.0)
            successes = float(history.get("successes") or 0.0)
            reward_sum = float(history.get("reward") or 0.0)
            avg_reward = reward_sum / visits if visits else 0.0
            learned_loser = bool(visits >= 3 and successes == 0 and avg_reward <= -0.35)
            policy_bonus = 0.0
            if replay_mode == "exploitation":
                policy_bonus += 0.18 if action in replay_preferred else -0.03
            elif replay_mode == "hybrid":
                policy_bonus += 0.10 if action in replay_preferred else (0.04 if visits == 0 else 0.0)
            else:  # explicit exploration policy
                policy_bonus += 0.08 if visits == 0 else 0.02 / (visits + 1.0)
            candidates.append({
                "action": action,
                "score": round(_ucb_score(base, action, stats, total) + policy_bonus, 6),
                "base_score": base,
                "reason": reason + ("; suppressed by repeated negative trajectory reward" if learned_loser else ""),
                "risk": risk,
                "allowed": bool(allowed and not learned_loser),
                "historical_visits": int(visits),
                "historical_successes": int(successes),
                "historical_average_reward": round(avg_reward, 4),
                "learned_loser": learned_loser,
                "replay_policy_mode": replay_mode,
                "replay_policy_bonus": round(policy_bonus, 6),
            })

        surface_fatal = bool(surface_gate.get("fatal"))
        surface_pass = bool(surface_gate.get("pass", not surface_fatal))
        resolved = bool(binding.get("resolved"))
        reason = str(binding.get("reason") or "")
        control = binding.get("control") if isinstance(binding.get("control"), dict) else {}
        target_interactable = bool(control.get("interactable", False))
        has_dependencies = bool(node.get("depends_on"))

        if surface_fatal or not surface_pass:
            add("stop_surface_lost", 1.2, "active Create/Wizard surface is not proven", risk="none")
        if resolved:
            add("execute_bound_action", 1.0, "unique semantic binding is available")
            add("verify_existing_value", 0.88, "the specialized phase filler may already have committed the value")
            if not target_interactable and str(node.get("action")) != "verify_only":
                add("recover_stale_overlay", 0.74, "bound control exists but is not currently hit-testable")
                add("wait_for_rerender", 0.64, "Angular/DDS may still be settling")
        else:
            if "ambiguous" in reason:
                add("stop_ambiguous_binding", 1.05, "multiple controls remain plausible; typing is unsafe", risk="none")
            if has_dependencies:
                add("recommit_parent_event", 0.84, "conditional child may require a real parent event")
                add("reveal_structural_parent", 0.78, "target may be hidden under a structural parent")
            add("rebind_after_rerender", 0.72, "generated DDS controls may have been replaced")
            add("wait_for_rerender", 0.62, "live form may still be rendering")

        if interaction_state.get("blockedByOverlay") or interaction_state.get("hitTestPass") is False:
            add("recover_stale_overlay", 0.92, "presentation overlay blocks an existing target")

        candidates.sort(key=lambda item: (bool(item.get("allowed")), float(item.get("score") or 0)), reverse=True)
        selected = next((item for item in candidates if item.get("allowed")), {
            "action": "stop_ambiguous_binding",
            "score": 0.0,
            "reason": "no safe action candidate",
            "risk": "none",
            "allowed": True,
        })
        return mask_sensitive_data({
            "schema_version": "hip.action-model-plan.v1",
            "field_key": node.get("field_key"),
            "node_id": node.get("node_id"),
            "selected_action": selected.get("action"),
            "selected_reason": selected.get("reason"),
            "selected_score": selected.get("score"),
            "selected_control_identity": binding.get("selected_identity") or "",
            "binding_score": binding.get("best_score"),
            "binding_margin": binding.get("score_margin"),
            "candidate_actions": candidates,
            "memory_match_count": len(matches),
            "reward_policy": "agentq_bounded_ucb_success_failure_memory_v2",
            "replay_policy_mode": replay_mode,
            "replay_preferred_actions": sorted(replay_preferred),
            "negative_action_suppression": True,
            "live_search_bounded": True,
            "destructive_actions_considered": False,
        })

    def critique(
        self,
        *,
        node: Dict[str, Any],
        action_plan: Dict[str, Any],
        outcome: Dict[str, Any],
    ) -> Dict[str, Any]:
        success = bool(outcome.get("success"))
        reason = str(outcome.get("reason") or "")
        proof = outcome.get("transaction_proof") if isinstance(outcome.get("transaction_proof"), dict) else {}
        stability = proof.get("stability") if isinstance(proof.get("stability"), dict) else {}
        changes = proof.get("protected_state_changes") if isinstance(proof.get("protected_state_changes"), list) else []
        event_proof = proof.get("explicit_event_proof") if isinstance(proof.get("explicit_event_proof"), dict) else {}
        score = 1.0 if success else 0.0
        issues: List[str] = []
        if not stability.get("stable", success):
            score -= 0.25
            issues.append("unstable_post_action_state")
        if changes:
            score -= 0.7
            issues.append("previously_committed_fields_changed")
        if str(node.get("action")) in {"select_single", "select_multi", "select_radio", "toggle"} and not event_proof.get("pass", success):
            score -= 0.25
            issues.append("explicit_browser_event_not_proven")
        if "AMBIGUOUS" in reason:
            issues.append("ambiguous_binding")
        if "SURFACE_LOST" in reason:
            issues.append("surface_lost")
        if "VALIDATION" in reason:
            issues.append("blocking_validation")
        recommendation = "continue" if success and not issues else "rebind_and_retry_field"
        if changes:
            recommendation = "stop_unintended_mutation"
        elif "surface_lost" in issues:
            recommendation = "stop_preserve_surface_evidence"
        elif "ambiguous_binding" in issues:
            recommendation = "stop_ambiguous_binding"
        return mask_sensitive_data({
            "schema_version": "hip.action-model-critic.v1",
            "node_id": node.get("node_id"),
            "field_key": node.get("field_key"),
            "pass": bool(success and not changes),
            "critic_score": round(max(-1.0, min(1.0, score)), 4),
            "issues": issues,
            "recommendation": recommendation,
            "action_plan": action_plan,
        })



def build_phase_task_plan(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Compile the course planner concept into an auditable HIP task list.

    Each business field is one sequential task followed by exact verification.
    Dependencies are preserved; mutation actions are never generated.
    """
    tasks: List[Dict[str, Any]] = []
    for index, node in enumerate(graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else [], start=1):
        if not isinstance(node, dict) or node.get("expected_value") in (None, "", []):
            continue
        tasks.append({
            "id": index,
            "node_id": node.get("node_id"),
            "field_key": node.get("field_key"),
            "section": node.get("section"),
            "row_kind": node.get("row_kind"),
            "row_index": node.get("row_index"),
            "action": node.get("action"),
            "depends_on": list(node.get("depends_on") or []),
            "verification": [
                "exact target value",
                "stable Angular/DDS state",
                "no previously committed field changed",
            ],
        })
    return mask_sensitive_data({
        "schema_version": "hip.agentq-phase-plan.v1",
        "phase": graph.get("phase"),
        "graph_id": graph.get("graph_id"),
        "strategy": "hierarchical sequential plan with verification after every task",
        "tasks": tasks,
        "task_count": len(tasks),
        "parallel_execution": False,
        "destructive_actions": [],
    })

def course_architecture_manifest() -> Dict[str, Any]:
    return {
        "schema_version": "hip.course-architecture-integration.v1",
        "web_representation_model": {
            "implemented": True,
            "inputs": ["DOM controls", "accessibility semantics", "Angular/DDS state", "surface gate", "events", "network/console signatures"],
            "output": "value-free structural/state embedding",
        },
        "action_model": {
            "implemented": True,
            "method": "bounded candidate search with trajectory priors and deterministic safety constraints",
        },
        "planner_actor_critic": {
            "implemented": True,
            "planner": "one node and its dependencies at a time",
            "actor": "Playwright MCP / deterministic DDS driver",
            "critic": "exact transaction proof plus committed-field protection",
        },
        "memory_personalization_engine": {
            "implemented": True,
            "stores": "validated topology, action identity and success/failure trajectories",
            "stores_customer_values": False,
        },
        "form_api_intelligence": {
            "implemented": True,
            "form_open_contract_capture": True,
            "ui_fill_contract_capture": True,
            "exact_submit_capture": "Playwright route abort before backend delivery",
            "exports": ["redacted API catalog", "OpenAPI", "Postman", "input-to-UI-to-API crosswalk"],
            "execution_modes": ["capture", "dry_run", "validate", "write_with_two_key_confirmation"],
            "endpoint_invention": False,
            "payload_key_invention": False,
            "customer_values_in_memory": False,
        },
        "multion_dependency": False,
        "browser_tools": [
            "Playwright MCP", "Chrome DevTools MCP", "HIP Intelligence MCP",
            "Browser Use same-CDP perception", "AutoWebGLM primary browser-decision protocol",
            "LangChain Playwright Browser Toolkit (read-only)", "PyAutoGUI MCP primary visible-desktop interaction"
        ],
    }
