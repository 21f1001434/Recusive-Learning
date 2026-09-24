from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple

from .action_model import HIPActionModel, build_phase_task_plan, course_architecture_manifest
from .hip_form_catalog import phase_to_form_family
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .trajectory_memory import TrajectoryMemory
from .replay_policy import replay_policy_engine_from_config
from .web_representation import build_web_representation, representation_drift


class HIPAgentQController:
    """HIP-specific planner/actor/critic controller.

    It does not replace the deterministic phase engine. It supplies state
    representation, value-free trajectory priors, bounded action planning and
    post-action critique to that engine.
    """

    def __init__(self, *, memory_root: str | Path, run_dir: str | Path, config: Any = None) -> None:
        self.memory_root = Path(memory_root)
        self.run_dir = Path(run_dir)
        self.config = config
        self.memory = TrajectoryMemory(self.memory_root / "action_trajectories")
        self.replay_policy = replay_policy_engine_from_config(config) if config is not None else None
        self.action_model = HIPActionModel()
        self.mcp_backend = None
        self._phase_representations: Dict[str, Dict[str, Any]] = {}
        self._sequence = 0
        safe_write_json(self.run_dir / "agentq_course_architecture_manifest.json", course_architecture_manifest())

    async def start(self) -> Dict[str, Any]:
        mcp_cfg = getattr(self.config, "mcp", None)
        if not bool(getattr(mcp_cfg, "use_hip_intelligence_mcp", True)):
            return {"status": "disabled"}
        try:
            from .hip_intelligence_mcp import HIPIntelligenceMCPBackend
            self.mcp_backend = HIPIntelligenceMCPBackend.from_config(
                self.config,
                run_dir=self.run_dir / "mcp_runtime" / "hip_intelligence",
                memory_dir=self.memory_root / "action_trajectories",
            )
            await self.mcp_backend.start()
            return {"status": "started", "tools": sorted(self.mcp_backend.client.tools)}
        except Exception as exc:
            self.mcp_backend = None
            required = bool(getattr(mcp_cfg, "hip_intelligence_mcp_required", False))
            status = {"status": "error_fail_open" if not required else "error", "error": mask_sensitive_string(str(exc))}
            if required:
                raise RuntimeError(f"Required HIP Intelligence MCP failed: {status}") from exc
            return status

    async def close(self) -> None:
        if self.mcp_backend is not None:
            try:
                await self.mcp_backend.close()
            except Exception:
                pass
            self.mcp_backend = None

    def _phase_dir(self, page: Any, phase: str) -> Path:
        session = getattr(page, "_hip_browser_session", None)
        root = Path(getattr(session, "run_dir", self.run_dir))
        out = root / "agentq_runtime"
        out.mkdir(parents=True, exist_ok=True)
        return out

    async def represent(
        self,
        *,
        page: Any,
        phase: str,
        controls: Sequence[Dict[str, Any]],
        surface_gate: Dict[str, Any] | None = None,
        transition: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        session = getattr(page, "_hip_browser_session", None)
        console_signatures = []
        for item in list(getattr(session, "console_messages", []) or [])[-30:]:
            if not isinstance(item, dict):
                continue
            level = str(item.get("type") or item.get("level") or "log").lower()
            text = str(item.get("text") or item.get("message") or "").lower()
            category = next((name for name in ("timeout", "validation", "unauthorized", "forbidden", "network", "error") if name in text), "other")
            console_signatures.append(f"{level}:{category}")
        network_signatures = []
        for item in list(getattr(session, "network_tab_events", []) or [])[-40:]:
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            if not isinstance(item, dict):
                continue
            method = str(item.get("method") or item.get("request_method") or "")
            url = str(item.get("url") or item.get("request_url") or "").split("?", 1)[0]
            import re as _re
            url = _re.sub(r"/[0-9]+(?=/|$)", "/{id}", url)
            url = _re.sub(r"/[0-9a-f]{8}-[0-9a-f-]{27,}(?=/|$)", "/{uuid}", url, flags=_re.I)
            status = str(item.get("status") or item.get("status_code") or "")
            if method or url or status:
                network_signatures.append(f"{method}:{url}:{status}")
        payload = {
            "phase": phase,
            "url": str(getattr(page, "url", "") or ""),
            "controls": list(controls or []),
            "surface_gate": surface_gate or {},
            "dom_transition": transition or {},
            "console_signatures": console_signatures,
            "network_signatures": network_signatures,
        }
        if self.mcp_backend is not None:
            try:
                result = await self.mcp_backend.build_representation(payload)
                if isinstance(result, dict) and result.get("structural_fingerprint"):
                    return result
            except Exception:
                pass
        return build_web_representation(**payload)

    async def begin_phase(
        self,
        *,
        page: Any,
        phase: str,
        controls: Sequence[Dict[str, Any]],
        surface_gate: Dict[str, Any] | None = None,
        graph: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        representation = await self.represent(page=page, phase=phase, controls=controls, surface_gate=surface_gate)
        previous = self._phase_representations.get(phase)
        drift = representation_drift(representation, previous) if previous else {"drift_detected": False, "first_observation": True}
        self._phase_representations[phase] = representation
        phase_plan = build_phase_task_plan(graph or {}) if isinstance(graph, dict) else {}
        payload = {"representation": representation, "drift": drift, "phase_plan": phase_plan}
        safe_write_json(self._phase_dir(page, phase) / "phase_initial_web_representation.json", payload)
        if phase_plan:
            safe_write_json(self._phase_dir(page, phase) / "hierarchical_phase_plan.json", phase_plan)
        return payload

    async def plan_node(
        self,
        *,
        page: Any,
        phase: str,
        node: Dict[str, Any],
        controls: Sequence[Dict[str, Any]],
        binding: Dict[str, Any],
        surface_gate: Dict[str, Any] | None = None,
        interaction_state: Dict[str, Any] | None = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        representation = await self.represent(page=page, phase=phase, controls=controls, surface_gate=surface_gate)
        family = phase_to_form_family(phase)
        matches = self.memory.retrieve(
            phase=phase, family=family, node=node, representation=representation, limit=8
        )
        node_signature = "|".join([
            str(node.get("section") or ""), str(node.get("row_kind") or ""),
            str(node.get("row_index") if node.get("row_index") is not None else ""),
            str(node.get("field_key") or ""), str(node.get("action") or ""),
        ])
        policy_hint = (
            self.replay_policy.action_policy(memory_matches=matches, node_signature=node_signature)
            if self.replay_policy is not None and bool(getattr(getattr(self.config, "replay_policy", None), "enabled", True))
            else {"mode": "exploration", "preferred_actions": [], "reason": "replay_policy_disabled"}
        )
        preferred = self.memory.preferred_control_identities(matches)
        enriched = dict(node)
        enriched["_preferred_control_identities"] = preferred[:5]
        enriched["_trajectory_memory_match_count"] = len(matches)
        enriched["_replay_policy_mode"] = str(policy_hint.get("mode") or "exploration")
        enriched["_replay_preferred_actions"] = list(policy_hint.get("preferred_actions") or [])
        enriched["_replay_policy_support"] = int(policy_hint.get("support") or 0)
        local_plan = self.action_model.plan(
            node=enriched,
            representation=representation,
            binding=binding,
            surface_gate=surface_gate,
            memory_matches=matches,
            interaction_state=interaction_state,
        )
        plan = local_plan
        if self.mcp_backend is not None:
            try:
                remote = await self.mcp_backend.plan_action({
                    "node": enriched,
                    "representation": representation,
                    "binding": binding,
                    "surface_gate": surface_gate or {},
                    "memory_matches": matches,
                    "interaction_state": interaction_state or {},
                })
                if isinstance(remote, dict) and remote.get("selected_action"):
                    remote_action = str(remote.get("selected_action") or "")
                    local_candidates = {
                        str(item.get("action") or ""): item
                        for item in (local_plan.get("candidate_actions") or [])
                        if isinstance(item, dict)
                    }
                    local_candidate = local_candidates.get(remote_action)
                    # Remote AgentQ/MCP is advisory. It cannot resurrect an action
                    # that local trajectory reward has learned is a repeated loser,
                    # nor can it expand the deterministic safe action surface.
                    if local_candidate and bool(local_candidate.get("allowed", True)):
                        plan = {**local_plan, **remote}
                        plan["candidate_actions"] = local_plan.get("candidate_actions") or []
                        plan["reward_policy"] = local_plan.get("reward_policy")
                        plan["mcp_plan_advisory"] = "accepted_with_local_reward_gate"
                    else:
                        plan = dict(local_plan)
                        plan["mcp_plan_advisory"] = "rejected_by_local_reward_or_safe_action_gate"
                        plan["rejected_remote_action"] = remote_action
            except Exception:
                pass
        self._sequence += 1
        safe_write_json(self._phase_dir(page, phase) / f"action_model_plan_{self._sequence:04d}.json", {
            "node": {k: v for k, v in enriched.items() if k != "expected_value"},
            "representation": representation,
            "memory_matches": matches,
            "replay_policy": policy_hint,
            "plan": plan,
        })
        return enriched, plan, representation

    async def record_outcome(
        self,
        *,
        page: Any,
        phase: str,
        node: Dict[str, Any],
        representation_before: Dict[str, Any],
        action_plan: Dict[str, Any],
        outcome: Dict[str, Any],
        controls_after: Sequence[Dict[str, Any]],
        surface_gate: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        representation_after = await self.represent(
            page=page, phase=phase, controls=controls_after, surface_gate=surface_gate
        )
        critic = self.action_model.critique(node=node, action_plan=action_plan, outcome=outcome)
        record = {}
        if self.mcp_backend is not None:
            try:
                record = await self.mcp_backend.record_outcome({
                    "phase": phase,
                    "family": phase_to_form_family(phase),
                    "node": node,
                    "representation_before": representation_before,
                    "action_plan": action_plan,
                    "outcome": outcome,
                    "representation_after": representation_after,
                })
            except Exception:
                record = {}
        if not isinstance(record, dict) or not record:
            record = self.memory.record(
                phase=phase,
                family=phase_to_form_family(phase),
                node=node,
                representation_before=representation_before,
                action_plan=action_plan,
                outcome=outcome,
                representation_after=representation_after,
            )
        self._sequence += 1
        payload = {"critic": critic, "trajectory_record": record, "representation_after": representation_after}
        safe_write_json(self._phase_dir(page, phase) / f"action_critic_{self._sequence:04d}.json", payload)
        return mask_sensitive_data(payload)

    async def finalize_phase(
        self,
        *,
        page: Any,
        phase: str,
        controls: Sequence[Dict[str, Any]],
        success: bool,
        surface_gate: Dict[str, Any] | None = None,
        graph: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        representation = await self.represent(page=page, phase=phase, controls=controls, surface_gate=surface_gate)
        initial = self._phase_representations.get(phase) or {}
        payload = {
            "schema_version": "hip.agentq-phase-summary.v1",
            "phase": phase,
            "success": bool(success),
            "initial_fingerprint": initial.get("state_fingerprint"),
            "final_fingerprint": representation.get("state_fingerprint"),
            "structural_drift": representation_drift(representation, initial) if initial else {},
            "values_stored": False,
        }
        safe_write_json(self._phase_dir(page, phase) / "phase_agentq_summary.json", payload)
        return payload
