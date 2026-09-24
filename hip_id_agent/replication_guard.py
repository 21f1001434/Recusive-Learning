from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .safe_io import safe_write_json
from .active_surface import classify_doctype_surface_text


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _read_text_file(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception:
        return ""


def collect_dom_texts(run_dir: Path) -> Dict[str, str]:
    texts: Dict[str, str] = {}
    for p in sorted(run_dir.rglob("*.txt")):
        if "dom_snapshots" not in str(p).replace("\\", "/"):
            continue
        texts[str(p)] = _read_text_file(p)
    return texts


def _latest_after_fill_text(dom_texts: Dict[str, str]) -> str:
    # Use the most relevant foreground form snapshot. Never join the listing_before_add
    # snapshot with a modal/drawer snapshot because DDS keeps background pages in the
    # DOM and that caused false listing detections.
    preferred = [(p, t) for p, t in dom_texts.items() if "after_dummy_fill_no_save" in p.lower()]
    if not preferred:
        preferred = [(p, t) for p, t in dom_texts.items() if "add_form_opened" in p.lower() or "after_" in p.lower()]
    if preferred:
        return "\n".join(t for _, t in preferred)
    non_listing = [(p, t) for p, t in dom_texts.items() if "listing_before_add" not in p.lower()]
    if non_listing:
        return "\n".join(t for _, t in non_listing)
    return "\n".join(dom_texts.values())


def _has_any(text: str, needles: Sequence[str]) -> bool:
    n = _norm_text(text)
    return any(_norm_text(x) in n for x in needles)


def _has_all(text: str, needles: Sequence[str]) -> bool:
    n = _norm_text(text)
    return all(_norm_text(x) in n for x in needles)


def detect_false_listing_or_template_surface(phase: str, run_dir: Path) -> Dict[str, Any]:
    """Detect the common false-positive case where the captured screenshot is not a filled form.

    The portal can leave the browser on a listing/grid/template picker after a safe +Add click.
    Old builds still captured that page and marked the phase pass. This detector uses the
    saved DOM text snapshots to fail those runs deterministically before a replay blueprint is
    trusted.
    """
    dom_texts = collect_dom_texts(run_dir)
    text = _latest_after_fill_text(dom_texts)
    result: Dict[str, Any] = {
        "phase": phase,
        "dom_text_files": list(dom_texts.keys()),
        "surface": "unknown",
        "filled_form_detected": False,
        "fatal": [],
        "warnings": [],
    }
    if not text.strip():
        result["warnings"].append("No DOM text snapshot was available for strict surface validation.")
        return result

    if phase == "data_map":
        if _has_any(text, ["Create Map", "Map Reference", "Mapping Details", "Cross Reference Table Details", "Map Validation Details", "Browse Files"]):
            result["surface"] = "add_form"
            result["filled_form_detected"] = True
        elif _has_all(text, ["Data Maps", "Map Name", "Map Identifier", "Items per page"]):
            result["surface"] = "listing_grid"
            result["fatal"].append("Data Map after-fill evidence is the listing/grid page, not the Add Data Map form.")
        elif _has_any(text, ["Map Identifier", "Map Name", "Map Class", "Contivo", "Map Data", "Upload"]):
            result["surface"] = "add_form"
            result["filled_form_detected"] = True
    elif phase in {"source_document_type", "target_document_type"}:
        classified = classify_doctype_surface_text(text)
        result["doctype_surface_classifier"] = classified
        if classified.get("pass"):
            result["surface"] = "add_form"
            result["filled_form_detected"] = True
        else:
            result["surface"] = "listing_or_lost_create_surface"
            result["fatal"].append(
                "Document Type after-fill evidence is not the proven Create Document Type form "
                f"({classified.get('reason')}); listing/filter/background controls cannot satisfy the phase."
            )
    elif phase == "rule":
        if _has_any(text, ["Create Rule", "Rule :", "Conditions :", "Actions :", "Execute Action(s) When", "Rule Scope"]):
            result["surface"] = "add_form"
            result["filled_form_detected"] = True
        elif _has_all(text, ["Rules", "Rule Name", "Items per page"]):
            result["surface"] = "listing_grid"
            result["fatal"].append("Rule after-fill evidence is the listing/grid page, not the Add Rule form.")
        elif _has_any(text, ["Rule Name", "Rule Type", "Rule Scope", "Condition", "Action", "Execute Actions"]):
            result["surface"] = "add_form"
            result["filled_form_detected"] = True
    elif phase in {"source_transport_profile", "target_transport_profile"}:
        if _has_all(text, ["Transport Profiles", "Profile Name", "Items per page"]) and not _has_any(text, ["System Type", "Profile Usage", "Interface Type", "Existing Account"]):
            result["surface"] = "listing_grid"
            result["fatal"].append("Transport Profile after-fill evidence is the listing/grid page, not the Add Transport Profile form.")
        elif _has_any(text, ["System Type", "Partner Name", "Application Name", "Profile Usage", "Interface Type", "Existing Account", "Deployment Group"]):
            result["surface"] = "add_form"
            result["filled_form_detected"] = True
    elif phase == "biz_flow":
        if _has_all(text, ["Create Biz Flow", "B2B-Flow-PubSub-Template", "Inbound", "Outbound"]) and not _has_any(text, ["Flow Details", "Configure Source", "Configure Target", "Configure Routing"]):
            result["surface"] = "template_picker"
            result["fatal"].append("BizFlow after-fill evidence is the template picker/listing page, not the filled BizFlow wizard tabs.")
        elif _has_any(text, ["Flow Details", "Configure Source", "Configure Target", "Configure Routing", "Business Flow Name"]):
            result["surface"] = "add_form_tabs"
            result["filled_form_detected"] = True
    return result


def detect_dummy_value_leak(attempts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    leaks: List[Dict[str, Any]] = []
    for a in attempts or []:
        val = a.get("value_used") if "value_used" in a else a.get("value_redacted") or a.get("value")
        if re.search(r"\bDUMMY|DUMMY_|_KB\b|dummy-", str(val or ""), re.I):
            leaks.append({
                "field": a.get("field") or a.get("key") or a.get("label"),
                "label": a.get("label"),
                "selector": a.get("selector"),
                "value_seen": str(val)[:160],
            })
    return leaks


def detect_upload_acceptance(attempts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    upload_attempts = [a for a in attempts or [] if a.get("upload_attempted") or a.get("asset")]
    accepted = [a for a in upload_attempts if a.get("filled") or a.get("success") or a.get("filename_visible_after_upload")]
    return {
        "upload_attempts": len(upload_attempts),
        "accepted_uploads": len(accepted),
        "uploads": upload_attempts[:20],
        "fatal": ["File upload was expected/attempted but no upload attempt was accepted by the UI."] if upload_attempts and not accepted else [],
    }


def write_strict_replication_report(run_dir: Path, report: Dict[str, Any]) -> str:
    return safe_write_json(run_dir / "strict_replication_gate.json", report, mask=True)
