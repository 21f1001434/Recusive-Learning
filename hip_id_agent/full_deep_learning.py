from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .bizflow_deep_discovery import BizFlowDeepDiscoveryFlow
from .capability_graph import HIPCapabilityGraph
from .datamap_deep_discovery import DataMapDeepDiscoveryFlow
from .doctype_deep_discovery import DocumentTypeDeepDiscoveryFlow
from .dummy_fill_e2e import validate_live_input_contract
from .models import RunContext, utc_now
from .rules_deep_discovery import RuleDeepDiscoveryFlow
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .transport_profile_deep_discovery import TransportProfileDeepDiscoveryFlow


FAMILY_SEQUENCE: Sequence[str] = (
    "data_maps",
    "document_types",
    "rules",
    "transport_profiles",
    "bizflows",
)

SUMMARY_FILENAMES: Dict[str, str] = {
    "data_maps": "datamap_deep_discovery_summary.json",
    "document_types": "doctype_deep_discovery_summary.json",
    "rules": "rules_deep_discovery_summary.json",
    "transport_profiles": "transport_profile_deep_discovery_summary.json",
    "bizflows": "bizflow_deep_discovery_summary.json",
}

EXPECTED_ACTIONS: Dict[str, Sequence[str]] = {
    "data_maps": ("edit", "clone", "migrate", "deploy", "delete"),
    "document_types": ("edit", "clone", "migrate", "deploy", "delete"),
    "rules": ("edit", "clone", "migrate", "deploy", "delete"),
    "transport_profiles": ("edit", "clone", "migrate", "deploy", "delete"),
    "bizflows": ("edit", "clone", "migrate", "deploy", "delete"),
}

REQUIRED_REPLAY_PROFILES: Dict[str, Sequence[str]] = {
    "data_maps": (),  # at least one verified entity_* read/draft replay is required.
    "document_types": ("create_source_document_type", "create_target_document_type"),
    "rules": ("create_rule",),
    "transport_profiles": ("create_source_transport_profile", "create_target_transport_profile"),
    "bizflows": ("create_biz_flow",),
}


class HIPCapabilityCertifier:
    """Derive a fail-closed readiness verdict from the persistent capability graph.

    The certifier deliberately separates *operational readiness* from *coverage
    completeness*. A tenant may not expose every permission-dependent row action,
    but that must be reported as a gap instead of being silently treated as learned.
    """

    SCHEMA = "hip.full-deep-capability-certification.v1"

    def __init__(self, graph: HIPCapabilityGraph) -> None:
        self.graph = graph

    @staticmethod
    def _norm(value: Any) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")

    def _family_caps(self, family: str) -> List[Dict[str, Any]]:
        return self.graph.query_capabilities(page_family=family)

    def _family_apis(self, family: str) -> List[Dict[str, Any]]:
        out = []
        for row in self.graph.data.get("api_contracts", {}).values():
            if family in (row.get("page_families") or []):
                out.append(dict(row))
        return out

    def _family_replays(self, family: str) -> List[Dict[str, Any]]:
        return self.graph.replay_profiles(page_family=family, verified_only=False)

    def _summary_safety(self, summary: Mapping[str, Any]) -> Dict[str, Any]:
        text = json.dumps(mask_sensitive_data(dict(summary)), ensure_ascii=False).lower()
        return {
            "safe_discovery": bool(summary.get("safe_discovery") is True),
            "mutation_probe_network_abort": bool(summary.get("mutation_probe_network_abort") is True),
            "values_not_persisted": not any(token in text for token in ('"values_stored": true', '"input_values_persisted_to_capability_memory": true')),
        }

    def certify_family(self, family: str, summary: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        caps = self._family_caps(family)
        apis = self._family_apis(family)
        replays = self._family_replays(family)
        verified_replays = [r for r in replays if r.get("verified")]
        kinds = {str(c.get("kind") or "") for c in caps}
        labels_n = [self._norm(c.get("label")) for c in caps]
        actions = {}
        for action in EXPECTED_ACTIONS.get(family, ()):
            actions[action] = any(action in label for label in labels_n)

        required_replays = list(REQUIRED_REPLAY_PROFILES.get(family, ()))
        replay_by_name = {str(r.get("name") or ""): r for r in replays}
        replay_checks = {name: bool((replay_by_name.get(name) or {}).get("verified")) for name in required_replays}
        if family == "data_maps":
            replay_checks["verified_entity_action"] = any(
                bool(r.get("verified")) and str(r.get("name") or "").startswith("entity_")
                for r in replays
            )

        response_evidence = any(bool(row.get("response_statuses")) for row in apis)
        causal_api_evidence = any(bool(row.get("caused_by_capability_ids")) for row in apis)
        page_observed = family in self.graph.data.get("pages", {})
        search_observed = any(c.get("kind") == "search" or "search" in self._norm(c.get("label")) for c in caps)
        row_action_observed = any(c.get("kind") == "row_action" for c in caps)
        form_capability_observed = family == "data_maps" or any(c.get("kind") in {"form_field", "form_control"} for c in caps)
        safety = self._summary_safety(summary or {}) if summary else {
            "safe_discovery": True,
            "mutation_probe_network_abort": True,
            "values_not_persisted": bool(self.graph.data.get("values_stored") is False),
        }

        core_checks = {
            "page_observed": page_observed,
            "search_observed": search_observed,
            "row_action_observed": row_action_observed,
            "form_capability_observed": form_capability_observed,
            "api_contract_observed": bool(apis),
            "api_response_evidence": response_evidence,
            "causal_api_evidence": causal_api_evidence,
            "required_replay_verified": all(replay_checks.values()) if replay_checks else bool(verified_replays),
            "safe_discovery": safety["safe_discovery"],
            "mutation_probe_network_abort": safety["mutation_probe_network_abort"],
            "value_free_persistent_memory": safety["values_not_persisted"] and bool(self.graph.data.get("values_stored") is False),
        }
        operational_ready = all(core_checks.values())
        visible_action_coverage = sum(1 for ok in actions.values() if ok)
        expected_action_count = len(actions)
        action_coverage_pct = 100.0 if expected_action_count == 0 else round(100.0 * visible_action_coverage / expected_action_count, 2)
        gaps: List[Dict[str, Any]] = []
        for name, ok in core_checks.items():
            if not ok:
                gaps.append({"family": family, "type": "core_readiness", "item": name, "severity": "blocker"})
        for name, ok in actions.items():
            if not ok:
                gaps.append({"family": family, "type": "visibility_coverage", "item": name, "severity": "warning", "reason": "action not observed in current tenant/permission state"})
        for name, ok in replay_checks.items():
            if not ok:
                gaps.append({"family": family, "type": "replay", "item": name, "severity": "blocker"})

        return mask_sensitive_data({
            "page_family": family,
            "operational_ready": operational_ready,
            "core_checks": core_checks,
            "metrics": {
                "capability_count": len(caps),
                "api_contract_count": len(apis),
                "verified_replay_count": len(verified_replays),
                "replay_count": len(replays),
                "action_coverage_pct": action_coverage_pct,
                "observed_expected_actions": visible_action_coverage,
                "expected_action_count": expected_action_count,
            },
            "expected_actions": actions,
            "required_replays": replay_checks,
            "gaps": gaps,
            "values_stored": False,
        })

    def certify(self, family_summaries: Optional[Mapping[str, Mapping[str, Any]]] = None, *, input_contract: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        family_summaries = family_summaries or {}
        families = {family: self.certify_family(family, family_summaries.get(family)) for family in FAMILY_SEQUENCE}
        gaps = [gap for row in families.values() for gap in (row.get("gaps") or [])]
        operational_ready = all(bool(row.get("operational_ready")) for row in families.values())
        input_ready = bool((input_contract or {}).get("pass")) if input_contract is not None else True
        full_action_coverage = all(float((row.get("metrics") or {}).get("action_coverage_pct") or 0) >= 100.0 for row in families.values())
        return mask_sensitive_data({
            "schema_version": self.SCHEMA,
            "generated_at": utc_now(),
            "operational_readiness": bool(operational_ready and input_ready),
            "configuration_input_readiness": input_ready,
            "full_visible_action_coverage": full_action_coverage,
            "families": families,
            "gap_count": len(gaps),
            "blocker_gap_count": sum(1 for g in gaps if g.get("severity") == "blocker"),
            "warning_gap_count": sum(1 for g in gaps if g.get("severity") == "warning"),
            "gaps": gaps,
            "capability_graph_manifest": self.graph.manifest(),
            "values_stored": False,
        })


class FullHIPDeepLearningMission:
    """Run and certify all five deep HIP portal-family learners.

    Each learner reuses the same persistent Chrome user-data directory, so one
    completed Dell SSO authentication is reused across the family sequence while
    the implementation remains isolated and crash-resumable per family.
    """

    SCHEMA = "hip.full-deep-learning-mission.v1"

    def __init__(self, config: Any, *, capability_graph: Optional[HIPCapabilityGraph] = None) -> None:
        self.config = config
        graph_root = Path(config.reporting.memory_dir) / str(config.brain.directory or "portal_brain")
        self.graph = capability_graph or HIPCapabilityGraph(graph_root)
        self.flow_factories = {
            "data_maps": DataMapDeepDiscoveryFlow,
            "document_types": DocumentTypeDeepDiscoveryFlow,
            "rules": RuleDeepDiscoveryFlow,
            "transport_profiles": TransportProfileDeepDiscoveryFlow,
            "bizflows": BizFlowDeepDiscoveryFlow,
        }

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    def _new_state(self, ctx: RunContext, input_json: str | Path) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "run_id": ctx.run_id,
            "started_at": utc_now(),
            "status": "in_progress",
            "input_json": str(input_json),
            "persistent_sso_profile": str(self.config.portal.browser_user_data_dir),
            "sso_reuse_policy": "one authenticated persistent Chrome profile is reused sequentially across all deep-learning families",
            "family_sequence": list(FAMILY_SEQUENCE),
            "families": {f: {"status": "pending", "attempts": 0, "adopted_from": "", "error": ""} for f in FAMILY_SEQUENCE},
        }

    def _load_resume_summary(self, resume_dir: Path, family: str) -> Optional[Dict[str, Any]]:
        path = resume_dir / SUMMARY_FILENAMES[family]
        return self._read_json(path) if path.is_file() else None

    def _family_pass(self, family: str, summary: Mapping[str, Any]) -> bool:
        if family == "data_maps":
            return bool(((summary.get("deterministic_replay") or {}).get("verified_count") or 0) > 0)
        if family == "document_types":
            phases = summary.get("phases") or {}
            return all(bool(((phases.get(p) or {}).get("create_form_parent_child_learning") or {}).get("pass")) for p in ("source_document_type", "target_document_type"))
        if family == "rules":
            return bool((summary.get("create_form_parent_child_learning") or {}).get("pass"))
        if family == "transport_profiles":
            phases = summary.get("phases") or {}
            return all(bool(((phases.get(p) or {}).get("create_form_parent_child_learning") or {}).get("pass")) for p in ("source_transport_profile", "target_transport_profile"))
        if family == "bizflows":
            return bool((summary.get("create_form_parent_child_learning") or {}).get("pass"))
        return False

    def _write_global_catalogs(self, root: Path) -> Dict[str, str]:
        api_rows = [mask_sensitive_data(dict(x)) for x in self.graph.data.get("api_contracts", {}).values()]
        api_rows.sort(key=lambda x: (str(x.get("endpoint") or ""), str(x.get("method") or "")))
        replay_rows = self.graph.replay_profiles(verified_only=False)
        capability_rows = [mask_sensitive_data(dict(x)) for x in self.graph.data.get("capabilities", {}).values()]
        capability_rows.sort(key=lambda x: (str(x.get("page_family") or ""), str(x.get("kind") or ""), str(x.get("label") or "")))
        safe_write_json(root / "hip_global_api_catalog.json", {"schema_version": "hip.global-api-catalog.v1", "count": len(api_rows), "api_contracts": api_rows, "values_stored": False})
        safe_write_json(root / "hip_replay_registry.json", {"schema_version": "hip.replay-registry.v1", "count": len(replay_rows), "replay_profiles": replay_rows, "values_stored": False})
        safe_write_json(root / "hip_capability_inventory.json", {"schema_version": "hip.capability-inventory.v1", "count": len(capability_rows), "capabilities": capability_rows, "values_stored": False})
        return {
            "api_catalog": str(root / "hip_global_api_catalog.json"),
            "replay_registry": str(root / "hip_replay_registry.json"),
            "capability_inventory": str(root / "hip_capability_inventory.json"),
        }

    async def run(
        self,
        *,
        ctx: RunContext,
        input_json: str | Path,
        resume_run: str | Path | None = None,
        families: Optional[Sequence[str]] = None,
        continue_on_family_failure: bool = True,
    ) -> Dict[str, Any]:
        root = Path(ctx.run_dir)
        root.mkdir(parents=True, exist_ok=True)
        selected = [f for f in FAMILY_SEQUENCE if not families or f in set(families)]
        state_path = root / "full_deep_learning_mission_state.json"
        state = self._new_state(ctx, input_json)
        state["selected_families"] = selected
        safe_write_json(state_path, state)

        payload = json.loads(Path(input_json).read_text(encoding="utf-8"))
        input_contract = validate_live_input_contract(payload if isinstance(payload, dict) else {})
        safe_write_json(root / "full_deep_input_contract_preflight.json", input_contract)
        if not input_contract.get("pass"):
            state["status"] = "blocked_input_contract"
            state["completed_at"] = utc_now()
            safe_write_json(state_path, state)
            raise RuntimeError(f"Full deep-learning input contract failed: {mask_sensitive_string(str(input_contract.get('issues') or []))}")

        resume_dir = Path(resume_run) if resume_run else None
        summaries: Dict[str, Dict[str, Any]] = {}
        for family in selected:
            row = state["families"][family]
            if resume_dir and resume_dir.is_dir():
                prior = self._load_resume_summary(resume_dir, family)
                if prior and self._family_pass(family, prior):
                    summaries[family] = prior
                    row.update({"status": "complete", "adopted_from": str(resume_dir), "attempts": 0})
                    safe_write_json(state_path, state)
                    continue
            row["status"] = "in_progress"
            row["attempts"] = int(row.get("attempts") or 0) + 1
            row["started_at"] = utc_now()
            safe_write_json(state_path, state)
            try:
                flow = self.flow_factories[family](self.config, capability_graph=self.graph)
                summary = await flow.run(ctx=ctx, input_json=input_json)
                summaries[family] = summary
                passed = self._family_pass(family, summary)
                row.update({"status": "complete" if passed else "incomplete", "completed_at": utc_now(), "pass": passed})
                if not passed and not continue_on_family_failure:
                    raise RuntimeError(f"{family} deep learning did not reach its verified completion criterion")
            except Exception as exc:
                row.update({"status": "failed", "error": mask_sensitive_string(str(exc))[:3000], "completed_at": utc_now(), "pass": False})
                safe_write_json(state_path, state)
                if not continue_on_family_failure:
                    raise
            self.graph.save()
            safe_write_json(state_path, state)

        certifier = HIPCapabilityCertifier(self.graph)
        certification = certifier.certify(summaries, input_contract=input_contract)
        safe_write_json(root / "hip_capability_certification.json", certification)
        safe_write_json(root / "hip_capability_gap_queue.json", {
            "schema_version": "hip.capability-gap-queue.v1",
            "generated_at": utc_now(),
            "gap_count": certification.get("gap_count", 0),
            "gaps": certification.get("gaps", []),
            "values_stored": False,
        })
        catalogs = self._write_global_catalogs(root)
        state["status"] = "complete" if certification.get("operational_readiness") else "needs_more_learning"
        state["completed_at"] = utc_now()
        state["operational_readiness"] = bool(certification.get("operational_readiness"))
        state["full_visible_action_coverage"] = bool(certification.get("full_visible_action_coverage"))
        state["capability_graph_manifest"] = self.graph.manifest()
        safe_write_json(state_path, state)

        result = mask_sensitive_data({
            "schema_version": self.SCHEMA,
            "run_id": ctx.run_id,
            "status": state["status"],
            "selected_families": selected,
            "families": {f: state["families"][f] for f in selected},
            "input_contract": input_contract,
            "certification": certification,
            "artifacts": {
                "mission_state": str(state_path),
                "certification": str(root / "hip_capability_certification.json"),
                "gap_queue": str(root / "hip_capability_gap_queue.json"),
                **catalogs,
            },
            "persistent_sso_profile": str(self.config.portal.browser_user_data_dir),
            "values_stored": False,
        })
        safe_write_json(root / "full_hip_deep_learning_summary.json", result)
        return result


def latest_certification(runs_dir: str | Path) -> Dict[str, Any]:
    root = Path(runs_dir)
    if not root.is_dir():
        return {"found": False, "reason": "runs directory missing"}
    candidates = sorted((p for p in root.iterdir() if p.is_dir() and (p / "hip_capability_certification.json").is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {"found": False, "reason": "no full deep certification found"}
    path = candidates[0] / "hip_capability_certification.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"found": False, "reason": mask_sensitive_string(str(exc))}
    return {"found": True, "run_id": candidates[0].name, "path": str(path), "certification": mask_sensitive_data(data)}
