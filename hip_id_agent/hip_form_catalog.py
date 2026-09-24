from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import urlparse

from .security import mask_sensitive_data


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _tokens(value: Any) -> set[str]:
    return {x for x in _norm(value).split("_") if x}


# The catalog is intentionally broader than the current seven create phases.  It
# is the policy coverage registry for every known HIP Portal form family plus a
# generic fallback for newly introduced Dell pages.
HIP_FORM_FAMILIES: Dict[str, Dict[str, Any]] = {
    "account": {
        "display": "Account",
        "path_markers": ["/bizlink/account", "/bizlink/partner"],
        "surface_markers": ["account", "account name", "deployment group"],
        "repeatable_kinds": ["contact", "domain", "deployment_group"],
    },
    "partner": {
        "display": "Partner",
        "path_markers": ["/bizlink/partner"],
        "surface_markers": ["partner", "partner identifier", "primary domain"],
        "repeatable_kinds": ["contact", "additional_domain", "identifier"],
    },
    "system": {
        "display": "System",
        "path_markers": ["/bizlink/system"],
        "surface_markers": ["system", "business contact", "support contact"],
        "repeatable_kinds": ["contact", "domain", "identifier"],
    },
    "domain": {
        "display": "Domain",
        "path_markers": ["/bizlink/domain", "/bizlink/system"],
        "surface_markers": ["domain", "view domain", "primary domain"],
        "repeatable_kinds": ["system", "deployment_group"],
    },
    "deployment_group": {
        "display": "Deployment Group",
        "path_markers": ["deployment", "group"],
        "surface_markers": ["deployment group", "sender", "receiver"],
        "repeatable_kinds": ["environment", "member"],
    },
    "data_map": {
        "display": "Data Map",
        "path_markers": ["/securelink/datamaps"],
        "surface_markers": ["data map", "map identifier", "map class", "contivo"],
        "repeatable_kinds": ["cross_reference", "upload"],
    },
    "document_type": {
        "display": "Document Type",
        "path_markers": ["/securelink/doctypes"],
        "surface_markers": ["document type", "document identifier", "attributes to configure"],
        "repeatable_kinds": ["document_identifier", "attribute"],
    },
    "rule": {
        "display": "Rule",
        "path_markers": ["/securelink/rules"],
        "surface_markers": ["rule", "condition", "action", "execute actions when"],
        "repeatable_kinds": ["condition", "action"],
    },
    "transport_profile": {
        "display": "Transport Profile",
        "path_markers": ["/securelink/transportprofiles"],
        "surface_markers": ["transport profile", "interface details", "document type details"],
        "repeatable_kinds": ["interface_parameter", "document_type", "endpoint"],
    },
    "biz_flow": {
        "display": "BizFlow",
        "path_markers": ["/bizexchange/bizflows"],
        "surface_markers": ["business flow", "source details", "process steps", "configure routing"],
        "repeatable_kinds": ["flow_identifier", "process_step", "routing_condition", "routing_action"],
    },
    "workflow": {
        "display": "Workflow",
        "path_markers": ["workflow"],
        "surface_markers": ["workflow", "task", "approval", "step"],
        "repeatable_kinds": ["task", "step", "approval"],
    },
    "transport_profile_orchestration": {
        "display": "Transport Profile Orchestration",
        "path_markers": ["orchestration", "transport"],
        "surface_markers": ["transport profile orchestration", "operation", "interface details"],
        "repeatable_kinds": ["interface_parameter", "document_type"],
    },
    "flow_orchestration": {
        "display": "Flow Orchestration",
        "path_markers": ["orchestration", "flow"],
        "surface_markers": ["flow orchestration", "flow definition", "process steps", "source details"],
        "repeatable_kinds": ["flow_identifier", "process_step", "routing"],
    },
    "generic_hip_form": {
        "display": "Generic HIP Form",
        "path_markers": ["/hybrid-integrations"],
        "surface_markers": ["form", "add", "details"],
        "repeatable_kinds": [],
    },
}


def catalog_manifest() -> Dict[str, Any]:
    return mask_sensitive_data({
        "schema_version": "hip.form-family-catalog.v1",
        "coverage": "all known HIP Portal forms plus generic fallback",
        "families": HIP_FORM_FAMILIES,
        "policy_inheritance": {
            "all_forms_use_shared_interaction_policy": True,
            "unknown_hybrid_integrations_pages_use_generic_fallback": True,
            "final_mutations_remain_blocked": True,
        },
    })


def classify_form_surface(
    *,
    url: str = "",
    text: str = "",
    controls: Sequence[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Classify a HIP form without relying on generated element IDs.

    URL, visible headings/labels and semantic control metadata are scored.  The
    generic fallback still receives the full interaction policy, but never
    receives validated fast replay merely from a weak classification.
    """
    raw_url = str(url or "")
    parsed = urlparse(raw_url)
    url_text = f"{parsed.path} {parsed.query}".lower()
    control_text = " ".join(
        " ".join(str(c.get(k) or "") for k in ("section", "label", "semantic_key", "role", "component_tag"))
        for c in (controls or []) if isinstance(c, dict)
    )
    haystack = f"{text} {control_text}".lower()
    scores: List[Dict[str, Any]] = []
    for family, spec in HIP_FORM_FAMILIES.items():
        if family == "generic_hip_form":
            continue
        score = 0
        matched_paths: List[str] = []
        matched_markers: List[str] = []
        for marker in spec.get("path_markers", []):
            if str(marker).lower() in url_text:
                score += 55
                matched_paths.append(str(marker))
        for marker in spec.get("surface_markers", []):
            marker_text = str(marker).lower()
            if marker_text and marker_text in haystack:
                score += 22
                matched_markers.append(str(marker))
        scores.append({
            "family": family,
            "display": spec.get("display", family),
            "score": score,
            "matched_paths": matched_paths,
            "matched_markers": matched_markers,
        })
    scores.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("family") or "")))
    best = scores[0] if scores else {"family": "generic_hip_form", "score": 0}
    second_score = int(scores[1].get("score") or 0) if len(scores) > 1 else 0
    best_score = int(best.get("score") or 0)
    hybrid_page = "/hybrid-integrations" in url_text
    confident = bool(best_score >= 55 and best_score - second_score >= 12)
    family = str(best.get("family") or "generic_hip_form") if confident else "generic_hip_form"
    spec = HIP_FORM_FAMILIES[family]
    fingerprint_parts = [
        family,
        *sorted(_tokens(text)),
        *sorted({
            f"{_norm(c.get('section'))}:{_norm(c.get('semantic_key') or c.get('label'))}:{_norm(c.get('role'))}"
            for c in (controls or []) if isinstance(c, dict)
        }),
    ]
    fingerprint = hashlib.sha256("|".join(fingerprint_parts).encode("utf-8", errors="ignore")).hexdigest()[:24]
    return mask_sensitive_data({
        "schema_version": "hip.form-surface-classification.v1",
        "family": family,
        "display": spec.get("display", family),
        "confidence": round(min(1.0, best_score / 140.0), 4) if confident else 0.25 if hybrid_page else 0.0,
        "confident": confident,
        "best_score": best_score,
        "second_score": second_score,
        "score_margin": best_score - second_score,
        "matched_paths": best.get("matched_paths", []) if confident else [],
        "matched_markers": best.get("matched_markers", []) if confident else [],
        "repeatable_kinds": spec.get("repeatable_kinds", []),
        "structure_fingerprint": fingerprint,
        "shared_policy_required": bool(hybrid_page or confident),
        "validated_fast_replay_eligible": bool(confident),
    })


def phase_to_form_family(phase: str) -> str:
    phase_n = _norm(phase)
    if "document_type" in phase_n:
        return "document_type"
    if "transport_profile" in phase_n:
        return "transport_profile"
    aliases = {
        "data_map": "data_map",
        "rule": "rule",
        "biz_flow": "biz_flow",
        "partner": "partner",
        "system": "system",
        "account": "account",
        "domain": "domain",
        "deployment_group": "deployment_group",
        "workflow": "workflow",
        "flow_orchestration": "flow_orchestration",
        "transport_profile_orchestration": "transport_profile_orchestration",
    }
    return aliases.get(phase_n, "generic_hip_form")
