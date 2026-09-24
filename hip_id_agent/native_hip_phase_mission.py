from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .capability_graph import HIPCapabilityGraph
from .dummy_fill_e2e import FullDummyFillE2EFlow, FullDummyFillOptions, PHASE_SEQUENCE, validate_live_input_contract
from .full_deep_learning import FAMILY_SEQUENCE, FullHIPDeepLearningMission, HIPCapabilityCertifier
from .models import RunContext
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


class NativeHIPPhaseMissionCoordinator:
    """Authoritative phase-native learn -> exploit -> verify coordinator.

    The universal agent remains a fallback for unknown/future portal areas. For the
    five known HIP families this coordinator deliberately prefers their dedicated
    KB/stateful runtimes because they understand nested rows, Angular/DDS controls,
    TP wizard expansion and BizFlow multitab/routing semantics.
    """

    FAMILY_TO_PHASES = {
        "data_maps": ("data_map",),
        "document_types": ("source_document_type", "target_document_type"),
        "rules": ("rule",),
        "transport_profiles": ("source_transport_profile", "target_transport_profile"),
        "bizflows": ("biz_flow",),
    }

    def __init__(self, config: Any, graph: HIPCapabilityGraph) -> None:
        self.config = config
        self.graph = graph

    @staticmethod
    def recognize(input_json: str | Path) -> Dict[str, Any]:
        path = Path(input_json)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"recognized": False, "reason": mask_sensitive_string(str(exc))}
        if not isinstance(payload, dict):
            return {"recognized": False, "reason": "input root is not an object"}
        contract = validate_live_input_contract(payload)
        phase_coverage = contract.get("phase_coverage") if isinstance(contract.get("phase_coverage"), Mapping) else {}
        selected = [p for p in PHASE_SEQUENCE if isinstance(phase_coverage.get(p), Mapping) and int((phase_coverage.get(p) or {}).get("input_leaf_count") or 0) > 0]
        # validate_live_input_contract can fail because one downstream phase is absent;
        # recognition only requires at least one canonical HIP phase payload.
        return {"recognized": bool(selected), "selected_phases": selected, "input_contract": contract}

    def learning_readiness(self, families: Sequence[str]) -> Dict[str, Any]:
        certifier = HIPCapabilityCertifier(self.graph)
        rows: Dict[str, Any] = {}
        missing: List[str] = []
        for family in families:
            row = certifier.certify_family(family)
            rows[family] = row
            if not bool(row.get("operational_ready")):
                missing.append(family)
        return {"ready": not missing, "missing_families": missing, "families": rows, "manifest": self.graph.manifest()}

    async def run(
        self, *, parent_run_id: str, run_dir: Path, input_json: str | Path,
        deep_learn: bool = True, selected_phases: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        recognition = self.recognize(input_json)
        phases = [p for p in PHASE_SEQUENCE if not selected_phases or p in set(selected_phases)]
        if recognition.get("selected_phases"):
            phases = [p for p in phases if p in set(recognition.get("selected_phases") or phases)] or phases
        families = [family for family in FAMILY_SEQUENCE if any(p in phases for p in self.FAMILY_TO_PHASES[family])]
        readiness_before = self.learning_readiness(families)
        safe_write_json(run_dir / "native_phase_learning_readiness_before.json", readiness_before)

        learning: Dict[str, Any] = {"status": "reused_existing_knowledge", "selected_families": []}
        missing = list(readiness_before.get("missing_families") or [])
        learning_review_phases = [
            phase
            for family in missing
            for phase in self.FAMILY_TO_PHASES.get(family, ())
            if phase in phases
        ] if deep_learn else []
        if deep_learn and missing:
            learn_dir = run_dir / "native_deep_learning"
            ctx = RunContext(
                run_id=f"{parent_run_id}-NATIVE-LEARN", customer="production", partner_query="", system_query="",
                run_dir=learn_dir, screenshots_dir=learn_dir / "screenshots",
            )
            try:
                learning = await FullHIPDeepLearningMission(self.config, capability_graph=self.graph).run(
                    ctx=ctx, input_json=input_json, families=missing, continue_on_family_failure=True,
                )
            except Exception as exc:
                learning = {"status": "learning_failed", "error": mask_sensitive_string(str(exc)), "selected_families": missing}
        self.graph.save()
        readiness_after = self.learning_readiness(families)
        safe_write_json(run_dir / "native_phase_learning_readiness_after.json", readiness_after)

        # Authoritative execution uses the dedicated phase runtimes, not the generic
        # universal form compiler. Full KB crawling is not repeated once the graph is
        # ready; current live controls are still re-proved on every run.
        exec_dir = run_dir / "native_phase_execution"
        ctx = RunContext(
            run_id=f"{parent_run_id}-NATIVE-EXEC", customer="production", partner_query="", system_query="",
            run_dir=exec_dir, screenshots_dir=exec_dir / "screenshots",
        )
        options = FullDummyFillOptions(
            full_kb_context=False,
            phases=tuple(phases),
            write_heavy_evidence=False,
            save_replay_blueprint=True,
            strict_replication=True,
            section_judge=True,
            require_text_judge=True,
            require_vision_judge=True,
            section_judge_max_repairs=2,
            section_judge_fail_closed=True,
            portal_brain_enabled=True,
            runtime_self_heal_enabled=True,
            runtime_self_heal_max_phase_attempts=5,
            runtime_self_heal_until_complete=True,
            forensic_evidence=True,
            continue_after_phase_block=False,
            learning_review_phases=tuple(learning_review_phases),
        )
        try:
            execution = await FullDummyFillE2EFlow(self.config, options).run(ctx, input_json=str(input_json))
        except Exception as exc:
            execution = {"status": "execution_exception", "pass": False, "error": mask_sensitive_string(str(exc))}

        mission = execution.get("mission") if isinstance(execution, Mapping) and isinstance(execution.get("mission"), Mapping) else {}
        application_complete = bool(mission.get("application_complete"))
        if not mission and isinstance(execution, Mapping):
            application_complete = str(execution.get("overall_status") or "").lower() in {"pass", "complete"}
        result = mask_sensitive_data({
            "schema_version": "hip.native-phase-mission.v1",
            "pass": application_complete,
            "status": "complete" if application_complete else "blocked_or_incomplete",
            "selected_phases": phases,
            "selected_families": families,
            "learning_review_phases": learning_review_phases,
            "learning": learning,
            "learning_readiness_before": readiness_before,
            "learning_readiness_after": readiness_after,
            "execution": execution,
            "execution_engine": "phase_native_kb_flows",
            "universal_agent_role": "fallback_for_unknown_or_drifted_capabilities",
            "knowledge_policy": "learn_missing_family_once_then_exploit_verified_capability_graph_and_portal_brain_with_live_reproof",
            "values_stored": False,
        })
        safe_write_json(run_dir / "native_hip_phase_mission_summary.json", result)
        return result
