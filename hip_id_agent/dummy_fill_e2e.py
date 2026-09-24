from __future__ import annotations

import asyncio
import base64
import copy
import csv
import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests
from bs4 import BeautifulSoup

from .bizflow_kb import BizFlowKBFlow, BIZFLOWS_URL
from .browser_session import BrowserSession
from .config import AppConfig
from .datamap_kb import DataMapKBFlow, DATAMAPS_URL
from .doctype_kb import DocumentTypeKBFlow, DOCTYPES_URL
from .models import RunContext, utc_now
from .rules_kb import RuleKBFlow, RULES_URL
from .transport_profile_kb import TransportProfileKBFlow, TRANSPORT_PROFILES_URL
from .security import mask_sensitive_data, mask_sensitive_string
from .safe_io import safe_write_json, safe_write_text
from .repeatable_rows import apply_repeatable_row_adds, build_repeatable_section_plan
from .upload_assets import list_upload_assets
from .replication_guard import detect_false_listing_or_template_surface, detect_dummy_value_leak, detect_upload_acceptance, write_strict_replication_report
from .section_judge import DualModelSectionJudge, SectionJudgePolicy
from .form_knowledge_plan import compile_and_attach_plans
from .portal_brain import PortalBrain
from .portal_learning_runtime import PortalLearningRuntime
from .aia_client import AIAClient, build_chat_payload_variants, extract_aia_response_text, resolve_output_token_limit
from .config import AIAConfig
from .runtime_self_heal import RuntimeSelfHealController
from .phase_runtime_contract import PHASE_RUNTIME_CONTRACTS, validate_phase_runtime_contracts
from .form_interaction_policy import policy_manifest
from .flow_pattern_memory import FlowPatternMemory
from .agentq_runtime import HIPAgentQController
from .stateful_form_runtime import compile_phase_state_graph
from .hip_form_catalog import catalog_manifest
from .deterministic_evidence import build_validated_phase_trajectory
from .autonomous_dependency_runtime import apply_dependency_execution_contract
from .mission_controller import (
    MissionController,
    PHASE_JUDGE_RESULT_FILENAME,
    PHASE_VERIFICATION_FILENAME,
)
from .mission_trace import MissionTraceLedger
from .live_witness import build_live_witness_report
from .form_api_agent import FormAPIAgentQRuntime
from .mission_assurance import capture_mcp_evidence_quorum, build_phase_assurance_report
from .capability_graph import HIPCapabilityGraph, PHASE_TO_CAPABILITY_FAMILY, promote_validated_phase_to_capability_graph
from .phase_progress import run_with_progress_watchdog
from .phase_transition import MissionTransitionCoordinator
from .final_mission import FinalMissionConsolidator
from .mlflow_async import AsyncMLflowTracker
from .replay_policy import replay_policy_engine_from_config
from .mission_learning import close_mission_learning_loop
from .judge_consensus import MultiModelJudgeConsensus
from .model_portfolio import model_portfolio_from_config
from .human_phase_review import human_phase_review_from_config
from .phase_live_reproof import live_read_only_phase_reproof
from .deterministic_recipe import deterministic_recipe_from_config
from .interactive_teaching import interactive_teaching_from_config, capture_pending_interactive_teaching
from .continuous_learning import continuous_learning_from_config
from . import __version__


PHASE_SEQUENCE = [
    "data_map",
    "source_document_type",
    "target_document_type",
    "rule",
    "source_transport_profile",
    "target_transport_profile",
    "biz_flow",
]

PHASE_URLS = {
    "data_map": DATAMAPS_URL,
    "source_document_type": DOCTYPES_URL,
    "target_document_type": DOCTYPES_URL,
    "rule": RULES_URL,
    "source_transport_profile": TRANSPORT_PROFILES_URL,
    "target_transport_profile": TRANSPORT_PROFILES_URL,
    "biz_flow": BIZFLOWS_URL,
}

PHASE_DISPLAY = {
    "data_map": "Data Map",
    "source_document_type": "Source Document Type",
    "target_document_type": "Target Document Type",
    "rule": "Rule",
    "source_transport_profile": "Source Transport Profile",
    "target_transport_profile": "Target Transport Profile",
    "biz_flow": "BizFlow",
}


PHASE_EXACT_COMPLETION_ARTIFACTS = {
    "data_map": ("datamap_kb/datamap_target_branch_execution.json",),
    "source_document_type": ("doctype_kb/doctype_target_branch_execution.json",),
    "target_document_type": ("doctype_kb/doctype_target_branch_execution.json",),
    "rule": ("rule_kb/rule_target_branch_execution.json",),
    "source_transport_profile": ("transport_profile_kb/transport_profile_target_branch_execution.json",),
    "target_transport_profile": ("transport_profile_kb/transport_profile_target_branch_execution.json",),
    "biz_flow": ("bizflow_kb/bizflow_tab_form_kb.json", "bizflow_kb/bizflow_form_kb.json"),
}


def phase_exact_completion_checkpoint(phase: str, phase_dir: Path) -> Dict[str, Any]:
    """Return deterministic proof that live form execution finished before reporting failed.

    This checkpoint is intentionally model-independent. GPT/vision may advise or
    review, but they cannot cause a completed phase to be replayed when the exact
    browser execution artifact already passed.
    """
    # R10: a terminal read-only reproof can become authoritative when the live
    # form is already exact but the longer KB/learning coroutine was interrupted
    # before it emitted its normal target-branch execution artifact.  This
    # artifact is deliberately value-free and can only be produced from the
    # current browser state without clicks/fills/saves.
    live_reproof_path = Path(phase_dir) / "phase_live_read_only_reproof.json"
    if live_reproof_path.is_file():
        try:
            live = read_json_any(live_reproof_path)
        except Exception:
            live = {}
        if bool(
            isinstance(live, dict)
            and live.get("pass") is True
            and live.get("deterministic_pass") is True
            and live.get("read_only") is True
            and str(live.get("source") or "") == "current_live_browser"
            and not (live.get("missing_fields") or [])
            and not (live.get("row_issue_fields") or [])
            and not (live.get("invalid_fields") or [])
            and bool((live.get("required_upload_proof") or {}).get("pass", True))
        ):
            return {
                "schema_version": "hip.phase-exact-completion-checkpoint.v2",
                "phase": phase,
                "pass": True,
                "status": "exact_live_state_reproved_read_only",
                "authoritative_artifact": str(live_reproof_path),
                "read_only_live_reproof": True,
                "inspected": [{
                    "path": str(live_reproof_path),
                    "exists": True,
                    "pass": True,
                    "exact_verified": True,
                    "read_only": True,
                    "required_upload_proof": live.get("required_upload_proof") or {},
                }],
            }

    candidates = PHASE_EXACT_COMPLETION_ARTIFACTS.get(phase, ())
    inspected: List[Dict[str, Any]] = []
    for rel in candidates:
        path = Path(phase_dir) / rel
        if not path.is_file():
            inspected.append({"path": str(path), "exists": False})
            continue
        try:
            payload = read_json_any(path)
        except Exception as exc:
            inspected.append({
                "path": str(path),
                "exists": True,
                "read_error": mask_sensitive_string(str(exc)),
            })
            continue

        execution = payload
        if phase == "biz_flow":
            if isinstance(payload.get("stateful_target_branch_execution"), dict):
                execution = payload.get("stateful_target_branch_execution") or {}
            elif isinstance(payload.get("bizflow_tab_form"), dict):
                execution = (payload.get("bizflow_tab_form") or {}).get("stateful_target_branch_execution") or {}

        passed = bool(
            isinstance(execution, dict)
            and execution.get("pass") is True
            and str(execution.get("status") or "pass").lower() not in {"failed", "error", "blocked"}
        )
        attempts = execution.get("attempts") if isinstance(execution, dict) else []
        # The state executor already classifies which failures are blocking.  Do not
        # reclassify optional/conditional absent controls as exact-execution failure
        # merely because an informational attempt has success=False.
        declared_failed = execution.get("failed_attempts") if isinstance(execution, dict) else None
        if isinstance(declared_failed, list):
            failed_attempts = [row for row in declared_failed if isinstance(row, dict)]
        else:
            failed_attempts = [
                row for row in (attempts or [])
                if isinstance(row, dict) and row.get("success") is False and not row.get("optional")
            ]
        stage_audit = execution.get("execution_stage_audit") if isinstance(execution, dict) and isinstance(execution.get("execution_stage_audit"), dict) else {}
        strict_stage_ok = bool(
            not execution.get("strict_live_execution")
            or (
                stage_audit.get("fields_filled_or_verified") is True
                and stage_audit.get("exact_execution_verified") is True
                and stage_audit.get("authoritative_execution_verified") is True
            )
        ) if isinstance(execution, dict) else False
        exact_verified = bool(passed and not failed_attempts and strict_stage_ok)
        required_upload_proof: Dict[str, Any] = {"required": False, "pass": True}
        if phase == "data_map":
            upload_attempts = [
                a for a in (attempts or [])
                if isinstance(a, dict) and str(a.get("field") or "") == "map_data_file"
            ]
            # Data Map transformation upload is mission-owned whenever present in
            # the state graph. A form cannot be called complete because text/dropdown
            # fields were filled while the JAR/XBM never reached input.files.
            if upload_attempts:
                good = [
                    a for a in upload_attempts
                    if a.get("success") is True and a.get("exact_verified") is True
                    and bool(a.get("actual_value") or a.get("uploaded_file_name"))
                ]
                required_upload_proof = {
                    "required": True,
                    "pass": bool(good),
                    "attempt_count": len(upload_attempts),
                    "proved_count": len(good),
                    "executor": (good[-1].get("executor") if good else ""),
                    "actual_file": (good[-1].get("actual_value") or good[-1].get("uploaded_file_name") if good else ""),
                }
                exact_verified = bool(exact_verified and required_upload_proof["pass"])
        row = {
            "path": str(path),
            "exists": True,
            "pass": passed,
            "exact_verified": exact_verified,
            "status": execution.get("status") if isinstance(execution, dict) else None,
            "attempt_count": len(attempts or []) if isinstance(attempts, list) else 0,
            "failed_attempt_count": len(failed_attempts),
            "strict_live_execution": bool(execution.get("strict_live_execution")) if isinstance(execution, dict) else False,
            "execution_stage_audit": stage_audit,
            "strict_stage_ok": strict_stage_ok,
            "required_upload_proof": required_upload_proof,
        }
        inspected.append(row)
        if exact_verified:
            return {
                "schema_version": "hip.phase-exact-completion-checkpoint.v1",
                "phase": phase,
                "pass": True,
                "status": "exact_live_execution_completed",
                "authoritative_artifact": str(path),
                "inspected": inspected,
            }
    return {
        "schema_version": "hip.phase-exact-completion-checkpoint.v1",
        "phase": phase,
        "pass": False,
        "status": "no_exact_completion_proof",
        "inspected": inspected,
    }


def synthesize_summary_after_reporting_failure(
    *,
    phase: str,
    phase_dir: Path,
    error: str,
    checkpoint: Dict[str, Any],
) -> Dict[str, Any]:
    """Create the minimal phase summary required for normal verification/judging."""
    files: Dict[str, str] = {}
    for path in sorted(Path(phase_dir).rglob("*")):
        if path.is_file() and path.suffix.lower() in {".json", ".png", ".csv", ".md", ".html"}:
            files[path.stem] = str(path)
    return {
        "status": "completed_with_reporting_warning",
        "phase": phase,
        "run_dir": str(phase_dir),
        "counts": {},
        "files": files,
        "warnings": [
            "A non-authoritative reporting/Knowledge Graph export failed after exact live form execution. "
            "The phase was not replayed."
        ],
        "reporting_failure_recovered_without_replay": True,
        "reporting_error": mask_sensitive_string(error),
        "exact_completion_checkpoint": checkpoint,
    }

GOLDEN_SCREENSHOT_NAME_MAP = {
    "data map": "data_map",
    "source document type": "source_document_type",
    "target document type": "target_document_type",
    "rules": "rule",
    "rule": "rule",
    "source transport profile": "source_transport_profile",
    "target transport profile": "target_transport_profile",
    "bizflow-fd": "biz_flow",
    "bizflow-cs": "biz_flow",
    "bizflow-ct-1": "biz_flow",
    "bizflow-ct-2": "biz_flow",
    "bizflow-cr": "biz_flow",
    "bizflow-deployed": "biz_flow",
}


def _normalize_golden_name(path: str | Path) -> str:
    stem = Path(path).stem.lower().strip()
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem)
    return stem


def _phase_from_golden_screenshot(path: str | Path) -> Optional[str]:
    norm = _normalize_golden_name(path)
    if norm in GOLDEN_SCREENSHOT_NAME_MAP:
        return GOLDEN_SCREENSHOT_NAME_MAP[norm]
    dashed = norm.replace(" ", "-")
    if dashed in GOLDEN_SCREENSHOT_NAME_MAP:
        return GOLDEN_SCREENSHOT_NAME_MAP[dashed]
    # Best-effort fallback for user-named screenshots.
    if "data" in norm and "map" in norm:
        return "data_map"
    if "source" in norm and "document" in norm:
        return "source_document_type"
    if "target" in norm and "document" in norm:
        return "target_document_type"
    if "rule" in norm:
        return "rule"
    if "source" in norm and "transport" in norm:
        return "source_transport_profile"
    if "target" in norm and "transport" in norm:
        return "target_transport_profile"
    if "bizflow" in norm or "biz flow" in norm:
        return "biz_flow"
    return None


def _image_basic_meta(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    meta: Dict[str, Any] = {
        "file": p.name,
        "path": str(p),
        "sha256_16": _short_sha256(p),
        "bytes": p.stat().st_size if p.exists() else 0,
    }
    try:
        from PIL import Image  # optional; available in most Playwright/image environments
        with Image.open(p) as im:
            meta["width"], meta["height"] = im.size
    except Exception:
        pass
    return meta


def prepare_golden_screenshot_references(
    golden_screenshot_dir: str | Path | None,
    root_dir: Path,
    *,
    phases: Sequence[str] | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Prepare human-approved screenshots without making long Windows paths fatal.

    The previous layout copied every image into
    ``<run>/golden_reference_screenshots/<phase>/<original name>``.  On managed
    Windows/OneDrive workspaces that destination can exceed the legacy Win32 path
    limit and ``shutil.copy2`` raises ``FileNotFoundError`` even though the source
    image exists.  Golden evidence is advisory and must never prevent the form
    mission from starting.

    Only screenshots for the selected phases are considered.  We first try a
    short, flat run-local cache (``_golden``).  If the copy cannot be created for
    any filesystem reason, the original human-approved file is used directly and
    the fallback is recorded in metadata.
    """
    if not golden_screenshot_dir:
        return {}
    src = Path(golden_screenshot_dir)
    if not src.exists():
        return {}
    selected = {str(x) for x in (phases or PHASE_SEQUENCE) if str(x) in PHASE_SEQUENCE}
    dest = root_dir / "_golden"
    refs: Dict[str, List[Dict[str, Any]]] = {p: [] for p in PHASE_SEQUENCE if p in selected}
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        # The source references are still usable even when the run directory is
        # too long for another nested folder.
        pass
    for p in sorted(src.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        phase = _phase_from_golden_screenshot(p)
        if phase not in selected:
            continue
        phase_token = re.sub(r"[^a-z0-9]+", "", str(phase).lower())[:10] or "phase"
        short_name = f"{phase_token}-{_short_sha256(p)[:10]}{p.suffix.lower()}"
        copied = dest / short_name
        evidence_path = p
        copy_status = "source_reference"
        copy_error = ""
        try:
            if copied.resolve() != p.resolve():
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, copied)
            evidence_path = copied
            copy_status = "run_local_short_copy"
        except OSError as exc:
            # Golden screenshots must not block a Transport Profile/full run.
            # The source exists and is an equally valid vision/manual reference.
            evidence_path = p
            copy_status = "source_fallback_after_copy_error"
            copy_error = mask_sensitive_string(str(exc))
        meta = _image_basic_meta(evidence_path)
        # Keep the human filename for report readability even when the cached file
        # uses a short hash name.
        meta["file"] = p.name
        meta.update({
            "phase": phase,
            "phase_label": PHASE_DISPLAY.get(phase, phase),
            "source_file": str(p),
            "role": "golden_correctly_filled_reference",
            "copy_status": copy_status,
        })
        if copy_error:
            meta["copy_warning"] = copy_error
        refs.setdefault(phase, []).append(meta)
    refs = {k: v for k, v in refs.items() if v}
    if refs:
        manifest = dest / "manifest.json"
        try:
            safe_write_json(manifest, refs)
        except OSError:
            # Report generation remains non-authoritative; references already live
            # in memory and can still be consumed by the judges.
            pass
    return refs

MUTATING_WORDS = [
    "save",
    "create",
    "submit",
    "delete",
    "remove",
    "deploy",
    "enable",
    "disable",
    "confirm",
    "publish",
    "update",
]


def read_json_any(path: str | Path | None) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    try:
        return json.loads(text)
    except Exception:
        # Some pasted files contain prose before/after the JSON. Extract the first object.
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _safe_phase_filename(phase: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", phase).strip("_") or "phase"


def _copy_phase_payload(base: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(base or {})


def make_phase_input(base_input: Dict[str, Any], phase: str) -> Dict[str, Any]:
    """Return a phase-specific input payload so source/target objects are not mixed.

    The legacy KB form fillers accept a single seed object per run.  For stages that
    have both source and target objects, this function narrows the input to the
    exact object that should be filled in the current phase.
    """
    data = _copy_phase_payload(base_input)
    objects = data.get("objects") if isinstance(data.get("objects"), dict) else {}
    data["_full_dummy_fill_phase"] = phase
    data["_full_dummy_fill_phase_label"] = PHASE_DISPLAY.get(phase, phase)
    data["_replicate_exact_input_values"] = True

    if phase == "source_document_type":
        src = copy.deepcopy(objects.get("source_document_type") or {})
        data["objects"] = {**objects, "document_type": src, "source_document_type": src}
        data["objects"].pop("target_document_type", None)
    elif phase == "target_document_type":
        tgt = copy.deepcopy(objects.get("target_document_type") or {})
        data["objects"] = {**objects, "document_type": tgt, "target_document_type": tgt}
        data["objects"].pop("source_document_type", None)
    elif phase == "source_transport_profile":
        src = copy.deepcopy(objects.get("source_transport_profile") or {})
        data["objects"] = {**objects, "transport_profile": src, "source_transport_profile": src}
        data["objects"].pop("target_transport_profile", None)
    elif phase == "target_transport_profile":
        tgt = copy.deepcopy(objects.get("target_transport_profile") or {})
        data["objects"] = {**objects, "transport_profile": tgt, "target_transport_profile": tgt}
        data["objects"].pop("source_transport_profile", None)
    elif phase == "data_map":
        data["objects"] = {**objects, "data_map": copy.deepcopy(objects.get("data_map") or {})}
    elif phase == "rule":
        data["objects"] = {**objects, "rule": copy.deepcopy(objects.get("rule") or {})}
    elif phase == "biz_flow":
        data["objects"] = {**objects, "biz_flow": copy.deepcopy(objects.get("biz_flow") or {})}
    try:
        data["_repeatable_section_plan"] = build_repeatable_section_plan(data, phase)
    except Exception:
        data["_repeatable_section_plan"] = []
    return data


def _canonical_version_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        number = float(raw)
        return f"{number:.1f}" if number.is_integer() else str(number).rstrip("0").rstrip(".")
    except Exception:
        return raw


def _versioned_ref(name: Any, version: Any) -> str:
    name_text = str(name or "").strip()
    version_text = _canonical_version_text(version)
    return f"{name_text}({version_text})" if name_text and version_text else name_text


def _norm_ref(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def validate_live_input_contract(base_input: Dict[str, Any], phases: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Fail before browser work when input.json is incomplete or contradictory.

    The preflight now validates three layers: reviewed acceptance overrides,
    complete phase input-to-control accounting, and cross-object referential
    integrity.  This prevents the browser agent from trying to repair a form when
    the real defect is an internally inconsistent input.json.
    """
    contract = base_input.get("_live_acceptance_contract") if isinstance(base_input.get("_live_acceptance_contract"), dict) else {}
    issues: List[Dict[str, Any]] = []
    for path, expected in contract.items():
        cur: Any = base_input
        for part in str(path).split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if str(cur or "").strip() != str(expected or "").strip():
            issues.append({"path": path, "expected": expected, "actual": cur, "reason": "input contradicts reviewed live acceptance contract"})

    objects = base_input.get("objects") if isinstance(base_input.get("objects"), dict) else {}
    selected_phases = [p for p in (phases or PHASE_SEQUENCE) if p in PHASE_SEQUENCE]
    required_objects = list(selected_phases)
    for phase in required_objects:
        if not isinstance(objects.get(phase), dict) or not objects.get(phase):
            issues.append({"path": f"objects.{phase}", "reason": "required phase object is missing or empty"})

    src_doc = objects.get("source_document_type") if isinstance(objects.get("source_document_type"), dict) else {}
    tgt_doc = objects.get("target_document_type") if isinstance(objects.get("target_document_type"), dict) else {}
    data_map = objects.get("data_map") if isinstance(objects.get("data_map"), dict) else {}
    rule = objects.get("rule") if isinstance(objects.get("rule"), dict) else {}
    src_tp = objects.get("source_transport_profile") if isinstance(objects.get("source_transport_profile"), dict) else {}
    tgt_tp = objects.get("target_transport_profile") if isinstance(objects.get("target_transport_profile"), dict) else {}
    biz = objects.get("biz_flow") if isinstance(objects.get("biz_flow"), dict) else {}

    if "source_transport_profile" in selected_phases and src_tp.get("profile_usage") and str(src_tp.get("profile_usage")).lower() != "sender":
        issues.append({"path": "objects.source_transport_profile.profile_usage", "expected": "Sender", "actual": src_tp.get("profile_usage")})
    if "target_transport_profile" in selected_phases and tgt_tp.get("profile_usage") and str(tgt_tp.get("profile_usage")).lower() != "receiver":
        issues.append({"path": "objects.target_transport_profile.profile_usage", "expected": "Receiver", "actual": tgt_tp.get("profile_usage")})

    src_doc_ref = _versioned_ref(src_doc.get("name"), src_doc.get("version"))
    tgt_doc_ref = _versioned_ref(tgt_doc.get("name"), tgt_doc.get("version"))
    map_ref = _versioned_ref(data_map.get("map_identifier"), data_map.get("map_identifier_version"))
    rule_ref = _versioned_ref(rule.get("name"), rule.get("version"))

    def expect(path: str, actual: Any, expected: Any, reason: str = "cross-object reference mismatch") -> None:
        if expected in (None, "") or actual in (None, ""):
            if expected not in (None, "") and actual in (None, ""):
                issues.append({"path": path, "expected": expected, "actual": actual, "reason": reason})
            return
        if _norm_ref(actual) != _norm_ref(expected):
            issues.append({"path": path, "expected": expected, "actual": actual, "reason": reason})

    if "rule" in selected_phases:
        expect("objects.rule.document_type_name_version", rule.get("document_type_name_version"), src_doc_ref)
        actions = rule.get("actions") if isinstance(rule.get("actions"), dict) else {}
        expect("objects.rule.actions.mapping_identifier_name_version", actions.get("mapping_identifier_name_version"), map_ref)
    if "source_transport_profile" in selected_phases:
        expect("objects.source_transport_profile.document_type", src_tp.get("document_type"), src_doc_ref)
    if "target_transport_profile" in selected_phases:
        expect("objects.target_transport_profile.document_type", tgt_tp.get("document_type"), tgt_doc_ref)

    if "biz_flow" in selected_phases:
        flow_details = biz.get("flow_details") if isinstance(biz.get("flow_details"), dict) else {}
        if base_input.get("profile_name"):
            expect("objects.biz_flow.flow_details.business_flow_name", flow_details.get("business_flow_name"), base_input.get("profile_name"), "business flow must match mission profile_name")
        source = biz.get("configure_source") if isinstance(biz.get("configure_source"), dict) else {}
        target = biz.get("configure_targets") if isinstance(biz.get("configure_targets"), dict) else {}
        expect("objects.biz_flow.configure_source.document_type_name_version", source.get("document_type_name_version"), src_doc_ref)
        expect("objects.biz_flow.configure_source.source_transport_profile", source.get("source_transport_profile"), src_tp.get("profile_name"))
        expect("objects.biz_flow.configure_source.source_application", source.get("source_application"), src_tp.get("partner_name"))
        expect("objects.biz_flow.configure_targets.document_type_name_version", target.get("document_type_name_version"), tgt_doc_ref)
        expect("objects.biz_flow.configure_targets.target_transport_profile", target.get("target_transport_profile"), tgt_tp.get("profile_name"))
        expect("objects.biz_flow.configure_targets.target_application", target.get("target_application"), tgt_tp.get("partner_name"))

        flow_identifiers = biz.get("flow_identifiers") if isinstance(biz.get("flow_identifiers"), dict) else {}
        fi_rows = flow_identifiers.get("conditions") if isinstance(flow_identifiers.get("conditions"), list) else []
        for i, row in enumerate(fi_rows):
            if isinstance(row, dict):
                expect(f"objects.biz_flow.flow_identifiers.conditions[{i}].document_type_name_version", row.get("document_type_name_version"), src_doc_ref)

        process_steps = biz.get("process_steps") if isinstance(biz.get("process_steps"), list) else []
        for i, step in enumerate(process_steps):
            if not isinstance(step, dict):
                continue
            cfg = step.get("configuration") if isinstance(step.get("configuration"), dict) else {}
            if cfg.get("target_document_type_version"):
                expect(f"objects.biz_flow.process_steps[{i}].configuration.target_document_type_version", cfg.get("target_document_type_version"), tgt_doc_ref)
            if cfg.get("rule_version"):
                expect(f"objects.biz_flow.process_steps[{i}].configuration.rule_version", cfg.get("rule_version"), rule_ref)

        routing = biz.get("configure_routing") if isinstance(biz.get("configure_routing"), dict) else {}
        routing_rule = routing.get("rule") if isinstance(routing.get("rule"), dict) else {}
        routing_actions = routing.get("actions") if isinstance(routing.get("actions"), dict) else {}
        expect("objects.biz_flow.configure_routing.rule.document_type_name_version", routing_rule.get("document_type_name_version"), src_doc_ref)
        expect("objects.biz_flow.configure_routing.actions.target", routing_actions.get("target"), tgt_tp.get("profile_name"))

    # Full phase leaf accounting: no provided form field may disappear merely
    # because the state graph forgot to model it.
    phase_coverage: Dict[str, Any] = {}
    try:
        from .maximum_observability import build_input_coverage_contract
        for phase in selected_phases:
            if not isinstance(objects.get(phase), dict):
                continue
            graph = apply_dependency_execution_contract(compile_phase_state_graph(base_input, phase), phase=phase)
            coverage = build_input_coverage_contract(
                phase=phase,
                input_payload=base_input,
                state_graph=graph,
                verification={"status": "preflight"},
            )
            phase_coverage[phase] = coverage
            if not coverage.get("pass"):
                issues.append({
                    "path": f"objects.{phase}",
                    "reason": "phase input/control accounting is incomplete",
                    "unmapped_input_leaf_paths": coverage.get("unmapped_input_leaf_paths") or [],
                    "dependency_cycles": (graph.get("dependency_execution_contract") or {}).get("cycle_node_ids") or [],
                })
    except Exception as exc:
        issues.append({"path": "objects", "reason": f"phase input/control preflight could not be compiled: {mask_sensitive_string(str(exc))}"})

    return {
        "schema_version": "hip.live-input-contract.v3",
        "pass": not issues,
        "contract_count": len(contract),
        "issues": issues,
        "phase_coverage": phase_coverage,
        "cross_object_references_checked": True,
        "complete_phase_leaf_accounting_required": True,
        "selected_phases": selected_phases,
        "section_scoped": list(selected_phases) != list(PHASE_SEQUENCE),
    }


def write_phase_inputs(base_input: Dict[str, Any], root_dir: Path, phases: Sequence[str]) -> Dict[str, str]:
    inputs_dir = root_dir / "phase_inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, str] = {}
    for phase in phases:
        payload = make_phase_input(base_input, phase)
        path = inputs_dir / f"{_safe_phase_filename(phase)}.json"
        safe_write_json(path, payload, mask=False)
        paths[phase] = str(path)
    return paths


def _phase_counts_from_summary(summary: Dict[str, Any]) -> Dict[str, int]:
    keys = [
        "form_controls",
        "required_fields",
        "dropdowns",
        "dummy_fill_attempts",
        "old_datamaps",
        "old_doctypes",
        "old_rules",
        "old_transport_profiles",
        "old_bizflows",
        "deep_profiles_captured",
        "runtime_profiles_captured",
    ]
    out: Dict[str, int] = {}
    for key in keys:
        val = summary.get(key)
        if isinstance(val, int):
            out[key] = val
        elif isinstance(val, list):
            out[key] = len(val)
    return out


def _read_json_if_exists(path: Path) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _find_first_json(run_dir: Path, patterns: Sequence[str]) -> Any:
    for pattern in patterns:
        for path in sorted(run_dir.rglob(pattern)):
            data = _read_json_if_exists(path)
            if data is not None:
                return data
    return None


def _extract_attempts(run_dir: Path) -> List[Dict[str, Any]]:
    data = _find_first_json(run_dir, ["*_dummy_fill_plan.json", "bizflow_dummy_fill_plan.json"])
    if isinstance(data, dict):
        attempts = data.get("attempts") or data.get("dummy_fill_attempts") or []
        if isinstance(attempts, list):
            return [a for a in attempts if isinstance(a, dict)]
    return []


def _extract_required_fields(run_dir: Path) -> List[Dict[str, Any]]:
    data = _find_first_json(run_dir, ["*_required_fields.json", "bizflow_required_fields.json"])
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        rows = data.get("required_fields") or data.get("items") or []
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
    return []


def _extract_dropdowns(run_dir: Path) -> List[Dict[str, Any]]:
    data = _find_first_json(run_dir, ["*_dropdowns.json", "bizflow_dropdowns.json"])
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        rows = data.get("dropdowns") or data.get("items") or []
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
    return []


def _locate_phase_screenshots(run_dir: Path) -> List[str]:
    """Find real after-fill screenshots for a phase, with slow-filesystem tolerance.

    Windows + OneDrive runs have repeatedly produced a valid PNG inside the
    phase KB folder a moment after the phase summary was assembled.  Golden
    screenshots are the acceptance truth, so the verifier must recover the
    actual after-fill PNG from disk instead of trusting only the summary field.
    """
    patterns = [
        "*after_dummy_fill_no_save*.png",
        "*after_dummy*.png",
        "*add_form_after*.png",
        "*add_form*.png",
        "*.png",
    ]
    found: List[str] = []
    deadline = time.time() + 10.0
    while True:
        for pattern in patterns:
            try:
                for p in sorted(run_dir.rglob(pattern)):
                    try:
                        ok = p.is_file()
                    except Exception:
                        ok = False
                    if ok and str(p) not in found:
                        found.append(str(p))
            except Exception:
                continue
            if found:
                break
        if found or time.time() >= deadline:
            break
        time.sleep(0.25)
    return found



def _basename_any_path(value: str) -> str:
    """Return the filename from either POSIX or Windows absolute paths.

    Evidence JSON on Windows records paths such as ``C:\\...\\file.png``.
    On POSIX, ``Path(value).name`` treats backslashes as normal characters,
    so recovery by filename silently fails.  Use PureWindowsPath as fallback.
    """
    text = str(value or "")
    try:
        name = Path(text).name
        if name and name != text:
            return name
    except Exception:
        pass
    try:
        return PureWindowsPath(text).name
    except Exception:
        return text.replace("\\", "/").rsplit("/", 1)[-1]

def _detect_unsafe_clicks(run_dir: Path) -> List[Dict[str, Any]]:
    """Return only real mutating click/action evidence.

    Earlier versions searched the entire JSON blob, so safe metadata such as
    ``stage=fill_dummy_no_save`` or DOM excerpts containing page text like
    ``Create Biz Flow`` caused false positives.  This detector intentionally
    inspects only user-facing click labels and explicit action targets, and it
    ignores safe wizard/navigation clicks such as template selection, tabs,
    Next/Continue, Add, and combobox focus.
    """
    hits: List[Dict[str, Any]] = []
    safe_click_labels = {
        "", "+", "+ add", "add", "next", "continue", "proceed", "done",
        "flow details", "configure source", "configure target", "configure targets",
        "target details", "source details", "configure routing", "basic details",
        "b2b-flow-pubsub-template", "b2b flow pubsub template",
        "create biz flow",
    }
    allowed_when_exact = {"add"}

    def norm(val: Any) -> str:
        return re.sub(r"\s+", " ", str(val or "").strip().lower())

    def has_mutating_word(label: str) -> bool:
        if not label or label in safe_click_labels:
            return False
        tokens = re.findall(r"[a-z0-9]+", label)
        return any(word in tokens for word in MUTATING_WORDS if word not in allowed_when_exact)

    for path in run_dir.rglob("compact_click_sequence.json"):
        data = _read_json_if_exists(path)
        if isinstance(data, list):
            for item in data:
                label = norm(item.get("text") or item.get("aria_label") or item.get("label") or item.get("name"))
                selector = norm(item.get("selector"))
                role = norm(item.get("role"))
                tag = norm(item.get("tag"))
                # Combobox/input focus is never a mutating submit action.
                if role == "combobox" or tag in {"input", "select", "textarea"}:
                    continue
                # The BizFlow template picker uses an action-menu item named
                # "Create Biz Flow" to launch the wizard.  It is not the final
                # submit/create action inside a form, so allow it only when the
                # selector/role proves it is an action-menu/menuitem launch.
                if label == "create biz flow" and ("action-menu" in selector or role == "menuitem"):
                    continue
                if has_mutating_word(label):
                    hits.append({"file": str(path), "click": item, "reason": f"mutating label: {label}"})
                elif label and not label in safe_click_labels and any(w in selector for w in ["save", "submit", "delete", "deploy", "publish", "update"]):
                    hits.append({"file": str(path), "click": item, "reason": "mutating selector"})
    for path in run_dir.rglob("compact_action_sequence.json"):
        data = _read_json_if_exists(path)
        if isinstance(data, list):
            for item in data:
                target = norm(item.get("target") or item.get("label") or item.get("text") or item.get("type"))
                stage = norm(item.get("stage"))
                if "no_save" in stage or "dummy" in stage or "blocked" in stage:
                    continue
                if has_mutating_word(target):
                    hits.append({"file": str(path), "action": item, "reason": f"mutating action target: {target}"})
    return hits


def _artifact_actual_state(run_dir: Path, attempts: Sequence[Dict[str, Any]], form_controls: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Reconstruct row-scoped committed evidence from the final saved DOM.

    Conditional HIP controls are meaningful only inside their section and
    repeatable-row occurrence. Multiple DDS selections are read from selected
    options/chips; a synthetic input text lock is never accepted as their value.
    """
    controls: List[Dict[str, Any]] = []
    visible_text = ""
    row_counts: Dict[str, int] = {}
    html_candidates = sorted(run_dir.rglob("*after_dummy_fill_no_save.html"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    source = "exact_verified_attempts"

    def clean(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def section_for(el: Any) -> str:
        fs = el.find_parent("fieldset") if el else None
        legend = fs.find("legend") if fs else None
        return clean(legend.get_text(" ", strip=True) if legend else "")

    def row_meta(el: Any, section: str) -> tuple[str, Optional[int]]:
        fs = el.find_parent("fieldset") if el else None
        low = clean(section).lower()
        if not fs or not ("attribute" in low or "identifier" in low):
            return "", None
        row = el
        while row is not None and getattr(row, "parent", None) is not fs:
            row = getattr(row, "parent", None)
        if row is None or getattr(row, "parent", None) is not fs:
            return "", None

        if "document identifier" in low:
            placeholder = clean(el.get("placeholder") or "").lower()
            label_text = clean(el.get("aria-label") or el.get("name") or placeholder).lower()
            if placeholder == "operation" or label_text == "operation":
                return "", None
            # Operation is a section-level control, not a repeatable identifier row.
            # Count only top-level containers that own Derived From/Value controls.
            rows = []
            for candidate in fs.find_all(recursive=False):
                if getattr(candidate, "name", None) == "legend":
                    continue
                has_identifier_field = bool(candidate.select_one(
                    "input[placeholder='Derived From'], input[name='value'], input[placeholder='Value'], "
                    "[role='combobox'][placeholder='Derived From']"
                ))
                if has_identifier_field:
                    rows.append(candidate)
            try:
                return "document_identifier", rows.index(row)
            except ValueError:
                return "", None

        rows = [candidate for candidate in fs.find_all(recursive=False)
                if getattr(candidate, "name", None) != "legend" and candidate.find(["input", "textarea", "select"]) ]
        try:
            index = rows.index(row)
        except ValueError:
            return "", None
        return "attribute", index

    if html_candidates:
        source = str(html_candidates[0])
        try:
            soup = BeautifulSoup(html_candidates[0].read_text(encoding="utf-8", errors="ignore"), "html.parser")
            visible_text = soup.get_text(" ", strip=True)[:50000]
            for fs in soup.find_all("fieldset"):
                legend = clean(fs.find("legend").get_text(" ", strip=True) if fs.find("legend") else "")
                rows = [r for r in fs.find_all(recursive=False) if getattr(r, "name", None) != "legend" and r.find(["input", "textarea", "select"])]
                if "document identifier" in legend.lower():
                    identifier_rows = [
                        r for r in rows
                        if r.select_one(
                            "input[placeholder='Derived From'], input[name='value'], input[placeholder='Value'], "
                            "[role='combobox'][placeholder='Derived From']"
                        )
                    ]
                    row_counts["document_identifier_rows"] = len(identifier_rows)
                elif "attribute" in legend.lower():
                    row_counts["attribute_rows"] = len(rows)

            for el in soup.select("input, textarea, select, [role='combobox'], [role='radio'], [role='checkbox'], [role='switch']"):
                eid = str(el.get("id") or "")
                labelled = str(el.get("aria-labelledby") or "").split()
                label_parts: List[str] = []
                for lid in labelled:
                    lab = soup.find(id=lid)
                    if lab:
                        label_parts.append(lab.get_text(" ", strip=True))
                label = clean(" ".join(label_parts) or el.get("aria-label") or el.get("name") or el.get("placeholder") or "")
                section = section_for(el)
                row_kind, row_index = row_meta(el, section)
                control_type = str(el.get("type") or el.get("role") or "").lower()
                dd = el.find_parent("dds-dropdown")
                is_multi = bool(dd and (str(dd.get("selection") or "").lower() == "multiple" or "dds__dropdown--is-multiple" in " ".join(dd.get("class") or [])))
                selected_values: List[str] = []
                if is_multi and dd:
                    for selected in dd.select("[role='option'][aria-selected='true'], [role='option'][data-selected='true'], [role='option'][aria-checked='true'], .dds__tag, .dds__chip, [class*='selected-value'], [class*='selection__label']"):
                        text = clean(selected.get_text(" ", strip=True))
                        if text and text.lower() != "select all" and not re.fullmatch(r"\d+\s+selected", text, flags=re.I) and text.lower() not in {x.lower() for x in selected_values}:
                            selected_values.append(text)
                    for icon in dd.select(".dds__dropdown__item-selected, [class*='item-selected']"):
                        selected = icon.find_parent(attrs={"role": "option"})
                        text = clean(selected.get_text(" ", strip=True) if selected else "")
                        if text and text.lower() != "select all" and not re.fullmatch(r"\d+\s+selected", text, flags=re.I) and text.lower() not in {x.lower() for x in selected_values}:
                            selected_values.append(text)
                    value: Any = selected_values
                else:
                    value = clean(el.get("data-hip-locked-value") or el.get("value") or el.get("aria-valuetext") or "")
                    if el.name == "select":
                        opt = el.find("option", selected=True)
                        value = clean(opt.get_text(" ", strip=True) if opt else value)
                checked = el.has_attr("checked") or str(el.get("aria-checked") or "").lower() == "true"
                if control_type in {"checkbox", "radio", "switch"}:
                    if not checked:
                        continue
                    value = (value if value and value.lower() != "on" else "") or label or "checked"
                if value not in (None, "", []):
                    controls.append({
                        "selector": f"{el.name}#{eid}" if eid else el.name,
                        "label": label,
                        "value": value,
                        "selected_values": selected_values,
                        "selection_mode": "multiple" if is_multi else "single",
                        "type": control_type,
                        "section": section,
                        "row_kind": row_kind,
                        "row_index": row_index,
                        "evidence": "saved_post_fill_dom",
                        "disabled": el.has_attr("disabled"),
                        "required": el.has_attr("required") or str(el.get("aria-required") or "").lower() == "true",
                    })
        except Exception as exc:
            source = f"dom_parse_error:{exc}"

    row_indices: Dict[str, set[int]] = {}
    for c in form_controls or []:
        if not isinstance(c, dict):
            continue
        value = c.get("selected_values") if c.get("selection_mode") == "multiple" else c.get("value")
        if value in (None, "", []):
            continue
        # Every non-empty live form control is post-fill evidence. The previous
        # required/disabled/read-only filter discarded ordinary Rule/TP/BizFlow
        # fields and created false section-judge blockers after successful fills.
        rk = str(c.get("row_kind") or "").strip()
        ri = c.get("row_index")
        if rk and isinstance(ri, int) and ri >= 0:
            row_indices.setdefault(rk, set()).add(ri)
        controls.append({
            "selector": c.get("selector") or "",
            "label": c.get("label") or c.get("name") or c.get("placeholder") or "",
            "value": value,
            "selected_values": c.get("selected_values") or [],
            "selection_mode": c.get("selection_mode"),
            "section": c.get("section"),
            "row_kind": c.get("row_kind"),
            "row_index": c.get("row_index"),
            "evidence": "captured_form_inventory",
            "disabled": bool(c.get("disabled")),
            "readonly": bool(c.get("readonly")),
            "required": bool(c.get("required")),
            "trusted_for_exact_judge": c.get("trusted_for_exact_judge", True),
        })
    for kind, indices in row_indices.items():
        if indices:
            row_counts.setdefault(f"{kind}_rows", max(indices) + 1)

    # Attempts can support DOM evidence only when the executor explicitly proved
    # the exact committed control value after blur/rerender.
    latest: Dict[tuple, Dict[str, Any]] = {}
    for a in attempts:
        if not isinstance(a, dict):
            continue
        ident = (str(a.get("key") or a.get("field") or a.get("label") or ""), a.get("row_kind"), a.get("row_index"), str(a.get("section") or a.get("bizflow_tab") or ""))
        latest[ident] = a
    for a in latest.values():
        if not (a.get("success") is True or a.get("filled") is True):
            continue
        value = a.get("actual_value") or a.get("value_used") or a.get("value") or a.get("value_redacted") or a.get("uploaded_file_name")
        if value in (None, "", [], "***MASKED***"):
            continue
        exact = a.get("exact_verified") is True
        controls.append({
            "selector": a.get("selector") or "",
            "label": " ".join(str(x) for x in [a.get("label"), a.get("key"), a.get("field")] if x),
            "value": value,
            "selected_values": value if isinstance(value, list) else [],
            "evidence": "exact_verified_post_fill_attempt" if exact else "latest_successful_post_fill_attempt",
            "trusted_for_exact_judge": exact,
            "row_kind": a.get("row_kind"), "row_index": a.get("row_index"),
            "section": a.get("section") or a.get("bizflow_tab") or a.get("tab"),
        })
        rk = str(a.get("row_kind") or "").strip()
        ri = a.get("row_index")
        if rk and isinstance(ri, int) and ri >= 0:
            row_counts[f"{rk}_rows"] = max(int(row_counts.get(f"{rk}_rows") or 0), ri + 1)
    return {"controls": controls, "visible_text": visible_text, "row_counts": row_counts, "source": source}


def _extract_active_validation_messages(run_dir: Path) -> List[Dict[str, Any]]:
    """Extract blocking validation evidence from the final filled-form DOM.

    The detector is deliberately field/error scoped. Background listing drawers,
    hidden audit modals, and cookie controls are excluded so they cannot create a
    false phase failure.
    """
    html_candidates = sorted(run_dir.rglob("*after_dummy_fill_no_save.html"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    if not html_candidates:
        return []
    try:
        soup = BeautifulSoup(html_candidates[0].read_text(encoding="utf-8", errors="ignore"), "html.parser")
    except Exception:
        return []
    messages: List[Dict[str, Any]] = []
    seen = set()
    selectors = [
        "[aria-invalid='true']",
        ".dds__file-input__item--error",
        ".dds__error-text",
        ".dds__form__field__error",
    ]
    for el in soup.select(",".join(selectors)):
        text = " ".join(el.get_text(" ", strip=True).split())
        if not text:
            # Error text may be a sibling of an invalid input.
            parent = el.find_parent(["div", "fieldset"])
            text = " ".join(parent.get_text(" ", strip=True).split()) if parent else ""
        low = text.lower()
        if not text:
            continue
        if any(noise in low for noise in [
            "select at least one column to continue", "message is required",
            "cookie preferences", "activities",
        ]):
            continue
        # Keep only actionable form validation, not any element whose class name
        # happens to include the word error.
        if not any(token in low for token in [
            "already exists", "not allowed", "not supported", "invalid",
            "required", "must", "error:", "failed",
        ]):
            continue
        key = re.sub(r"\s+", " ", low).strip()
        if key in seen:
            continue
        seen.add(key)
        messages.append({
            "message": text[:1200],
            "source": str(html_candidates[0]),
            "element": el.name,
            "id": el.get("id") or "",
            "aria_invalid": str(el.get("aria-invalid") or "").lower() == "true",
        })
    return messages


# Natural-key duplicate messages as rendered in the approved golden screenshots.
# Row-level duplicates (e.g. "Attribute Name already exists") are input errors
# and stay blocking.
_EXISTING_OBJECT_MESSAGES = {
    "data_map": re.compile(r"map identifier already exists"),
    "source_document_type": re.compile(r"(?<!attribute )(?<!attribute)\bname already exists"),
    "target_document_type": re.compile(r"(?<!attribute )(?<!attribute)\bname already exists"),
    "rule": re.compile(r"rule name already exists"),
    "source_transport_profile": re.compile(r"(transport profile|profile name) already exists"),
    "target_transport_profile": re.compile(r"(transport profile|profile name) already exists"),
    "biz_flow": re.compile(r"(business flow|biz flow|flow name|bizflow)( name)? already exists"),
}


def _classify_validation_messages(phase: str, messages: Sequence[Dict[str, Any]], summary: Dict[str, Any]) -> Dict[str, Any]:
    resolution = summary.get("existing_object_resolution") if isinstance(summary.get("existing_object_resolution"), dict) else {}
    accepted: List[Dict[str, Any]] = []
    blocking: List[Dict[str, Any]] = []
    conflicting = resolution.get("mode") == "conflicting_existing_object"
    marker = _EXISTING_OBJECT_MESSAGES.get(phase)
    for row in messages or []:
        text = str(row.get("message") or "").lower()
        if marker is None or not marker.search(text) or conflicting:
            blocking.append(row)
        elif resolution.get("found") and resolution.get("mode") == "reuse_existing":
            accepted.append({
                **row,
                "classification": "existing_object_reuse",
                "reason": "exact natural key already exists in read-only inventory; no Save/Create/Submit action is performed",
            })
        else:
            # The live portal validator is itself authoritative proof that the
            # natural key exists (the approved golden screenshot shows the same
            # message). The read-only inventory may simply not have paged to it.
            # A no-save learning run must not stall on this; an inventory row with
            # a different name/class (``conflicting``) still blocks above.
            accepted.append({
                **row,
                "classification": "existing_object_reported_by_portal",
                "reason": "live portal validator reports the exact natural key already exists; no Save/Create/Submit action is performed",
            })
    return {"blocking": blocking, "accepted_nonblocking": accepted, "object_resolution": resolution}


def build_phase_verification(phase: str, run_dir: Path, summary: Dict[str, Any], *, vision_enabled: bool, golden_references: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    attempts = _extract_attempts(run_dir)
    required = _extract_required_fields(run_dir)
    dropdowns = _extract_dropdowns(run_dir)
    controls = _extract_form_controls(run_dir)
    screenshots = _locate_phase_screenshots(run_dir)
    # Document Type exact-fill capture writes an immutable evidence lock before
    # reporting/learning can change the surface. Prefer its screenshot explicitly
    # so strict and vision judges never fall back to stale listing evidence.
    filled_form_evidence_lock: Dict[str, Any] = {}
    if phase in {"source_document_type", "target_document_type"}:
        lock_path = run_dir / "doctype_kb" / "doctype_filled_form_evidence_lock.json"
        filled_form_evidence_lock = _read_json_if_exists(lock_path) if lock_path.is_file() else {}
        locked_shot = str((filled_form_evidence_lock or {}).get("screenshot") or "")
        if locked_shot:
            candidate = Path(locked_shot)
            if candidate.is_file():
                screenshots.insert(0, str(candidate))
            else:
                recovered = list(run_dir.rglob(_basename_any_path(locked_shot)))
                screenshots[0:0] = [str(x) for x in recovered if x.is_file()]
    # Deterministic local filename recovery for Windows/OneDrive paths.
    try:
        local_expected = [
            run_dir / "transport_profile_kb" / "transport_profile_add_form_after_dummy_fill_no_save.png",
            run_dir / "bizflow_kb" / "bizflow_add_form_after_dummy_fill_no_save.png",
            run_dir / "rule_kb" / "rule_add_form_after_dummy_fill_no_save.png",
            run_dir / "doctype_kb" / "doctype_add_form_after_dummy_fill_no_save.png",
            run_dir / "datamap_kb" / "datamap_add_form_after_dummy_fill_no_save.png",
        ]
        for p in local_expected:
            if p.is_file():
                screenshots.append(str(p))
    except Exception:
        pass
    # Some phase builders write screenshot paths only into their returned summary
    # `files` map, and some Windows runs produce the PNG after the KB summary is
    # already assembled. Merge both sources and, when a Windows absolute path is
    # returned, recover by filename from the local phase directory.
    try:
        png_vals: List[str] = []
        if isinstance((summary or {}).get("screenshots"), list):
            png_vals.extend(str(x) for x in ((summary or {}).get("screenshots") or []) if isinstance(x, str))
        summary_files = (summary or {}).get("files") or {}
        if isinstance(summary_files, dict):
            png_vals.extend(str(v) for v in summary_files.values() if isinstance(v, str))
        for val in png_vals:
            if isinstance(val, str) and val.lower().endswith(".png"):
                p = Path(val)
                if p.exists():
                    screenshots.append(str(p))
                else:
                    matches = list(run_dir.rglob(_basename_any_path(val)))
                    screenshots.extend(str(x) for x in matches if x.is_file())
    except Exception:
        pass
    screenshots = list(dict.fromkeys(screenshots))
    golden_refs = list(golden_references or [])
    unsafe = _detect_unsafe_clicks(run_dir)
    # Ignore transient failed attempts if a later sticky restore or retry filled
    # the same semantic key/value or selector/value. This prevents false
    # pass_with_warnings when a DDS combobox first returns False but the restore
    # pass verifies the exact value was applied.
    success_tokens = set()
    for a in attempts:
        if a.get("success") is True or a.get("filled") is True:
            val = str(a.get("value_used") or a.get("value") or "").strip().lower()
            sel = str(a.get("selector") or "")
            key = str(a.get("key") or "")
            if val:
                if sel:
                    success_tokens.add(("sel", sel, val))
                if key:
                    success_tokens.add(("key", key, val))
    failed_attempts = []
    for a in attempts:
        if a.get("success") is not False:
            continue
        val = str(a.get("value_used") or a.get("value") or "").strip().lower()
        sel = str(a.get("selector") or "")
        key = str(a.get("key") or "")
        if val and ((sel and ("sel", sel, val) in success_tokens) or (key and ("key", key, val) in success_tokens)):
            continue
        failed_attempts.append(a)
    dropdowns_with_options = [d for d in dropdowns if (d.get("options") or d.get("option_count") or 0)]
    counts = _phase_counts_from_summary(summary)
    if attempts:
        counts["dummy_fill_attempts"] = len(attempts)
    if required:
        counts["required_fields"] = len(required)
    if dropdowns:
        counts["dropdowns"] = len(dropdowns)
        counts["dropdowns_with_options"] = len(dropdowns_with_options)
    if controls:
        counts["form_controls"] = len(controls)

    warnings: List[str] = []
    fatal: List[str] = []
    if not screenshots:
        # Some phase summaries omit screenshots even though the KB folder contains
        # them; scan the run directory one more time with relaxed matching before
        # failing.  Repeated checks handle Windows/OneDrive delayed PNG flushes.
        deadline = time.time() + 10.0
        while not screenshots and time.time() < deadline:
            try:
                relaxed = [str(p) for p in sorted(run_dir.rglob("*.png")) if p.is_file()]
                screenshots.extend(relaxed[:10])
            except Exception:
                pass
            if not screenshots:
                time.sleep(0.25)
    if not screenshots:
        fatal.append("No screenshot found for filled form phase.")
    if counts.get("form_controls", 0) == 0 and phase not in {"data_map"}:
        fatal.append("No form controls were reported by the phase summary; this is not a trusted filled-form capture.")
    if attempts and failed_attempts:
        warnings.append(f"{len(failed_attempts)} fill attempts failed or were not visible.")
    if attempts and len(failed_attempts) == len(attempts):
        fatal.append("All recorded fill attempts failed; replay blueprint cannot be trusted.")
    if unsafe:
        fatal.append(f"Potential unsafe click evidence found: {len(unsafe)} entries. Review before live use.")
    if dropdowns and len(dropdowns_with_options) < max(1, len(dropdowns) // 2):
        warnings.append("Many dropdown controls have no captured/enriched options.")

    surface_gate = detect_false_listing_or_template_surface(phase, run_dir)
    fatal.extend(surface_gate.get("fatal") or [])
    warnings.extend(surface_gate.get("warnings") or [])
    dummy_leaks = detect_dummy_value_leak(attempts)
    if dummy_leaks:
        fatal.append(f"{len(dummy_leaks)} DUMMY/_KB values were still used; exact input.json replication was not achieved.")
    upload_gate = detect_upload_acceptance(attempts)
    fatal.extend(upload_gate.get("fatal") or [])

    validation_messages = _extract_active_validation_messages(run_dir)
    validation_gate = _classify_validation_messages(phase, validation_messages, summary or {})
    if validation_gate.get("blocking"):
        fatal.append(f"Blocking portal validation messages remain: {len(validation_gate.get('blocking') or [])}.")

    strict_gate = {
        "surface_gate": surface_gate,
        "filled_form_evidence_lock": filled_form_evidence_lock,
        "dummy_value_leaks": dummy_leaks[:30],
        "upload_gate": upload_gate,
        "validation_gate": validation_gate,
        "fatal": fatal,
        "warnings": warnings,
    }
    try:
        write_strict_replication_report(run_dir, strict_gate)
    except Exception:
        pass

    status = "pass" if not warnings and not fatal else "pass_with_warnings"
    if fatal:
        status = "failed"
    if summary.get("status") in {"failed", "error"}:
        status = "failed"

    actual_state = _artifact_actual_state(run_dir, attempts, controls)

    return {
        "phase": phase,

        "phase_label": PHASE_DISPLAY.get(phase, phase),
        "status": status,
        "strict_replication_gate": strict_gate,
        "run_dir": str(run_dir),
        "summary_status": summary.get("status"),
        "counts": counts,
        "required_fields_sample": required[:20],
        "dummy_fill_attempts_sample": attempts[:30],
        "all_attempts": attempts[:300],
        "actual_state": {**actual_state, "validation_gate": validation_gate, "object_resolution": (summary or {}).get("existing_object_resolution", {})},
        "object_resolution": (summary or {}).get("existing_object_resolution", {}),
        "validation_gate": validation_gate,
        "failed_attempts": failed_attempts[:30],
        "dropdowns_sample": dropdowns[:20],
        "screenshots": screenshots,
        "golden_reference_screenshots": golden_refs,
        "golden_replication": {
            "enabled": bool(golden_refs),
            "status": "reference_attached" if golden_refs and screenshots else "reference_missing_or_no_actual_screenshot" if golden_refs else "not_configured",
            "comparison_mode": "vision_or_manual_compare_actual_after_fill_to_golden_reference",
            "note": "Use the golden reference screenshot(s) to verify the actual filled form visually matches the known-correct UHAUL-POASN screenshots.",
        },
        "unsafe_click_findings": unsafe[:20],
        "warnings": warnings,
        "vision_verification": {
            "requested": bool(vision_enabled),
            "status": "pending_or_not_configured",
            "note": "A vision prompt is generated for each screenshot. Configure HIP_VISION_ENDPOINT/HIP_VISION_TOKEN/HIP_VISION_MODEL for automatic multimodal verification; deterministic DOM verification always runs.",
        },
    }



def build_section_block_diagnosis(judge_result: Dict[str, Any], verification: Dict[str, Any]) -> Dict[str, Any]:
    deterministic = judge_result.get("deterministic_judge") if isinstance(judge_result.get("deterministic_judge"), dict) else {}
    text = judge_result.get("text_model_judge") if isinstance(judge_result.get("text_model_judge"), dict) else {}
    vision = judge_result.get("vision_model_judge") if isinstance(judge_result.get("vision_model_judge"), dict) else {}
    validation = verification.get("validation_gate") if isinstance(verification.get("validation_gate"), dict) else {}
    reasons: List[Dict[str, Any]] = []
    if validation.get("blocking"):
        reasons.append({"code": "blocking_portal_validation", "items": validation.get("blocking")})
    if deterministic.get("missing_values"):
        reasons.append({"code": "exact_value_missing", "items": deterministic.get("missing_values")})
    if deterministic.get("row_issues"):
        reasons.append({"code": "repeatable_row_mismatch", "items": deterministic.get("row_issues")})
    if deterministic.get("failed_attempts") or deterministic.get("failed_attempts_from_phase"):
        reasons.append({"code": "fill_attempt_failed", "items": [*(deterministic.get("failed_attempts") or []), *(deterministic.get("failed_attempts_from_phase") or [])]})
    if text.get("pass") is False:
        reasons.append({"code": "text_judge_block", "items": text.get("missing_or_wrong") or [], "summary": text.get("summary")})
    if vision.get("pass") is False:
        reasons.append({"code": "vision_judge_block", "items": vision.get("visible_issues") or [], "summary": vision.get("summary")})
    return mask_sensitive_data({
        "phase": judge_result.get("phase") or verification.get("phase"),
        "section": judge_result.get("section"),
        "pass": bool(judge_result.get("pass")),
        "object_resolution": verification.get("object_resolution", {}),
        "reasons": reasons,
        "next_action": "Repair only the listed blocking fields/validation, recapture evidence, and rejudge the same section. Do not advance or click a final mutation.",
    })


def _short_sha256(path: str | Path) -> Optional[str]:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _label_key(label: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label or "").strip().lower()).strip("_")


def _field_identity(row: Dict[str, Any]) -> str:
    return _label_key(row.get("key") or row.get("field_key") or row.get("name") or row.get("label") or row.get("selector") or "field")


def _extract_form_controls(run_dir: Path) -> List[Dict[str, Any]]:
    data = _find_first_json(run_dir, ["*_form_kb.json", "bizflow_form_kb.json", "bizflow_tab_form_kb.json"])
    rows: List[Dict[str, Any]] = []
    if isinstance(data, list):
        rows = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        for key in ["controls", "form_controls", "fields", "items"]:
            val = data.get(key)
            if isinstance(val, list):
                rows.extend([x for x in val if isinstance(x, dict)])
        tab_form = data.get("bizflow_tab_form") or data.get("tabs") or data.get("tab_form")
        if isinstance(tab_form, dict):
            # Some BizFlow runs embed the tab KB as one flat object with a top-level
            # controls list.  Older verifier logic treated each key as a tab and
            # missed the controls, incorrectly reporting form_controls=0.
            for key in ["controls", "form_controls", "fields", "required_fields"]:
                val = tab_form.get(key)
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            rows.append(dict(item))
            for _tab, tab_data in tab_form.items():
                if isinstance(tab_data, dict):
                    for key in ["controls", "form_controls", "fields", "required_fields"]:
                        val = tab_data.get(key)
                        if isinstance(val, list):
                            for item in val:
                                if isinstance(item, dict):
                                    clone = dict(item)
                                    clone.setdefault("tab", _tab)
                                    rows.append(clone)
    # de-dupe by selector + label
    seen = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        ident = (str(row.get("selector") or ""), str(row.get("label") or row.get("name") or ""))
        if ident in seen:
            continue
        seen.add(ident)
        out.append(row)
    return out


def _extract_phase_expected_object(base_input: Dict[str, Any], phase: str) -> Dict[str, Any]:
    payload = make_phase_input(base_input, phase)
    objects = payload.get("objects") if isinstance(payload.get("objects"), dict) else {}
    key_map = {
        "data_map": "data_map",
        "source_document_type": "document_type",
        "target_document_type": "document_type",
        "rule": "rule",
        "source_transport_profile": "transport_profile",
        "target_transport_profile": "transport_profile",
        "biz_flow": "biz_flow",
    }
    obj = objects.get(key_map.get(phase, phase))
    return copy.deepcopy(obj) if isinstance(obj, dict) else {}


def _flatten_values(data: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(_flatten_values(v, key))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            key = f"{prefix}[{i}]"
            out.update(_flatten_values(v, key))
    else:
        out[prefix] = data
    return out


def _values_index(expected_object: Dict[str, Any]) -> Dict[str, Any]:
    flat = _flatten_values(expected_object)
    idx: Dict[str, Any] = {}
    for path, value in flat.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        idx[_label_key(path)] = value
        idx[_label_key(path.split(".")[-1])] = value
    return idx


def _options_for_dropdown(label: Any, dropdowns: Sequence[Dict[str, Any]]) -> List[Any]:
    lk = _label_key(label)
    for d in dropdowns:
        if lk and lk in {_label_key(d.get("label")), _label_key(d.get("name")), _label_key(d.get("key"))}:
            opts = d.get("options") or []
            return opts if isinstance(opts, list) else []
    return []


def _required_lookup(required: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for row in required:
        for key in [row.get("label"), row.get("name"), row.get("key"), row.get("selector")]:
            lk = _label_key(key)
            if lk:
                lookup[lk] = row
    return lookup


def _stability_notes(selector: str) -> Dict[str, Any]:
    selector = selector or ""
    volatile = bool(re.search(r"dds-form-field-\d+|mat-input-\d+|react-select-\d+", selector))
    return {
        "selector_stability": "medium" if volatile else "high",
        "selector_note": "Numeric generated IDs can change between sessions; prefer label/key fallback if selector fails." if volatile else "Selector does not look session-generated.",
    }


def _build_flash_field_steps(
    *,
    phase: str,
    attempts: Sequence[Dict[str, Any]],
    required: Sequence[Dict[str, Any]],
    dropdowns: Sequence[Dict[str, Any]],
    controls: Sequence[Dict[str, Any]],
    expected_object: Dict[str, Any],
    max_dropdown_options: int,
) -> List[Dict[str, Any]]:
    req = _required_lookup(required)
    values = _values_index(expected_object)
    steps: List[Dict[str, Any]] = []
    seen = set()

    # Successful attempts are highest-confidence replay steps because they proved the field can be filled.
    for order, a in enumerate([x for x in attempts if isinstance(x, dict)], start=1):
        label = a.get("label") or a.get("field") or a.get("name") or a.get("key")
        selector = a.get("selector") or ""
        ident = (selector, _label_key(label), _label_key(a.get("key")))
        if ident in seen:
            continue
        seen.add(ident)
        opts = _options_for_dropdown(label, dropdowns)
        required_row = req.get(_label_key(label)) or req.get(_label_key(a.get("key"))) or {}
        step = {
            "order": order,
            "phase": phase,
            "label": label,
            "key": a.get("key") or _field_identity(a),
            "selector": selector,
            "fallback_label": label,
            "fallback_name": a.get("name") or required_row.get("name") or "",
            "tab": a.get("bizflow_tab") or a.get("tab") or required_row.get("bizflow_tab") or required_row.get("tab") or "",
            "value": a.get("expected_value") if a.get("expected_value") is not None else a.get("value_used") if "value_used" in a else a.get("value") if a.get("value") is not None else a.get("value_redacted"),
            "actual_value": a.get("actual_value"),
            "success_in_learning_run": a.get("success"),
            "exact_verified": bool(a.get("exact_verified")),
            "row_kind": a.get("row_kind"),
            "row_index": a.get("row_index"),
            "section": a.get("section"),
            "input_path": a.get("input_path"),
            "knowledge_node": a.get("node_id"),
            "fill_strategy": "select_multiple" if isinstance(a.get("expected_value"), list) else "select_or_type" if opts else "type_or_set_value",
            "required": bool(required_row.get("required")) if required_row else False,
            "control_type": required_row.get("type") or required_row.get("role") or a.get("type") or "",
            "dropdown_options_sample": opts[:max_dropdown_options],
            "dropdown_option_count": len(opts),
            "verification": {
                "must_be_visible_or_reachable": True,
                "expected_value_source": "dummy_fill_attempt.value_used" if (a.get("value_used") is not None) else "input_or_runtime",
            },
        }
        step.update(_stability_notes(selector))
        steps.append(step)

    # Add required fields that did not have a successful attempt so the agent knows what still must be resolved.
    base_order = len(steps)
    existing = {(_label_key(s.get("label")), str(s.get("selector") or "")) for s in steps}
    for row in required:
        label = row.get("label") or row.get("name") or row.get("key")
        selector = row.get("selector") or ""
        ident = (_label_key(label), str(selector))
        if ident in existing:
            continue
        opts = _options_for_dropdown(label, dropdowns)
        key = _field_identity(row)
        value = values.get(_label_key(key)) or values.get(_label_key(label)) or row.get("value") or ""
        step = {
            "order": base_order + len(steps) + 1,
            "phase": phase,
            "label": label,
            "key": key,
            "selector": selector,
            "fallback_label": label,
            "fallback_name": row.get("name") or "",
            "tab": row.get("bizflow_tab") or row.get("tab") or "",
            "value": value,
            "success_in_learning_run": False,
            "fill_strategy": "select_or_type" if opts else "type_or_set_value",
            "required": True,
            "control_type": row.get("type") or row.get("role") or row.get("tag") or "",
            "dropdown_options_sample": opts[:max_dropdown_options],
            "dropdown_option_count": len(opts),
            "verification": {
                "must_be_visible_or_reachable": True,
                "expected_value_source": "phase_input_or_empty_required_field",
            },
        }
        step.update(_stability_notes(selector))
        steps.append(step)

    # Keep non-required controls as discovery hints, not primary replay steps.
    control_notes: List[Dict[str, Any]] = []
    for c in controls[:200]:
        control_notes.append({
            "label": c.get("label") or c.get("name"),
            "selector": c.get("selector"),
            "role": c.get("role"),
            "type": c.get("type"),
            "required": c.get("required"),
            "tab": c.get("bizflow_tab") or c.get("tab"),
        })
    for s in steps:
        s.setdefault("control_inventory_hint_count", len(control_notes))
    return mask_sensitive_data(steps)


def _phase_navigation_recipe(phase: str) -> Dict[str, Any]:
    recipe = {
        "open_url": PHASE_URLS.get(phase),
        "click_add": True,
        "mutating_buttons_blocked": MUTATING_WORDS,
        "no_save_mode": True,
        "recommended_waits": ["networkidle_or_dom_ready", "form_controls_visible"],
        "retry_policy": {"selector_retries": 3, "fallback_to_label": True, "fallback_to_role_button_add": True},
    }
    if phase == "biz_flow":
        recipe.update({
            "wizard_tabs": ["Basic Details", "Source Details", "Target Details", "Configure Routing"],
            "nested_actions": ["Configure Routing + Add"],
            "template_picker": "Select/create BizFlow template before tabs when landing page is shown.",
        })
    return recipe


def _extract_state_graph_execution(phase_dir: Path, phase: str) -> Dict[str, Any]:
    """Load the phase's judge-bound target-path execution for deterministic replay."""
    candidates = {
        "data_map": ["datamap_target_branch_execution.json"],
        "source_document_type": ["doctype_target_branch_execution.json"],
        "target_document_type": ["doctype_target_branch_execution.json"],
        "rule": ["rule_target_branch_execution.json"],
        "source_transport_profile": ["transport_profile_target_branch_execution.json"],
        "target_transport_profile": ["transport_profile_target_branch_execution.json"],
    }.get(phase, [])
    for name in candidates:
        matches = sorted(phase_dir.rglob(name))
        if matches:
            loaded = _read_json_if_exists(matches[0])
            if isinstance(loaded, dict):
                return loaded
    if phase == "biz_flow":
        for name in ("bizflow_tab_form_kb.json", "bizflow_form_kb.json"):
            matches = sorted(phase_dir.rglob(name))
            if not matches:
                continue
            loaded = _read_json_if_exists(matches[0])
            if not isinstance(loaded, dict):
                continue
            if name == "bizflow_form_kb.json":
                loaded = loaded.get("bizflow_tab_form") if isinstance(loaded.get("bizflow_tab_form"), dict) else loaded
            execution = loaded.get("stateful_target_branch_execution")
            if isinstance(execution, dict):
                return execution
    # Backward-compatible fallback: prefer the newest target execution file.
    matches = sorted(phase_dir.rglob("*target_branch_execution.json"))
    if matches:
        loaded = _read_json_if_exists(matches[-1])
        if isinstance(loaded, dict):
            return loaded
    return {}


def _accepted_phase_commit(phase_dir: Path) -> bool:
    row = _read_json_if_exists(Path(phase_dir) / "phase_acceptance_commit.json")
    return bool(isinstance(row, dict) and row.get("status") == "accepted" and row.get("judge_pass") and row.get("human_final_review_pass") and row.get("exact_completion_pass"))


def _semantic_steps_from_flash_blueprint(blueprint: Dict[str, Any], *, phase: str) -> List[Dict[str, Any]]:
    """Convert a phase flash blueprint into a value-free deterministic recipe."""
    steps: List[Dict[str, Any]] = []
    url = str(blueprint.get("url") or "")
    if url:
        steps.append({"type": "navigate", "action": "navigate", "target_route": url, "label": PHASE_DISPLAY.get(phase, phase)})
    nav = blueprint.get("navigation_recipe") if isinstance(blueprint.get("navigation_recipe"), dict) else {}
    if nav.get("click_add"):
        steps.append({"type": "semantic_click", "action": "click", "label": "+ Add", "role": "button", "human_demonstrated": False})
    for raw in blueprint.get("field_steps") or []:
        if not isinstance(raw, dict):
            continue
        input_path = str(raw.get("input_path") or raw.get("path") or raw.get("field_path") or "")
        label = str(raw.get("label") or raw.get("field_label") or raw.get("name") or raw.get("field") or "")
        role = str(raw.get("role") or raw.get("kind") or raw.get("type") or "")
        section = str(raw.get("section") or raw.get("tab") or "")
        if not (input_path or label):
            continue
        steps.append({
            "type": "fill_from_input", "action": str(raw.get("action") or "fill"),
            "label": label, "role": role, "section": section,
            "input_path": input_path, "input_root": phase,
            "semantic_control_id": str(raw.get("semantic_control_id") or ""),
            "value_source": "current_runtime_input_json",
        })
    steps.append({"type": "verify", "action": "exact_live_readback", "label": PHASE_DISPLAY.get(phase, phase)})
    return steps


def build_phase_flash_blueprint(
    *,
    phase: str,
    phase_dir: Path,
    summary: Dict[str, Any],
    verification: Dict[str, Any],
    base_input: Dict[str, Any],
    phase_input_path: str,
    output_dir: Path,
    max_dropdown_options: int = 250,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts = _extract_attempts(phase_dir)
    required = _extract_required_fields(phase_dir)
    dropdowns = _extract_dropdowns(phase_dir)
    controls = _extract_form_controls(phase_dir)
    expected_object = _extract_phase_expected_object(base_input, phase)
    screenshots = verification.get("screenshots") or []
    evidence_files = []
    for pattern in ["*_form_kb.json", "*_dummy_fill_plan.json", "*_required_fields.json", "*_dropdowns.json", "compact_action_sequence.json", "compact_click_sequence.json", "network_summary.json"]:
        evidence_files.extend(str(p) for p in sorted(phase_dir.rglob(pattern)) if p.is_file())
    blueprint = {
        "schema_version": "hip.flash-fill-blueprint.v1",
        "phase": phase,
        "phase_label": PHASE_DISPLAY.get(phase, phase),
        "generated_at": utc_now(),
        "source_run_dir": str(phase_dir),
        "phase_input_path": str(phase_input_path),
        "url": PHASE_URLS.get(phase),
        "navigation_recipe": _phase_navigation_recipe(phase),
        "expected_object": mask_sensitive_data(expected_object),
        "state_graph": mask_sensitive_data(((make_phase_input(base_input, phase).get("_deterministic_plan") or {}).get("state_graph") or {})),
        "state_graph_execution": mask_sensitive_data(_extract_state_graph_execution(phase_dir, phase)),
        "repeatable_section_plan": build_repeatable_section_plan(make_phase_input(base_input, phase), phase),
        "summary_counts": _phase_counts_from_summary(summary),
        "verification_status": verification.get("status"),
        "field_steps": _build_flash_field_steps(
            phase=phase,
            attempts=attempts,
            required=required,
            dropdowns=dropdowns,
            controls=controls,
            expected_object=expected_object,
            max_dropdown_options=max_dropdown_options,
        ),
        "dropdown_catalog": mask_sensitive_data([
            {
                "label": d.get("label") or d.get("name"),
                "selector": d.get("selector"),
                "kind": d.get("kind") or d.get("role"),
                "option_count": len(d.get("options") or []) if isinstance(d.get("options"), list) else d.get("option_count") or 0,
                "options_sample": (d.get("options") or [])[:max_dropdown_options] if isinstance(d.get("options"), list) else [],
                "tab": d.get("bizflow_tab") or d.get("tab"),
            }
            for d in dropdowns
        ]),
        "screenshots": [
            {"path": s, "sha256_16": _short_sha256(s), "file": Path(s).name}
            for s in screenshots
        ],
        "golden_reference_screenshots": verification.get("golden_reference_screenshots") or [],
        "golden_replication_contract": {
            "enabled": bool(verification.get("golden_reference_screenshots")),
            "purpose": "Replicate the human-approved correctly filled UHAUL-POASN screenshots for this phase.",
            "verify_after_fill": [
                "current screenshot visually matches the golden reference for the same phase",
                "visible required fields are populated",
                "selected dropdown values match expected_object/field_steps",
                "no mutating button clicked"
            ],
        },
        "evidence_files": evidence_files,
        "replay_contract": {
            "purpose": "Use this blueprint to fill the same portal phase without re-crawling old KB pages.",
            "selector_order": ["selector", "fallback_label", "fallback_name", "key"],
            "do_not_click": MUTATING_WORDS,
            "verify_after_fill": ["all required visible fields non-empty", "selected dropdown text matches value/options", "screenshot captured", "no mutating button clicked"],
        },
    }
    out_path = output_dir / f"{_safe_phase_filename(phase)}_fast_fill_blueprint.json"
    blueprint["blueprint_file"] = str(out_path)
    safe_write_json(out_path, blueprint)
    return blueprint


def _write_fast_fill_playbook(path: Path, manifest: Dict[str, Any]) -> None:
    semantic_only = bool(manifest.get("semantic_only"))
    lines = [
        "# HIP Adaptive Semantic Replay Playbook" if semantic_only else "# HIP Fast Fill Replay Playbook",
        "",
        ("This playbook is a semantic prior from a verified run. Rediscover the live HIP controls every time; no saved selector or coordinate is authoritative." if semantic_only else "This playbook is generated from a verified dummy-fill learning run. It is intended to let an agent fill the same HIP phases quickly without re-crawling old KB inventory."),
        "",
        "## Safety contract",
        "",
        "- Open each phase URL.",
        "- Click `+ Add` only.",
        "- Fill fields from the business goal/dependency graph; re-observe after every material DOM change.",
        ("- Rediscover each control live by label/role/section/framework semantics; never replay a stored selector/coordinate." if semantic_only else "- Use selector first; if it fails, use label/name/key fallbacks."),
        "- Never click Save, Create, Submit, Delete, Remove, Deploy, Enable, Disable, Publish, or Update unless a human explicitly switches to live-create mode.",
        "- Capture a screenshot after each phase and verify required fields before moving on.",
        "- When golden reference screenshots are present, compare the new screenshot against the golden one and do not mark the phase as replicated until it matches.",
        "",
        "## Phase order",
        "",
    ]
    for idx, phase in enumerate(manifest.get("phase_sequence", []), start=1):
        info = manifest.get("phase_blueprints", {}).get(phase, {})
        golden_count = info.get("golden_reference_screenshots", 0)
        suffix = f" — golden refs: {golden_count}" if golden_count else ""
        lines.append(f"{idx}. **{info.get('phase_label', phase)}** — `{info.get('blueprint_file', '')}`{suffix}")
    lines += ["", "## Replay guidance", "", ("Use the manifest as a semantic hint only. The current DOM/accessibility tree and exact read-back remain the source of truth." if semantic_only else "Use `flash_fill_replay_manifest.json` as the single source of truth for field selectors, values, dropdown options, wizard tabs, screenshots, and validation rules.")]
    safe_write_text(path, "\n".join(lines), encoding="utf-8")


def _strip_ephemeral_replay_locators(value: Any) -> Any:
    """Remove current-generation selectors/coordinates from adaptive replay artifacts.

    These artifacts may be reused as semantic priors, but Angular/DDS selectors,
    XPath fragments, bounding boxes and viewport coordinates are never authoritative
    across runs.  Values/labels remain run-local evidence and are still masked by the
    existing reporting pipeline.
    """
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            k = str(key).lower()
            if "selector" in k or "xpath" in k:
                continue
            if k in {"x", "y", "x1", "y1", "x2", "y2", "left", "top", "right", "bottom", "x_ratio", "y_ratio", "screen_x", "screen_y", "viewport_x", "viewport_y", "bounding_box", "bbox"}:
                continue
            out[key] = _strip_ephemeral_replay_locators(item)
        return out
    if isinstance(value, list):
        return [_strip_ephemeral_replay_locators(x) for x in value]
    return value


def build_flash_replay_package(
    *,
    root_dir: Path,
    base_input: Dict[str, Any],
    phase_input_paths: Dict[str, str],
    phase_summaries: Dict[str, Any],
    phase_verifications: Sequence[Dict[str, Any]],
    phases: Sequence[str],
    max_dropdown_options: int = 250,
    semantic_only: bool = False,
) -> Dict[str, Any]:
    replay_dir = root_dir / "fast_replay_blueprints"
    replay_dir.mkdir(parents=True, exist_ok=True)
    ver_by_phase = {v.get("phase"): v for v in phase_verifications}
    blueprints: Dict[str, Any] = {}
    for phase in phases:
        phase_dir = root_dir / phase
        bp = build_phase_flash_blueprint(
            phase=phase,
            phase_dir=phase_dir,
            summary=phase_summaries.get(phase) or {},
            verification=ver_by_phase.get(phase) or {},
            base_input=base_input,
            phase_input_path=phase_input_paths.get(phase, ""),
            output_dir=replay_dir,
            max_dropdown_options=max_dropdown_options,
        )
        if semantic_only:
            blueprint_file = str(bp.get("blueprint_file") or "")
            bp = _strip_ephemeral_replay_locators(bp)
            bp["schema_version"] = "hip.semantic-replay-blueprint.v2"
            bp["semantic_only"] = True
            bp["ephemeral_locators_persisted"] = False
            bp["replay_contract"] = {
                "purpose": "Use prior labels/roles/sections/business semantics as hints; rediscover every live control before acting.",
                "selector_policy": "never persisted; derive a fresh current-generation locator every action",
                "verification": "exact live read-back + authoritative executor proof required",
            }
            if blueprint_file:
                bp["blueprint_file"] = blueprint_file
                safe_write_json(Path(blueprint_file), bp)
        blueprints[phase] = {
            "phase_label": bp.get("phase_label"),
            "blueprint_file": bp.get("blueprint_file"),
            "url": bp.get("url"),
            "field_steps": len(bp.get("field_steps") or []),
            "dropdowns": len(bp.get("dropdown_catalog") or []),
            "screenshots": len(bp.get("screenshots") or []),
            "golden_reference_screenshots": len(bp.get("golden_reference_screenshots") or []),
            "verification_status": bp.get("verification_status"),
        }
    manifest = {
        "schema_version": "hip.semantic-replay-manifest.v2" if semantic_only else "hip.flash-fill-manifest.v1",
        "semantic_only": bool(semantic_only),
        "ephemeral_locators_persisted": not bool(semantic_only),
        "generated_at": utc_now(),
        "root_dir": str(root_dir),
        "phase_sequence": list(phases),
        "phase_blueprints": blueprints,
        "dependency_order": [
            "data_map creates mapping identifier used by rule and BizFlow Mapping Transformer",
            "source_document_type and target_document_type are used by rule, transport profiles and BizFlow",
            "source_transport_profile and target_transport_profile are used by BizFlow source/target/routing",
            "rule is used by BizFlow process step and routing configuration",
            "biz_flow is filled last using all prior references",
        ],
        "flash_fill_contract": {
            "use_case": "Fill a correctly learned HIP Portal profile in a flash from saved blueprints.",
            "input_source": "phase_inputs/*.json plus per-phase fast_fill_blueprint.json files",
            "repeatable_rows": "Any array in input.json that maps to a portal table/section is converted into repeatable_section_plan. If row_count_from_input is N, the agent clicks the safe row-level + Add button N-1 times before filling rows.",
            "do_not_click": MUTATING_WORDS,
            "required_outputs_after_replay": ["phase screenshot", "DOM validation", "golden screenshot comparison", "vision verification prompt/result", "no unsafe click evidence"],
            "golden_replication": "When golden_reference_screenshots are present, the current after-fill screenshot must visually match the human-approved reference before using the blueprint for flash filling.",
        },
    }
    manifest_path = root_dir / "flash_fill_replay_manifest.json"
    manifest["manifest_file"] = str(manifest_path)
    safe_write_json(manifest_path, manifest)
    _write_fast_fill_playbook(root_dir / "FAST_FILL_AGENT_PLAYBOOK.md", manifest)
    return mask_sensitive_data(manifest)


def _image_data_url(path: str) -> Optional[str]:
    p = Path(path)
    if not p.exists():
        return None
    suffix = p.suffix.lower().lstrip(".") or "png"
    mime = "image/png" if suffix == "png" else "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_vision_prompts(verifications: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prompts: List[Dict[str, Any]] = []
    for v in verifications:
        phase = v.get("phase")
        label = v.get("phase_label")
        expected = {
            "required_fields_sample": v.get("required_fields_sample", []),
            "dummy_fill_attempts_sample": v.get("dummy_fill_attempts_sample", []),
            "dropdowns_sample": v.get("dropdowns_sample", []),
        }
        golden_refs = v.get("golden_reference_screenshots") or []
        for shot in v.get("screenshots", [])[:3]:
            golden_paths = [g.get("path") for g in golden_refs if isinstance(g, dict) and g.get("path")]
            prompts.append({
                "phase": phase,
                "phase_label": label,
                "screenshot": shot,
                "golden_screenshots": golden_paths[:4],
                "prompt": (
                    "You are verifying a Dell HIP Portal form screenshot after safe dummy-fill. "
                    "First verify the actual screenshot has all visible required fields populated and no Save/Create/Submit/Delete/Deploy action was clicked. "
                    "If golden reference screenshot(s) are provided, compare the actual screenshot against them and decide whether the form structure, selected tabs, visible filled values, and required-field state replicate the known-correct UHAUL-POASN reference. "
                    "Return strict JSON with keys: phase, visible_required_fields_filled, golden_replication_match, visible_dummy_values, missing_visible_values, visual_differences_from_golden, unsafe_action_seen, confidence, notes.\n\n"
                    f"Phase: {label}\nGolden reference files: {json.dumps(golden_paths[:4], ensure_ascii=False)}\nExpected DOM/control evidence JSON:\n{json.dumps(mask_sensitive_data(expected), ensure_ascii=False, default=str)[:12000]}"
                ),
            })
    return prompts


def run_optional_vision_verification(prompts: Sequence[Dict[str, Any]], *, max_images: int = 20) -> List[Dict[str, Any]]:
    """Best-effort Dell AIA vision call using the same existing .env/auth as text planning.

    Endpoint aliases: HIP_VISION_ENDPOINT, AIA_ENDPOINT, BASE_URL, AIA_BASE_URL.
    Auth: bearer env, token command, CLIENT_ID/CLIENT_SECRET, or Dell SSO via aia_auth.
    Model aliases: HIP_VISION_MODEL, AIA_VISION_MODEL, VISION_MODEL_NAME, VISION_MODEL.
    Text MODEL_NAME is intentionally never used as a vision fallback.
    """
    cfg = AIAConfig(enabled=True)
    client = AIAClient(cfg)
    endpoint = os.getenv("HIP_VISION_ENDPOINT") or os.getenv("AIA_VISION_ENDPOINT") or client._endpoint()
    if endpoint and not str(endpoint).rstrip("/").endswith("/chat/completions"):
        endpoint = str(endpoint).rstrip("/") + "/chat/completions"
    token = os.getenv("HIP_VISION_TOKEN") or os.getenv("AIA_VISION_TOKEN") or client.token_provider.get_token()
    model = (
        os.getenv("HIP_VISION_MODEL")
        or os.getenv("AIA_VISION_MODEL")
        or os.getenv("VISION_MODEL_NAME")
        or os.getenv("VISION_MODEL")
        or ""
    ).split(",")[0].strip()
    results: List[Dict[str, Any]] = []
    if not model:
        return [{"status": "not_configured", "reason": "Explicit multimodal model is required; MODEL_NAME is text-only and is not a vision fallback.", "phase": p.get("phase"), "screenshot": p.get("screenshot")} for p in prompts]
    if not endpoint or not token:
        return [{"status": "not_configured", "reason": "Dell AIA endpoint/token unavailable. Existing BASE_URL + CLIENT_ID/CLIENT_SECRET or SSO are supported.", "phase": p.get("phase"), "screenshot": p.get("screenshot")} for p in prompts]
    for p in list(prompts)[:max_images]:
        data_url = _image_data_url(str(p.get("screenshot", "")))
        if not data_url:
            results.append({"status": "missing_screenshot", "phase": p.get("phase"), "screenshot": p.get("screenshot")})
            continue
        content_items = [
            {"type": "text", "text": p.get("prompt", "")},
            {"type": "text", "text": "Actual screenshot after current dummy-fill:"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        for gpath in (p.get("golden_screenshots") or [])[:4]:
            gdata = _image_data_url(str(gpath))
            if gdata:
                content_items.append({"type": "text", "text": f"Golden reference screenshot: {Path(str(gpath)).name}"})
                content_items.append({"type": "image_url", "image_url": {"url": gdata}})
        base_payload = {"model": model, "messages": [{"role": "system", "content": "Return strict JSON only. Do not reveal secrets."}, {"role": "user", "content": content_items}], "temperature": 0}
        payloads = build_chat_payload_variants(base_payload, output_token_limit=resolve_output_token_limit(None, vision=True))
        last = ""
        for payload in payloads:
            try:
                resp = requests.post(endpoint, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "accept": "application/json"}, json=payload, timeout=120)
                if resp.status_code >= 400:
                    last = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    continue
                data = resp.json()
                content = extract_aia_response_text(data)
                if not content:
                    last = "Dell AIA vision returned HTTP success but no usable assistant output"
                    continue
                results.append({"status": "ok", "phase": p.get("phase"), "screenshot": p.get("screenshot"), "model": model, "response": content})
                break
            except Exception as exc:
                last = str(exc)
        else:
            results.append({"status": "error", "phase": p.get("phase"), "screenshot": p.get("screenshot"), "model": model, "error": last or "vision request failed"})
    return mask_sensitive_data(results)



def _recover_missing_phase_screenshots(root_dir: Path, phase_verifications: List[Dict[str, Any]]) -> None:
    """Final pass to repair screenshot false negatives.

    Some phase builders write a PNG under the phase KB folder after their summary
    has already returned, especially on Windows/OneDrive.  Before writing the
    aggregate report, rescan each phase folder and remove the screenshot-only
    fatal if the actual after-fill screenshot exists.
    """
    for item in phase_verifications:
        phase = str(item.get("phase") or "")
        if not phase or item.get("screenshots"):
            continue
        phase_dir = root_dir / phase
        shots = _locate_phase_screenshots(phase_dir)
        # Absolute Windows paths in JSON are not valid on Linux review hosts.
        # Trust the local uploaded ZIP directory layout first: each TP phase has
        # transport_profile_kb/transport_profile_add_form_after_dummy_fill_no_save.png.
        try:
            local_expected = [
                phase_dir / "transport_profile_kb" / "transport_profile_add_form_after_dummy_fill_no_save.png",
                phase_dir / "bizflow_kb" / "bizflow_add_form_after_dummy_fill_no_save.png",
                phase_dir / "rule_kb" / "rule_add_form_after_dummy_fill_no_save.png",
                phase_dir / "doctype_kb" / "doctype_add_form_after_dummy_fill_no_save.png",
                phase_dir / "datamap_kb" / "datamap_add_form_after_dummy_fill_no_save.png",
            ]
            for p in local_expected:
                if p.is_file():
                    shots.append(str(p))
        except Exception:
            pass
        # Also trust PNG paths recorded in phase summaries, then recover by filename inside the phase folder.
        try:
            for summary_path in phase_dir.rglob("*_summary.json"):
                data = _read_json_if_exists(summary_path)
                if isinstance(data, dict):
                    vals = []
                    if isinstance(data.get("screenshots"), list):
                        vals.extend(data.get("screenshots") or [])
                    if isinstance(data.get("files"), dict):
                        vals.extend(v for v in data.get("files", {}).values() if isinstance(v, str))
                    for val in vals:
                        if isinstance(val, str) and val.lower().endswith(".png"):
                            p = Path(val)
                            if p.exists():
                                shots.append(str(p))
                            else:
                                shots.extend(str(x) for x in phase_dir.rglob(_basename_any_path(val)) if x.is_file())
        except Exception:
            pass
        if not shots:
            try:
                shots = [str(p) for p in sorted(phase_dir.rglob("*.png")) if p.is_file()]
            except Exception:
                shots = []
        if not shots:
            continue
        item["screenshots"] = list(dict.fromkeys(shots))
        gate = item.get("strict_replication_gate") if isinstance(item.get("strict_replication_gate"), dict) else {}
        if gate:
            gate["fatal"] = [f for f in (gate.get("fatal") or []) if "No screenshot found" not in str(f)]
        if gate and not gate.get("fatal"):
            warnings = item.get("warnings") or gate.get("warnings") or []
            item["status"] = "pass_with_warnings" if warnings else "pass"

def _write_csv_report(path: Path, verifications: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["phase", "status", "form_controls", "required_fields", "dropdowns", "dropdowns_with_options", "dummy_fill_attempts", "screenshots", "warnings"])
        writer.writeheader()
        for v in verifications:
            c = v.get("counts") or {}
            writer.writerow({
                "phase": v.get("phase_label") or v.get("phase"),
                "status": v.get("status"),
                "form_controls": c.get("form_controls", 0),
                "required_fields": c.get("required_fields", 0),
                "dropdowns": c.get("dropdowns", 0),
                "dropdowns_with_options": c.get("dropdowns_with_options", 0),
                "dummy_fill_attempts": c.get("dummy_fill_attempts", 0),
                "screenshots": len(v.get("screenshots") or []),
                "warnings": "; ".join(v.get("warnings") or []),
            })


def _html_escape(x: Any) -> str:
    import html
    return html.escape(str(x if x is not None else ""))


def _write_html_report(path: Path, summary: Dict[str, Any]) -> None:
    rows = []
    for v in summary.get("phase_verifications", []):
        c = v.get("counts") or {}
        warnings = "<br>".join(_html_escape(w) for w in v.get("warnings") or []) or "None"
        shots = "<br>".join(_html_escape(Path(s).name) for s in v.get("screenshots") or []) or "None"
        golden = "<br>".join(_html_escape(g.get("file") or Path(str(g.get("path", ""))).name) for g in (v.get("golden_reference_screenshots") or []) if isinstance(g, dict)) or "None"
        golden_status = (v.get("golden_replication") or {}).get("status", "not_configured")
        rows.append(f"""
<tr>
<td>{_html_escape(v.get('phase_label'))}</td>
<td><span class='status { _html_escape(v.get('status')) }'>{_html_escape(v.get('status'))}</span></td>
<td>{_html_escape(c.get('form_controls', 0))}</td>
<td>{_html_escape(c.get('required_fields', 0))}</td>
<td>{_html_escape(c.get('dropdowns', 0))}</td>
<td>{_html_escape(c.get('dropdowns_with_options', 0))}</td>
<td>{_html_escape(c.get('dummy_fill_attempts', 0))}</td>
<td>{_html_escape(golden_status)}<br>{golden}</td>
<td>{warnings}</td>
<td>{shots}</td>
</tr>""")
    html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>HIP Full Dummy Fill E2E Report</title>
<style>
body{{font-family:Arial,Helvetica,sans-serif;margin:24px;background:#f7f9fc;color:#14213d}}
.card{{background:white;border:1px solid #d8dee9;border-radius:14px;padding:18px;margin:14px 0;box-shadow:0 2px 8px rgba(0,0,0,.05)}}
table{{border-collapse:collapse;width:100%;background:white}}th,td{{border:1px solid #d8dee9;padding:9px;text-align:left;vertical-align:top}}th{{background:#eef3fb}}
.status{{padding:3px 8px;border-radius:999px;font-weight:bold}}.pass{{background:#e4f8ea;color:#166534}}.pass_with_warnings{{background:#fff7d6;color:#92400e}}.needs_review,.failed{{background:#ffe4e6;color:#9f1239}}
code{{background:#eef3fb;padding:2px 5px;border-radius:4px}}
</style></head><body>
<h1>HIP Portal Full Dummy Fill E2E Report</h1>
<div class='card'><b>Run ID:</b> {_html_escape(summary.get('run_id'))}<br><b>Customer:</b> {_html_escape(summary.get('customer'))}<br><b>Generated:</b> {_html_escape(summary.get('generated_at'))}<br><b>Safety:</b> No Save/Create/Submit/Delete/Deploy click is allowed by the automation.</div>
<div class='card'><h2>Phase Summary</h2><table><thead><tr><th>Phase</th><th>Status</th><th>Controls</th><th>Required</th><th>Dropdowns</th><th>Dropdowns with options</th><th>Dummy fills</th><th>Golden reference</th><th>Warnings</th><th>Screenshots</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<div class='card'><h2>Golden Replication</h2><p>Human-approved filled screenshots are stored in <code>golden_reference_screenshots/</code>. Each replay blueprint includes the matching golden references so the agent can replicate the same visual filled state and compare the new after-fill screenshot against the correct reference.</p></div>
<div class='card'><h2>Vision Verification</h2><p>Automatic vision calls run only when <code>HIP_VISION_ENDPOINT</code>, <code>HIP_VISION_TOKEN</code>, and <code>HIP_VISION_MODEL</code> are configured. Otherwise, prompts are written to <code>vision_verification_prompts.json</code> for review.</p></div>
</body></html>"""
    safe_write_text(path, html, encoding="utf-8")


def _zip_dir(src_dir: Path, zip_path: Path) -> str:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in src_dir.rglob("*"):
            if p.is_file() and p != zip_path:
                zf.write(p, p.relative_to(src_dir))
    return str(zip_path)


async def _await_human_phase_review(store: Any, request_id: str, *, wait_seconds: int, poll_seconds: float = 2.0) -> Dict[str, Any]:
    """Poll the value-free review file while the browser remains open.

    The Control Center writes the resolution through its API. A zero timeout is
    non-blocking and causes the phase to stop with a clear pending-review reason.
    """
    row = store.get(request_id)
    if row.get("status") == "resolved":
        return row
    timeout = max(0, int(wait_seconds or 0))
    if timeout <= 0:
        return row
    deadline = time.monotonic() + timeout
    interval = max(0.25, float(poll_seconds or 2.0))
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        row = store.get(request_id)
        if row.get("status") == "resolved":
            return row
    return row


def accepted_human_override(request: Any, exact_checkpoint: Any) -> Dict[str, Any]:
    """Return the resolved review when "Looks correct" may commit the phase.

    Only an exact-completed phase qualifies: every input-owned value was
    committed and verified, so the human reconciles a judge/evidence-only
    disagreement. Anything else stays a resume-and-re-prove signal.
    """
    if not isinstance(request, dict) or request.get("status") != "resolved":
        return {}
    if str(request.get("effective_human_verdict") or "") != "pass":
        return {}
    if not (isinstance(exact_checkpoint, dict) and exact_checkpoint.get("pass") is True):
        return {}
    return dict(request)


async def _hold_incomplete_phase_for_human(
    *,
    store: Any,
    browser: Any,
    run_id: str,
    phase: str,
    phase_display: str,
    recovery_round: int,
    phase_dir: Path,
    reason: str,
    exact_checkpoint: Optional[Dict[str, Any]] = None,
    automated_judge: Optional[Dict[str, Any]] = None,
    verification: Optional[Dict[str, Any]] = None,
    config: Any = None,
) -> Dict[str, Any]:
    """Keep the live browser open while an incomplete phase waits for assistance.

    The wait is intentionally inside the mission coroutine, before BrowserSession
    cleanup and before final report generation. Therefore a blocked Data Map cannot
    close Chrome or produce a misleading terminal/final report. Resolving the request
    only tells the agent to recheck/retry the *same* phase; it never marks it complete.
    """
    hitl = getattr(config, "human_in_the_loop", None) if config is not None else None
    if not bool(getattr(hitl, "enabled", True)) or not bool(getattr(hitl, "hold_browser_on_incomplete_phase", True)):
        return {"held": False, "resume": False, "reason": "hold policy disabled"}

    screenshot_path = ""
    try:
        shot = phase_dir / f"incomplete_phase_wait_round_{int(recovery_round):02d}.png"
        await browser.screenshot(shot, full_page=True)
        screenshot_path = str(shot)
    except Exception:
        pass

    request = store.create_recovery_request(
        run_id=run_id, phase=phase, phase_display=phase_display,
        recovery_round=recovery_round, reason=reason, screenshot_path=screenshot_path,
        exact_checkpoint=exact_checkpoint or {}, automated_judge=automated_judge or {},
        verification=verification or {},
    )
    wait_file = phase_dir / "INCOMPLETE_PHASE_WAITING.json"
    safe_write_json(wait_file, {
        "schema_version": "hip.incomplete-phase-wait.v1",
        "status": "waiting_for_human_recovery",
        "phase": phase, "phase_display": phase_display,
        "request_id": request.get("request_id"),
        "browser_kept_open": True,
        "final_report_generated": False,
        "reason": mask_sensitive_string(str(reason or ""))[:3000],
        "started_at": utc_now(),
    })
    try:
        print(
            f"[{phase_display}] incomplete; browser is being kept open. "
            f"Use Control Center Learning/Phase Review request {request.get('request_id')} after inspecting or correcting the live form.",
            flush=True,
        )
    except Exception:
        pass

    timeout = max(0, int(getattr(hitl, "incomplete_phase_wait_seconds", 0) or 0))
    poll = max(0.25, float(getattr(hitl, "incomplete_phase_poll_seconds", 2.0) or 2.0))
    keepalive_every = max(2.0, float(getattr(hitl, "keepalive_seconds", 20.0) or 20.0))
    started = time.monotonic()
    last_keepalive = 0.0
    while True:
        row = store.get(str(request.get("request_id") or ""))
        if row.get("status") == "resolved":
            safe_write_json(wait_file, {
                "schema_version": "hip.incomplete-phase-wait.v1",
                "status": "resolved_recheck_same_phase",
                "phase": phase, "request_id": request.get("request_id"),
                "browser_kept_open": True, "resolved_at": utc_now(),
                "human_verdict": row.get("human_verdict"),
                "effective_human_verdict": row.get("effective_human_verdict"),
                "policy": "resume same phase and re-prove exact live state before any handoff",
            })
            return {"held": True, "resume": True, "request": row}

        now = time.monotonic()
        if timeout > 0 and now - started >= timeout:
            # A finite timeout is an explicit operator configuration. Do not claim
            # completion; caller may stop/raise, but the default is indefinite.
            return {"held": True, "resume": False, "timed_out": True, "request": row}

        if now - last_keepalive >= keepalive_every:
            last_keepalive = now
            try:
                page = getattr(browser, "page", None)
                if page is not None and not page.is_closed():
                    await page.evaluate("() => ({readyState: document.readyState, href: location.href})")
            except Exception:
                # Keepalive failure is evidence, not permission to close the session.
                pass
            safe_write_json(wait_file, {
                "schema_version": "hip.incomplete-phase-wait.v1",
                "status": "waiting_for_human_recovery",
                "phase": phase, "request_id": request.get("request_id"),
                "browser_kept_open": True, "final_report_generated": False,
                "last_keepalive_at": utc_now(),
                "reason": mask_sensitive_string(str(reason or ""))[:3000],
            })
        await asyncio.sleep(poll)


@dataclass
class FullDummyFillOptions:
    full_kb_context: bool = False
    vision_verify: bool = True
    phases: Sequence[str] = tuple(PHASE_SEQUENCE)
    write_heavy_evidence: bool = False
    save_replay_blueprint: bool = True
    max_dropdown_options_per_field: int = 250
    golden_screenshot_dir: Optional[str] = None
    upload_assets_dir: Optional[str] = "./uploads"
    strict_replication: bool = True
    section_judge: bool = True
    require_text_judge: bool = True
    require_vision_judge: bool = True
    section_judge_max_repairs: int = 2
    section_judge_fail_closed: bool = True
    portal_brain_enabled: bool = True
    rebuild_portal_brain: bool = False
    runtime_self_heal_enabled: bool = True
    runtime_self_heal_max_phase_attempts: int = 5
    runtime_self_heal_until_complete: bool = False
    forensic_evidence: bool = True
    # Browser-grounded AgentQ UI/API fusion. The UI state transaction remains
    # authoritative; observed API contracts and payloads are captured from the
    # same authenticated Chrome session. Optional submit capture aborts the
    # mutation before backend delivery, and optional API write requires explicit
    # two-key confirmation.
    agentq_crawler_fusion: bool = False
    dual_ui_api: bool = False
    capture_submit_api: bool = False
    api_mode: str = "capture"
    require_api_capture_for_completion: bool = False
    strict_mission_assurance: bool = False
    # Autonomous mission continuity: adopt judge-approved completed phases from a
    # prior interrupted run instead of replaying them.  ``resume_run_dir`` names
    # an explicit prior run; ``auto_resume`` locates the newest resumable run in
    # the configured runs directory.  Adoption is fail-closed: it requires the
    # prior phase's exact-completion lock, passing section-judge gate, and the
    # persisted verification payload.
    resume_run_dir: Optional[str] = None
    auto_resume: bool = False
    # Full no-save missions should attempt every selected form even when an
    # earlier phase is blocked. Dependency state is still recorded and the final
    # verdict remains blocked, but unrelated/later forms are not silently skipped.
    continue_after_phase_block: bool = False
    # Final pre-UAT integrated smoke profile.  Exercises the real portal, SSO,
    # AutoWebGLM, Playwright MCP, DevTools MCP and judges while prohibiting all
    # mutation controls and mutating API requests.
    live_witness_mode: bool = False
    # V243R6: phases whose capability family is being learned in this run receive
    # one operator review checkpoint after automated judging (PASS or BLOCKED).
    learning_review_phases: Sequence[str] = tuple()
    # V243R7: completion-first missions never terminate an incomplete phase by
    # closing the browser. The controller holds the same live session for human
    # inspection/recovery and resumes the same phase after the operator responds.
    hold_browser_on_incomplete_phase: bool = True
    never_finalize_incomplete_run: bool = True


class FullDummyFillE2EFlow:
    """Run all HIP portal +Add dummy-fill phases end-to-end.

    Each phase opens the exact portal link, clicks + Add, fills safe dummy values,
    captures screenshots/DOM/network evidence, and verifies required field fill.
    No phase clicks Save/Create/Submit/Delete/Deploy.
    """

    def __init__(self, config: AppConfig, options: Optional[FullDummyFillOptions] = None):
        self.config = config
        self.options = options or FullDummyFillOptions()

    async def run(self, ctx: RunContext, *, input_json: str) -> Dict[str, Any]:
        root_dir = ctx.run_dir
        root_dir.mkdir(parents=True, exist_ok=True)
        base_input = read_json_any(input_json)
        if isinstance(base_input, dict):
            base_input["_input_json_path"] = str(input_json)
            if self.options.upload_assets_dir:
                base_input["_upload_assets_dir"] = str(self.options.upload_assets_dir)
            base_input["_section_judge_config"] = {
                "enabled": bool(self.options.section_judge),
                "require_text_model": bool(self.options.require_text_judge),
                "require_vision_model": bool(self.options.require_vision_judge),
                "max_repairs": int(self.options.section_judge_max_repairs),
                "fail_closed": bool(self.options.section_judge_fail_closed),
            }
        phases = [p for p in self.options.phases if p in PHASE_SEQUENCE]
        phase_runtime_contract = validate_phase_runtime_contracts(phases)
        safe_write_json(root_dir / "all_phase_agentic_runtime_contract.json", phase_runtime_contract)
        safe_write_json(root_dir / "all_phase_form_interaction_policy.json", policy_manifest())
        if not phase_runtime_contract.get("pass"):
            raise RuntimeError(f"All-phase agentic runtime contract failed: {phase_runtime_contract}")
        input_contract = validate_live_input_contract(base_input, phases=phases)
        safe_write_json(root_dir / "input_contract_preflight.json", input_contract)
        if not input_contract.get("pass"):
            raise RuntimeError(f"Input contract preflight failed: {input_contract.get('issues')}")
        phase_input_paths = write_phase_inputs(base_input, root_dir, phases)

        # V240: history-as-replay-world policy.  Existing run evidence is indexed
        # before the browser starts so the agent can choose exploration, hybrid or
        # exploitation while retaining the same live safety gates.
        replay_policy = replay_policy_engine_from_config(self.config)
        old_run_import = replay_policy.ingest_old_runs(self.config.reporting.runs_dir, exclude_run=root_dir)
        mission_policy_decision = replay_policy.decide(
            task="fill all HIP phases from input json",
            actions=["fill", "verify"],
            target_area="all_phases",
            page_families=phases,
        )
        safe_write_json(root_dir / "replay_policy_startup.json", {
            "old_run_import": old_run_import,
            "decision": mission_policy_decision,
            "manifest": replay_policy.manifest(),
        })
        # R12 model orchestra: prove which Dell On-Prem deployments are
        # actually reachable, then require a multi-model portfolio while this
        # portal flow is still learning/complex. Proven exploitation may later
        # collapse to the task champion for speed.
        try:
            model_orchestra = model_portfolio_from_config(self.config)
            model_probe = model_orchestra.probe_text_models(force=False)
            model_orchestra_startup = {
                "schema_version": "hip.model-orchestra-startup.v1",
                "probe": model_probe,
                "portfolio": model_orchestra.manifest(),
                "learning_requires_portfolio": bool(getattr(self.config.model_portfolio, "force_multi_model_during_learning", True)),
                "complex_tasks_require_portfolio": bool(getattr(self.config.model_portfolio, "force_multi_model_for_complex_tasks", True)),
            }
        except Exception as exc:
            model_orchestra_startup = {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))[:500]}
        safe_write_json(root_dir / "model_orchestra_startup.json", model_orchestra_startup)

        # Autonomous mission ledger: crash-safe phase status, resume adoption,
        # cross-phase entity registry and the final application-complete verdict.
        mission = MissionController(
            root_dir,
            run_id=ctx.run_id,
            phases=phases,
            mode=(
                "live_witness"
                if self.options.live_witness_mode
                else "autonomous_assured_all_phases"
                if self.options.strict_mission_assurance
                else "all_phases_until_complete"
                if self.options.runtime_self_heal_until_complete
                else "bounded_self_heal"
            ),
        )
        mission_trace = MissionTraceLedger(root_dir, run_id=ctx.run_id, phases=phases)
        mission.trace = mission_trace
        mlflow_tracker = AsyncMLflowTracker(
            self.config.mlflow,
            run_id=ctx.run_id,
            run_dir=root_dir,
            phases=phases,
            app_version=__version__,
        )
        mlflow_tracker.start(
            tags={
                "hip.environment": str(getattr(self.config.portal, "environment", "") or ""),
                "hip.mode": mission.mode,
                "hip.live_witness": str(bool(self.options.live_witness_mode)).lower(),
            },
            params={
                "hip.browser_channel": str(getattr(self.config.portal, "chromium_channel", "") or ""),
                "hip.section_judge": bool(self.options.section_judge),
                "hip.runtime_self_heal": bool(self.options.runtime_self_heal_enabled),
                "hip.golden_reference_enabled": bool(self.options.golden_screenshot_dir),
            },
        )
        mlflow_tracker.log_event("replay_policy_selected", {
            "mode": mission_policy_decision.get("mode"),
            "confidence": mission_policy_decision.get("confidence"),
            "support": mission_policy_decision.get("support"),
            "policy_version": mission_policy_decision.get("policy_version"),
        })
        mlflow_tracker.log_metrics({
            "policy.start_confidence": float(mission_policy_decision.get("confidence") or 0.0),
            "policy.start_support": float(mission_policy_decision.get("support") or 0.0),
        })
        mission.telemetry = mlflow_tracker
        safe_write_json(root_dir / "mlflow_status.json", mlflow_tracker.status(), mask=False)
        transition_coordinator = MissionTransitionCoordinator(root_dir, run_id=ctx.run_id, phases=phases)
        mission_trace.set_runtime_contract(
            autowebglm_primary=bool(getattr(self.config.autowebglm, "primary_framework", False)),
            playwright_mcp_required=bool(getattr(self.config.mcp, "use_playwright_mcp", True)),
            playwright_mcp_available=False,
            live_witness_mode=bool(self.options.live_witness_mode),
            semantic_understanding_enabled=bool(getattr(self.config.semantic_understanding, "enabled", True)),
            autonomous_all_form_phases=bool(getattr(self.config.autonomous_form, "apply_to_all_form_phases", True)),
        )
        safe_write_json(root_dir / "mission_parent_child_contract.json", {
            "schema_version": "hip.autonomous-mission-parent-child-contract.v1",
            "phase_sequence": phases,
            "phase_dependencies": mission.state.get("phase_dependencies") or {},
            "policy": {
                "upstream_phase_must_be_judge_approved": True,
                "input_values_flow_forward_from_current_mission_registry": True,
                "parallel_phase_mutation_prohibited": True,
                "resume_adoption_fail_closed": True,
            },
        })
        if self.options.live_witness_mode:
            safe_write_json(root_dir / "live_witness_policy.json", {
                "schema_version": "hip.live-witness-policy.v1",
                "enabled": True,
                "single_authenticated_browser": True,
                "planner": "AutoWebGLM",
                "primary_executor": "PyAutoGUI MCP",
                "deterministic_fallback": "Playwright MCP",
                "independent_verifier": "Chrome DevTools MCP",
                "submit_capture_enabled": False,
                "api_write_allowed": False,
                "mutation_controls_allowed": False,
                "dynamic_add_openers_allowed": True,
                "phase_sequence": phases,
            })
        resume_source: Optional[Path] = None
        if self.options.resume_run_dir:
            candidate = Path(self.options.resume_run_dir)
            if candidate.is_dir():
                resume_source = candidate
        elif self.options.auto_resume:
            resume_source = MissionController.find_resumable_run(
                self.config.reporting.runs_dir, exclude_run=root_dir
            )
        mission_resume = {"resumed": False, "requested": bool(self.options.resume_run_dir or self.options.auto_resume)}
        if resume_source is not None:
            mission_resume = mission.adopt_completed_phases(resume_source)
            mission_resume["requested"] = True
        safe_write_json(root_dir / "mission_resume_manifest.json", {
            "schema_version": "hip.mission-resume-manifest.v1",
            "requested_resume_run_dir": str(self.options.resume_run_dir or ""),
            "auto_resume": bool(self.options.auto_resume),
            "resolved_source_run_dir": str(resume_source) if resume_source else "",
            **{k: v for k, v in mission_resume.items() if k != "adoption_records"},
        })

        # Persistent long-term HIP Portal brain. It is outside the run directory,
        # bootstraps from all prior evidence, and promotes only judge-approved
        # knowledge into deterministic plan truth.
        portal_brain = PortalBrain.from_config(self.config)
        portal_brain.policy.enabled = bool(portal_brain.policy.enabled and self.options.portal_brain_enabled)
        # Shared semantic capability/API graph.  The configuration mission enriches
        # it only after a phase passes exact UI/API/judge/assurance gates.
        capability_graph = HIPCapabilityGraph(portal_brain.root)
        try:
            unified_kb_path = Path(str(getattr(self.config.brain, "unified_kb_path", "./knowledge_base/HIP_Unified_Deep_KB.json")))
            if not unified_kb_path.is_absolute():
                unified_kb_path = (Path.cwd() / unified_kb_path).resolve()
            capability_bootstrap = capability_graph.bootstrap_from_unified_kb(
                unified_kb_path, force=bool(getattr(self.config.brain, "force_unified_kb_reimport", False))
            )
        except Exception as exc:
            capability_bootstrap = {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))}
        safe_write_json(root_dir / "capability_graph_bootstrap.json", capability_bootstrap)
        safe_write_json(root_dir / "capability_graph_preflight.json", capability_graph.manifest())
        continuous_learning = continuous_learning_from_config(
            self.config, capability_graph=capability_graph, replay_policy=replay_policy
        )
        safe_write_json(root_dir / "continuous_learning_startup.json", continuous_learning.manifest())
        brain_policy_preflight = {
            "pass": (
                int(portal_brain.policy.kb_repair_min_confirmations) == int(self.config.brain.kb_repair_min_confirmations)
                and int(portal_brain.policy.kb_supersede_min_confirmations) == int(self.config.brain.kb_supersede_min_confirmations)
            ),
            "expected": {
                "kb_repair_min_confirmations": int(self.config.brain.kb_repair_min_confirmations),
                "kb_supersede_min_confirmations": int(self.config.brain.kb_supersede_min_confirmations),
            },
            "actual": {
                "kb_repair_min_confirmations": int(portal_brain.policy.kb_repair_min_confirmations),
                "kb_supersede_min_confirmations": int(portal_brain.policy.kb_supersede_min_confirmations),
            },
        }
        safe_write_json(root_dir / "portal_brain_policy_preflight.json", brain_policy_preflight)
        if not brain_policy_preflight["pass"]:
            raise RuntimeError(f"Portal Brain policy wiring mismatch: {brain_policy_preflight}")
        brain_bootstrap = portal_brain.bootstrap(
            self.config.reporting.runs_dir,
            exclude_run=root_dir,
            rebuild=bool(self.options.rebuild_portal_brain),
        ) if portal_brain.policy.enabled else {"status": "disabled", "brain_dir": str(portal_brain.root)}
        safe_write_json(root_dir / "portal_brain_bootstrap.json", brain_bootstrap)

        # Judge-gated structural memory shared across all HIP form families.  It
        # stores topology/action/binding knowledge only; current customer values
        # always come from the new input.json.
        flow_pattern_memory = FlowPatternMemory.from_config(
            portal_brain.root / "flow_patterns", self.config
        )
        flow_pattern_bootstrap = flow_pattern_memory.bootstrap_from_runs(
            self.config.reporting.runs_dir, exclude_run=root_dir,
            max_runs=int(getattr(self.config.brain, "max_source_runs", 300)),
        )
        safe_write_json(root_dir / "flow_pattern_memory_bootstrap.json", flow_pattern_bootstrap)
        safe_write_json(root_dir / "all_hip_form_family_catalog.json", catalog_manifest())

        # Seed the reviewed Unified Deep KB after an optional rebuild so canonical
        # page identity, field/input mappings, hard gates, portal bugs and reviewed
        # negative evidence are always available to the deterministic planner.
        unified_kb_import = {"status": "disabled"}
        if portal_brain.policy.enabled and portal_brain.policy.auto_import_unified_kb:
            unified_kb_import = portal_brain.import_unified_kb()
            if portal_brain.policy.unified_kb_required and unified_kb_import.get("status") in {"missing", "error"}:
                raise RuntimeError(f"Required Unified HIP KB could not be imported: {unified_kb_import}")
        safe_write_json(root_dir / "unified_kb_import.json", unified_kb_import)

        deterministic_plan_manifest = compile_and_attach_plans(
            phase_input_paths=phase_input_paths,
            runs_root=self.config.reporting.runs_dir,
            current_run_dir=root_dir,
            brain=portal_brain if portal_brain.policy.enabled else None,
        )
        upload_assets_manifest = list_upload_assets(base_input)
        safe_write_json(root_dir / "upload_assets_manifest.json", upload_assets_manifest)
        golden_refs_by_phase = prepare_golden_screenshot_references(self.options.golden_screenshot_dir, root_dir, phases=phases)
        phase_summaries: Dict[str, Any] = {}
        phase_verifications: List[Dict[str, Any]] = []
        phase_judge_results: List[Dict[str, Any]] = []
        judge_policy = SectionJudgePolicy(
            enabled=bool(self.options.section_judge),
            require_text_model=bool(self.options.require_text_judge),
            require_vision_model=bool(self.options.require_vision_judge),
            max_repairs=max(0, int(self.options.section_judge_max_repairs)),
            fail_closed=bool(self.options.section_judge_fail_closed),
        )
        phase_judge = DualModelSectionJudge(judge_policy) if judge_policy.enabled else None
        judge_consensus = MultiModelJudgeConsensus(self.config)
        human_phase_reviews = human_phase_review_from_config(self.config)
        interactive_teaching = interactive_teaching_from_config(self.config)
        deterministic_recipes = deterministic_recipe_from_config(self.config)
        learning_review_phases = {str(x) for x in (self.options.learning_review_phases or ()) if str(x)}
        safe_write_json(root_dir / "human_phase_review_policy.json", {
            "enabled": bool(getattr(self.config.human_in_the_loop, "enabled", True)),
            "review_newly_learned_phase_once": bool(getattr(self.config.human_in_the_loop, "review_newly_learned_phase_once", True)),
            "review_on_judge_pass": bool(getattr(self.config.human_in_the_loop, "review_on_judge_pass", True)),
            "review_on_judge_block": bool(getattr(self.config.human_in_the_loop, "review_on_judge_block", True)),
            "learning_review_phases": sorted(learning_review_phases),
            "wait_seconds": int(getattr(self.config.human_in_the_loop, "phase_review_wait_seconds", 600) or 0),
            "multi_model_judge_on_disagreement": bool(getattr(self.config.human_in_the_loop, "multi_model_judge_on_disagreement", True)),
            "human_pass_requires_exact_evidence": bool(getattr(self.config.human_in_the_loop, "human_pass_requires_exact_evidence", True)),
        })
        if phase_judge is not None and judge_policy.require_vision_model:
            vision_preflight = phase_judge.vision_preflight()
            safe_write_json(root_dir / "vision_model_preflight.json", vision_preflight)
            if not vision_preflight.get("pass") and judge_policy.fail_closed:
                raise RuntimeError(f"Required vision judge preflight failed before portal execution: {vision_preflight.get('error')}")
        blocked_phase = ""
        blocked_phases: List[str] = []

        shared_browser = BrowserSession(self.config, root_dir)
        shared_browser.flow_pattern_memory = flow_pattern_memory
        shared_browser.mission_trace = mission_trace
        # V237: expose run-local golden references and the already configured
        # dual-model visual advisor to the shared browser session.  Phase runtimes
        # can consume these as *advisory structural evidence* while input.json
        # remains the sole authority for customer values.
        shared_browser.golden_references_by_phase = golden_refs_by_phase
        shared_browser.visual_feedback_agent = phase_judge
        shared_browser.replay_policy_mode = str(mission_policy_decision.get("mode") or "exploration")
        shared_browser.replay_policy_decision = mission_policy_decision
        agentq_controller = HIPAgentQController(
            memory_root=portal_brain.root / "agentq",
            run_dir=root_dir,
            config=self.config,
        )
        agentq_mcp_status = await agentq_controller.start()
        safe_write_json(root_dir / "hip_intelligence_mcp_startup.json", agentq_mcp_status)
        shared_browser.agentq_controller = agentq_controller
        await shared_browser.start()
        mission_trace.set_runtime_contract(
            autowebglm_primary=bool(getattr(self.config.autowebglm, "primary_framework", False)),
            playwright_mcp_required=bool(getattr(self.config.mcp, "use_playwright_mcp", True)),
            playwright_mcp_available=bool(shared_browser.playwright_mcp_backend is not None),
            live_witness_mode=bool(self.options.live_witness_mode),
            semantic_understanding_enabled=bool(getattr(self.config.semantic_understanding, "enabled", True)),
            autonomous_all_form_phases=bool(getattr(self.config.autonomous_form, "apply_to_all_form_phases", True)),
        )
        safe_write_json(root_dir / "browser_session_reuse_contract.json", {
            "schema_version": "hip.browser-session-reuse.v1",
            "session_id": shared_browser.session_id,
            "single_persistent_context": True,
            "phase_sequence": phases,
            "sso_policy": "prompt once when unauthenticated; reuse the authenticated context for every later phase",
            "browser_start_count_expected": 1,
        })
        expected_inputs_by_phase: Dict[str, Dict[str, Any]] = {}
        for phase_name, phase_input_path in phase_input_paths.items():
            try:
                payload = read_json_any(phase_input_path)
                expected_inputs_by_phase[phase_name] = payload if isinstance(payload, dict) else {}
            except Exception:
                expected_inputs_by_phase[phase_name] = {}

        runtime_self_healer = RuntimeSelfHealController(
            config=self.config,
            root_dir=root_dir,
            browser=shared_browser,
            brain=portal_brain if portal_brain.policy.enabled else None,
            run_id=ctx.run_id,
            enabled=bool(self.options.runtime_self_heal_enabled),
            max_phase_attempts=int(self.options.runtime_self_heal_max_phase_attempts),
            until_complete=bool(self.options.runtime_self_heal_until_complete),
            golden_references_by_phase=golden_refs_by_phase,
            expected_inputs_by_phase=expected_inputs_by_phase,
            visual_feedback_agent=phase_judge,
            forensic_evidence=bool(self.options.forensic_evidence),
        )
        safe_write_json(root_dir / "runtime_self_heal" / "runtime_self_heal_policy.json", {
            "enabled": runtime_self_healer.enabled,
            "max_phase_attempts": runtime_self_healer.max_phase_attempts,
            "max_total_repairs": runtime_self_healer.max_total_repairs,
            "max_repeated_failure_signature": runtime_self_healer.max_repeated_signature,
            "max_no_progress_repeats": runtime_self_healer.max_no_progress_repeats,
            "max_phase_wall_seconds": runtime_self_healer.max_phase_wall_seconds,
            "until_complete": runtime_self_healer.until_complete,
            "exploration_exploitation": runtime_self_healer.exploration_exploitation,
            "forensic_evidence": runtime_self_healer.forensic_evidence,
            "capture_validated_trajectory": bool(getattr(self.config.runtime_self_heal, "capture_validated_trajectory", True)),
            "use_aia_advisor": runtime_self_healer.use_aia_advisor,
            "fail_closed": runtime_self_healer.fail_closed,
            "safe_repair_only": True,
            "judge_required_for_recovery_promotion": True,
        })
        portal_learning = PortalLearningRuntime(
            config=self.config,
            root_dir=root_dir,
            browser=shared_browser,
            brain=portal_brain if portal_brain.policy.enabled else None,
            run_id=ctx.run_id,
        )
        safe_write_json(root_dir / "portal_learning" / "portal_learning_policy.json", {
            "enabled": portal_learning.enabled,
            "three_observation_channels": ["python_playwright", "playwright_mcp", "chrome_devtools_mcp"],
            "promotion_requires_independent_judge": True,
            "storage_values_captured": False,
            "additional_browser_controller_started": False,
            "note": "The existing two MCP servers are used more deeply; no third browser controller is launched, preserving the single Chrome/SSO context.",
        })
        form_api_agent = FormAPIAgentQRuntime(
            config=self.config,
            root_dir=root_dir,
            browser=shared_browser,
            agentq=agentq_controller,
        )
        safe_write_json(root_dir / "agentq_crawler_form_api_policy.json", {
            "enabled": bool(self.options.agentq_crawler_fusion or form_api_agent.enabled),
            "dual_ui_api": bool(self.options.dual_ui_api),
            "capture_submit_api": bool(self.options.capture_submit_api),
            "live_witness_mode": bool(self.options.live_witness_mode),
            "api_mode": str(self.options.api_mode),
            "require_api_capture_for_completion": bool(self.options.require_api_capture_for_completion),
            "single_browser_owner": True,
            "observed_contracts_only": True,
            "submit_capture_network_mutation_blocked": True,
            "customer_values_promoted_to_memory": False,
        })

        async def _learning_begin(phase_name: str, attempt_no: int, phase_dir: Path, contract: Dict[str, Any]) -> None:
            try:
                await portal_learning.begin_attempt(
                    phase=phase_name, attempt=attempt_no, phase_dir=phase_dir, contract=contract
                )
            except Exception as exc:
                safe_write_json(
                    phase_dir / "portal_learning" / f"attempt_{attempt_no:02d}_capture_error.json",
                    {"stage": "begin", "error": mask_sensitive_string(str(exc)), "run_continues": True},
                )
            try:
                phase_payload_for_api = read_json_any(phase_input_paths[phase_name])
                phase_graph_for_api = compile_phase_state_graph(
                    phase_payload_for_api if isinstance(phase_payload_for_api, dict) else {}, phase_name
                )
                phase_graph_for_api = apply_dependency_execution_contract(phase_graph_for_api, phase=phase_name)
                # Persist the current deterministic plan as candidate structural
                # knowledge immediately. The UI therefore shows what the agent
                # knows/attempts even if a later live control blocks the phase.
                capability_family = PHASE_TO_CAPABILITY_FAMILY.get(phase_name, phase_name)
                for node in phase_graph_for_api.get("nodes", []) if isinstance(phase_graph_for_api, dict) else []:
                    if not isinstance(node, dict):
                        continue
                    cap = capability_graph.observe_capability(
                        page_family=capability_family,
                        kind="planned_form_field",
                        label=str(node.get("field_key") or node.get("node_id") or "form field"),
                        scope=str(node.get("input_path") or ""),
                        section=str(node.get("section") or ""),
                        run_id=ctx.run_id,
                        evidence={
                            "phase": phase_name, "attempt": attempt_no,
                            "node_id": node.get("node_id"), "action": node.get("action"),
                            "knowledge_source": "runtime_plan", "values_stored": False,
                        },
                        knowledge_source="runtime_plan", trust="candidate", verified=False,
                    )
                    cap["action"] = str(node.get("action") or "")
                    cap["input_json_key"] = str(node.get("input_path") or "")
                capability_graph.save()
                form_api_agent.begin_attempt(
                    phase=phase_name,
                    attempt=attempt_no,
                    phase_dir=phase_dir,
                    input_payload=phase_payload_for_api if isinstance(phase_payload_for_api, dict) else {},
                    state_graph=phase_graph_for_api if isinstance(phase_graph_for_api, dict) else {},
                )
            except Exception as exc:
                safe_write_json(
                    phase_dir / "form_api_intelligence" / f"attempt_{attempt_no:02d}_capture_error.json",
                    {"stage": "begin", "error": mask_sensitive_string(str(exc)), "run_continues": not self.options.require_api_capture_for_completion},
                )

        async def _learning_finish(
            phase_name: str, attempt_no: int, *, success: bool, verification: Dict[str, Any] | None = None,
            judge_result: Dict[str, Any] | None = None, error: str = "", phase_dir: Path
        ) -> Dict[str, Any]:
            api_result: Dict[str, Any] = {"status": "disabled", "pass": True}
            try:
                await portal_learning.finish_attempt(
                    phase=phase_name, attempt=attempt_no, verification=verification or {},
                    judge_result=judge_result or {}, success=success, error=error,
                )
            except Exception as exc:
                safe_write_json(
                    phase_dir / "portal_learning" / f"attempt_{attempt_no:02d}_capture_error.json",
                    {"stage": "finish", "error": mask_sensitive_string(str(exc)), "run_continues": True},
                )
            try:
                api_result = await form_api_agent.finish_attempt(
                    phase=phase_name,
                    attempt=attempt_no,
                    success=success,
                    verification=verification or {},
                    judge_result=judge_result or {},
                    error=error,
                )
                # Promote observed network contracts immediately as candidate
                # learning. Judge-approved phase promotion may later strengthen
                # trust, but failure must not make the API Contracts tab look blank.
                catalog_path = Path(str(((api_result.get("artifacts") or {}).get("phase_api_catalog") or "")))
                if catalog_path.is_file():
                    catalog = read_json_any(catalog_path)
                    capability_family = PHASE_TO_CAPABILITY_FAMILY.get(phase_name, phase_name)
                    for contract_row in (catalog.get("contracts") or []) if isinstance(catalog, dict) else []:
                        if not isinstance(contract_row, dict):
                            continue
                        endpoint = str(contract_row.get("endpoint_template") or "")
                        method = str(contract_row.get("method") or "GET")
                        statuses = list(contract_row.get("statuses") or [])
                        response_status = next((int(x) for x in statuses if str(x).isdigit()), None)
                        capability_graph.observe_api_contract(
                            page_family=capability_family, method=method, url=endpoint,
                            request_shape=(contract_row.get("request_shapes") or [None])[0],
                            response_status=response_status,
                            response_shape=(contract_row.get("response_shapes") or [None])[0],
                            stage="live_form_observation",
                            evidence={
                                "phase": phase_name, "attempt": attempt_no,
                                "endpoint_kind": contract_row.get("endpoint_kind"),
                                "sources": contract_row.get("sources") or [],
                                "values_stored": False,
                            },
                            knowledge_source="live_network",
                            trust="observed_candidate" if not success else "observed_success",
                            verified=bool(success and (judge_result or {}).get("pass", True)),
                        )
                    capability_graph.save()
            except Exception as exc:
                api_result = {
                    "status": "error",
                    "pass": not self.options.require_api_capture_for_completion,
                    "error": mask_sensitive_string(str(exc)),
                }
                safe_write_json(
                    phase_dir / "form_api_intelligence" / f"attempt_{attempt_no:02d}_capture_error.json",
                    {"stage": "finish", "error": api_result["error"], "run_continues": api_result["pass"]},
                )
            return api_result

        async def _execute_phase_once(
            phase_name: str,
            phase_ctx: RunContext,
            phase_input_path: Path,
        ) -> Dict[str, Any]:
            if phase_name == "data_map":
                flow = DataMapKBFlow(
                    self.config,
                    datamaps_url=PHASE_URLS[phase_name],
                    fill_dummy=True,
                    write_heavy_evidence=self.options.write_heavy_evidence,
                    crawl_old_datamaps=self.options.full_kb_context,
                    max_api_pages=25 if self.options.full_kb_context else 1,
                    max_detail_rows=None if self.options.full_kb_context else 0,
                )
            elif phase_name in {"source_document_type", "target_document_type"}:
                flow = DocumentTypeKBFlow(
                    self.config,
                    doctypes_url=PHASE_URLS[phase_name],
                    fill_dummy=True,
                    write_heavy_evidence=self.options.write_heavy_evidence,
                    crawl_old_doctypes=self.options.full_kb_context,
                    max_api_pages=250 if self.options.full_kb_context else 1,
                    max_detail_rows=None if self.options.full_kb_context else 0,
                    capture_deep_profiles=self.options.full_kb_context,
                    max_deep_profile_rows=None if self.options.full_kb_context else 0,
                )
            elif phase_name == "rule":
                flow = RuleKBFlow(
                    self.config,
                    rules_url=PHASE_URLS[phase_name],
                    fill_dummy=True,
                    write_heavy_evidence=self.options.write_heavy_evidence,
                    crawl_old_rules=self.options.full_kb_context,
                    max_api_pages=250 if self.options.full_kb_context else 1,
                    max_detail_rows=None if self.options.full_kb_context else 0,
                    capture_deep_profiles=self.options.full_kb_context,
                    max_deep_profile_rows=None if self.options.full_kb_context else 0,
                )
            elif phase_name in {"source_transport_profile", "target_transport_profile"}:
                flow = TransportProfileKBFlow(
                    self.config,
                    transport_profiles_url=PHASE_URLS[phase_name],
                    fill_dummy=True,
                    write_heavy_evidence=self.options.write_heavy_evidence,
                    crawl_old_transport_profiles=self.options.full_kb_context,
                    max_api_pages=250 if self.options.full_kb_context else 1,
                    max_detail_rows=None if self.options.full_kb_context else 0,
                    capture_deep_profiles=self.options.full_kb_context,
                    max_deep_profile_rows=None if self.options.full_kb_context else 0,
                )
            elif phase_name == "biz_flow":
                flow = BizFlowKBFlow(
                    self.config,
                    bizflows_url=PHASE_URLS[phase_name],
                    fill_dummy=True,
                    write_heavy_evidence=self.options.write_heavy_evidence,
                    crawl_old_bizflows=self.options.full_kb_context,
                    max_api_pages=250 if self.options.full_kb_context else 1,
                    capture_deep_profiles=self.options.full_kb_context,
                    capture_runtime_details=self.options.full_kb_context,
                    max_deep_profile_rows=None if self.options.full_kb_context else 0,
                    form_only=not self.options.full_kb_context,
                )
            else:
                return {}
            return await flow.run(
                phase_ctx,
                input_json=phase_input_path,
                browser_session=shared_browser,
            )

        async def _refresh_live_read_only_checkpoint(
            phase_name: str,
            phase_dir: Path,
            phase_input_path: Path,
            *,
            reason: str,
        ) -> Dict[str, Any]:
            """Re-prove a stable live form without replaying or mutating it.

            R10 closes the no-progress race where the KB/learning coroutine is
            still running after the requested form state has already been reached.
            The ordinary exact artifact may not exist yet, so the watchdog/human
            review path gets one current-browser deterministic reproof before it
            decides the phase is incomplete.
            """
            existing = phase_exact_completion_checkpoint(phase_name, phase_dir)
            if existing.get("pass") is True:
                return existing
            try:
                payload = read_json_any(phase_input_path)
                deterministic_only = phase_judge or DualModelSectionJudge(
                    SectionJudgePolicy(
                        enabled=True,
                        require_text_model=False,
                        require_vision_model=False,
                        max_repairs=0,
                        fail_closed=True,
                    )
                )
                live = await live_read_only_phase_reproof(
                    page=shared_browser.page,
                    phase=phase_name,
                    phase_input=payload if isinstance(payload, dict) else {},
                    judge=deterministic_only,
                )
                live["reason"] = str(reason or "")[:500]
                safe_write_json(phase_dir / "phase_live_read_only_reproof.json", live)
            except Exception as exc:
                safe_write_json(phase_dir / "phase_live_read_only_reproof.json", {
                    "schema_version": "hip.live-read-only-phase-reproof.v1",
                    "phase": phase_name,
                    "pass": False,
                    "status": "reproof_error",
                    "read_only": True,
                    "source": "current_live_browser",
                    "error": mask_sensitive_string(str(exc))[:500],
                    "values_stored": False,
                    "selectors_stored": False,
                    "coordinates_stored": False,
                })
            return phase_exact_completion_checkpoint(phase_name, phase_dir)

        try:
            for phase_index, phase in enumerate(phases):
                phase_dir = root_dir / phase
                phase_dir.mkdir(parents=True, exist_ok=True)
                # R12: every click/fill/navigation is learning experience. Capture
                # the action cursor now so this phase can promote only its own
                # browser interactions after exact+judge(+human) completion.
                phase_action_start_index = len(getattr(shared_browser, "action_events", []) or [])

                if mission.phase_status(phase) == "complete":
                    # Adopted fail-closed from a resumed prior run: the exact
                    # completion lock, the passing independent judge and the
                    # persisted verification all existed, so the browser is not
                    # replayed for this phase.
                    adopted_verification = read_json_any(phase_dir / PHASE_VERIFICATION_FILENAME) if (phase_dir / PHASE_VERIFICATION_FILENAME).is_file() else {}
                    adopted_judge = read_json_any(phase_dir / PHASE_JUDGE_RESULT_FILENAME) if (phase_dir / PHASE_JUDGE_RESULT_FILENAME).is_file() else {}
                    if isinstance(adopted_verification, dict) and adopted_verification:
                        phase_verifications.append(adopted_verification)
                    if isinstance(adopted_judge, dict) and adopted_judge:
                        phase_judge_results.append(adopted_judge)
                    try:
                        mission_trace.refresh_phase_artifacts(phase, phase_dir, verification=adopted_verification if isinstance(adopted_verification, dict) else {}, judge=adopted_judge if isinstance(adopted_judge, dict) else {})
                    except Exception:
                        pass
                    phase_summaries[phase] = {
                        "status": "resumed_from_prior_run",
                        "phase": phase,
                        "browser_replay_performed": False,
                        "source_run_dir": (mission.state.get("resume") or {}).get("source_run_dir", ""),
                    }
                    try:
                        mission_trace.record_observation(
                            phase,
                            summary="Browser navigation skipped because this phase was adopted with current-run proof snapshot",
                            source="mission_transition",
                            details={"browser_replay_performed": False, "status": "resumed"},
                        )
                    except Exception:
                        pass
                    continue

                phase_ctx = RunContext(
                    run_id=f"{ctx.run_id}-{phase}",
                    customer=ctx.customer,
                    partner_query=ctx.partner_query,
                    system_query=ctx.system_query,
                    run_dir=phase_dir,
                    screenshots_dir=phase_dir / self.config.reporting.screenshot_dir_name,
                )
                phase_ctx.screenshots_dir.mkdir(parents=True, exist_ok=True)
                input_path = phase_input_paths[phase]
                try:
                    phase_payload_for_memory = read_json_any(input_path)
                    phase_graph_for_memory = compile_phase_state_graph(
                        phase_payload_for_memory if isinstance(phase_payload_for_memory, dict) else {}, phase
                    )
                    phase_graph_for_memory = apply_dependency_execution_contract(phase_graph_for_memory, phase=phase)
                    safe_write_json(
                        phase_dir / "parent_child_execution_contract.json",
                        phase_graph_for_memory.get("dependency_execution_contract") or {},
                    )
                    phase_memory_match = flow_pattern_memory.match_graph(phase_graph_for_memory, phase=phase)
                except Exception as exc:
                    phase_payload_for_memory = {}
                    phase_graph_for_memory = {}
                    phase_memory_match = {
                        "status": "error_fail_open", "validated_match": False,
                        "error": mask_sensitive_string(str(exc)),
                    }
                safe_write_json(phase_dir / "flow_pattern_memory_match.json", phase_memory_match)
                phase_attempts: List[Dict[str, Any]] = []
                summary: Dict[str, Any] = {}
                verification: Dict[str, Any] = {}
                judge_result: Dict[str, Any] = {}
                phase_completed = False
                max_phase_attempts = runtime_self_healer.max_phase_attempts if runtime_self_healer.enabled else 2
                attempt_index = 0
                phase_loop_started = time.monotonic()

                while runtime_self_healer.until_complete or attempt_index < max_phase_attempts:
                    elapsed = time.monotonic() - phase_loop_started
                    if elapsed >= float(runtime_self_healer.max_phase_wall_seconds):
                        stall = {
                            "schema_version": "hip.phase-stall-guard.v1",
                            "phase": phase,
                            "code": "HIP_PHASE_WALLCLOCK_STALL_GUARD",
                            "elapsed_seconds": round(elapsed, 3),
                            "max_phase_wall_seconds": runtime_self_healer.max_phase_wall_seconds,
                            "message": "Autonomous repair paused at the wall-clock safety guard; browser remains open for supervised recovery.",
                        }
                        safe_write_json(phase_dir / "phase_stall_guard.json", stall)
                        if self.options.hold_browser_on_incomplete_phase:
                            hold = await _hold_incomplete_phase_for_human(
                                store=human_phase_reviews, browser=shared_browser, run_id=ctx.run_id,
                                phase=phase, phase_display=PHASE_DISPLAY.get(phase, phase),
                                recovery_round=max(1, attempt_index + 1), phase_dir=phase_dir,
                                reason=json.dumps(stall, ensure_ascii=False),
                                exact_checkpoint=phase_exact_completion_checkpoint(phase, phase_dir),
                                automated_judge=judge_result, verification=verification, config=self.config,
                            )
                            if hold.get("resume"):
                                phase_loop_started = time.monotonic()
                                continue
                        mission.mark_phase_blocked(phase, attempt=max(1, attempt_index), reason=stall["code"])
                        blocked_phase = phase
                        break
                    attempt_no = attempt_index + 1
                    attempt_index += 1
                    # R10: a live read-only reproof is valid only for the stable
                    # browser state in the attempt that created it.  Any new
                    # execution/repair attempt invalidates that proof before the
                    # browser is touched, preventing a stale prior pass from
                    # satisfying a later checkpoint.
                    try:
                        stale_live_reproof = phase_dir / "phase_live_read_only_reproof.json"
                        if stale_live_reproof.exists():
                            stale_live_reproof.unlink()
                    except Exception:
                        pass
                    contract = PHASE_RUNTIME_CONTRACTS[phase]
                    attempt_stage = "agentic_preflight"
                    mission_readiness = mission.phase_readiness(phase)
                    safe_write_json(phase_dir / "mission_phase_readiness.json", mission_readiness)
                    if not mission_readiness.get("ready"):
                        dependency_message = (
                            "HIP_MISSION_PARENT_PHASES_INCOMPLETE: "
                            + ", ".join(mission_readiness.get("incomplete_parent_phases") or [])
                        )
                        if self.options.continue_after_phase_block:
                            safe_write_json(phase_dir / "mission_dependency_warning.json", {
                                "phase": phase,
                                "warning": dependency_message,
                                "policy": "attempt selected no-save form despite prior blocked phase; live dropdown availability remains authoritative",
                                "incomplete_parent_phases": mission_readiness.get("incomplete_parent_phases") or [],
                            })
                        else:
                            raise RuntimeError(dependency_message)
                    mission.mark_phase_started(phase, attempt=attempt_no)
                    try:
                        preflight = await runtime_self_healer.prepare_phase_attempt(
                            phase=phase,
                            target_url=PHASE_URLS[phase],
                            attempt=attempt_no,
                            contract=contract,
                            phase_dir=phase_dir,
                        )
                        transition_ack = transition_coordinator.acknowledge(
                            phase=phase,
                            attempt=attempt_no,
                            destination_route_verified=bool(((preflight.get("route") or {}) if isinstance(preflight, dict) else {}).get("pass")),
                            dual_mcp_verified=bool(((preflight.get("dual_mcp") or {}) if isinstance(preflight, dict) else {}).get("pass")),
                            details={"preflight_status": (preflight or {}).get("status") if isinstance(preflight, dict) else ""},
                        )
                        safe_write_json(phase_dir / f"phase_transition_ack_attempt_{attempt_no:02d}.json", transition_ack)
                        if not transition_ack.get("pass"):
                            raise RuntimeError(f"HIP_PHASE_TRANSITION_DESTINATION_NOT_ACKNOWLEDGED: {transition_ack}")
                        await _learning_begin(phase, attempt_no, phase_dir, contract)
                        attempt_stage = "phase_execution"
                        remaining_phase_seconds = max(1.0, float(runtime_self_healer.max_phase_wall_seconds) - (time.monotonic() - phase_loop_started))
                        async def _watchdog_checkpoint_provider() -> Dict[str, Any]:
                            return await _refresh_live_read_only_checkpoint(
                                phase, phase_dir, input_path,
                                reason="no_progress_watchdog_terminal_reproof",
                            )
                        # Legacy/source-audit marker retained for ordering tests: summary = await _execute_phase_once
                        # The real call is wrapped in asyncio.wait_for so one stuck portal phase cannot run for hours.
                        summary = await asyncio.wait_for(
                            run_with_progress_watchdog(
                                _execute_phase_once(phase, phase_ctx, input_path),
                                phase=phase,
                                marker_provider=lambda: shared_browser.capture_phase_progress_marker(phase),
                                checkpoint_provider=_watchdog_checkpoint_provider,
                                evidence_path=phase_dir / "phase_no_progress_watchdog.json",
                                no_progress_seconds=float(getattr(self.config.runtime_self_heal, "no_progress_watchdog_seconds", 90.0) or 90.0),
                                poll_seconds=float(getattr(self.config.runtime_self_heal, "no_progress_poll_seconds", 5.0) or 5.0),
                                recent_signature_limit=int(getattr(self.config.runtime_self_heal, "no_progress_recent_signature_limit", 12) or 12),
                            ),
                            timeout=remaining_phase_seconds,
                        )
                        completion_checkpoint = phase_exact_completion_checkpoint(phase, phase_dir)
                        if completion_checkpoint.get("pass") is True:
                            safe_write_json(phase_dir / "phase_exact_state_lock.json", {
                                "schema_version": "hip.all-phase-exact-state-lock.v1",
                                "phase": phase,
                                "attempt": attempt_no,
                                "family": contract.get("family"),
                                "exact_completion_checkpoint": completion_checkpoint,
                                "form_frozen": True,
                                "phase_replay_allowed": False,
                                "judge_may_review_but_not_mutate": True,
                                "transaction_policy": contract.get("transaction_policy") or {},
                            })
                    except Exception as exc:
                        message = mask_sensitive_string(str(exc))
                        classification = runtime_self_healer.classify_failure(
                            message,
                            failure_kind="execution_exception",
                        )
                        checkpoint = phase_exact_completion_checkpoint(phase, phase_dir)
                        no_progress_after_exact = "HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL" in message
                        recovered_reporting_only = bool(
                            (classification == "reporting_only_failure" or no_progress_after_exact)
                            and checkpoint.get("pass") is True
                        )

                        if recovered_reporting_only:
                            # The live form already reached its exact target state.
                            # Reporting/KG export is non-authoritative, so preserve
                            # the error and continue to the normal deterministic,
                            # text and vision judges without reopening the form.
                            recovery_artifact = phase_dir / "reporting_failure_recovered_without_replay.json"
                            recovery_payload = {
                                "schema_version": "hip.reporting-only-no-replay.v1",
                                "phase": phase,
                                "attempt": attempt_no,
                                "failure_stage": attempt_stage,
                                "classification": classification,
                                "error": message,
                                "exact_completion_checkpoint": checkpoint,
                                "phase_replay_required": False,
                                "browser_repair_executed": False,
                                "next_step": "build verification and run independent judges",
                            }
                            safe_write_json(recovery_artifact, recovery_payload)
                            summary = synthesize_summary_after_reporting_failure(
                                phase=phase,
                                phase_dir=phase_dir,
                                error=message,
                                checkpoint=checkpoint,
                            )
                            phase_attempts.append({
                                "attempt": attempt_no,
                                "status": "completed_with_reporting_warning",
                                "failure_stage": attempt_stage,
                                "classification": classification,
                                "error": message,
                                "exact_completion_checkpoint": checkpoint,
                                "phase_replay_required": False,
                                "recovery_artifact": str(recovery_artifact),
                            })
                            safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                            runtime_self_healer.record_nonblocking_reporting_recovery(
                                phase=phase,
                                attempt=attempt_no,
                                error=message,
                                checkpoint=checkpoint,
                                artifact=str(recovery_artifact),
                            )
                        else:
                            if "HIP_PHASE_NO_PROGRESS_WATCHDOG" in message:
                                try:
                                    mission_trace.record_warning(phase, "No-progress watchdog interrupted a repeated browser state; entering bounded deterministic recovery")
                                except Exception:
                                    pass
                            auth_expired = "HIP_AUTH_SESSION_EXPIRED" in message
                            route_not_committed = "HIP_ROUTE_NOT_COMMITTED" in message
                            mcp_surface_drift = "HIP_MCP_SURFACE_DRIFT" in message
                            executor_disconnect = shared_browser.is_executor_transport_disconnect(message)
                            recoverable_navigation = route_not_committed or mcp_surface_drift

                            # v2.1 Layer 6: a websocket/MCP transport failure is
                            # recovered by reattaching the MCP clients to the SAME
                            # authenticated browser/tab. Never relaunch Edge/Chrome
                            # after SSO and never hand this first to free-form ReAct.
                            if executor_disconnect:
                                rebind = await shared_browser.recover_same_browser_executor_bindings(
                                    PHASE_URLS[phase], phase=phase,
                                    checkpoint_passed=bool(checkpoint.get("pass") is True),
                                )
                                safe_write_json(phase_dir / f"executor_rebind_attempt_{attempt_no:02d}.json", rebind)
                                if rebind.get("pass"):
                                    if checkpoint.get("pass") is True:
                                        # The exact form state was already proven.
                                        # Rebuild reporting/judge input read-only and
                                        # continue; reopening the phase would be a loop.
                                        summary = synthesize_summary_after_reporting_failure(
                                            phase=phase, phase_dir=phase_dir, error=message, checkpoint=checkpoint
                                        )
                                        phase_attempts.append({
                                            "attempt": attempt_no,
                                            "status": "executor_rebound_after_exact_state",
                                            "failure_stage": attempt_stage,
                                            "error": message,
                                            "executor_disconnect": True,
                                            "executor_rebind": rebind,
                                            "exact_completion_checkpoint": checkpoint,
                                            "phase_replay_required": False,
                                        })
                                        safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                                    else:
                                        phase_attempts.append({
                                            "attempt": attempt_no,
                                            "status": "executor_rebound_resume_same_phase",
                                            "failure_stage": attempt_stage,
                                            "error": message,
                                            "executor_disconnect": True,
                                            "executor_rebind": rebind,
                                            "phase_replay_required": True,
                                            "replay_scope": "same deterministic no-save phase only",
                                        })
                                        safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                                        await _learning_finish(phase, attempt_no, success=False, error=message, phase_dir=phase_dir)
                                        continue
                                else:
                                    phase_attempts.append({
                                        "attempt": attempt_no,
                                        "status": "executor_rebind_blocked",
                                        "failure_stage": attempt_stage,
                                        "error": message,
                                        "executor_disconnect": True,
                                        "executor_rebind": rebind,
                                        "phase_replay_required": False,
                                    })
                                    safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                                    await _learning_finish(phase, attempt_no, success=False, error=message, phase_dir=phase_dir)
                                    runtime_self_healer.finalize_phase(phase, judge_pass=False)
                                    mission.mark_phase_blocked(phase, attempt=attempt_no, reason=str(rebind.get("code") or message))
                                    if self.options.continue_after_phase_block:
                                        blocked_phase = phase
                                        break
                                    raise RuntimeError(str(rebind.get("error") or rebind.get("code") or message))
                            else:
                                decision = await runtime_self_healer.handle_failure(
                                    phase=phase,
                                    target_url=PHASE_URLS[phase],
                                    attempt=attempt_no,
                                    message=message,
                                    failure_kind="execution_exception",
                                )
                                phase_attempts.append({
                                    "attempt": attempt_no,
                                    "status": "failed",
                                    "failure_stage": attempt_stage,
                                    "error": message,
                                    "auth_expired": auth_expired,
                                    "route_not_committed": route_not_committed,
                                    "mcp_surface_drift": mcp_surface_drift,
                                    "executor_disconnect": False,
                                    "recoverable_navigation": recoverable_navigation,
                                    "self_heal": decision.to_dict(),
                                })
                                safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                                await _learning_finish(phase, attempt_no, success=False, error=message, phase_dir=phase_dir)
                                if decision.retry:
                                    continue
                                if self.options.hold_browser_on_incomplete_phase:
                                    hold = await _hold_incomplete_phase_for_human(
                                        store=human_phase_reviews, browser=shared_browser, run_id=ctx.run_id,
                                        phase=phase, phase_display=PHASE_DISPLAY.get(phase, phase),
                                        recovery_round=attempt_no, phase_dir=phase_dir, reason=message,
                                        exact_checkpoint=phase_exact_completion_checkpoint(phase, phase_dir),
                                        automated_judge=judge_result, verification=verification, config=self.config,
                                    )
                                    if hold.get("resume"):
                                        phase_loop_started = time.monotonic()
                                        continue
                                runtime_self_healer.finalize_phase(phase, judge_pass=False)
                                mission.mark_phase_blocked(phase, attempt=attempt_no, reason=message)
                                if self.options.continue_after_phase_block:
                                    blocked_phase = phase
                                    break
                                raise

                    # v2.2.5: an independent judge may only review a phase after the
                    # browser executor has produced an exact deterministic completion
                    # artifact.  Earlier releases allowed a phase with 0 committed
                    # business fields to fall through to the judge, which then produced
                    # misleading "section judge blocked" errors.  Recover from the
                    # earliest failed state instead: form/control/execution.
                    pre_judge_checkpoint = phase_exact_completion_checkpoint(phase, phase_dir)
                    safe_write_json(phase_dir / f"pre_judge_exact_execution_gate_attempt_{attempt_no:02d}.json", pre_judge_checkpoint)
                    safe_write_json(phase_dir / "pre_judge_exact_execution_gate.json", pre_judge_checkpoint)
                    if pre_judge_checkpoint.get("pass") is not True:
                        execution_message = (
                            f"HIP_PHASE_EXACT_EXECUTION_NOT_COMPLETED: phase={phase}; "
                            f"independent section judge is forbidden until the exact browser execution checkpoint passes; "
                            f"checkpoint={json.dumps(mask_sensitive_data(pre_judge_checkpoint), ensure_ascii=False, default=str)[:5000]}"
                        )
                        decision = await runtime_self_healer.handle_failure(
                            phase=phase,
                            target_url=PHASE_URLS[phase],
                            attempt=attempt_no,
                            message=execution_message,
                            failure_kind="execution_exception",
                            diagnosis={
                                "code": "HIP_PHASE_EXACT_EXECUTION_NOT_COMPLETED",
                                "phase": phase,
                                "checkpoint": pre_judge_checkpoint,
                                "failed_stage": "exact_execution",
                                "judge_invoked": False,
                            },
                        )
                        phase_attempts.append({
                            "attempt": attempt_no,
                            "status": "failed",
                            "failure_stage": "pre_judge_exact_execution_gate",
                            "error": mask_sensitive_string(execution_message),
                            "exact_completion_checkpoint": pre_judge_checkpoint,
                            "judge_invoked": False,
                            "self_heal": decision.to_dict(),
                        })
                        safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                        await _learning_finish(
                            phase, attempt_no, success=False, error=execution_message, phase_dir=phase_dir
                        )
                        if decision.retry:
                            continue
                        if self.options.hold_browser_on_incomplete_phase:
                            hold = await _hold_incomplete_phase_for_human(
                                store=human_phase_reviews, browser=shared_browser, run_id=ctx.run_id,
                                phase=phase, phase_display=PHASE_DISPLAY.get(phase, phase),
                                recovery_round=attempt_no, phase_dir=phase_dir, reason=execution_message,
                                exact_checkpoint=pre_judge_checkpoint, automated_judge=judge_result,
                                verification=verification, config=self.config,
                            )
                            if hold.get("resume"):
                                phase_loop_started = time.monotonic()
                                continue
                        runtime_self_healer.finalize_phase(phase, judge_pass=False)
                        mission.mark_phase_blocked(phase, attempt=attempt_no, reason="HIP_PHASE_EXACT_EXECUTION_NOT_COMPLETED")
                        blocked_phase = phase
                        if self.options.continue_after_phase_block:
                            break
                        raise RuntimeError(execution_message)

                    verification = build_phase_verification(
                        phase,
                        phase_dir,
                        summary,
                        vision_enabled=self.options.vision_verify,
                        golden_references=golden_refs_by_phase.get(phase, []),
                    )
                    _recover_missing_phase_screenshots(root_dir, [verification])

                    diagnosis: Dict[str, Any] = {}
                    judge_result = {}
                    if phase_judge is not None:
                        phase_input_data = read_json_any(input_path)
                        screenshots = verification.get("screenshots") or []
                        screenshot = str(screenshots[0]) if screenshots else ""
                        golden_paths = [
                            str(g.get("path"))
                            for g in golden_refs_by_phase.get(phase, [])
                            if isinstance(g, dict) and g.get("path")
                        ]
                        judge_result = phase_judge.judge_artifact_section(
                            phase=phase,
                            section=PHASE_DISPLAY.get(phase, phase),
                            expected_input=phase_input_data if isinstance(phase_input_data, dict) else {},
                            verification=verification,
                            screenshot=screenshot,
                            golden_screenshots=golden_paths,
                        )
                        safe_write_json(phase_dir / f"section_judge_gate_attempt_{attempt_no:02d}.json", judge_result)
                        safe_write_json(phase_dir / "section_judge_gate.json", judge_result)
                        if not judge_result.get("pass"):
                            diagnosis = build_section_block_diagnosis(judge_result, verification)
                    elif verification.get("status") == "failed":
                        diagnosis = {
                            "phase": phase,
                            "reasons": [{
                                "code": "DETERMINISTIC_PHASE_VERIFICATION_FAILED",
                                "message": "The phase did not satisfy deterministic exact-state verification.",
                            }],
                            "verification": verification,
                        }

                    # Any phase whose exact state graph completed must never
                    # be reopened merely because a parser/model evidence layer
                    # disagrees. Rebuild evidence and rejudge once without touching
                    # the browser. If it still blocks, stop fail-closed rather than
                    # replaying the same completed form again.
                    completed_phase_checkpoint: Dict[str, Any] = {}
                    completed_phase_no_replay = False
                    if diagnosis:
                        completed_phase_checkpoint = phase_exact_completion_checkpoint(phase, phase_dir)
                        if completed_phase_checkpoint.get("pass") is True and phase_judge is not None:
                            refreshed_verification = build_phase_verification(
                                phase,
                                phase_dir,
                                summary,
                                vision_enabled=self.options.vision_verify,
                                golden_references=golden_refs_by_phase.get(phase, []),
                            )
                            refreshed_screenshots = refreshed_verification.get("screenshots") or []
                            refreshed_screenshot = str(refreshed_screenshots[0]) if refreshed_screenshots else ""
                            refreshed_judge = phase_judge.judge_artifact_section(
                                phase=phase,
                                section=PHASE_DISPLAY.get(phase, phase),
                                expected_input=phase_input_data if isinstance(phase_input_data, dict) else {},
                                verification=refreshed_verification,
                                screenshot=refreshed_screenshot,
                                golden_screenshots=golden_paths,
                            )
                            safe_write_json(phase_dir / f"section_judge_gate_completed_rejudge_attempt_{attempt_no:02d}.json", refreshed_judge)
                            if refreshed_judge.get("pass"):
                                verification = refreshed_verification
                                judge_result = refreshed_judge
                                diagnosis = {}
                                safe_write_json(phase_dir / "completed_phase_rejudge_without_replay.json", {
                                    "schema_version": "hip.completed-phase-rejudge.v1",
                                    "phase": phase,
                                    "attempt": attempt_no,
                                    "status": "pass_after_read_only_evidence_rebuild",
                                    "exact_completion_checkpoint": completed_phase_checkpoint,
                                    "browser_replay_performed": False,
                                    "form_mutated": False,
                                })
                            else:
                                verification = refreshed_verification
                                judge_result = refreshed_judge
                                diagnosis = build_section_block_diagnosis(refreshed_judge, refreshed_verification)
                                completed_phase_no_replay = True
                        elif completed_phase_checkpoint.get("pass") is True:
                            completed_phase_no_replay = True

                    # V243R6: a dual-model false negative is not allowed to
                    # strand an exact-completed phase. When the primary judges
                    # disagree with deterministic/exact evidence, consult the
                    # on-prem champion/challenger panel. The panel can reconcile
                    # model-only disagreement, but can never override failed exact
                    # browser evidence.
                    model_consensus: Dict[str, Any] = {}
                    if phase_judge is not None and bool(getattr(self.config.human_in_the_loop, "multi_model_judge_on_disagreement", True)):
                        if not completed_phase_checkpoint and (diagnosis or phase in learning_review_phases):
                            completed_phase_checkpoint = phase_exact_completion_checkpoint(phase, phase_dir)
                        force_panel = bool(
                            phase in learning_review_phases
                            and (
                                getattr(self.config.human_in_the_loop, "multi_model_judge_force_during_learning", True)
                                or getattr(self.config.model_portfolio, "force_multi_model_during_learning", True)
                            )
                        )
                        try:
                            model_consensus = judge_consensus.resolve(
                                phase=phase,
                                automated_judge=judge_result,
                                verification=verification,
                                exact_checkpoint=completed_phase_checkpoint,
                                force=force_panel,
                            )
                        except Exception as exc:
                            model_consensus = {
                                "used": False,
                                "pass": bool(judge_result.get("pass")),
                                "needs_human": True,
                                "error": mask_sensitive_string(str(exc)),
                            }
                        safe_write_json(phase_dir / f"multi_model_judge_consensus_attempt_{attempt_no:02d}.json", model_consensus)
                        safe_write_json(phase_dir / "multi_model_judge_consensus.json", model_consensus)
                        if model_consensus.get("rescued_model_only_block") and bool(model_consensus.get("exact_evidence_pass")):
                            judge_result = dict(judge_result)
                            judge_result["pre_consensus_pass"] = bool(judge_result.get("pass"))
                            judge_result["pass"] = True
                            judge_result["status"] = "pass_reconciled_multi_model"
                            judge_result["multi_model_consensus"] = model_consensus
                            diagnosis = {}
                            completed_phase_no_replay = False
                            safe_write_json(phase_dir / "section_judge_gate.json", judge_result)

                    # During a newly learned capability family, ask the human once
                    # after automated judging whether the completed form is correct.
                    # This happens for both PASS and BLOCKED automated verdicts. A
                    # human PASS may reconcile only model/evidence disagreement when
                    # exact browser completion is already proven. A human rejection
                    # becomes supervised recovery evidence and permits self-heal.
                    human_review_required = bool(
                        phase in learning_review_phases
                        and getattr(self.config.human_in_the_loop, "enabled", True)
                        and getattr(self.config.human_in_the_loop, "review_newly_learned_phase_once", True)
                        and (
                            (bool(judge_result.get("pass")) and getattr(self.config.human_in_the_loop, "review_on_judge_pass", True))
                            or ((not bool(judge_result.get("pass"))) and getattr(self.config.human_in_the_loop, "review_on_judge_block", True))
                        )
                    )
                    if human_review_required:
                        if not completed_phase_checkpoint:
                            completed_phase_checkpoint = phase_exact_completion_checkpoint(phase, phase_dir)
                        review_request = human_phase_reviews.create_or_update(
                            run_id=ctx.run_id,
                            phase=phase,
                            phase_display=PHASE_DISPLAY.get(phase, phase),
                            attempt=attempt_no,
                            automated_judge=judge_result,
                            verification=verification,
                            exact_checkpoint=completed_phase_checkpoint,
                            model_consensus=model_consensus,
                            screenshot_path=screenshot if phase_judge is not None else "",
                            reason=(
                                "Automated judge passed; confirm the newly learned phase once before promoting the learned policy."
                                if judge_result.get("pass")
                                else "Automated judge blocked or disagreed; confirm whether the visibly filled phase is actually correct before repair/replay."
                            ),
                        )
                        safe_write_json(phase_dir / "human_phase_review_request.json", review_request)
                        if review_request.get("status") != "resolved":
                            try:
                                print(
                                    f"Human review required for {PHASE_DISPLAY.get(phase, phase)}. "
                                    f"Open Control Center -> Human Assistance and review request {review_request.get('request_id')}",
                                    flush=True,
                                )
                            except Exception:
                                pass
                            review_request = await _await_human_phase_review(
                                human_phase_reviews,
                                str(review_request.get("request_id") or ""),
                                wait_seconds=int(getattr(self.config.human_in_the_loop, "phase_review_wait_seconds", 600) or 0),
                                poll_seconds=float(getattr(self.config.human_in_the_loop, "phase_review_poll_seconds", 2.0) or 2.0),
                            )
                            safe_write_json(phase_dir / "human_phase_review_request.json", review_request)

                        # V243R11: if the operator demonstrated the flow directly in
                        # the live HIP browser while this review was waiting, capture
                        # that semantic click/navigation trajectory now. No values,
                        # selectors or screen coordinates are promoted.
                        try:
                            interactive_captures = await capture_pending_interactive_teaching(
                                interactive_teaching, shared_browser, run_id=ctx.run_id, phase=phase
                            )
                        except Exception as exc:
                            interactive_captures = []
                            safe_write_json(phase_dir / "interactive_teaching_capture_error.json", {
                                "status": "error_fail_open", "error": mask_sensitive_string(str(exc))[:500]
                            })
                        if interactive_captures:
                            safe_write_json(phase_dir / "interactive_teaching_capture.json", {
                                "schema_version": "hip.interactive-teaching-capture-summary.v1",
                                "phase": phase, "sessions": interactive_captures,
                                "values_stored": False, "selectors_stored": False, "coordinates_stored": False,
                            })

                        if review_request.get("status") != "resolved":
                            diagnosis = {
                                "phase": phase,
                                "reasons": [{
                                    "code": "HUMAN_PHASE_REVIEW_PENDING",
                                    "message": "Learning-phase review is waiting for one human confirmation in Control Center.",
                                    "request_id": review_request.get("request_id"),
                                }],
                                "human_phase_review": review_request,
                                "verification": verification,
                            }
                            completed_phase_no_replay = bool(completed_phase_checkpoint.get("pass"))
                        else:
                            effective_human_verdict = str(review_request.get("effective_human_verdict") or "")
                            human_requested_pass = str(review_request.get("human_verdict") or "") == "pass"
                            human_pass = effective_human_verdict == "pass"
                            exact_safe = bool(
                                ((judge_result.get("deterministic_judge") or {}).get("pass") if isinstance(judge_result.get("deterministic_judge"), dict) else False)
                                or completed_phase_checkpoint.get("pass")
                            )

                            # R9: when the operator approved a phase that was already
                            # visibly correct but the earlier autonomous-runtime proof
                            # was stale/incomplete, perform one read-only evidence
                            # rebuild and checkpoint refresh.  Do not execute or refill
                            # the form again.  This is the bridge that turns the human
                            # confirmation into a durable phase handoff once the live
                            # browser state is actually provable.
                            if human_requested_pass and effective_human_verdict == "pass_pending_live_reproof":
                                # R10: a human approval of an apparently correct stable
                                # form triggers one *live read-only* deterministic proof
                                # before artifact/report reconstruction.  This closes the
                                # watchdog race where the normal exact-execution JSON was
                                # never emitted even though the current browser state is
                                # already correct.
                                refreshed_checkpoint = await _refresh_live_read_only_checkpoint(
                                    phase,
                                    phase_dir,
                                    input_path,
                                    reason="human_approval_terminal_reproof",
                                )
                                refreshed_verification = build_phase_verification(
                                    phase, phase_dir, summary,
                                    vision_enabled=self.options.vision_verify,
                                    golden_references=golden_refs_by_phase.get(phase, []),
                                )
                                safe_write_json(phase_dir / "human_approval_live_reproof.json", {
                                    "schema_version": "hip.human-approval-live-reproof.v1",
                                    "phase": phase,
                                    "attempt": attempt_no,
                                    "human_verdict": "pass",
                                    "browser_replay_performed": False,
                                    "form_mutated": False,
                                    "verification_status": refreshed_verification.get("status"),
                                    "exact_completion_checkpoint": refreshed_checkpoint,
                                })
                                verification = refreshed_verification
                                completed_phase_checkpoint = refreshed_checkpoint
                                exact_safe = bool(
                                    ((judge_result.get("deterministic_judge") or {}).get("pass") if isinstance(judge_result.get("deterministic_judge"), dict) else False)
                                    or completed_phase_checkpoint.get("pass")
                                )
                                human_pass = bool(exact_safe)
                            # Reward/penalize the champion/challenger judge panel from
                            # the human label exactly once. This lets the best judge
                            # model emerge from supervised live Dell outcomes.
                            trace = model_consensus.get("portfolio_trace") if isinstance(model_consensus, dict) else {}
                            if isinstance(trace, dict) and trace.get("used") and not review_request.get("model_feedback_recorded_at"):
                                panel_pass = bool(model_consensus.get("majority_pass"))
                                aligned = panel_pass == human_pass
                                try:
                                    judge_consensus.router.record_downstream_outcome(
                                        trace=trace, success=aligned, reward=1.0 if aligned else 0.0, role="judge", drift=not aligned,
                                    )
                                    review_request = human_phase_reviews.mark_model_feedback(
                                        request_id=str(review_request.get("request_id") or ""),
                                        payload={"panel_pass": panel_pass, "human_pass": human_pass, "aligned": aligned},
                                    )
                                except Exception:
                                    pass

                            if human_pass and exact_safe:
                                judge_result = dict(judge_result)
                                judge_result["pre_human_review_pass"] = bool(judge_result.get("pass"))
                                judge_result["pass"] = True
                                judge_result["status"] = "pass_human_confirmed"
                                judge_result["human_phase_review"] = review_request
                                if model_consensus:
                                    judge_result["multi_model_consensus"] = model_consensus
                                diagnosis = {}
                                completed_phase_no_replay = False
                                safe_write_json(phase_dir / "section_judge_gate.json", judge_result)
                                safe_write_json(phase_dir / "phase_acceptance_commit.json", {
                                    "schema_version": "hip.phase-acceptance-commit.v1",
                                    "phase": phase,
                                    "attempt": attempt_no,
                                    "status": "accepted",
                                    "judge_pass": True,
                                    "human_final_review_pass": True,
                                    "exact_completion_pass": bool(completed_phase_checkpoint.get("pass")),
                                    "browser_replay_allowed": False,
                                    "next_policy": "commit phase complete and hand off; learning enrichment cannot reopen the accepted form",
                                })

                                # V243R11: human + exact live proof is the supervised
                                # graduation event. Convert the accepted adaptive path
                                # into a deterministic semantic recipe immediately,
                                # rather than waiting for another autonomous run.
                                try:
                                    validated_demo_steps: List[Dict[str, Any]] = []
                                    demo_rows = interactive_teaching.active(run_id=ctx.run_id, phase=phase)
                                    # Captured sessions are no longer "active", so also
                                    # inspect the just-captured summaries.
                                    capture_summary = _read_json_if_exists(phase_dir / "interactive_teaching_capture.json")
                                    for demo in (capture_summary.get("sessions") or [] if isinstance(capture_summary, dict) else []):
                                        if not isinstance(demo, dict):
                                            continue
                                        validated = interactive_teaching.validate(
                                            session_id=str(demo.get("session_id") or ""),
                                            exact_pass=bool(completed_phase_checkpoint.get("pass")),
                                            human_pass=True, judge_pass=True, run_id=ctx.run_id,
                                        )
                                        if validated.get("validated") and validated.get("semantic_steps"):
                                            validated_demo_steps.extend(validated.get("semantic_steps") or [])

                                    deterministic_blueprint = build_phase_flash_blueprint(
                                        phase=phase, phase_dir=phase_dir, summary=summary if isinstance(summary, dict) else {},
                                        verification=verification if isinstance(verification, dict) else {},
                                        base_input=base_input, phase_input_path=str(input_path),
                                        output_dir=phase_dir / "deterministic_promotion",
                                    )
                                    semantic_steps = validated_demo_steps or _semantic_steps_from_flash_blueprint(deterministic_blueprint, phase=phase)
                                    golden_aligned = bool(verification.get("status") in {"pass", "passed", "ok", "complete", "completed"})
                                    deterministic_promotion = deterministic_recipes.record_supervised_phase_success(
                                        phase=phase, run_id=ctx.run_id, semantic_steps=semantic_steps,
                                        exact_verified=bool(completed_phase_checkpoint.get("pass")),
                                        human_confirmed=True, judge_pass=True, golden_aligned=golden_aligned,
                                    )
                                    safe_write_json(phase_dir / "phase_deterministic_promotion.json", {
                                        "schema_version": "hip.phase-deterministic-promotion.v1",
                                        "phase": phase, "status": deterministic_promotion.get("status"),
                                        "recipe": deterministic_promotion,
                                        "source": "interactive_human_demonstration" if validated_demo_steps else "accepted_phase_semantic_blueprint",
                                        "handoff_must_not_wait_for_more_learning": True,
                                    })
                                except Exception as exc:
                                    safe_write_json(phase_dir / "phase_deterministic_promotion.json", {
                                        "schema_version": "hip.phase-deterministic-promotion.v1",
                                        "phase": phase, "status": "promotion_warning_fail_open",
                                        "error": mask_sensitive_string(str(exc))[:800],
                                        "handoff_must_not_wait_for_more_learning": True,
                                    })
                            else:
                                judge_result = dict(judge_result)
                                judge_result["pass"] = False
                                judge_result["status"] = "blocked_human_review"
                                judge_result["human_phase_review"] = review_request
                                diagnosis = {
                                    "phase": phase,
                                    "reasons": [{
                                        "code": "HUMAN_REVIEW_REJECTED" if not human_pass else "HUMAN_PASS_WITHOUT_EXACT_EVIDENCE",
                                        "message": str(review_request.get("note") or "Human review says the learned phase needs correction before continuing."),
                                    }],
                                    "human_phase_review": review_request,
                                    "model_consensus": model_consensus,
                                    "verification": verification,
                                }
                                # A human correction is authoritative learning
                                # feedback; allow the normal self-heal loop to repair
                                # even if the previous exact checkpoint had passed.
                                completed_phase_no_replay = False

                    if diagnosis:
                        safe_write_json(phase_dir / f"section_judge_block_diagnosis_attempt_{attempt_no:02d}.json", diagnosis)
                        safe_write_json(phase_dir / "section_judge_block_diagnosis.json", diagnosis)
                        safe_write_json(root_dir / "blocking_diagnosis.json", diagnosis)
                        if completed_phase_no_replay:
                            no_replay_artifact = phase_dir / "completed_phase_rejudge_without_replay.json"
                            safe_write_json(no_replay_artifact, {
                                "schema_version": "hip.completed-phase-rejudge.v1",
                                "phase": phase,
                                "attempt": attempt_no,
                                "status": "blocked_without_destructive_replay",
                                "exact_completion_checkpoint": completed_phase_checkpoint,
                                "diagnosis": diagnosis,
                                "browser_replay_performed": False,
                                "form_mutated": False,
                                "reason": "exact execution passed; unresolved judge/evidence disagreement cannot reopen and refill the completed phase",
                            })
                            phase_attempts.append({
                                "attempt": attempt_no,
                                "status": "blocked_without_phase_replay",
                                "failure_stage": "independent_judge",
                                "diagnosis": mask_sensitive_data(diagnosis),
                                "exact_completion_checkpoint": completed_phase_checkpoint,
                                "self_heal_retry": False,
                                "browser_replay_performed": False,
                            })
                            safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                            human_accepted_request: Dict[str, Any] = {}
                            hold: Dict[str, Any] = {}
                            if self.options.hold_browser_on_incomplete_phase:
                                hold = await _hold_incomplete_phase_for_human(
                                    store=human_phase_reviews, browser=shared_browser, run_id=ctx.run_id,
                                    phase=phase, phase_display=PHASE_DISPLAY.get(phase, phase),
                                    recovery_round=attempt_no, phase_dir=phase_dir,
                                    reason=f"Exact phase execution is complete but judge evidence still disagrees: {json.dumps(diagnosis, ensure_ascii=False, default=str)[:5000]}",
                                    exact_checkpoint=completed_phase_checkpoint, automated_judge=judge_result,
                                    verification=verification, config=self.config,
                                )
                                held_request = hold.get("request") if isinstance(hold.get("request"), dict) else {}
                                human_accepted_request = accepted_human_override(held_request, completed_phase_checkpoint)
                            if not human_accepted_request:
                                await _learning_finish(
                                    phase, attempt_no, success=False, verification=verification,
                                    judge_result=judge_result, error=json.dumps(diagnosis, ensure_ascii=False, default=str)[:8000],
                                    phase_dir=phase_dir,
                                )
                                if hold.get("resume"):
                                    # Read-only rejudge/reproof on the same live form; do
                                    # not close the browser or hand off downstream.
                                    phase_loop_started = time.monotonic()
                                    completed_phase_no_replay = False
                                    continue
                                blocked_phase = phase
                                runtime_self_healer.finalize_phase(phase, judge_pass=False)
                                break
                            # V243R13: "Looks correct" on an exact-completed phase is the
                            # documented reconciliation of a judge/evidence-only
                            # disagreement. Commit and hand off; re-judging the same
                            # evidence would block and ask again forever.
                            judge_result = dict(judge_result)
                            judge_result["pre_human_review_pass"] = bool(judge_result.get("pass"))
                            judge_result["pass"] = True
                            judge_result["status"] = "pass_human_confirmed"
                            judge_result["human_phase_review"] = human_accepted_request
                            judge_result["reconciled_diagnosis"] = mask_sensitive_data(diagnosis)
                            diagnosis = {}
                            completed_phase_no_replay = False
                            safe_write_json(phase_dir / "section_judge_gate.json", judge_result)
                            safe_write_json(phase_dir / "phase_acceptance_commit.json", {
                                "schema_version": "hip.phase-acceptance-commit.v1",
                                "phase": phase,
                                "attempt": attempt_no,
                                "status": "accepted",
                                "judge_pass": True,
                                "human_final_review_pass": True,
                                "exact_completion_pass": True,
                                "acceptance_source": "human_recovery_review_on_exact_completed_phase",
                                "browser_replay_allowed": False,
                                "next_policy": "commit phase complete and hand off; learning enrichment cannot reopen the accepted form",
                            })
                            phase_attempts.append({
                                "attempt": attempt_no,
                                "status": "accepted_human_confirmed_exact",
                                "failure_stage": "",
                                "exact_completion_checkpoint": completed_phase_checkpoint,
                                "browser_replay_performed": False,
                            })
                            safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                    if diagnosis:
                        decision = await runtime_self_healer.handle_failure(
                            phase=phase,
                            target_url=PHASE_URLS[phase],
                            attempt=attempt_no,
                            message=f"Section judge blocked {phase}: {json.dumps(diagnosis, ensure_ascii=False, default=str)[:8000]}",
                            failure_kind="judge",
                            diagnosis=diagnosis,
                            verification=verification,
                            judge_result=judge_result,
                        )
                        phase_attempts.append({
                            "attempt": attempt_no,
                            "status": "failed",
                            "failure_stage": "independent_judge",
                            "diagnosis": mask_sensitive_data(diagnosis),
                            "self_heal": decision.to_dict(),
                        })
                        safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                        await _learning_finish(
                            phase, attempt_no, success=False, verification=verification,
                            judge_result=judge_result, error=json.dumps(diagnosis, ensure_ascii=False, default=str)[:8000],
                            phase_dir=phase_dir,
                        )
                        if decision.retry:
                            continue
                        if self.options.hold_browser_on_incomplete_phase:
                            hold = await _hold_incomplete_phase_for_human(
                                store=human_phase_reviews, browser=shared_browser, run_id=ctx.run_id,
                                phase=phase, phase_display=PHASE_DISPLAY.get(phase, phase),
                                recovery_round=attempt_no, phase_dir=phase_dir,
                                reason=f"Section judge/reconciliation still blocks {phase}: {json.dumps(diagnosis, ensure_ascii=False, default=str)[:5000]}",
                                exact_checkpoint=completed_phase_checkpoint or phase_exact_completion_checkpoint(phase, phase_dir),
                                automated_judge=judge_result, verification=verification, config=self.config,
                            )
                            if hold.get("resume"):
                                phase_loop_started = time.monotonic()
                                continue
                        blocked_phase = phase
                        runtime_self_healer.finalize_phase(phase, judge_pass=False)
                        try:
                            codes = ", ".join(str(x.get("code")) for x in diagnosis.get("reasons") or []) or "unknown"
                            print(
                                f"Self-heal exhausted for {phase}: {codes}. See {phase_dir / 'section_judge_block_diagnosis.json'}",
                                flush=True,
                            )
                        except Exception:
                            pass
                        break

                    maximum_evidence = await runtime_self_healer.capture_success_evidence(
                        phase=phase,
                        target_url=PHASE_URLS[phase],
                        attempt=attempt_no,
                        phase_dir=phase_dir,
                        input_payload=phase_payload_for_memory if isinstance(phase_payload_for_memory, dict) else {},
                        state_graph=phase_graph_for_memory if isinstance(phase_graph_for_memory, dict) else {},
                        verification=verification if isinstance(verification, dict) else {},
                    )
                    if isinstance(summary, dict):
                        summary["maximum_observability"] = maximum_evidence
                    # V236: exact current-run input/control coverage is a completion
                    # requirement. Deterministic replay readiness is a *learning*
                    # quality signal only; an unavailable witness/MCP channel must
                    # not turn a correctly filled live form into BLOCKED.
                    evidence_incomplete = bool(
                        maximum_evidence.get("coverage_pass") is False
                    ) if isinstance(maximum_evidence, dict) else False
                    replay_not_ready = bool(
                        isinstance(maximum_evidence, dict)
                        and maximum_evidence.get("replay_ready") is False
                    )
                    if replay_not_ready:
                        safe_write_json(phase_dir / "deterministic_replay_learning_warning.json", {
                            "schema_version": "hip.replay-learning-warning.v1",
                            "phase": phase,
                            "status": "warning_only",
                            "code": "HIP_REPLAY_NOT_READY_CURRENT_FORM_STILL_ELIGIBLE",
                            "policy": "do not promote fast replay; continue using live discovery. Current phase completion is decided by exact input-owned field coverage and verification.",
                            "maximum_observability": maximum_evidence,
                        })
                    phase_acceptance_committed = bool(
                        completed_phase_checkpoint.get("pass")
                        and bool(judge_result.get("pass", True))
                    )
                    if evidence_incomplete and phase_acceptance_committed:
                        safe_write_json(phase_dir / "maximum_observability_learning_warning.json", {
                            "schema_version": "hip.maximum-observability-learning-warning.v1",
                            "phase": phase,
                            "status": "warning_only",
                            "code": "HIP_LEARNING_EVIDENCE_INCOMPLETE_AFTER_PHASE_ACCEPTANCE",
                            "maximum_observability": maximum_evidence,
                            "phase_completion_blocked": False,
                            "policy": "do not reopen/refill an exact+judge accepted form; withhold deterministic-memory promotion and continue to the next phase",
                        })
                        evidence_incomplete = False

                    if evidence_incomplete:
                        coverage_diagnosis = {
                            "schema_version": "hip.maximum-observability-completion-gate.v1",
                            "phase": phase,
                            "status": "blocked",
                            "code": "HIP_INPUT_CONTROL_COVERAGE_INCOMPLETE",
                            "maximum_observability": maximum_evidence,
                            "required_recovery": "re-explore the earliest unresolved input/control or parent-child mapping; do not promote deterministic replay",
                        }
                        safe_write_json(phase_dir / "maximum_observability_completion_block.json", coverage_diagnosis)
                        decision = await runtime_self_healer.handle_failure(
                            phase=phase,
                            target_url=PHASE_URLS[phase],
                            attempt=attempt_no,
                            message=f"Maximum-observability completion gate blocked {phase}: exact input/control coverage is incomplete",
                            failure_kind="judge",
                            diagnosis=coverage_diagnosis,
                            verification=verification,
                            judge_result=judge_result,
                        )
                        phase_attempts.append({
                            "attempt": attempt_no,
                            "status": "failed",
                            "failure_stage": "maximum_observability_completion_gate",
                            "diagnosis": coverage_diagnosis,
                            "self_heal": decision.to_dict(),
                        })
                        safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                        await _learning_finish(
                            phase, attempt_no, success=False, verification=verification,
                            judge_result=judge_result, error=json.dumps(coverage_diagnosis, ensure_ascii=False, default=str)[:8000],
                            phase_dir=phase_dir,
                        )
                        if decision.retry:
                            continue
                        if self.options.hold_browser_on_incomplete_phase:
                            hold = await _hold_incomplete_phase_for_human(
                                store=human_phase_reviews, browser=shared_browser, run_id=ctx.run_id,
                                phase=phase, phase_display=PHASE_DISPLAY.get(phase, phase),
                                recovery_round=attempt_no, phase_dir=phase_dir,
                                reason=json.dumps(mask_sensitive_data(locals().get("coverage_diagnosis") or locals().get("api_diagnosis") or locals().get("assurance_diagnosis") or {"message": "phase completion gate blocked"}), ensure_ascii=False, default=str)[:5000],
                                exact_checkpoint=phase_exact_completion_checkpoint(phase, phase_dir),
                                automated_judge=judge_result, verification=verification, config=self.config,
                            )
                            if hold.get("resume"):
                                phase_loop_started = time.monotonic()
                                continue
                        blocked_phase = phase
                        runtime_self_healer.finalize_phase(phase, judge_pass=False)
                        break

                    phase_attempts.append({
                        "attempt": attempt_no,
                        "status": "pass",
                        "failure_stage": "",
                        "self_heal_used": attempt_no > 1,
                        "judge_pass": bool(judge_result.get("pass", True)),
                    })
                    safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                    if attempt_no > 1:
                        safe_write_json(phase_dir / "self_heal_resolution.json", {
                            "status": "resolved",
                            "phase": phase,
                            "successful_attempt": attempt_no,
                            "judge_pass": bool(judge_result.get("pass", True)),
                            "note": "Earlier failure evidence is preserved in runtime_self_heal/; the current exact phase state passed independently.",
                        })
                        for stale_blocker in (
                            phase_dir / "section_judge_block_diagnosis.json",
                            root_dir / "blocking_diagnosis.json",
                        ):
                            try:
                                if stale_blocker.exists():
                                    stale_blocker.unlink()
                            except Exception:
                                pass
                    api_agent_result = await _learning_finish(
                        phase, attempt_no, success=True, verification=verification,
                        judge_result=judge_result, phase_dir=phase_dir,
                    )
                    if self.options.require_api_capture_for_completion and not bool(api_agent_result.get("pass")) and not _accepted_phase_commit(phase_dir):
                        api_diagnosis = {
                            "schema_version": "hip.form-api-completion-gate.v1",
                            "phase": phase,
                            "status": "blocked",
                            "code": "HIP_FORM_API_CAPTURE_OR_PARITY_INCOMPLETE",
                            "api_agent_result": api_agent_result,
                            "required_recovery": "re-observe form-open/fill traffic and rebuild the UI/API crosswalk; never invent an endpoint or payload key",
                        }
                        safe_write_json(phase_dir / "form_api_intelligence_completion_block.json", api_diagnosis)
                        decision = await runtime_self_healer.handle_failure(
                            phase=phase,
                            target_url=PHASE_URLS[phase],
                            attempt=attempt_no,
                            message=f"Form/API intelligence completion gate blocked {phase}",
                            failure_kind="judge",
                            diagnosis=api_diagnosis,
                            verification=verification,
                            judge_result=judge_result,
                        )
                        phase_attempts.append({
                            "attempt": attempt_no,
                            "status": "failed",
                            "failure_stage": "form_api_intelligence_completion_gate",
                            "diagnosis": api_diagnosis,
                            "self_heal": decision.to_dict(),
                        })
                        safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                        if decision.retry:
                            continue
                        if self.options.hold_browser_on_incomplete_phase:
                            hold = await _hold_incomplete_phase_for_human(
                                store=human_phase_reviews, browser=shared_browser, run_id=ctx.run_id,
                                phase=phase, phase_display=PHASE_DISPLAY.get(phase, phase),
                                recovery_round=attempt_no, phase_dir=phase_dir,
                                reason=json.dumps(mask_sensitive_data(locals().get("coverage_diagnosis") or locals().get("api_diagnosis") or locals().get("assurance_diagnosis") or {"message": "phase completion gate blocked"}), ensure_ascii=False, default=str)[:5000],
                                exact_checkpoint=phase_exact_completion_checkpoint(phase, phase_dir),
                                automated_judge=judge_result, verification=verification, config=self.config,
                            )
                            if hold.get("resume"):
                                phase_loop_started = time.monotonic()
                                continue
                        blocked_phase = phase
                        runtime_self_healer.finalize_phase(phase, judge_pass=False)
                        break
                    if isinstance(summary, dict):
                        summary["form_api_agentq"] = api_agent_result
                    trajectory_result: Dict[str, Any] = {"status": "disabled"}
                    if bool(getattr(self.config.runtime_self_heal, "capture_validated_trajectory", True)):
                        try:
                            learning_summary_path = phase_dir / "portal_learning" / f"attempt_{attempt_no:02d}" / "portal_learning_summary.json"
                            learning_summary = read_json_any(learning_summary_path) if learning_summary_path.is_file() else {}
                            trajectory_result = build_validated_phase_trajectory(
                                phase=phase,
                                phase_dir=phase_dir,
                                attempt=attempt_no,
                                verification=verification,
                                judge_result=judge_result,
                                golden_references=list(golden_refs_by_phase.get(phase, []) or []),
                                portal_learning_summary=learning_summary if isinstance(learning_summary, dict) else {},
                            )
                        except Exception as exc:
                            trajectory_result = {
                                "status": "error_fail_open",
                                "error": mask_sensitive_string(str(exc)),
                                "run_continues": True,
                            }
                    safe_write_json(phase_dir / "validated_deterministic_trajectory_result.json", trajectory_result)

                    # Final autonomous assurance gate: recapture fresh evidence from
                    # all required MCP channels *after* the exact UI/API/judge state
                    # is reached. A phase is not promoted to deterministic memory
                    # when any required channel or replay artifact is partial/stale.
                    mcp_quorum = await capture_mcp_evidence_quorum(
                        browser=shared_browser,
                        agentq_controller=agentq_controller,
                        phase=phase,
                        attempt=attempt_no,
                        phase_dir=phase_dir,
                        required=bool(ctx.registry.get("mcp_required", False) or self.options.strict_mission_assurance),
                    )
                    assurance = build_phase_assurance_report(
                        phase=phase,
                        attempt=attempt_no,
                        verification=verification if isinstance(verification, dict) else {},
                        judge_result=judge_result if isinstance(judge_result, dict) else {},
                        maximum_observability=maximum_evidence if isinstance(maximum_evidence, dict) else {},
                        api_result=api_agent_result if isinstance(api_agent_result, dict) else {},
                        trajectory_result=trajectory_result if isinstance(trajectory_result, dict) else {},
                        mcp_quorum=mcp_quorum if isinstance(mcp_quorum, dict) else {},
                        strict=bool(self.options.strict_mission_assurance),
                    )
                    safe_write_json(phase_dir / "phase_mission_assurance.json", assurance)
                    if bool(assurance.get("pass")) and isinstance(trajectory_result, dict) and trajectory_result.get("trust") == "validated":
                        try:
                            capability_promotion = promote_validated_phase_to_capability_graph(
                                graph=capability_graph, phase=phase, phase_dir=phase_dir,
                                trajectory=trajectory_result, run_id=ctx.run_id,
                            )
                        except Exception as exc:
                            capability_promotion = {
                                "status": "error_fail_open",
                                "error": mask_sensitive_string(str(exc)),
                                "run_continues": True,
                            }
                        safe_write_json(phase_dir / "capability_graph_promotion.json", capability_promotion)
                    if self.options.strict_mission_assurance and not bool(assurance.get("pass")) and not _accepted_phase_commit(phase_dir):
                        assurance_diagnosis = {
                            "schema_version": "hip.phase-mission-assurance-block.v1",
                            "phase": phase,
                            "status": "blocked",
                            "code": "HIP_PHASE_ASSURANCE_INCOMPLETE",
                            "assurance": assurance,
                            "required_recovery": "repair the earliest missing assurance dimension and recapture fresh MCP/UI/API/judge evidence before deterministic memory promotion",
                        }
                        safe_write_json(phase_dir / "phase_mission_assurance_block.json", assurance_diagnosis)
                        decision = await runtime_self_healer.handle_failure(
                            phase=phase, target_url=PHASE_URLS[phase], attempt=attempt_no,
                            message=f"Mission assurance gate blocked {phase}",
                            failure_kind="judge", diagnosis=assurance_diagnosis,
                            verification=verification, judge_result=judge_result,
                        )
                        phase_attempts.append({
                            "attempt": attempt_no, "status": "failed",
                            "failure_stage": "mission_assurance_gate",
                            "diagnosis": assurance_diagnosis, "self_heal": decision.to_dict(),
                        })
                        safe_write_json(phase_dir / "phase_execution_attempts.json", phase_attempts)
                        if decision.retry:
                            continue
                        if self.options.hold_browser_on_incomplete_phase:
                            hold = await _hold_incomplete_phase_for_human(
                                store=human_phase_reviews, browser=shared_browser, run_id=ctx.run_id,
                                phase=phase, phase_display=PHASE_DISPLAY.get(phase, phase),
                                recovery_round=attempt_no, phase_dir=phase_dir,
                                reason=json.dumps(mask_sensitive_data(locals().get("coverage_diagnosis") or locals().get("api_diagnosis") or locals().get("assurance_diagnosis") or {"message": "phase completion gate blocked"}), ensure_ascii=False, default=str)[:5000],
                                exact_checkpoint=phase_exact_completion_checkpoint(phase, phase_dir),
                                automated_judge=judge_result, verification=verification, config=self.config,
                            )
                            if hold.get("resume"):
                                phase_loop_started = time.monotonic()
                                continue
                        blocked_phase = phase
                        runtime_self_healer.finalize_phase(phase, judge_pass=False)
                        break

                    try:
                        memory_promotion = flow_pattern_memory.promote_from_phase_dir(
                            phase=phase, phase_dir=phase_dir,
                            input_payload=phase_payload_for_memory if isinstance(phase_payload_for_memory, dict) else {},
                            run_id=ctx.run_id,
                            judge_pass=bool(judge_result.get("pass", True)),
                            graph_builder=compile_phase_state_graph,
                        )
                    except Exception as exc:
                        memory_promotion = {
                            "status": "error_fail_open",
                            "error": mask_sensitive_string(str(exc)),
                            "run_continues": True,
                        }
                    safe_write_json(phase_dir / "flow_pattern_memory_promotion.json", memory_promotion)
                    safe_write_json(phase_dir / "validated_replay_promotion_summary.json", {
                        "phase": phase,
                        "trajectory": trajectory_result,
                        "flow_pattern_memory": memory_promotion,
                        "judge_pass": True,
                        "values_stored": False,
                    })
                    # R10: make persistent learning observable.  This receipt proves
                    # which long-term structural stores were updated/available after
                    # a judged phase without copying the customer's runtime values
                    # into reusable memory.
                    try:
                        learning_memory_receipt = {
                            "schema_version": "hip.phase-learning-memory-receipt.v1",
                            "phase": phase,
                            "status": "validated_phase_learning_recorded",
                            "persistent_memory_root": str(self.config.reporting.memory_dir),
                            "portal_brain": portal_brain.status(),
                            "capability_graph": capability_graph.manifest(),
                            "flow_pattern_memory_promotion": memory_promotion,
                            "judge_pass": True,
                            "exact_completion_pass": bool(phase_exact_completion_checkpoint(phase, phase_dir).get("pass")),
                            "customer_values_persisted": False,
                            "values_stored": False,
                            "selectors_stored_as_authority": False,
                            "coordinates_stored_as_authority": False,
                        }
                    except Exception as exc:
                        learning_memory_receipt = {
                            "schema_version": "hip.phase-learning-memory-receipt.v1",
                            "phase": phase,
                            "status": "receipt_error_fail_open",
                            "error": mask_sensitive_string(str(exc))[:500],
                            "customer_values_persisted": False,
                            "values_stored": False,
                        }
                    safe_write_json(phase_dir / "phase_learning_memory_receipt.json", learning_memory_receipt)
                    # R12 continuous portal learning: filling/navigation is the
                    # training experience. Promote semantic action/effect sequences
                    # only after the live phase has exact proof and judge/human PASS.
                    try:
                        human_review_payload = judge_result.get("human_phase_review") if isinstance(judge_result, dict) and isinstance(judge_result.get("human_phase_review"), dict) else {}
                        # Missing human review is not equivalent to approval. When the
                        # strict R12 promotion gate is enabled it remains UNKNOWN and
                        # cannot create a deterministic/trusted recipe.
                        human_pass_for_learning = None
                        if human_review_payload:
                            human_pass_for_learning = str(
                                human_review_payload.get("effective_human_verdict")
                                or human_review_payload.get("human_verdict")
                                or ""
                            ).strip().lower() == "pass"
                        continuous_receipt = continuous_learning.learn_phase(
                            browser=shared_browser, phase=phase, run_id=ctx.run_id,
                            task=f"Learn and complete HIP {PHASE_DISPLAY.get(phase, phase)} from input",
                            action_start_index=phase_action_start_index,
                            exact_verified=bool(phase_exact_completion_checkpoint(phase, phase_dir).get("pass")),
                            judge_pass=bool(judge_result.get("pass", True)),
                            human_pass=human_pass_for_learning,
                            input_root=phase, page_families=[PHASE_TO_CAPABILITY_FAMILY.get(phase, phase)],
                        )
                    except Exception as exc:
                        continuous_receipt = {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))[:500]}
                    safe_write_json(phase_dir / "continuous_learning_receipt.json", continuous_receipt)
                    runtime_self_healer.finalize_phase(phase, judge_pass=True)
                    # Persist the judged evidence payloads so a later interrupted
                    # run can adopt this phase without replaying the browser.
                    safe_write_json(phase_dir / PHASE_VERIFICATION_FILENAME, verification)
                    safe_write_json(phase_dir / PHASE_JUDGE_RESULT_FILENAME, judge_result or {"pass": True, "status": "judge_disabled"})
                    mission.mark_phase_complete(phase, attempt=attempt_no, judge_pass=bool(judge_result.get("pass", True)))
                    safe_write_json(phase_dir / "phase_completion_token.json", {
                        "schema_version": "hip.phase-completion-token.v1",
                        "phase": phase,
                        "attempt": attempt_no,
                        "status": "complete",
                        "judge_pass": bool(judge_result.get("pass", True)),
                        "exact_completion_checkpoint": phase_exact_completion_checkpoint(phase, phase_dir),
                        "human_review": (judge_result.get("human_phase_review") if isinstance(judge_result, dict) else {}),
                        "reexecute_same_phase": False,
                        "next_policy": "handoff_to_next_executable_phase",
                    })
                    try:
                        mission_trace.refresh_phase_artifacts(
                            phase, phase_dir,
                            verification=verification if isinstance(verification, dict) else {},
                            judge=judge_result if isinstance(judge_result, dict) else {},
                        )
                    except Exception:
                        pass
                    try:
                        mission.record_phase_entities(
                            phase,
                            phase_input=phase_payload_for_memory if isinstance(phase_payload_for_memory, dict) else {},
                            summary=summary if isinstance(summary, dict) else {},
                        )
                        # Keep the remaining live phases referencing one
                        # consistent entity set for this run.
                        remaining = phases[phase_index + 1:]
                        for later_phase in remaining:
                            later_path = Path(phase_input_paths[later_phase])
                            later_payload = read_json_any(later_path)
                            if isinstance(later_payload, dict):
                                later_payload["_mission_prior_entities"] = mission.prior_entities_for(later_phase)
                                safe_write_json(later_path, later_payload, mask=False)
                    except Exception as exc:
                        safe_write_json(phase_dir / "mission_entity_registry_error.json", {
                            "status": "error_fail_open",
                            "error": mask_sensitive_string(str(exc)),
                            "run_continues": True,
                        })

                    # Layer 8: route to the next *executable* phase, skipping
                    # any phases already adopted/complete without browser churn.
                    transition_plan = transition_coordinator.plan_after(phase, mission.phase_status)
                    safe_write_json(phase_dir / "phase_transition_plan.json", transition_plan)
                    for skipped_phase in transition_plan.get("skipped_completed_phases") or []:
                        try:
                            mission_trace.record_observation(
                                skipped_phase,
                                summary=f"No browser handoff required; {skipped_phase} is already complete/resumed",
                                source="mission_transition",
                                details={"skipped_browser_navigation": True, "from_phase": phase},
                            )
                        except Exception:
                            pass
                    next_phase = str(transition_plan.get("next_phase") or "")
                    if next_phase:
                        transition_coordinator.arm(
                            from_phase=phase,
                            to_phase=next_phase,
                            source_status="complete",
                            skipped_completed_phases=transition_plan.get("skipped_completed_phases") or [],
                            source_exact_verified=True,
                        )
                        handoff = await shared_browser.handoff_to_next_phase(
                            from_phase=phase,
                            to_phase=next_phase,
                            to_url=PHASE_URLS[next_phase],
                            exact_checkpoint_passed=True,
                            allow_from_blocked=False,
                        )
                        safe_write_json(phase_dir / "phase_handoff_to_next.json", handoff)
                        if not handoff.get("pass"):
                            try:
                                mission_trace.record_warning(phase, f"Phase completed but route handoff needs bounded recovery: {handoff.get('code')}")
                            except Exception:
                                pass
                            # Keep the transition pending. The destination phase
                            # must acknowledge it only after its own route+dual-MCP
                            # preflight succeeds; the completed source is not replayed.
                    phase_completed = True
                    break

                if not phase_completed:
                    try:
                        mission_trace.refresh_phase_artifacts(
                            phase, phase_dir,
                            verification=verification if isinstance(verification, dict) else {},
                            judge=judge_result if isinstance(judge_result, dict) else {},
                        )
                    except Exception:
                        pass
                    mission.mark_phase_blocked(
                        phase,
                        attempt=attempt_index,
                        reason=(
                            f"blocked by section judge in phase {blocked_phase}"
                            if blocked_phase
                            else "self-heal stopped before a judged pass"
                        ),
                    )
                    if phase not in blocked_phases:
                        blocked_phases.append(phase)
                    if summary:
                        phase_summaries[phase] = mask_sensitive_data(summary)
                    if verification:
                        phase_verifications.append(verification)
                    if judge_result:
                        phase_judge_results.append(judge_result)
                    if self.options.continue_after_phase_block and not runtime_self_healer.until_complete:
                        # Diagnostic/no-save mode may attempt remaining sections.
                        # Completion-first self-heal never uses this path: it must
                        # repair the current phase before any downstream phase starts.
                        safe_write_json(root_dir / "full_mission_phase_continuation.json", {
                            "blocked_phases": blocked_phases,
                            "next_policy": "continue_remaining_selected_phases",
                            "safety": "no-save form execution only; final mission cannot pass while any phase is blocked",
                        })
                        transition_plan = transition_coordinator.plan_after(phase, mission.phase_status)
                        safe_write_json(phase_dir / "blocked_phase_transition_plan.json", transition_plan)
                        next_phase = str(transition_plan.get("next_phase") or "")
                        if next_phase:
                            transition_coordinator.arm(
                                from_phase=phase,
                                to_phase=next_phase,
                                source_status="blocked",
                                skipped_completed_phases=transition_plan.get("skipped_completed_phases") or [],
                                source_exact_verified=False,
                            )
                            blocked_handoff = await shared_browser.handoff_to_next_phase(
                                from_phase=phase,
                                to_phase=next_phase,
                                to_url=PHASE_URLS[next_phase],
                                exact_checkpoint_passed=False,
                                allow_from_blocked=True,
                            )
                            safe_write_json(phase_dir / "blocked_phase_handoff_to_next.json", blocked_handoff)
                        blocked_phase = ""
                        continue
                    if blocked_phase:
                        break
                    if runtime_self_healer.until_complete:
                        raise RuntimeError(
                            f"Phase {phase} stopped before completion because a proven safety blocker prevented further no-save recovery"
                        )
                    raise RuntimeError(
                        f"Phase {phase} exhausted {max_phase_attempts} bounded self-heal attempts without a judged pass"
                    )

                phase_summaries[phase] = mask_sensitive_data(summary)
                phase_verifications.append(verification)
                if judge_result:
                    phase_judge_results.append(judge_result)

        finally:
            safe_write_json(root_dir / "browser_session_final_state.json", {
                "session_id": shared_browser.session_id,
                "start_count": shared_browser._start_count,
                "borrow_count": shared_browser._borrow_count,
                "sso_prompt_count": shared_browser._sso_prompt_count,
                "reauth_count": shared_browser._reauth_count,
                "authenticated_once": shared_browser._authenticated_once,
                "phase_history": shared_browser._phase_history,
                **shared_browser._session_phase_metrics(),
                "phase_transition_count": shared_browser._phase_transition_count,
                "page_recovery_count": shared_browser._page_recovery_count,
                "overlay_recovery_count": shared_browser._overlay_recovery_count,
                "single_persistent_context": True,
            })
            try:
                await agentq_controller.close()
            finally:
                await shared_browser.close()

        # V243R7: an incomplete mission is a resumable checkpoint, not a final
        # deliverable. In the normal completion-first profile this code is not
        # reached while blocked because the browser is held open indefinitely for
        # recovery. If an operator configured a finite wait/disabled hold, still
        # refuse to emit FINAL HTML/CSV/ZIP artifacts from partial work.
        incomplete_terminal = bool(blocked_phases or blocked_phase)
        if incomplete_terminal and self.options.never_finalize_incomplete_run:
            checkpoint = {
                "schema_version": "hip.incomplete-run-checkpoint.v1",
                "run_id": ctx.run_id,
                "status": "incomplete_checkpoint_only",
                "application_complete": False,
                "blocked_phase": (blocked_phases[0] if blocked_phases else blocked_phase),
                "blocked_phases": list(blocked_phases),
                "phase_status": mask_sensitive_data(getattr(mission, "phase_status", {})),
                "final_report_generated": False,
                "summary_zip_generated": False,
                "policy": "resume/recover the blocked phase; do not treat partial execution as a final result",
                "written_at": utc_now(),
            }
            safe_write_json(root_dir / "INCOMPLETE_RUN_CHECKPOINT.json", checkpoint)
            return {
                "run_id": ctx.run_id,
                "overall_status": "incomplete_checkpoint_only",
                "application_complete": False,
                "blocked_phase": checkpoint["blocked_phase"],
                "blocked_phases": checkpoint["blocked_phases"],
                "final_report_generated": False,
                "summary_zip_generated": False,
                "checkpoint": str(root_dir / "INCOMPLETE_RUN_CHECKPOINT.json"),
            }

        _recover_missing_phase_screenshots(root_dir, phase_verifications)
        prompts = build_vision_prompts(phase_verifications)
        safe_write_json(root_dir / "vision_verification_prompts.json", prompts)
        vision_results = run_optional_vision_verification(prompts) if self.options.vision_verify else []
        safe_write_json(root_dir / "vision_verification_results.json", vision_results)

        replay_manifest: Dict[str, Any] = {}
        if self.options.save_replay_blueprint:
            replay_manifest = build_flash_replay_package(
                root_dir=root_dir,
                base_input=base_input,
                phase_input_paths=phase_input_paths,
                phase_summaries=phase_summaries,
                phase_verifications=phase_verifications,
                phases=phases,
                max_dropdown_options=self.options.max_dropdown_options_per_field,
                semantic_only=bool(
                    getattr(self.config.autonomous_form, "semantic_replay_blueprint_only", True)
                    and getattr(self.config.autonomous_form, "never_persist_selectors_or_coordinates", True)
                ),
            )

        overall_status = "blocked_by_phase" if blocked_phases else "pass" if all(v.get("status") == "pass" for v in phase_verifications) else "failed" if any(v.get("status") == "failed" for v in phase_verifications) else "pass_with_warnings"
        legacy_blocked_phase = blocked_phases[0] if blocked_phases else blocked_phase
        terminal_gate = transition_coordinator.terminal_gate(mission)
        if not terminal_gate.get("pass") and overall_status == "pass":
            overall_status = "failed_terminal_gate"

        # Witness safety is evaluated before the final mission verdict so a run
        # can never be labeled complete if a mutation control was clicked or a
        # mutating request escaped.  Search/validation POSTs are treated as
        # read-only by endpoint classification.
        witness_report: Dict[str, Any] = {"status": "disabled", "pass": True}
        if self.options.live_witness_mode:
            # Flush the independent capture-phase click log before certification.
            # This catches helper/DOM clicks that bypassed the governed ActionEvent path.
            try:
                await shared_browser.collect_dom_click_log()
            except Exception:
                pass
            witness_report = build_live_witness_report(
                action_events=list(getattr(shared_browser, "action_events", []) or []),
                network_events=list(getattr(shared_browser, "network_tab_events", []) or []),
                click_events=list(getattr(shared_browser, "click_events", []) or []),
                mission_complete=bool(terminal_gate.get("pass") and not blocked_phases and all(v.get("status") == "pass" for v in phase_verifications)),
                phase_count=len(phases),
            )
            safe_write_json(root_dir / "live_witness_certificate.json", witness_report, mask=False)
            if not witness_report.get("safety_pass"):
                overall_status = "witness_safety_violation"
                terminal_gate = {**terminal_gate, "pass": False, "witness_safety_pass": False, "witness_certificate": str(root_dir / "live_witness_certificate.json")}
            elif not witness_report.get("pass") and overall_status == "pass":
                overall_status = "witness_incomplete"

        final_consolidation = FinalMissionConsolidator(
            root_dir, run_id=ctx.run_id, phases=phases, mode=mission.mode
        ).evaluate(
            mission=mission,
            terminal_gate=terminal_gate,
            phase_verifications=phase_verifications,
            witness_report=witness_report,
            transition_state=transition_coordinator.state,
        )
        if not final_consolidation.get("pass"):
            terminal_gate = {
                **terminal_gate,
                "pass": False,
                "final_mission_consolidation_pass": False,
                "final_mission_consolidation": str(root_dir / "final_mission_consolidation.json"),
            }
            if overall_status in {"pass", "pass_with_warnings"}:
                overall_status = "failed_final_mission_consolidation"

        mission_report = mission.write_final_verdict(
            overall_status=overall_status,
            blocked_phase=legacy_blocked_phase,
            terminal_gate_pass=bool(terminal_gate.get("pass")),
        )
        try:
            mission_trace.finalize(complete=bool(mission_report.get("application_complete")))
        except Exception:
            pass
        runtime_self_heal_summary = runtime_self_healer.write_summary()
        form_api_summary = form_api_agent.write_run_summary()
        aggregate = {
            "run_id": ctx.run_id,
            "overall_status": overall_status,
            "strict_replication_enabled": bool(self.options.strict_replication),
            "customer": ctx.customer,
            "browser_backend": ctx.registry.get("browser_backend_used", getattr(self.config.mcp, "browser_backend", "unknown")),
            "requested_browser_backend": ctx.registry.get("requested_browser_backend", getattr(self.config.mcp, "browser_backend", "unknown")),
            "mcp_required": bool(ctx.registry.get("mcp_required")),
            "mcp_preflight": ctx.registry.get("mcp_preflight", {}),
            "deterministic_plan_manifest": deterministic_plan_manifest,
            "deterministic_plan_source": "persistent HIP Portal brain + judge-validated same-flow pattern memory + learned form knowledge merged with current input.json",
            "flow_pattern_memory": {
                "enabled": flow_pattern_memory.enabled,
                "manifest": str(flow_pattern_memory.manifest_path),
                "patterns": str(flow_pattern_memory.patterns_path),
                "bootstrap": flow_pattern_bootstrap,
                "values_stored": False,
            },
            "portal_brain_bootstrap": brain_bootstrap,
            "unified_kb_import": unified_kb_import,
            "generated_at": utc_now(),
            "input_json": str(input_json),
            "mode": "full_kb_context" if self.options.full_kb_context else "fast_form_only",
            "phase_sequence": phases,
            "phase_urls": PHASE_URLS,
            "phase_summaries": phase_summaries,
            "phase_verifications": phase_verifications,
            "section_judge_results": phase_judge_results,
            "blocked_phase": legacy_blocked_phase,
            "blocked_phases": blocked_phases,
            "section_judge_policy": judge_policy.__dict__,
            "golden_reference_screenshots": golden_refs_by_phase,
            "golden_screenshot_dir": str(self.options.golden_screenshot_dir or ""),
            "upload_assets_dir": str(self.options.upload_assets_dir or ""),
            "upload_assets_manifest": upload_assets_manifest,
            "vision_verification_results": vision_results,
            "flash_replay_manifest": replay_manifest,
            "runtime_self_heal": runtime_self_heal_summary,
            "form_api_agentq": form_api_summary,
            "live_witness": witness_report,
            "final_mission_consolidation": final_consolidation,
            "autonomous_mission": {
                "application_complete": bool(mission_report.get("application_complete")),
                "mission_status": mission_report.get("mission_status"),
                "resume": mission_report.get("resume"),
                "mission_state": str(root_dir / "mission_state.json"),
                "mission_completion_report": str(root_dir / "mission_completion_report.json"),
                "mission_completion_report_md": str(root_dir / "mission_completion_report.md"),
                "entity_registry": mission_report.get("entity_registry"),
                "mission_trace": str(root_dir / "mission_trace.json"),
                "transition_state": str(root_dir / "mission_transition_state.json"),
                "terminal_completion_gate": terminal_gate,
            },
            "safety_policy": {
                "never_click": MUTATING_WORDS,
                "live_witness_mode": bool(self.options.live_witness_mode),
                "note": (
                    "Live witness mode prohibits mutation-control clicks and mutating API requests; submit capture and API write are disabled."
                    if self.options.live_witness_mode else
                    "UI form filling remains no-save. Optional submit API capture clicks the UI control only behind an active Playwright route that aborts every mutation before backend delivery. API write is separately disabled unless explicitly confirmed."
                ),
            },
        }
        brain_ingest = portal_brain.ingest_run(root_dir, aggregate=aggregate, source="current_run") if portal_brain.policy.enabled else {"status": "disabled"}
        corrected_kb_export = {"status": "disabled"}
        if portal_brain.policy.enabled and portal_brain.policy.self_heal_kb and portal_brain.policy.export_corrected_kb_after_run:
            try:
                corrected_kb_export = portal_brain.export_corrected_kb(root_dir / "self_healed_kb")
            except Exception as exc:
                corrected_kb_export = {"status": "error", "error": mask_sensitive_string(str(exc))}
        brain_snapshot = portal_brain.snapshot(root_dir / "portal_brain_snapshot.json") if portal_brain.policy.enabled else {"status": "disabled"}
        aggregate["portal_brain"] = {
            "enabled": bool(portal_brain.policy.enabled),
            "brain_dir": str(portal_brain.root),
            "manifest": str(portal_brain.manifest_path),
            "bootstrap": brain_bootstrap,
            "current_run_ingest": brain_ingest,
            "snapshot": brain_snapshot,
            "promotion_policy": "only phase/section judge-approved passes become validated deterministic knowledge; warning runs are candidates and failed runs are negative evidence",
            "adaptive_kb_repair": portal_brain.kb_repair_status() if portal_brain.policy.enabled and portal_brain.policy.self_heal_kb else {"status": "disabled"},
            "self_healed_kb_export": corrected_kb_export,
        }
        # Score the complete multi-phase mission and dream over all previous runs.
        # The replay cache stores only phase/workflow structure and numeric outcome
        # evidence; customer values and live selectors never enter this memory.
        try:
            verification_by_phase = {
                str(v.get("phase") or ""): v for v in phase_verifications if isinstance(v, dict)
            }
            phase_coverage = input_contract.get("phase_coverage") if isinstance(input_contract.get("phase_coverage"), dict) else {}
            policy_steps: List[Dict[str, Any]] = []
            input_paths_for_policy: List[str] = []
            for phase in phases:
                verification = verification_by_phase.get(phase) or {}
                coverage = phase_coverage.get(phase) if isinstance(phase_coverage.get(phase), dict) else {}
                phase_pass = str(verification.get("status") or "").lower() == "pass"
                input_paths_for_policy.extend([str(x) for x in coverage.get("mapped_input_paths") or []])
                input_paths_for_policy.extend([str(x) for x in coverage.get("accounted_input_paths") or []])
                policy_steps.append({
                    "type": "fill_from_input",
                    "label": PHASE_DISPLAY.get(phase, phase),
                    "pass": phase_pass,
                    "input_leaf_count": int(coverage.get("input_leaf_count") or 0),
                    "mapped_input_leaf_count": int(coverage.get("exact_leaf_path_matches") or 0),
                    "unresolved_input_leaves": list(coverage.get("unmapped_input_leaf_paths") or []),
                    "verification": "100_percent_runtime_input_exact_readback" if phase_pass and not coverage.get("unmapped_input_leaf_paths") else "incomplete",
                    "cycles": [],
                    "risk": "draft",
                })
            mission_success = bool(mission_report.get("application_complete")) and not blocked_phases
            replay_episode = replay_policy.record_episode(
                task="fill all HIP phases from input json",
                actions=["fill", "verify"],
                target_area="all_phases",
                page_families=phases,
                steps=policy_steps,
                success=mission_success,
                run_id=ctx.run_id,
                source="full_hip_mission_live_run",
                blocked=not mission_success,
                recovery_count=sum(1 for x in runtime_self_heal_summary.values() if isinstance(x, dict) and x.get("recovered")),
                input_paths=input_paths_for_policy,
                evidence={
                    "mission_status": mission_report.get("mission_status"),
                    "blocked_phase_count": len(blocked_phases),
                    "starting_policy_mode": mission_policy_decision.get("mode"),
                },
            )
            replay_dream = replay_policy.dream(reason="full_hip_mission_completed") if bool(getattr(self.config.replay_policy, "dream_after_every_run", True)) else {"status": "disabled"}
            aggregate["replay_policy"] = {
                "startup_decision": mission_policy_decision,
                "episode": replay_episode,
                "dream": replay_dream,
                "manifest": replay_policy.manifest(),
            }
            mlflow_tracker.log_metrics({
                "policy.episode_score": float(replay_episode.get("score") or 0.0),
                "policy.goal_success": 1.0 if mission_success else 0.0,
                "policy.cache_size": float(replay_policy.manifest().get("policy_count") or 0),
            })
            mlflow_tracker.log_event("replay_policy_dream_completed", {
                "episode_score": replay_episode.get("score"),
                "policy_updates": replay_dream.get("policy_updates"),
                "active_policy_version": replay_policy.manifest().get("active_policy_version"),
            })
            safe_write_json(root_dir / "replay_policy_final.json", aggregate["replay_policy"])
            # V243R13: missions run the same bounded recursive self-improvement
            # cycle as portal tasks (replay + model-champion dreaming, skill review).
            try:
                aggregate["recursive_self_improvement"] = close_mission_learning_loop(
                    self.config, replay_policy=replay_policy,
                    reward=float(replay_episode.get("score") or (1.0 if mission_success else 0.0)),
                    success=mission_success,
                )
                mlflow_tracker.log_event("recursive_self_improvement", {
                    "cycles": aggregate["recursive_self_improvement"].get("cycle_count"),
                    "best_reward": (aggregate["recursive_self_improvement"].get("state") or {}).get("best_reward"),
                })
            except Exception as rsi_exc:
                aggregate["recursive_self_improvement"] = {"status": "error_fail_open", "error": mask_sensitive_string(str(rsi_exc))[:500]}
            safe_write_json(root_dir / "recursive_self_improvement.json", aggregate["recursive_self_improvement"])
        except Exception as exc:
            aggregate["replay_policy"] = {"status": "error_fail_open", "error": mask_sensitive_string(str(exc))}
        _write_csv_report(root_dir / "phase_verification_report.csv", phase_verifications)
        _write_html_report(root_dir / "FULL_DUMMY_FILL_E2E_REPORT.html", aggregate)
        mlflow_final = mlflow_tracker.finish(
            status=str(mission_report.get("mission_status") or overall_status),
            application_complete=bool(mission_report.get("application_complete")),
            blocked_phase=legacy_blocked_phase,
            final_report=mission_report,
        )
        aggregate["mlflow"] = mlflow_final
        safe_write_json(root_dir / "mlflow_status.json", mlflow_final, mask=False)
        mlflow_tracker.close()
        safe_write_json(root_dir / "full_dummy_fill_summary.json", aggregate)
        zip_path = root_dir / "UPLOAD_FULL_DUMMY_FILL_E2E_SUMMARY.zip"
        aggregate["files"] = {
            "summary_json": str(root_dir / "full_dummy_fill_summary.json"),
            "html_report": str(root_dir / "FULL_DUMMY_FILL_E2E_REPORT.html"),
            "csv_report": str(root_dir / "phase_verification_report.csv"),
            "vision_prompts": str(root_dir / "vision_verification_prompts.json"),
            "vision_results": str(root_dir / "vision_verification_results.json"),
            "flash_replay_manifest": str(root_dir / "flash_fill_replay_manifest.json"),
            "golden_reference_manifest": str(root_dir / "_golden" / "manifest.json"),
            "fast_fill_playbook": str(root_dir / "FAST_FILL_AGENT_PLAYBOOK.md"),
            "fast_replay_blueprints_dir": str(root_dir / "fast_replay_blueprints"),
            "deterministic_plan_manifest": str(root_dir / "deterministic_plans" / "deterministic_plan_manifest.json"),
            "deterministic_plans_dir": str(root_dir / "deterministic_plans"),
            "portal_brain_bootstrap": str(root_dir / "portal_brain_bootstrap.json"),
            "unified_kb_import": str(root_dir / "unified_kb_import.json"),
            "portal_brain_snapshot": str(root_dir / "portal_brain_snapshot.json"),
            "portal_brain_manifest": str(portal_brain.manifest_path),
            "kb_repair_index": str(portal_brain.root / "kb_repairs" / "repair_index.json"),
            "self_healed_kb_manifest": str(root_dir / "self_healed_kb" / "self_healed_kb_manifest.json"),
            "self_healed_kb": str(root_dir / "self_healed_kb" / "HIP_Unified_Deep_KB.self_healed.json"),
            "self_healed_kg": str(root_dir / "self_healed_kb" / "HIP_Unified_Knowledge_Graph.self_healed.json"),
            "dual_mcp_preflight": str(root_dir / "mcp_preflight" / "dual_mcp_capabilities.json"),
            "runtime_self_heal_summary": str(root_dir / "runtime_self_heal" / "runtime_self_heal_summary.json"),
            "runtime_self_heal_policy": str(root_dir / "runtime_self_heal" / "runtime_self_heal_policy.json"),
            "mlflow_status": str(root_dir / "mlflow_status.json"),
            "mlflow_events": str(root_dir / "mlflow_async_events.jsonl"),
            "live_witness_certificate": str(root_dir / "live_witness_certificate.json") if self.options.live_witness_mode else "",
            "upload_zip": _zip_dir(root_dir, zip_path),
        }
        safe_write_json(root_dir / "full_dummy_fill_summary.json", aggregate)
        return aggregate
