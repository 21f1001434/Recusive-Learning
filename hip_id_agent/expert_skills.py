from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .section_scope import PHASE_SEQUENCE, resolve_section, section_catalog
from .security import mask_sensitive_data


PHASE_TO_FAMILY: Dict[str, str] = {
    "data_map": "data_map",
    "source_document_type": "document_type",
    "target_document_type": "document_type",
    "rule": "rule",
    "source_transport_profile": "transport_profile",
    "target_transport_profile": "transport_profile",
    "biz_flow": "biz_flow",
}

PHASE_TO_INPUT_KEYS: Dict[str, Sequence[str]] = {
    "data_map": ("data_map", "datamap", "map"),
    "source_document_type": ("source_document_type", "source_doctype", "source_document"),
    "target_document_type": ("target_document_type", "target_doctype", "target_document"),
    "rule": ("rule", "mapping_rule"),
    "source_transport_profile": ("source_transport_profile", "source_tp"),
    "target_transport_profile": ("target_transport_profile", "target_tp"),
    "biz_flow": ("biz_flow", "bizflow", "business_flow"),
}


@dataclass(frozen=True)
class ExpertSkill:
    id: str
    phase: str
    label: str
    implementation_module: str
    expertise_sources: Sequence[str]
    deterministic_contract: str
    verification_contract: str
    recovery_contract: str
    mutating: bool = False


_SKILLS: Dict[str, ExpertSkill] = {
    "data_map": ExpertSkill(
        id="hip.skill.data-map.form-fill.v1", phase="data_map", label="Data Map form fill",
        implementation_module="hip_id_agent.datamap_kb",
        expertise_sources=("Data Map learned KB", "live DOM/DDS state", "captured API evidence", "Portal Brain"),
        deterministic_contract="Use the Data Map-specific deterministic form routine before any adaptive recovery.",
        verification_contract="Every mutable field must reach an exact stable live value; Map Identifier Version is verification-only when portal-owned.",
        recovery_contract="PyAutoGUI MCP primary -> Playwright MCP fallback -> deterministic Playwright compatibility -> Browser-Use state-guided recovery -> exact DOM verification.",
    ),
    "source_document_type": ExpertSkill(
        id="hip.skill.source-document-type.form-fill.v1", phase="source_document_type", label="Source Document Type form fill",
        implementation_module="hip_id_agent.doctype_kb",
        expertise_sources=("Document Type learned KB", "parent-child state graph", "DDS multiselect runtime", "Portal Brain"),
        deterministic_contract="Use the Source Document Type-specific deterministic transaction runtime.",
        verification_contract="Exact selected values, exact multiselect set, stable parent-child transaction and section judge proof are required.",
        recovery_contract="Rebind after rerender, wait stable option universe, then Browser-Use/PyAutoGUI fallback only for the already-resolved control.",
    ),
    "target_document_type": ExpertSkill(
        id="hip.skill.target-document-type.form-fill.v1", phase="target_document_type", label="Target Document Type form fill",
        implementation_module="hip_id_agent.doctype_kb",
        expertise_sources=("Document Type learned KB", "parent-child state graph", "DDS multiselect runtime", "Portal Brain"),
        deterministic_contract="Use the Target Document Type-specific deterministic transaction runtime.",
        verification_contract="Exact selected values, exact multiselect set, stable parent-child transaction and section judge proof are required.",
        recovery_contract="Rebind after rerender, wait stable option universe, then Browser-Use/PyAutoGUI fallback only for the already-resolved control.",
    ),
    "rule": ExpertSkill(
        id="hip.skill.rule.form-fill.v1", phase="rule", label="Rule form fill",
        implementation_module="hip_id_agent.rules_kb",
        expertise_sources=("Rules learned KB", "FormArray row runtime", "rule parent-child graph", "Portal Brain"),
        deterministic_contract="Fill global rule state first, then input-driven condition rows one-to-one.",
        verification_contract="Each condition row is rebound to its physical row and exact values are stable before another + row is created.",
        recovery_contract="Section-local + only, exact N->N+1 proof, Browser-Use recovery evidence, then PyAutoGUI only if the target is uniquely resolved.",
    ),
    "source_transport_profile": ExpertSkill(
        id="hip.skill.source-transport-profile.form-fill.v1", phase="source_transport_profile", label="Source Transport Profile form fill",
        implementation_module="hip_id_agent.transport_profile_kb",
        expertise_sources=("Transport Profile learned KB", "dependency-aware control graph", "captured runtime profiles", "Portal Brain"),
        deterministic_contract="Fill Source Transport Profile in parent-first dependency order using the source input branch only.",
        verification_contract="Every required TP field must be exact and stable; downstream controls are re-read after parent changes.",
        recovery_contract="PyAutoGUI MCP primary -> DDS/Angular rerender recovery -> Playwright fallback -> Browser-Use state context -> exact DOM verification.",
    ),
    "target_transport_profile": ExpertSkill(
        id="hip.skill.target-transport-profile.form-fill.v1", phase="target_transport_profile", label="Target Transport Profile form fill",
        implementation_module="hip_id_agent.transport_profile_kb",
        expertise_sources=("Transport Profile learned KB", "dependency-aware control graph", "captured runtime profiles", "Portal Brain"),
        deterministic_contract="Fill Target Transport Profile in parent-first dependency order using the target input branch only.",
        verification_contract="Every required TP field must be exact and stable; downstream controls are re-read after parent changes.",
        recovery_contract="PyAutoGUI MCP primary -> DDS/Angular rerender recovery -> Playwright fallback -> Browser-Use state context -> exact DOM verification.",
    ),
    "biz_flow": ExpertSkill(
        id="hip.skill.bizflow.form-fill.v1", phase="biz_flow", label="BizFlow form fill",
        implementation_module="hip_id_agent.bizflow_kb",
        expertise_sources=("BizFlow learned KB", "nested accordion/runtime graph", "repeatable-row profiles", "Portal Brain"),
        deterministic_contract="Open and fill BizFlow tabs/nested sections in the learned deterministic order.",
        verification_contract="Nested rows, routing conditions and process children require exact row ownership and stable values.",
        recovery_contract="Nested section-local + resolution -> Browser-Use structured recovery context -> PyAutoGUI only on the exact resolved control.",
    ),
}


def expert_skill_catalog(phases: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    wanted = list(phases or PHASE_SEQUENCE)
    return [asdict(_SKILLS[p]) for p in wanted if p in _SKILLS]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def infer_execution_from_description(description: str) -> Dict[str, Any]:
    """Treat the user description as the trigger while remaining deterministic.

    This intentionally does not delegate section selection to an LLM.  Strong HIP
    family phrases map to explicit phase skills.  Ambiguous/multi-family requests
    become an explicit custom phase subset; a description containing full/all/end-
    to-end maps to the complete seven-phase mission.
    """
    text = _norm(description)
    if not text:
        return {"pass": False, "reason": "description is empty", "description": description, "phases": []}

    if re.search(r"\b(full|all hip|all sections|everything|end[- ]?to[- ]?end|complete flow|complete configuration)\b", text):
        spec = resolve_section("all")
        return {"pass": True, "trigger": "description", "description": description, "section": spec, "phases": spec["phases"], "reason": "explicit full-flow intent"}

    hits: List[str] = []
    def add(*phases: str) -> None:
        for phase in phases:
            if phase not in hits:
                hits.append(phase)

    if re.search(r"\b(source\s+transport\s+profile|source\s+tp)\b", text): add("source_transport_profile")
    if re.search(r"\b(target\s+transport\s+profile|target\s+tp)\b", text): add("target_transport_profile")
    if not any(p in hits for p in ("source_transport_profile", "target_transport_profile")) and re.search(r"\btransport\s+profiles?\b|\btps?\b", text):
        add("source_transport_profile", "target_transport_profile")

    if re.search(r"\bsource\s+document\s+type\b|\bsource\s+doctype\b", text): add("source_document_type")
    if re.search(r"\btarget\s+document\s+type\b|\btarget\s+doctype\b", text): add("target_document_type")
    if not any(p in hits for p in ("source_document_type", "target_document_type")) and re.search(r"\bdocument\s+types?\b|\bdoctypes?\b", text):
        add("source_document_type", "target_document_type")

    if re.search(r"\bdata\s+maps?\b|\bdatamaps?\b", text): add("data_map")
    if re.search(r"\brules?\b|\bmapping\s+rules?\b", text): add("rule")
    if re.search(r"\bbiz\s*flows?\b|\bbizflows?\b|\bbusiness\s+flows?\b", text): add("biz_flow")

    # Preserve canonical mission order rather than phrase order; dependencies and
    # evidence/reporting assume this stable execution ordering.
    ordered = [phase for phase in PHASE_SEQUENCE if phase in hits]
    if not ordered:
        return {
            "pass": False,
            "trigger": "description",
            "description": description,
            "reason": "No supported HIP section could be inferred deterministically",
            "supported_sections": [row["label"] for row in section_catalog()],
            "phases": [],
        }

    # Prefer a named section when the phase set exactly matches one catalog item.
    for row in section_catalog():
        if list(row["phases"]) == ordered:
            spec = resolve_section(row["id"])
            return {"pass": True, "trigger": "description", "description": description, "section": spec, "phases": ordered, "reason": "deterministic section phrase match"}

    return {
        "pass": True,
        "trigger": "description",
        "description": description,
        "section": {
            "id": "custom",
            "label": "Description-selected HIP sections",
            "phases": ordered,
            "phase_csv": ",".join(ordered),
            "phase_count": len(ordered),
            "isolated": len(ordered) < len(PHASE_SEQUENCE),
            "dependency_note": "Only the phases explicitly inferred from the description are executed, in canonical HIP order.",
        },
        "phases": ordered,
        "reason": "deterministic multi-section description match",
    }


def build_context_budget(
    *,
    phases: Sequence[str],
    input_payload: Optional[Mapping[str, Any]] = None,
    capability_graph: Optional[Any] = None,
    max_capabilities_per_family: int = 48,
    max_serialized_chars: int = 64000,
) -> Dict[str, Any]:
    """Build a compact phase-local context manifest instead of dumping all KB state."""
    selected_phases = [p for p in PHASE_SEQUENCE if p in set(phases)]
    objects = dict((input_payload or {}).get("objects") or {}) if isinstance(input_payload, Mapping) else {}
    selected_input_keys: List[str] = []
    selected_objects: Dict[str, Any] = {}
    for phase in selected_phases:
        for key in PHASE_TO_INPUT_KEYS.get(phase, ()):
            if key in objects:
                selected_input_keys.append(key)
                selected_objects[key] = objects[key]
                break

    selected_families = list(dict.fromkeys(PHASE_TO_FAMILY.get(p, p) for p in selected_phases))
    capabilities: List[Dict[str, Any]] = []
    if capability_graph is not None:
        rows = []
        try:
            rows = list((getattr(capability_graph, "data", {}) or {}).get("capabilities", {}).values())
        except Exception:
            rows = []
        per_family: Dict[str, int] = {}
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            fam = str(raw.get("page_family") or "")
            if fam not in selected_families:
                continue
            if per_family.get(fam, 0) >= max_capabilities_per_family:
                continue
            capabilities.append({
                "capability_id": raw.get("capability_id"),
                "page_family": fam,
                "kind": raw.get("kind"),
                "action": raw.get("action"),
                "risk": raw.get("risk"),
                "verified": raw.get("verified"),
                "selector_profile": raw.get("selector_profile"),
            })
            per_family[fam] = per_family.get(fam, 0) + 1

    payload = mask_sensitive_data({
        "phases": selected_phases,
        "families": selected_families,
        "input_objects": selected_objects,
        "capabilities": capabilities,
        "expert_skills": expert_skill_catalog(selected_phases),
    })
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    truncated = len(raw) > max_serialized_chars
    return {
        "policy": "phase-local context with larger adaptive budget; AutoWebGLM primary action policy on phase-local context; Browser-Use/LangChain broaden context only after failure",
        "context_tier": "phase_local",
        "selected_phases": selected_phases,
        "selected_families": selected_families,
        "selected_input_keys": selected_input_keys,
        "capability_count": len(capabilities),
        "max_capabilities_per_family": max_capabilities_per_family,
        "serialized_chars_before_limit": len(raw),
        "max_serialized_chars": max_serialized_chars,
        "truncated": truncated,
        "context_excerpt": raw[:max_serialized_chars],
    }


def vet_expert_skills(
    phases: Sequence[str],
    *,
    phase_rows: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Vet deterministic phase skills before execution.

    A skill passes only when its implementation module exists and, when phase
    preflight rows are supplied, the selected phase's input/form accounting also
    passes.  Browser-Use/PyAutoGUI are recovery aids and are never substitutes for
    a missing deterministic skill.
    """
    phase_preflight: Dict[str, bool] = {}
    for row in phase_rows or []:
        phase = str(row.get("phase") or "")
        if phase:
            phase_preflight[phase] = bool(row.get("pass"))

    results: List[Dict[str, Any]] = []
    for phase in phases:
        skill = _SKILLS.get(str(phase))
        if skill is None:
            results.append({"phase": phase, "pass": False, "reason": "no registered deterministic expert skill"})
            continue
        module_present = importlib.util.find_spec(skill.implementation_module) is not None
        input_pass = phase_preflight.get(phase, True)
        results.append({
            **asdict(skill),
            "module_present": module_present,
            "phase_preflight_pass": input_pass,
            "pass": bool(module_present and input_pass),
            "vetting": "implementation module + selected phase preflight + deterministic verification contract",
        })
    return {
        "pass": bool(results) and all(bool(r.get("pass")) for r in results),
        "skills": results,
        "selected_skill_count": len(results),
        "policy": "vet each expert skill before execution; recovery tools cannot replace an unvetted deterministic skill",
    }
