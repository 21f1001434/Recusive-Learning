from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence


PHASE_SEQUENCE: tuple[str, ...] = (
    "data_map",
    "source_document_type",
    "target_document_type",
    "rule",
    "source_transport_profile",
    "target_transport_profile",
    "biz_flow",
)

_SECTION_SPECS: tuple[Dict[str, Any], ...] = (
    {
        "id": "data-map",
        "label": "Data Map only",
        "phases": ("data_map",),
        "aliases": ("data-map", "data_map", "datamap", "data maps", "data map"),
        "dependency_note": "Runs only the Data Map form. No other HIP form is opened.",
    },
    {
        "id": "source-document-type",
        "label": "Source Document Type only",
        "phases": ("source_document_type",),
        "aliases": ("source-document-type", "source_document_type", "source doctype", "source document type"),
        "dependency_note": "Runs only the Source Document Type form.",
    },
    {
        "id": "target-document-type",
        "label": "Target Document Type only",
        "phases": ("target_document_type",),
        "aliases": ("target-document-type", "target_document_type", "target doctype", "target document type"),
        "dependency_note": "Runs only the Target Document Type form.",
    },
    {
        "id": "document-type",
        "label": "Document Types only (Source + Target)",
        "phases": ("source_document_type", "target_document_type"),
        "aliases": ("document-type", "document-types", "document_type", "document_types", "doctype", "doctypes", "document type", "document types"),
        "dependency_note": "Runs Source and Target Document Type only; Data Map, Rule, Transport Profile and BizFlow are skipped.",
    },
    {
        "id": "rule",
        "label": "Rule only",
        "phases": ("rule",),
        "aliases": ("rule", "rules"),
        "dependency_note": "Runs only the Rule form. Any referenced mapping/document objects must already exist in HIP when the live dropdown requires them.",
    },
    {
        "id": "source-transport-profile",
        "label": "Source Transport Profile only",
        "phases": ("source_transport_profile",),
        "aliases": ("source-transport-profile", "source_transport_profile", "source tp", "source transport profile"),
        "dependency_note": "Runs only Source Transport Profile. Referenced System/Partner/Application, account and Document Type values must already be selectable in HIP.",
    },
    {
        "id": "target-transport-profile",
        "label": "Target Transport Profile only",
        "phases": ("target_transport_profile",),
        "aliases": ("target-transport-profile", "target_transport_profile", "target tp", "target transport profile"),
        "dependency_note": "Runs only Target Transport Profile. Referenced System/Partner/Application, account and Document Type values must already be selectable in HIP.",
    },
    {
        "id": "transport-profile",
        "label": "Transport Profiles only (Source + Target)",
        "phases": ("source_transport_profile", "target_transport_profile"),
        "aliases": ("transport-profile", "transport-profiles", "transport_profile", "transport_profiles", "tp", "tps", "transport profile", "transport profiles"),
        "dependency_note": "Runs Source and Target Transport Profile only. It does not recreate upstream Document Types/Systems/Partners; referenced values must already exist/select correctly.",
    },
    {
        "id": "bizflow",
        "label": "BizFlow only",
        "phases": ("biz_flow",),
        "aliases": ("bizflow", "biz-flow", "biz_flow", "biz flow", "business flow"),
        "dependency_note": "Runs only BizFlow. Referenced Transport Profiles, Document Types, Rule/Map values must already exist/select correctly.",
    },
    {
        "id": "all",
        "label": "All HIP sections",
        "phases": PHASE_SEQUENCE,
        "aliases": ("all", "full", "full-flow", "full flow", "all sections", "all hip"),
        "dependency_note": "Runs the complete seven-phase HIP sequence.",
    },
)


def _norm(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text


def section_catalog(*, include_all: bool = True) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for spec in _SECTION_SPECS:
        if not include_all and spec["id"] == "all":
            continue
        rows.append({
            "id": spec["id"],
            "label": spec["label"],
            "phases": list(spec["phases"]),
            "phase_count": len(spec["phases"]),
            "dependency_note": spec["dependency_note"],
        })
    return rows


def resolve_section(section: str) -> Dict[str, Any]:
    wanted = _norm(section)
    for spec in _SECTION_SPECS:
        candidates = {_norm(spec["id"]), *(_norm(alias) for alias in spec["aliases"])}
        if wanted in candidates:
            return {
                "id": spec["id"],
                "label": spec["label"],
                "phases": list(spec["phases"]),
                "phase_csv": ",".join(spec["phases"]),
                "phase_count": len(spec["phases"]),
                "dependency_note": spec["dependency_note"],
                "isolated": spec["id"] != "all",
            }
    valid = ", ".join(row["id"] for row in section_catalog())
    raise ValueError(f"Unknown HIP section '{section}'. Valid sections: {valid}")


def resolve_section_phases(section: str) -> List[str]:
    return list(resolve_section(section)["phases"])


def phase_csv_for_section(section: str) -> str:
    return str(resolve_section(section)["phase_csv"])


def is_exact_phase_subset(phases: Sequence[str]) -> bool:
    values = list(phases)
    return bool(values) and all(value in PHASE_SEQUENCE for value in values) and len(values) == len(set(values))
