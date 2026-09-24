from __future__ import annotations

import asyncio
import ast
import csv
import hashlib
import html
import json
import re
import time
import zipfile
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse, quote
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from playwright.async_api import Locator, Page

from .browser_session import BrowserSession, browser_session_scope
from .config import AppConfig
from .models import RunContext, utc_now
from .security import mask_sensitive_data, mask_sensitive_string
from .deployment_group_policy import resolve_transport_deployment_group
from .safe_io import safe_write_json, safe_write_csv
from .repeatable_rows import apply_repeatable_row_adds, build_repeatable_section_plan
from .portal_form_exploration import run_portal_form_exploration, merge_section_knowledge
from .upload_assets import attempt_upload_for_control
from .dds_control_driver import active_form_root_info, assert_active_surface, close_open_dropdown, get_active_form_root, restore_filled_values, set_text_control as dds_set_text_control, select_dds_combobox, select_radio_value, set_checkbox_value, semantic_runtime_enabled, open_control_for_discovery
from .phase_form_entry import ensure_phase_form_entry, find_same_page_top_right_add, same_page_add_candidate
from .llm_form_planner import LLMFormPlanner
from .deterministic_plan_runtime import ordered_keys as deterministic_ordered_keys, annotate_attempt as annotate_plan_attempt, plan_summary as deterministic_plan_summary
from .stateful_form_runtime import compile_phase_state_graph, execute_phase_state_graph, build_target_branch_knowledge, capture_stateful_controls
from .autonomous_form_runtime import execute_autonomous_phase_goal, autonomous_phase_enabled

TRANSPORT_PROFILES_URL = "https://developer.dell.com/hybrid-integrations/securelink/transportprofiles"

DEFAULT_DUMMY_TRANSPORT_PROFILE = {
    "transport_profile_name": "DUMMY_SFTP_HAFT_TP_KB",
    "transport_profile_version": "1",
    "transport_profile_operation": "create",
    "status": "Enable",
    "description": "Dummy Transport Profile KB capture only - no save/create",
    "interface_type": "SFTP",
    "transport_protocol": "SFTP-HAFT",
    "direction": "Outbound",
    "system_type": "Partner",
    "partner_name": "dce-test-partner",
    "system_name": "dce-test-partner",
    "environment": "DEV",
    "host": "dummy-sftp.example.com",
    "port": "22",
    "user_name": "DUMMY_USER",
    "folder_path": "/dummy/outbound",
    "file_pattern": "DUMMY_*.xml",
    "document_type_id": "10483",
    "document_type_name": "XML_DellAutoASN_10_U-HAUL_ANS_IB",
    "document_type_version": "1",
    "requested_by": "KB_LEARNER",
    "work_order_id": "KB-TRANSPORT-PROFILE-LEARNER",
}

DROPDOWN_SELECTORS = [
    "select",
    "[role='combobox']",
    "button[aria-haspopup='listbox']",
    "button[aria-haspopup='menu']",
    ".dds__select",
    ".dds__dropdown",
    ".dds__dropdown__button",
    ".dds__input-select",
]

FORM_CONTROL_SELECTORS = [
    "input:not([type=hidden])",
    "textarea",
    "select",
    "[role='combobox']",
    "[contenteditable='true']",
    ".dds__input input:not([type=hidden])",
    ".dds__textarea textarea",
]

SAVE_UNSAFE = {"save", "submit", "create", "update", "delete", "remove", "disable", "enable", "archive"}


@dataclass
class TransportProfileKBResult:
    run_id: str
    run_dir: str
    kb_dir: str
    status: str
    counts: Dict[str, int] = field(default_factory=dict)
    files: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("_") or "unknown"


def _read_json(path: str | Path | None) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    return json.loads(p.read_text(encoding="utf-8"))


def _scalar_text(value: Any) -> str:
    """Return a compact scalar string without leaking nested JSON into UI fields."""
    if value in (None, ""):
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(_scalar_text(v) for v in value if _scalar_text(v))
    if isinstance(value, dict):
        for key in ("value", "name", "text", "label", "rootElement", "root_element"):
            if value.get(key) not in (None, ""):
                return _scalar_text(value.get(key))
        return ""
    return str(value)


def _extract_transport_profile_identifier_parts(value: Any) -> Dict[str, str]:
    """Normalize the portal/API transportProfileIdentifier structure into KB-fillable parts.

    The live details API returns transportProfileIdentifier as either a scalar/string or a
    dict such as::

        {"attributeList": [{"derivedFrom": "ELEMENT_IN_PAYLOAD",
                            "expression": "ISA*06", "value": "ABBVIE"}],
         "operator": "ONE"}

    Earlier builds only understood a `rows` array, so `attributeList` was preserved
    only inside raw_detail_compact and the normalized KB fields stayed blank.
    """
    out = {
        "transport_profile_identifier": "",
        "transport_profile_identifier_operation": "",
        "transport_profile_identifier_derived_from": "",
    }
    if isinstance(value, dict):
        out["transport_profile_identifier_operation"] = _scalar_text(value.get("operation") or value.get("operator"))
        rows = []
        for row_key in ("rows", "attributeList", "attributes", "identifierRows", "conditions"):
            if isinstance(value.get(row_key), list):
                rows = value.get(row_key) or []
                break
        if rows and isinstance(rows[0], dict):
            first = rows[0]
            out["transport_profile_identifier_derived_from"] = _scalar_text(
                first.get("derived_from") or first.get("derivedFrom") or first.get("source")
            )
            out["transport_profile_identifier"] = _scalar_text(
                first.get("value")
                or first.get("rootElement")
                or first.get("root_element")
                or first.get("identifier")
                or first.get("transportProfileIdentifier")
            )
        if not out["transport_profile_identifier"]:
            out["transport_profile_identifier"] = _scalar_text(value.get("value") or value.get("rootElement") or value.get("root_element"))
    else:
        out["transport_profile_identifier"] = _scalar_text(value)
    return out


def _first_attr_value(attrs: Any, key: str, default: str = "") -> str:
    if isinstance(attrs, list) and attrs:
        first = attrs[0] if isinstance(attrs[0], dict) else {}
        aliases = {
            "attribute_name": ["attribute_name", "attributeName", "name"],
            "attribute_derived_from": ["derived_from", "derivedFrom", "attribute_derived_from"],
            "attribute_usage": ["usage", "usages", "attribute_usage"],
            "attribute_expression": ["expression", "xpath", "value", "attribute_expression"],
        }.get(key, [key])
        for alias in aliases:
            if first.get(alias) not in (None, ""):
                return _scalar_text(first.get(alias))
    return default


def _first_environment(value: Any, default: str = "DEV") -> str:
    if value in (None, ""):
        return default
    if isinstance(value, list):
        return _scalar_text(value[0]).strip() or default
    text = str(value).strip()
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list) and parsed:
            return _scalar_text(parsed[0]).strip() or default
    except Exception:
        pass
    for sep in [",", "|", ";"]:
        if sep in text:
            text = text.split(sep)[0].strip()
            break
    return text.strip("[]'\" ") or default


def extract_transport_profile_seed(input_data: Dict[str, Any], phase: str | None = None) -> Dict[str, Any]:
    """Extract only the phase-specific Transport Profile seed.

    Previous builds merged target, source and generic transport_profile in one pass,
    so Source TP could inherit target partner/account values.  The full dummy E2E
    runner sets `_full_dummy_fill_phase`; use that to isolate the object that the
    current wizard is filling.
    """
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    phase_hint = (phase or input_data.get("_full_dummy_fill_phase") or "").strip().lower() if isinstance(input_data, dict) else ""
    if phase_hint == "source_transport_profile":
        candidate_keys = ["source_transport_profile"]
    elif phase_hint == "target_transport_profile":
        candidate_keys = ["target_transport_profile"]
    else:
        candidate_keys = ["transport_profile", "source_transport_profile", "target_transport_profile"]

    candidates: List[Dict[str, Any]] = []
    for key in candidate_keys:
        val = (objects or {}).get(key) if isinstance(objects, dict) else None
        if val is None and isinstance(input_data, dict):
            val = input_data.get(key)
        if isinstance(val, dict):
            candidates.append(val)
            if phase_hint in {"source_transport_profile", "target_transport_profile"}:
                break

    result = dict(DEFAULT_DUMMY_TRANSPORT_PROFILE)
    if phase_hint == "source_transport_profile":
        result.update({
            "system_type": "Dell Application",
            "partner_name": "AIC - DCE",
            "system_name": "AIC - DCE",
            "profile_usage": "Sender",
            "usage": "Sender",
            "deployment_group": "da-sender-sftphaft-dce-shared",
            "interface_type": "SFTP HAFT",
            "transport_protocol": "SFTP HAFT",
            "environment": "UAT",
            "existing_account": "Yes",
            "existing_account_name": "haftatap10251108",
            "use_existing_folder": "No",
            "folder_path": "/SFTP_U-HAUL_ASN_PC_SRC_IB",
            "file_pattern": ".*\\*.",
            "document_type_name": "XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)",
        })
    elif phase_hint == "target_transport_profile":
        result.update({
            "system_type": "Partner",
            "partner_name": "dce-test-partner",
            "system_name": "dce-test-partner",
            "profile_usage": "Receiver",
            "usage": "Receiver",
            "deployment_group": "pt-receiver-sftphaft-dce-shared",
            "interface_type": "SFTP HAFT",
            "transport_protocol": "SFTP HAFT",
            "environment": "UAT",
            "existing_account": "Yes",
            "existing_account_name": "haftattp10251114",
            "use_existing_folder": "No",
            "folder_path": "/Inbound/ASN",
            "file_pattern": ".*\\*.",
            "document_type_name": "XML_SHIPMENT_NOTICE_10_U-HAUL_ANS_OB(1.0)",
        })

    for dt in candidates:
        for src_key, dst_key in [
            ("profile_name", "transport_profile_name"),
            ("profile_usage", "profile_usage"),
            ("profile_usage", "usage"),
            ("system_type", "system_type"),
            ("partner_name", "partner_name"),
            ("application_name", "system_name"),
            ("system_name", "system_name"),
            ("deployment_group", "deployment_group"),
            ("interface_type", "interface_type"),
            ("interface_type", "transport_protocol"),
            ("interface_environment", "environment"),
            ("existing_account", "existing_account"),
            ("existing_account_name", "existing_account_name"),
            ("use_existing_folder", "use_existing_folder"),
            ("post_transfer_action", "post_transfer_action"),
            ("is_compression_required", "splitter_required"),
            ("document_type", "document_type_name"),
            ("subscription_folder", "folder_path"),
            ("file_filtering_pattern", "file_pattern"),
        ]:
            if dt.get(src_key) not in (None, ""):
                result[dst_key] = dt.get(src_key)
        for src_key, dst_key in [
            ("name", "transport_profile_name"),
            ("transport_profileName", "transport_profile_name"),
            ("transport_profile_name", "transport_profile_name"),
            ("version", "transport_profile_version"),
            ("transport_profileVersion", "transport_profile_version"),
            ("transport_profile_version", "transport_profile_version"),
            ("status", "status"),
            ("transaction_type", "transaction_type"),
            ("transactionType", "transaction_type"),
            ("tx_code", "transaction_type"),
            ("data_format_type", "format"),
            ("dataFormatType", "format"),
            ("format", "format"),
            ("description", "description"),
            ("validation_type", "validation_type"),
            ("validationType", "validation_type"),
            ("rootElement", "root_element"),
            ("root_element", "root_element"),
            ("schema_file", "schema_file"),
            ("schemaFile", "schema_file"),
            ("usage", "usage"),
        ]:
            if dt.get(src_key) not in (None, ""):
                result[dst_key] = dt.get(src_key)
                if dst_key == "format":
                    result["data_format_type"] = dt.get(src_key)
        doc_identifier_value = dt.get("transportProfileIdentifier") if dt.get("transportProfileIdentifier") not in (None, "") else dt.get("transport_profile_identifier")
        if doc_identifier_value not in (None, ""):
            result["transport_profile_identifier_payload"] = doc_identifier_value
            result.update({k: v for k, v in _extract_transport_profile_identifier_parts(doc_identifier_value).items() if v})
        if dt.get("operation") not in (None, ""):
            result["transport_profile_identifier_operation"] = dt.get("operation")
        attrs = dt.get("attributes_to_configure") or dt.get("attributes") or dt.get("transport_profileAttributes")
        if isinstance(attrs, list) and attrs:
            result["attributes_to_configure"] = attrs
            result["attribute_name"] = _first_attr_value(attrs, "attribute_name", result.get("attribute_name", ""))
            result["attribute_derived_from"] = _first_attr_value(attrs, "attribute_derived_from", result.get("attribute_derived_from", ""))
            result["attribute_usage"] = _first_attr_value(attrs, "attribute_usage", result.get("attribute_usage", ""))
            result["attribute_expression"] = _first_attr_value(attrs, "attribute_expression", result.get("attribute_expression", ""))
    result["deployment_group"] = resolve_transport_deployment_group(
        interface_type=result.get("interface_type") or result.get("transport_protocol"),
        profile_usage=result.get("profile_usage") or result.get("usage"),
        current_value=result.get("deployment_group"),
    )
    return result

def build_previous_interaction_values(input_data: Dict[str, Any], *, known_transport_profile_id: str | None = None) -> Dict[str, Any]:
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    dm = extract_transport_profile_seed(input_data, phase=input_data.get("_full_dummy_fill_phase") if isinstance(input_data, dict) else None)
    source_dt = (objects or {}).get("source_transport_profile") or {}
    target_dt = (objects or {}).get("target_transport_profile") or {}
    transport_profile = (objects or {}).get("transport_profile") or {}
    return {
        "source": "uploaded_input_json_and_prior_manual_context",
        "captured_at": utc_now(),
        "known_ids": {
            "transport_profile_id": known_transport_profile_id or "UNKNOWN_FROM_CURRENT_RULE_KB_RUN",
            "target_transport_profile_id": "10483",  # from prior API phase note; confirm before final create if DEV differs
        },
        "transport_profile_values_to_fill": dm,
        "related_values": {
            "source_transport_profile_name": source_dt.get("name"),
            "source_transport_profile_version": source_dt.get("version"),
            "target_transport_profile_name": target_dt.get("name"),
            "target_transport_profile_version": target_dt.get("version"),
            "transport_profile_name": transport_profile.get("name"),
            "transport_profile_version": transport_profile.get("version"),
            "mapping_identifier_name_version": ((transport_profile.get("actions") or {}) if isinstance(transport_profile, dict) else {}).get("mapping_identifier_name_version"),
        },
    }


def build_dummy_fill_values(seed: Dict[str, Any], *, exact: bool = False) -> Dict[str, str]:
    """Use real-shaped but disposable scalar values for Transport Profiles.

    The flow may fill the + Add form to learn control bindings, but it never clicks
    Save/Create/Submit.  Names are prefixed with DUMMY to prevent accidental reuse.
    """
    values = dict(DEFAULT_DUMMY_TRANSPORT_PROFILE)
    for key, value in (seed or {}).items():
        if value not in (None, "") and isinstance(value, (str, int, float, bool)):
            values[key] = _scalar_text(value)
    if not exact:
        if not values.get("transport_profile_name", "").upper().startswith("DUMMY"):
            values["transport_profile_name"] = f"DUMMY_{_safe_name(values['transport_profile_name'])}_KB"
    values.setdefault("profile_usage", values.get("usage") or "Sender")
    values.setdefault("usage", values.get("profile_usage") or "Sender")
    values.setdefault("deployment_group", "da-sender-sftphaft-dce-shared")
    values.setdefault("existing_account", "No")
    values.setdefault("post_transfer_action", "None")
    values.setdefault("splitter_required", "No")
    values.setdefault("process_step_type", "Receive")
    values.setdefault("condition_attribute", "Receiver")
    values.setdefault("condition_operator", "Equals")
    values.setdefault("condition_value", "DUMMY_RECEIVER")
    values.setdefault("action_type", "Mapping Transformer")
    values.setdefault("mapping_identifier", "DUMMY_MAP_IDENTIFIER_KB")
    return {k: str(v) for k, v in values.items() if isinstance(v, (str, int, float, bool))}

def guess_field_key(label: str, attrs: Dict[str, Any]) -> Optional[str]:
    text = " ".join(str(x or "") for x in [label, attrs.get("name"), attrs.get("id"), attrs.get("placeholder"), attrs.get("ariaLabel")]).lower()
    text = re.sub(r"\s+", " ", text).strip()
    # Avoid mapping table/search/filter controls as Transport Profile data fields.
    if any(skip in text for skip in ["table search", "filter by column", "search"]):
        return None
    exact_label = str(label or "").strip().lower()
    exact_compact_label = re.sub(r"[^a-z0-9]+", "", exact_label)
    exact_map = {
        "name": "transport_profile_name",
        "transport_profile name": "transport_profile_name",
        "doc type name": "transport_profile_name",
        "version": "transport_profile_version",
        "transport_profile version": "transport_profile_version",
        "transaction type": "transaction_type",
        "data format type": "format",
        "format": "format",
        "description": "description",
        "validation type": "validation_type",
        "operation": "transport_profile_identifier_operation",
        "transport_profile identifier": "transport_profile_identifier",
        "root element": "root_element",
        "attribute name": "attribute_name",
        "derived from": "derived_from",
        "usage": "attribute_usage",
        "expression": "attribute_expression",
        "transport_profile schema file": "schema_file",
        "schema file": "schema_file",
        "transportprofilename": "transport_profile_name",
        "profile name": "transport_profile_name",
        "transport profile name": "transport_profile_name",
        "system type": "system_type",
        "partner name": "partner_name",
        "application name": "system_name",
        "system name": "system_name",
        "profile usage": "profile_usage",
        "deployment group": "deployment_group",
        "interface type": "interface_type",
        "interface environment": "environment",
        "available environments": "environment",
        "available environment": "environment",
        "existing account name": "existing_account_name",
        "existing account": "existing_account",
        "use existing folder": "use_existing_folder",
        "subscription folder": "folder_path",
        "document type supported": "document_type_name",
        "document type": "document_type_name",
        "primary domain": "primary_domain",
        "host": "host",
        "port": "port",
        "folder path": "folder_path",
        "file pattern": "file_pattern",
        "file filtering pattern": "file_pattern",
        "post transfer action": "post_transfer_action",
        "splitter required": "splitter_required",
        "step type": "process_step_type",
        "user name": "user_name",
        "username": "user_name",
    }
    if exact_label in exact_map:
        return exact_map[exact_label]
    if exact_compact_label in exact_map:
        return exact_map[exact_compact_label]
    # Important: do not let substring checks map `transportProfileName` to `port`
    # just because the camelCase word contains "port".  From here down, use
    # field-specific phrases before narrow words such as port/file.
    patterns = [
        ("transport_profile_name", ["transport profile name", "profile name", "transportprofilename", "transport_profile name", "doc type name"]),
        ("system_type", ["system type"]),
        ("partner_name", ["partner name"]),
        ("system_name", ["system name", "application name"]),
        ("profile_usage", ["profile usage"]),
        ("deployment_group", ["deployment group"]),
        ("interface_type", ["interface type"]),
        ("environment", ["interface environment", "available environment", "environment"]),
        ("existing_account_name", ["existing account name"]),
        ("existing_account", ["existing account"]),
        ("use_existing_folder", ["use existing folder"]),
        ("folder_path", ["subscription folder"]),
        ("document_type_name", ["document type supported", "document type"]),
        ("host", [" host ", "hostname", "server host"]),
        ("port", [" port ", "remote port", "sftp port"]),
        ("folder_path", ["folder path", "directory path", "remote directory", "folder", "directory"]),
        ("file_pattern", ["file filtering pattern", "file pattern", "filename", "file name"]),
        ("post_transfer_action", ["post transfer action"]),
        ("splitter_required", ["splitter required"]),
        ("process_step_type", ["step type"]),
        ("user_name", ["user name", "username", "user id"]),
        ("attribute_name", ["attribute name"]),
        ("transport_profile_version", ["transport_profile version", "transport_profile version", " version"]),
        ("transaction_type", ["transaction type", "transaction"]),
        ("format", ["data format", "format", "edi", "xml", "x12"]),
        ("validation_type", ["validation type", "validation"]),
        ("description", ["description"]),
        ("transport_profile_identifier_operation", ["operation"]),
        ("transport_profile_identifier", ["transport_profile identifier", "identifier"]),
        ("root_element", ["root element", "root tag", "root"]),
        ("derived_from", ["derived from", "derivedfrom"]),
        ("attribute_usage", ["usage", "source", "target"]),
        ("attribute_expression", ["expression", "xpath", "path"]),
        ("status", ["status"]),
        ("schema_file", ["schema", "xsd", "file", "upload"]),
    ]
    padded_text = f" {text} "
    for key, terms in patterns:
        if any(t in padded_text for t in terms):
            return key
    return None


async def _evaluate_controls(page: Page) -> List[Dict[str, Any]]:
    js = r"""
() => {
  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    return !!(r.width && r.height && st.visibility !== 'hidden' && st.display !== 'none');
  }
  function isNoiseControl(el, label) {
    const lower = (label || '').toLowerCase();
    if (el.closest('#onetrust-consent-sdk, .otPcCenter, .onetrust-pc-dark-filter, app-footer, footer, header, nav')) return true;
    if (el.closest('dds-pagination, .dds__pagination, .dds__table__search, dds-table-ribbon')) return true;
    if (el.closest('app-audit, #confirmationModal, app-modalpopup')) return true;
    if (/^(search|table search|filter by column name\.?|items per page|page|provide comment for this action|marketing|statistical|uncategorized|checkbox label|cookie list search)$/i.test(label || '')) return true;
    if (/(pagination|vendor-search|ot-group|select-all|auditmessage)/i.test((el.id || '') + ' ' + (el.name || ''))) return true;
    return false;
  }
  function findTransportProfileTypeFormRoot() {
    const candidates = Array.from(document.querySelectorAll('app-generic-drawer, .dds__drawer, [role=dialog], form, .dds__container, .dds__card, app-transport_profiles, main, body'))
      .filter(isVisible)
      .map(el => {
        const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        let score = 0;
        const controlCount = Array.from(el.querySelectorAll("input:not([type=hidden]), textarea, select, [role='combobox'], input[type=radio], input[type=checkbox]")).filter(isVisible).length;
        for (const term of ['Create Transport Profile', 'Transport Profile Details', 'Basic Details', 'Interface Details', 'Document(s) Supported', 'Document Type', 'Profile Name', 'Profile Usage', 'Deployment Group', 'System Type', 'Partner Name', 'System Name', 'Existing Account', 'File Filtering Pattern', 'Post Transfer Action']) {
          if (txt.toLowerCase().includes(term.toLowerCase())) score += 3;
        }
        score += Math.min(controlCount, 24);
        if (/Create\s+Transport\s+Profile/i.test(txt)) score += 20;
        if (/Transport\s+Profile\s+Details/i.test(txt)) score += 15;
        if (/Document\(s\)\s+Supported|Document Type/i.test(txt)) score += 10;
        if (/Name\s+Usage\s+Interface Type\s+System Name/i.test(txt) && !/Create\s+Transport\s+Profile/i.test(txt)) score -= 50;
        if (/Items per page|Filter by column|Table search/i.test(txt) && !/Transport\s+Profile\s+Details/i.test(txt)) score -= 25;
        const r = el.getBoundingClientRect();
        const area = Math.max(1, r.width * r.height);
        return {el, score, area, controlCount, text: txt.slice(0,400)};
      })
      .filter(x => x.score >= 10)
      // Prefer the full Create TP form/wizard over a tiny fieldset.  Tiny fieldsets
      // caused only System Type/System Name to be captured even though the full
      // page already contained Existing Account and Document(s) Supported.
      .sort((a, b) => (b.score - a.score) || (b.controlCount - a.controlCount) || (b.area - a.area));
    return candidates.length ? candidates[0].el : document;
  }
  const root = findTransportProfileTypeFormRoot();
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 7) {
      let part = el.tagName.toLowerCase();
      if (el.id) { part += '#' + CSS.escape(el.id); parts.unshift(part); break; }
      const cls = (el.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0, 3).map(c => CSS.escape(c)).join('.');
      if (cls) part += '.' + cls;
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(x => x.tagName === el.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(el) + 1) + ')';
      }
      parts.unshift(part);
      el = parent;
    }
    return parts.join(' > ');
  }
  function labelFor(el) {
    const id = el.id;
    let label = '';
    if (id) {
      const l = document.querySelector('label[for="' + CSS.escape(id) + '"]');
      if (l) label = l.innerText || l.textContent || '';
    }
    if (!label) {
      const wrap = el.closest('label');
      if (wrap) label = wrap.innerText || wrap.textContent || '';
    }
    if (!label) {
      const group = el.closest('.dds__form__field, .dds__form-field, .dds__input, .dds__textarea, .dds__select, .dds__dropdown, .form-group, .field, div');
      if (group) {
        const lab = group.querySelector('label, .dds__label, .dds__form__label, .label');
        if (lab) label = lab.innerText || lab.textContent || '';
      }
    }
    if (!label) {
      const box = el.getBoundingClientRect ? el.getBoundingClientRect() : {x:0,y:0,width:0,height:0,top:0,left:0};
      const textEls = Array.from(root.querySelectorAll('label, legend, span, div, p'))
        .filter(x => isVisible(x))
        .map(x => ({el:x, txt:(x.innerText||x.textContent||'').trim().replace(/\\s+/g,' '), r:x.getBoundingClientRect()}))
        .filter(x => x.txt && x.txt.length <= 80 && !/^(yes|no|select|add|cancel|create|items per page|page)$/i.test(x.txt));
      const nearby = textEls
        .map(x => ({...x, dist: Math.abs((x.r.bottom||0) - (box.top||0)) + Math.abs((x.r.left||0) - (box.left||0))/4}))
        .filter(x => x.r.bottom <= box.top + 14 && x.r.bottom >= box.top - 90 && Math.abs((x.r.left||0) - (box.left||0)) < 340)
        .sort((a,b)=>a.dist-b.dist)[0];
      if (nearby) label = nearby.txt;
    }
    return (label || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().replace(/\\s+/g, ' ');
  }
  const els = Array.from(root.querySelectorAll("input:not([type=hidden]), textarea, select, [role='combobox'], input[type=radio], input[type=checkbox], [contenteditable='true']"))
    .filter(el => isVisible(el))
    .map((el, idx) => ({el, label: labelFor(el), idx}))
    .filter(x => !isNoiseControl(x.el, x.label));
  return els.map((item, idx) => {
    const el = item.el;
    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {x:0,y:0,width:0,height:0};
    const opts = el.tagName.toLowerCase() === 'select' ? Array.from(el.options || []).map(o => ({text:o.text, value:o.value, selected:o.selected})) : [];
    return {
      index: idx,
      tag: (el.tagName || '').toLowerCase(),
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      label: labelFor(el),
      id: el.id || '',
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\*/.test(labelFor(el))),
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      readonly: !!el.readOnly,
      value: el.value || el.getAttribute('value') || '',
      selector: cssPath(el),
      options: opts,
      visible: !!(r.width && r.height),
      boundingBox: {x:r.x, y:r.y, width:r.width, height:r.height},
      dom_events_to_try: ['input', 'change', 'blur']
    };
  });
}
"""
    try:
        data = await page.evaluate(js)
        return mask_sensitive_data(data or [])
    except Exception:
        return []


async def _evaluate_buttons(page: Page) -> List[Dict[str, Any]]:
    js = r"""
() => {
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 7) {
      let part = el.tagName.toLowerCase();
      if (el.id) { part += '#' + CSS.escape(el.id); parts.unshift(part); break; }
      const cls = (el.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c => CSS.escape(c)).join('.');
      if (cls) part += '.' + cls;
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(x => x.tagName === el.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(el) + 1) + ')';
      }
      parts.unshift(part); el = parent;
    }
    return parts.join(' > ');
  }
  return Array.from(document.querySelectorAll('button, a[role=button], [role=button], input[type=button], input[type=submit]')).map((el, idx) => {
    const txt = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ');
    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {x:0,y:0,width:0,height:0};
    return {index:idx, text:txt, tag:(el.tagName||'').toLowerCase(), id:el.id||'', classes:(el.className||'').toString(), selector:cssPath(el), disabled:!!el.disabled || el.getAttribute('aria-disabled')==='true', visible:!!(r.width&&r.height), boundingBox:{x:r.x,y:r.y,width:r.width,height:r.height}};
  });
}
"""
    try:
        data = await page.evaluate(js)
    except Exception:
        data = []
    buttons = []
    for b in data or []:
        txt = str(b.get("text") or "").lower()
        b["unsafe_for_kb_run"] = any(w in txt for w in SAVE_UNSAFE)
        buttons.append(mask_sensitive_data(b))
    return buttons


async def _find_add_button(page: Page) -> Optional[Locator]:
    """Find the Transport Profiles page-level top-right + Add in-place."""
    shared = await find_same_page_top_right_add(page)
    if shared is not None:
        return shared
    candidates = [
        "main button:has-text('+ Add')",
        "main [role=button]:has-text('+ Add')",
        "main dds-button:has-text('+ Add')",
        "main a:has-text('+ Add')",
        "button:has-text('+ Add')",
        "button[aria-label*='Add' i]",
        "[role=button]:has-text('Add')",
        "a:has-text('Add')",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=1500) and await loc.is_enabled(timeout=1500):
                if await same_page_add_candidate(page, loc):
                    return loc
        except Exception:
            continue
    return None


async def _disable_stale_loading_overlays(page: Page) -> int:
    """Disable pointer-events on stale Dell loading overlays as a last-resort UI-only recovery.

    This is used only for the read-only KB learning Add-form open step. It does not
    click Save/Create/Submit and it does not call any mutation API.
    """
    try:
        return int(await page.evaluate(
            """
() => {
  const selectors = [
    'app-loadingindicator .dds__loading-indicator__overlay',
    '.dds__loading-indicator__overlay',
    '[class*="loading-indicator"]'
  ];
  let changed = 0;
  for (const el of Array.from(document.querySelectorAll(selectors.join(',')))) {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const ariaHidden = (el.getAttribute('aria-hidden') || '').toLowerCase();
    const visible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') !== 0;
    if (visible && ariaHidden !== 'true' && style.pointerEvents !== 'none') {
      el.setAttribute('data-hip-kb-pointer-events-disabled', 'true');
      el.style.pointerEvents = 'none';
      changed += 1;
    }
  }
  return changed;
}
            """
        ))
    except Exception:
        return 0




async def _force_hide_stale_transport_profile_drawers(page: Page) -> int:
    """Last-resort read-only cleanup for stale detail drawers blocking + Add.

    Called only before opening Add. It hides existing detail/menu/modal surfaces that
    do not look like the Add Transport Profile form. This mirrors a user closing a
    stuck drawer and avoids Playwright pointer interception by `app-generic-drawer`.
    """
    try:
        return int(await page.evaluate(r"""
() => {
  function visible(el) {
    const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : {width:0,height:0};
    const st = el ? window.getComputedStyle(el) : null;
    return !!(r.width && r.height && st && st.display !== 'none' && st.visibility !== 'hidden');
  }
  const sels = ['app-generic-drawer', '.dds__drawer', '[role=dialog]', '.dds__modal', '.dds__popover', '.dds__menu', '[role=menu]'];
  let changed = 0;
  for (const el of Array.from(document.querySelectorAll(sels.join(',')))) {
    if (!visible(el)) continue;
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ');
    if (/add\s+transport\s+profile|transport\s+profile\s+name.*interface\s+type/i.test(text)) continue;
    el.setAttribute('data-hip-kb-stale-surface-hidden', 'true');
    el.style.pointerEvents = 'none';
    el.style.visibility = 'hidden';
    el.style.display = 'none';
    changed += 1;
  }
  return changed;
}
"""))
    except Exception:
        return 0

async def _close_transport_profile_transient_surfaces(page: Page) -> int:
    """Close drawers/menus from row-action learning before clicking + Add.

    Transport Profiles rows can leave an app-generic-drawer open; Playwright then sees the Add
    button but the drawer header/body intercepts the pointer. This helper only
    clicks safe close/cancel/X controls and never Save/Create/Submit.
    """
    closed = 0
    try:
        await close_open_dropdown(page, "transport_profile" if "transport" in __name__ else "biz_flow")
        await page.wait_for_timeout(250)
        await close_open_dropdown(page, "transport_profile" if "transport" in __name__ else "biz_flow")
        await page.wait_for_timeout(250)
    except Exception:
        pass
    try:
        closed = int(await page.evaluate(r"""
() => {
  function isVisible(el) {
    const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : {width:0,height:0};
    const st = el ? window.getComputedStyle(el) : null;
    return !!(r.width && r.height && st && st.display !== 'none' && st.visibility !== 'hidden');
  }
  const surfaceSel = '.dds__drawer, app-generic-drawer, .dds__modal, [role=dialog], .dds__popover, .dds__menu, [role=menu]';
  const closeRx = /^(close|cancel|dismiss|back|×|x)$/i;
  const unsafeRx = /(save|create|submit|delete|remove|disable|enable|archive)/i;
  let count = 0;
  for (const surface of Array.from(document.querySelectorAll(surfaceSel))) {
    if (!isVisible(surface)) continue;
    const buttons = Array.from(surface.querySelectorAll('button, a, [role=button], .dds__drawer__close, [class*=close], [aria-label*=Close], [title*=Close]'));
    const close = buttons.find(el => {
      const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
      const cls = (el.className || '').toString();
      return isVisible(el) && !unsafeRx.test(text + ' ' + cls) && (closeRx.test(text) || /close|cancel|dismiss|drawer__close|modal__close|dds__icon--close/i.test(text + ' ' + cls));
    });
    if (close) {
      close.click();
      count += 1;
    }
  }
  return count;
}
"""))
        await page.wait_for_timeout(600)
    except Exception:
        pass
    return closed


async def _looks_like_transport_profile_add_form(page: Page) -> bool:
    """Return true when the visible Create Transport Profile drawer/form is present.

    The HIP TP wizard sometimes keeps the header as only `Back` after the
    Interface Type step, while the real Create TP controls remain visible.
    Earlier builds required the exact `Create Transport Profile` text; that
    caused dummy-fill to stop before the first field and record zero attempts.
    Accept a visible drawer/form when it contains multiple TP-specific controls
    such as transportProfileName, Profile Usage, Deployment Group, Interface
    Type, File Filtering Pattern, or Post Transfer Action.  The listing grid is
    excluded because it has no editable TP create controls.
    """
    try:
        return bool(await page.evaluate(
            r"""
() => {
  function visible(el) {
    const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : {width:0,height:0};
    const st = el ? window.getComputedStyle(el) : null;
    return !!(r.width && r.height && st && st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || '1') !== 0);
  }
  const surfaces = Array.from(document.querySelectorAll('app-generic-drawer, .dds__drawer, [role=dialog], form, main'))
    .filter(visible);
  for (const s of surfaces) {
    const text = (s.innerText || s.textContent || '').replace(/\s+/g, ' ');
    const html = s.innerHTML || '';
    const controls = Array.from(s.querySelectorAll('input:not([type=hidden]), textarea, select, [role=combobox]')).filter(visible);
    if (!controls.length) continue;
    const createHeader = /Create\s+Transport\s+Profile/i.test(text) || /create-transport-profile|transport-profile-create/i.test(html);
    const hasTransportProfileName = /transportProfileName|Transport\s*Profile\s*Name|Profile\s*Name/i.test(text + ' ' + html);
    const tpFieldScore = [
      /System\s+Type/i,
      /Partner\s+Name/i,
      /transportProfileName|Transport\s*Profile\s*Name|Profile\s*Name/i,
      /Profile\s+Usage/i,
      /Deployment\s+Group/i,
      /Interface\s+Type/i,
      /Interface\s+Environment/i,
      /File\s+Filtering\s+Pattern/i,
      /Post\s+Transfer\s+Action/i,
      /Document\(s\)\s+Supported|Document\s+Type/i,
      /Splitter\s+Required|Process\s+Steps|Step\s+Type/i
    ].reduce((n, rx) => n + (rx.test(text) || rx.test(html) ? 1 : 0), 0) + (hasTransportProfileName ? 1 : 0);
    const looksLikeGridOnly = /Transport Profiles.*Name\s+Usage\s+Interface Type\s+System Name\s+System Type/i.test(text) && !/Profile\s+Name|transportProfileName|Deployment\s+Group|File\s+Filtering\s+Pattern/i.test(text + ' ' + html);
    if (!looksLikeGridOnly && (createHeader || tpFieldScore >= 3)) return true;
  }
  return false;
}
            """
        ))
    except Exception:
        return False


def _controls_look_like_transport_profile_add_form_controls(rows: List[Dict[str, Any]]) -> bool:
    """Offline/fallback check for TP create controls when text detector is too strict."""
    score = 0
    text = "\n".join(" ".join(str(r.get(k) or "") for k in ["label", "name", "id", "placeholder", "ariaLabel"]) for r in rows).lower()
    for needle in [
        "transportprofilename",
        "profile usage",
        "deployment group",
        "interface type",
        "interface environment",
        "file filtering pattern",
        "post transfer action",
        "partner name",
    ]:
        if needle in text:
            score += 1
    return score >= 3


async def _select_transport_profile_combobox_option(page: Page, selector: str, option_text: str) -> bool:
    # Primary path: semantic proof -> AutoWebGLM -> Playwright MCP -> exact effect verification.
    # Under Layer-11 the result is fail-closed: a semantic/DDS failure must never
    # fall through into the legacy raw pointer/keyboard routine below.
    try:
        root = await get_active_form_root(page, "transport_profile")
        if await select_dds_combobox(page, root, selector, str(option_text), phase="transport_profile"):
            return True
    except Exception:
        if semantic_runtime_enabled(page):
            return False
    if semantic_runtime_enabled(page):
        return False
    """Select an option from a Dell DDS/Angular combobox without saving the form.

    Use a real Playwright click on the visible DDS option.  A previous hardening
    used HTMLElement.click() from page.evaluate; on this TP wizard that left the
    System Type dropdown open with aria-selected=false, so the wizard stayed on
    the shallow first step.  This helper clicks the popup option with browser
    pointer events, verifies the value or dropdown close when possible; do not fall back to directly setting arbitrary combobox text. It only
    falls back to typed text for non-DDs autocomplete controls.
    """
    if not selector or not option_text:
        return False
    wanted = str(option_text).strip()
    if not wanted:
        return False

    async def _safe_option_click(opt: Locator) -> bool:
        try:
            if not await opt.count():
                return False
            if not await opt.is_visible(timeout=1200):
                return False
            unsafe_context = await opt.evaluate(r"""
(el) => !!(el.closest('dds-pagination, .dds__pagination, dds-table-ribbon, nav, header, footer, app-footer'))
""")
            if unsafe_context:
                return False
            # Use real pointer events; plain DOM click does not update this DDS
            # dropdown reliably in the Transport Profile create wizard.
            await opt.click(timeout=3000, force=True)
            await page.wait_for_timeout(1200)
            return True
        except Exception:
            return False

    async def _selection_state() -> Dict[str, Any]:
        try:
            return await page.evaluate(r"""
({selector, wanted}) => {
  const w = String(wanted || '').trim().toLowerCase();
  let el = null;
  try { el = document.querySelector(selector); } catch(e) { el = null; }
  if (!el) return {detached: true, selected: true, value: '', expanded: false};
  const value = String(el.value || el.getAttribute('value') || '').trim();
  const wrapper = el.closest('.dds__dropdown') || el.closest('[aria-haspopup=listbox]') || el.parentElement;
  const expanded = (el.getAttribute('aria-expanded') === 'true') || !!(wrapper && wrapper.querySelector('.dds__dropdown__popup--visible, [aria-expanded="true"]'));
  const selectedOptions = Array.from(document.querySelectorAll('[role=option][aria-selected=true], [role=option][data-selected=true]'))
    .map(o => String(o.innerText || o.textContent || '').trim().toLowerCase());
  const optionSelected = selectedOptions.some(t => t === w || t.includes(w) || w.includes(t));
  const valueMatch = value && (value.toLowerCase() === w || value.toLowerCase().includes(w) || w.includes(value.toLowerCase()));
  // Do not treat a closed DDS combobox with any existing value as selected.
  // Transport Profile fields like Interface Environment can default to UAT;
  // that must not satisfy a requested DEV selection.  Detached nodes still
  // count as handled by the caller because DDS often re-renders after a real
  // option click.
  return {detached: false, selected: !!(valueMatch || optionSelected), value, expanded};
}
""", {"selector": selector, "wanted": wanted})
        except Exception:
            return {"detached": True, "selected": True, "value": "", "expanded": False}

    try:
        loc = page.locator(selector).first
        if not (await loc.count()) or not (await loc.is_visible(timeout=1000)) or not (await loc.is_enabled(timeout=1000)):
            return False
        current = str(await loc.input_value(timeout=1000) or "").strip()
        if current and (current.lower() == wanted.lower() or wanted.lower() in current.lower() or current.lower() in wanted.lower()):
            return True

        # Multiple attempts are needed because the DDS popup can open one tick
        # after the first click, and selecting System Type re-renders the drawer.
        quoted = json.dumps(wanted)
        option_selectors = [
            f"button[role=option]:has-text({quoted})",
            f"[role=option]:has-text({quoted})",
            f".dds__dropdown__item-option:has-text({quoted})",
            f".dds__dropdown__item:has-text({quoted})",
            f".dds__list-item:has-text({quoted})",
            f"li:has-text({quoted})",
            # Final broad option only; safe_context excludes pagination/nav/footer.
            f"button:has-text({quoted})",
        ]
        for attempt in range(3):
            try:
                loc = page.locator(selector).first
                if await loc.count() and await loc.is_visible(timeout=1000):
                    await loc.click(timeout=3000)
                    await page.wait_for_timeout(500 + attempt * 250)
            except Exception:
                pass
            for opt_sel in option_selectors:
                try:
                    opts = page.locator(opt_sel)
                    count = min(await opts.count(), 20)
                    for idx in range(count):
                        if await _safe_option_click(opts.nth(idx)):
                            state = await _selection_state()
                            if state.get("selected") or state.get("detached") or not state.get("expanded"):
                                return True
                except Exception:
                    continue
            # Keyboard fallback for open DDS listboxes.  This still uses the real
            # active popup and is safer than arbitrary DOM value injection.
            try:
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(150)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(900)
                state = await _selection_state()
                if state.get("selected") or state.get("detached") or not state.get("expanded"):
                    return True
            except Exception:
                pass

        # Autocomplete-style Partner/Document fields sometimes require typed text
        # followed by Enter.  Keep this as a last resort and verify that the value
        # is accepted instead of blindly reporting success.
        try:
            loc = page.locator(selector).first
            if await loc.count() and await loc.is_visible(timeout=1000):
                await loc.fill(wanted, timeout=2000)
                await page.wait_for_timeout(250)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(1200)
                state = await _selection_state()
                current2 = str(await loc.input_value(timeout=1000) or "").strip() if await loc.count() else ""
                return bool(state.get("selected") or current2 and (current2.lower() == wanted.lower() or wanted.lower() in current2.lower() or current2.lower() in wanted.lower()))
        except Exception:
            return False
    except Exception:
        return False
    return False


async def _click_transport_profile_wizard_progression(page: Page) -> Dict[str, Any]:
    """Click a safe non-final wizard progression button inside Create TP drawer.

    The listing page also has pagination buttons named Next.  This helper scopes
    itself to the visible Create Transport Profile drawer/form and explicitly
    ignores Save/Create/Submit/Delete actions.
    """
    try:
        candidate = await page.evaluate(r"""
() => {
  function visible(el) {
    const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : {width:0,height:0};
    const st = el ? window.getComputedStyle(el) : null;
    return !!(r.width && r.height && st && st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || '1') !== 0);
  }
  function path(el) {
    const parts=[]; let n=el;
    for(let d=0;n&&n.nodeType===1&&d<8;d++,n=n.parentElement){
      let p=n.tagName.toLowerCase();
      if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;}
      const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-/.test(c)).slice(0,3);
      if(cls.length)p+='.'+cls.map(c=>CSS.escape(c)).join('.');
      const parent=n.parentElement;
      if(parent){const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName); if(same.length>1)p+=':nth-of-type('+(same.indexOf(n)+1)+')';}
      parts.unshift(p);
    }
    return parts.join(' > ');
  }
  const unsafe = /(save|create|submit|delete|remove|disable|enable|archive)/i;
  const ok = /^(next|continue|proceed|done|ok)$/i;
  const surfaces = Array.from(document.querySelectorAll('app-generic-drawer, .dds__drawer, [role=dialog], form'))
    .filter(s => visible(s) && /Create\s+Transport\s+Profile/i.test((s.innerText || s.textContent || '')));
  // Prefer the smallest Create TP surface so table pagination mounted behind the
  // drawer is not considered part of the wizard. Never click DDS table pagination.
  surfaces.sort((a, b) => {
    const ar = a.getBoundingClientRect();
    const br = b.getBoundingClientRect();
    return (ar.width * ar.height) - (br.width * br.height);
  });
  for (const surface of surfaces) {
    const buttons = Array.from(surface.querySelectorAll('button, [role=button], a'));
    for (const btn of buttons) {
      if (!visible(btn)) continue;
      if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') continue;
      if (btn.closest('dds-pagination, .dds__pagination, dds-table, .dds__table, dds-table-ribbon')) continue;
      const text = (btn.innerText || btn.textContent || btn.getAttribute('aria-label') || btn.getAttribute('title') || '').replace(/\s+/g, ' ').trim();
      const cls = (btn.className || '').toString();
      const marker = `${text} ${cls} ${btn.getAttribute('aria-label') || ''} ${btn.getAttribute('title') || ''}`;
      if (!text || unsafe.test(marker)) continue;
      if (/pagination|next-page|previous-page|items per page|table/i.test(marker)) continue;
      if (!ok.test(text)) continue;
      return {clicked: false, found: true, selector: path(btn), text, className: cls};
    }
  }
  return {clicked: false, reason: 'no safe wizard progression button found'};
}
""")
        if isinstance(candidate, dict) and candidate.get("found") and candidate.get("selector"):
            clicked = await open_control_for_discovery(
                page, str(candidate.get("selector") or ""),
                label=f"Transport Profile wizard {candidate.get('text') or 'Next'}", phase="transport_profile",
            )
            candidate["clicked"] = bool(clicked)
            candidate["executor"] = "semantic_discovery_dispatch" if semantic_runtime_enabled(page) else "legacy_exact_dispatch"
        return candidate if isinstance(candidate, dict) else {"clicked": False, "reason": "no safe wizard progression button found"}
    except Exception as exc:
        return {"clicked": False, "error": mask_sensitive_string(str(exc))}

async def _count_transport_profile_create_controls(page: Page) -> int:
    """Count visible controls likely belonging to Create TP form/drawer, not grid."""
    try:
        return int(await page.evaluate(r"""
() => {
  function visible(el) {
    const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : {width:0,height:0};
    const st = el ? window.getComputedStyle(el) : null;
    return !!(r.width && r.height && st && st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || '1') !== 0);
  }
  const surfaces = Array.from(document.querySelectorAll('app-generic-drawer, .dds__drawer, [role=dialog], form, main'))
    .filter(s => visible(s) && /Create\s+Transport\s+Profile/i.test((s.innerText || s.textContent || '')));
  let best = 0;
  for (const s of surfaces) {
    const controls = Array.from(s.querySelectorAll('input:not([type=hidden]), textarea, select, [role=combobox]')).filter(visible);
    best = Math.max(best, controls.length);
  }
  return best;
}
"""))
    except Exception:
        return 0
async def _advance_transport_profile_add_wizard(
    page: Page,
    browser: BrowserSession,
    *,
    kb_dir: Path,
    warnings: List[str],
    dummy_values: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Expand the Create Transport Profile wizard before final form capture.

    The TP create page first exposes only a `System Type` combobox. This helper
    safely chooses non-mutating wizard values so downstream fields are revealed.
    It never clicks Save/Create/Submit/Delete.
    """
    try:
        await browser.save_dom_snapshot("transport_profile_add_form_before_wizard_expand")
    except Exception:
        pass

    def find_control(rows: List[Dict[str, Any]], *needles: str) -> Optional[Dict[str, Any]]:
        for c in rows:
            text = " ".join(str(c.get(k) or "") for k in ["label", "name", "id", "placeholder", "ariaLabel"]).lower()
            if all(n.lower() in text for n in needles):
                return c
        return None

    step_attempts: List[Dict[str, Any]] = []
    direction = str(dummy_values.get("direction") or "").strip().lower()
    usage_choice = str(dummy_values.get("usage") or dummy_values.get("profile_usage") or "").strip()
    if not usage_choice or usage_choice.lower() in {"outbound", "inbound"}:
        usage_choice = "Receiver" if direction in {"inbound", "receiver"} else "Sender"
    interface_choice = str(dummy_values.get("interface_type") or dummy_values.get("transport_protocol") or "SFTP HAFT").strip()
    if interface_choice.upper() in {"SFTP", "SFTP-HAFT", "HAFT"}:
        interface_choice = "SFTP HAFT"
    # Ordered wizard values.  Partner Name is required after System Type=Partner;
    # without it the portal only shows the shallow first step.
    planned = [
        (("system", "type"), dummy_values.get("system_type", "Partner")),
        (("partner", "name"), dummy_values.get("partner_name") or dummy_values.get("system_name") or "dce-test-partner"),
        (("application", "name"), dummy_values.get("system_name", "AIC - DCE")),
        (("available", "environment"), dummy_values.get("environment", "DEV")),
        (("environment",), dummy_values.get("environment", "DEV")),
        (("usage",), usage_choice),
        (("interface", "type"), interface_choice),
        (("protocol",), dummy_values.get("transport_protocol", interface_choice)),
        (("document", "type"), dummy_values.get("document_type_name") or dummy_values.get("document_type") or "XML_DellAutoASN_10_U-HAUL_ANS_IB"),
    ]
    seen_selectors: set[str] = set()
    for round_no in range(3):
        made_progress = False
        for labels, value in planned:
            controls = await _evaluate_controls(page)
            ctrl = find_control(controls, *labels)
            if not ctrl:
                continue
            selector = str(ctrl.get("selector") or "")
            attempt_key = selector + "|" + "_".join(labels)
            if attempt_key in seen_selectors:
                continue
            before_count = await _count_transport_profile_create_controls(page)
            ok = await _select_transport_profile_combobox_option(page, selector, str(value))
            await page.wait_for_timeout(1200)
            after_count = await _count_transport_profile_create_controls(page)
            # System Type and Partner Name must reveal additional controls.  If a
            # dropdown option click returned but the control count did not change,
            # treat it as unselected and retry in the next round.  This prevents
            # the run from recording a shallow first-step-only form as success.
            critical_reveal = labels in {("system", "type"), ("partner", "name"), ("interface", "type")}
            revealed = after_count > before_count
            selected_for_progress = bool(ok and (revealed or not critical_reveal))
            step_attempts.append({
                "round": round_no + 1,
                "labels": labels,
                "selector": selector,
                "value": value,
                "selected": ok,
                "revealed": revealed,
                "create_controls_before": before_count,
                "create_controls_after": after_count,
            })
            if selected_for_progress or revealed:
                seen_selectors.add(attempt_key)
            made_progress = made_progress or selected_for_progress or revealed
            try:
                await browser.save_dom_snapshot("transport_profile_add_form_after_" + "_".join(labels))
            except Exception:
                pass
            # Some HIP create surfaces require a non-final Continue/Next after
            # choosing Partner Name.  Keep this strictly scoped to the create drawer.
            progression = await _click_transport_profile_wizard_progression(page)
            if progression.get("clicked"):
                await page.wait_for_timeout(1800)
                step_attempts.append({"round": round_no + 1, "wizard_progression": progression})
                made_progress = True
                try:
                    await browser.save_dom_snapshot("transport_profile_add_form_after_wizard_progression")
                except Exception:
                    pass
        if not made_progress:
            break
        if await _count_transport_profile_create_controls(page) > 8:
            break

    controls = await _evaluate_controls(page)
    if len(controls) <= 3:
        warnings.append("Transport Profile Add form still has shallow controls after wizard expansion; captured evidence may be first-step only. Wizard attempts: " + mask_sensitive_string(str(step_attempts)))
    else:
        warnings.append(f"Expanded Transport Profile Add wizard before capture; controls={len(controls)}; attempts={mask_sensitive_string(str(step_attempts[:6]))}")
    try:
        _write_json(kb_dir / "transport_profile_add_wizard_expand_audit.json", step_attempts)
    except Exception:
        pass
    return controls


async def _click_add_transport_profile_with_overlay_recovery(
    page: Page,
    browser: BrowserSession,
    add: Locator,
    *,
    kb_dir: Path,
    warnings: List[str],
) -> bool:
    """Open + Add for form learning without crashing on stale loading overlays."""
    attempts: List[str] = []
    closed = await _close_transport_profile_transient_surfaces(page)
    if closed:
        warnings.append(f"Closed {closed} stale Transport Profile drawer/menu surface(s) before + Add form capture.")
    for label in ["normal", "after_overlay_wait"]:
        try:
            await browser.wait_for_blocking_overlays_gone(timeout_ms=30000 if label == "normal" else 60000)
            await browser.click_and_wait(
                action=f"structural_opener click_add_transport_profile_do_not_save_{label}",
                locator=add,
                selector="Add Transport Profile",
            )
            await page.wait_for_timeout(1000)
            if await _looks_like_transport_profile_add_form(page):
                return True
            attempts.append(f"{label}: click returned but Add form was not detected")
        except Exception as exc:
            msg = mask_sensitive_string(str(exc))
            attempts.append(f"{label}: {msg}")
            try:
                await browser.save_dom_snapshot(f"transport_profile_add_click_failed_{label}")
            except Exception:
                pass
            await page.wait_for_timeout(1500)

    hidden_surfaces = await _force_hide_stale_transport_profile_drawers(page)
    if hidden_surfaces:
        warnings.append(f"Force-hidden {hidden_surfaces} stale Transport Profile detail drawer/menu surface(s) before + Add retry.")
        await page.wait_for_timeout(500)
        fresh_add = await _find_add_button(page)
        if fresh_add is not None:
            add = fresh_add
    disabled = await _disable_stale_loading_overlays(page)
    if disabled:
        warnings.append(f"Stale Transport Profile loading overlay detected before + Add; disabled pointer-events on {disabled} overlay node(s) for read-only KB form capture.")
    # Final retry remains under the same semantic gate after stale overlays are
    # neutralized. A semantic rejection must never fall through to JS/DOM click.
    try:
        fresh_add = await _find_add_button(page)
        if fresh_add is not None:
            add = fresh_add
        await browser.click_and_wait(
            action="structural_opener click_add_transport_profile_do_not_save_semantic_overlay_recovery",
            locator=add, selector="Add Transport Profile", mutation_risk=False,
        )
        await page.wait_for_timeout(1800)
        if await _looks_like_transport_profile_add_form(page):
            return True
        attempts.append("semantic_overlay_recovery: Add form was not detected")
    except Exception as exc:
        attempts.append(f"semantic_overlay_recovery: {mask_sensitive_string(str(exc))}")

    warnings.append("Could not open + Add Transport Profile form after overlay-aware retries. Old Transport Profile inventory/API/UI-row KB is still saved; Add-form controls are skipped. Attempts: " + " | ".join(attempts[-4:]))
    try:
        await browser.screenshot(kb_dir / "transport_profile_add_click_failed_overlay.png", full_page=True)
    except Exception:
        pass
    return False


def _merge_dropdown_kb(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge pre-fill and post-fill dropdown captures without duplicating a field.

    Prefer the capture with more real options for the same label/selector.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for src in (primary or []) + (secondary or []):
        key = (str(src.get("label") or "").strip().lower(), str(src.get("selector") or "").strip())
        k = "|".join(key)
        if k not in merged:
            merged[k] = dict(src)
            order.append(k)
            continue
        old_opts = merged[k].get("options") or []
        new_opts = src.get("options") or []
        if len(new_opts) > len(old_opts):
            merged[k] = dict(src)
    return [merged[k] for k in order]


def _filter_transport_profile_dropdown_options(label: str, options: List[Dict[str, Any]], portal_noise: List[str], option_allowlists: Dict[str, set]) -> List[Dict[str, Any]]:
    """Keep dropdown options scoped to Transport Profile form values.

    The Dell portal keeps header/navigation/footer/listing controls mounted while the Add form is open.
    Some DDS dropdown popups are rendered globally, so a naive transport_profile-wide option scan can mix
    form values with navbar options like Home/BizLink/SecureLink or pagination values.
    """
    label_key = str(label or "").strip().lower()
    allow = None
    for known_label, values in option_allowlists.items():
        if known_label in label_key:
            allow = values
            break
    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for raw in options or []:
        item = dict(raw or {})
        text = str(item.get("text") or item.get("value") or "").strip()
        if not text:
            continue
        lower = text.lower()
        if any(noise == lower or noise in lower for noise in portal_noise):
            continue
        if allow is not None and lower not in allow:
            continue
        key = (lower, str(item.get("value") or ""))
        if key in seen:
            continue
        seen.add(key)
        item["index"] = len(cleaned)
        cleaned.append(item)
    return cleaned


async def _collect_dropdown_options(page: Page, controls: List[Dict[str, Any]], max_dropdowns: int = 30) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    dropdown_terms = [
        "status", "version", "transaction", "type", "format", "validation", "operation", "derived", "usage", "select", "dropdown"
    ]
    footer_noise = ["copyright", "privacy", "terms of use", "accessibility", "cookie", "all rights reserved"]
    portal_noise = [
        "home", "bizlink", "securelink", "bizexchange", "transtrack", "bizmon",
        "transport_profile", "data map", "transport_profiles", "transport profile", "profile", "logout",
        "items per page", "page", "search", "filter by column", "privacy", "terms"
    ]
    option_allowlists = {
        "data format type": {"xml", "json", "edifact", "edix12", "csv", "flat"},
    }
    for c in controls[:max_dropdowns]:
        tag = c.get("tag")
        role = c.get("role")
        ctype = str(c.get("type") or "").lower()
        label = c.get("label") or c.get("name") or c.get("id") or f"control_{c.get('index')}"
        if c.get("disabled") or c.get("readonly"):
            continue
        if tag == "select":
            # Ignore listing/pagination selects that can remain mounted behind the Add form.
            text_for_select = " ".join(str(x or "") for x in [label, c.get("ariaLabel"), c.get("placeholder"), c.get("selector"), c.get("id"), c.get("name")]).lower()
            if any(k in text_for_select for k in ["pagination", "items per page", "page", "filter", "search"]):
                continue
            opts = _filter_transport_profile_dropdown_options(label, c.get("options") or [], portal_noise, option_allowlists)
            results.append({"label": label, "selector": c.get("selector"), "kind": "select", "options": opts, "dom_event": "change"})
            continue
        if role != "combobox" and "select" not in str(c.get("selector", "")).lower():
            continue
        # Only open things that look like Transport Profile dropdowns, not arbitrary table filters/search boxes.
        text = " ".join(str(x or "") for x in [label, c.get("ariaLabel"), c.get("placeholder"), c.get("selector")]).lower()
        if any(k in text for k in ["filter", "search", "sort", "pagination", "page"]):
            continue
        if not any(k in text for k in dropdown_terms):
            continue
        try:
            loc = page.locator(c.get("selector")).first
            if not await loc.is_visible(timeout=1000) or not await loc.is_enabled(timeout=1000):
                continue
            if not await open_control_for_discovery(page, str(c.get("selector") or ""), label=str(label), phase="transport_profile"):
                continue
            await page.wait_for_timeout(450)
            opts = await page.evaluate(r"""
({footerNoise}) => {
  const selectors = [
    '[role=option]', '[role=menuitem]', '[role=listbox] [role=option]',
    '.dds__dropdown__item', '.dds__select__option', '.dds__list-item', 'mat-option'
  ];
  const seen = new Set();
  const out = [];
  for (const sel of selectors) {
    for (const el of Array.from(document.querySelectorAll(sel)).slice(0, 250)) {
      const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {x:0,y:0,width:0,height:0};
      const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
      const lower = text.toLowerCase();
      if (!text || !r.width || !r.height) continue;
      if (footerNoise.some(x => lower.includes(x))) continue;
      if (r.bottom < 0 || r.top > window.innerHeight + 20) continue;
      const key = lower + '|' + (el.getAttribute('value') || el.getAttribute('data-value') || '');
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({index: out.length, text, value: el.getAttribute('value') || el.getAttribute('data-value') || '', visible: true});
    }
  }
  return out.slice(0, 120);
}
""", {"footerNoise": footer_noise + portal_noise})
            await close_open_dropdown(page, "transport_profile" if "transport" in __name__ else "biz_flow")
            opts = _filter_transport_profile_dropdown_options(label, opts or [], portal_noise, option_allowlists)
            results.append({"label": label, "selector": c.get("selector"), "kind": "combobox", "options": mask_sensitive_data(opts or []), "dom_event": "click->listbox option->change"})
        except Exception as exc:
            results.append({"label": label, "selector": c.get("selector"), "kind": "combobox", "options": [], "error": mask_sensitive_string(str(exc)), "dom_event": "click attempted"})
    return results


def _collect_dropdown_options_noninvasive(controls: List[Dict[str, Any]], max_dropdowns: int = 30) -> List[Dict[str, Any]]:
    """Collect dropdown evidence without clicking live DDS controls.

    The Transport Profile create wizard is fragile: opening DDS comboboxes and
    pressing Escape before dummy-fill can close the Create Transport Profile
    drawer.  Use this before no-save dummy-fill so the form remains open.  A
    fuller dropdown scan may run after the after-fill snapshot is saved.
    """
    results: List[Dict[str, Any]] = []
    dropdown_terms = [
        "system type", "partner name", "profile usage", "deployment group",
        "interface type", "interface environment", "post transfer action",
        "existing account", "document", "splitter", "step type", "select"
    ]
    for c in controls[:max_dropdowns]:
        if c.get("disabled") or c.get("readonly"):
            continue
        label = str(c.get("label") or c.get("name") or c.get("id") or f"control_{c.get('index')}").strip()
        role = str(c.get("role") or "").lower()
        tag = str(c.get("tag") or "").lower()
        text = " ".join(str(x or "") for x in [label, c.get("ariaLabel"), c.get("placeholder"), c.get("selector"), c.get("mapped_transport_profile_key")]).lower()
        if any(k in text for k in ["filter", "search", "pagination", "items per page", "page"]):
            continue
        if tag == "select":
            results.append({
                "label": label,
                "selector": c.get("selector"),
                "kind": "select",
                "options": mask_sensitive_data(c.get("options") or []),
                "dom_event": "non-invasive/select-options-read",
            })
            continue
        is_dropdownish = role == "combobox" or str(c.get("placeholder") or "").lower() == "select" or any(k in text for k in dropdown_terms)
        if not is_dropdownish:
            continue
        results.append({
            "label": label,
            "selector": c.get("selector"),
            "kind": "combobox",
            "options": mask_sensitive_data(c.get("options") or []),
            "dom_event": "non-invasive/no-click-before-dummy-fill",
            "note": "Captured as dropdown candidate without opening DDS popup so the Create Transport Profile drawer stays open for dummy fill.",
        })
    return results


async def _set_control_value(page: Page, selector: str, value: str, *, kind: str = "text") -> bool:
    if not selector:
        return False
    # AutoWebGLM is the primary decision layer through the shared DDS text tool.
    # Keep the historical local setter only as a compatibility fallback.
    try:
        if await dds_set_text_control(page, None, selector, str(value)):
            return True
    except Exception:
        # A Layer-11 semantic rejection is authoritative. Never catch it and
        # silently continue into raw Playwright/JavaScript setters.
        if semantic_runtime_enabled(page):
            return False
    if semantic_runtime_enabled(page):
        return False
    # Prefer Playwright's real fill path for Angular/DDS controls, then fall back
    # to a native-value-setter JS path that dispatches InputEvent/change/blur.
    try:
        loc = page.locator(selector).first
        if await loc.count() and await loc.is_visible(timeout=800) and await loc.is_enabled(timeout=800):
            tag_type = await loc.evaluate("el => ({tag:(el.tagName||'').toLowerCase(), type:(el.getAttribute('type')||'').toLowerCase(), role:el.getAttribute('role')||'', readonly:!!el.readOnly})")
            if tag_type.get("type") == "file" or tag_type.get("readonly"):
                return False
            if tag_type.get("tag") in {"input", "textarea"} and tag_type.get("role") != "combobox":
                await loc.fill(str(value), timeout=2500)
                await loc.dispatch_event("input")
                await loc.dispatch_event("change")
                await loc.dispatch_event("blur")
                return True
    except Exception:
        pass
    js = r"""
({selector, value}) => {
  const el = document.querySelector(selector);
  if (!el) return {ok:false, reason:'not found'};
  if (el.disabled || el.getAttribute('aria-disabled') === 'true' || el.readOnly) return {ok:false, reason:'disabled/readonly'};
  const tag = (el.tagName || '').toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  if (type === 'file') return {ok:false, reason:'file input skipped'};
  const setNativeValue = (node, val) => {
    const proto = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(node, val);
    else node.value = val;
  };
  if (tag === 'select') {
    const opts = Array.from(el.options || []);
    const wanted = String(value).trim().toLowerCase();
    const match = opts.find(o => (o.text || '').trim().toLowerCase() === wanted) || opts.find(o => String(o.value).trim().toLowerCase() === wanted) || opts.find(o => (o.text || '').toLowerCase().includes(wanted));
    if (match) el.value = match.value;
    else el.value = value;
  } else if (el.getAttribute('contenteditable') === 'true') {
    el.textContent = value;
  } else {
    el.focus();
    setNativeValue(el, value);
  }
  for (const ev of ['input','change']) el.dispatchEvent(new InputEvent(ev, {bubbles:true, inputType:'insertText', data:String(value)}));
  el.dispatchEvent(new Event('blur', {bubbles:true}));
  return {ok:true, value: tag === 'select' ? el.value : (el.value || el.textContent || '')};
}
"""
    try:
        result = await page.evaluate(js, {"selector": selector, "value": value})
        return bool(result and result.get("ok"))
    except Exception:
        return False



async def _select_transport_profile_radio_option(page: Page, group_label: str, option_text: str) -> bool:
    """Select a TP radio through the shared explicit-click radio contract."""
    return await select_radio_value(
        page,
        None,
        str(option_text or ""),
        section=str(group_label or ""),
        phase="transport_profile",
    )


async def _set_transport_profile_checkbox(page: Page, label_text: str, desired: str) -> bool:
    """Set a DDS checkbox/switch without directly assigning checked state."""
    want = str(desired or "").strip().lower() in {"yes", "true", "1", "checked", "enable", "enabled"}
    try:
        found = await page.evaluate(r"""
(labelText) => {
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function css(el){if(!el||!el.tagName)return '';if(el.id)return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;const p=[];let n=el;while(n&&n.nodeType===1&&p.length<9){let x=n.tagName.toLowerCase();const par=n.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===n.tagName);if(same.length>1)x+=`:nth-of-type(${same.indexOf(n)+1})`;}p.unshift(x);n=par;}return p.join(' > ');}
  function visibleHost(el){const host=el.closest('label,.dds__checkbox,[role=checkbox],[role=switch]')||el;const r=host.getBoundingClientRect();const s=getComputedStyle(host);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden');}
  const target=clean(labelText).toLowerCase();
  const candidates=Array.from(document.querySelectorAll('input[type=checkbox],[role=checkbox],[role=switch]')).filter(visibleHost).map(el=>{
    const lab=el.closest('label')||(el.id?document.querySelector(`label[for="${CSS.escape(el.id)}"]`):null)||el.parentElement;
    const text=clean(`${lab?(lab.innerText||lab.textContent||''):''} ${el.getAttribute('aria-label')||''} ${el.name||''}`);
    let score=0;const low=text.toLowerCase();if(low===target)score=200;else if(low.includes(target)||target.includes(low))score=100;
    return {selector:css(el),text,score};
  }).filter(x=>x.score>0).sort((a,b)=>b.score-a.score||a.text.length-b.text.length);
  if(!candidates.length)return {resolved:false,reason:'checkbox not found'};
  if(candidates.length>1&&candidates[0].score===candidates[1].score)return {resolved:false,reason:'ambiguous checkbox',candidates:candidates.slice(0,5)};
  return {resolved:true,...candidates[0]};
}
""", str(label_text or ""))
    except Exception:
        return False
    if not isinstance(found, dict) or not found.get("resolved") or not found.get("selector"):
        return False
    return await set_checkbox_value(page, str(found["selector"]), want, label=str(label_text or ""), phase="transport_profile")


async def _scroll_transport_profile_form_to_top(page: Page) -> None:
    """Reset the Create TP form to the top before taking golden-truth evidence.

    The approved golden screenshots are full-form screenshots starting at System
    Type / Application-or-Partner Details.  Earlier runs captured the lower SFTP
    section only, which looked filled but could not be compared to the golden
    source/target images.  This scrolls every likely inner TP container to top.
    """
    try:
        await page.evaluate(r"""
() => {
  window.scrollTo(0, 0);
  for (const el of Array.from(document.querySelectorAll('app-generic-drawer, .dds__drawer, [role=dialog], form, main, .app__content, .dds__container, body'))) {
    const txt=(el.innerText||el.textContent||'');
    if (/Create\s+Transport\s+Profile|Transport\s+Profile\s+Details|System\s+Type/i.test(txt) || el === document.body) {
      try { el.scrollTop = 0; } catch(e) {}
    }
  }
  const header = Array.from(document.querySelectorAll('h1,h2,label,legend,span,div'))
    .find(el => /Create\s+Transport\s+Profile|System\s+Type/i.test(el.innerText||el.textContent||''));
  if (header && header.scrollIntoView) header.scrollIntoView({block:'start', inline:'nearest'});
}
""")
        await page.wait_for_timeout(450)
    except Exception:
        pass


async def _save_transport_profile_golden_truth_screenshot(page: Page, browser: BrowserSession, kb_dir: Path, phase_name: str) -> bool:
    """Save screenshot in the same visual posture as the human-approved golden image.

    The 165602/172450 runs showed the form was visible but no screenshot file was
    attached.  This function now writes the image through multiple independent
    paths and returns true as soon as a non-empty PNG exists.
    """
    await _scroll_transport_profile_form_to_top(page)
    shot = kb_dir / "transport_profile_add_form_after_dummy_fill_no_save.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.set_viewport_size({"width": 1650, "height": 950})
        await page.wait_for_timeout(350)
    except Exception:
        pass

    async def ok() -> bool:
        # On Windows/OneDrive, Playwright can return before the PNG is fully
        # visible to later verifier code.  Poll briefly so the golden-truth
        # screenshot is not treated as missing while the file is still flushing.
        deadline = time.time() + 3.0
        while True:
            try:
                if shot.exists() and shot.stat().st_size > 1000:
                    return True
            except Exception:
                pass
            if time.time() >= deadline:
                return False
            await page.wait_for_timeout(250)

    # 1) Direct Playwright page screenshot.  This is independent from the
    # BrowserSession wrapper and works even when the MCP screenshot adapter is
    # unavailable or returns a non-file payload.
    try:
        await page.screenshot(path=str(shot), full_page=True)
        if await ok():
            return True
    except Exception:
        pass

    # 2) BrowserSession wrapper fallback.
    try:
        await browser.screenshot(shot, full_page=True)
        if await ok():
            return True
    except Exception:
        pass

    # 3) Root crop fallback; useful when full-page screenshots fail because the
    # drawer/overlay is in a weird scroll state.
    try:
        root = await get_active_form_root(page, phase_name)
        await root.screenshot(path=str(shot), timeout=8000)
        if await ok():
            return True
    except Exception:
        pass

    # 4) Final body crop fallback, still better than no golden evidence.
    try:
        await page.locator("body").screenshot(path=str(shot), timeout=8000)
        if await ok():
            return True
    except Exception:
        pass
    return False


async def _scroll_transport_profile_form_to_reveal(page: Page, text_hint: str = "") -> None:
    """Scroll the active Create TP form and page to reveal lower SFTP sections."""
    try:
        await page.evaluate(r"""
(textHint) => {
  const roots = Array.from(document.querySelectorAll('app-generic-drawer, .dds__drawer, [role=dialog], form, main, body'))
    .filter(el => (el.innerText||el.textContent||'').match(/Create\s+Transport\s+Profile|Transport\s+Profile\s+Details|Document\(s\)\s+Supported/i));
  for (const el of roots) {
    try { el.scrollTop = Math.min((el.scrollHeight||0), (el.scrollTop||0) + 650); } catch(e) {}
  }
  if (textHint) {
    const hint = String(textHint).toLowerCase();
    const all = Array.from(document.querySelectorAll('label, legend, span, div, input, textarea'));
    const hit = all.find(el => ((el.innerText||el.textContent||el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.name||'').toLowerCase().includes(hint)));
    if (hit && hit.scrollIntoView) hit.scrollIntoView({block:'center', inline:'nearest'});
  } else {
    window.scrollBy(0, 650);
  }
}
""", text_hint)
        await page.wait_for_timeout(250)
    except Exception:
        pass


RULE_URL_HINTS = [
    "transport_profile", "transport_profiles", "transport-profile", "transport-profiles", "transportprofiles",
    "transportprofile", "transportProfile", "tpdetail", "tpDetail", "securelink",
]


def _is_transport_profile_api_url_like(url: str) -> bool:
    """Return True for every observed Dell Transport Profile API spelling.

    The portal uses both underscore and hyphen variants. The first TP build only
    matched `/transport_profile`, so it ignored the real
    `/transport-profiles/details-in-order` endpoint for many rows.
    """
    u = str(url or "").lower()
    return bool(re.search(r"transport[_-]?profiles?|transportprofiles|transportprofile", u))

RULE_FIELD_KEYS = {
    "transport_profile_id": ["transportProfileId", "transport_profile_id", "transportId", "tpId", "id"],
    "transport_profile_name": ["transportProfileName", "transport_profileName", "transport_profile_name", "profileName", "tpName", "name"],
    "transport_profile_version": ["transportProfileVersion", "transport_profileVersion", "transport_profile_version", "profileVersion", "version", "latestDevVersion", "latest_dev_version"],
    "transport_profile_operation": ["transportProfileOperation", "transport_profile_operation", "operation", "profileOperation"],
    "status": ["status", "state", "active", "enabled"],
    "description": ["description", "transportProfileDescription", "transport_profileDescription", "transport_profile_description"],
    "interface_type": ["interfaceType", "interface_type", "interface", "interfaceName", "connectionType"],
    "transport_protocol": ["transportProtocol", "protocol", "protocolType", "transportProfileType", "profileType", "type"],
    "direction": ["direction", "flowDirection", "transactionDirection"],
    "host": ["host", "hostname", "server", "serverName", "url", "endpoint", "remoteHost", "sftpHost"],
    "port": ["port", "remotePort", "sftpPort"],
    "user_name": ["userName", "username", "user", "login", "accountUserName"],
    "folder_path": ["folderPath", "directory", "remoteDirectory", "path", "targetDirectory", "sourceDirectory"],
    "file_pattern": ["filePattern", "fileNamePattern", "filenamePattern", "fileName", "pattern"],
    "account_id": ["accountId", "account_id"],
    "account_name": ["accountName", "account_name"],
    "partner_id": ["partnerId", "partner_id"],
    "partner_name": ["partnerName", "partner_name"],
    "system_id": ["systemId", "system_id", "domainId", "domain_id"],
    "system_name": ["systemName", "system_name", "domainName", "domain_name"],
    "document_type_id": ["documentTypeId", "document_type_id", "docTypeId"],
    "document_type_name": ["documentTypeName", "document_type_name", "docTypeName"],
    "document_type_version": ["documentTypeVersion", "document_type_version", "docTypeVersion"],
    "interface_details": ["interfaceDetails", "interface_details", "connectionDetails", "transportProfileDetails"],
    "parameters": ["parameters", "parameterList", "connectionParameters", "interfaceParameters", "interfaceDetails.parameters"],
    "document_type_details": ["documentTypeDetails", "document_type_details", "docTypeDetails"],
    "created_by": ["createdBy", "created_by", "requestedBy", "requested_by"],
    "updated_by": ["updatedBy", "modifiedBy", "lastModifiedBy"],
    "created_at": ["createdAt", "createdDate", "createdOn"],
    "updated_at": ["updatedAt", "modifiedDate", "lastModifiedDate"],
    "available_environments": ["availableEnvironments", "available_environments", "environments", "environment", "env"],
}


def _safe_json_load(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return None


def _contains_transport_profile_hint(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = text.lower()
    return any(h in text for h in RULE_URL_HINTS)


def _first_present(row: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    lower = {str(k).lower(): k for k in row.keys()}
    for key in keys:
        k = lower.get(str(key).lower())
        if k and row.get(k) not in (None, ""):
            return row.get(k)
    return None


def _iter_json_candidate_rows(payload: Any) -> Iterable[Dict[str, Any]]:
    """Yield table/list-shaped objects from common Dell API response wrappers."""
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
            elif isinstance(item, list):
                for sub in _iter_json_candidate_rows(item):
                    yield sub
        return
    if not isinstance(payload, dict):
        return
    # A single entity object.
    if any(k in payload for k in ["transportProfileId", "docTypeId", "transport_profileName", "transportProfileIdentifier", "rootElement", "dataFormatType", "transactionType", "validationType"]):
        yield payload
    for key in ["items", "content", "data", "records", "results", "result", "rows", "transport_profiles", "transport_profileList", "docTypes", "transport_profiles", "payload", "transport_profileDetail", "transport_profileDetails", "transport_profile_detail", "docTypeDetail", "details"]:
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
                elif isinstance(item, list):
                    for sub in _iter_json_candidate_rows(item):
                        yield sub
        elif isinstance(value, dict):
            for sub in _iter_json_candidate_rows(value):
                yield sub


def _normalize_transport_profile_conditions(value: Any) -> List[Dict[str, Any]]:
    if value in (None, "", [], {}):
        return []
    rows = value if isinstance(value, list) else [value]
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(mask_sensitive_data({
            "attribute": _scalar_text(_first_present(row, ["attribute", "attributeName", "field", "name", "conditionAttribute"])),
            "derived_from": _scalar_text(_first_present(row, ["derivedFrom", "derived_from", "source"])),
            "operator": _scalar_text(_first_present(row, ["operator", "comparisonOperator", "conditionOperator"])),
            "value": _scalar_text(_first_present(row, ["value", "expectedValue", "conditionValue", "expression"])),
            "expression": _scalar_text(_first_present(row, ["expression", "xpath", "path"])),
            "raw": mask_sensitive_data(row),
        }))
    return out


def _normalize_transport_profile_actions(value: Any) -> List[Dict[str, Any]]:
    if value in (None, "", [], {}):
        return []
    rows = value if isinstance(value, list) else [value]
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(mask_sensitive_data({
            "action_type": _scalar_text(_first_present(row, ["actionType", "action", "type", "transport_profileAction", "actionName"])),
            "mapping_identifier": _scalar_text(_first_present(row, ["mappingIdentifier", "mappingIdentifierName", "mapIdentifier", "mapName", "mappingName"])),
            "mapping_version": _scalar_text(_first_present(row, ["mappingVersion", "mapVersion", "version"])),
            "target_document_type": _scalar_text(_first_present(row, ["targetDocumentTypeName", "targetDocTypeName", "targetDocumentType"])),
            "raw": mask_sensitive_data(row),
        }))
    return out


def _compact_transport_profile_blob(value: Any) -> Any:
    if value in (None, "", [], {}):
        return {} if isinstance(value, dict) else [] if isinstance(value, list) else ""
    return mask_sensitive_data(value)


def _normalize_transport_profile_parameters(value: Any) -> List[Dict[str, Any]]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, dict):
        # A single dict can either be a parameter map or a wrapper around a list.
        for k in ["parameters", "parameterList", "connectionParameters", "interfaceParameters"]:
            if isinstance(value.get(k), list):
                value = value.get(k)
                break
        else:
            return [{"name": str(k), "value": _scalar_text(v), "raw": mask_sensitive_data({k: v})} for k, v in value.items() if v not in (None, "")]
    rows = value if isinstance(value, list) else [value]
    out: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(mask_sensitive_data({
                "name": _scalar_text(_first_present(row, ["name", "key", "parameterName", "paramName"])),
                "value": _scalar_text(_first_present(row, ["value", "parameterValue", "paramValue", "defaultValue"])),
                "type": _scalar_text(_first_present(row, ["type", "dataType", "parameterType"])),
                "required": _scalar_text(_first_present(row, ["required", "isRequired", "mandatory"])),
                "raw": mask_sensitive_data(row),
            }))
    return out


def extract_transport_profile_record(row: Dict[str, Any], *, source_url: str = "", source: str = "api") -> Optional[Dict[str, Any]]:
    """Normalize one API/list/detail row into the Transport Profile inventory schema."""
    if not isinstance(row, dict):
        return None
    values = {field: _first_present(row, keys) for field, keys in RULE_FIELD_KEYS.items()}
    # Deep/list APIs may wrap details under transport_profileDetail.
    detail = row.get("transport_profileDetail") if isinstance(row.get("transport_profileDetail"), dict) else row
    if detail is not row and isinstance(detail, dict):
        for field, keys in RULE_FIELD_KEYS.items():
            if values.get(field) in (None, "", [], {}):
                values[field] = _first_present(detail, keys)
    conditions = _normalize_transport_profile_conditions(values.get("conditions") or _recursive_find_key(row, ["transport_profileConditions", "conditions", "conditionList"], max_depth=5))
    actions = _normalize_transport_profile_actions(values.get("actions") or _recursive_find_key(row, ["transport_profileActions", "actions", "actionList"], max_depth=5))
    interface_details = values.get("interface_details") or _recursive_find_key(row, ["interfaceDetails", "interface_details", "connectionDetails"], max_depth=6)
    parameters = _normalize_transport_profile_parameters(values.get("parameters") or _recursive_find_key(row, ["parameters", "parameterList", "connectionParameters", "interfaceParameters"], max_depth=7))
    document_type_details = values.get("document_type_details") or _recursive_find_key(row, ["documentTypeDetails", "document_type_details", "docTypeDetails"], max_depth=6)
    has_transport_profile_specific = any(values.get(k) not in (None, "", [], {}) for k in [
        "transport_profile_name", "transport_profile_version", "transport_profile_operation", "interface_type", "transport_protocol", "direction",
        "host", "port", "account_id", "partner_id", "system_id", "document_type_id", "document_type_name"
    ]) or bool(parameters or interface_details or document_type_details or conditions or actions)
    if not has_transport_profile_specific and not _contains_transport_profile_hint(source_url):
        return None
    if values.get("transport_profile_id") in (None, "") and not has_transport_profile_specific:
        return None
    normalized = {
        "transport_profile_id": _scalar_text(values.get("transport_profile_id")),
        "transport_profile_name": _scalar_text(values.get("transport_profile_name")),
        "transport_profile_version": _scalar_text(values.get("transport_profile_version")),
        "status": _scalar_text(values.get("status")),
        "description": _scalar_text(values.get("description")),
        "transport_profile_operation": _scalar_text(values.get("transport_profile_operation")),
        "interface_type": _scalar_text(values.get("interface_type")),
        "transport_protocol": _scalar_text(values.get("transport_protocol")),
        "direction": _scalar_text(values.get("direction")),
        "host": _scalar_text(values.get("host")),
        "port": _scalar_text(values.get("port")),
        "user_name": _scalar_text(values.get("user_name")),
        "folder_path": _scalar_text(values.get("folder_path")),
        "file_pattern": _scalar_text(values.get("file_pattern")),
        "account_id": _scalar_text(values.get("account_id")),
        "account_name": _scalar_text(values.get("account_name")),
        "partner_id": _scalar_text(values.get("partner_id")),
        "partner_name": _scalar_text(values.get("partner_name")),
        "system_id": _scalar_text(values.get("system_id")),
        "system_name": _scalar_text(values.get("system_name")),
        "document_type_id": _scalar_text(values.get("document_type_id")),
        "document_type_name": _scalar_text(values.get("document_type_name")),
        "document_type_version": _scalar_text(values.get("document_type_version")),
        "interface_details": _compact_transport_profile_blob(interface_details),
        "parameters": parameters,
        "parameter_count": len(parameters),
        "document_type_details": _compact_transport_profile_blob(document_type_details),
        "source_document_type_id": _scalar_text(values.get("source_document_type_id")),
        "source_document_type_name": _scalar_text(values.get("source_document_type_name")),
        "source_document_type_version": _scalar_text(values.get("source_document_type_version")),
        "target_document_type_id": _scalar_text(values.get("target_document_type_id")),
        "target_document_type_name": _scalar_text(values.get("target_document_type_name")),
        "target_document_type_version": _scalar_text(values.get("target_document_type_version")),
        "condition_operation": _scalar_text(values.get("condition_operation")),
        "mapping_identifier": _scalar_text(values.get("mapping_identifier")),
        "mapping_version": _scalar_text(values.get("mapping_version")),
        "conditions": conditions,
        "condition_count": len(conditions),
        "actions": actions,
        "action_count": len(actions),
        "created_by": _scalar_text(values.get("created_by")),
        "updated_by": _scalar_text(values.get("updated_by")),
        "created_at": _scalar_text(values.get("created_at")),
        "updated_at": _scalar_text(values.get("updated_at")),
        "available_environments": values.get("available_environments") or "",
        "latest_dev_version": _scalar_text(_first_present(row, ["latestDevVersion", "latest_dev_version"]) or values.get("transport_profile_version")),
        "source": source,
        "source_url": mask_sensitive_string(source_url),
        "raw_row_compact": mask_sensitive_data(row),
    }
    if not any(normalized.get(k) for k in ["transport_profile_id", "transport_profile_name", "source_document_type_id", "target_document_type_id", "mapping_identifier"]) and not (conditions or actions):
        return None
    return normalized

def extract_transport_profile_records_from_payload(payload: Any, *, source_url: str = "", source: str = "api") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in _iter_json_candidate_rows(payload):
        rec = extract_transport_profile_record(row, source_url=source_url, source=source)
        if rec:
            rows.append(rec)
    return _dedupe_transport_profile_records(rows)


DEEP_PROFILE_LIST_KEYS = {
    "attributes", "transport_profileattributes", "transport_profiletypeattributes", "attributedetails",
    "attributes_to_configure", "transport_profileattribute", "identifiers", "transport_profileidentifiers",
    "transport_profileidentifierrows", "transport_profileidentifierdetails", "rows",
}


def _recursive_find_key(obj: Any, aliases: List[str], *, max_depth: int = 7) -> Any:
    """Return the first non-empty value found by key alias in a nested object."""
    alias_set = {str(a).lower() for a in aliases}
    seen: set[int] = set()

    def walk(value: Any, depth: int) -> Any:
        if depth < 0 or value is None:
            return None
        if isinstance(value, (dict, list)):
            oid = id(value)
            if oid in seen:
                return None
            seen.add(oid)
        if isinstance(value, dict):
            lower = {str(k).lower(): k for k in value.keys()}
            for a in alias_set:
                k = lower.get(a)
                if k is not None and value.get(k) not in (None, "", [], {}):
                    return value.get(k)
            for v in value.values():
                found = walk(v, depth - 1)
                if found not in (None, "", [], {}):
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item, depth - 1)
                if found not in (None, "", [], {}):
                    return found
        return None

    return walk(obj, max_depth)


def _recursive_collect_lists(obj: Any, key_aliases: set[str], *, max_depth: int = 7) -> List[List[Any]]:
    out: List[List[Any]] = []
    seen: set[int] = set()

    def walk(value: Any, depth: int, parent_key: str = "") -> None:
        if depth < 0 or value is None:
            return
        if isinstance(value, (dict, list)):
            oid = id(value)
            if oid in seen:
                return
            seen.add(oid)
        if isinstance(value, dict):
            for k, v in value.items():
                kl = str(k).lower()
                if isinstance(v, list) and kl in key_aliases:
                    out.append(v)
                walk(v, depth - 1, kl)
        elif isinstance(value, list):
            if parent_key in key_aliases:
                out.append(value)
            for item in value:
                walk(item, depth - 1, parent_key)

    walk(obj, max_depth)
    return out


def _normalize_usage(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(_scalar_text(item.get("name") or item.get("label") or item.get("value") or item.get("usage")))
            else:
                parts.append(_scalar_text(item))
        return ", ".join([p for p in parts if p])
    if isinstance(value, dict):
        return _scalar_text(value.get("name") or value.get("label") or value.get("value") or value.get("usage"))
    return _scalar_text(value)


def _looks_like_attribute_row(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    lower = {str(k).lower() for k in row.keys()}
    has_attr_name = bool(lower & {"attributename", "attribute_name", "name", "label"})
    has_attr_detail = bool(lower & {"usage", "usages", "expression", "xpath", "path", "attributeexpression", "derivedfrom", "derived_from"})
    # Do not mistake transport_profile-identifier rows like {derivedFrom, value} for attributes.
    if not has_attr_name:
        return False
    return has_attr_detail


def _normalize_attribute_row(row: Dict[str, Any]) -> Dict[str, Any]:
    name = _first_present(row, ["attributeName", "attribute_name", "name", "label"])
    derived = _first_present(row, ["derivedFrom", "derived_from", "attributeDerivedFrom", "source"])
    usage = _first_present(row, ["usage", "usages", "attributeUsage", "usageList"])
    expr = _first_present(row, ["expression", "xpath", "path", "value", "attributeExpression"])
    return mask_sensitive_data({
        "attribute_name": _scalar_text(name),
        "derived_from": _scalar_text(derived),
        "usage": _normalize_usage(usage),
        "expression": _scalar_text(expr),
        "data_type": _scalar_text(_first_present(row, ["dataType", "type", "format"])),
        "required": _first_present(row, ["required", "mandatory"]),
        "raw": row,
    })


def _normalize_attributes_from_detail(obj: Any) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    # Prefer specifically named attribute lists, then fall back to any row-shaped list.
    for lst in _recursive_collect_lists(obj, DEEP_PROFILE_LIST_KEYS):
        for item in lst:
            if _looks_like_attribute_row(item):
                candidates.append(item)
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list):
                for item in v:
                    if _looks_like_attribute_row(item):
                        candidates.append(item)
    seen = set()
    out: List[Dict[str, Any]] = []
    for row in candidates:
        norm = _normalize_attribute_row(row)
        key = (norm.get("attribute_name"), norm.get("derived_from"), norm.get("expression"), norm.get("usage"))
        if not any(key) or key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out[:200]


def _normalize_identifier_rows_from_detail(obj: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    raw = _recursive_find_key(obj, ["transportProfileIdentifier", "transport_profile_identifier", "transportProfileIdentifiers", "transportProfileIdentifierDetails", "identifierRows", "rows"])
    if isinstance(raw, dict):
        maybe_rows = None
        for row_key in ("rows", "attributeList", "attributes", "identifierRows", "conditions"):
            if isinstance(raw.get(row_key), list):
                maybe_rows = raw.get(row_key)
                break
        if maybe_rows is not None:
            raw = maybe_rows
        else:
            raw = [raw]
    elif isinstance(raw, str):
        raw = [{"value": raw}]
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                rows.append(mask_sensitive_data({
                    "derived_from": _scalar_text(_first_present(item, ["derivedFrom", "derived_from", "source"])),
                    "value": _scalar_text(_first_present(item, ["value", "rootElement", "root_element", "identifier", "transportProfileIdentifier"])),
                    "expression": _scalar_text(_first_present(item, ["expression", "xpath", "path"])),
                    "raw": item,
                }))
            elif item not in (None, ""):
                rows.append({"derived_from": "", "value": _scalar_text(item), "expression": "", "raw": item})
    # Remove empty/duplicate rows.
    seen = set()
    out = []
    for row in rows:
        key = (row.get("derived_from"), row.get("value"), row.get("expression"))
        if not any(key) or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out[:100]


def _detail_payload_candidates(payload: Any) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return (transport_profile-detail-object, wrapper) pairs from API responses."""
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    if isinstance(payload, dict):
        for key in ["transport_profileDetail", "transport_profileDetails", "transport_profile_detail", "docTypeDetail", "details", "transport_profile", "data", "payload", "result"]:
            v = payload.get(key)
            if isinstance(v, dict):
                out.append((v, payload))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        out.append((item, payload))
        if any(k in payload for k in ["transportProfileId", "transport_profileName", "transportProfileIdentifier", "rootElement", "dataFormatType", "transactionType", "validationType"]):
            out.append((payload, payload))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                out.extend(_detail_payload_candidates(item))
    # Deduplicate by object id/order.
    seen: set[int] = set()
    deduped: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for item, wrapper in out:
        if id(item) in seen:
            continue
        seen.add(id(item))
        deduped.append((item, wrapper))
    return deduped


def _compact_related_detail(value: Any) -> Dict[str, Any]:
    if isinstance(value, list):
        return {"count": len(value), "sample": mask_sensitive_data(value[:5])}
    if isinstance(value, dict):
        rows = list(_iter_json_candidate_rows(value))
        return {"count": len(rows) if rows else (1 if value else 0), "sample": mask_sensitive_data(rows[:5] if rows else value)}
    return {"count": 0, "sample": []}


def _deep_profile_score(profile: Dict[str, Any]) -> int:
    keys = [
        "transport_profile_id", "transport_profile_name", "transport_profile_version", "status", "description",
        "transport_profile_operation", "interface_type", "transport_protocol", "direction",
        "host", "port", "account_id", "partner_id", "system_id", "document_type_id", "document_type_name",
        "source_document_type_id", "source_document_type_name", "target_document_type_id", "target_document_type_name",
    ]
    score = sum(1 for k in keys if profile.get(k) not in (None, "", [], {}))
    score += min(18, int(profile.get("parameter_count") or 0) * 3)
    score += min(8, int(profile.get("condition_count") or 0) * 2)
    score += min(8, int(profile.get("action_count") or 0) * 2)
    return score


def _is_transport_profile_summary_endpoint(url: str) -> bool:
    u = str(url or "").lower()
    return bool(
        re.search(r"/api/transport[_-]?profile/summary(?:$|[?#/])", u)
        or re.search(r"/api/transport[_-]?profiles/summary(?:$|[?#/])", u)
        or re.search(r"/api/transportprofiles/summary(?:$|[?#/])", u)
        or re.search(r"/api/transportprofile/summary(?:$|[?#/])", u)
    )


def _is_full_transport_profile_deep_profile(profile: Dict[str, Any]) -> bool:
    """Return True only for detail/edit/profile-quality Transport Profile payloads.

    The Transport Profiles summary endpoint exposes only name/version/environment. Treating that
    as a deep profile caused the previous run to mark all 283 rows as resolved before
    attempting real detail endpoints. Full/usable profiles must expose at least one
    transport_profile-specific configuration field such as conditions, actions, mapping, source/
    target document type, status/description, or a numeric transportProfileId.
    """
    if not profile:
        return False
    if int(profile.get("parameter_count") or 0) > 0:
        return True
    if profile.get("interface_details") not in (None, "", [], {}):
        return True
    if int(profile.get("condition_count") or 0) > 0:
        return True
    if int(profile.get("action_count") or 0) > 0:
        return True
    for key in [
        "transport_profile_operation", "interface_type", "transport_protocol", "direction", "host", "port",
        "account_id", "partner_id", "system_id", "document_type_id", "document_type_name",
        "mapping_identifier", "source_document_type_id", "source_document_type_name",
        "target_document_type_id", "target_document_type_name", "condition_operation",
        "status", "description", "transport_profile_id",
    ]:
        if profile.get(key) not in (None, "", [], {}):
            return True
    return _deep_profile_score(profile) >= 4


def _should_reuse_network_transport_profile_profile(profile: Dict[str, Any]) -> bool:
    """Gate reused network payloads so `/api/transport_profile/summary` never short-circuits deep learning."""
    if not _is_full_transport_profile_deep_profile(profile):
        return False
    return not _is_transport_profile_summary_endpoint(str(profile.get("source_url") or ""))


def _transport_profile_detail_payload_candidates(payload: Any) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    if isinstance(payload, dict):
        for key in ["transportProfileDetails", "transportProfileDetail", "transport_profileDetail", "transport_profileDetails", "transport_profile", "transportProfile", "interfaceDetails", "details", "data", "payload", "result"]:
            v = payload.get(key)
            if isinstance(v, dict):
                out.append((v, payload))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        out.append((item, payload))
        if any(k in payload for k in ["transportProfileId", "transportProfileName", "transport_profileName", "transportProfileDetails", "interfaceDetails", "parameters", "transport_profileConditions", "transport_profileActions", "sourceDocumentTypeId", "targetDocumentTypeId"]):
            out.append((payload, payload))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                out.extend(_transport_profile_detail_payload_candidates(item))
    seen: set[int] = set()
    deduped: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for item, wrapper in out:
        if id(item) in seen:
            continue
        seen.add(id(item))
        deduped.append((item, wrapper))
    return deduped


def normalize_transport_profile_deep_profile(payload: Any, *, source_url: str = "", source: str = "api_deep_profile", base_row: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Normalize `/api/transport_profile/{id}/details` style payloads into a full Transport Profile profile."""
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for detail, wrapper in _transport_profile_detail_payload_candidates(payload):
        values = {field: (_first_present(detail, keys) or _recursive_find_key(detail, keys, max_depth=5)) for field, keys in RULE_FIELD_KEYS.items()}
        if not values.get("transport_profile_id"):
            values["transport_profile_id"] = _extract_numeric_transport_profile_id_from_url_or_payload(source_url) or _recursive_find_key(detail, ["id", "transportProfileId"], max_depth=2)
        if base_row:
            for k in ["transport_profile_id", "transport_profile_name", "transport_profile_version", "status", "source_document_type_id", "source_document_type_name", "target_document_type_id", "target_document_type_name", "available_environments", "latest_dev_version"]:
                if values.get(k) in (None, "", [], {}) and base_row.get(k) not in (None, "", [], {}):
                    values[k] = base_row.get(k)
        conditions = _normalize_transport_profile_conditions(values.get("conditions") or _recursive_find_key(detail, ["transport_profileConditions", "conditions", "conditionList"], max_depth=6))
        actions = _normalize_transport_profile_actions(values.get("actions") or _recursive_find_key(detail, ["transport_profileActions", "actions", "actionList"], max_depth=6))
        interface_details = values.get("interface_details") or _recursive_find_key(detail, ["interfaceDetails", "interface_details", "connectionDetails"], max_depth=7)
        parameters = _normalize_transport_profile_parameters(values.get("parameters") or _recursive_find_key(detail, ["parameters", "parameterList", "connectionParameters", "interfaceParameters"], max_depth=8))
        document_type_details = values.get("document_type_details") or _recursive_find_key(detail, ["documentTypeDetails", "document_type_details", "docTypeDetails"], max_depth=7)
        related = {
            "flowDetail": _compact_related_detail(wrapper.get("flowDetail") if isinstance(wrapper, dict) else None),
            "documentTypeDetail": _compact_related_detail(wrapper.get("documentTypeDetail") if isinstance(wrapper, dict) else None),
            "mapDetail": _compact_related_detail(wrapper.get("mapDetail") if isinstance(wrapper, dict) else None),
            "tpDetail": _compact_related_detail(wrapper.get("tpDetail") if isinstance(wrapper, dict) else None),
        }
        profile = mask_sensitive_data({
            "transport_profile_id": _scalar_text(values.get("transport_profile_id")),
            "transport_profile_name": _scalar_text(values.get("transport_profile_name")),
            "transport_profile_version": _scalar_text(values.get("transport_profile_version")),
            "status": _scalar_text(values.get("status")),
            "description": _scalar_text(values.get("description")),
            "transport_profile_operation": _scalar_text(values.get("transport_profile_operation")),
            "interface_type": _scalar_text(values.get("interface_type")),
            "transport_protocol": _scalar_text(values.get("transport_protocol")),
            "direction": _scalar_text(values.get("direction")),
            "host": _scalar_text(values.get("host")),
            "port": _scalar_text(values.get("port")),
            "user_name": _scalar_text(values.get("user_name")),
            "folder_path": _scalar_text(values.get("folder_path")),
            "file_pattern": _scalar_text(values.get("file_pattern")),
            "account_id": _scalar_text(values.get("account_id")),
            "account_name": _scalar_text(values.get("account_name")),
            "partner_id": _scalar_text(values.get("partner_id")),
            "partner_name": _scalar_text(values.get("partner_name")),
            "system_id": _scalar_text(values.get("system_id")),
            "system_name": _scalar_text(values.get("system_name")),
            "document_type_id": _scalar_text(values.get("document_type_id")),
            "document_type_name": _scalar_text(values.get("document_type_name")),
            "document_type_version": _scalar_text(values.get("document_type_version")),
            "interface_details": _compact_transport_profile_blob(interface_details),
            "parameters": parameters,
            "parameter_count": len(parameters),
            "document_type_details": _compact_transport_profile_blob(document_type_details),
            "source_document_type_id": _scalar_text(values.get("source_document_type_id")),
            "source_document_type_name": _scalar_text(values.get("source_document_type_name")),
            "source_document_type_version": _scalar_text(values.get("source_document_type_version")),
            "target_document_type_id": _scalar_text(values.get("target_document_type_id")),
            "target_document_type_name": _scalar_text(values.get("target_document_type_name")),
            "target_document_type_version": _scalar_text(values.get("target_document_type_version")),
            "condition_operation": _scalar_text(values.get("condition_operation")),
            "conditions": conditions,
            "condition_count": len(conditions),
            "actions": actions,
            "action_count": len(actions),
            "mapping_identifier": _scalar_text(values.get("mapping_identifier") or (actions[0].get("mapping_identifier") if actions else "")),
            "mapping_version": _scalar_text(values.get("mapping_version") or (actions[0].get("mapping_version") if actions else "")),
            "created_by": _scalar_text(values.get("created_by")),
            "updated_by": _scalar_text(values.get("updated_by")),
            "created_at": _scalar_text(values.get("created_at")),
            "updated_at": _scalar_text(values.get("updated_at")),
            "available_environments": values.get("available_environments") or (base_row or {}).get("available_environments", ""),
            "latest_dev_version": _scalar_text(_first_present(detail, ["latestDevVersion", "latest_dev_version"]) or (base_row or {}).get("latest_dev_version") or values.get("transport_profile_version")),
            "related_usage": related,
            "source": source,
            "source_url": mask_sensitive_string(source_url),
            "raw_detail_compact": mask_sensitive_data(detail),
        })
        score = _deep_profile_score(profile)
        if score > best_score:
            best = profile
            best_score = score
    if best and best_score > 0:
        best["deep_profile_completeness_score"] = best_score
        return best
    return None


def extract_transport_profile_deep_profiles_from_payload(payload: Any, *, source_url: str = "", source: str = "api_deep_profile", base_row: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for detail, wrapper in _transport_profile_detail_payload_candidates(payload):
        payload_for_one = {"transport_profileDetail": detail}
        if isinstance(wrapper, dict):
            for key in ["flowDetail", "documentTypeDetail", "mapDetail", "tpDetail"]:
                if key in wrapper:
                    payload_for_one[key] = wrapper[key]
        profile = normalize_transport_profile_deep_profile(payload_for_one, source_url=source_url, source=source, base_row=base_row)
        if profile:
            profiles.append(profile)
    if not profiles:
        profile = normalize_transport_profile_deep_profile(payload, source_url=source_url, source=source, base_row=base_row)
        if profile:
            profiles.append(profile)
    seen = set()
    out = []
    for profile in profiles:
        key = (profile.get("transport_profile_id"), profile.get("transport_profile_name"), profile.get("transport_profile_version"), profile.get("source_url"))
        if key in seen:
            continue
        seen.add(key)
        out.append(profile)
    return out


def _record_matches_deep_profile(profile: Dict[str, Any], row: Dict[str, Any]) -> bool:
    for key in ["transport_profile_id", "transport_profile_name"]:
        a = str(profile.get(key) or "").strip().lower()
        b = str(row.get(key) or "").strip().lower()
        if a and b and a == b:
            return True
    return False


def _apply_deep_profile_to_record(row: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(row)
    for key in [
        "transport_profile_id", "transport_profile_name", "transport_profile_version", "status", "description",
        "transport_profile_operation", "interface_type", "transport_protocol", "direction", "host", "port", "user_name", "folder_path", "file_pattern",
        "account_id", "account_name", "partner_id", "partner_name", "system_id", "system_name",
        "document_type_id", "document_type_name", "document_type_version",
        "source_document_type_id", "source_document_type_name", "source_document_type_version",
        "target_document_type_id", "target_document_type_name", "target_document_type_version",
        "condition_operation", "mapping_identifier", "mapping_version",
        "created_by", "updated_by", "created_at", "updated_at", "available_environments", "latest_dev_version",
    ]:
        if profile.get(key) not in (None, "", [], {}) and not merged.get(key):
            merged[key] = profile.get(key)
    if profile.get("transport_profile_id") and not row.get("transport_profile_id"):
        merged["transport_profile_id_source"] = "deep_profile_api"
    merged["deep_profile_status"] = "captured" if _deep_profile_score(profile) >= 4 else "partial"
    merged["deep_profile_source_url"] = profile.get("source_url", "")
    merged["deep_profile_completeness_score"] = profile.get("deep_profile_completeness_score", _deep_profile_score(profile))
    merged["parameters"] = profile.get("parameters") or merged.get("parameters") or []
    merged["parameter_count"] = len(merged.get("parameters") or [])
    merged["interface_details"] = profile.get("interface_details") or merged.get("interface_details") or {}
    merged["document_type_details"] = profile.get("document_type_details") or merged.get("document_type_details") or {}
    merged["conditions"] = profile.get("conditions") or merged.get("conditions") or []
    merged["condition_count"] = len(merged.get("conditions") or [])
    merged["actions"] = profile.get("actions") or merged.get("actions") or []
    merged["action_count"] = len(merged.get("actions") or [])
    merged["related_usage"] = profile.get("related_usage") or {}
    merged["deep_profile"] = profile
    return merged


def _deep_profile_report(rows: List[Dict[str, Any]], attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    captured = sum(1 for r in rows if r.get("deep_profile_status") == "captured")
    partial = sum(1 for r in rows if r.get("deep_profile_status") == "partial")
    with_conditions = sum(1 for r in rows if int(r.get("condition_count") or 0) > 0)
    with_actions = sum(1 for r in rows if int(r.get("action_count") or 0) > 0)
    with_mapping = sum(1 for r in rows if r.get("mapping_identifier"))
    return {
        "total_transport_profiles": total,
        "deep_profiles_captured": captured,
        "deep_profiles_partial": partial,
        "deep_profiles_missing": max(0, total - captured - partial),
        "with_conditions": with_conditions,
        "with_actions": with_actions,
        "with_mapping_identifier": with_mapping,
        "attempts": len(attempts),
        "capture_percent": round(((captured + partial) / total * 100), 2) if total else 0.0,
        "note": "Deep profile capture parses `/api/transport_profile/{id}/details` payloads plus any row action network payloads. Missing means the portal/API did not expose the full profile during this run.",
    }


def _candidate_transport_profile_deep_profile_urls(row: Dict[str, Any], interactions: List[Dict[str, Any]]) -> List[str]:
    urls: List[str] = []
    transport_profile_id = str(row.get("transport_profile_id") or "").strip()
    name = str(row.get("transport_profile_name") or "").strip()
    version = str(row.get("transport_profile_version") or row.get("latest_dev_version") or "").strip()
    env = _first_environment(row.get("available_environments"), "DEV")

    def add(url: str) -> None:
        if url and url not in urls:
            urls.append(url)

    id_bases = [
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profile",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profile/details",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transportprofiles",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transportprofile",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profile",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profiles",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profile-details",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profile-details",
    ]
    if transport_profile_id:
        for prefix in id_bases:
            add(f"{prefix}/{quote(transport_profile_id, safe='')}/details")
            add(f"{prefix}/{quote(transport_profile_id, safe='')}/detail")
            add(f"{prefix}/details/{quote(transport_profile_id, safe='')}")
            add(f"{prefix}/detail/{quote(transport_profile_id, safe='')}")
            add(f"{prefix}/details?transportProfileId={quote(transport_profile_id, safe='')}")
            add(f"{prefix}/detail?transportProfileId={quote(transport_profile_id, safe='')}")
    if name:
        # Real SecureLink Transport Profiles deep details endpoint observed in live runs.
        # This endpoint is name-based and does not require numeric transportProfileId.
        # Keep it first so every row gets the same high-value lookup, not only rows
        # where the endpoint happened to be captured from UI row actions.
        details_in_order_q = {"transportProfileName": name, "hipEnvironment": env}
        if version:
            details_in_order_q["version"] = version
            details_in_order_q["transportProfileVersion"] = version
        add("https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profiles/details-in-order?" + urlencode(details_in_order_q))
        add("https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profiles/details?" + urlencode(details_in_order_q))

        for base in [
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profile/details",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profile/detail",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profiles/details",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transportprofiles/details",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transportprofile/details",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profile/details",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profile/detail",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profiles/details",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profiles/detail",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profile-details",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profile-details",
        ]:
            q = {"transportProfileName": name, "transport_profileName": name, "profileName": name, "environment": env, "hipEnvironment": env}
            if version:
                q["version"] = version
                q["transport_profileVersion"] = version
                q["transportProfileVersion"] = version
            add(base + "?" + urlencode(q))

    # Reuse any observed detail endpoint shape from the session.  When a prior UI
    # click revealed a concrete /transport_profile/<id>/details URL, replay that shape for the
    # current Transport Profile ID.  When only name-based APIs exist, keep the original URL so
    # its payload can be parsed from already captured network bodies.
    for inter in interactions or []:
        u = str(inter.get("url") or "")
        if not _is_transport_profile_api_url_like(u) or "detail" not in u.lower():
            continue
        parsed = urlparse(u)
        if transport_profile_id and re.search(r"/(?:transport[_-]?profile|transport[_-]?profiles|transportprofiles|transportprofile|transport[_-]?profile-details?)/\d+/(?:detail|details)", parsed.path, flags=re.IGNORECASE):
            add(re.sub(r"(/(?:transport[_-]?profile|transport[_-]?profiles|transportprofiles|transportprofile|transport[_-]?profile-details?)/)[^/]+(/(?:detail|details))", rf"\g<1>{quote(transport_profile_id, safe='')}\2", urlunparse(parsed._replace(query="")), flags=re.IGNORECASE))
        elif transport_profile_id and re.search(r"/(?:detail|details)/\d+", parsed.path, flags=re.IGNORECASE):
            add(re.sub(r"(/(?:detail|details)/)[^/]+", rf"\g<1>{quote(transport_profile_id, safe='')}", urlunparse(parsed._replace(query="")), flags=re.IGNORECASE))
        elif name:
            add(u)
    return urls[:20]


async def _capture_transport_profile_deep_profiles(
    page: Page,
    browser: BrowserSession,
    records: List[Dict[str, Any]],
    interactions: List[Dict[str, Any]],
    *,
    max_profiles: int | None = None,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
    timeout_ms: int = 12000,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Capture full Transport Profile profiles for every learned Transport Profile row.

    This mirrors the Document Type deep-profile phase.  It is strictly read-only:
    1. parse all Transport Profile detail payloads already captured during listing/UI row actions;
    2. call safe details endpoints by learned transportProfileId or by transport_profileName/version when ID is hidden;
    3. checkpoint old_transport_profiles_deep_profiles + audit so a long run is recoverable.
    """
    rows = _merge_transport_profile_records(records)
    limit = min(len(rows), int(max_profiles)) if max_profiles is not None else len(rows)
    target_rows = rows[:limit]
    attempts: List[Dict[str, Any]] = []

    headers: Dict[str, Any] = {}
    for inter in interactions or []:
        for hk, hv in (inter.get("request_headers_compact") or {}).items():
            if str(hk).lower() in {"x-requester-id", "referer", "origin"} and hv:
                headers[hk] = hv
        if headers.get("x-requester-id"):
            break
    headers.setdefault("Referer", TRANSPORT_PROFILES_URL)

    # Reuse all network detail payloads that were already triggered by UI row actions.
    # This is critical for Transport Profiles because the listing API often hides numeric transportProfileId.
    event_profiles: List[Dict[str, Any]] = []
    for ev in getattr(browser, "network_tab_events", []) or []:
        d = _event_dict(ev)
        url = str(d.get("url") or "")
        event_text = json.dumps(d, ensure_ascii=False, default=str)
        if not (_is_transport_profile_api_url_like(url) or _is_transport_profile_api_url_like(event_text)):
            continue
        payload = d.get("response_body_redacted") or _safe_json_load(d.get("response_body_text_redacted"))
        if payload is None:
            continue
        for profile in extract_transport_profile_deep_profiles_from_payload(payload, source_url=url, source="network_deep_profile_reuse"):
            if _should_reuse_network_transport_profile_profile(profile):
                event_profiles.append(profile)

    for idx, row in enumerate(target_rows, start=1):
        label = row.get("transport_profile_name") or row.get("transport_profile_id") or f"row {idx}"
        if progress_cb:
            maybe = progress_cb(
                phase="transport_profile_deep_profile_enrichment",
                completed=idx - 1,
                total=max(1, limit),
                detail=f"deep profile {idx}/{limit}: {str(label)[:80]}",
                counts={"deep_profiles_captured": sum(1 for r in rows if r.get("deep_profile_status") in {"captured", "partial"})},
            )
            if asyncio.iscoroutine(maybe):
                await maybe

        best = None
        for profile in event_profiles:
            if _record_matches_deep_profile(profile, row):
                if best is None or _deep_profile_score(profile) > _deep_profile_score(best):
                    best = profile
        row_attempt = {
            "transport_profile_id": row.get("transport_profile_id"),
            "transport_profile_name": row.get("transport_profile_name"),
            "used_existing_network_profile": bool(best),
            "attempts": [],
            "resolved": False,
        }
        if best:
            row.update(_apply_deep_profile_to_record(row, best))
            row_attempt["resolved"] = True
            row_attempt["source"] = best.get("source_url")

        if not row_attempt["resolved"]:
            for url in _candidate_transport_profile_deep_profile_urls(row, interactions):
                body, meta = await _fetch_json_with_page(page, url, headers=headers, timeout_ms=timeout_ms)
                profiles = extract_transport_profile_deep_profiles_from_payload(body, source_url=url, source="direct_deep_profile_api", base_row=row) if body is not None else []
                matched = None
                for profile in profiles:
                    if _record_matches_deep_profile(profile, row):
                        matched = profile
                        break
                if matched is None and len(profiles) == 1:
                    matched = profiles[0]
                if matched and not _is_full_transport_profile_deep_profile(matched):
                    # Preserve the attempt but do not treat a summary/listing-shaped payload as deep learning.
                    matched = None
                row_attempt["attempts"].append(mask_sensitive_data({**meta, "profiles_extracted": len(profiles), "matched": bool(matched)}))
                if matched:
                    row.update(_apply_deep_profile_to_record(row, matched))
                    row_attempt["resolved"] = True
                    row_attempt["source"] = matched.get("source_url")
                    break

        # Last-resort partial: preserve expanded row text as evidence instead of
        # reporting a false crash.  This is not counted as full capture unless the
        # normalized profile score meets the captured threshold.
        if not row_attempt["resolved"] and row.get("ui_expanded_detail_text_compact"):
            profile = normalize_transport_profile_deep_profile({"transport_profileDetail": {**row, "description": row.get("ui_expanded_detail_text_compact")}}, source_url="ui_expanded_row_text", source="ui_expanded_row_text", base_row=row)
            if profile and _is_full_transport_profile_deep_profile(profile):
                row.update(_apply_deep_profile_to_record(row, profile))
                row_attempt["resolved"] = True
                row_attempt["source"] = "ui_expanded_row_text"
                row_attempt["partial_from_expanded_row_text"] = True

        attempts.append(row_attempt)
        if kb_dir and (idx % 10 == 0 or idx == limit):
            try:
                _write_json(kb_dir / "old_transport_profiles_deep_profiles.checkpoint.json", rows)
                _write_json(kb_dir / "transport_profile_deep_profile_enrichment_audit.checkpoint.json", attempts)
                _write_json(kb_dir / "transport_profile_deep_profile_report.checkpoint.json", _deep_profile_report(rows, attempts))
            except Exception:
                pass

    if progress_cb:
        maybe = progress_cb(
            phase="transport_profile_deep_profile_enrichment_done",
            completed=limit,
            total=max(1, limit),
            detail="Deep Transport Profile profile capture complete",
            counts=_deep_profile_report(rows, attempts),
        )
        if asyncio.iscoroutine(maybe):
            await maybe
    return _merge_transport_profile_records(rows), attempts, _deep_profile_report(rows, attempts)


def _dedupe_transport_profile_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = str(row.get("transport_profile_id") or "").strip()
        if not key:
            key = "|".join(str(row.get(k) or "").strip().lower() for k in ["transport_profile_name", "transport_profile_version", "transport_profile_identifier", "root_element"])
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _compact_payload_shape(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, list):
        sample = payload[0] if payload else None
        return {"type": "list", "row_count": len(payload), "sample_keys": sorted(list(sample.keys()))[:80] if isinstance(sample, dict) else []}
    if isinstance(payload, dict):
        wrapper_keys = sorted(list(payload.keys()))[:80]
        rows = list(_iter_json_candidate_rows(payload))
        sample = rows[0] if rows else None
        return {"type": "dict", "wrapper_keys": wrapper_keys, "row_count": len(rows), "sample_keys": sorted(list(sample.keys()))[:80] if isinstance(sample, dict) else []}
    return {"type": type(payload).__name__, "row_count": 0, "sample_keys": []}


def _event_dict(event: Any) -> Dict[str, Any]:
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        return event.model_dump()
    if hasattr(event, "dict"):
        return event.dict()
    return dict(getattr(event, "__dict__", {}) or {})


def collect_transport_profile_api_interactions(events: Iterable[Any], *, stage_label: str = "unknown") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extract compact API learning evidence and old Transport Profile rows from captured network events."""
    interactions: List[Dict[str, Any]] = []
    inventory: List[Dict[str, Any]] = []
    for ev in events:
        d = _event_dict(ev)
        url = str(d.get("url") or "")
        method = str(d.get("method") or "")
        payload = d.get("response_body_redacted")
        if payload is None:
            payload = _safe_json_load(d.get("response_body_text_redacted"))
        request_body = d.get("request_body_redacted")
        is_relevant = _is_transport_profile_api_url_like(url) or _contains_transport_profile_hint(payload) or _contains_transport_profile_hint(request_body)
        if not is_relevant:
            continue
        rows = extract_transport_profile_records_from_payload(payload, source_url=url, source=f"network:{stage_label}") if payload is not None else []
        inventory.extend(rows)
        interactions.append(mask_sensitive_data({
            "stage": stage_label,
            "timestamp": d.get("timestamp"),
            "method": method,
            "url": url,
            "status": d.get("status"),
            "mime_type": d.get("mime_type"),
            "resource_type": d.get("resource_type"),
            "page_context": d.get("page_context"),
            "request_headers_compact": _compact_request_headers(d.get("request_headers") or {}),
            "request_body_redacted": request_body,
            "response_shape": _compact_payload_shape(payload),
            "transport_profile_rows_extracted": len(rows),
            "sample_transport_profile_rows": rows[:3],
        }))
    return interactions, _dedupe_transport_profile_records(inventory)


def _compact_request_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"accept", "content-type", "x-requester-id", "x-account-id", "referer", "origin"}
    out = {}
    for k, v in (headers or {}).items():
        if str(k).lower() in allowed:
            out[k] = v
    return mask_sensitive_data(out)


def _dedupe_api_interactions(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = "|".join(str(item.get(k) or "") for k in ["stage", "method", "url", "status", "transport_profile_rows_extracted"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _build_transport_profile_lookup(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    lookup: Dict[str, Any] = {"by_transport_profile_id": {}, "by_transport_profile_name": {}, "by_transport_profile_identifier": {}, "by_root_element": {}}
    for row in records:
        compact = {k: row.get(k, "") for k in ["transport_profile_id", "transport_profile_name", "transport_profile_version", "latest_dev_version", "status", "transaction_type", "format", "validation_type", "transport_profile_identifier", "root_element", "transport_profile_version", "schema_file", "available_environments", "source_url"]}
        for field, bucket in [("transport_profile_id", "by_transport_profile_id"), ("transport_profile_name", "by_transport_profile_name"), ("transport_profile_identifier", "by_transport_profile_identifier"), ("root_element", "by_root_element")]:
            val = str(row.get(field) or "").strip()
            if val:
                lookup[bucket].setdefault(val, []).append(compact)
    return lookup


async def _collect_ui_transport_profile_rows(page: Page) -> List[Dict[str, Any]]:
    """Fallback: collect visible Transport Profile rows directly from the DOM grid.

    The Transport Profile page can render visible rows while the summary/listing API is not
    captured by the Playwright network listener.  The first TP build was filtering for
    `transport_profile`/DocType-specific text, so visible rows such as
    `Name | Usage | Interface Type | System Name` were ignored and the run then sat in a
    long rows=0 API replay.  This collector is intentionally grid-first and returns both raw
    text evidence and normalized candidate fields.
    """
    js = r"""
() => {
  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    return !!(r.width && r.height && st.visibility !== 'hidden' && st.display !== 'none');
  }
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 7) {
      let part = el.tagName.toLowerCase();
      if (el.id) { part += '#' + CSS.escape(el.id); parts.unshift(part); break; }
      const cls = (el.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0, 3).map(c => CSS.escape(c)).join('.');
      if (cls) part += '.' + cls;
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(x => x.tagName === el.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(el) + 1) + ')';
      }
      parts.unshift(part); el = parent;
    }
    return parts.join(' > ');
  }
  function cellText(el) {
    return (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
  }
  function directCells(row) {
    let cells = Array.from(row.querySelectorAll(':scope > td, :scope > th, :scope > [role="cell"], :scope > [role="gridcell"], :scope > .dds__td, :scope > .dds__table__cell'))
      .filter(isVisible).map(cellText).filter(Boolean);
    if (!cells.length) {
      cells = Array.from(row.children || []).filter(isVisible).map(cellText).filter(Boolean);
    }
    return cells.map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
  }
  function looksLikeDataRow(text, cells) {
    const lower = (text || '').toLowerCase();
    if (!text || text.length < 3) return false;
    if (/^(name\s+usage\s+interface|usage\s+interface|table search|items per page|page \d+)/i.test(text)) return false;
    if (rowNoise(lower)) return false;
    if (cells.length >= 2 && cells.some(c => /^(sender|receiver)$/i.test(c)) ) return true;
    if (cells.length >= 2 && cells.some(c => /^(https-as2|https|sftp|ftp|as2|sftp-haft)$/i.test(c)) ) return true;
    if (/\b(sender|receiver)\b/i.test(text) && /\b(https-as2|https|sftp|ftp|as2|sftp-haft)\b/i.test(text)) return true;
    // TP names often include protocol hints even if the row cells are flattened.
    if (/\b(tp|transport|sftp|https|as2|receiver|sender|outbound|inbound)\b/i.test(text) && cells.length >= 2) return true;
    return false;
  }
  function rowNoise(lower) {
    return /dell technologies developer|securelink\s*$|bizlink\s*$|home\s+bizlink|chrome is being controlled|cookie|privacy|learn more|filter|navigation/.test(lower);
  }
  function fieldGuess(cells, text) {
    const clean = cells.map(c => c.trim()).filter(Boolean);
    const usage = clean.find(c => /^(sender|receiver)$/i.test(c)) || '';
    const iface = clean.find(c => /^(https-as2|https|sftp|ftp|as2|sftp-haft)$/i.test(c)) || '';
    let name = clean[0] || (text || '').split(/\s{2,}|\n/)[0] || '';
    // Some rows start with an expand/collapse icon label; remove it.
    name = name.replace(/^(expand|collapse)( the)? row\s*/i, '').trim();
    if (/^(sender|receiver)$/i.test(name) && clean.length > 1) name = clean[1];
    const systemIdx = iface ? clean.findIndex(c => c === iface) + 1 : -1;
    const systemName = systemIdx > 0 && clean[systemIdx] ? clean[systemIdx] : '';
    return {transport_profile_name: name, usage, interface_type: iface, system_name: systemName};
  }
  const selectors = [
    'table tbody tr', '.dds__table tbody tr', '.dds__table__row', '.dds__tr',
    '[role="row"]', '[class*="table"] [class*="row"]', '[class*="data-table"] [class*="row"]'
  ];
  const rows = [];
  for (const sel of selectors) {
    for (const el of Array.from(document.querySelectorAll(sel)).slice(0, 1000)) {
      if (!isVisible(el)) continue;
      const text = cellText(el);
      const cells = directCells(el);
      if (!looksLikeDataRow(text, cells)) continue;
      rows.push({selector: cssPath(el), text: text.slice(0, 2000), cells: cells.slice(0, 20), ...fieldGuess(cells, text), link_count: el.querySelectorAll('a').length, button_count: el.querySelectorAll('button,[role=button]').length});
    }
  }
  const seen = new Set();
  return rows.filter(x => {
    const key = (x.transport_profile_name || '') + '|' + (x.usage || '') + '|' + (x.interface_type || '') + '|' + (x.system_name || '') + '|' + x.text.slice(0,120);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 500);
}
"""
    try:
        return mask_sensitive_data(await page.evaluate(js) or [])
    except Exception:
        return []


def _transport_profile_records_from_ui_rows(ui_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize visible DOM grid rows into inventory seed records."""
    records: List[Dict[str, Any]] = []
    for idx, row in enumerate(ui_rows or [], start=1):
        name = _scalar_text(row.get("transport_profile_name") or "").strip()
        text = _scalar_text(row.get("text") or "")
        cells = row.get("cells") or []
        if not name and cells:
            name = _scalar_text(cells[0]).strip()
        name = re.sub(r"^(expand|collapse)( the)? row\s*", "", name, flags=re.I).strip()
        if not name or name.lower() in {"name", "usage", "interface type"}:
            continue
        rec = {
            "transport_profile_id": "",
            "transport_profile_name": name,
            "transport_profile_version": "",
            "usage": _scalar_text(row.get("usage") or ""),
            "interface_type": _scalar_text(row.get("interface_type") or ""),
            "transport_protocol": _scalar_text(row.get("interface_type") or ""),
            "system_name": _scalar_text(row.get("system_name") or ""),
            "source_url": TRANSPORT_PROFILES_URL,
            "source": "ui_visible_grid_fallback",
            "ui_row_selector": row.get("selector") or "",
            "ui_row_text_compact": text[:1500],
            "raw_row_compact": mask_sensitive_data(row),
        }
        records.append(rec)
    return _dedupe_transport_profile_records(records)




def _parse_transport_profile_pagination_info(text: str) -> Dict[str, int]:
    """Parse Dell DDS pagination text such as `1 - 10 of 659 items`.

    The Transport Profiles page often exposes only the first page through the DOM
    while the public listing API returns no rows.  This parser lets the UI crawler
    know that there are more pages even when the raw HTML snapshot does not show
    a pagination component.
    """
    txt = re.sub(r"\s+", " ", str(text or " ")).strip()
    info = {"start": 0, "end": 0, "total": 0, "page_size": 0, "total_pages": 0}
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s+of\s+(\d+)\s+items", txt, flags=re.I)
    if m:
        start, end, total = [int(x) for x in m.groups()]
        info.update({"start": start, "end": end, "total": total, "page_size": max(1, end - start + 1)})
        info["total_pages"] = max(1, (total + info["page_size"] - 1) // info["page_size"])
        return info
    m = re.search(r"of\s+(\d+)\s+items", txt, flags=re.I)
    if m:
        total = int(m.group(1))
        info.update({"total": total, "page_size": 10, "total_pages": max(1, (total + 9) // 10)})
    return info


async def _transport_profile_listing_body_text(page: Page) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=2500) or "")
    except Exception:
        try:
            return str(await page.evaluate("() => document.body && document.body.innerText || ''") or "")
        except Exception:
            return ""


async def _try_set_transport_profiles_items_per_page_100(page: Page) -> bool:
    """Best-effort increase page size before crawling UI pages.

    This is intentionally optional: if the DDS dropdown is not clickable, the
    crawler still walks the Next button page-by-page.  It never clicks any
    mutating Save/Create/Submit/Delete control.
    """
    before = await _collect_ui_transport_profile_rows(page)
    if len(before) >= 40:
        return True
    body = await _transport_profile_listing_body_text(page)
    if "Items per page" not in body or not re.search(r"\b100\b", body):
        return False
    try:
        # The page-size options are read-only pagination controls.  Try the most
        # specific text locator first; if it fails, ignore and continue with Next.
        opt = page.get_by_text("100", exact=True).last
        if await opt.count() and await opt.is_visible(timeout=1200):
            if semantic_runtime_enabled(page):
                clicked = await open_control_for_discovery(
                    page, "text=100", label="Transport Profiles Items per page 100", phase="transport_profile"
                )
                if not clicked:
                    return False
            else:
                await opt.click(timeout=2500)
            await page.wait_for_timeout(1800)
            after = await _collect_ui_transport_profile_rows(page)
            return len(after) > len(before)
    except Exception:
        pass
    return False


async def _click_transport_profiles_next_page(page: Page) -> bool:
    """Click the visible, enabled pagination Next button, including open shadow roots."""
    selectors = [
        "button:has-text('Next')",
        "[role=button]:has-text('Next')",
        "a:has-text('Next')",
        "button[aria-label*='Next' i]",
        "[aria-label*='Next' i]",
    ]
    for sel in selectors:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(min(count, 8)):
                loc = locs.nth(i)
                try:
                    if not await loc.is_visible(timeout=600):
                        continue
                    disabled = await loc.evaluate("""el => !!(el.disabled || el.getAttribute('aria-disabled') === 'true' || /disabled/i.test(el.className || ''))""")
                    if disabled:
                        continue
                    if semantic_runtime_enabled(page):
                        stable_sel = await loc.evaluate(r"""el => {
                          if(el.id) return '#'+CSS.escape(el.id);
                          const parts=[]; let n=el;
                          for(let d=0;n&&n.nodeType===1&&d<7;d++,n=n.parentElement){
                            let p=n.tagName.toLowerCase(); const par=n.parentElement;
                            if(par){const same=Array.from(par.children).filter(x=>x.tagName===n.tagName); if(same.length>1)p+=':nth-of-type('+(same.indexOf(n)+1)+')';}
                            parts.unshift(p);
                          }
                          return parts.join(' > ');
                        }""")
                        if not await open_control_for_discovery(
                            page, stable_sel, label="Transport Profiles pagination Next", phase="transport_profile"
                        ):
                            continue
                    else:
                        await loc.click(timeout=2500)
                    await page.wait_for_timeout(1800)
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    # JS deep fallback is legacy standalone-only. Under Layer-11, a missing
    # semantic pagination target fails closed instead of DOM-dispatching.
    if semantic_runtime_enabled(page):
        return False
    # JS deep fallback for DDS/web components whose controls are inside open shadow roots.
    try:
        clicked = await page.evaluate(r"""
() => {
  function all(root) {
    const out = [];
    const walk = (node) => {
      if (!node) return;
      if (node.nodeType === 1) {
        out.push(node);
        if (node.shadowRoot) walk(node.shadowRoot);
      }
      for (const ch of Array.from(node.children || [])) walk(ch);
    };
    walk(root || document);
    return out;
  }
  function visible(el) {
    const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : {width:0,height:0};
    const st = el ? window.getComputedStyle(el) : null;
    return !!(r.width && r.height && st && st.display !== 'none' && st.visibility !== 'hidden');
  }
  const candidates = all(document).filter(el => /^(button|a)$/i.test(el.tagName || '') || el.getAttribute('role') === 'button');
  for (const el of candidates) {
    const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ');
    const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true' || /disabled/i.test(el.className || '');
    if (/^next$/i.test(text) && visible(el) && !disabled) { el.click(); return true; }
  }
  return false;
}
""")
        if clicked:
            await page.wait_for_timeout(1800)
            return True
    except Exception:
        pass
    return False


async def _crawl_old_transport_profile_inventory_from_ui_pagination(
    page: Page,
    *,
    max_pages: int = 250,
    progress_cb: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Crawl the Transport Profiles grid by using the visible pagination controls.

    The TP page in DEV shows `1 - 10 of 659 items`, but network/API replay can
    expose only the first 10 rows.  This read-only UI crawl collects every visible
    page, clicks `Next`, and stops when the parsed total is reached or the Next
    button disappears.  It only uses listing pagination and never mutates data.
    """
    rows: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    seen_names: set[str] = set()
    await _clear_transport_profile_listing_search(page)
    await _try_set_transport_profiles_items_per_page_100(page)
    body = await _transport_profile_listing_body_text(page)
    info = _parse_transport_profile_pagination_info(body)
    total_items = int(info.get("total") or 0)
    total_pages = int(info.get("total_pages") or 0) or max_pages
    limit_pages = max(1, min(int(max_pages or 1), total_pages if total_pages else max_pages, 300))
    last_signature = ""
    for page_idx in range(1, limit_pages + 1):
        ui_rows = await _collect_ui_transport_profile_rows(page)
        recs = _transport_profile_records_from_ui_rows(ui_rows)
        new_count = 0
        for rec in recs:
            key = (str(rec.get("transport_profile_name") or "").strip().lower(), str(rec.get("usage") or "").strip().lower(), str(rec.get("interface_type") or "").strip().lower(), str(rec.get("system_name") or "").strip().lower())
            if key not in seen_names:
                seen_names.add(key)
                rows.append(rec)
                new_count += 1
        body = await _transport_profile_listing_body_text(page)
        info = _parse_transport_profile_pagination_info(body)
        audit.append({
            "source": "ui_pagination_grid_crawl",
            "page_index": page_idx,
            "visible_rows": len(ui_rows),
            "new_records": new_count,
            "total_records_so_far": len(rows),
            "pagination": info,
        })
        if progress_cb:
            maybe = progress_cb(
                "old_transport_profile_ui_pagination",
                len(rows),
                max(total_items or len(rows), 1),
                f"Crawled Transport Profile grid page {page_idx}/{limit_pages}; rows={len(rows)}",
                {"old_transport_profiles": len(rows)},
            )
            if asyncio.iscoroutine(maybe):
                await maybe
        if total_items and len(rows) >= total_items:
            break
        signature = "|".join(str(r.get("transport_profile_name") or "") for r in recs[:3])
        clicked = await _click_transport_profiles_next_page(page)
        if not clicked:
            audit[-1]["stop_reason"] = "next_button_not_found_or_disabled"
            break
        # Wait until the page visibly changes.  If it does not, stop to avoid an infinite loop.
        changed = False
        for _ in range(8):
            await page.wait_for_timeout(500)
            next_rows = _transport_profile_records_from_ui_rows(await _collect_ui_transport_profile_rows(page))
            next_sig = "|".join(str(r.get("transport_profile_name") or "") for r in next_rows[:3])
            if next_sig and next_sig != signature and next_sig != last_signature:
                changed = True
                last_signature = next_sig
                break
        if not changed and page_idx > 1:
            audit[-1]["stop_reason"] = "next_click_did_not_change_visible_rows"
            break
    return _dedupe_transport_profile_records(rows), audit

def _urls_same_path(url_a: str, url_b: str) -> bool:
    try:
        a = urlparse(url_a)
        b = urlparse(url_b)
        return (a.netloc.lower(), a.path.rstrip("/")) == (b.netloc.lower(), b.path.rstrip("/"))
    except Exception:
        return False


async def _wait_for_transport_profile_listing_ready(page: Page, browser: BrowserSession, transport_profiles_url: str, warnings: List[str], *, attempts: int = 4) -> bool:
    """Wait until the Transport Profiles SPA has loaded enough to expose rows, Add, or the Transport Profile API.

    This specifically protects against a live Dell behavior where SSO lands on the
    correct URL, then an immediate second navigation aborts Angular chunks and
    leaves a blank page.  We only reload when the page is blank and no Transport Profile
    signal has appeared.
    """
    for attempt in range(1, attempts + 1):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=12000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        interactions, rows = collect_transport_profile_api_interactions(browser.network_tab_events, stage_label=f"readiness_attempt_{attempt}")
        if rows:
            return True
        try:
            ui_rows = await _collect_ui_transport_profile_rows(page)
            if ui_rows:
                return True
        except Exception:
            pass
        try:
            add = await _find_add_button(page)
            if add is not None:
                return True
        except Exception:
            pass
        try:
            body_text = (await page.locator("body").inner_text(timeout=2000) or "").strip()
        except Exception:
            body_text = ""
        if len(body_text) > 80 and re.search(r"transport_profile\s*type|doc\s*type|transport_profile|root\s*element|securelink", body_text, re.I):
            return True
        # Recover from a blank shell or aborted remoteEntry/chunk load.
        if attempt < attempts:
            browser.set_stage(f"transport_profile_kb_retry_blank_listing_{attempt}")
            try:
                if _urls_same_path(page.url, transport_profiles_url):
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                else:
                    await page.goto(transport_profiles_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                warnings.append(f"Transport Profile listing retry {attempt} navigation/reload failed: {exc}")
    warnings.append("Transport Profile listing page did not expose rows/Add/API after retries; using direct summary API fallback if authenticated.")
    return False


async def _direct_fetch_transport_profile_summary(page: Page, *, max_pages: int = 25) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch the known read-only Transport Profile summary API when the SPA/network capture misses it."""
    urls: List[str] = [
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profile/summary",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transportprofiles/summary",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transportprofile/summary",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profile/summary",
        "/inaas-gateway/hipService-svc/api/transport-profile/summary",
        "/inaas-gateway/hipService-svc/api/transportprofiles/summary",
        "/inaas-gateway/hipService-svc/api/transportprofile/summary",
        "/inaas-gateway/hipService-svc/api/transport_profile/summary",
        "/inaas-gateway/hipService-svc/api/transport_profiles/summary",
    ]
    # Common pagination shapes; the bare endpoint currently returns all rows in DEV,
    # but keep these as fallback for future backend changes.
    for page_no in range(max(0, min(max_pages, 8))):
        urls.append(f"/inaas-gateway/hipService-svc/api/transport-profile/summary?page={page_no}&size=100")
        urls.append(f"/inaas-gateway/hipService-svc/api/transport-profile/summary?pageIndex={page_no}&pageSize=100")
        urls.append(f"/inaas-gateway/hipService-svc/api/transportprofiles/summary?page={page_no}&size=100")
        urls.append(f"/inaas-gateway/hipService-svc/api/transport_profile/summary?page={page_no}&size=100")
    rows: List[Dict[str, Any]] = []
    interactions: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw_url in urls:
        if raw_url in seen:
            continue
        seen.add(raw_url)
        if raw_url.startswith("/"):
            url = "https://developer.dell.com" + raw_url
        else:
            url = raw_url
        body, meta = await _fetch_json_with_page(page, url, headers={"Referer": TRANSPORT_PROFILES_URL}, timeout_ms=4500)
        page_rows = extract_transport_profile_records_from_payload(body, source_url=url, source="direct_summary_api_fallback") if body is not None else []
        meta = mask_sensitive_data({**meta, "source": "direct_summary_api_fallback", "transport_profile_rows_extracted": len(page_rows)})
        audit.append(meta)
        interactions.append(mask_sensitive_data({
            "stage": "direct_summary_api_fallback",
            "timestamp": utc_now(),
            "method": "GET",
            "url": url,
            "status": meta.get("status"),
            "mime_type": "application/json" if body is not None else None,
            "resource_type": "Fetch",
            "page_context": page.url,
            "request_headers_compact": {"Referer": TRANSPORT_PROFILES_URL, "Accept": "application/json, text/plain, */*"},
            "request_body_redacted": "",
            "response_shape": _compact_payload_shape(body),
            "transport_profile_rows_extracted": len(page_rows),
            "sample_transport_profile_rows": page_rows[:3],
        }))
        if page_rows:
            rows.extend(page_rows)
            # The bare endpoint returning rows is enough; paginated calls can be redundant.
            if "?" not in url:
                break
        # Stop quickly on auth/errors/empty pagination.  This prevents user runs with
        # --max-api-pages 25000 from sitting for many minutes when TP summary APIs are
        # not exposed to the session.
        status = int(meta.get("status") or 0) if str(meta.get("status") or "").isdigit() else 0
        if status in {401, 403, 404, 405}:
            break
        if "?" in url and not page_rows:
            if rows or len([a for a in audit if "?" in str(a.get("url") or "")]) >= 4:
                break
    return _dedupe_transport_profile_records(rows), interactions, audit


async def _fetch_json_with_page(page: Page, url: str, headers: Optional[Dict[str, Any]] = None, *, timeout_ms: int = 25000) -> Tuple[Any, Dict[str, Any]]:
    headers = dict(headers or {})
    headers.setdefault("Accept", "application/json, text/plain, */*")
    try:
        result = await asyncio.wait_for(page.evaluate(
            """
async ({url, headers, timeoutMs}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || 25000);
  try {
    const res = await fetch(url, {method: 'GET', credentials: 'include', headers: headers || {}, cache: 'no-store', signal: controller.signal});
    const text = await res.text();
    return {ok: res.ok, status: res.status, url: res.url, text};
  } catch (err) {
    return {ok: false, status: 'failed', url, text: '', error: String(err && err.message ? err.message : err)};
  } finally { clearTimeout(timer); }
}
""",
            {"url": url, "headers": headers, "timeoutMs": timeout_ms}), timeout=(timeout_ms / 1000) + 5)
        body = _safe_json_load(result.get("text"))
        return body, {"url": url, "resolved_url": result.get("url"), "ok": bool(result.get("ok")), "status": result.get("status"), "error": result.get("error"), "row_count": len(list(_iter_json_candidate_rows(body))) if body is not None else 0}
    except Exception as exc:
        return None, {"url": url, "ok": False, "status": "failed", "error": str(exc), "row_count": 0}


def _pagination_urls_from_interaction(interaction: Dict[str, Any], *, max_pages: int = 20) -> List[str]:
    url = str(interaction.get("url") or "")
    if not url or str(interaction.get("method") or "GET").upper() != "GET":
        return []
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    page_keys = [k for k in qs if k.lower() in {"page", "pagenumber", "pageindex", "currentpage"}]
    size_keys = [k for k in qs if k.lower() in {"size", "pagesize", "limit"}]
    if not page_keys and not size_keys:
        return []
    page_key = page_keys[0] if page_keys else "page"
    size_key = size_keys[0] if size_keys else "size"
    size = qs.get(size_key, ["100"])[0] or "100"
    urls = []
    # Dell APIs are usually zero based; include 0..max_pages-1 and preserve all other query params.
    for page_no in range(max_pages):
        new_qs = {k: list(v) for k, v in qs.items()}
        new_qs[page_key] = [str(page_no)]
        new_qs[size_key] = [str(size)]
        query = urlencode(new_qs, doseq=True)
        urls.append(urlunparse(parsed._replace(query=query)))
    return urls


async def _crawl_old_transport_profile_inventory_from_apis(page: Page, interactions: List[Dict[str, Any]], *, max_pages: int = 20) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Best-effort pagination replay using the exact listing APIs observed from Network."""
    rows: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    headers: Dict[str, Any] = {}
    # Reuse x-requester-id/referer if it was observed.
    for inter in interactions:
        h = inter.get("request_headers_compact") or {}
        for key in ["x-requester-id", "referer", "origin"]:
            for hk, hv in h.items():
                if str(hk).lower() == key and hv:
                    headers[hk] = hv
        if headers.get("x-requester-id"):
            break
    tried: set[str] = set()
    for inter in interactions:
        urls = _pagination_urls_from_interaction(inter, max_pages=max_pages)
        for url in urls:
            if url in tried:
                continue
            tried.add(url)
            body, meta = await _fetch_json_with_page(page, url, headers=headers)
            audit.append(mask_sensitive_data({**meta, "source": "observed_api_pagination_replay"}))
            if meta.get("ok"):
                page_rows = extract_transport_profile_records_from_payload(body, source_url=url, source="api_pagination_replay")
                rows.extend(page_rows)
                if not page_rows and meta.get("row_count") == 0:
                    # Likely exhausted.
                    break
    return _dedupe_transport_profile_records(rows), audit



def _merge_transport_profile_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge duplicate Transport Profile rows while preferring records that include numeric transportProfileId/details."""
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("transport_profile_id") or "").strip().lower()
        name = str(row.get("transport_profile_name") or "").strip().lower()
        version = str(row.get("transport_profile_version") or row.get("latest_dev_version") or "").strip().lower()
        src = str(row.get("source_document_type_id") or row.get("source_document_type_name") or "").strip().lower()
        tgt = str(row.get("target_document_type_id") or row.get("target_document_type_name") or "").strip().lower()
        key = rid or "|".join([name, version, src, tgt]).strip("|")
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(row)
            order.append(key)
            continue
        existing = merged[key]
        for k, v in row.items():
            if v not in (None, "", [], {}):
                if existing.get(k) in (None, "", [], {}):
                    existing[k] = v
                elif k in {"conditions", "actions"} and isinstance(existing.get(k), list) and isinstance(v, list):
                    existing[k] = existing[k] or v
                elif k == "raw_row_compact" and isinstance(existing.get(k), dict) and isinstance(v, dict):
                    existing[k] = {**existing[k], **v}
        if "detail" in str(row.get("source") or "") and row.get("source"):
            existing["source"] = row.get("source")
            existing["detail_source_url"] = row.get("source_url") or row.get("detail_source_url", "")
    return [merged[k] for k in order]

def _known_transport_profile_id_by_identifier(previous_values: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    dm = (previous_values or {}).get("transport_profile_values_to_fill") or {}
    known = (previous_values or {}).get("known_ids") or {}
    mid = known.get("transport_profile_id")
    ident = dm.get("transport_profile_name")
    if ident and mid and str(mid).upper() not in {"UNKNOWN", "UNKNOWN_FROM_CURRENT_RULE_KB_RUN"}:
        out[str(ident).strip().lower()] = str(mid)
    return out


def _apply_known_transport_profile_ids(records: List[Dict[str, Any]], previous_values: Dict[str, Any]) -> List[Dict[str, Any]]:
    known = _known_transport_profile_id_by_identifier(previous_values)
    patched = []
    for row in records:
        item = dict(row)
        ident = str(item.get("transport_profile_name") or "").strip().lower()
        if ident in known and not item.get("transport_profile_id"):
            item["transport_profile_id"] = known[ident]
            item["transport_profile_id_source"] = "known_prior_context"
        patched.append(item)
    return patched


def _extract_detail_base_paths(interactions: List[Dict[str, Any]]) -> List[str]:
    bases: List[str] = []
    for inter in interactions:
        url = str(inter.get("url") or "")
        if not url:
            continue
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            continue
        path = parsed.path
        if "/api/" in path and any(x in path.lower() for x in ["transport_profile", "transport_profiletype", "transport_profile", "transport_profile"]):
            base_path = re.split(r"/(summary|detail|details|lookup|versions?|edit|view)(?:/|$)", path, maxsplit=1)[0]
            bases.append(urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", "")))
        if "/api/" in path and any(x in path.lower() for x in ["transport_profile", "transport_profile", "transport_profile", "securelink"]):
            # Keep the observed collection endpoint too; some APIs accept identifier filters on the summary route.
            bases.append(urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")))
    bases.extend(["https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profile", "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profile", "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profiles"])
    out: List[str] = []
    seen: set[str] = set()
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _detail_query_params(row: Dict[str, Any]) -> Dict[str, str]:
    ident = str(row.get("transport_profile_name") or "").strip()
    version = str(row.get("transport_profile_version") or row.get("latest_dev_version") or "").strip()
    env = _first_environment(row.get("available_environments"), "DEV")
    params = {"transport_profileName": ident, "environment": env}
    if version:
        params["version"] = version
        params["transport_profileVersion"] = version
    return {k: v for k, v in params.items() if v}


def _candidate_transport_profile_detail_urls(row: Dict[str, Any], interactions: List[Dict[str, Any]]) -> List[str]:
    ident = str(row.get("transport_profile_name") or "").strip()
    transport_profile_id = str(row.get("transport_profile_id") or "").strip()
    version = str(row.get("transport_profile_version") or row.get("latest_dev_version") or "").strip()
    params = _detail_query_params(row)
    bases = _extract_detail_base_paths(interactions)
    urls: List[str] = []
    for base in bases:
        parsed = urlparse(base)
        base_no_query = urlunparse(parsed._replace(query=""))
        def add(path_suffix: str = "", q: Dict[str, str] | None = None) -> None:
            full = base_no_query.rstrip("/") + path_suffix
            if q:
                full = full + "?" + urlencode(q)
            urls.append(full)
        if transport_profile_id:
            add("/" + quote(transport_profile_id, safe=""))
            add("/" + quote(transport_profile_id, safe="") + "/details")
            add("/" + quote(transport_profile_id, safe="") + "/detail")
            add("/details/" + quote(transport_profile_id, safe=""))
        if ident:
            add("/details", params)
            add("/detail", params)
            add("/lookup", params)
            add("/" + quote(ident, safe=""))
            if version:
                add("/" + quote(ident, safe="") + "/" + quote(version, safe=""))
    if ident:
        env = _first_environment(row.get("available_environments"), "DEV")
        details_in_order_q = {"transportProfileName": ident, "hipEnvironment": env}
        if version:
            details_in_order_q["version"] = version
            details_in_order_q["transportProfileVersion"] = version
        urls.insert(0, "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profiles/details-in-order?" + urlencode(details_in_order_q))
        urls.insert(1, "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profiles/details?" + urlencode(details_in_order_q))
    for base in ["https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profile", "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport-profiles", "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profile", "https://developer.dell.com/inaas-gateway/hipService-svc/api/transport_profiles"]:
        if transport_profile_id:
            urls.extend([f"{base}/{quote(transport_profile_id, safe='')}/details", f"{base}/details?transportProfileId={quote(transport_profile_id, safe='')}"])
        elif ident:
            urls.append(f"{base}/details?" + urlencode(params))
    out: List[str] = []
    seen: set[str] = set()
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out[:18]


def _record_matches_transport_profile(row: Dict[str, Any], target: Dict[str, Any]) -> bool:
    rid = str(target.get("transport_profile_id") or "").strip().lower()
    name = str(target.get("transport_profile_name") or "").strip().lower()
    if rid and str(row.get("transport_profile_id") or "").strip().lower() == rid:
        return True
    if name and str(row.get("transport_profile_name") or "").strip().lower() == name:
        return True
    return False


async def _enrich_old_transport_profiles_with_detail_apis(
    page: Page,
    records: List[Dict[str, Any]],
    interactions: List[Dict[str, Any]],
    previous_values: Dict[str, Any],
    *,
    max_details: int = 250,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
    timeout_ms: int = 5000,
    max_candidate_urls_per_transport_profile: int = 4,
    probe_rows: int = 15,
    stop_if_probe_finds_no_ids: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Best-effort read-only detail enrichment to discover numeric transportProfileIds.

    Bounded by design. The previous implementation could try many speculative
    URL shapes for every Transport Profile, which can look like a hang when the Dell gateway
    slowly returns 404/500/timeout. This version writes checkpoints and stops
    the expensive detail phase when the first probe batch proves that the
    visible/listing APIs do not expose numeric transportProfileId through read-only detail
    endpoints. It still preserves every old Transport Profile row from the listing.
    """
    enriched: List[Dict[str, Any]] = _apply_known_transport_profile_ids(records, previous_values)
    audit: List[Dict[str, Any]] = []
    headers: Dict[str, Any] = {}
    for inter in interactions:
        h = inter.get("request_headers_compact") or {}
        for key in ["x-requester-id", "referer", "origin"]:
            for hk, hv in h.items():
                if str(hk).lower() == key and hv and hk not in headers:
                    headers[hk] = hv
    detail_found = 0
    processed = 0
    skipped_after_probe = 0
    stop_reason = ""
    by_key = _merge_transport_profile_records(enriched)
    limit = max(0, int(max_details or 0))
    candidates_rows = by_key[:limit]
    baseline_ids = sum(1 for r in by_key if r.get("transport_profile_id"))

    async def emit_progress(detail: str) -> None:
        if progress_cb:
            maybe = progress_cb(
                phase="transport_profile_detail_id_enrichment",
                completed=processed,
                total=len(candidates_rows),
                detail=detail,
                counts={
                    "old_transport_profiles": len(by_key),
                    "transport_profile_ids_found": sum(1 for r in by_key if r.get("transport_profile_id")),
                    "detail_found_this_run": detail_found,
                },
            )
            if asyncio.iscoroutine(maybe):
                await maybe

    for row in candidates_rows:
        processed += 1
        await emit_progress(f"checking {processed}/{len(candidates_rows)} {row.get('transport_profile_name') or row.get('transport_profile_identifier') or ''}")
        if row.get("transport_profile_id"):
            continue
        # Probe mode: once enough rows have been tested and no detail IDs were found,
        # stop wasting time on the remaining 200+ Transport Profiles. This keeps the run moving
        # and records the limitation clearly in transport_profile_id_completion_report.json.
        if stop_if_probe_finds_no_ids and processed > max(1, int(probe_rows or 0)) and detail_found == 0:
            skipped_after_probe = max(0, len(candidates_rows) - processed + 1)
            stop_reason = (
                f"Stopped detail enrichment after probe_rows={probe_rows}: no numeric transportProfileId was exposed by "
                "the attempted read-only detail endpoints. Listing inventory was kept; only numeric IDs remain blank."
            )
            break
        candidates = _candidate_transport_profile_detail_urls(row, interactions)[: max(1, int(max_candidate_urls_per_transport_profile or 1))]
        row_audit = {
            "transport_profile_name": row.get("transport_profile_name"),
            "transport_profile_identifier": row.get("transport_profile_identifier"),
            "attempts": [],
            "resolved": False,
            "candidate_url_count_used": len(candidates),
        }
        for url in candidates:
            body, meta = await _fetch_json_with_page(page, url, headers=headers, timeout_ms=timeout_ms)
            meta = mask_sensitive_data({**meta, "source": "transport_profile_detail_id_enrichment"})
            recs = extract_transport_profile_records_from_payload(body, source_url=url, source="api_detail_enrichment") if body is not None else []
            matched = None
            for rec in recs:
                if _record_matches_transport_profile(rec, row):
                    matched = rec
                    break
            if matched is None and len(recs) == 1:
                matched = recs[0]
            meta["transport_profile_rows_extracted"] = len(recs)
            meta["matched"] = bool(matched)
            meta["transport_profile_id_found"] = bool(matched and matched.get("transport_profile_id"))
            row_audit["attempts"].append(meta)
            if matched:
                merged = _merge_transport_profile_records([row, matched])[0]
                row.update(merged)
                if matched.get("transport_profile_id"):
                    row["transport_profile_id_source"] = "api_detail_enrichment"
                    row_audit["resolved"] = True
                    detail_found += 1
                    break
        audit.append(row_audit)
        # Checkpoint every five rows so a stopped run still has useful output.
        if kb_dir and (processed % 5 == 0 or processed == len(candidates_rows)):
            try:
                _write_json(kb_dir / "old_transport_profiles_inventory_with_ids.checkpoint.json", by_key)
                _write_json(kb_dir / "transport_profile_detail_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    merged_records = _merge_transport_profile_records(by_key)
    with_ids = sum(1 for r in merged_records if r.get("transport_profile_id"))
    report = {
        "total_old_transport_profiles": len(merged_records),
        "transport_profile_ids_found": with_ids,
        "transport_profile_ids_missing": max(0, len(merged_records) - with_ids),
        "detail_enrichment_processed": processed,
        "detail_enrichment_found_ids": detail_found,
        "known_prior_ids_applied": sum(1 for r in merged_records if r.get("transport_profile_id_source") == "known_prior_context"),
        "baseline_ids_before_detail_enrichment": baseline_ids,
        "detail_timeout_ms": timeout_ms,
        "max_candidate_urls_per_transport_profile": max_candidate_urls_per_transport_profile,
        "probe_rows": probe_rows,
        "skipped_after_probe": skipped_after_probe,
        "stop_reason": stop_reason,
        "completion_percent": round((with_ids / len(merged_records) * 100), 2) if merged_records else 0.0,
        "note": "Blank transport_profile_id means the visible/listing API did not expose numeric transportProfileId and no read-only detail endpoint returned it during this run.",
    }
    if kb_dir:
        try:
            _write_json(kb_dir / "old_transport_profiles_inventory_with_ids.checkpoint.json", merged_records)
            _write_json(kb_dir / "transport_profile_detail_enrichment_audit.checkpoint.json", audit)
            _write_json(kb_dir / "transport_profile_id_completion_report.checkpoint.json", report)
        except Exception:
            pass
    return merged_records, audit, report



def _extract_numeric_transport_profile_id_from_url_or_payload(value: Any) -> str:
    """Extract a plausible numeric Transport Profile id from a URL/payload."""
    if value in (None, ""):
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    low = text.lower()
    if not any(h in low for h in ["transport_profile", "transport_profileid", "transport_profile_id", "transport_profiles"]):
        return ""
    patterns = [
        r"(?:transportProfileId|transport_profile_id)[\"'=:\s]+(\d{1,10})",
        # Some Transport Profile detail payloads use a generic id field while the same body still
        # contains transport_profileName/transport_profileCondition/transport_profileAction. The transport_profile-word guard above keeps
        # this from picking unrelated IDs.
        r"[\"]id[\"]\s*:\s*(\d{1,10})",
        r"/(?:transport[_-]?profile|transport[_-]?profiles|transportprofiles|transportprofile|transport[_-]?profile-details?)/(?:detail/|details/|edit/|view/)?(\d{1,10})(?:[/?#]|$)",
        r"/(?:detail|details|edit|view)/(\d{1,10})(?:[/?#]|$)",
        r"[?&](?:transportProfileId|transport_profile_id|id)=(\d{1,10})(?:&|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


async def _find_transport_profile_listing_search(page: Page) -> Optional[Locator]:
    """Find the visible Transport Profiles list search box, avoiding Filter buttons."""
    candidates = [
        "input[placeholder*='Search' i]",
        "input[aria-label*='Search' i]",
        "input[type='search']",
        "[role='searchbox']",
        "input:not([type=hidden])",
    ]
    for sel in candidates:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for idx in range(min(count, 12)):
                loc = locs.nth(idx)
                try:
                    if not await loc.is_visible(timeout=600):
                        continue
                    attrs = await loc.evaluate("""el => ({
                        tag:(el.tagName||'').toLowerCase(), type:el.getAttribute('type')||'',
                        placeholder:el.getAttribute('placeholder')||'', aria:el.getAttribute('aria-label')||'',
                        id:el.id||'', name:el.getAttribute('name')||'', text:(el.innerText||el.value||'')
                    })""")
                    label = " ".join(str(attrs.get(k) or "") for k in ["placeholder", "aria", "id", "name", "text"]).lower()
                    if any(bad in label for bad in ["filter button", "clear", "close"]):
                        continue
                    if "search" in label or str(attrs.get("type") or "").lower() in {"search", "text", ""}:
                        return loc
                except Exception:
                    continue
        except Exception:
            continue
    return None


async def _set_transport_profile_listing_search(page: Page, value: str) -> bool:
    loc = await _find_transport_profile_listing_search(page)
    if loc is None:
        return False
    if semantic_runtime_enabled(page):
        try:
            selector = await loc.evaluate("el => el.id ? ('#' + CSS.escape(el.id)) : ''")
            if not selector:
                return False
            if not await dds_set_text_control(page, None, selector, value, phase="transport_profile"):
                return False
            session = getattr(page, "_hip_browser_session", None)
            if session is not None and hasattr(session, "press_and_log"):
                await session.press_and_log(locator=page.locator(selector).first, key="Enter", selector=selector)
            await page.wait_for_timeout(900)
            return True
        except Exception:
            return False
    try:
        await loc.click(timeout=1500)
        # DDS/Angular searches usually react to Ctrl+A + type + Enter/change.
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await loc.fill(value, timeout=2500)
        await loc.dispatch_event("input")
        await loc.dispatch_event("change")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(900)
        return True
    except Exception:
        try:
            selector = await loc.evaluate("el => el.id ? ('#' + CSS.escape(el.id)) : ''")
            if selector:
                return await _set_control_value(page, selector, value)
        except Exception:
            pass
    return False


async def _clear_transport_profile_listing_search(page: Page) -> None:
    loc = await _find_transport_profile_listing_search(page)
    if loc is None:
        return
    if semantic_runtime_enabled(page):
        try:
            selector = await loc.evaluate("el => el.id ? ('#' + CSS.escape(el.id)) : ''")
            if not selector:
                return
            if not await dds_set_text_control(page, None, selector, "", phase="transport_profile"):
                return
            session = getattr(page, "_hip_browser_session", None)
            if session is not None and hasattr(session, "press_and_log"):
                await session.press_and_log(locator=page.locator(selector).first, key="Enter", selector=selector)
            await page.wait_for_timeout(500)
            return
        except Exception:
            return
    try:
        await loc.click(timeout=1000)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await loc.dispatch_event("input")
        await loc.dispatch_event("change")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
    except Exception:
        pass


async def _reset_transport_profiles_listing_for_row_learning(page: Page, *, reason: str = "") -> bool:
    """Put the Transport Profiles listing back into a clean searchable state.

    The Transport Profiles grid can keep an expanded row/drawer/menu focused after a row-action
    attempt. When that happens the next iteration may not find the search box, or
    the only row button may be "Collapse the row". A bounded hard reset is safer
    than continuing and marking the Transport Profile as missing. It only navigates back to the
    read-only Transport Profiles listing; it never clicks Save/Create/Submit/Delete.
    """
    try:
        await _close_transport_profile_transient_surfaces(page)
    except Exception:
        pass
    try:
        if not _urls_same_path(page.url, TRANSPORT_PROFILES_URL):
            await page.goto(TRANSPORT_PROFILES_URL, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(1200)
        search = await _find_transport_profile_listing_search(page)
        if search is None:
            await page.goto(TRANSPORT_PROFILES_URL, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(1500)
        await _clear_transport_profile_listing_search(page)
        return True
    except Exception:
        return False


async def _click_safe_transport_profile_row_action(page: Page, row_hint: str) -> Dict[str, Any]:
    """Open one row's read-only/detail/edit action and return click metadata.

    This intentionally skips Save/Create/Submit/Delete style buttons.  It first tries
    row-scoped overflow/action buttons, then global menu items opened by the row.
    """
    js_find_row = r"""
({hint}) => {
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 7) {
      let part = el.tagName.toLowerCase();
      if (el.id) { part += '#' + CSS.escape(el.id); parts.unshift(part); break; }
      const cls = (el.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.');
      if (cls) part += '.' + cls;
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(x => x.tagName === el.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(el)+1) + ')';
      }
      parts.unshift(part); el = parent;
    }
    return parts.join(' > ');
  }
  const h = String(hint || '').toLowerCase().trim();
  const rowSels = ['table tbody tr','[role=row]','.dds__data-table__row','.dds__table tr','.dds__card','.customCard','dds-table-body-row','[class*=row]'];
  const candidates = [];
  for (const sel of rowSels) {
    for (const row of Array.from(document.querySelectorAll(sel))) {
      const r = row.getBoundingClientRect ? row.getBoundingClientRect() : {width:0,height:0};
      const text = (row.innerText || row.textContent || '').trim().replace(/\s+/g,' ');
      const lower = text.toLowerCase();
      if (!text || !r.width || !r.height || !lower.includes(h)) continue;
      const cells = Array.from(row.querySelectorAll('td,th,[role=cell],[class*=td],dds-table-cell')).map(c => (c.innerText || c.textContent || '').trim().replace(/\s+/g,' ')).filter(Boolean);
      const cellLowers = cells.map(c => c.toLowerCase());
      let score = 10;
      if (cellLowers.some(c => c === h)) score += 120;
      if (lower === h || lower.startsWith(h + ' ')) score += 90;
      if (new RegExp('(^|\\s)' + h.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(\\s|$)').test(lower)) score += 45;
      // Avoid selecting TESTEY_<transport_profile> when the requested transport_profile is <transport_profile>.
      if (!lower.startsWith(h + ' ') && lower.includes('testey_' + h)) score -= 80;
      if (!lower.startsWith(h + ' ') && lower.includes('test_' + h)) score -= 40;
      const buttons = Array.from(row.querySelectorAll('button,a,[role=button]')).map((el, idx) => ({
        index:idx, selector:cssPath(el), text:(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim().replace(/\s+/g,' '),
        classes:(el.className||'').toString(), href:el.href || ''
      }));
      candidates.push({score, rowSelector: cssPath(row), rowText: text.slice(0,700), buttons});
    }
  }
  candidates.sort((a,b) => b.score - a.score);
  return candidates[0] || null;
}
"""
    info = await page.evaluate(js_find_row, {"hint": row_hint})
    if not info:
        return {"clicked": False, "reason": "row_not_found", "row_hint": row_hint}
    buttons = info.get("buttons") or []
    # Treat DDS table row expansion as a safe read-only action.
    # In the live Transport Profiles grid the only row-scoped button may be labelled
    # "Expand the row"; previous builds skipped it, so the real detail area
    # and any Edit/View action revealed after expansion were never inspected.
    safe_words = ["view", "detail", "details", "edit", "open", "show", "expand", "expanded", "collapse", "collapsed"]
    overflow_words = ["more", "actions", "option", "menu", "ellipsis", "overflow", "three", "kebab"]
    unsafe_words = ["delete", "remove", "save", "submit", "create", "add", "disable", "enable", "archive"]

    def score_button(b: Dict[str, Any]) -> int:
        text = " ".join(str(b.get(k) or "") for k in ["text", "classes", "href"]).lower()
        if any(w in text for w in unsafe_words):
            return -100
        if any(w in text for w in safe_words):
            return 20
        if any(w in text for w in overflow_words) or not str(b.get("text") or "").strip():
            return 10
        return 0

    ordered = sorted([b for b in buttons if b.get("selector")], key=score_button, reverse=True)

    async def capture_expanded_snapshot() -> str:
        try:
            return str(await page.evaluate(r"""
({hint}) => {
  const h = String(hint || '').toLowerCase();
  const candidates = Array.from(document.querySelectorAll('[aria-expanded="true"], .dds__tr, .dds__tbody, dds-table-body-row, [role=row], [class*=expanded], [class*=detail], [class*=accordion]'));
  for (const el of candidates) {
    const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g,' ');
    if (text && text.toLowerCase().includes(h)) return text.slice(0, 1800);
  }
  return '';
}
""", {"hint": row_hint}) or "")
        except Exception:
            return ""

    clicked_direct = None
    # If the row already shows "Collapse the row", it is already expanded. Do
    # not click it (that would close the details panel and lose evidence). Treat
    # the current expanded state as a successful read-only open.
    for b in ordered[:6]:
        text_blob = " ".join(str(b.get(k) or "") for k in ["text", "classes", "href"]).lower()
        if "collapse" in text_blob and b.get("selector"):
            expanded_snapshot = await capture_expanded_snapshot()
            return {
                "clicked": True,
                "already_expanded": True,
                "row_hint": row_hint,
                "row": info,
                "row_action_clicked": b,
                "menu_action_clicked": None,
                "expanded_snapshot": expanded_snapshot,
                "url_after_click": page.url,
            }

    for b in ordered[:6]:
        if score_button(b) <= 0:
            continue
        try:
            loc = page.locator(b.get("selector")).first
            if await loc.is_visible(timeout=800) and await loc.is_enabled(timeout=800):
                if semantic_runtime_enabled(page):
                    if not await open_control_for_discovery(page, str(b.get("selector") or ""), label="Transport Profile row action", phase="transport_profile"):
                        continue
                else:
                    await loc.click(timeout=2500)
                clicked_direct = b
                # DDS expandable rows render details asynchronously. Give the
                # expanded area a short bounded wait before looking for any
                # newly revealed View/Edit/Details action.
                await page.wait_for_timeout(900)
                break
        except Exception:
            continue
    if clicked_direct is None:
        return {"clicked": False, "reason": "no_safe_row_action", "row_hint": row_hint, "row": info}

    direct_text = " ".join(str(clicked_direct.get(k) or "") for k in ["text", "classes", "href"]).lower()
    direct_is_expander = any(w in direct_text for w in ["expand", "expanded", "row-expand", "row expanded", "show details"])
    direct_is_overflow = any(w in direct_text for w in overflow_words) and not any(w in direct_text for w in ["expand", "expanded", "collapse", "collapsed"])
    # IMPORTANT: Do not return immediately after clicking an Expand-row control.
    # Many Transport Profiles only expose transportProfileId/conditions/actions after the row expands and
    # then reveals a View/Edit/Details menu item.  A previous patch treated every
    # non-overflow click as terminal, which dropped coverage from 261/283 to
    # 111/283.  Only terminal View/Edit/Details/Open clicks should return here;
    # Expand clicks must fall through to the safe menu/action scan below.
    if not direct_is_overflow and not direct_is_expander:
        expanded_snapshot = await capture_expanded_snapshot()
        return {"clicked": True, "row_hint": row_hint, "row": info, "row_action_clicked": clicked_direct, "menu_action_clicked": None, "expanded_snapshot": expanded_snapshot, "url_after_click": page.url}

    # If an overflow menu opened, or an expandable row revealed inline actions,
    # choose a safe read-only/edit action from the menu/details area.
    menu_clicked = None
    try:
        options = await page.evaluate(r"""
() => {
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 7) {
      let part = el.tagName.toLowerCase();
      if (el.id) { part += '#' + CSS.escape(el.id); parts.unshift(part); break; }
      const cls = (el.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.');
      if (cls) part += '.' + cls;
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(x => x.tagName === el.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(el)+1) + ')';
      }
      parts.unshift(part); el = parent;
    }
    return parts.join(' > ');
  }
  const sels = ['[role=menuitem]','.dds__dropdown__item','.dds__menu__item','li button','li a','button','a[role=button]'];
  const out = [];
  for (const sel of sels) {
    for (const el of Array.from(document.querySelectorAll(sel)).slice(0,200)) {
      const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {width:0,height:0};
      const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().replace(/\s+/g,' ');
      if (text && r.width && r.height) out.push({selector:cssPath(el), text, classes:(el.className||'').toString()});
    }
  }
  return out;
}
""")
        for opt in options or []:
            t = " ".join(str(opt.get(k) or "") for k in ["text", "classes"]).lower()
            if any(w in t for w in unsafe_words) or any(w in t for w in ["navigation", "side-nav", "side nav", "breadcrumb"]):
                continue
            if any(w in t for w in safe_words):
                loc = page.locator(opt.get("selector")).first
                if await loc.is_visible(timeout=800) and await loc.is_enabled(timeout=800):
                    if semantic_runtime_enabled(page):
                        if not await open_control_for_discovery(page, str(opt.get("selector") or ""), label="Transport Profile row menu action", phase="transport_profile"):
                            continue
                    else:
                        await loc.click(timeout=2500)
                    menu_clicked = opt
                    await page.wait_for_timeout(1200)
                    break
    except Exception:
        pass
    expanded_snapshot = await capture_expanded_snapshot()
    return {"clicked": True, "row_hint": row_hint, "row": info, "row_action_clicked": clicked_direct, "menu_action_clicked": menu_clicked, "expanded_snapshot": expanded_snapshot, "url_after_click": page.url}


async def _learn_old_transport_profile_ids_from_ui_row_actions(
    page: Page,
    browser: BrowserSession,
    records: List[Dict[str, Any]],
    previous_values: Dict[str, Any],
    *,
    max_rows: int = 250,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Repeat the manually validated UI approach for old Transport Profiles.

    This mirrors the Partner/System strategy: use the live UI row, click the safe row
    action, capture the API that the UI itself triggers, then parse IDs from URL/body.
    It never clicks Save/Create/Submit/Delete.  It is checkpointed so a stopped run
    still preserves useful IDs/evidence.
    """
    rows = _merge_transport_profile_records(_apply_known_transport_profile_ids(records, previous_values))
    limit = min(len(rows), max(0, int(max_rows or 0)))
    audit: List[Dict[str, Any]] = []
    ids_before = sum(1 for r in rows if r.get("transport_profile_id"))
    found_this_phase = 0

    async def emit(i: int, detail: str) -> None:
        if progress_cb:
            maybe = progress_cb(
                phase="transport_profile_ui_row_action_id_learning",
                completed=i,
                total=max(1, limit),
                detail=detail,
                counts={"old_transport_profiles": len(rows), "transport_profile_ids_found": sum(1 for r in rows if r.get("transport_profile_id")), "ui_ids_found": found_this_phase},
            )
            if asyncio.iscoroutine(maybe):
                await maybe

    if limit <= 0:
        return rows, audit, {"ui_rows_attempted": 0, "ui_ids_found": 0, "note": "No old Transport Profile rows available for UI row-action learning."}

    for idx, row in enumerate(rows[:limit], start=1):
        hint = str(row.get("transport_profile_name") or row.get("transport_profile_identifier") or row.get("root_element") or "").strip()
        if not hint:
            continue
        await emit(idx, f"UI row detail learning {idx}/{limit}: {hint[:80]}")
        if row.get("transport_profile_id") and row.get("transport_profile_id_source") == "known_prior_context":
            audit.append({"transport_profile_name": row.get("transport_profile_name"), "skipped": True, "reason": "already_has_known_transport_profile_id"})
            continue
        before_count = len(browser.network_tab_events)
        searched = await _set_transport_profile_listing_search(page, hint)
        click_info: Dict[str, Any] = {"clicked": False, "reason": "search_failed"}
        if searched:
            try:
                click_info = await asyncio.wait_for(_click_safe_transport_profile_row_action(page, hint), timeout=10)
            except Exception as exc:
                click_info = {"clicked": False, "reason": f"ui_click_error: {exc}", "row_hint": hint}

        # Retry once from a clean listing state. This is the critical parity fix
        # with Document Type deep learning: a single transient expanded row/drawer
        # must not permanently mark the next Transport Profile as unresolved.
        retry_reasons = {"search_failed", "row_not_found", "no_safe_row_action"}
        if not click_info.get("clicked") and str(click_info.get("reason") or "") in retry_reasons:
            await _reset_transport_profiles_listing_for_row_learning(page, reason=str(click_info.get("reason") or ""))
            before_count = len(browser.network_tab_events)
            searched = await _set_transport_profile_listing_search(page, hint)
            click_info = {"clicked": False, "reason": "search_failed_after_reset"}
            if searched:
                try:
                    click_info = await asyncio.wait_for(_click_safe_transport_profile_row_action(page, hint), timeout=12)
                    if not click_info.get("clicked"):
                        click_info["retry_after_listing_reset"] = True
                except Exception as exc:
                    click_info = {"clicked": False, "reason": f"ui_click_error_after_reset: {exc}", "row_hint": hint, "retry_after_listing_reset": True}
            else:
                click_info["retry_after_listing_reset"] = True

        await page.wait_for_timeout(1100)
        new_events = browser.network_tab_events[before_count:]
        interactions, api_rows = collect_transport_profile_api_interactions(new_events, stage_label="transport_profile_ui_row_action_detail")
        matched = None
        for rec in api_rows:
            if _record_matches_transport_profile(rec, row):
                matched = rec
                break
        if matched is None and len(api_rows) == 1:
            matched = api_rows[0]
        url_transport_profile_id = _extract_numeric_transport_profile_id_from_url_or_payload(page.url)
        event_url_transport_profile_id = ""
        for ev in new_events:
            d = _event_dict(ev)
            event_url_transport_profile_id = _extract_numeric_transport_profile_id_from_url_or_payload(d.get("url")) or event_url_transport_profile_id
            event_url_transport_profile_id = _extract_numeric_transport_profile_id_from_url_or_payload(d.get("response_body_redacted") or d.get("response_body_text_redacted")) or event_url_transport_profile_id
            if event_url_transport_profile_id:
                break
        row_audit = {
            "transport_profile_name": row.get("transport_profile_name"),
            "transport_profile_identifier": row.get("transport_profile_identifier"),
            "search_used": searched,
            "click": mask_sensitive_data(click_info),
            "network_events_after_click": len(new_events),
            "api_interactions_after_click": len(interactions),
            "api_rows_after_click": len(api_rows),
            "url_after_click": mask_sensitive_string(page.url),
            "url_transport_profile_id_candidate": url_transport_profile_id,
            "event_transport_profile_id_candidate": event_url_transport_profile_id,
            "resolved": False,
        }
        if matched:
            merged = _merge_transport_profile_records([row, matched])[0]
            row.update(merged)
        if not row.get("transport_profile_id"):
            expanded_text = ""
            try:
                expanded_text = str((click_info or {}).get("expanded_snapshot") or "")
            except Exception:
                expanded_text = ""
            expanded_transport_profile_id = _extract_numeric_transport_profile_id_from_url_or_payload(expanded_text)
            candidate = event_url_transport_profile_id or url_transport_profile_id or expanded_transport_profile_id
            if candidate:
                row["transport_profile_id"] = candidate
                row["transport_profile_id_source"] = "ui_row_action_network_or_url_or_expanded_dom"
            if expanded_text:
                row["ui_expanded_detail_text_compact"] = expanded_text[:1200]
        if row.get("transport_profile_id") and not row_audit["resolved"]:
            row_audit["resolved"] = True
            if str(row.get("transport_profile_id_source") or "").startswith("ui_row_action") or (matched and matched.get("transport_profile_id")):
                found_this_phase += 1
        row_audit["api_interactions"] = interactions[:10]
        audit.append(row_audit)
        # Return to the list and clear search so the next row starts clean.
        try:
            await _close_transport_profile_transient_surfaces(page)
            if not _urls_same_path(page.url, TRANSPORT_PROFILES_URL):
                await page.go_back(timeout=4000)
                await page.wait_for_timeout(800)
        except Exception:
            pass
        await _clear_transport_profile_listing_search(page)
        # Make every iteration independent; this keeps long Transport Profiles runs from
        # alternating between good rows and search failures as expanded rows stack.
        if idx % 10 == 0:
            await _reset_transport_profiles_listing_for_row_learning(page, reason="periodic_cleanup")
        if kb_dir and (idx % 5 == 0 or idx == limit):
            try:
                _write_json(kb_dir / "old_transport_profiles_inventory_with_ids.ui_checkpoint.json", rows)
                _write_json(kb_dir / "transport_profile_ui_row_action_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    # Final rescue pass for Transport Profiles that did not resolve on the first sweep.
    # This reloads the listing before each unresolved row and tries exact row matching again.
    # It specifically fixes long-run grid drift where early rows or similarly named
    # TESTEY_* rows can poison later searches. It is read-only and uses the same safe
    # row expansion/detail action; it never clicks Save/Create/Submit/Delete.
    unresolved_positions = [i for i, r in enumerate(rows[:limit]) if not r.get("transport_profile_id")]
    if unresolved_positions:
        await _reset_transport_profiles_listing_for_row_learning(page, reason="final_unresolved_rescue_start")
    for rescue_idx, pos in enumerate(unresolved_positions, start=1):
        row = rows[pos]
        hint = str(row.get("transport_profile_name") or row.get("transport_profile_identifier") or row.get("root_element") or "").strip()
        if not hint or row.get("transport_profile_id"):
            continue
        await emit(pos + 1, f"UI row final rescue {rescue_idx}/{len(unresolved_positions)}: {hint[:80]}")
        await _reset_transport_profiles_listing_for_row_learning(page, reason="final_unresolved_rescue_each_row")
        before_count = len(browser.network_tab_events)
        searched = await _set_transport_profile_listing_search(page, hint)
        click_info: Dict[str, Any] = {"clicked": False, "reason": "final_rescue_search_failed", "final_rescue_pass": True}
        if searched:
            try:
                click_info = await asyncio.wait_for(_click_safe_transport_profile_row_action(page, hint), timeout=14)
                click_info["final_rescue_pass"] = True
            except Exception as exc:
                click_info = {"clicked": False, "reason": f"final_rescue_ui_click_error: {exc}", "row_hint": hint, "final_rescue_pass": True}
        await page.wait_for_timeout(1400)
        new_events = browser.network_tab_events[before_count:]
        interactions, api_rows = collect_transport_profile_api_interactions(new_events, stage_label="transport_profile_ui_row_action_detail_final_rescue")
        matched = None
        for rec in api_rows:
            if _record_matches_transport_profile(rec, row):
                matched = rec
                break
        if matched is None and len(api_rows) == 1:
            matched = api_rows[0]
        url_transport_profile_id = _extract_numeric_transport_profile_id_from_url_or_payload(page.url)
        event_url_transport_profile_id = ""
        for ev in new_events:
            d = _event_dict(ev)
            event_url_transport_profile_id = _extract_numeric_transport_profile_id_from_url_or_payload(d.get("url")) or event_url_transport_profile_id
            event_url_transport_profile_id = _extract_numeric_transport_profile_id_from_url_or_payload(d.get("response_body_redacted") or d.get("response_body_text_redacted")) or event_url_transport_profile_id
            if event_url_transport_profile_id:
                break
        rescue_audit = {
            "transport_profile_name": row.get("transport_profile_name"),
            "transport_profile_identifier": row.get("transport_profile_identifier"),
            "search_used": searched,
            "click": mask_sensitive_data(click_info),
            "network_events_after_click": len(new_events),
            "api_interactions_after_click": len(interactions),
            "api_rows_after_click": len(api_rows),
            "url_after_click": mask_sensitive_string(page.url),
            "url_transport_profile_id_candidate": url_transport_profile_id,
            "event_transport_profile_id_candidate": event_url_transport_profile_id,
            "resolved": False,
            "final_rescue_pass": True,
        }
        if matched:
            merged = _merge_transport_profile_records([row, matched])[0]
            row.update(merged)
        if not row.get("transport_profile_id"):
            expanded_text = str((click_info or {}).get("expanded_snapshot") or "")
            expanded_transport_profile_id = _extract_numeric_transport_profile_id_from_url_or_payload(expanded_text)
            candidate = event_url_transport_profile_id or url_transport_profile_id or expanded_transport_profile_id
            if candidate:
                row["transport_profile_id"] = candidate
                row["transport_profile_id_source"] = "ui_row_action_final_rescue_network_or_url_or_expanded_dom"
            if expanded_text:
                row["ui_expanded_detail_text_compact"] = expanded_text[:1200]
        if row.get("transport_profile_id"):
            rescue_audit["resolved"] = True
            found_this_phase += 1
        rescue_audit["api_interactions"] = interactions[:10]
        audit.append(rescue_audit)
        try:
            await _close_transport_profile_transient_surfaces(page)
        except Exception:
            pass
        await _clear_transport_profile_listing_search(page)
        if kb_dir and (rescue_idx % 3 == 0 or rescue_idx == len(unresolved_positions)):
            try:
                _write_json(kb_dir / "old_transport_profiles_inventory_with_ids.ui_checkpoint.json", rows)
                _write_json(kb_dir / "transport_profile_ui_row_action_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    rows = _merge_transport_profile_records(rows)
    with_ids = sum(1 for r in rows if r.get("transport_profile_id"))
    report = {
        "ui_rows_attempted": limit,
        "ui_ids_found_this_phase": found_this_phase,
        "transport_profile_ids_before_ui_phase": ids_before,
        "transport_profile_ids_after_ui_phase": with_ids,
        "transport_profile_ids_missing_after_ui_phase": max(0, len(rows) - with_ids),
        "final_rescue_rows_attempted": len(unresolved_positions),
        "completion_percent_after_ui_phase": round((with_ids / len(rows) * 100), 2) if rows else 0.0,
        "note": "UI row-action phase repeats the real portal interaction: search each old Transport Profile, click safe View/Edit/Details/open row action, capture the API triggered by that click, and parse transportProfileId when the API/URL exposes it.",
    }
    return rows, audit, report


class TransportProfileKBFlow:
    def __init__(self, config: AppConfig, *, transport_profiles_url: str = TRANSPORT_PROFILES_URL, fill_dummy: bool = True, known_transport_profile_id: str | None = None, write_heavy_evidence: bool = False, crawl_old_transport_profiles: bool = True, max_api_pages: int = 250, max_detail_rows: int | None = None, capture_deep_profiles: bool = True, max_deep_profile_rows: int | None = None):
        self.config = config
        self.transport_profiles_url = transport_profiles_url
        self.fill_dummy = fill_dummy
        self.known_transport_profile_id = known_transport_profile_id
        self.write_heavy_evidence = write_heavy_evidence
        self.crawl_old_transport_profiles = crawl_old_transport_profiles
        self.max_api_pages = max_api_pages
        # None means enrich every discovered Transport Profile row.  Earlier builds
        # accidentally used max_api_pages as the detail/UI row cap, which could
        # stop the Transport Profile KB before collecting full row-level information.
        self.max_detail_rows = max_detail_rows
        # Deep profile capture is the third phase: after inventory + ID learning it
        # parses/fetches `/api/transport_profile/{id}/details` so every Transport Profile can
        # carry transportProfileIdentifier/rootElement/schema/attributes/usage evidence.
        self.capture_deep_profiles = capture_deep_profiles
        self.max_deep_profile_rows = max_deep_profile_rows

    async def run(self, ctx: RunContext, input_json: str | None = None, *, browser_session: BrowserSession | None = None) -> Dict[str, Any]:
        run_dir = ctx.run_dir
        kb_dir = run_dir / "transport_profile_kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        input_data = _read_json(input_json)
        previous_values = build_previous_interaction_values(input_data, known_transport_profile_id=self.known_transport_profile_id)
        seed = extract_transport_profile_seed(input_data, phase=input_data.get("_full_dummy_fill_phase") if isinstance(input_data, dict) else None)
        dummy_values = build_dummy_fill_values(seed, exact=bool(input_data.get("_replicate_exact_input_values")))
        warnings: List[str] = []
        status = "completed"
        files: Dict[str, str] = {}

        def progress(phase: str, completed: int = 0, total: int = 0, detail: str = "", counts: Optional[Dict[str, Any]] = None) -> None:
            _write_transport_profile_progress(kb_dir, phase=phase, completed=completed, total=total, detail=detail, counts=counts)

        progress("initializing", 0, 8, "Preparing browser, SSO, Transport Profile API capture and output folders")
        async with browser_session_scope(self.config, run_dir, existing=browser_session, phase_name=run_dir.name) as browser:
            page = await browser.start() if browser.page is None else browser.page
            browser.set_stage("transport_profile_kb_login")
            progress("login", 1, 8, "Opening browser and waiting for Dell SSO/Transport Profiles page")
            self.config.portal.base_url = self.transport_profiles_url
            await browser.goto_base_and_complete_sso(self.transport_profiles_url)
            browser.set_stage("transport_profile_kb_open_transport_profiles")
            progress("open_transport_profiles", 2, 8, "Opening Transport Profiles link and waiting for page/API readiness")
            # Do not blindly re-navigate when SSO already landed on Transport Profiles.
            # A second immediate navigation can abort Angular remoteEntry/chunk loading and leave a blank page.
            if not _urls_same_path(page.url, self.transport_profiles_url):
                await browser.navigate(self.transport_profiles_url)
            else:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
            ready = await _wait_for_transport_profile_listing_ready(page, browser, self.transport_profiles_url, warnings)
            progress("transport_profile_page_ready", 3, 8, f"Transport Profiles ready={ready}; capturing listing/API evidence")
            await browser.save_dom_snapshot("transport_profiles_listing_before_add")
            await page.wait_for_timeout(1000)
            add_api_interactions: List[Dict[str, Any]] = []
            listing_buttons = await _evaluate_buttons(page)
            listing_ui_rows = await _collect_ui_transport_profile_rows(page)
            listing_api_interactions, old_transport_profiles = collect_transport_profile_api_interactions(browser.network_tab_events, stage_label="transport_profile_listing_load")
            # DOM grid fallback: when network/API capture returns rows=0 but the page visibly
            # contains Transport Profile rows, seed inventory from the visible grid immediately.
            # This keeps the run moving into UI row-action ID/deep-profile learning.
            ui_seed_transport_profiles = _transport_profile_records_from_ui_rows(listing_ui_rows)
            if ui_seed_transport_profiles:
                old_transport_profiles = _merge_transport_profile_records([*old_transport_profiles, *ui_seed_transport_profiles])
            if self.crawl_old_transport_profiles and not old_transport_profiles:
                browser.set_stage("transport_profile_kb_direct_summary_api_fallback")
                effective_direct_pages = max(1, min(int(self.max_api_pages or 1), 8))
                progress("direct_summary_api_fallback", 0, effective_direct_pages, "No list rows from network yet; calling read-only Transport Profile summary API")
                direct_rows, direct_interactions, direct_audit = await _direct_fetch_transport_profile_summary(page, max_pages=effective_direct_pages)
                if direct_interactions:
                    listing_api_interactions.extend(direct_interactions)
                    old_transport_profiles = _merge_transport_profile_records([*old_transport_profiles, *direct_rows])
                    # Preserve these calls in the pagination audit so failures are visible in the upload summary.
                    pagination_audit_fallback_seed = direct_audit
                else:
                    pagination_audit_fallback_seed = []
            else:
                pagination_audit_fallback_seed = []
            pagination_audit: List[Dict[str, Any]] = list(pagination_audit_fallback_seed)
            # Full UI pagination fallback: the Transport Profiles listing can expose
            # `1 - 10 of 659 items` while API/network replay only gives the first 10.
            # Crawl the read-only grid pages now so the downstream ID/deep-profile
            # phases work on the full inventory, not just the first visible page.
            if self.crawl_old_transport_profiles:
                browser.set_stage("transport_profile_kb_ui_pagination_inventory")
                effective_ui_pages = max(1, min(int(self.max_api_pages or 1), 300))
                progress("old_transport_profile_ui_pagination", len(old_transport_profiles), max(1, len(old_transport_profiles)), "Crawling visible Transport Profile grid pages when API pagination is incomplete")
                ui_page_transport_profiles, ui_page_audit = await _crawl_old_transport_profile_inventory_from_ui_pagination(page, max_pages=effective_ui_pages, progress_cb=progress)
                if ui_page_audit:
                    pagination_audit.extend(ui_page_audit)
                if ui_page_transport_profiles:
                    old_transport_profiles = _merge_transport_profile_records([*old_transport_profiles, *ui_page_transport_profiles])
            paginated_transport_profiles: List[Dict[str, Any]] = []
            detail_enrichment_audit: List[Dict[str, Any]] = []
            detail_enrichment_report: Dict[str, Any] = {}
            ui_row_action_audit: List[Dict[str, Any]] = []
            ui_row_action_report: Dict[str, Any] = {}
            deep_profile_audit: List[Dict[str, Any]] = []
            deep_profile_report: Dict[str, Any] = {}
            if self.crawl_old_transport_profiles and (listing_api_interactions or old_transport_profiles):
                browser.set_stage("transport_profile_kb_old_transport_profile_api_pagination")
                effective_crawl_pages = max(1, min(int(self.max_api_pages or 1), 250 if old_transport_profiles else 8))
                progress("old_transport_profile_api_pagination", 0, effective_crawl_pages, f"Replaying observed listing/pagination APIs; rows={len(old_transport_profiles)}")
                paginated_transport_profiles, replay_audit = await _crawl_old_transport_profile_inventory_from_apis(page, listing_api_interactions, max_pages=effective_crawl_pages)
                pagination_audit.extend(replay_audit)
                old_transport_profiles = _merge_transport_profile_records([*old_transport_profiles, *paginated_transport_profiles])
                browser.set_stage("transport_profile_kb_old_transport_profile_detail_id_enrichment")
                detail_limit = min(len(old_transport_profiles), int(self.max_detail_rows)) if self.max_detail_rows is not None else len(old_transport_profiles)
                progress("transport_profile_detail_id_enrichment", 0, max(1, detail_limit), f"Trying read-only detail lookups for numeric transportProfileIds; old_transport_profiles={len(old_transport_profiles)}; detail_limit={detail_limit}")
                old_transport_profiles, detail_enrichment_audit, detail_enrichment_report = await _enrich_old_transport_profiles_with_detail_apis(
                    page, old_transport_profiles, listing_api_interactions, previous_values, max_details=detail_limit, kb_dir=kb_dir, progress_cb=progress
                )
                progress("transport_profile_detail_id_enrichment_done", detail_limit, max(1, detail_limit), f"Detail enrichment done; transportProfileIds={detail_enrichment_report.get('transport_profile_ids_found', 0)}/{detail_enrichment_report.get('total_old_transport_profiles', len(old_transport_profiles))}")
                # Second phase: repeat the real UI row/action detail flow, like Partner/System discovery.
                # This is the important phase for portals where the list API hides numeric transportProfileId.
                browser.set_stage("transport_profile_kb_ui_row_action_id_learning")
                max_ui_rows = detail_limit
                progress("transport_profile_ui_row_action_id_learning", 0, max(1, max_ui_rows), "Learning old Transport Profile IDs from real UI row actions/details")
                old_transport_profiles, ui_row_action_audit, ui_row_action_report = await _learn_old_transport_profile_ids_from_ui_row_actions(
                    page, browser, old_transport_profiles, previous_values, max_rows=max_ui_rows, kb_dir=kb_dir, progress_cb=progress
                )
                progress("transport_profile_ui_row_action_id_learning_done", max_ui_rows, max(1, max_ui_rows), f"UI row-action ID learning done; transportProfileIds={ui_row_action_report.get('transport_profile_ids_after_ui_phase', 0)}/{len(old_transport_profiles)}")
                # Merge both API and UI completion reports into the main report used by summaries.
                with_ids_after_ui = sum(1 for r in old_transport_profiles if r.get("transport_profile_id"))
                detail_enrichment_report.update({
                    "ui_row_action_phase": ui_row_action_report,
                    "transport_profile_ids_found": with_ids_after_ui,
                    "transport_profile_ids_missing": max(0, len(old_transport_profiles) - with_ids_after_ui),
                    "completion_percent": round((with_ids_after_ui / len(old_transport_profiles) * 100), 2) if old_transport_profiles else 0.0,
                })
                if self.capture_deep_profiles:
                    browser.set_stage("transport_profile_kb_deep_profile_enrichment")
                    deep_limit = min(len(old_transport_profiles), int(self.max_deep_profile_rows)) if self.max_deep_profile_rows is not None else len(old_transport_profiles)
                    progress("transport_profile_deep_profile_enrichment", 0, max(1, deep_limit), f"Capturing full Transport Profile profiles from details API; old_transport_profiles={len(old_transport_profiles)}; deep_limit={deep_limit}")
                    old_transport_profiles, deep_profile_audit, deep_profile_report = await _capture_transport_profile_deep_profiles(
                        page, browser, old_transport_profiles, [*listing_api_interactions, *pagination_audit], max_profiles=deep_limit, kb_dir=kb_dir, progress_cb=progress
                    )
                    progress("transport_profile_deep_profile_enrichment_done", deep_limit, max(1, deep_limit), f"Deep profile capture done; profiles={deep_profile_report.get('deep_profiles_captured', 0) + deep_profile_report.get('deep_profiles_partial', 0)}/{deep_profile_report.get('total_transport_profiles', len(old_transport_profiles))}")
            else:
                old_transport_profiles = _apply_known_transport_profile_ids(old_transport_profiles, previous_values)
            # UI fallback rows are stored as evidence even when they cannot be normalized to IDs.
            progress("find_add_button", 4, 8, "Looking for + Add button after inventory capture")
            add = await _find_add_button(page)
            add_form_opened = False
            if add is None:
                status = "partial_success"
                warnings.append("Could not locate + Add button. Saved old Transport Profile listing/API KB only.")
            else:
                browser.set_stage("transport_profile_kb_click_add")
                progress("click_add_button", 5, 8, "Clicking + Add safely; will not save/create")
                before_add_event_count = len(browser.network_tab_events)
                add_form_opened = await _click_add_transport_profile_with_overlay_recovery(
                    page, browser, add, kb_dir=kb_dir, warnings=warnings
                )
                add_api_interactions, add_api_transport_profiles = collect_transport_profile_api_interactions(browser.network_tab_events[before_add_event_count:], stage_label="transport_profile_add_click")
                old_transport_profiles = _merge_transport_profile_records([*old_transport_profiles, *add_api_transport_profiles])
                if add_form_opened:
                    await browser.save_dom_snapshot("transport_profile_add_form_opened")
                else:
                    status = "partial_success"
            phase_name = str(input_data.get("_full_dummy_fill_phase") or "transport_profile")
            async def _react_click_transport_profile(add_loc, step_no):
                return await _click_add_transport_profile_with_overlay_recovery(
                    page, browser, add_loc, kb_dir=kb_dir, warnings=warnings
                )
            entry_audit = await ensure_phase_form_entry(
                page=page, browser=browser, phase=phase_name, listing_url=self.transport_profiles_url,
                find_add=_find_add_button, is_form_open=_looks_like_transport_profile_add_form,
                click_add=_react_click_transport_profile, evidence_dir=kb_dir, max_steps=4,
                require_same_route=True,
            )
            files["phase_form_entry_react_json"] = str(kb_dir / "phase_form_entry_react.json")
            add_form_opened = bool(entry_audit.get("pass") and await _looks_like_transport_profile_add_form(page))
            if entry_audit.get("pass"):
                status = "completed"
            browser.set_stage("transport_profile_kb_capture_form")
            progress("capture_add_form", 6, 8, "Capturing Add Transport Profile form controls/dropdowns/required fields")
            repeatable_row_audit = []
            if add_form_opened and self.fill_dummy:
                try:
                    repeatable_row_audit = [await apply_repeatable_row_adds(page, input_data, phase_name)]
                except Exception as exc:
                    warnings.append(f"Repeatable row Add planning skipped: {mask_sensitive_string(str(exc))}")
            if add_form_opened:
                controls = await _advance_transport_profile_add_wizard(page, browser, kb_dir=kb_dir, warnings=warnings, dummy_values=dummy_values)
                stateful_controls = await capture_stateful_controls(page, phase_name)
                if stateful_controls:
                    seen_selectors = {str(c.get("selector") or "") for c in controls if isinstance(c, dict)}
                    controls.extend(dict(c) for c in stateful_controls if str(c.get("selector") or "") not in seen_selectors)
                buttons = await _evaluate_buttons(page)
                # Do not open DDS dropdowns before dummy-fill.  In Transport
                # Profile create, dropdown probing + Escape can close the drawer
                # before the no-save fill starts.  Capture non-invasive dropdown
                # candidates now and do the fuller click-based scan only after the
                # after-fill snapshot is safely saved.
                dropdowns = _collect_dropdown_options_noninvasive(controls)
            else:
                controls = []
                buttons = listing_buttons
                dropdowns = []
            exploration_knowledge: Dict[str, Any] = {
                "status": "deferred_until_target_branch_committed",
                "learning_order": "current input target branch first",
            }
            fill_attempts: List[Dict[str, Any]] = []
            phase_name = str(input_data.get("_full_dummy_fill_phase") or "transport_profile")
            state_graph: Dict[str, Any] = compile_phase_state_graph(input_data, phase_name)
            target_branch_execution: Dict[str, Any] = {}
            target_branch_knowledge: Dict[str, Any] = {}
            required_fields: List[Dict[str, Any]] = []
            attr_rows = seed.get("attributes_to_configure") if isinstance(seed.get("attributes_to_configure"), list) else []
            attr_index = -1
            transport_profile_identifier_derived_from_used = False
            for c in controls:
                attrs = {k: c.get(k) for k in ["name", "id", "placeholder", "ariaLabel"]}
                key = guess_field_key(c.get("label") or "", attrs)
                recommended = dummy_values.get(key or "", "")
                if key == "attribute_name":
                    attr_index += 1
                    if attr_rows:
                        recommended = _first_attr_value([attr_rows[min(attr_index, len(attr_rows) - 1)]], "attribute_name", recommended)
                elif key == "derived_from":
                    if attr_index < 0 and not transport_profile_identifier_derived_from_used:
                        key = "transport_profile_identifier_derived_from"
                        recommended = dummy_values.get("transport_profile_identifier_derived_from", "TRANSACTION_ROOT_ELEMENT")
                        transport_profile_identifier_derived_from_used = True
                    else:
                        key = "attribute_derived_from"
                        if attr_rows:
                            recommended = _first_attr_value([attr_rows[min(max(attr_index, 0), len(attr_rows) - 1)]], "attribute_derived_from", dummy_values.get("attribute_derived_from", "ELEMENT_IN_PAYLOAD"))
                elif key == "attribute_usage" and attr_rows:
                    recommended = _first_attr_value([attr_rows[min(max(attr_index, 0), len(attr_rows) - 1)]], "attribute_usage", dummy_values.get("attribute_usage", ""))
                elif key == "attribute_expression" and attr_rows:
                    recommended = _first_attr_value([attr_rows[min(max(attr_index, 0), len(attr_rows) - 1)]], "attribute_expression", dummy_values.get("attribute_expression", ""))
                if key == "interface_type":
                    # The portal label says Interface Type, but for SFTP-HAFT flows the
                    # selectable UI value is "SFTP HAFT".  Do not recommend plain
                    # "SFTP" because selecting that can collapse/alter the HAFT sub-form.
                    recommended = dummy_values.get("transport_protocol") or recommended
                    if str(recommended).upper() in {"SFTP", "SFTP-HAFT", "HAFT"}:
                        recommended = "SFTP HAFT"
                c["mapped_transport_profile_key"] = key
                c["recommended_value"] = recommended
                if c.get("required"):
                    required_fields.append(c)
            if self.fill_dummy and state_graph.get("nodes"):
                browser.set_stage("transport_profile_kb_fill_dummy_no_save")
                progress("fill_dummy_no_save", 0, len(controls), "Filling dummy values only; Save/Create/Submit blocked")

                # Fill the TP wizard as a dependency-ordered flow.  The first visible
                # surface can contain only System Type/System Name; later SFTP HAFT
                # controls are rendered only after those DDS selections are accepted.
                def _control_key_for(row: Dict[str, Any]) -> Optional[str]:
                    attrs = {k: row.get(k) for k in ["name", "id", "placeholder", "ariaLabel"]}
                    return row.get("mapped_transport_profile_key") or guess_field_key(row.get("label") or "", attrs)

                def _find_fresh_control(fresh_rows: List[Dict[str, Any]], target_key: str) -> Optional[Dict[str, Any]]:
                    scored: List[tuple[int, Dict[str, Any]]] = []
                    for fr in fresh_rows:
                        fr_key = _control_key_for(fr)
                        label = str(fr.get("label") or fr.get("name") or fr.get("id") or "").lower()
                        score = 0
                        if fr_key == target_key:
                            score += 20
                        if target_key == "system_name" and any(x in label for x in ["partner name", "application name", "system name"]):
                            score += 12
                        if target_key == "transport_profile_name" and any(x in label for x in ["profile name", "transport profile name", "transportprofilename"]):
                            score += 12
                        if target_key == "document_type_name" and "document" in label:
                            score += 8
                        if score:
                            row = dict(fr)
                            row["mapped_transport_profile_key"] = target_key
                            scored.append((score, row))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    return scored[0][1] if scored else None

                def _value_matches(current: str, expected: str) -> bool:
                    c = str(current or "").strip().lower()
                    e = str(expected or "").strip().lower()
                    return bool(c and e and (c == e or c in e or e in c))

                # Optional Dell AIA/AutoGen planner: learn TP dependency order from
                # current controls before deterministic MCP/Playwright execution.
                # It never clicks Save/Create/Submit; it only records a plan/judgment
                # for fields that appear after System Type / Interface Type / radios.
                try:
                    planner = LLMFormPlanner.from_env()
                    if planner is not None:
                        before_tp_state = {"control_count": len(controls), "controls": controls[:40]}
                        llm_tp = planner.plan_fill(phase=phase_name, tab="Transport Profile", controls=controls, input_values=dummy_values, row_plan=[], before_state=before_tp_state, failures=[])
                        fill_attempts.append({
                            "field": "llm_form_planner",
                            "label": "Dell AIA AutoGen Transport Profile planner",
                            "selector": "",
                            "value_redacted": "gpt-oss-120b",
                            "filled": False,
                            "planner_only": True,
                            "success": bool(llm_tp.used),
                            "plan_status": llm_tp.status,
                            "provider": llm_tp.as_audit().get("provider"),
                            "plan": llm_tp.as_audit().get("plan"),
                            "safety": "LLM plans only; MCP/Playwright performs safe fills; no Save/Create/Submit clicked",
                        })
                except Exception as exc:
                    fill_attempts.append({"field": "llm_form_planner", "label": "Dell AIA AutoGen Transport Profile planner", "filled": False, "planner_only": True, "reason": mask_sensitive_string(str(exc))})
                fill_order = [
                    "system_type",
                    "system_name",
                    "transport_profile_name",
                    "profile_usage",
                    "deployment_group",
                    "interface_type",
                    "environment",
                    "existing_account",
                    "existing_account_name",
                    "use_existing_folder",
                    "folder_path",
                    "file_pattern",
                    "post_transfer_action",
                    "splitter_required",
                    "document_type_name",
                ]
                fill_order = deterministic_ordered_keys(input_data, fill_order, phase=phase_name)
                preserve_combobox_keys = {"system_type", "system_name", "partner_name", "profile_usage", "deployment_group", "interface_type", "environment", "existing_account", "existing_account_name", "use_existing_folder", "post_transfer_action", "splitter_required", "document_type_name"}
                missing_once: set[str] = set()
                for idx, key in enumerate(fill_order, start=1):
                    if not await _looks_like_transport_profile_add_form(page):
                        probe_rows = await _evaluate_controls(page)
                        if not _controls_look_like_transport_profile_add_form_controls(probe_rows):
                            warnings.append("Transport Profile Add form closed during dummy fill; stopped before unsafe/stale actions.")
                            break
                    value = dummy_values.get(key or "")
                    if key == "system_name":
                        value = dummy_values.get("system_name") or dummy_values.get("partner_name") or value
                    if key == "interface_type":
                        value = dummy_values.get("transport_protocol") or value
                        if str(value).upper() in {"SFTP", "SFTP-HAFT", "HAFT"}:
                            value = "SFTP HAFT"
                    if key == "use_existing_folder" and str(dummy_values.get("use_existing_folder") or "").strip().lower() in {"", "no", "false"}:
                        # Keep filling the folder path manually when the portal asks for it.
                        value = dummy_values.get("use_existing_folder") or "No"
                    if value in (None, ""):
                        continue
                    # DDS radios/checkboxes need a real option click; attempting to
                    # type into the group leaves the Angular model unchanged.
                    if key == "existing_account":
                        ok = await _select_transport_profile_radio_option(page, "Existing Account", str(value or "Yes"))
                        fill_attempts.append({"field": key, "label": "Existing Account", "selector": "radio-group:Existing Account", "value_redacted": value, "filled": ok, "dom_events": ["radio-click", "change", "blur"], "scoped_to_active_root": True})
                        await page.wait_for_timeout(1000)
                        continue
                    if key == "use_existing_folder":
                        # Some TP layouts do not expose this switch until after an account is chosen;
                        # if present, set it.  If absent, do not fail the required TP gate.
                        ok = await _select_transport_profile_radio_option(page, "Existing Folder", str(value or "No"))
                        if not ok:
                            ok = await _select_transport_profile_radio_option(page, "Use Existing Folder", str(value or "No"))
                        fill_attempts.append({"field": key, "label": "Use Existing Folder", "selector": "radio-group:Use Existing Folder", "value_redacted": value, "filled": ok, "dom_events": ["radio-click", "change", "blur"], "scoped_to_active_root": True, "optional": True})
                        await page.wait_for_timeout(600)
                        if not ok:
                            continue
                        continue
                    if key == "splitter_required":
                        ok = await _set_transport_profile_checkbox(page, "Splitter Required", str(value or "No"))
                        fill_attempts.append({"field": key, "label": "Splitter Required", "selector": "checkbox:Splitter Required", "value_redacted": value, "filled": ok, "dom_events": ["checkbox-click", "change", "blur"], "scoped_to_active_root": True, "optional": True})
                        await page.wait_for_timeout(500)
                        continue

                    if key in {"existing_account_name", "folder_path", "file_pattern", "post_transfer_action", "document_type_name"}:
                        await _scroll_transport_profile_form_to_reveal(page, "Document Type" if key == "document_type_name" else ("Folder" if key == "folder_path" else key.replace('_', ' ')))
                    fresh_rows = await _evaluate_controls(page)
                    current = _find_fresh_control(fresh_rows, key)
                    if not current and key == "system_name":
                        current = _find_fresh_control(fresh_rows, "partner_name")
                    if not current:
                        # Some sections are behind Next/Continue; click only safe wizard
                        # progression controls and retry once.
                        if key not in missing_once:
                            missing_once.add(key)
                            try:
                                await _click_transport_profile_wizard_progression(page)
                                await page.wait_for_timeout(900)
                                await _scroll_transport_profile_form_to_reveal(page, "Document Type" if key == "document_type_name" else key.replace('_', ' '))
                            except Exception:
                                pass
                            fresh_rows = await _evaluate_controls(page)
                            current = _find_fresh_control(fresh_rows, key) or ( _find_fresh_control(fresh_rows, "partner_name") if key == "system_name" else None )
                    if not current:
                        fill_attempts.append({"field": key, "label": "", "selector": "", "value_redacted": value, "filled": False, "reason": "control not visible after dependency expansion/scroll", "scoped_to_active_root": True})
                        continue
                    selector = current.get("selector", "")
                    role = str(current.get("role") or "").lower()
                    ctype = str(current.get("type") or "").lower()
                    is_combo = role == "combobox" or current.get("placeholder") == "Select" or key in preserve_combobox_keys
                    if ctype == "checkbox":
                        ok = await _set_transport_profile_checkbox(page, str(current.get("label") or key), str(value))
                        events = ["checkbox-click", "change", "blur"]
                    elif ctype == "radio":
                        ok = await _select_transport_profile_radio_option(page, str(current.get("label") or key), str(value))
                        events = ["radio-click", "change", "blur"]
                    elif is_combo:
                        ok = await select_dds_combobox(page, await get_active_form_root(page, phase_name), selector, str(value), phase=phase_name)
                        events = ["click", "option/select", "change", "blur"]
                    else:
                        ok = await dds_set_text_control(page, await get_active_form_root(page, phase_name), selector, str(value))
                        events = ["input", "change", "blur"]
                    try:
                        verify_rows = await _evaluate_controls(page)
                        verify = _find_fresh_control(verify_rows, key) or current
                        verify_value = str(verify.get("value") or "")
                        if not ok and _value_matches(verify_value, str(value)):
                            ok = True
                    except Exception:
                        pass
                    fill_attempts.append(annotate_plan_attempt({"field": key, "label": current.get("label"), "selector": selector, "value_redacted": value, "filled": ok, "dom_events": events, "scoped_to_active_root": True}, input_data, str(key or ""), phase=phase_name))
                    await restore_filled_values(page, phase_name, reason=f"after Transport Profile fill {key}", attempts=fill_attempts)
                    await page.wait_for_timeout(250)
                    if idx % 3 == 0 or idx == len(fill_order):
                        progress("fill_dummy_no_save", idx, len(fill_order), f"Transport Profile fill checkpoint {idx}/{len(fill_order)}")
                autonomous_execution: Dict[str, Any] = {}
                if autonomous_phase_enabled(self.config, phase_name):
                    autonomous_cfg = getattr(self.config, "autonomous_form", None)
                    autonomous_execution = await execute_autonomous_phase_goal(
                        page=page, graph=state_graph, phase=phase_name, input_data=input_data,
                        config=self.config, output_dir=kb_dir / "autonomous_form_runtime",
                        prior_attempts=fill_attempts,
                        max_cycles=int(getattr(autonomous_cfg, "max_adaptive_cycles", 5) or 5),
                        repair=True, strict_live_execution=True,
                    )
                    target_branch_execution = autonomous_execution.get("final_execution") or {}
                    _write_json(kb_dir / "transport_profile_autonomous_form_execution.json", autonomous_execution)
                else:
                    target_branch_execution = await execute_phase_state_graph(
                        page, state_graph, phase=phase_name, max_retries=2, repair=True, prior_attempts=fill_attempts, strict_live_execution=True
                    )
                _write_json(kb_dir / "transport_profile_target_branch_execution.json", target_branch_execution)
                if not target_branch_execution.get("pass"):
                    raise RuntimeError(
                        f"{phase_name} target state graph did not commit exactly: "
                        + mask_sensitive_string(json.dumps((target_branch_execution.get("failed_attempts") or [])[:12], ensure_ascii=False, default=str))
                    )
                fill_attempts.extend(dict(a, execution_stage="state_graph_reconciliation") for a in target_branch_execution.get("attempts", []) if isinstance(a, dict))
                portal_form_dir = kb_dir.parent.parent / "portal_form_knowledge"
                portal_form_dir.mkdir(parents=True, exist_ok=True)
                target_branch_knowledge = build_target_branch_knowledge(state_graph, target_branch_execution)
                target_knowledge_file = portal_form_dir / f"{phase_name}_target_branch_form_knowledge.json"
                target_branch_knowledge["knowledge_file"] = str(target_knowledge_file)
                safe_write_json(target_knowledge_file, target_branch_knowledge)
                await browser.save_dom_snapshot("transport_profile_target_branch_before_exploration")
                try:
                    await browser.screenshot(kb_dir / "transport_profile_target_branch_before_exploration.png", full_page=True)
                except Exception:
                    pass

                final_probe_rows = await _evaluate_controls(page)
                final_form_visible = await _looks_like_transport_profile_add_form(page) or _controls_look_like_transport_profile_add_form_controls(final_probe_rows)
                # Golden screenshots are the acceptance truth.  Require the fields
                # visible in the approved Source/Target Transport Profile images,
                # but allow the verifier to count a value already present in the
                # captured controls even when the fill attempt list is unavailable
                # from an older runner path.
                if phase_name == "source_transport_profile":
                    required_tp_keys = {"system_type", "system_name", "transport_profile_name", "profile_usage", "deployment_group", "interface_type", "environment", "existing_account", "existing_account_name", "use_existing_folder", "folder_path", "file_pattern", "post_transfer_action", "document_type_name"}
                elif phase_name == "target_transport_profile":
                    required_tp_keys = {"system_type", "system_name", "transport_profile_name", "profile_usage", "deployment_group", "interface_type", "environment", "existing_account", "existing_account_name", "use_existing_folder", "folder_path", "document_type_name"}
                else:
                    required_tp_keys = {"system_type", "system_name", "transport_profile_name", "profile_usage", "deployment_group", "interface_type", "environment", "existing_account", "document_type_name"}
                filled_tp_keys = {str(a.get("field") or "") for a in fill_attempts if a.get("filled")}
                for row in final_probe_rows:
                    rk = row.get("mapped_transport_profile_key") or guess_field_key(row.get("label") or "", {k: row.get(k) for k in ["name", "id", "placeholder", "ariaLabel"]})
                    rv = str(row.get("value") or "").strip()
                    expected = str(dummy_values.get(str(rk or "")) or "").strip()
                    if rk and rv and (not expected or _value_matches(rv, expected)):
                        filled_tp_keys.add(str(rk))
                missing_required_tp_keys = sorted(required_tp_keys - filled_tp_keys)
                if missing_required_tp_keys:
                    warnings.append("Transport Profile required SFTP HAFT fields not filled: " + ", ".join(missing_required_tp_keys))
                if final_form_visible:
                    await restore_filled_values(page, phase_name, reason="before Transport Profile screenshot/evidence", attempts=fill_attempts)
                    await browser.save_dom_snapshot("transport_profile_add_form_after_dummy_fill_no_save")
                    try:
                        saved_golden_shot = await _save_transport_profile_golden_truth_screenshot(page, browser, kb_dir, phase_name)
                        if not saved_golden_shot:
                            warnings.append("Transport Profile golden-truth screenshot could not be saved even though the form was visible.")
                    except Exception as exc:
                        warnings.append(f"Transport Profile golden-truth screenshot failed: {mask_sensitive_string(str(exc))}")
                else:
                    warnings.append("Transport Profile Add form was not visible after dummy fill; final no-save form snapshot was skipped to avoid recording the listing grid as the form.")
                # Some Transport Profile comboboxes become populated only after earlier required
                # fields receive dummy values. Capture once more and merge so the KB has
                # the fullest read-only Add-form dropdown evidence possible.
                try:
                    post_fill_controls = await _evaluate_controls(page)
                    if await _looks_like_transport_profile_add_form(page) or _controls_look_like_transport_profile_add_form_controls(post_fill_controls):
                        # At this point the after-fill no-save snapshot has already
                        # been saved, so an invasive dropdown option scan cannot destroy
                        # the primary form evidence.
                        if len(post_fill_controls) > len(controls):
                            controls = post_fill_controls
                            # Re-annotate the fuller post-fill controls so CSV/verification
                            # sees the real expanded form, not the shallow pre-fill root.
                            required_fields = []
                            for c2 in controls:
                                attrs2 = {k: c2.get(k) for k in ["name", "id", "placeholder", "ariaLabel"]}
                                key2 = guess_field_key(c2.get("label") or "", attrs2)
                                if key2:
                                    c2["mapped_transport_profile_key"] = key2
                                    c2["recommended_value"] = dummy_values.get(key2, "")
                                if c2.get("required"):
                                    required_fields.append(c2)
                        post_fill_dropdowns = await _collect_dropdown_options(page, post_fill_controls)
                        dropdowns = _merge_dropdown_kb(dropdowns, post_fill_dropdowns)
                except Exception as exc:
                    warnings.append(f"Post-fill dropdown capture skipped: {mask_sensitive_string(str(exc))}")
                # Only now explore other System Type / Interface / Account / Folder
                # branches. The SFTP-HAFT target branch has already been filled and
                # captured, so empty-form assumptions cannot become planner truth.
                try:
                    live_controls = await _evaluate_controls(page)
                    live_buttons = await _evaluate_buttons(page)
                    live_dropdowns = _collect_dropdown_options_noninvasive(live_controls)
                    exploration_knowledge = await run_portal_form_exploration(
                        page=page, phase=phase_name, section="Create Transport Profile",
                        input_data=input_data, controls=live_controls, dropdowns=live_dropdowns,
                        buttons=live_buttons, repeatable_plan=build_repeatable_section_plan(input_data, phase_name),
                        repeatable_audit=repeatable_row_audit,
                        output_dir=kb_dir.parent.parent / "portal_form_knowledge",
                        config=self.config, allow_live_branching=True,
                    )
                    if exploration_knowledge.get("restore_errors") or str(exploration_knowledge.get("status") or "").startswith("failed"):
                        raise RuntimeError("Transport Profile exploration could not restore the target branch")
                    if autonomous_phase_enabled(self.config, phase_name):
                        autonomous_cfg = getattr(self.config, "autonomous_form", None)
                        restored_autonomous = await execute_autonomous_phase_goal(
                            page=page, graph=state_graph, phase=phase_name, input_data=input_data,
                            config=self.config, output_dir=kb_dir / "autonomous_form_runtime_restore",
                            prior_attempts=fill_attempts,
                            max_cycles=int(getattr(autonomous_cfg, "max_adaptive_cycles", 5) or 5),
                            repair=True, strict_live_execution=True,
                        )
                        restored_execution = restored_autonomous.get("final_execution") or {}
                        _write_json(kb_dir / "transport_profile_autonomous_restore_execution.json", restored_autonomous)
                    else:
                        restored_execution = await execute_phase_state_graph(
                            page, state_graph, phase=phase_name, max_retries=2, repair=True, prior_attempts=fill_attempts, strict_live_execution=True
                        )
                    _write_json(kb_dir / "transport_profile_post_exploration_restore_execution.json", restored_execution)
                    if not restored_execution.get("pass"):
                        raise RuntimeError("Transport Profile target path failed after exploratory branches")
                    fill_attempts.extend(dict(a, execution_stage="post_exploration_restore") for a in restored_execution.get("attempts", []) if isinstance(a, dict))
                    target_branch_execution = restored_execution
                    target_branch_knowledge = build_target_branch_knowledge(state_graph, restored_execution)
                    target_branch_knowledge["knowledge_file"] = str(target_knowledge_file)
                    safe_write_json(target_knowledge_file, target_branch_knowledge)
                    exploration_knowledge["learning_order"] = "target branch first; alternatives second; deterministic target restore last"
                    # Overwrite final evidence only after the graph has restored all
                    # requested parent/child values.
                    if await _looks_like_transport_profile_add_form(page) or _controls_look_like_transport_profile_add_form_controls(await _evaluate_controls(page)):
                        await browser.save_dom_snapshot("transport_profile_add_form_after_dummy_fill_no_save")
                        await _save_transport_profile_golden_truth_screenshot(page, browser, kb_dir, phase_name)
                except Exception as exc:
                    raise RuntimeError("Transport Profile state learning failed closed after target fill: " + mask_sensitive_string(str(exc)))
            browser.set_stage("transport_profile_kb_summarize")
            progress("summarize_and_write_outputs", 7, 8, "Writing compact KB, checkpoints and upload zip")
            network_api_interactions, all_api_transport_profiles = collect_transport_profile_api_interactions(browser.network_tab_events, stage_label="full_transport_profile_run")
            # Include direct fallback/pagination learned APIs in the final API KB, not only raw Network events.
            all_api_interactions = _dedupe_api_interactions([*listing_api_interactions, *add_api_interactions, *network_api_interactions])
            old_transport_profiles = _merge_transport_profile_records(_apply_known_transport_profile_ids([*old_transport_profiles, *all_api_transport_profiles], previous_values))
            with_ids = sum(1 for r in old_transport_profiles if r.get("transport_profile_id"))
            if not detail_enrichment_report:
                detail_enrichment_report = {}
            detail_enrichment_report.update({
                "total_old_transport_profiles": len(old_transport_profiles),
                "transport_profile_ids_found": with_ids,
                "transport_profile_ids_missing": max(0, len(old_transport_profiles) - with_ids),
                "completion_percent": round((with_ids / len(old_transport_profiles) * 100), 2) if old_transport_profiles else 0.0,
                "final_report_note": "Counts are recalculated after final merge of listing, direct fallback, pagination, detail, UI-row-action, deep-profile, and full-run API evidence.",
                "deep_profile_phase": deep_profile_report,
            })
            transport_profile_lookup = _build_transport_profile_lookup(old_transport_profiles)
            network_relevant = []
            for e in browser.network_tab_events[-200:]:
                payload = e.model_dump() if hasattr(e, "model_dump") else getattr(e, "__dict__", {})
                url = str(payload.get("url") or "")
                if any(k in url.lower() for k in ["transport_profile", "transport_profile", "transport_profile", "transport_profiletype", "transport_profile", "securelink"]):
                    payload.pop("response_body_text_redacted", None)
                    network_relevant.append(mask_sensitive_data(payload))
            kb = {
                "run_id": ctx.run_id,
                "captured_at": utc_now(),
                "url": self.transport_profiles_url,
                "safety": {
                    "save_clicked": False,
                    "create_clicked": False,
                    "submit_clicked": False,
                    "note": "The flow opens + Add and fills disposable dummy values only. It never clicks Save/Create/Submit.",
                },
                "previous_interaction_values": previous_values,
                "dummy_fill_values": dummy_values,
                "deterministic_plan_runtime": deterministic_plan_summary(input_data),
                "old_transport_profiles_inventory": old_transport_profiles,
                "old_transport_profile_id_lookup_by_name": transport_profile_lookup,
                "transport_profile_api_interactions": all_api_interactions,
                "listing_api_interactions": listing_api_interactions,
                "add_api_interactions": add_api_interactions,
                "pagination_replay_audit": pagination_audit,
                "detail_enrichment_audit": detail_enrichment_audit,
                "ui_row_action_enrichment_audit": ui_row_action_audit,
                "deep_profile_enrichment_audit": deep_profile_audit,
                "detail_enrichment_report": detail_enrichment_report,
                "ui_row_action_report": ui_row_action_report,
                "deep_profile_report": deep_profile_report,
                "listing_ui_rows": listing_ui_rows,
                "listing_buttons_before_add": listing_buttons,
                "add_form_opened": add_form_opened,
                "form_controls": controls,
                "required_fields": required_fields,
                "dropdowns": dropdowns,
                "buttons_after_add": buttons,
                "dummy_fill_attempts": fill_attempts,
                "stateful_target_branch_graph": state_graph,
                "stateful_target_branch_execution": target_branch_execution,
                "autonomous_goal_runtime_enabled": autonomous_phase_enabled(self.config, str(input_data.get("_full_dummy_fill_phase") or "transport_profile")),
                "autonomous_goal_runtime_policy": "live-goal-authoritative; phase-specific logic is advisory/structural acceleration only",
                "target_branch_knowledge": target_branch_knowledge,
                "repeatable_section_plan": build_repeatable_section_plan(input_data, str(input_data.get("_full_dummy_fill_phase") or "transport_profile")),
                "repeatable_row_audit": repeatable_row_audit,
                "portal_form_exploration": exploration_knowledge,
                "network_events_relevant_compact": network_relevant,
                "warnings": warnings,
            }
            files["transport_profile_form_kb_json"] = _write_json(kb_dir / "transport_profile_form_kb.json", kb)
            files["old_transport_profiles_inventory_json"] = _write_json(kb_dir / "old_transport_profiles_inventory.json", old_transport_profiles)
            files["old_transport_profile_id_lookup_by_name_json"] = _write_json(kb_dir / "old_transport_profile_id_lookup_by_name.json", transport_profile_lookup)
            files["transport_profile_api_interactions_json"] = _write_json(kb_dir / "transport_profile_api_interactions.json", all_api_interactions)
            files["transport_profile_api_pagination_audit_json"] = _write_json(kb_dir / "transport_profile_api_pagination_audit.json", pagination_audit)
            files["transport_profile_detail_enrichment_audit_json"] = _write_json(kb_dir / "transport_profile_detail_enrichment_audit.json", detail_enrichment_audit)
            files["transport_profile_ui_row_action_enrichment_audit_json"] = _write_json(kb_dir / "transport_profile_ui_row_action_enrichment_audit.json", ui_row_action_audit)
            files["transport_profile_deep_profile_enrichment_audit_json"] = _write_json(kb_dir / "transport_profile_deep_profile_enrichment_audit.json", deep_profile_audit)
            files["transport_profile_deep_profile_report_json"] = _write_json(kb_dir / "transport_profile_deep_profile_report.json", deep_profile_report)
            files["old_transport_profiles_deep_profiles_json"] = _write_json(kb_dir / "old_transport_profiles_deep_profiles.json", old_transport_profiles)
            files["transport_profile_id_completion_report_json"] = _write_json(kb_dir / "transport_profile_id_completion_report.json", detail_enrichment_report)
            files["old_transport_profiles_inventory_with_ids_json"] = _write_json(kb_dir / "old_transport_profiles_inventory_with_ids.json", old_transport_profiles)
            files["old_transport_profiles_inventory_with_ids_csv"] = _write_transport_profiles_inventory_csv(kb_dir / "old_transport_profiles_inventory_with_ids.csv", old_transport_profiles)
            files["listing_ui_rows_json"] = _write_json(kb_dir / "transport_profile_listing_ui_rows.json", listing_ui_rows)
            files["old_transport_profiles_inventory_csv"] = _write_transport_profiles_inventory_csv(kb_dir / "old_transport_profiles_inventory.csv", old_transport_profiles)
            files["previous_values_json"] = _write_json(kb_dir / "transport_profile_previous_interaction_values.json", previous_values)
            files["dropdowns_json"] = _write_json(kb_dir / "transport_profile_dropdowns.json", dropdowns)
            files["required_fields_json"] = _write_json(kb_dir / "transport_profile_required_fields.json", required_fields)
            files["dummy_fill_plan_json"] = _write_json(kb_dir / "transport_profile_dummy_fill_plan.json", {"values": dummy_values, "attempts": fill_attempts})
            files["dom_events_json"] = _write_json(kb_dir / "transport_profile_dom_events.json", _build_dom_event_kb(controls, dropdowns, buttons))
            files.update(_write_transport_profile_api_flow_knowledge_graph_safe(kb_dir, kb))
            files["markdown"] = _write_markdown(kb_dir / "RULE_KB_SUMMARY.md", kb)
            files["csv"] = _write_controls_csv(kb_dir / "transport_profile_form_controls.csv", controls)
            # Avoid huge evidence unless explicitly requested. Always write compact summaries before zipping.
            if self.write_heavy_evidence:
                await browser.flush_logs(force=True)
            else:
                await _write_compact_browser_summary(browser, kb_dir)
                browser._logs_flushed = True
            files["compact_action_sequence_json"] = str(kb_dir / "compact_action_sequence.json")
            files["compact_click_sequence_json"] = str(kb_dir / "compact_click_sequence.json")
            files["compact_network_summary_json"] = str(kb_dir / "compact_network_summary.json")
            shot = kb_dir / "transport_profile_add_form_after_dummy_fill_no_save.png"
            # Always record the intended golden-truth screenshot location.  The
            # E2E verifier also scans by filename, but recording this path makes
            # phase reports deterministic on Windows/OneDrive and prevents false
            # TP failures when the PNG appears just after the summary is built.
            files["after_fill_screenshot_png"] = str(shot)
            files["screenshot_png"] = str(shot)
            files["upload_zip"] = _zip_summary(run_dir, kb_dir)
            progress("completed", 8, 8, "Transport Profile KB summary zip created")
        counts = {
            "old_transport_profiles": len(json.loads((kb_dir / "old_transport_profiles_inventory.json").read_text(encoding="utf-8"))) if (kb_dir / "old_transport_profiles_inventory.json").exists() else 0,
            "old_transport_profiles_with_numeric_id": (json.loads((kb_dir / "transport_profile_id_completion_report.json").read_text(encoding="utf-8")).get("transport_profile_ids_found", 0) if (kb_dir / "transport_profile_id_completion_report.json").exists() else 0),
            "api_interactions": len(json.loads((kb_dir / "transport_profile_api_interactions.json").read_text(encoding="utf-8"))) if (kb_dir / "transport_profile_api_interactions.json").exists() else 0,
            "form_controls": len(files) and len((json.loads((kb_dir / "transport_profile_form_kb.json").read_text(encoding="utf-8"))).get("form_controls", [])),
            "required_fields": len(json.loads((kb_dir / "transport_profile_required_fields.json").read_text(encoding="utf-8"))),
            "dropdowns": len(json.loads((kb_dir / "transport_profile_dropdowns.json").read_text(encoding="utf-8"))),
            "deep_profiles_captured": (json.loads((kb_dir / "transport_profile_deep_profile_report.json").read_text(encoding="utf-8")).get("deep_profiles_captured", 0) if (kb_dir / "transport_profile_deep_profile_report.json").exists() else 0),
        }
        result = TransportProfileKBResult(run_id=ctx.run_id, run_dir=str(run_dir), kb_dir=str(kb_dir), status=status, counts=counts, files=files, warnings=warnings)
        payload = result.__dict__
        shot = kb_dir / "transport_profile_add_form_after_dummy_fill_no_save.png"
        payload["screenshots"] = [str(shot)]
        payload.setdefault("files", files)["after_fill_screenshot_png"] = str(shot)
        (run_dir / "transport_profile_kb_summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return payload



def _write_transport_profile_progress(kb_dir: Path, *, phase: str, completed: int, total: int, detail: str = "", counts: Optional[Dict[str, Any]] = None) -> None:
    """Write a lightweight heartbeat/progress checkpoint for long Transport Profile KB runs."""
    kb_dir.mkdir(parents=True, exist_ok=True)
    total_i = max(0, int(total or 0))
    completed_i = max(0, int(completed or 0))
    percent = round((completed_i / total_i * 100), 2) if total_i else 0.0
    payload = {
        "timestamp": utc_now(),
        "phase": phase,
        "completed": completed_i,
        "total": total_i,
        "percent": percent,
        "detail": mask_sensitive_string(str(detail or "")),
        "counts": mask_sensitive_data(counts or {}),
    }
    try:
        (kb_dir / "transport_profile_progress.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        with (kb_dir / "transport_profile_progress_events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        (kb_dir / "transport_profile_progress_heartbeat.txt").write_text(f"{payload['timestamp']} | {phase} | {completed_i}/{total_i} | {percent}% | {payload['detail']}\n", encoding="utf-8")
    except Exception:
        pass
    # Keep the terminal alive so the user can see that it has not hung.
    try:
        bar_len = 28
        filled = int(bar_len * (completed_i / total_i)) if total_i else 0
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"[{bar}] {completed_i}/{total_i} {percent}% | {phase} | {payload['detail']}", flush=True)
    except Exception:
        pass

def _write_json(path: Path, data: Any) -> str:
    return safe_write_json(path, data)


def _write_transport_profiles_inventory_csv(path: Path, rows: List[Dict[str, Any]]) -> str:
    fields = [
        "transport_profile_id", "transport_profile_name", "transport_profile_version", "status", "transaction_type", "format", "validation_type",
        "description", "transport_profile_identifier", "transport_profile_identifier_operation", "transport_profile_identifier_derived_from", "root_element",
        "transport_profile_version", "usage", "schema_file", "attribute_count", "deep_profile_status", "deep_profile_completeness_score",
        "deep_profile_source_url", "available_environments", "latest_dev_version", "created_by", "updated_by", "created_at", "updated_at",
        "source", "source_url",
    ]
    return safe_write_csv(path, fields, rows)


def _write_controls_csv(path: Path, controls: List[Dict[str, Any]]) -> str:
    fields = ["index", "mapped_transport_profile_key", "label", "tag", "type", "role", "required", "disabled", "readonly", "name", "id", "placeholder", "selector", "recommended_value"]
    return safe_write_csv(path, fields, controls)


def _build_dom_event_kb(controls: List[Dict[str, Any]], dropdowns: List[Dict[str, Any]], buttons: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "text_inputs": [
            {"label": c.get("label"), "selector": c.get("selector"), "events": ["focus", "input", "change", "blur"], "mapped_transport_profile_key": c.get("mapped_transport_profile_key")}
            for c in controls if (c.get("tag") in {"input", "textarea"} and str(c.get("type") or "").lower() != "file")
        ],
        "file_inputs": [
            {"label": c.get("label"), "selector": c.get("selector"), "events": ["setInputFiles", "change"], "mapped_transport_profile_key": c.get("mapped_transport_profile_key")}
            for c in controls if str(c.get("type") or "").lower() == "file" or c.get("mapped_transport_profile_key") == "schema_file"
        ],
        "dropdowns": [
            {"label": d.get("label"), "selector": d.get("selector"), "events": d.get("dom_event"), "options_count": len(d.get("options") or [])}
            for d in dropdowns
        ],
        "buttons": [
            {"text": b.get("text"), "selector": b.get("selector"), "unsafe_for_kb_run": b.get("unsafe_for_kb_run"), "events": ["click"]}
            for b in buttons
        ],
        "do_not_click_in_kb_mode": [b for b in buttons if b.get("unsafe_for_kb_run")],
    }



def _kg_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(p) for p in parts if p is not None)
    return f"{prefix}:{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:12]}"


def _kg_label(value: Any, limit: int = 160) -> str:
    text = mask_sensitive_string(str(value or "").replace("\n", " ").strip())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def build_transport_profile_api_flow_knowledge_graph(kb: Dict[str, Any]) -> Dict[str, Any]:
    """Build a compact Knowledge Graph for Transport Profile API learning + Add-form KB.

    This is deliberately separate from the heavy browser/run KG. It focuses on:
    Transport Profiles page -> listing APIs -> pagination replay -> old Transport Profile rows -> +Add -> form controls/dropdowns -> dummy fill.
    It stores only compact request/response shape and sample rows, never full raw network bodies.
    """
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    def add_node(node_id: str, node_type: str, display_label: str, /, **props: Any) -> str:
        # `props` can legitimately contain keys named `label`, `type`, or `id`
        # from DOM controls / dummy-fill audit records. Keep those as node
        # properties without colliding with the node display label argument.
        old = nodes.get(node_id, {})
        merged = {**old.get("properties", {}), **mask_sensitive_data(props)}
        nodes[node_id] = {"id": node_id, "type": node_type, "label": _kg_label(display_label), "properties": merged}
        return node_id

    def add_edge(source: str, target: str, relation: str, **props: Any) -> None:
        edges.append({
            "id": _kg_id("edge", source, target, relation, json.dumps(mask_sensitive_data(props), sort_keys=True, default=str)),
            "source": source,
            "target": target,
            "relation": relation,
            "properties": mask_sensitive_data(props),
        })

    run_id = str(kb.get("run_id") or "RULE-KB")
    run_node = add_node(_kg_id("run", run_id), "RUN", run_id, captured_at=kb.get("captured_at"), mode="transport_profile_api_learning")
    page_node = add_node(_kg_id("page", kb.get("url")), "PAGE", kb.get("url") or "Transport Profiles", url=kb.get("url"))
    add_edge(run_node, page_node, "RUN_OPENED_PAGE")

    safety_node = add_node(_kg_id("safety", run_id), "SAFETY_POLICY", "Do not save Transport Profile", **(kb.get("safety") or {}))
    add_edge(run_node, safety_node, "RUN_ENFORCED_SAFETY")

    prev = kb.get("previous_interaction_values") or {}
    if prev:
        prev_node = add_node(_kg_id("previous_values", run_id), "PREVIOUS_INTERACTION_VALUES", "Uploaded/prior Transport Profile values", **prev)
        add_edge(run_node, prev_node, "USED_PREVIOUS_VALUES")
        dm_vals = prev.get("transport_profile_values_to_fill") or {}
        if dm_vals:
            transport_profile_node = add_node(_kg_id("seed_transport_profile", dm_vals.get("transport_profile_name"), dm_vals.get("transport_profile_identifier")), "RULE_SEED", dm_vals.get("transport_profile_name") or dm_vals.get("transport_profile_identifier") or "Transport Profile seed", **dm_vals, known_ids=prev.get("known_ids"))
            add_edge(prev_node, transport_profile_node, "CONTAINED_RULE_SEED")

    stage_nodes: Dict[str, str] = {}
    def stage_node(stage: str) -> str:
        if stage not in stage_nodes:
            stage_nodes[stage] = add_node(_kg_id("stage", run_id, stage), "STAGE", stage)
            add_edge(run_node, stage_nodes[stage], "RUN_STARTED_STAGE")
        return stage_nodes[stage]

    # API interactions from initial list load, pagination replay and +Add click.
    interactions = kb.get("transport_profile_api_interactions") or []
    for idx, inter in enumerate(interactions, start=1):
        stage = str(inter.get("stage") or "unknown")
        st = stage_node(stage)
        endpoint = f"{inter.get('method','GET')} {inter.get('url','')}"
        api_node = add_node(
            _kg_id("api", inter.get("method"), inter.get("url"), idx),
            "API_INTERACTION",
            endpoint,
            order=idx,
            stage=stage,
            method=inter.get("method"),
            url=inter.get("url"),
            status=inter.get("status"),
            request_headers_compact=inter.get("request_headers_compact"),
            request_body_redacted=inter.get("request_body_redacted"),
            transport_profile_rows_extracted=inter.get("transport_profile_rows_extracted"),
        )
        add_edge(st, api_node, "STAGE_OBSERVED_API", order=idx)
        add_edge(page_node, api_node, "PAGE_TRIGGERED_API", stage=stage)
        endpoint_node = add_node(_kg_id("endpoint", inter.get("method"), inter.get("url")), "ENDPOINT", endpoint, method=inter.get("method"), url=inter.get("url"))
        add_edge(api_node, endpoint_node, "API_HIT_ENDPOINT")
        shape = inter.get("response_shape") or {}
        shape_node = add_node(_kg_id("response_shape", inter.get("url"), json.dumps(shape, sort_keys=True, default=str)), "RESPONSE_SHAPE", f"{shape.get('type','response')} rows={shape.get('row_count',0)}", **shape)
        add_edge(api_node, shape_node, "API_RETURNED_RESPONSE_SHAPE")
        for ridx, row in enumerate(inter.get("sample_transport_profile_rows") or [], start=1):
            rec_node = add_node(_kg_id("transport_profile_record", row.get("transport_profile_id") or row.get("transport_profile_name") or ridx), "RULE_RECORD", row.get("transport_profile_name") or row.get("transport_profile_identifier") or row.get("transport_profile_id") or f"record {ridx}", **row)
            add_edge(api_node, rec_node, "API_RETURNED_RULE_RECORD", sample=True)

    # Old inventory records: link every normalized row but keep node props compact.
    for idx, row in enumerate(kb.get("old_transport_profiles_inventory") or [], start=1):
        compact = {k: row.get(k, "") for k in ["transport_profile_id", "transport_profile_name", "transport_profile_version", "status", "transport_profile_identifier", "root_element", "transport_profile_version", "schema_file", "source", "source_url"]}
        rec_node = add_node(_kg_id("transport_profile_record", compact.get("transport_profile_id") or compact.get("transport_profile_name") or idx), "RULE_RECORD", compact.get("transport_profile_name") or compact.get("transport_profile_identifier") or compact.get("transport_profile_id") or f"Transport Profile {idx}", **compact)
        add_edge(run_node, rec_node, "RUN_NORMALIZED_OLD_RULE")

    for idx, audit in enumerate(kb.get("pagination_replay_audit") or [], start=1):
        audit_node = add_node(_kg_id("pagination", audit.get("url"), idx), "PAGINATION_REPLAY", audit.get("url") or f"pagination {idx}", **{**audit, "order": idx})
        add_edge(stage_node("transport_profile_kb_old_transport_profile_api_pagination"), audit_node, "REPLAYED_PAGINATED_API", order=idx)

    for idx, audit in enumerate(kb.get("detail_enrichment_audit") or [], start=1):
        dm_label = audit.get("transport_profile_name") or audit.get("transport_profile_identifier") or f"detail enrichment {idx}"
        audit_node = add_node(_kg_id("detail_enrichment", dm_label, idx), "RULE_DETAIL_ENRICHMENT", dm_label, order=idx, resolved=audit.get("resolved"), attempts_count=len(audit.get("attempts") or []), transport_profile_name=audit.get("transport_profile_name"), transport_profile_identifier=audit.get("transport_profile_identifier"))
        add_edge(stage_node("transport_profile_kb_old_transport_profile_detail_id_enrichment"), audit_node, "TRIED_DETAIL_ID_ENRICHMENT", order=idx, resolved=audit.get("resolved"))
        for aidx, attempt in enumerate((audit.get("attempts") or [])[:8], start=1):
            api_attempt = add_node(_kg_id("detail_api_attempt", attempt.get("url"), idx, aidx), "DETAIL_API_ATTEMPT", attempt.get("url") or f"attempt {aidx}", **{**attempt, "order": aidx})
            add_edge(audit_node, api_attempt, "ATTEMPTED_READ_ONLY_DETAIL_API", status=attempt.get("status"), transport_profile_id_found=attempt.get("transport_profile_id_found"))

    for idx, audit in enumerate(kb.get("ui_row_action_enrichment_audit") or [], start=1):
        dm_label = audit.get("transport_profile_name") or audit.get("transport_profile_identifier") or f"ui row action {idx}"
        ui_node = add_node(_kg_id("ui_row_action_detail", dm_label, idx), "UI_ROW_ACTION_DETAIL", dm_label, order=idx, resolved=audit.get("resolved"), search_used=audit.get("search_used"), network_events_after_click=audit.get("network_events_after_click"), api_interactions_after_click=audit.get("api_interactions_after_click"), transport_profile_name=audit.get("transport_profile_name"), transport_profile_identifier=audit.get("transport_profile_identifier"), url_transport_profile_id_candidate=audit.get("url_transport_profile_id_candidate"), event_transport_profile_id_candidate=audit.get("event_transport_profile_id_candidate"))
        add_edge(stage_node("transport_profile_kb_ui_row_action_id_learning"), ui_node, "REPEATED_UI_ROW_ACTION_TO_LEARN_ID", order=idx, resolved=audit.get("resolved"))
        click_info = audit.get("click") or {}
        click_node = add_node(_kg_id("ui_row_click", dm_label, idx), "UI_ACTION", click_info.get("reason") or click_info.get("url_after_click") or "row action", clicked=click_info.get("clicked"), row_hint=click_info.get("row_hint"), url_after_click=click_info.get("url_after_click"))
        add_edge(ui_node, click_node, "CLICKED_SAFE_ROW_ACTION", clicked=click_info.get("clicked"))
        for aidx, inter in enumerate((audit.get("api_interactions") or [])[:6], start=1):
            api_node = add_node(_kg_id("ui_row_api", inter.get("method"), inter.get("url"), idx, aidx), "API_INTERACTION", f"{inter.get('method','GET')} {inter.get('url','')}", order=aidx, method=inter.get("method"), url=inter.get("url"), status=inter.get("status"), transport_profile_rows_extracted=inter.get("transport_profile_rows_extracted"))
            add_edge(click_node, api_node, "ROW_ACTION_TRIGGERED_API", rows=inter.get("transport_profile_rows_extracted"))

    # UI/form path.
    add_click_node = add_node(_kg_id("ui_action", run_id, "click_add"), "UI_ACTION", "Click + Add Transport Profile", action="click_add_transport_profile_do_not_save", save_clicked=False)
    add_edge(run_node, add_click_node, "RUN_PERFORMED_SAFE_UI_ACTION")
    add_edge(add_click_node, safety_node, "ACTION_GUARDED_BY_SAFETY")

    for idx, field in enumerate(kb.get("form_controls") or [], start=1):
        field_node = add_node(_kg_id("form_field", field.get("selector") or idx), "FORM_FIELD", field.get("label") or field.get("name") or field.get("id") or f"field {idx}", order=idx, field_label=field.get("label"), selector=field.get("selector"), tag=field.get("tag"), type=field.get("type"), role=field.get("role"), required=field.get("required"), mapped_transport_profile_key=field.get("mapped_transport_profile_key"), recommended_value=field.get("recommended_value"), dom_events_to_try=field.get("dom_events_to_try"))
        add_edge(add_click_node, field_node, "ADD_FORM_CONTAINED_FIELD", required=field.get("required"), mapped_key=field.get("mapped_transport_profile_key"))
        if field.get("required"):
            add_edge(field_node, safety_node, "REQUIRED_BUT_NOT_SAVED")

    for idx, dd in enumerate(kb.get("dropdowns") or [], start=1):
        dd_node = add_node(_kg_id("dropdown", dd.get("selector") or dd.get("label") or idx), "DROPDOWN", dd.get("label") or f"dropdown {idx}", selector=dd.get("selector"), kind=dd.get("kind"), dom_event=dd.get("dom_event"), options_count=len(dd.get("options") or []), options=(dd.get("options") or [])[:50])
        add_edge(add_click_node, dd_node, "ADD_FORM_CONTAINED_DROPDOWN")

    for idx, fill in enumerate(kb.get("dummy_fill_attempts") or [], start=1):
        fill_node = add_node(_kg_id("dummy_fill", fill.get("field"), fill.get("selector"), idx), "DUMMY_FILL", fill.get("field") or f"dummy fill {idx}", **{**fill, "order": idx})
        add_edge(add_click_node, fill_node, "DUMMY_VALUE_FILLED_WITHOUT_SAVE", filled=fill.get("filled"))
        add_edge(fill_node, safety_node, "DUMMY_FILL_DID_NOT_SAVE")

    graph = {
        "schema_version": "transport_profile_api_flow_kg_v1",
        "created_at": utc_now(),
        "run_id": run_id,
        "url": kb.get("url"),
        "debug_questions_supported": [
            "Which API was triggered when opening Transport Profiles?",
            "Which API returned old Transport Profile IDs?",
            "Which pagination URLs were replayed?",
            "Which endpoint returned transportProfileId/transport_profileName/dataFormatType/transactionType/validationType?",
            "Which read-only detail lookup attempts were used to find numeric transportProfileId?",
            "Which real UI row actions were clicked to learn old Transport Profile IDs?",
            "Which fields/dropdowns appeared after + Add?",
            "Which DOM events are required to fill the Add Transport Profile form?",
            "Which dummy values were filled without saving?",
        ],
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "api_interactions": len(interactions),
            "old_transport_profiles": len(kb.get("old_transport_profiles_inventory") or []),
            "old_transport_profiles_with_numeric_id": (kb.get("detail_enrichment_report") or {}).get("transport_profile_ids_found"),
            "detail_enrichment_attempts": len(kb.get("detail_enrichment_audit") or []),
            "ui_row_action_attempts": len(kb.get("ui_row_action_enrichment_audit") or []),
            "ui_row_action_report": kb.get("ui_row_action_report") or {},
            "form_fields": len(kb.get("form_controls") or []),
            "required_fields": len(kb.get("required_fields") or []),
            "dropdowns": len(kb.get("dropdowns") or []),
            "dummy_fill_attempts": len(kb.get("dummy_fill_attempts") or []),
            "heavy_raw_network_included": False,
        },
        "nodes": list(nodes.values()),
        "edges": edges,
    }
    return mask_sensitive_data(graph)


def _write_transport_profile_api_flow_knowledge_graph(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    graph = build_transport_profile_api_flow_knowledge_graph(kb)
    json_path = kb_dir / "transport_profile_api_flow_knowledge_graph.json"
    mmd_path = kb_dir / "transport_profile_api_flow_knowledge_graph.mmd"
    md_path = kb_dir / "transport_profile_api_flow_knowledge_graph.md"
    html_path = kb_dir / "transport_profile_api_flow_knowledge_graph.html"
    json_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def sid(node_id: str) -> str:
        return "N" + hashlib.sha1(node_id.encode("utf-8", errors="ignore")).hexdigest()[:10]
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    lines = ["flowchart TD"]
    used: set[str] = set()
    for edge in graph.get("edges", [])[:260]:
        src, tgt = edge.get("source"), edge.get("target")
        if not src or not tgt:
            continue
        for nid in [src, tgt]:
            if nid not in used:
                n = nodes.get(nid, {})
                label = _kg_label(f"{n.get('type','NODE')}: {n.get('label', nid)}", 80).replace('"', "'")
                lines.append(f"  {sid(nid)}[\"{label}\"]")
                used.add(nid)
        rel = _kg_label(edge.get("relation"), 42).replace('"', "'")
        lines.append(f"  {sid(src)} -- {rel} --> {sid(tgt)}")
    mmd = "\n".join(lines) + "\n"
    mmd_path.write_text(mmd, encoding="utf-8")

    summary = graph.get("summary", {})
    md = "\n".join([
        "# Transport Profile API Flow Knowledge Graph",
        "",
        f"Run: `{graph.get('run_id')}`",
        f"URL: `{graph.get('url')}`",
        "",
        "## Summary",
        "```json",
        json.dumps(summary, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Debug questions supported",
        *[f"- {q}" for q in graph.get("debug_questions_supported", [])],
        "",
        "## Mermaid",
        "```mermaid",
        mmd,
        "```",
    ])
    md_path.write_text(md, encoding="utf-8")

    html_doc = f"""<!transport_profile html><html><head><meta charset='utf-8'/>
<title>Transport Profile API Flow Knowledge Graph</title>
<script type='module'>import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs'; mermaid.initialize({{startOnLoad:true,securityLevel:'loose'}});</script>
<style>body{{font-family:Arial,sans-serif;margin:28px;color:#222}}.card{{border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0}}pre{{background:#f6f8fa;padding:12px;overflow:auto;max-height:520px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #ddd;padding:6px}}</style>
</head><body><h1>Transport Profile API Flow Knowledge Graph</h1>
<div class='card'><b>Run:</b> {html.escape(str(graph.get('run_id')))}<br/><b>URL:</b> {html.escape(str(graph.get('url')))}<br/><b>Nodes:</b> {len(graph.get('nodes', []))} <b>Edges:</b> {len(graph.get('edges', []))}</div>
<h2>Summary</h2><pre>{html.escape(json.dumps(summary, indent=2, ensure_ascii=False))}</pre>
<h2>Flow graph</h2><div class='mermaid'>{html.escape(mmd)}</div>
<h2>Node preview</h2><pre>{html.escape(json.dumps(graph.get('nodes', [])[:160], indent=2, ensure_ascii=False))}</pre>
<h2>Edge preview</h2><pre>{html.escape(json.dumps(graph.get('edges', [])[:220], indent=2, ensure_ascii=False))}</pre>
</body></html>"""
    html_path.write_text(html_doc, encoding="utf-8")
    return {
        "transport_profile_api_flow_kg_json": str(json_path),
        "transport_profile_api_flow_kg_mermaid": str(mmd_path),
        "transport_profile_api_flow_kg_markdown": str(md_path),
        "transport_profile_api_flow_kg_html": str(html_path),
    }


def _write_transport_profile_api_flow_knowledge_graph_safe(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    """Export the reporting Knowledge Graph without replaying a completed portal phase.

    Exact live form execution and independent verification are authoritative.
    Serializer/report failures are preserved as warning artifacts and never
    cause the agent to reopen and refill an already-correct unsaved form.
    """
    status_path = kb_dir / "transport_profile_api_flow_knowledge_graph_export_status.json"
    try:
        files = _write_transport_profile_api_flow_knowledge_graph(kb_dir, kb)
        status = {
            "status": "ok",
            "pass": True,
            "non_blocking": True,
            "run_id": kb.get("run_id"),
            "files": files,
        }
        status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {**files, "transport_profile_api_flow_kg_export_status": str(status_path)}
    except Exception as exc:
        status = {
            "status": "warning",
            "pass": False,
            "non_blocking": True,
            "run_id": kb.get("run_id"),
            "error_type": type(exc).__name__,
            "error": mask_sensitive_string(str(exc)),
            "reason": "Knowledge Graph/reporting export failed after exact portal execution; the completed phase must not be replayed.",
        }
        status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {"transport_profile_api_flow_kg_export_status": str(status_path)}


def _write_markdown(path: Path, kb: Dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pv = kb.get("previous_interaction_values", {})
    dm = (pv.get("transport_profile_values_to_fill") or {})
    lines = [
        "# HIP SecureLink Transport Profile Form KB",
        "",
        f"Run ID: `{kb.get('run_id')}`",
        f"URL: `{kb.get('url')}`",
        "",
        "## Safety",
        "This KB run opens `+ Add`, fills dummy values, captures form/dropdown/DOM evidence, and never clicks Save/Create/Submit.",
        "",
        "## Previous Transport Profile values from provided input",
        "| Field | Value |",
        "|---|---|",
    ]
    for k, v in dm.items():
        lines.append(f"| `{k}` | `{v}` |")
    lines += ["", "## Known/prior IDs", "| ID | Value |", "|---|---|"]
    for k, v in (pv.get("known_ids") or {}).items():
        lines.append(f"| `{k}` | `{v}` |")
    old_maps = kb.get("old_transport_profiles_inventory") or []
    id_report = kb.get("detail_enrichment_report") or {}
    lines += ["", "## Old Transport Profiles learned from listing/detail APIs", f"Total old Transport Profile rows normalized: `{len(old_maps)}`", f"Numeric transportProfileIds found: `{id_report.get('transport_profile_ids_found', 0)}` / `{id_report.get('total_old_transport_profiles', len(old_maps))}`", f"ID completion: `{id_report.get('completion_percent', 0)}%`", "", "| transportProfileId | Transport Profile Name | Version | Status | Transaction Type | Data Format Type | Validation Type | Transport Profile Identifier | Root Element | File | Source |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in old_maps[:300]:
        lines.append(f"| `{row.get('transport_profile_id','')}` | `{row.get('transport_profile_name','')}` | `{row.get('transport_profile_version','')}` | `{row.get('status','')}` | `{row.get('transaction_type','')}` | `{row.get('format','')}` | `{row.get('validation_type','')}` | `{row.get('transport_profile_identifier','')}` | `{row.get('root_element','')}` | `{row.get('schema_file','')}` | `{row.get('source','')}` |")
    if len(old_maps) > 300:
        lines.append(f"| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | `{len(old_maps)-300} more rows in old_transport_profiles_inventory.csv` |")
    ui_report = kb.get("ui_row_action_report") or {}
    lines += ["", "## Old Transport Profile UI row/action ID learning", "This phase repeats the real portal interaction: search an old Transport Profile row, click its safe View/Edit/Details action, capture the API triggered by that click, and parse numeric `transportProfileId` when exposed.", "", "```json", json.dumps(ui_report, indent=2, ensure_ascii=False), "```"]
    api_interactions = kb.get("transport_profile_api_interactions") or []
    lines += ["", "## Transport Profile API interactions learned", "| Method | Status | URL | Rows extracted | Response shape |", "|---|---:|---|---:|---|"]
    for inter in api_interactions[:80]:
        shape = inter.get('response_shape') or {}
        lines.append(f"| `{inter.get('method','')}` | `{inter.get('status','')}` | `{inter.get('url','')}` | `{inter.get('transport_profile_rows_extracted',0)}` | `{shape.get('type','')} rows={shape.get('row_count',0)}` |")
    lines += ["", "## Required fields discovered", "| Label | Mapped key | Selector | Recommended dummy value |", "|---|---|---|---|"]
    for c in kb.get("required_fields", []):
        lines.append(f"| {c.get('label','')} | `{c.get('mapped_transport_profile_key','')}` | `{c.get('selector','')}` | `{c.get('recommended_value','')}` |")
    lines += ["", "## Dropdowns discovered", "| Label | Options | DOM event |", "|---|---|---|"]
    for d in kb.get("dropdowns", []):
        opts = ", ".join(str((o or {}).get("text") or (o or {}).get("value") or "") for o in (d.get("options") or [])[:20])
        lines.append(f"| {d.get('label','')} | {opts} | `{d.get('dom_event','')}` |")
    lines += ["", "## Dummy fill attempts", "| Field | Label | Filled | Notes |", "|---|---|---:|---|"]
    for a in kb.get("dummy_fill_attempts", []):
        lines.append(f"| `{a.get('field','')}` | {a.get('label','')} | {a.get('filled')} | {a.get('reason','')} |")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


async def _write_compact_browser_summary(browser: BrowserSession, kb_dir: Path) -> None:
    actions = [a.model_dump() if hasattr(a, "model_dump") else getattr(a, "__dict__", {}) for a in browser.action_events[-80:]]
    clicks = [c.model_dump() if hasattr(c, "model_dump") else getattr(c, "__dict__", {}) for c in browser.click_events[-80:]]
    net = []
    for e in browser.network_tab_events[-120:]:
        d = e.model_dump() if hasattr(e, "model_dump") else getattr(e, "__dict__", {})
        d.pop("response_body_text_redacted", None)
        d.pop("response_body_redacted", None)
        net.append(d)
    _write_json(kb_dir / "compact_action_sequence.json", actions)
    _write_json(kb_dir / "compact_click_sequence.json", clicks)
    _write_json(kb_dir / "compact_network_summary.json", net)


def _zip_summary(run_dir: Path, kb_dir: Path) -> str:
    zip_path = run_dir / "UPLOAD_RULE_KB_SUMMARY.zip"
    include = [
        kb_dir / "transport_profile_form_kb.json",
        kb_dir / "old_transport_profiles_inventory.json",
        kb_dir / "old_transport_profiles_inventory.csv",
        kb_dir / "old_transport_profile_id_lookup_by_name.json",
        kb_dir / "transport_profile_api_interactions.json",
        kb_dir / "transport_profile_api_pagination_audit.json",
        kb_dir / "transport_profile_detail_enrichment_audit.json",
        kb_dir / "transport_profile_ui_row_action_enrichment_audit.json",
        kb_dir / "transport_profile_id_completion_report.json",
        kb_dir / "old_transport_profiles_inventory_with_ids.json",
        kb_dir / "old_transport_profiles_inventory_with_ids.csv",
        kb_dir / "transport_profile_listing_ui_rows.json",
        kb_dir / "transport_profile_previous_interaction_values.json",
        kb_dir / "transport_profile_dropdowns.json",
        kb_dir / "transport_profile_required_fields.json",
        kb_dir / "transport_profile_dummy_fill_plan.json",
        kb_dir / "transport_profile_dom_events.json",
        kb_dir / "transport_profile_api_flow_knowledge_graph.json",
        kb_dir / "transport_profile_api_flow_knowledge_graph.mmd",
        kb_dir / "transport_profile_api_flow_knowledge_graph.md",
        kb_dir / "transport_profile_api_flow_knowledge_graph.html",
        kb_dir / "RULE_KB_SUMMARY.md",
        kb_dir / "transport_profile_form_controls.csv",
        kb_dir / "compact_action_sequence.json",
        kb_dir / "compact_click_sequence.json",
        kb_dir / "compact_network_summary.json",
    ]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in include:
            if p.exists():
                z.write(p, arcname=str(p.relative_to(run_dir)))
    return str(zip_path)
