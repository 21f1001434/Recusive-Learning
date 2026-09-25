from __future__ import annotations

import asyncio
import ast
import csv
import hashlib
import html
import json
import re
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
from .safe_io import safe_write_json, safe_write_csv
from .repeatable_rows import apply_repeatable_row_adds, build_repeatable_section_plan
from .portal_form_exploration import run_portal_form_exploration, merge_section_knowledge
from .deterministic_plan_runtime import sort_controls as deterministic_sort_controls, annotate_attempt as annotate_plan_attempt, plan_summary as deterministic_plan_summary
from .stateful_form_runtime import compile_phase_state_graph, execute_phase_state_graph, build_target_branch_knowledge, capture_stateful_controls
from .autonomous_form_runtime import execute_autonomous_phase_goal, autonomous_phase_enabled, autonomous_target_execution
from .upload_assets import attempt_upload_for_control
from .dds_control_driver import close_open_dropdown, get_active_form_root, select_dds_combobox, set_text_control as dds_set_text_control, _read_control_value, _lock_filled_value, semantic_runtime_enabled, open_control_for_discovery
from .phase_form_entry import ensure_phase_form_entry, find_same_page_top_right_add, same_page_add_candidate

RULES_URL = "https://developer.dell.com/hybrid-integrations/securelink/rules"

DEFAULT_DUMMY_RULE = {
    "rule_name": "DUMMY_RULE_UHAUL_ASN_KB",
    "rule_version": "1",
    "status": "Enable",
    "description": "Dummy Rule KB capture only",
    "source_document_type_id": "10482",
    "source_document_type_name": "DUMMY_SOURCE_XML_DOCTYPE_KB",
    "source_document_type_version": "1",
    "target_document_type_id": "10483",
    "target_document_type_name": "DUMMY_TARGET_XML_DOCTYPE_KB",
    "target_document_type_version": "1",
    "document_type_name_version": "DUMMY_SOURCE_XML_DOCTYPE_KB(1.0)",
    "condition_operation": "one or more conditions are satisfied",
    "condition_type": "Attributes",
    "condition_attribute": "Receiver",
    "condition_operator": "Equals",
    "condition_value": "DUMMY_RECEIVER",
    "action_name": "DUMMY_RULE_ROUTE_KB",
    "action_type": "Route Document",
    "mapping_identifier": "DUMMY_MAP_IDENTIFIER_KB",
    "mapping_identifier_name_version": "DUMMY_MAP_IDENTIFIER_KB(1.0)",
    "mapping_version": "1",
    "created_by": "KB_LEARNER",
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
class RuleKBResult:
    run_id: str
    run_dir: str
    kb_dir: str
    status: str
    counts: Dict[str, int] = field(default_factory=dict)
    files: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    existing_object_resolution: Dict[str, Any] = field(default_factory=dict)


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


def _extract_rule_identifier_parts(value: Any) -> Dict[str, str]:
    """Normalize the portal/API ruleIdentifier structure into KB-fillable parts.

    The live details API returns ruleIdentifier as either a scalar/string or a
    dict such as::

        {"attributeList": [{"derivedFrom": "ELEMENT_IN_PAYLOAD",
                            "expression": "ISA*06", "value": "ABBVIE"}],
         "operator": "ONE"}

    Earlier builds only understood a `rows` array, so `attributeList` was preserved
    only inside raw_detail_compact and the normalized KB fields stayed blank.
    """
    out = {
        "rule_identifier": "",
        "rule_identifier_operation": "",
        "rule_identifier_derived_from": "",
    }
    if isinstance(value, dict):
        out["rule_identifier_operation"] = _scalar_text(value.get("operation") or value.get("operator"))
        rows = []
        for row_key in ("rows", "attributeList", "attributes", "identifierRows", "conditions"):
            if isinstance(value.get(row_key), list):
                rows = value.get(row_key) or []
                break
        if rows and isinstance(rows[0], dict):
            first = rows[0]
            out["rule_identifier_derived_from"] = _scalar_text(
                first.get("derived_from") or first.get("derivedFrom") or first.get("source")
            )
            out["rule_identifier"] = _scalar_text(
                first.get("value")
                or first.get("rootElement")
                or first.get("root_element")
                or first.get("identifier")
                or first.get("ruleIdentifier")
            )
        if not out["rule_identifier"]:
            out["rule_identifier"] = _scalar_text(value.get("value") or value.get("rootElement") or value.get("root_element"))
    else:
        out["rule_identifier"] = _scalar_text(value)
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



def _rule_obj(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Return the Rule object from the user input, regardless of wrapper shape."""
    objs = input_data.get("objects") if isinstance(input_data, dict) else {}
    if isinstance(objs, dict) and isinstance(objs.get("rule"), dict):
        return objs.get("rule") or {}
    return input_data.get("rule") if isinstance(input_data.get("rule") if isinstance(input_data, dict) else None, dict) else {}


def _extract_rule_condition_rows(input_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize Rules condition rows from input.json.

    UHAUL-POASN has two Rule conditions in input.json. The portal starts with one
    visible condition row, so the runtime must click the row-level +Add beside
    `Conditions:` once, then fill each row in order.
    """
    rule = _rule_obj(input_data)
    cond = rule.get("conditions") if isinstance(rule, dict) else None
    rows: Any = []
    if isinstance(cond, dict):
        rows = cond.get("rows") or cond.get("conditions") or cond.get("ruleConditions") or []
    elif isinstance(cond, list):
        rows = cond
    elif isinstance(rule.get("ruleConditions") if isinstance(rule, dict) else None, list):
        rows = rule.get("ruleConditions")
    out: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({
            "condition_type": _scalar_text(row.get("condition_type") or row.get("conditionType") or row.get("type") or "Attributes") or "Attributes",
            "operator": _scalar_text(row.get("operator") or row.get("condition_operator") or row.get("conditionOperator") or "Equals") or "Equals",
            "value": _scalar_text(row.get("value") or row.get("condition_value") or row.get("conditionValue")),
            "attribute_name_unit": _scalar_text(row.get("attribute_name_unit") or row.get("attributeNameUnit") or row.get("attribute_name") or row.get("attributeName") or row.get("attribute") or row.get("name")),
            "raw": mask_sensitive_data(row),
        })
    return out


def _extract_rule_action_row(input_data: Dict[str, Any]) -> Dict[str, str]:
    rule = _rule_obj(input_data)
    actions = rule.get("actions") if isinstance(rule, dict) else None
    first: Dict[str, Any] = {}
    if isinstance(actions, dict):
        nested = actions.get("rows") or actions.get("actions")
        if isinstance(nested, list) and nested and isinstance(nested[0], dict):
            first = {**actions, **nested[0]}
        else:
            first = actions
    elif isinstance(actions, list) and actions and isinstance(actions[0], dict):
        first = actions[0]
    return {
        "action_name": _scalar_text(first.get("action_name") or first.get("actionName") or first.get("name")),
        "action_type": _scalar_text(first.get("action_type") or first.get("actionType") or first.get("type") or first.get("action") or "Route Document") or "Route Document",
        "mapping_identifier_name_version": _scalar_text(first.get("mapping_identifier_name_version") or first.get("mappingIdentifierNameVersion") or first.get("mapping_identifier") or first.get("mappingIdentifier") or first.get("mappingIdentifierName")),
    }


def extract_rule_seed(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract Rule values already known from user/manual input.

    The Rule input often stores `rule_identifier` as the portal payload
    shape: `{operation, rows:[{derived_from, value}]}`.  For the KB/form learner we
    preserve the original structure and also extract the first fillable value so the
    dummy form does not receive a raw Python dict string.
    """
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    candidates: List[Dict[str, Any]] = []
    for key in ["target_rule", "source_rule", "rule"]:
        val = (objects or {}).get(key) or input_data.get(key) if isinstance(input_data, dict) else None
        if isinstance(val, dict):
            candidates.append(val)
    result = dict(DEFAULT_DUMMY_RULE)
    for dt in candidates:
        for src_key, dst_key in [
            ("name", "rule_name"),
            ("ruleName", "rule_name"),
            ("rule_name", "rule_name"),
            ("version", "rule_version"),
            ("ruleVersion", "rule_version"),
            ("rule_version", "rule_version"),
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
            ("rule_version", "rule_version"),
            ("ruleVersion", "rule_version"),
            ("schema_file", "schema_file"),
            ("schemaFile", "schema_file"),
            ("usage", "usage"),
        ]:
            if dt.get(src_key) not in (None, ""):
                result[dst_key] = dt.get(src_key)
                if dst_key == "format":
                    result["data_format_type"] = dt.get(src_key)
        doc_identifier_value = dt.get("ruleIdentifier") if dt.get("ruleIdentifier") not in (None, "") else dt.get("rule_identifier")
        if doc_identifier_value not in (None, ""):
            result["rule_identifier_payload"] = doc_identifier_value
            result.update({k: v for k, v in _extract_rule_identifier_parts(doc_identifier_value).items() if v})
        if dt.get("operation") not in (None, ""):
            result["rule_identifier_operation"] = dt.get("operation")
        attrs = dt.get("attributes_to_configure") or dt.get("attributes") or dt.get("ruleAttributes")
        if isinstance(attrs, list) and attrs:
            result["attributes_to_configure"] = attrs
            result["attribute_name"] = _first_attr_value(attrs, "attribute_name", result.get("attribute_name", ""))
            result["attribute_derived_from"] = _first_attr_value(attrs, "attribute_derived_from", result.get("attribute_derived_from", ""))
            result["attribute_usage"] = _first_attr_value(attrs, "attribute_usage", result.get("attribute_usage", ""))
            result["attribute_expression"] = _first_attr_value(attrs, "attribute_expression", result.get("attribute_expression", ""))
    # Exact UHAUL/manual Rule shape: two Conditions rows and one Action row.
    rule_obj = _rule_obj(input_data)
    if isinstance(rule_obj, dict):
        for src_key, dst_key in [
            ("document_type_name_version", "document_type_name_version"),
            ("documentTypeNameVersion", "document_type_name_version"),
            ("rule_type", "rule_type"),
            ("ruleType", "rule_type"),
            ("rule_scope", "rule_scope"),
            ("ruleScope", "rule_scope"),
        ]:
            if rule_obj.get(src_key) not in (None, ""):
                result[dst_key] = _scalar_text(rule_obj.get(src_key))
        cond = rule_obj.get("conditions")
        if isinstance(cond, dict):
            if cond.get("execute_actions_when") not in (None, ""):
                result["condition_operation"] = _scalar_text(cond.get("execute_actions_when"))
            elif cond.get("executeActionsWhen") not in (None, ""):
                result["condition_operation"] = _scalar_text(cond.get("executeActionsWhen"))
        condition_rows = _extract_rule_condition_rows(input_data)
        if condition_rows:
            result["condition_rows"] = condition_rows
            first = condition_rows[0]
            result["condition_type"] = first.get("condition_type") or result.get("condition_type", "Attributes")
            result["condition_attribute"] = first.get("attribute_name_unit") or result.get("condition_attribute", "")
            result["condition_operator"] = first.get("operator") or result.get("condition_operator", "")
            result["condition_value"] = first.get("value") or result.get("condition_value", "")
        action = _extract_rule_action_row(input_data)
        for k, v in action.items():
            if v:
                result[k] = v
                if k == "mapping_identifier_name_version":
                    result["mapping_identifier"] = v

    return result

def build_previous_interaction_values(input_data: Dict[str, Any], *, known_rule_id: str | None = None) -> Dict[str, Any]:
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    dm = extract_rule_seed(input_data)
    source_dt = (objects or {}).get("source_rule") or {}
    target_dt = (objects or {}).get("target_rule") or {}
    rule = (objects or {}).get("rule") or {}
    return {
        "source": "uploaded_input_json_and_prior_manual_context",
        "captured_at": utc_now(),
        "known_ids": {
            "rule_id": known_rule_id or "UNKNOWN_FROM_CURRENT_RULE_KB_RUN",
            "target_rule_id": "10483",  # from prior API phase note; confirm before final create if DEV differs
        },
        "rule_values_to_fill": dm,
        "related_values": {
            "source_rule_name": source_dt.get("name"),
            "source_rule_version": source_dt.get("version"),
            "target_rule_name": target_dt.get("name"),
            "target_rule_version": target_dt.get("version"),
            "rule_name": rule.get("name"),
            "rule_version": rule.get("version"),
            "mapping_identifier_name_version": ((rule.get("actions") or {}) if isinstance(rule, dict) else {}).get("mapping_identifier_name_version"),
        },
    }


def build_dummy_fill_values(seed: Dict[str, Any], *, exact: bool = False) -> Dict[str, str]:
    """Use real-shaped but disposable scalar values for Rules.

    The flow may fill the + Add form to learn control bindings, but it never clicks
    Save/Create/Submit.  Names are prefixed with DUMMY to prevent accidental reuse.
    """
    values = dict(DEFAULT_DUMMY_RULE)
    for key, value in (seed or {}).items():
        if value not in (None, "") and isinstance(value, (str, int, float, bool)):
            values[key] = _scalar_text(value)
    if not exact:
        if not values.get("rule_name", "").upper().startswith("DUMMY"):
            values["rule_name"] = f"DUMMY_{_safe_name(values['rule_name'])}_KB"
    values.setdefault("condition_attribute", "Receiver")
    values.setdefault("condition_operator", "Equals")
    values.setdefault("condition_value", "DUMMY_RECEIVER")
    values.setdefault("action_type", "Mapping Transformer")
    values.setdefault("mapping_identifier", "DUMMY_MAP_IDENTIFIER_KB")
    return {k: str(v) for k, v in values.items() if isinstance(v, (str, int, float, bool))}

def guess_field_key(label: str, attrs: Dict[str, Any]) -> Optional[str]:
    text = " ".join(str(x or "") for x in [label, attrs.get("name"), attrs.get("id"), attrs.get("placeholder"), attrs.get("ariaLabel")]).lower()
    text = re.sub(r"\s+", " ", text).strip()
    # Avoid mapping table/search/filter controls as Rule data fields.
    if any(skip in text for skip in ["table search", "filter by column", "search"]):
        return None
    exact_label = str(label or "").strip().lower()
    exact_map = {
        "name": "rule_name",
        "rule name": "rule_name",
        "doc type name": "rule_name",
        "version": "rule_version",
        "rule version": "rule_version",
        "transaction type": "transaction_type",
        "data format type": "format",
        "format": "format",
        "description": "description",
        "document type name (version)": "document_type_name_version",
        "execute action(s) when": "condition_operation",
        "condition type": "condition_type",
        "operator": "condition_operator",
        "value": "condition_value",
        "attribute name/unit": "condition_attribute",
        "attribute name / unit": "condition_attribute",
        "mapping identifier name (version)": "mapping_identifier_name_version",
        "validation type": "validation_type",
        "operation": "rule_identifier_operation",
        "rule identifier": "rule_identifier",
        "root element": "root_element",
        "attribute name": "attribute_name",
        "derived from": "derived_from",
        "usage": "attribute_usage",
        "expression": "attribute_expression",
        "rule schema file": "schema_file",
        "schema file": "schema_file",
    }
    if exact_label in exact_map:
        return exact_map[exact_label]
    patterns = [
        ("rule_name", ["rule name", "rule name", "doc type name"]),
        ("attribute_name", ["attribute name"]),
        ("rule_version", ["rule version", "rule version", " version"]),
        ("transaction_type", ["transaction type", "transaction"]),
        ("format", ["data format", "format", "edi", "xml", "x12"]),
        ("document_type_name_version", ["document type name (version)", "document type name version", "document type"]),
        ("condition_operation", ["execute action(s) when", "execute actions when"]),
        ("condition_type", ["condition type"]),
        ("condition_operator", ["operator"]),
        ("condition_value", ["value"]),
        ("condition_attribute", ["attribute name/unit", "attribute name unit", "attribute name"]),
        ("mapping_identifier_name_version", ["mapping identifier name (version)", "mapping identifier name version", "mapping identifier"]),
        ("validation_type", ["validation type", "validation"]),
        ("description", ["description"]),
        ("rule_identifier_operation", ["operation"]),
        ("rule_identifier", ["rule identifier", "identifier"]),
        ("root_element", ["root element", "root tag", "root"]),
        ("derived_from", ["derived from", "derivedfrom"]),
        ("attribute_usage", ["usage", "source", "target"]),
        ("attribute_expression", ["expression", "xpath", "path"]),
        ("status", ["status"]),
        ("schema_file", ["schema", "xsd", "file", "upload"]),
    ]
    for key, terms in patterns:
        if any(t in text for t in terms):
            return key
    return None


def _filter_foreground_rule_controls(controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only Add/Create Rule drawer controls, not background listing/cookie controls."""
    keep=[]
    wanted=["name", "version", "document type", "rule type", "rule scope", "status", "execute", "description", "condition", "operator", "value", "action", "map", "target"]
    noise=["search", "table search", "filter by column", "items per page", "page", "cookie", "marketing", "statistical", "uncategorized", "provide comment"]
    for c in controls or []:
        label=str(c.get("label") or c.get("name") or c.get("id") or "").strip()
        text=re.sub(r"\s+", " ", label).lower()
        nm=str(c.get("name") or "").lower(); cid=str(c.get("id") or "").lower()
        if any(n in text for n in noise) or any(n in nm or n in cid for n in ["pagination", "ot-group", "vendor", "auditmessage"]):
            continue
        if any(w in text for w in wanted):
            keep.append(c)
    return keep


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
  function findRuleTypeFormRoot() {
    const candidates = Array.from(document.querySelectorAll('form, .dds__modal, .dds__drawer, .dds__container, .dds__card, app-rules, main, body'))
      .filter(isVisible)
      .map(el => {
        const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        let score = 0;
        for (const term of ['Name', 'Transaction Type', 'Version', 'Data Format Type', 'Operation', 'Derived From', 'Attribute Name', 'Usage', 'Validation Type']) {
          if (txt.includes(term)) score += 1;
        }
        // Prefer the smallest visible container with the Rule Add form labels.
        const r = el.getBoundingClientRect();
        return {el, score, area: Math.max(1, r.width * r.height)};
      })
      .filter(x => x.score >= 5)
      .sort((a, b) => (b.score - a.score) || (a.area - b.area));
    return candidates.length ? candidates[0].el : document;
  }
  const root = findRuleTypeFormRoot();
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
    return (label || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().replace(/\\s+/g, ' ');
  }
  const els = Array.from(root.querySelectorAll("input:not([type=hidden]), textarea, select, [role='combobox'], [contenteditable='true']"))
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
    """Find the Rules page-level top-right + Add without leaving the listing route."""
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


async def _close_rule_transient_surfaces(page: Page) -> int:
    """Close drawers/menus from row-action learning before clicking + Add.

    Rules rows can leave an app-generic-drawer open; Playwright then sees the Add
    button but the drawer header/body intercepts the pointer. This helper only
    clicks safe close/cancel/X controls and never Save/Create/Submit.
    """
    closed = 0
    try:
        await page.mouse.click(5, 5)
        await page.wait_for_timeout(250)
        await page.mouse.click(5, 5)
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
    const buttons = Array.from(surface.querySelectorAll('button, a, [role=button]'));
    const close = buttons.find(el => {
      const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
      const cls = (el.className || '').toString();
      return isVisible(el) && !unsafeRx.test(text + ' ' + cls) && (closeRx.test(text) || /close|cancel|dismiss|drawer__close|modal__close/i.test(text + ' ' + cls));
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


async def _looks_like_rule_add_form(page: Page) -> bool:
    try:
        return bool(await page.evaluate(
            """
() => {
  const text = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ');
  const html = document.body && document.body.innerHTML || '';
  const hasRuleName = /Rule\\s*Type\\s*Name/i.test(text) || /ruleName/i.test(html);
  const hasDocIdentifier = /Rule\\s*Identifier/i.test(text) || /Root\\s*Element/i.test(text);
  const hasFormControl = !!document.querySelector('input:not([type=hidden]), textarea, select, [role=combobox]');
  return hasFormControl && (hasRuleName || hasDocIdentifier);
}
            """
        ))
    except Exception:
        return False


async def _click_add_rule_with_overlay_recovery(
    page: Page,
    browser: BrowserSession,
    add: Locator,
    *,
    kb_dir: Path,
    warnings: List[str],
) -> bool:
    """Open + Add for form learning without crashing on stale loading overlays."""
    attempts: List[str] = []
    closed = await _close_rule_transient_surfaces(page)
    if closed:
        warnings.append(f"Closed {closed} stale Rule drawer/menu surface(s) before + Add form capture.")
    for label in ["normal", "after_overlay_wait"]:
        try:
            await browser.wait_for_blocking_overlays_gone(timeout_ms=30000 if label == "normal" else 60000)
            await browser.click_and_wait(
                action=f"structural_opener click_add_rule_do_not_save_{label}",
                locator=add,
                selector="Add Rule",
            )
            await page.wait_for_timeout(1000)
            if await _looks_like_rule_add_form(page):
                return True
            attempts.append(f"{label}: click returned but Add form was not detected")
        except Exception as exc:
            msg = mask_sensitive_string(str(exc))
            attempts.append(f"{label}: {msg}")
            try:
                await browser.save_dom_snapshot(f"rule_add_click_failed_{label}")
            except Exception:
                pass
            await page.wait_for_timeout(1500)

    disabled = await _disable_stale_loading_overlays(page)
    if disabled:
        warnings.append(f"Stale Rule loading overlay detected before + Add; disabled pointer-events on {disabled} overlay node(s) for read-only KB form capture.")
    try:
        await browser.click_and_wait(
            action="structural_opener click_add_rule_do_not_save_semantic_overlay_recovery",
            locator=add, selector="Add Rule", mutation_risk=False,
        )
        await page.wait_for_timeout(1500)
        if await _looks_like_rule_add_form(page):
            return True
        attempts.append("semantic_overlay_recovery: Add form was not detected")
    except Exception as exc:
        attempts.append(f"semantic_overlay_recovery: {mask_sensitive_string(str(exc))}")

    warnings.append("Could not open + Add Rule form after overlay-aware retries. Old Rule inventory/API/UI-row KB is still saved; Add-form controls are skipped. Attempts: " + " | ".join(attempts[-4:]))
    try:
        await browser.screenshot(kb_dir / "rule_add_click_failed_overlay.png", full_page=True)
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


def _filter_rule_dropdown_options(label: str, options: List[Dict[str, Any]], portal_noise: List[str], option_allowlists: Dict[str, set]) -> List[Dict[str, Any]]:
    """Keep dropdown options scoped to Rule form values.

    The Dell portal keeps header/navigation/footer/listing controls mounted while the Add form is open.
    Some DDS dropdown popups are rendered globally, so a naive rule-wide option scan can mix
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
        "rule", "data map", "rules", "transport profile", "profile", "logout",
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
            opts = _filter_rule_dropdown_options(label, c.get("options") or [], portal_noise, option_allowlists)
            results.append({"label": label, "selector": c.get("selector"), "kind": "select", "options": opts, "dom_event": "change"})
            continue
        if role != "combobox" and "select" not in str(c.get("selector", "")).lower():
            continue
        # Only open things that look like Rule dropdowns, not arbitrary table filters/search boxes.
        text = " ".join(str(x or "") for x in [label, c.get("ariaLabel"), c.get("placeholder"), c.get("selector")]).lower()
        if any(k in text for k in ["filter", "search", "sort", "pagination", "page"]):
            continue
        if not any(k in text for k in dropdown_terms):
            continue
        try:
            loc = page.locator(c.get("selector")).first
            if not await loc.is_visible(timeout=1000) or not await loc.is_enabled(timeout=1000):
                continue
            if not await open_control_for_discovery(page, str(c.get("selector") or ""), label=str(label), phase="rule"):
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
            await close_open_dropdown(page, "rule")
            opts = _filter_rule_dropdown_options(label, opts or [], portal_noise, option_allowlists)
            results.append({"label": label, "selector": c.get("selector"), "kind": "combobox", "options": mask_sensitive_data(opts or []), "dom_event": "click->listbox option->change"})
        except Exception as exc:
            results.append({"label": label, "selector": c.get("selector"), "kind": "combobox", "options": [], "error": mask_sensitive_string(str(exc)), "dom_event": "click attempted"})
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


RULE_URL_HINTS = ["rule", "rules", "rule-id", "rule_id", "ruleid", "securelink"]
RULE_FIELD_KEYS = {
    "rule_id": ["ruleId", "rule_id", "id"],
    "rule_name": ["ruleName", "rule_name", "name"],
    "rule_version": ["ruleVersion", "rule_version", "version", "latestDevVersion", "latest_dev_version"],
    "status": ["status", "state", "active", "enabled"],
    "description": ["description", "ruleDescription", "rule_description"],
    "source_document_type_id": ["sourceDocumentTypeId", "source_document_type_id", "srcDocumentTypeId", "sourceDocTypeId"],
    "source_document_type_name": ["sourceDocumentTypeName", "source_document_type_name", "srcDocumentTypeName", "sourceDocTypeName"],
    "source_document_type_version": ["sourceDocumentTypeVersion", "source_document_type_version", "sourceDocTypeVersion"],
    "target_document_type_id": ["targetDocumentTypeId", "target_document_type_id", "trgDocumentTypeId", "targetDocTypeId"],
    "target_document_type_name": ["targetDocumentTypeName", "target_document_type_name", "trgDocumentTypeName", "targetDocTypeName"],
    "target_document_type_version": ["targetDocumentTypeVersion", "target_document_type_version", "targetDocTypeVersion"],
    "condition_operation": ["conditionOperation", "ruleConditionOperation", "operator", "operation", "condition_operator"],
    "conditions": ["ruleConditions", "rule_conditions", "conditions", "conditionList", "ruleConditionList"],
    "actions": ["ruleActions", "rule_actions", "actions", "actionList", "ruleActionList"],
    "mapping_identifier": ["mappingIdentifier", "mapping_identifier", "mappingIdentifierName", "mapIdentifier", "mapName", "mappingName"],
    "mapping_version": ["mappingVersion", "mapping_version", "mapVersion"],
    "created_by": ["createdBy", "created_by", "requestedBy"],
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


def _contains_rule_hint(value: Any) -> bool:
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
    if any(k in payload for k in ["ruleId", "docTypeId", "ruleName", "ruleIdentifier", "rootElement", "dataFormatType", "transactionType", "validationType"]):
        yield payload
    for key in ["items", "content", "data", "records", "results", "result", "rows", "rules", "ruleList", "docTypes", "rules", "payload", "ruleDetail", "ruleDetails", "rule_detail", "docTypeDetail", "details"]:
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


def _normalize_rule_conditions(value: Any) -> List[Dict[str, Any]]:
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


def _normalize_rule_actions(value: Any) -> List[Dict[str, Any]]:
    if value in (None, "", [], {}):
        return []
    rows = value if isinstance(value, list) else [value]
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(mask_sensitive_data({
            "action_type": _scalar_text(_first_present(row, ["actionType", "action", "type", "ruleAction", "actionName"])),
            "mapping_identifier": _scalar_text(_first_present(row, ["mappingIdentifier", "mappingIdentifierName", "mapIdentifier", "mapName", "mappingName"])),
            "mapping_version": _scalar_text(_first_present(row, ["mappingVersion", "mapVersion", "version"])),
            "target_document_type": _scalar_text(_first_present(row, ["targetDocumentTypeName", "targetDocTypeName", "targetDocumentType"])),
            "raw": mask_sensitive_data(row),
        }))
    return out


def extract_rule_record(row: Dict[str, Any], *, source_url: str = "", source: str = "api") -> Optional[Dict[str, Any]]:
    """Normalize one API/list/detail row into the Rule inventory schema."""
    if not isinstance(row, dict):
        return None
    values = {field: _first_present(row, keys) for field, keys in RULE_FIELD_KEYS.items()}
    # Deep/list APIs may wrap details under ruleDetail.
    detail = row.get("ruleDetail") if isinstance(row.get("ruleDetail"), dict) else row
    if detail is not row and isinstance(detail, dict):
        for field, keys in RULE_FIELD_KEYS.items():
            if values.get(field) in (None, "", [], {}):
                values[field] = _first_present(detail, keys)
    conditions = _normalize_rule_conditions(values.get("conditions") or _recursive_find_key(row, ["ruleConditions", "conditions", "conditionList"], max_depth=5))
    actions = _normalize_rule_actions(values.get("actions") or _recursive_find_key(row, ["ruleActions", "actions", "actionList"], max_depth=5))
    has_rule_specific = any(values.get(k) not in (None, "", [], {}) for k in [
        "rule_name", "rule_version", "source_document_type_id", "target_document_type_id",
        "source_document_type_name", "target_document_type_name", "condition_operation", "mapping_identifier"
    ]) or bool(conditions or actions)
    if not has_rule_specific and not _contains_rule_hint(source_url):
        return None
    if values.get("rule_id") in (None, "") and not has_rule_specific:
        return None
    normalized = {
        "rule_id": _scalar_text(values.get("rule_id")),
        "rule_name": _scalar_text(values.get("rule_name")),
        "rule_version": _scalar_text(values.get("rule_version")),
        "status": _scalar_text(values.get("status")),
        "description": _scalar_text(values.get("description")),
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
        "latest_dev_version": _scalar_text(_first_present(row, ["latestDevVersion", "latest_dev_version"]) or values.get("rule_version")),
        "source": source,
        "source_url": mask_sensitive_string(source_url),
        "raw_row_compact": mask_sensitive_data(row),
    }
    if not any(normalized.get(k) for k in ["rule_id", "rule_name", "source_document_type_id", "target_document_type_id", "mapping_identifier"]) and not (conditions or actions):
        return None
    return normalized

def extract_rule_records_from_payload(payload: Any, *, source_url: str = "", source: str = "api") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in _iter_json_candidate_rows(payload):
        rec = extract_rule_record(row, source_url=source_url, source=source)
        if rec:
            rows.append(rec)
    return _dedupe_rule_records(rows)


DEEP_PROFILE_LIST_KEYS = {
    "attributes", "ruleattributes", "ruletypeattributes", "attributedetails",
    "attributes_to_configure", "ruleattribute", "identifiers", "ruleidentifiers",
    "ruleidentifierrows", "ruleidentifierdetails", "rows",
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
    # Do not mistake rule-identifier rows like {derivedFrom, value} for attributes.
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
    raw = _recursive_find_key(obj, ["ruleIdentifier", "rule_identifier", "ruleIdentifiers", "ruleIdentifierDetails", "identifierRows", "rows"])
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
                    "value": _scalar_text(_first_present(item, ["value", "rootElement", "root_element", "identifier", "ruleIdentifier"])),
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
    """Return (rule-detail-object, wrapper) pairs from API responses."""
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    if isinstance(payload, dict):
        for key in ["ruleDetail", "ruleDetails", "rule_detail", "docTypeDetail", "details", "rule", "data", "payload", "result"]:
            v = payload.get(key)
            if isinstance(v, dict):
                out.append((v, payload))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        out.append((item, payload))
        if any(k in payload for k in ["ruleId", "ruleName", "ruleIdentifier", "rootElement", "dataFormatType", "transactionType", "validationType"]):
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
        "rule_id", "rule_name", "rule_version", "status", "description",
        "source_document_type_id", "source_document_type_name", "source_document_type_version",
        "target_document_type_id", "target_document_type_name", "target_document_type_version",
        "condition_operation", "mapping_identifier", "mapping_version",
    ]
    score = sum(1 for k in keys if profile.get(k) not in (None, "", [], {}))
    score += min(12, int(profile.get("condition_count") or 0) * 3)
    score += min(12, int(profile.get("action_count") or 0) * 3)
    return score


def _is_rule_summary_endpoint(url: str) -> bool:
    u = str(url or "").lower()
    return bool(re.search(r"/api/rule/summary(?:$|[?#/])", u) or re.search(r"/api/rules/summary(?:$|[?#/])", u))


def _is_full_rule_deep_profile(profile: Dict[str, Any]) -> bool:
    """Return True only for detail/edit/profile-quality Rule payloads.

    The Rules summary endpoint exposes only name/version/environment. Treating that
    as a deep profile caused the previous run to mark all 283 rows as resolved before
    attempting real detail endpoints. Full/usable profiles must expose at least one
    rule-specific configuration field such as conditions, actions, mapping, source/
    target document type, status/description, or a numeric ruleId.
    """
    if not profile:
        return False
    if int(profile.get("condition_count") or 0) > 0:
        return True
    if int(profile.get("action_count") or 0) > 0:
        return True
    for key in [
        "mapping_identifier", "source_document_type_id", "source_document_type_name",
        "target_document_type_id", "target_document_type_name", "condition_operation",
        "status", "description", "rule_id",
    ]:
        if profile.get(key) not in (None, "", [], {}):
            return True
    return _deep_profile_score(profile) >= 4


def _should_reuse_network_rule_profile(profile: Dict[str, Any]) -> bool:
    """Gate reused network payloads so `/api/rule/summary` never short-circuits deep learning."""
    if not _is_full_rule_deep_profile(profile):
        return False
    return not _is_rule_summary_endpoint(str(profile.get("source_url") or ""))


def _rule_detail_payload_candidates(payload: Any) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    if isinstance(payload, dict):
        for key in ["ruleDetail", "ruleDetails", "rule", "details", "data", "payload", "result"]:
            v = payload.get(key)
            if isinstance(v, dict):
                out.append((v, payload))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        out.append((item, payload))
        if any(k in payload for k in ["ruleId", "ruleName", "ruleConditions", "ruleActions", "sourceDocumentTypeId", "targetDocumentTypeId"]):
            out.append((payload, payload))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                out.extend(_rule_detail_payload_candidates(item))
    seen: set[int] = set()
    deduped: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for item, wrapper in out:
        if id(item) in seen:
            continue
        seen.add(id(item))
        deduped.append((item, wrapper))
    return deduped


def normalize_rule_deep_profile(payload: Any, *, source_url: str = "", source: str = "api_deep_profile", base_row: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Normalize `/api/rule/{id}/details` style payloads into a full Rule profile."""
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for detail, wrapper in _rule_detail_payload_candidates(payload):
        values = {field: (_first_present(detail, keys) or _recursive_find_key(detail, keys, max_depth=5)) for field, keys in RULE_FIELD_KEYS.items()}
        if not values.get("rule_id"):
            values["rule_id"] = _extract_numeric_rule_id_from_url_or_payload(source_url) or _recursive_find_key(detail, ["id", "ruleId"], max_depth=2)
        if base_row:
            for k in ["rule_id", "rule_name", "rule_version", "status", "source_document_type_id", "source_document_type_name", "target_document_type_id", "target_document_type_name", "available_environments", "latest_dev_version"]:
                if values.get(k) in (None, "", [], {}) and base_row.get(k) not in (None, "", [], {}):
                    values[k] = base_row.get(k)
        conditions = _normalize_rule_conditions(values.get("conditions") or _recursive_find_key(detail, ["ruleConditions", "conditions", "conditionList"], max_depth=6))
        actions = _normalize_rule_actions(values.get("actions") or _recursive_find_key(detail, ["ruleActions", "actions", "actionList"], max_depth=6))
        related = {
            "flowDetail": _compact_related_detail(wrapper.get("flowDetail") if isinstance(wrapper, dict) else None),
            "documentTypeDetail": _compact_related_detail(wrapper.get("documentTypeDetail") if isinstance(wrapper, dict) else None),
            "mapDetail": _compact_related_detail(wrapper.get("mapDetail") if isinstance(wrapper, dict) else None),
            "tpDetail": _compact_related_detail(wrapper.get("tpDetail") if isinstance(wrapper, dict) else None),
        }
        profile = mask_sensitive_data({
            "rule_id": _scalar_text(values.get("rule_id")),
            "rule_name": _scalar_text(values.get("rule_name")),
            "rule_version": _scalar_text(values.get("rule_version")),
            "status": _scalar_text(values.get("status")),
            "description": _scalar_text(values.get("description")),
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
            "latest_dev_version": _scalar_text(_first_present(detail, ["latestDevVersion", "latest_dev_version"]) or (base_row or {}).get("latest_dev_version") or values.get("rule_version")),
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


def extract_rule_deep_profiles_from_payload(payload: Any, *, source_url: str = "", source: str = "api_deep_profile", base_row: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for detail, wrapper in _rule_detail_payload_candidates(payload):
        payload_for_one = {"ruleDetail": detail}
        if isinstance(wrapper, dict):
            for key in ["flowDetail", "documentTypeDetail", "mapDetail", "tpDetail"]:
                if key in wrapper:
                    payload_for_one[key] = wrapper[key]
        profile = normalize_rule_deep_profile(payload_for_one, source_url=source_url, source=source, base_row=base_row)
        if profile:
            profiles.append(profile)
    if not profiles:
        profile = normalize_rule_deep_profile(payload, source_url=source_url, source=source, base_row=base_row)
        if profile:
            profiles.append(profile)
    seen = set()
    out = []
    for profile in profiles:
        key = (profile.get("rule_id"), profile.get("rule_name"), profile.get("rule_version"), profile.get("source_url"))
        if key in seen:
            continue
        seen.add(key)
        out.append(profile)
    return out


def _record_matches_deep_profile(profile: Dict[str, Any], row: Dict[str, Any]) -> bool:
    for key in ["rule_id", "rule_name"]:
        a = str(profile.get(key) or "").strip().lower()
        b = str(row.get(key) or "").strip().lower()
        if a and b and a == b:
            return True
    return False


def _apply_deep_profile_to_record(row: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(row)
    for key in [
        "rule_id", "rule_name", "rule_version", "status", "description",
        "source_document_type_id", "source_document_type_name", "source_document_type_version",
        "target_document_type_id", "target_document_type_name", "target_document_type_version",
        "condition_operation", "mapping_identifier", "mapping_version",
        "created_by", "updated_by", "created_at", "updated_at", "available_environments", "latest_dev_version",
    ]:
        if profile.get(key) not in (None, "", [], {}) and not merged.get(key):
            merged[key] = profile.get(key)
    if profile.get("rule_id") and not row.get("rule_id"):
        merged["rule_id_source"] = "deep_profile_api"
    merged["deep_profile_status"] = "captured" if _deep_profile_score(profile) >= 4 else "partial"
    merged["deep_profile_source_url"] = profile.get("source_url", "")
    merged["deep_profile_completeness_score"] = profile.get("deep_profile_completeness_score", _deep_profile_score(profile))
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
        "total_rules": total,
        "deep_profiles_captured": captured,
        "deep_profiles_partial": partial,
        "deep_profiles_missing": max(0, total - captured - partial),
        "with_conditions": with_conditions,
        "with_actions": with_actions,
        "with_mapping_identifier": with_mapping,
        "attempts": len(attempts),
        "capture_percent": round(((captured + partial) / total * 100), 2) if total else 0.0,
        "note": "Deep profile capture parses `/api/rule/{id}/details` payloads plus any row action network payloads. Missing means the portal/API did not expose the full profile during this run.",
    }


def _candidate_rule_deep_profile_urls(row: Dict[str, Any], interactions: List[Dict[str, Any]]) -> List[str]:
    urls: List[str] = []
    rule_id = str(row.get("rule_id") or "").strip()
    name = str(row.get("rule_name") or "").strip()
    version = str(row.get("rule_version") or row.get("latest_dev_version") or "").strip()
    env = _first_environment(row.get("available_environments"), "DEV")

    def add(url: str) -> None:
        if url and url not in urls:
            urls.append(url)

    id_bases = [
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/rule",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/rules",
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/rule-details",
    ]
    if rule_id:
        for prefix in id_bases:
            add(f"{prefix}/{quote(rule_id, safe='')}/details")
            add(f"{prefix}/{quote(rule_id, safe='')}/detail")
            add(f"{prefix}/details/{quote(rule_id, safe='')}")
            add(f"{prefix}/detail/{quote(rule_id, safe='')}")
            add(f"{prefix}/details?ruleId={quote(rule_id, safe='')}")
            add(f"{prefix}/detail?ruleId={quote(rule_id, safe='')}")
    if name:
        for base in [
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/rule/details",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/rule/detail",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/rules/details",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/rules/detail",
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/rule-details",
        ]:
            q = {"ruleName": name, "environment": env}
            if version:
                q["version"] = version
                q["ruleVersion"] = version
            add(base + "?" + urlencode(q))

    # Reuse any observed detail endpoint shape from the session.  When a prior UI
    # click revealed a concrete /rule/<id>/details URL, replay that shape for the
    # current Rule ID.  When only name-based APIs exist, keep the original URL so
    # its payload can be parsed from already captured network bodies.
    for inter in interactions or []:
        u = str(inter.get("url") or "")
        if "/rule" not in u.lower() or "detail" not in u.lower():
            continue
        parsed = urlparse(u)
        if rule_id and re.search(r"/(?:rule|rules|rule-details?)/\d+/(?:detail|details)", parsed.path, flags=re.IGNORECASE):
            add(re.sub(r"(/(?:rule|rules|rule-details?)/)[^/]+(/(?:detail|details))", rf"\g<1>{quote(rule_id, safe='')}\2", urlunparse(parsed._replace(query="")), flags=re.IGNORECASE))
        elif rule_id and re.search(r"/(?:detail|details)/\d+", parsed.path, flags=re.IGNORECASE):
            add(re.sub(r"(/(?:detail|details)/)[^/]+", rf"\g<1>{quote(rule_id, safe='')}", urlunparse(parsed._replace(query="")), flags=re.IGNORECASE))
        elif name:
            add(u)
    return urls[:20]


async def _capture_rule_deep_profiles(
    page: Page,
    browser: BrowserSession,
    records: List[Dict[str, Any]],
    interactions: List[Dict[str, Any]],
    *,
    max_profiles: int | None = None,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
    timeout_ms: int = 60000,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Capture full Rule profiles for every learned Rule row.

    This mirrors the Document Type deep-profile phase.  It is strictly read-only:
    1. parse all Rule detail payloads already captured during listing/UI row actions;
    2. call safe details endpoints by learned ruleId or by ruleName/version when ID is hidden;
    3. checkpoint old_rules_deep_profiles + audit so a long run is recoverable.
    """
    rows = _merge_rule_records(records)
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
    headers.setdefault("Referer", RULES_URL)

    # Reuse all network detail payloads that were already triggered by UI row actions.
    # This is critical for Rules because the listing API often hides numeric ruleId.
    event_profiles: List[Dict[str, Any]] = []
    for ev in getattr(browser, "network_tab_events", []) or []:
        d = _event_dict(ev)
        url = str(d.get("url") or "")
        if "/rule" not in url.lower() and "rule" not in json.dumps(d, ensure_ascii=False, default=str).lower():
            continue
        payload = d.get("response_body_redacted") or _safe_json_load(d.get("response_body_text_redacted"))
        if payload is None:
            continue
        for profile in extract_rule_deep_profiles_from_payload(payload, source_url=url, source="network_deep_profile_reuse"):
            if _should_reuse_network_rule_profile(profile):
                event_profiles.append(profile)

    for idx, row in enumerate(target_rows, start=1):
        label = row.get("rule_name") or row.get("rule_id") or f"row {idx}"
        if progress_cb:
            maybe = progress_cb(
                phase="rule_deep_profile_enrichment",
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
            "rule_id": row.get("rule_id"),
            "rule_name": row.get("rule_name"),
            "used_existing_network_profile": bool(best),
            "attempts": [],
            "resolved": False,
        }
        if best:
            row.update(_apply_deep_profile_to_record(row, best))
            row_attempt["resolved"] = True
            row_attempt["source"] = best.get("source_url")

        if not row_attempt["resolved"]:
            for url in _candidate_rule_deep_profile_urls(row, interactions):
                body, meta = await _fetch_json_with_page(page, url, headers=headers, timeout_ms=timeout_ms)
                profiles = extract_rule_deep_profiles_from_payload(body, source_url=url, source="direct_deep_profile_api", base_row=row) if body is not None else []
                matched = None
                for profile in profiles:
                    if _record_matches_deep_profile(profile, row):
                        matched = profile
                        break
                if matched is None and len(profiles) == 1:
                    matched = profiles[0]
                if matched and not _is_full_rule_deep_profile(matched):
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
            profile = normalize_rule_deep_profile({"ruleDetail": {**row, "description": row.get("ui_expanded_detail_text_compact")}}, source_url="ui_expanded_row_text", source="ui_expanded_row_text", base_row=row)
            if profile and _is_full_rule_deep_profile(profile):
                row.update(_apply_deep_profile_to_record(row, profile))
                row_attempt["resolved"] = True
                row_attempt["source"] = "ui_expanded_row_text"
                row_attempt["partial_from_expanded_row_text"] = True

        attempts.append(row_attempt)
        if kb_dir and (idx % 10 == 0 or idx == limit):
            try:
                _write_json(kb_dir / "old_rules_deep_profiles.checkpoint.json", rows)
                _write_json(kb_dir / "rule_deep_profile_enrichment_audit.checkpoint.json", attempts)
                _write_json(kb_dir / "rule_deep_profile_report.checkpoint.json", _deep_profile_report(rows, attempts))
            except Exception:
                pass

    if progress_cb:
        maybe = progress_cb(
            phase="rule_deep_profile_enrichment_done",
            completed=limit,
            total=max(1, limit),
            detail="Deep Rule profile capture complete",
            counts=_deep_profile_report(rows, attempts),
        )
        if asyncio.iscoroutine(maybe):
            await maybe
    return _merge_rule_records(rows), attempts, _deep_profile_report(rows, attempts)


def _dedupe_rule_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = str(row.get("rule_id") or "").strip()
        if not key:
            key = "|".join(str(row.get(k) or "").strip().lower() for k in ["rule_name", "rule_version", "rule_identifier", "root_element"])
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


def collect_rule_api_interactions(events: Iterable[Any], *, stage_label: str = "unknown") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extract compact API learning evidence and old Rule rows from captured network events."""
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
        is_relevant = _contains_rule_hint(url) or _contains_rule_hint(payload) or _contains_rule_hint(request_body)
        if not is_relevant:
            continue
        rows = extract_rule_records_from_payload(payload, source_url=url, source=f"network:{stage_label}") if payload is not None else []
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
            "rule_rows_extracted": len(rows),
            "sample_rule_rows": rows[:3],
        }))
    return interactions, _dedupe_rule_records(inventory)


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
        key = "|".join(str(item.get(k) or "") for k in ["stage", "method", "url", "status", "rule_rows_extracted"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _build_rule_lookup(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    lookup: Dict[str, Any] = {"by_rule_id": {}, "by_rule_name": {}, "by_rule_identifier": {}, "by_root_element": {}}
    for row in records:
        compact = {k: row.get(k, "") for k in ["rule_id", "rule_name", "rule_version", "latest_dev_version", "status", "transaction_type", "format", "validation_type", "rule_identifier", "root_element", "rule_version", "schema_file", "available_environments", "source_url"]}
        for field, bucket in [("rule_id", "by_rule_id"), ("rule_name", "by_rule_name"), ("rule_identifier", "by_rule_identifier"), ("root_element", "by_root_element")]:
            val = str(row.get(field) or "").strip()
            if val:
                lookup[bucket].setdefault(val, []).append(compact)
    return lookup


async def _collect_ui_rule_rows(page: Page) -> List[Dict[str, Any]]:
    """Fallback: collect visible list/table/card text from the Rules page."""
    js = r"""
() => {
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 6) {
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
  const sels = ['table tbody tr', '[role=row]', '.dds__table tr', '.dds__data-table__row', '.dds__card', '.customCard', '[class*=card]', '[class*=row]'];
  const rows = [];
  for (const sel of sels) {
    for (const el of Array.from(document.querySelectorAll(sel)).slice(0, 500)) {
      const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {width:0,height:0};
      const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
      if (!text || !r.width || !r.height) continue;
      if (!/(rule\s*type|rule|transaction|format|validation|identifier|root|version|status|environment|data\s*format)/i.test(text)) continue;
      rows.push({selector: cssPath(el), text: text.slice(0, 1500), link_count: el.querySelectorAll('a').length, button_count: el.querySelectorAll('button,[role=button]').length});
    }
  }
  const seen = new Set();
  return rows.filter(x => { const k=x.selector+'|'+x.text; if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, 250);
}
"""
    try:
        return mask_sensitive_data(await page.evaluate(js) or [])
    except Exception:
        return []


def _urls_same_path(url_a: str, url_b: str) -> bool:
    try:
        a = urlparse(url_a)
        b = urlparse(url_b)
        return (a.netloc.lower(), a.path.rstrip("/")) == (b.netloc.lower(), b.path.rstrip("/"))
    except Exception:
        return False


async def _wait_for_rule_listing_ready(page: Page, browser: BrowserSession, rules_url: str, warnings: List[str], *, attempts: int = 4) -> bool:
    """Wait until the Rules SPA has loaded enough to expose rows, Add, or the Rule API.

    This specifically protects against a live Dell behavior where SSO lands on the
    correct URL, then an immediate second navigation aborts Angular chunks and
    leaves a blank page.  We only reload when the page is blank and no Rule
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
        interactions, rows = collect_rule_api_interactions(browser.network_tab_events, stage_label=f"readiness_attempt_{attempt}")
        if rows:
            return True
        try:
            ui_rows = await _collect_ui_rule_rows(page)
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
        if len(body_text) > 80 and re.search(r"rule\s*type|doc\s*type|rule|root\s*element|securelink", body_text, re.I):
            return True
        # Recover from a blank shell or aborted remoteEntry/chunk load.
        if attempt < attempts:
            browser.set_stage(f"rule_kb_retry_blank_listing_{attempt}")
            try:
                if _urls_same_path(page.url, rules_url):
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                else:
                    await page.goto(rules_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                warnings.append(f"Rule listing retry {attempt} navigation/reload failed: {exc}")
    warnings.append("Rule listing page did not expose rows/Add/API after retries; using direct summary API fallback if authenticated.")
    return False


async def _direct_fetch_rule_summary(page: Page, *, max_pages: int = 25) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch the known read-only Rule summary API when the SPA/network capture misses it."""
    urls: List[str] = [
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/rule/summary",
        "/inaas-gateway/hipService-svc/api/rule/summary",
        "/inaas-gateway/hipService-svc/api/rules/summary",
        "/inaas-gateway/hipService-svc/api/rule/summary",
        "/inaas-gateway/hipService-svc/api/rules/summary",
        "/inaas-gateway/hipService-svc/api/rule/summary",
    ]
    # Common pagination shapes; the bare endpoint currently returns all rows in DEV,
    # but keep these as fallback for future backend changes.
    for page_no in range(max(0, min(max_pages, 250))):
        urls.append(f"/inaas-gateway/hipService-svc/api/rule/summary?page={page_no}&size=100")
        urls.append(f"/inaas-gateway/hipService-svc/api/rule/summary?pageIndex={page_no}&pageSize=100")
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
        body, meta = await _fetch_json_with_page(page, url, headers={"Referer": RULES_URL})
        page_rows = extract_rule_records_from_payload(body, source_url=url, source="direct_summary_api_fallback") if body is not None else []
        meta = mask_sensitive_data({**meta, "source": "direct_summary_api_fallback", "rule_rows_extracted": len(page_rows)})
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
            "request_headers_compact": {"Referer": RULES_URL, "Accept": "application/json, text/plain, */*"},
            "request_body_redacted": "",
            "response_shape": _compact_payload_shape(body),
            "rule_rows_extracted": len(page_rows),
            "sample_rule_rows": page_rows[:3],
        }))
        if page_rows:
            rows.extend(page_rows)
            # The bare endpoint returning rows is enough; paginated calls can be redundant.
            if "?" not in url:
                break
        # Stop paginated replay when a paginated URL returns no rows after we already found data.
        if "?" in url and not page_rows and rows:
            break
    return _dedupe_rule_records(rows), interactions, audit


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


async def _crawl_old_rule_inventory_from_apis(page: Page, interactions: List[Dict[str, Any]], *, max_pages: int = 20) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
                page_rows = extract_rule_records_from_payload(body, source_url=url, source="api_pagination_replay")
                rows.extend(page_rows)
                if not page_rows and meta.get("row_count") == 0:
                    # Likely exhausted.
                    break
    return _dedupe_rule_records(rows), audit



def _merge_rule_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge duplicate Rule rows while preferring records that include numeric ruleId/details."""
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("rule_id") or "").strip().lower()
        name = str(row.get("rule_name") or "").strip().lower()
        version = str(row.get("rule_version") or row.get("latest_dev_version") or "").strip().lower()
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

def _known_rule_id_by_identifier(previous_values: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    dm = (previous_values or {}).get("rule_values_to_fill") or {}
    known = (previous_values or {}).get("known_ids") or {}
    mid = known.get("rule_id")
    ident = dm.get("rule_name")
    if ident and mid and str(mid).upper() not in {"UNKNOWN", "UNKNOWN_FROM_CURRENT_RULE_KB_RUN"}:
        out[str(ident).strip().lower()] = str(mid)
    return out


def _apply_known_rule_ids(records: List[Dict[str, Any]], previous_values: Dict[str, Any]) -> List[Dict[str, Any]]:
    known = _known_rule_id_by_identifier(previous_values)
    patched = []
    for row in records:
        item = dict(row)
        ident = str(item.get("rule_name") or "").strip().lower()
        if ident in known and not item.get("rule_id"):
            item["rule_id"] = known[ident]
            item["rule_id_source"] = "known_prior_context"
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
        if "/api/" in path and any(x in path.lower() for x in ["rule", "ruletype", "rule", "rule"]):
            base_path = re.split(r"/(summary|detail|details|lookup|versions?|edit|view)(?:/|$)", path, maxsplit=1)[0]
            bases.append(urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", "")))
        if "/api/" in path and any(x in path.lower() for x in ["rule", "rule", "rule", "securelink"]):
            # Keep the observed collection endpoint too; some APIs accept identifier filters on the summary route.
            bases.append(urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")))
    bases.extend(["https://developer.dell.com/inaas-gateway/hipService-svc/api/rule", "https://developer.dell.com/inaas-gateway/hipService-svc/api/rule", "https://developer.dell.com/inaas-gateway/hipService-svc/api/rules"])
    out: List[str] = []
    seen: set[str] = set()
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _detail_query_params(row: Dict[str, Any]) -> Dict[str, str]:
    ident = str(row.get("rule_name") or "").strip()
    version = str(row.get("rule_version") or row.get("latest_dev_version") or "").strip()
    env = _first_environment(row.get("available_environments"), "DEV")
    params = {"ruleName": ident, "environment": env}
    if version:
        params["version"] = version
        params["ruleVersion"] = version
    return {k: v for k, v in params.items() if v}


def _candidate_rule_detail_urls(row: Dict[str, Any], interactions: List[Dict[str, Any]]) -> List[str]:
    ident = str(row.get("rule_name") or "").strip()
    rule_id = str(row.get("rule_id") or "").strip()
    version = str(row.get("rule_version") or row.get("latest_dev_version") or "").strip()
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
        if rule_id:
            add("/" + quote(rule_id, safe=""))
            add("/" + quote(rule_id, safe="") + "/details")
            add("/" + quote(rule_id, safe="") + "/detail")
            add("/details/" + quote(rule_id, safe=""))
        if ident:
            add("/details", params)
            add("/detail", params)
            add("/lookup", params)
            add("/" + quote(ident, safe=""))
            if version:
                add("/" + quote(ident, safe="") + "/" + quote(version, safe=""))
    for base in ["https://developer.dell.com/inaas-gateway/hipService-svc/api/rule", "https://developer.dell.com/inaas-gateway/hipService-svc/api/rules"]:
        if rule_id:
            urls.extend([f"{base}/{quote(rule_id, safe='')}/details", f"{base}/details?ruleId={quote(rule_id, safe='')}"])
        elif ident:
            urls.append(f"{base}/details?" + urlencode(params))
    out: List[str] = []
    seen: set[str] = set()
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out[:18]


def _record_matches_rule(row: Dict[str, Any], target: Dict[str, Any]) -> bool:
    rid = str(target.get("rule_id") or "").strip().lower()
    name = str(target.get("rule_name") or "").strip().lower()
    if rid and str(row.get("rule_id") or "").strip().lower() == rid:
        return True
    if name and str(row.get("rule_name") or "").strip().lower() == name:
        return True
    return False


async def _enrich_old_rules_with_detail_apis(
    page: Page,
    records: List[Dict[str, Any]],
    interactions: List[Dict[str, Any]],
    previous_values: Dict[str, Any],
    *,
    max_details: int = 250,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
    timeout_ms: int = 5000,
    max_candidate_urls_per_rule: int = 4,
    probe_rows: int = 15,
    stop_if_probe_finds_no_ids: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Best-effort read-only detail enrichment to discover numeric ruleIds.

    Bounded by design. The previous implementation could try many speculative
    URL shapes for every Rule, which can look like a hang when the Dell gateway
    slowly returns 404/500/timeout. This version writes checkpoints and stops
    the expensive detail phase when the first probe batch proves that the
    visible/listing APIs do not expose numeric ruleId through read-only detail
    endpoints. It still preserves every old Rule row from the listing.
    """
    enriched: List[Dict[str, Any]] = _apply_known_rule_ids(records, previous_values)
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
    by_key = _merge_rule_records(enriched)
    limit = max(0, int(max_details or 0))
    candidates_rows = by_key[:limit]
    baseline_ids = sum(1 for r in by_key if r.get("rule_id"))

    async def emit_progress(detail: str) -> None:
        if progress_cb:
            maybe = progress_cb(
                phase="rule_detail_id_enrichment",
                completed=processed,
                total=len(candidates_rows),
                detail=detail,
                counts={
                    "old_rules": len(by_key),
                    "rule_ids_found": sum(1 for r in by_key if r.get("rule_id")),
                    "detail_found_this_run": detail_found,
                },
            )
            if asyncio.iscoroutine(maybe):
                await maybe

    for row in candidates_rows:
        processed += 1
        await emit_progress(f"checking {processed}/{len(candidates_rows)} {row.get('rule_name') or row.get('rule_identifier') or ''}")
        if row.get("rule_id"):
            continue
        # Probe mode: once enough rows have been tested and no detail IDs were found,
        # stop wasting time on the remaining 200+ Rules. This keeps the run moving
        # and records the limitation clearly in rule_id_completion_report.json.
        if stop_if_probe_finds_no_ids and processed > max(1, int(probe_rows or 0)) and detail_found == 0:
            skipped_after_probe = max(0, len(candidates_rows) - processed + 1)
            stop_reason = (
                f"Stopped detail enrichment after probe_rows={probe_rows}: no numeric ruleId was exposed by "
                "the attempted read-only detail endpoints. Listing inventory was kept; only numeric IDs remain blank."
            )
            break
        candidates = _candidate_rule_detail_urls(row, interactions)[: max(1, int(max_candidate_urls_per_rule or 1))]
        row_audit = {
            "rule_name": row.get("rule_name"),
            "rule_identifier": row.get("rule_identifier"),
            "attempts": [],
            "resolved": False,
            "candidate_url_count_used": len(candidates),
        }
        for url in candidates:
            body, meta = await _fetch_json_with_page(page, url, headers=headers, timeout_ms=timeout_ms)
            meta = mask_sensitive_data({**meta, "source": "rule_detail_id_enrichment"})
            recs = extract_rule_records_from_payload(body, source_url=url, source="api_detail_enrichment") if body is not None else []
            matched = None
            for rec in recs:
                if _record_matches_rule(rec, row):
                    matched = rec
                    break
            if matched is None and len(recs) == 1:
                matched = recs[0]
            meta["rule_rows_extracted"] = len(recs)
            meta["matched"] = bool(matched)
            meta["rule_id_found"] = bool(matched and matched.get("rule_id"))
            row_audit["attempts"].append(meta)
            if matched:
                merged = _merge_rule_records([row, matched])[0]
                row.update(merged)
                if matched.get("rule_id"):
                    row["rule_id_source"] = "api_detail_enrichment"
                    row_audit["resolved"] = True
                    detail_found += 1
                    break
        audit.append(row_audit)
        # Checkpoint every five rows so a stopped run still has useful output.
        if kb_dir and (processed % 5 == 0 or processed == len(candidates_rows)):
            try:
                _write_json(kb_dir / "old_rules_inventory_with_ids.checkpoint.json", by_key)
                _write_json(kb_dir / "rule_detail_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    merged_records = _merge_rule_records(by_key)
    with_ids = sum(1 for r in merged_records if r.get("rule_id"))
    report = {
        "total_old_rules": len(merged_records),
        "rule_ids_found": with_ids,
        "rule_ids_missing": max(0, len(merged_records) - with_ids),
        "detail_enrichment_processed": processed,
        "detail_enrichment_found_ids": detail_found,
        "known_prior_ids_applied": sum(1 for r in merged_records if r.get("rule_id_source") == "known_prior_context"),
        "baseline_ids_before_detail_enrichment": baseline_ids,
        "detail_timeout_ms": timeout_ms,
        "max_candidate_urls_per_rule": max_candidate_urls_per_rule,
        "probe_rows": probe_rows,
        "skipped_after_probe": skipped_after_probe,
        "stop_reason": stop_reason,
        "completion_percent": round((with_ids / len(merged_records) * 100), 2) if merged_records else 0.0,
        "note": "Blank rule_id means the visible/listing API did not expose numeric ruleId and no read-only detail endpoint returned it during this run.",
    }
    if kb_dir:
        try:
            _write_json(kb_dir / "old_rules_inventory_with_ids.checkpoint.json", merged_records)
            _write_json(kb_dir / "rule_detail_enrichment_audit.checkpoint.json", audit)
            _write_json(kb_dir / "rule_id_completion_report.checkpoint.json", report)
        except Exception:
            pass
    return merged_records, audit, report



def _extract_numeric_rule_id_from_url_or_payload(value: Any) -> str:
    """Extract a plausible numeric Rule id from a URL/payload."""
    if value in (None, ""):
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    low = text.lower()
    if not any(h in low for h in ["rule", "ruleid", "rule_id", "rules"]):
        return ""
    patterns = [
        r"(?:ruleId|rule_id)[\"'=:\s]+(\d{1,10})",
        # Some Rule detail payloads use a generic id field while the same body still
        # contains ruleName/ruleCondition/ruleAction. The rule-word guard above keeps
        # this from picking unrelated IDs.
        r"[\"]id[\"]\s*:\s*(\d{1,10})",
        r"/(?:rule|rules|rule-details?|rule-details?)/(?:detail/|details/|edit/|view/)?(\d{1,10})(?:[/?#]|$)",
        r"/(?:detail|details|edit|view)/(\d{1,10})(?:[/?#]|$)",
        r"[?&](?:ruleId|rule_id|id)=(\d{1,10})(?:&|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


async def _find_rule_listing_search(page: Page) -> Optional[Locator]:
    """Find the visible Rules list search box, avoiding Filter buttons."""
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


async def _set_rule_listing_search(page: Page, value: str) -> bool:
    loc = await _find_rule_listing_search(page)
    if loc is None:
        return False
    if semantic_runtime_enabled(page):
        try:
            selector = await loc.evaluate("el => el.id ? ('#' + CSS.escape(el.id)) : ''")
            if not selector:
                return False
            if not await dds_set_text_control(page, None, selector, value, phase="rule"):
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


async def _clear_rule_listing_search(page: Page) -> None:
    loc = await _find_rule_listing_search(page)
    if loc is None:
        return
    if semantic_runtime_enabled(page):
        try:
            selector = await loc.evaluate("el => el.id ? ('#' + CSS.escape(el.id)) : ''")
            if not selector:
                return
            if not await dds_set_text_control(page, None, selector, "", phase="rule"):
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


async def _reset_rules_listing_for_row_learning(page: Page, *, reason: str = "") -> bool:
    """Put the Rules listing back into a clean searchable state.

    The Rules grid can keep an expanded row/drawer/menu focused after a row-action
    attempt. When that happens the next iteration may not find the search box, or
    the only row button may be "Collapse the row". A bounded hard reset is safer
    than continuing and marking the Rule as missing. It only navigates back to the
    read-only Rules listing; it never clicks Save/Create/Submit/Delete.
    """
    try:
        await _close_rule_transient_surfaces(page)
    except Exception:
        pass
    try:
        if not _urls_same_path(page.url, RULES_URL):
            await page.goto(RULES_URL, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(1200)
        search = await _find_rule_listing_search(page)
        if search is None:
            await page.goto(RULES_URL, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(1500)
        await _clear_rule_listing_search(page)
        return True
    except Exception:
        return False


async def _click_safe_rule_row_action(page: Page, row_hint: str) -> Dict[str, Any]:
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
      // Avoid selecting TESTEY_<rule> when the requested rule is <rule>.
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
    # In the live Rules grid the only row-scoped button may be labelled
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
                    if not await open_control_for_discovery(page, str(b.get("selector") or ""), label="Rule row action", phase="rule"):
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
    # Many Rules only expose ruleId/conditions/actions after the row expands and
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
                        if not await open_control_for_discovery(page, str(opt.get("selector") or ""), label="Rule row menu action", phase="rule"):
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


async def _learn_old_rule_ids_from_ui_row_actions(
    page: Page,
    browser: BrowserSession,
    records: List[Dict[str, Any]],
    previous_values: Dict[str, Any],
    *,
    max_rows: int = 250,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Repeat the manually validated UI approach for old Rules.

    This mirrors the Partner/System strategy: use the live UI row, click the safe row
    action, capture the API that the UI itself triggers, then parse IDs from URL/body.
    It never clicks Save/Create/Submit/Delete.  It is checkpointed so a stopped run
    still preserves useful IDs/evidence.
    """
    rows = _merge_rule_records(_apply_known_rule_ids(records, previous_values))
    limit = min(len(rows), max(0, int(max_rows or 0)))
    audit: List[Dict[str, Any]] = []
    ids_before = sum(1 for r in rows if r.get("rule_id"))
    found_this_phase = 0

    async def emit(i: int, detail: str) -> None:
        if progress_cb:
            maybe = progress_cb(
                phase="rule_ui_row_action_id_learning",
                completed=i,
                total=max(1, limit),
                detail=detail,
                counts={"old_rules": len(rows), "rule_ids_found": sum(1 for r in rows if r.get("rule_id")), "ui_ids_found": found_this_phase},
            )
            if asyncio.iscoroutine(maybe):
                await maybe

    if limit <= 0:
        return rows, audit, {"ui_rows_attempted": 0, "ui_ids_found": 0, "note": "No old Rule rows available for UI row-action learning."}

    for idx, row in enumerate(rows[:limit], start=1):
        hint = str(row.get("rule_name") or row.get("rule_identifier") or row.get("root_element") or "").strip()
        if not hint:
            continue
        await emit(idx, f"UI row detail learning {idx}/{limit}: {hint[:80]}")
        if row.get("rule_id") and row.get("rule_id_source") == "known_prior_context":
            audit.append({"rule_name": row.get("rule_name"), "skipped": True, "reason": "already_has_known_rule_id"})
            continue
        before_count = len(browser.network_tab_events)
        searched = await _set_rule_listing_search(page, hint)
        click_info: Dict[str, Any] = {"clicked": False, "reason": "search_failed"}
        if searched:
            try:
                click_info = await asyncio.wait_for(_click_safe_rule_row_action(page, hint), timeout=10)
            except Exception as exc:
                click_info = {"clicked": False, "reason": f"ui_click_error: {exc}", "row_hint": hint}

        # Retry once from a clean listing state. This is the critical parity fix
        # with Document Type deep learning: a single transient expanded row/drawer
        # must not permanently mark the next Rule as unresolved.
        retry_reasons = {"search_failed", "row_not_found", "no_safe_row_action"}
        if not click_info.get("clicked") and str(click_info.get("reason") or "") in retry_reasons:
            await _reset_rules_listing_for_row_learning(page, reason=str(click_info.get("reason") or ""))
            before_count = len(browser.network_tab_events)
            searched = await _set_rule_listing_search(page, hint)
            click_info = {"clicked": False, "reason": "search_failed_after_reset"}
            if searched:
                try:
                    click_info = await asyncio.wait_for(_click_safe_rule_row_action(page, hint), timeout=12)
                    if not click_info.get("clicked"):
                        click_info["retry_after_listing_reset"] = True
                except Exception as exc:
                    click_info = {"clicked": False, "reason": f"ui_click_error_after_reset: {exc}", "row_hint": hint, "retry_after_listing_reset": True}
            else:
                click_info["retry_after_listing_reset"] = True

        await page.wait_for_timeout(1100)
        new_events = browser.network_tab_events[before_count:]
        interactions, api_rows = collect_rule_api_interactions(new_events, stage_label="rule_ui_row_action_detail")
        matched = None
        for rec in api_rows:
            if _record_matches_rule(rec, row):
                matched = rec
                break
        if matched is None and len(api_rows) == 1:
            matched = api_rows[0]
        url_rule_id = _extract_numeric_rule_id_from_url_or_payload(page.url)
        event_url_rule_id = ""
        for ev in new_events:
            d = _event_dict(ev)
            event_url_rule_id = _extract_numeric_rule_id_from_url_or_payload(d.get("url")) or event_url_rule_id
            event_url_rule_id = _extract_numeric_rule_id_from_url_or_payload(d.get("response_body_redacted") or d.get("response_body_text_redacted")) or event_url_rule_id
            if event_url_rule_id:
                break
        row_audit = {
            "rule_name": row.get("rule_name"),
            "rule_identifier": row.get("rule_identifier"),
            "search_used": searched,
            "click": mask_sensitive_data(click_info),
            "network_events_after_click": len(new_events),
            "api_interactions_after_click": len(interactions),
            "api_rows_after_click": len(api_rows),
            "url_after_click": mask_sensitive_string(page.url),
            "url_rule_id_candidate": url_rule_id,
            "event_rule_id_candidate": event_url_rule_id,
            "resolved": False,
        }
        if matched:
            merged = _merge_rule_records([row, matched])[0]
            row.update(merged)
        if not row.get("rule_id"):
            expanded_text = ""
            try:
                expanded_text = str((click_info or {}).get("expanded_snapshot") or "")
            except Exception:
                expanded_text = ""
            expanded_rule_id = _extract_numeric_rule_id_from_url_or_payload(expanded_text)
            candidate = event_url_rule_id or url_rule_id or expanded_rule_id
            if candidate:
                row["rule_id"] = candidate
                row["rule_id_source"] = "ui_row_action_network_or_url_or_expanded_dom"
            if expanded_text:
                row["ui_expanded_detail_text_compact"] = expanded_text[:1200]
        if row.get("rule_id") and not row_audit["resolved"]:
            row_audit["resolved"] = True
            if str(row.get("rule_id_source") or "").startswith("ui_row_action") or (matched and matched.get("rule_id")):
                found_this_phase += 1
        row_audit["api_interactions"] = interactions[:10]
        audit.append(row_audit)
        # Return to the list and clear search so the next row starts clean.
        try:
            await _close_rule_transient_surfaces(page)
            if not _urls_same_path(page.url, RULES_URL):
                await page.go_back(timeout=4000)
                await page.wait_for_timeout(800)
        except Exception:
            pass
        await _clear_rule_listing_search(page)
        # Make every iteration independent; this keeps long Rules runs from
        # alternating between good rows and search failures as expanded rows stack.
        if idx % 10 == 0:
            await _reset_rules_listing_for_row_learning(page, reason="periodic_cleanup")
        if kb_dir and (idx % 5 == 0 or idx == limit):
            try:
                _write_json(kb_dir / "old_rules_inventory_with_ids.ui_checkpoint.json", rows)
                _write_json(kb_dir / "rule_ui_row_action_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    # Final rescue pass for Rules that did not resolve on the first sweep.
    # This reloads the listing before each unresolved row and tries exact row matching again.
    # It specifically fixes long-run grid drift where early rows or similarly named
    # TESTEY_* rows can poison later searches. It is read-only and uses the same safe
    # row expansion/detail action; it never clicks Save/Create/Submit/Delete.
    unresolved_positions = [i for i, r in enumerate(rows[:limit]) if not r.get("rule_id")]
    if unresolved_positions:
        await _reset_rules_listing_for_row_learning(page, reason="final_unresolved_rescue_start")
    for rescue_idx, pos in enumerate(unresolved_positions, start=1):
        row = rows[pos]
        hint = str(row.get("rule_name") or row.get("rule_identifier") or row.get("root_element") or "").strip()
        if not hint or row.get("rule_id"):
            continue
        await emit(pos + 1, f"UI row final rescue {rescue_idx}/{len(unresolved_positions)}: {hint[:80]}")
        await _reset_rules_listing_for_row_learning(page, reason="final_unresolved_rescue_each_row")
        before_count = len(browser.network_tab_events)
        searched = await _set_rule_listing_search(page, hint)
        click_info: Dict[str, Any] = {"clicked": False, "reason": "final_rescue_search_failed", "final_rescue_pass": True}
        if searched:
            try:
                click_info = await asyncio.wait_for(_click_safe_rule_row_action(page, hint), timeout=14)
                click_info["final_rescue_pass"] = True
            except Exception as exc:
                click_info = {"clicked": False, "reason": f"final_rescue_ui_click_error: {exc}", "row_hint": hint, "final_rescue_pass": True}
        await page.wait_for_timeout(1400)
        new_events = browser.network_tab_events[before_count:]
        interactions, api_rows = collect_rule_api_interactions(new_events, stage_label="rule_ui_row_action_detail_final_rescue")
        matched = None
        for rec in api_rows:
            if _record_matches_rule(rec, row):
                matched = rec
                break
        if matched is None and len(api_rows) == 1:
            matched = api_rows[0]
        url_rule_id = _extract_numeric_rule_id_from_url_or_payload(page.url)
        event_url_rule_id = ""
        for ev in new_events:
            d = _event_dict(ev)
            event_url_rule_id = _extract_numeric_rule_id_from_url_or_payload(d.get("url")) or event_url_rule_id
            event_url_rule_id = _extract_numeric_rule_id_from_url_or_payload(d.get("response_body_redacted") or d.get("response_body_text_redacted")) or event_url_rule_id
            if event_url_rule_id:
                break
        rescue_audit = {
            "rule_name": row.get("rule_name"),
            "rule_identifier": row.get("rule_identifier"),
            "search_used": searched,
            "click": mask_sensitive_data(click_info),
            "network_events_after_click": len(new_events),
            "api_interactions_after_click": len(interactions),
            "api_rows_after_click": len(api_rows),
            "url_after_click": mask_sensitive_string(page.url),
            "url_rule_id_candidate": url_rule_id,
            "event_rule_id_candidate": event_url_rule_id,
            "resolved": False,
            "final_rescue_pass": True,
        }
        if matched:
            merged = _merge_rule_records([row, matched])[0]
            row.update(merged)
        if not row.get("rule_id"):
            expanded_text = str((click_info or {}).get("expanded_snapshot") or "")
            expanded_rule_id = _extract_numeric_rule_id_from_url_or_payload(expanded_text)
            candidate = event_url_rule_id or url_rule_id or expanded_rule_id
            if candidate:
                row["rule_id"] = candidate
                row["rule_id_source"] = "ui_row_action_final_rescue_network_or_url_or_expanded_dom"
            if expanded_text:
                row["ui_expanded_detail_text_compact"] = expanded_text[:1200]
        if row.get("rule_id"):
            rescue_audit["resolved"] = True
            found_this_phase += 1
        rescue_audit["api_interactions"] = interactions[:10]
        audit.append(rescue_audit)
        try:
            await _close_rule_transient_surfaces(page)
        except Exception:
            pass
        await _clear_rule_listing_search(page)
        if kb_dir and (rescue_idx % 3 == 0 or rescue_idx == len(unresolved_positions)):
            try:
                _write_json(kb_dir / "old_rules_inventory_with_ids.ui_checkpoint.json", rows)
                _write_json(kb_dir / "rule_ui_row_action_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    rows = _merge_rule_records(rows)
    with_ids = sum(1 for r in rows if r.get("rule_id"))
    report = {
        "ui_rows_attempted": limit,
        "ui_ids_found_this_phase": found_this_phase,
        "rule_ids_before_ui_phase": ids_before,
        "rule_ids_after_ui_phase": with_ids,
        "rule_ids_missing_after_ui_phase": max(0, len(rows) - with_ids),
        "final_rescue_rows_attempted": len(unresolved_positions),
        "completion_percent_after_ui_phase": round((with_ids / len(rows) * 100), 2) if rows else 0.0,
        "note": "UI row-action phase repeats the real portal interaction: search each old Rule, click safe View/Edit/Details/open row action, capture the API triggered by that click, and parse ruleId when the API/URL exposes it.",
    }
    return rows, audit, report



async def _find_rule_condition_add_candidate(page: Page) -> Dict[str, Any]:
    """Find the real row-level +Add button embedded in the `Conditions:` legend.

    The 172450 run proved the previous geometry fallback was still too broad: it
    selected a Rules listing-grid "Expand the row" icon behind/under the drawer.
    This implementation first resolves the visible Create Rule drawer, then the
    exact Conditions fieldset/legend, and only returns a button inside that legend
    whose tooltip/text/icon is Create Condition/add-cir. It never scans the
    background DDS table as a fallback.
    """
    js = r"""
() => {
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(); const st=getComputedStyle(el);
    return r.width>0 && r.height>0 && st.display!=='none' && st.visibility!=='hidden' && st.opacity !== '0';
  }
  function text(el){ return String(el && (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '') || '').replace(/\s+/g,' ').trim(); }
  function path(el){
    if(!el) return '';
    if(el.id) return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;
    const parts=[]; let n=el;
    for(let d=0;n && d<9 && n.nodeType===1;d++,n=n.parentElement){
      let part=n.tagName.toLowerCase();
      const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-/.test(c)).slice(0,3);
      if(cls.length) part+='.'+cls.map(c=>CSS.escape(c)).join('.');
      const p=n.parentElement;
      if(p){ const same=Array.from(p.children).filter(x=>x.tagName===n.tagName); if(same.length>1) part+=`:nth-of-type(${same.indexOf(n)+1})`; }
      parts.unshift(part);
    }
    return parts.join(' > ');
  }
  const rootCandidates = Array.from(document.querySelectorAll('app-generic-drawer,.dds__drawer,[role=dialog],form'))
    .filter(visible)
    .filter(r => /Create\s+Rule/i.test(text(r)) && /Conditions\s*:/i.test(text(r)));
  // Prefer the smallest visible drawer/form containing Create Rule + Conditions.
  rootCandidates.sort((a,b)=>{
    const ar=a.getBoundingClientRect(), br=b.getBoundingClientRect();
    return (ar.width*ar.height)-(br.width*br.height);
  });
  const root = rootCandidates[0];
  if(!root) return {ok:false, reason:'visible Create Rule drawer with Conditions not found'};

  const legends = Array.from(root.querySelectorAll('fieldset > legend, legend'))
    .filter(visible)
    .filter(el => /^Conditions\s*:/i.test(text(el)) || /Conditions\s*:\s*Create Condition/i.test(text(el)));
  const legend = legends[0];
  if(!legend) return {ok:false, reason:'Conditions legend not found inside Create Rule drawer'};

  const directButtons = Array.from(legend.querySelectorAll('button, dds-button button, [role=button]')).filter(visible);
  const good = directButtons.find(btn => {
    const t = text(btn) + ' ' + text(btn.closest('dds-button')) + ' ' + text(btn.querySelector('dds-tooltip'));
    const cls = String(btn.className||'') + ' ' + String(btn.getAttribute('class')||'') + ' ' + String(btn.querySelector('[class*=add],[class*=plus]')?.className || '');
    return /Create\s+Condition|add-cir|plus|\badd\b|\+/i.test(t + ' ' + cls) && !/save|submit|delete|deploy|cancel|close/i.test(t);
  });
  if(good) {
    const r=good.getBoundingClientRect();
    return {ok:true, selector:path(good), label:text(good) || 'Create Condition', method:'conditions_legend_direct_button', bbox:{x:r.x,y:r.y,width:r.width,height:r.height}};
  }
  return {ok:false, reason:'Conditions legend exists but no Create Condition/+Add button found', legend_text:text(legend)};
}
"""
    try:
        cand = await page.evaluate(js)
        return cand if isinstance(cand, dict) else {}
    except Exception as exc:
        return {"ok": False, "reason": mask_sensitive_string(str(exc))}


async def _inspect_rule_condition_rows(page: Page) -> List[Dict[str, Any]]:
    """Return one record per real Angular Conditions FormArray row.

    A DDS dropdown is represented by both a ``dds-dropdown`` host and a nested
    ``input[role=combobox]``.  Counting both nodes made one physical row look like
    two rows and caused row-2 input to overwrite row 1.

    The 20260720-191952 live run exposed the opposite edge case: after Angular
    revealed ``Attribute Name/Unit`` and its DDS popup was active, the outer
    FormArray wrapper/``dds-dropdown`` host could temporarily report a zero-sized
    bounding box even though the nested Condition Type input and the physical row
    were still present.  Filtering the wrapper/host by geometry therefore turned
    one real row into zero rows.  This inspector now anchors on the *visible nested
    Condition Type combobox*, walks up to its nearest CDK/DDS row, and deduplicates
    those physical row elements.  Host geometry is never used as the row-existence
    signal.
    """
    js = r"""
() => {
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(); const st=getComputedStyle(el);
    return r.width>0 && r.height>0 && st.display!=='none' && st.visibility!=='hidden' && st.opacity!=='0';
  }
  function clean(v){ return String(v||'').replace(/\s+/g,' ').trim(); }
  function path(el){
    if(!el) return '';
    if(el.id) return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;
    const parts=[]; let n=el;
    for(let d=0;n && d<10 && n.nodeType===1;d++,n=n.parentElement){
      let part=n.tagName.toLowerCase();
      const p=n.parentElement;
      if(p){ const same=Array.from(p.children).filter(x=>x.tagName===n.tagName); if(same.length>1) part+=`:nth-of-type(${same.indexOf(n)+1})`; }
      parts.unshift(part);
    }
    return parts.join(' > ');
  }
  function labelFor(el){
    if(el.id){ const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if(l) return clean(l.innerText||l.textContent); }
    const labelled=el.getAttribute('aria-labelledby');
    if(labelled){ const l=document.getElementById(labelled); if(l) return clean(l.innerText||l.textContent); }
    const host=el.closest('dds-dropdown,dds-input,dds-textarea');
    const l=host && host.querySelector('label,.dds__label');
    return clean((l&&(l.innerText||l.textContent))||el.getAttribute('name')||'');
  }
  const roots=Array.from(document.querySelectorAll('app-generic-drawer,.dds__drawer,[role=dialog],form'))
    .filter(visible).filter(r=>/Create\s+Rule/i.test(clean(r.innerText||r.textContent)) && /Conditions\s*:/i.test(clean(r.innerText||r.textContent)));
  roots.sort((a,b)=>{const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();return (ar.width*ar.height)-(br.width*br.height);});
  const root=roots[0]; if(!root) return [];
  const fieldset=Array.from(root.querySelectorAll('fieldset')).find(fs=>{const lg=fs.querySelector(':scope > legend');return lg&&/^Conditions\s*:/i.test(clean(lg.innerText||lg.textContent));});
  if(!fieldset) return [];
  const array=fieldset.querySelector('[formarrayname="conditions"]');
  if(!array) return [];
  const anchors=Array.from(array.querySelectorAll(
    'dds-dropdown[formcontrolname="conditionType"] input[role="combobox"],'
    +'[formcontrolname="conditionType"] input[role="combobox"],'
    +'dds-dropdown[name="conditionType"] input[role="combobox"]'
  )).filter(visible);
  const seen=new Set(); let rows=[];
  for(const anchor of anchors){
    // Prefer the inner CDK/DDS row.  The direct FormArray child is sometimes a
    // zero-sized Angular wrapper while a dependent dropdown popup is active.
    let row=anchor.closest('[cdkdrag],.dds__row');
    if(!row || !array.contains(row)){
      let n=anchor.parentElement;
      while(n && n.parentElement!==array) n=n.parentElement;
      row=n || anchor.closest('.dds__d-flex') || anchor.parentElement;
    }
    if(row && array.contains(row) && !seen.has(row)){ seen.add(row); rows.push(row); }
  }
  // Initial render fallback: some DDS versions delay the nested input for a
  // fraction of a frame.  Accept a direct child only when it contains a visible
  // Condition Type input; never count the custom-element host itself.
  if(!rows.length){
    for(const child of Array.from(array.children||[])){
      const anchor=child.querySelector(
        'dds-dropdown[formcontrolname="conditionType"] input[role="combobox"],'
        +'[formcontrolname="conditionType"] input[role="combobox"],'
        +'dds-dropdown[name="conditionType"] input[role="combobox"]'
      );
      if(anchor && visible(anchor)){
        const row=anchor.closest('[cdkdrag],.dds__row') || child;
        if(!seen.has(row)){ seen.add(row); rows.push(row); }
      }
    }
  }
  return rows.map((row,index)=>{
    const controls={};
    const els=Array.from(row.querySelectorAll('input:not([type=hidden]),textarea,select,[role=combobox]')).filter(visible);
    for(const el of els){
      const label=labelFor(el); const key=label.toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'');
      controls[key]={selector:path(el),id:el.id||'',label,value:clean(el.value||el.textContent||''),name:el.getAttribute('name')||'',form_control_name:(el.closest('[formcontrolname]')||el).getAttribute('formcontrolname')||''};
    }
    const type=controls.condition_type||{};
    return {row_index:index,row_selector:path(row),row_identity:type.id||type.selector||path(row),controls};
  });
}
"""
    try:
        rows = await page.evaluate(js)
        return [dict(x) for x in (rows or []) if isinstance(x, dict)]
    except Exception:
        return []


async def _count_rule_condition_rows(page: Page) -> int:
    """Count physical Angular Conditions rows, never DDS host/input nodes."""
    return len(await _inspect_rule_condition_rows(page))


async def _wait_for_rule_condition_row_count(page: Page, expected: int, *, timeout_ms: int = 5000) -> int:
    """Wait for Angular to expose the exact expected Conditions row count."""
    elapsed = 0
    last = await _count_rule_condition_rows(page)
    while elapsed < timeout_ms:
        if last == expected:
            return last
        await page.wait_for_timeout(200)
        elapsed += 200
        last = await _count_rule_condition_rows(page)
    return last


async def _rule_condition_popup_state(page: Page) -> Dict[str, Any]:
    """Inspect pending DDS interaction state inside the visible Conditions fieldset.

    In the 20260720-123425 live run, ``Attribute Name/Unit`` still had an open
    DDS search popup when the agent clicked ``Create Condition``.  The browser
    emitted only ``pointerdown``; that pointerdown committed/closed the dropdown,
    and no click reached the plus button.  Row creation therefore never happened.

    This helper is read-only.  It reports whether a Conditions combobox is still
    expanded, whether a visible listbox/popup exists, and which element owns focus.
    """
    js = r"""
() => {
  function clean(v){ return String(v||'').replace(/\s+/g,' ').trim(); }
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
    return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0');
  }
  function labelFor(el){
    if(!el) return '';
    const labelled=el.getAttribute && el.getAttribute('aria-labelledby');
    if(labelled){ const l=document.getElementById(labelled); if(l) return clean(l.innerText||l.textContent); }
    if(el.id){ const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if(l) return clean(l.innerText||l.textContent); }
    const host=el.closest && el.closest('dds-dropdown,dds-input,dds-textarea');
    const l=host && host.querySelector('label,.dds__label');
    return clean((l&&(l.innerText||l.textContent)) || el.getAttribute?.('name') || '');
  }
  const roots=Array.from(document.querySelectorAll('app-generic-drawer,.dds__drawer,[role=dialog],form'))
    .filter(visible).filter(r=>/Create\s+Rule/i.test(clean(r.innerText||r.textContent)) && /Conditions\s*:/i.test(clean(r.innerText||r.textContent)));
  roots.sort((a,b)=>{const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();return (ar.width*ar.height)-(br.width*br.height);});
  const root=roots[0];
  if(!root) return {surface_ok:false, expanded_count:0, visible_popup_count:0, active_label:'', active_tag:''};
  const fieldset=Array.from(root.querySelectorAll('fieldset')).find(fs=>{const lg=fs.querySelector(':scope > legend');return lg&&/^Conditions\s*:/i.test(clean(lg.innerText||lg.textContent));});
  if(!fieldset) return {surface_ok:false, expanded_count:0, visible_popup_count:0, active_label:'', active_tag:''};
  const expanded=Array.from(fieldset.querySelectorAll('[role=combobox][aria-expanded=true],input[aria-expanded=true]')).filter(visible);
  const popups=Array.from(fieldset.querySelectorAll('[role=listbox],.dds__dropdown__popup,.dds__popover,.dds__menu')).filter(visible);
  const active=document.activeElement;
  return {
    surface_ok:true,
    expanded_count:expanded.length,
    expanded_labels:expanded.map(labelFor),
    visible_popup_count:popups.length,
    popup_text_samples:popups.map(x=>clean(x.innerText||x.textContent).slice(0,160)),
    active_label:labelFor(active),
    active_tag:active ? String(active.tagName||'').toLowerCase() : '',
    active_role:active && active.getAttribute ? (active.getAttribute('role')||'') : ''
  };
}
"""
    try:
        out = await page.evaluate(js)
        return out if isinstance(out, dict) else {}
    except Exception as exc:
        return {"surface_ok": False, "error": mask_sensitive_string(str(exc))}


async def _settle_rule_condition_dropdowns(page: Page) -> Dict[str, Any]:
    """Commit and close any pending DDS Conditions popup without clicking the page shell.

    The sequence is deliberately non-destructive: Escape, explicit change/blur,
    then the shared non-clicking dropdown closer.  It never clicks Save/Create,
    never clicks the overlay, and never mutates the Angular FormArray directly.
    """
    before = await _rule_condition_popup_state(page)
    keyboard_used = False
    popup_was_open = bool(
        int(before.get("expanded_count") or 0) > 0
        or int(before.get("visible_popup_count") or 0) > 0
    )
    # Do not send Escape to a focused-but-collapsed DDS combobox.  The
    # 20260720-191952 portal state showed that Escape could race with a delayed
    # DDS open transition and *open* the Receiver popup instead of settling it.
    # Escape is reserved for proof of an actually expanded/visible popup.
    if popup_was_open:
        try:
            keyboard = getattr(page, "keyboard", None)
            if keyboard is not None:
                await keyboard.press("Escape")
                keyboard_used = True
        except Exception:
            keyboard_used = False
    try:
        await page.evaluate(r"""
() => {
  const el=document.activeElement;
  if(el && el!==document.body){
    try { el.dispatchEvent(new Event('input',{bubbles:true})); } catch(e) {}
    try { el.dispatchEvent(new Event('change',{bubbles:true})); } catch(e) {}
    try { el.dispatchEvent(new FocusEvent('focusout',{bubbles:true})); } catch(e) {}
    try { el.blur(); } catch(e) {}
  }
  return true;
}
""")
    except Exception:
        pass
    await close_open_dropdown(page, "rule")
    try:
        await page.wait_for_timeout(250)
    except Exception:
        pass
    after = await _rule_condition_popup_state(page)
    settled = bool(
        after.get("surface_ok")
        and int(after.get("expanded_count") or 0) == 0
        and int(after.get("visible_popup_count") or 0) == 0
    )
    return mask_sensitive_data({
        "before": before,
        "after": after,
        "popup_was_open": popup_was_open,
        "keyboard_escape_used": keyboard_used,
        "settled": settled,
        "safety": "Escape only for a proven open popup, then change/blur; no overlay/page-shell click",
    })


async def _arm_rule_condition_add_click_probe(page: Page, selector: str, token: str) -> bool:
    """Attach a capture-phase probe for the exact Conditions plus button.

    Dell DDS renders the visible plus as a nested ``span.dds__icon--add-cir``.
    The 20260720-214635 live run showed the document-level click recorder seeing
    that span while an element-local listener on the parent button saw nothing.
    DDS can stop bubbling or replace the internal button during tooltip/focus
    work.  A capture listener on the owning fieldset proves that the exact
    button *or one of its descendants* received the activation without relying
    on bubbling through the custom component.
    """
    try:
        return bool(await page.evaluate(r"""
({selector,token}) => {
  const el=document.querySelector(selector);
  if(!el) return false;
  const root=el.closest('fieldset') || el.closest('form') || document;
  window.__hipConditionAddClickProbe = {token, armed:true, clicked:false, at:Date.now(), selector};
  const handler = ev => {
    const live=document.querySelector(selector);
    if(!live) return;
    const target=ev.target;
    if(target===live || (target && live.contains(target))){
      window.__hipConditionAddClickProbe = {
        token, armed:true, clicked:true, trusted:!!ev.isTrusted, at:Date.now(),
        selector,
        targetTag:String(target && target.tagName || '').toLowerCase(),
        targetClass:String(target && target.className || '')
      };
      try { root.removeEventListener('click', handler, true); } catch(e) {}
    }
  };
  root.addEventListener('click', handler, true);
  return true;
}
""", {"selector": selector, "token": token}))
    except Exception:
        return False


async def _read_rule_condition_add_click_probe(page: Page, token: str) -> Dict[str, Any]:
    try:
        out = await page.evaluate(r"""
(token) => {
  const p=window.__hipConditionAddClickProbe || {};
  return p.token===token ? p : {token, armed:false, clicked:false};
}
""", token)
        return out if isinstance(out, dict) else {"token": token, "armed": False, "clicked": False}
    except Exception:
        return {"token": token, "armed": False, "clicked": False}


async def _inspect_rule_create_form_validity(page: Page) -> Dict[str, Any]:
    """Return bounded validity evidence for the foreground Create Rule form.

    HIP's ``Create Condition`` handler is present even when the Angular form is
    invalid, but in the live portal it performs no row addition until the base
    Rule and default Action row are valid.  This inspector is diagnostic and
    fail-closed only when a foreground form is positively identified as
    ``ng-invalid``.  Unknown/custom test pages remain non-blocking.
    """
    try:
        out = await page.evaluate(r"""
() => {
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(), st=getComputedStyle(el);
    return r.width>0 && r.height>0 && st.display!=='none' && st.visibility!=='hidden' && st.opacity!=='0';
  }
  function clean(v){ return String(v||'').replace(/\s+/g,' ').trim(); }
  function labelFor(el){
    if(el.id){ const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if(l) return clean(l.innerText||l.textContent); }
    const ids=clean(el.getAttribute('aria-labelledby')).split(/\s+/).filter(Boolean);
    const txt=ids.map(id=>{const n=document.getElementById(id);return n?clean(n.innerText||n.textContent):'';}).filter(Boolean).join(' ');
    if(txt) return txt;
    const host=el.closest('dds-dropdown,dds-input,dds-textarea,dds-switch');
    const l=host && host.querySelector('label,.dds__label');
    return clean((l&&(l.innerText||l.textContent))||el.getAttribute('name')||el.getAttribute('formcontrolname')||'');
  }
  const forms=Array.from(document.querySelectorAll('form')).filter(visible)
    .filter(f=>/Create\s+Rule/i.test(clean((f.closest('app-generic-drawer,.dds__drawer,[role=dialog]')||f).innerText||'')) && /Conditions\s*:/i.test(clean(f.innerText||f.textContent)));
  const form=forms[0];
  if(!form) return {known:false, valid:null, reason:'foreground Create Rule form not found'};
  const classes=String(form.className||'');
  const angularValid=form.classList.contains('ng-valid') ? true : (form.classList.contains('ng-invalid') ? false : null);
  let nativeValid=null;
  try { nativeValid=typeof form.checkValidity==='function' ? !!form.checkValidity() : null; } catch(e) {}
  const invalid=Array.from(form.querySelectorAll('[aria-invalid="true"],.ng-invalid'))
    .filter(el=>visible(el) && /^(input|select|textarea|button)$/i.test(el.tagName||'') || visible(el.querySelector && el.querySelector('input,select,textarea')))
    .slice(0,20)
    .map(el=>{
      const ctl=/^(input|select|textarea|button)$/i.test(el.tagName||'')?el:el.querySelector('input,select,textarea,button');
      return {label:labelFor(ctl||el), name:clean((ctl||el).getAttribute('name')||(ctl||el).getAttribute('formcontrolname')), ariaInvalid:clean((ctl||el).getAttribute('aria-invalid'))};
    });
  return {known:true, valid:angularValid!==null?angularValid:nativeValid, angularValid, nativeValid, classes, invalidControls:invalid};
}
""")
        return out if isinstance(out, dict) else {"known": False, "valid": None, "reason": "invalid inspector result"}
    except Exception as exc:
        return {"known": False, "valid": None, "reason": mask_sensitive_string(str(exc))}


async def _click_rule_condition_add_exact(
    page: Page,
    *,
    expected_rows_prefix: List[Dict[str, Any]],
    max_click_attempts: int = 2,
) -> Dict[str, Any]:
    """Click Conditions ``+`` with popup settlement and exact ``N -> N+1`` proof.

    A second click is allowed only when the first attempt produced no row, the
    existing rows still exactly match input, and the dedicated button is rebound.
    This bounded retry handles Dell DDS swallowing the first click to finish a
    pending dropdown commit.  It cannot branch into uncontrolled browser actions.
    """
    settle_before = await _settle_rule_condition_dropdowns(page)
    before_rows = await _inspect_rule_condition_rows(page)
    before = len(before_rows)
    prefix_proof = _rule_condition_rows_exact(before_rows, expected_rows_prefix)
    entry: Dict[str, Any] = {
        "row_count_before": before,
        "row_count_after": before,
        "row_identities_before": [str(r.get("row_identity") or "") for r in before_rows],
        "settle_before": settle_before,
        "existing_rows_exact_before_click": bool(prefix_proof.get("pass")),
        "existing_rows_proof": prefix_proof,
        "clicked": False,
        "exact_plus_one": False,
        "physical_click_attempts": [],
    }
    if before != len(expected_rows_prefix) or not prefix_proof.get("pass"):
        entry["reason"] = "existing Conditions rows are not exact; refusing Create Condition"
        return mask_sensitive_data(entry)

    form_validity = await _inspect_rule_create_form_validity(page)
    entry["form_validity_before_click"] = form_validity
    if form_validity.get("known") and form_validity.get("valid") is False:
        entry["reason"] = "Create Rule form is still invalid; refusing ineffective Create Condition click"
        return mask_sensitive_data(entry)

    before_identities = list(entry["row_identities_before"])
    for click_no in range(1, max(1, int(max_click_attempts)) + 1):
        cand = await _find_rule_condition_add_candidate(page)
        selector = str((cand or {}).get("selector") or "")
        label = str((cand or {}).get("label") or "")
        safe_row_create = bool(re.search(r"create\s+condition|add-cir|\badd\b|\+", label, re.I))
        click_audit: Dict[str, Any] = {
            "attempt": click_no,
            "candidate": cand,
            "row_count_before": before,
            "clicked_event_seen": False,
        }
        if not selector or not safe_row_create or re.search(r"\b(save|submit|delete|remove|deploy|enable|disable|confirm|publish|update)\b", label, re.I):
            click_audit["reason"] = (cand or {}).get("reason") or "no safe Conditions + candidate found"
            entry["physical_click_attempts"].append(click_audit)
            entry["reason"] = click_audit["reason"]
            break
        token = f"condition-add-{click_no}-{before}"
        click_audit["probe_armed"] = await _arm_rule_condition_add_click_probe(page, selector, token)
        try:
            loc = page.locator(selector).first
            await loc.scroll_into_view_if_needed(timeout=2000)
            if semantic_runtime_enabled(page):
                if not await open_control_for_discovery(
                    # The plus is named only by its "Create Condition" tooltip; the
                    # broker label must not read as a final mutation (V243R20).
                    page, selector, label="structural_opener add row Conditions", phase="rule"
                ):
                    raise RuntimeError("semantic Create Condition dispatch failed closed")
            else:
                await loc.click(timeout=3000)
        except Exception as exc:
            click_audit["reason"] = mask_sensitive_string(str(exc))
            entry["physical_click_attempts"].append(click_audit)
            entry["reason"] = click_audit["reason"]
            break

        try:
            await page.wait_for_timeout(120)
        except Exception:
            pass
        probe = await _read_rule_condition_add_click_probe(page, token)
        click_audit["click_probe"] = probe
        click_audit["clicked_event_seen"] = bool(probe.get("clicked"))
        after = await _wait_for_rule_condition_row_count(page, before + 1, timeout_ms=5000)
        after_rows = await _inspect_rule_condition_rows(page)
        after_identities = [str(r.get("row_identity") or "") for r in after_rows]
        new_identities = [x for x in after_identities if x and x not in before_identities]
        exact_plus_one = after == before + 1 and len(after_rows) == before + 1 and len(new_identities) == 1
        click_audit.update({
            "row_count_after": after,
            "row_identities_after": after_identities,
            "new_row_identities": new_identities,
            "exact_plus_one": exact_plus_one,
        })
        entry["physical_click_attempts"].append(click_audit)
        entry["row_count_after"] = after
        entry["row_identities_after"] = after_identities
        entry["new_row_identities"] = new_identities
        if exact_plus_one:
            entry["clicked"] = True
            entry["exact_plus_one"] = True
            entry["successful_physical_click_attempt"] = click_no
            return mask_sensitive_data(entry)

        # Wrong non-zero transition is unsafe and must never be retried.
        if after != before:
            entry["reason"] = f"Conditions + produced an unsafe row-count transition: {before} -> {after}"
            break

        # The only retryable case is no transition. Re-verify the existing rows,
        # settle the popup again, and rebind the exact Conditions plus button.
        live_rows = await _inspect_rule_condition_rows(page)
        live_proof = _rule_condition_rows_exact(live_rows, expected_rows_prefix)
        click_audit["existing_rows_exact_after_noop"] = bool(live_proof.get("pass"))
        click_audit["noop_existing_rows_proof"] = live_proof
        if click_no >= max_click_attempts or not live_proof.get("pass"):
            entry["reason"] = (
                "Create Condition click was swallowed/no-op and existing rows are no longer exact"
                if not live_proof.get("pass")
                else "Create Condition did not receive an effective click after bounded retry"
            )
            break
        click_audit["settle_before_retry"] = await _settle_rule_condition_dropdowns(page)

    if not entry.get("reason"):
        entry["reason"] = f"Conditions + did not create one distinct Angular FormArray row: {before} -> {entry.get('row_count_after', before)}"
    return mask_sensitive_data(entry)



async def _select_rule_condition_attribute_exact(
    page: Page,
    *,
    selector: str,
    value: str,
    occurrence: int,
    field: str,
) -> Dict[str, Any]:
    """Select one Rule Attribute Name/Unit from its own DDS popup only.

    Rule pages keep the background Document Type accordion and hundreds of
    checkboxes in the DOM while the Create Rule drawer is open.  A page-global
    text search can therefore mistake a document-type checkbox containing words
    such as ``Receiver`` for a dropdown option.  This transaction follows only
    the popup owned by the requested Attribute Name/Unit combobox and verifies
    the committed control value before returning success.
    """
    attempt: Dict[str, Any] = {
        "field": field,
        "section": "Conditions",
        "label": "Attribute Name/Unit",
        "occurrence": occurrence,
        "selector": selector,
        "value_redacted": mask_sensitive_string(str(value)),
        "filled": False,
        "executor": "rule.conditions.strict_owned_dds_popup",
        "popup_attempts": [],
    }
    if not selector or not str(value or "").strip():
        attempt["reason"] = "missing Attribute Name/Unit selector or value"
        return mask_sensitive_data(attempt)

    def _norm(v: Any) -> str:
        return re.sub(r"\s+", " ", str(v or "")).strip().lower()

    async def _fresh_attribute_value() -> Dict[str, Any]:
        """Read the committed value from a freshly rebound physical row.

        Selecting an Attribute option can rerender the Angular row and replace
        the original input id.  Verifying only through the pre-click locator can
        therefore report an empty value even when the new row control contains
        the committed option.  Conversely, text visible in the popup is not proof
        of a commit.  Re-inspection gives us a fresh selector plus the live value
        from the exact requested FormArray row.
        """
        rows = await _inspect_rule_condition_rows(page)
        if occurrence < 0 or occurrence >= len(rows):
            return {"row_found": False, "row_count": len(rows), "value": "", "selector": ""}
        row = rows[occurrence]
        controls = row.get("controls") if isinstance(row.get("controls"), dict) else {}
        control = controls.get("attribute_name_unit") if isinstance(controls, dict) else None
        return {
            "row_found": True,
            "row_count": len(rows),
            "row_identity": row.get("row_identity"),
            "value": str((control or {}).get("value") or ""),
            "selector": str((control or {}).get("selector") or ""),
        }

    async def _verify_fresh_commit(entry: Dict[str, Any]) -> bool:
        # A real DDS option click may replace the input node.  Tab provides a
        # bounded native commit/blur signal; the authoritative proof is the fresh
        # physical-row inspection that follows.
        try:
            keyboard = getattr(page, "keyboard", None)
            if keyboard is not None:
                await keyboard.press("Tab")
        except Exception:
            pass
        try:
            await page.wait_for_timeout(500)
        except Exception:
            pass
        fresh = await _fresh_attribute_value()
        entry["fresh_row_state"] = mask_sensitive_data(fresh)
        actual = str(fresh.get("value") or "")
        if not actual:
            try:
                actual = str(await _read_control_value(loc) or "")
            except Exception:
                actual = ""
        entry["actual_after_fresh_rebind"] = mask_sensitive_string(actual)
        verified = _norm(actual) == _norm(wanted)
        entry["verified"] = verified
        if verified:
            fresh_selector = str(fresh.get("selector") or selector)
            await _lock_filled_value(page, fresh_selector, wanted, combo=True)
        return verified

    loc = page.locator(selector).first
    try:
        if not await loc.count():
            attempt["reason"] = "Attribute Name/Unit control no longer exists"
            return mask_sensitive_data(attempt)
    except Exception as exc:
        attempt["reason"] = mask_sensitive_string(str(exc))
        return mask_sensitive_data(attempt)

    popup_probe_js = r"""
({selector,wanted}) => {
  function clean(v){ return String(v||'').replace(/\s+/g,' ').trim(); }
  function low(v){ return clean(v).toLowerCase(); }
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
    return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0');
  }
  function path(el){
    if(!el) return '';
    if(el.id) return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;
    const parts=[]; let n=el;
    for(let d=0;n && d<10 && n.nodeType===1;d++,n=n.parentElement){
      let part=n.tagName.toLowerCase(); const p=n.parentElement;
      if(p){ const same=Array.from(p.children).filter(x=>x.tagName===n.tagName); if(same.length>1) part+=`:nth-of-type(${same.indexOf(n)+1})`; }
      parts.unshift(part);
    }
    return parts.join(' > ');
  }
  const control=document.querySelector(selector);
  if(!control) return {control_found:false};
  const ownedIds=[];
  for(const attr of ['aria-controls','aria-owns']){
    for(const id of String(control.getAttribute(attr)||'').split(/\s+/).filter(Boolean)) ownedIds.push(id);
  }
  const host=control.closest('dds-dropdown,app-generic-dropdown');
  if(host){
    for(const attr of ['aria-controls','aria-owns']){
      for(const id of String(host.getAttribute(attr)||'').split(/\s+/).filter(Boolean)) ownedIds.push(id);
    }
  }
  let popups=ownedIds.map(id=>document.getElementById(id)).filter(Boolean).filter(visible);
  if(!popups.length){
    const cr=control.getBoundingClientRect();
    popups=Array.from(document.querySelectorAll('div[id^="dropdown-popup-list-"], [role=listbox]')).filter(visible);
    popups.sort((a,b)=>{
      const ar=a.getBoundingClientRect(), br=b.getBoundingClientRect();
      const ad=Math.abs(ar.left-cr.left)+Math.abs(ar.top-cr.bottom);
      const bd=Math.abs(br.left-cr.left)+Math.abs(br.top-cr.bottom);
      return ad-bd;
    });
  }
  const popup=popups[0]||null;
  if(!popup) return {control_found:true, popup_found:false, owned_ids:ownedIds};
  const options=Array.from(popup.querySelectorAll('[role=option], dds-dropdown-option button, .dds__dropdown__item, .dds__list__item'))
    .filter(visible)
    .map(el=>({selector:path(el), text:clean(el.innerText||el.textContent), disabled:el.getAttribute('aria-disabled')==='true'||el.disabled===true}));
  const target=low(wanted);
  const exact=options.find(o=>!o.disabled && low(o.text)===target);
  const folded=options.find(o=>!o.disabled && (low(o.text).includes(target)||target.includes(low(o.text))));
  return {
    control_found:true,
    popup_found:true,
    popup_selector:path(popup),
    popup_text:clean(popup.innerText||popup.textContent).slice(0,300),
    option_count:options.length,
    options:options.slice(0,30),
    selected_option:(exact||folded||null),
    no_options:/no options found/i.test(clean(popup.innerText||popup.textContent))
  };
}
"""

    wanted = str(value).strip()
    if semantic_runtime_enabled(page):
        semantic_entry: Dict[str, Any] = {"try": 1, "executor": "semantic_dds_driver"}
        try:
            root = await get_active_form_root(page, "rule")
            selected = await select_dds_combobox(page, root, selector, wanted, phase="rule")
            semantic_entry["selected"] = bool(selected)
            if selected and await _verify_fresh_commit(semantic_entry):
                attempt["popup_attempts"].append(semantic_entry)
                attempt["filled"] = True
                attempt["executor"] = "rule.conditions.semantic_dds_driver"
                attempt["selected_option"] = mask_sensitive_string(wanted)
                return mask_sensitive_data(attempt)
            semantic_entry.setdefault("reason", "semantic DDS selection/effect verification failed")
        except Exception as exc:
            semantic_entry["reason"] = "semantic DDS selection failed closed"
            semantic_entry["error"] = mask_sensitive_string(str(exc))
        attempt["popup_attempts"].append(semantic_entry)
        attempt["reason"] = semantic_entry.get("reason") or "semantic DDS selection failed closed"
        return mask_sensitive_data(attempt)
    for try_no in range(1, 4):
        probe: Dict[str, Any] = {}
        try:
            await loc.scroll_into_view_if_needed(timeout=2000)
            await loc.click(timeout=3000)
            await page.wait_for_timeout(300 if try_no == 1 else 550)
            probe = await page.evaluate(popup_probe_js, {"selector": selector, "wanted": wanted})
            if not isinstance(probe, dict):
                probe = {}
        except Exception as exc:
            probe = {"error": mask_sensitive_string(str(exc))}

        selected = probe.get("selected_option") if isinstance(probe.get("selected_option"), dict) else {}
        option_selector = str(selected.get("selector") or "")
        entry: Dict[str, Any] = {
            "try": try_no,
            "popup_found": bool(probe.get("popup_found")),
            "popup_selector": probe.get("popup_selector"),
            "option_count": int(probe.get("option_count") or 0),
            "no_options": bool(probe.get("no_options")),
            "selected_option_text": mask_sensitive_string(str(selected.get("text") or "")),
            "owned_popup_only": True,
        }
        if option_selector:
            try:
                option_loc = page.locator(option_selector).first
                try:
                    await option_loc.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                await option_loc.click(timeout=2500)
                entry["option_click_sent"] = True
                entry["verified"] = await _verify_fresh_commit(entry)
                attempt["popup_attempts"].append(entry)
                if entry["verified"]:
                    attempt["filled"] = True
                    attempt["selected_option"] = mask_sensitive_string(str(selected.get("text") or ""))
                    attempt["dom_events"] = ["click", "owned-option/click", "change", "blur", "fresh-row/rebind"]
                    return mask_sensitive_data(attempt)
            except Exception as exc:
                entry["click_error"] = mask_sensitive_string(str(exc))
        else:
            # Search only inside this DDS control.  The subsequent probe remains
            # constrained to the popup owned by this same combobox.
            try:
                await loc.fill(wanted, timeout=1800)
                await page.wait_for_timeout(500)
                probe2 = await page.evaluate(popup_probe_js, {"selector": selector, "wanted": wanted})
                selected2 = probe2.get("selected_option") if isinstance(probe2, dict) and isinstance(probe2.get("selected_option"), dict) else {}
                option_selector2 = str(selected2.get("selector") or "")
                entry["search_option_count"] = int((probe2 or {}).get("option_count") or 0) if isinstance(probe2, dict) else 0
                entry["search_no_options"] = bool((probe2 or {}).get("no_options")) if isinstance(probe2, dict) else False
                if option_selector2:
                    option_loc2 = page.locator(option_selector2).first
                    try:
                        await option_loc2.scroll_into_view_if_needed(timeout=1500)
                    except Exception:
                        pass
                    await option_loc2.click(timeout=2500)
                    entry["search_option_click_sent"] = True
                    entry["verified"] = await _verify_fresh_commit(entry)
                    if entry["verified"]:
                        attempt["popup_attempts"].append(entry)
                        attempt["filled"] = True
                        attempt["selected_option"] = mask_sensitive_string(str(selected2.get("text") or ""))
                        attempt["dom_events"] = ["click", "input", "owned-option/click", "change", "blur", "fresh-row/rebind"]
                        return mask_sensitive_data(attempt)
            except Exception as exc:
                entry["search_error"] = mask_sensitive_string(str(exc))
        attempt["popup_attempts"].append(entry)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            await loc.fill("", timeout=1000)
        except Exception:
            pass
        await page.wait_for_timeout(300)

    await close_open_dropdown(page, "rule")
    attempt["reason"] = "expected attribute was not available in the DDS popup owned by Attribute Name/Unit"
    return mask_sensitive_data(attempt)


async def _fill_rule_condition_row(
    page: Page,
    row: Dict[str, Any],
    *,
    row_index: int,
) -> List[Dict[str, Any]]:
    """Fill one Rule Conditions row in dependency order.

    ``Condition Type`` is committed first because it reveals Attribute Name/Unit.
    The remaining values are then filled into the same row occurrence.
    """
    attempts: List[Dict[str, Any]] = []
    attempts.append(await _set_rule_control_by_label(
        page, section="Conditions", label="Condition Type",
        value=str(row.get("condition_type") or "Attributes"), occurrence=row_index,
        field=f"conditions[{row_index}].condition_type",
    ))
    # DDS/Angular may replace the row after Condition Type changes.
    await page.wait_for_timeout(350)
    attempts.append(await _set_rule_control_by_label(
        page, section="Conditions", label="Operator",
        value=str(row.get("operator") or "Equals"), occurrence=row_index,
        field=f"conditions[{row_index}].operator",
    ))
    attempts.append(await _set_rule_control_by_label(
        page, section="Conditions", label="Value",
        value=str(row.get("value") or ""), occurrence=row_index,
        field=f"conditions[{row_index}].value",
    ))
    attribute = str(row.get("attribute_name_unit") or row.get("attribute_name") or "")
    if attribute:
        attempts.append(await _set_rule_control_by_label(
            page, section="Conditions", label="Attribute Name/Unit",
            value=attribute, occurrence=row_index,
            field=f"conditions[{row_index}].attribute_name_unit",
        ))
    return attempts


def _rule_condition_live_value(row: Dict[str, Any], key: str) -> str:
    controls = row.get("controls") if isinstance(row.get("controls"), dict) else {}
    control = controls.get(key) if isinstance(controls, dict) else None
    return re.sub(r"\s+", " ", str((control or {}).get("value") or "")).strip()


def _rule_condition_rows_exact(rows: List[Dict[str, Any]], expected_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    proofs: List[Dict[str, Any]] = []
    for idx, expected in enumerate(expected_rows):
        live = rows[idx] if idx < len(rows) else {}
        expected_map = {
            "condition_type": str(expected.get("condition_type") or "Attributes"),
            "operator": str(expected.get("operator") or "Equals"),
            "value": str(expected.get("value") or ""),
            "attribute_name_unit": str(expected.get("attribute_name_unit") or expected.get("attribute_name") or ""),
        }
        actual_map = {key: _rule_condition_live_value(live, key) for key in expected_map}
        field_pass = {
            key: re.sub(r"\s+", " ", expected_map[key]).strip().lower() == re.sub(r"\s+", " ", actual_map[key]).strip().lower()
            for key in expected_map
        }
        proofs.append({
            "row_index": idx,
            "row_identity": live.get("row_identity"),
            "expected": mask_sensitive_data(expected_map),
            "actual": mask_sensitive_data(actual_map),
            "field_pass": field_pass,
            "pass": bool(live) and all(field_pass.values()),
        })
    identities = [str(r.get("row_identity") or "") for r in rows]
    distinct = len(identities) == len(set(identities)) and all(identities)
    return mask_sensitive_data({
        "row_count_pass": len(rows) == len(expected_rows),
        "distinct_row_identity_pass": bool(distinct),
        "rows": proofs,
        "pass": len(rows) == len(expected_rows) and bool(distinct) and all(p.get("pass") for p in proofs),
    })



async def _rule_name_duplicate_state(page: Page) -> Dict[str, Any]:
    """Inspect the foreground Rule Name validation without touching the form."""
    try:
        out = await page.evaluate(r"""
() => {
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(), st=getComputedStyle(el);
    return r.width>0 && r.height>0 && st.display!=='none' && st.visibility!=='hidden' && st.opacity!=='0';
  }
  function clean(v){ return String(v||'').replace(/\s+/g,' ').trim(); }
  const roots=Array.from(document.querySelectorAll('app-generic-drawer,.dds__drawer,[role=dialog],form'))
    .filter(visible)
    .filter(el=>/Create\s+Rule/i.test(clean(el.innerText||el.textContent)));
  const root=roots[0]||document.body;
  const input=root.querySelector('input[name="ruleName"],input[formcontrolname="ruleName"]');
  if(!input) return {known:false, duplicate:false, value:'', message:'', reason:'Rule Name input not found'};
  const field=input.closest('.dds__input-text__container,.dds__form__field,.form-group,div')||input.parentElement;
  const local=clean((field&&field.innerText)||'');
  const nearby=clean(((field&&field.parentElement)&&field.parentElement.innerText)||'');
  const message=[local,nearby].find(t=>/rule\s+name\s+already\s+exists/i.test(t))||'';
  return {
    known:true,
    duplicate:/rule\s+name\s+already\s+exists/i.test(message),
    value:String(input.value||''),
    message:message.slice(0,500),
    aria_invalid:input.getAttribute('aria-invalid')||'',
    selector:input.id?`input#${CSS.escape(input.id)}`:'input[name="ruleName"]'
  };
}
""")
        return out if isinstance(out, dict) else {"known": False, "duplicate": False, "reason": "invalid duplicate-state result"}
    except Exception as exc:
        return {"known": False, "duplicate": False, "reason": mask_sensitive_string(str(exc))}


async def _wait_for_rule_name_duplicate_state(
    page: Page,
    *,
    timeout_ms: int = 6000,
) -> Dict[str, Any]:
    """Wait for the asynchronous Rule-name validator to settle.

    The DEV portal may render ``Rule Name already exists`` several seconds after
    the text input itself has been committed.  Treating the first quiet 400 ms as
    proof of uniqueness leaves the form Angular-invalid and makes Conditions ``+``
    a silent no-op.  This wait returns immediately on a duplicate message, or once
    the Rule Name control is visibly valid and no validation request/loading state
    remains.
    """
    # Lightweight unit/fallback pages may intentionally omit Playwright's
    # evaluate() API.  They cannot observe the asynchronous Angular validator,
    # so spending the full live-portal timeout provides no additional evidence.
    # Real Playwright Page objects always expose evaluate(), preserving the full
    # late-validation wait in authenticated HIP runs.
    if not callable(getattr(page, "evaluate", None)):
        state = await _rule_name_duplicate_state(page)
        state = state if isinstance(state, dict) else {}
        state["settled"] = bool(state.get("known"))
        state["settled_without_dom_probe"] = True
        return state

    deadline = asyncio.get_running_loop().time() + max(0.5, timeout_ms / 1000.0)
    last: Dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        state = await _rule_name_duplicate_state(page)
        last = state if isinstance(state, dict) else {}
        if last.get("duplicate"):
            last["settled"] = True
            return last
        try:
            settled = await page.evaluate(r"""
() => {
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(), s=getComputedStyle(el);
    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
  }
  const input=document.querySelector('app-configure-rules input[name="ruleName"],app-configure-rules input[formcontrolname="ruleName"]');
  if(!input) return false;
  const host=input.closest('dds-input,[formcontrolname="ruleName"]');
  const loading=Array.from(document.querySelectorAll('.dds__loading-indicator__container,[aria-busy="true"]')).some(visible);
  const valid=!!(host && host.classList.contains('ng-valid')) || input.getAttribute('aria-invalid')==='false';
  return valid && !loading;
}
""")
        except Exception:
            # Lightweight unit-test/fallback pages may not expose evaluate().
            # In the real portal evaluate is available and the async validator is
            # observed directly; without it, a known non-empty state is the best
            # available bounded proof.
            if last.get("known") and str(last.get("value") or ""):
                last["settled"] = True
                last["settled_without_dom_probe"] = True
                return last
            settled = False
        if settled:
            last["settled"] = True
            return last
        await page.wait_for_timeout(250)
    last["settled"] = False
    last["wait_timeout_ms"] = timeout_ms
    return last


def _temporary_rule_name_for_structure(original: str) -> str:
    """Return a bounded unique unsaved Rule Name for structural +Add actions."""
    raw = re.sub(r"\s+", "_", str(original or "RULE")).strip("_") or "RULE"
    token = hashlib.sha1(f"{raw}|{utc_now()}".encode("utf-8")).hexdigest()[:8].upper()
    suffix = f"__PROBE_{token}"
    # HIP Rule names observed in DEV fit comfortably within 64 characters.
    return f"{raw[:max(1, 64-len(suffix))]}{suffix}"


def _rule_mapping_value_matches(actual: str, variants: Iterable[str]) -> bool:
    """Return True only when a Mapping Identifier value is exactly committed.

    Typing into a DDS combobox changes ``input.value`` before Angular commits an
    option, so callers must combine this check with owned-option selection
    evidence (``aria-selected``/``data-selected``) or a closed popup.
    """
    current = re.sub(r"\s+", " ", str(actual or "")).strip().lower()
    if not current or current in {"select", "select...", "choose", "--select--"}:
        return False
    for variant in variants:
        wanted = re.sub(r"\s+", " ", str(variant or "")).strip().lower()
        if wanted and (current == wanted or current.startswith(wanted + "(")):
            return True
    return False


async def _finalize_rule_mapping_identifier_selection(
    page: Page,
    *,
    selector: str,
    wanted: str,
    variants: Iterable[str],
) -> Dict[str, Any]:
    """Settle and verify a Mapping Identifier that DDS already selected.

    In the live HIP drawer the option click can update Angular and mark the
    option selected, yet Playwright's ``Locator.click`` waits until its timeout
    because the DDS handler does not return promptly.  Treating that timeout as
    failure causes the autonomous agent to click an already-selected option over
    and over.  This helper rebinds the rerendered control, emits only safe
    commit/blur events, closes the owned popup, and verifies the final value.
    """
    details: Dict[str, Any] = {"verified": False, "selector": selector}
    fresh = await _wait_for_rule_control_selector(
        page,
        section="Actions",
        label="Mapping Identifier Name (Version)",
        occurrence=0,
        timeout_ms=10000,
    )
    fresh_selector = str((fresh or {}).get("selector") or selector)
    details["fresh_selector"] = fresh_selector
    loc = page.locator(fresh_selector).first
    if not await loc.count():
        details["reason"] = "fresh Mapping Identifier control was not found"
        return details

    actual_before = str(await _read_control_value(loc) or "")
    details["actual_before_settle"] = mask_sensitive_string(actual_before)
    if not _rule_mapping_value_matches(actual_before, variants):
        details["reason"] = "fresh Mapping Identifier value is not exact before settle"
        return details

    # No page-shell or overlay click is used.  These events only commit the
    # already-selected value and release the combobox focus.
    emitted: List[str] = []
    for event_name in ("change", "blur"):
        try:
            await loc.dispatch_event(event_name)
            emitted.append(event_name)
        except Exception:
            pass
    try:
        await close_open_dropdown(page, "rule")
        emitted.append("close_owned_dropdown")
    except Exception:
        pass
    try:
        await page.wait_for_timeout(300)
    except Exception:
        pass

    rebound = await _wait_for_rule_control_selector(
        page,
        section="Actions",
        label="Mapping Identifier Name (Version)",
        occurrence=0,
        timeout_ms=10000,
    )
    rebound_selector = str((rebound or {}).get("selector") or fresh_selector)
    rebound_loc = page.locator(rebound_selector).first
    actual_after = str(await _read_control_value(rebound_loc) or "") if await rebound_loc.count() else ""
    verified = _rule_mapping_value_matches(actual_after, variants)
    details.update({
        "selector": rebound_selector,
        "actual_after_settle": mask_sensitive_string(actual_after),
        "commit_events": emitted,
        "verified": verified,
    })
    if verified:
        await _lock_filled_value(page, rebound_selector, wanted, combo=True)
    else:
        details["reason"] = "Mapping Identifier did not remain exact after settle/rebind"
    return details


async def _select_rule_mapping_identifier_exact(
    page: Page,
    *,
    selector: str,
    value: str,
    field: str = "mapping_identifier_name_version",
    timeout_ms: int = 12000,
) -> Dict[str, Any]:
    """Select Mapping Identifier from the DDS popup owned by that combobox.

    The live Rules drawer can expose 300+ mapping options.  The expected option
    may exist far outside the visible viewport and the list can still be loading
    after Action Type is committed.  Page-global option search therefore misses
    the item or clicks background Rule inventory controls.  This routine follows
    only the listbox referenced by ``aria-controls``/``aria-owns``, waits for the
    async list, scrolls the exact option into view, clicks it, and verifies the
    freshly rebound input value.
    """
    wanted = re.sub(r"\s+", " ", str(value or "")).strip()
    attempt: Dict[str, Any] = {
        "field": field,
        "section": "Actions",
        "label": "Mapping Identifier Name (Version)",
        "selector": selector,
        "value_redacted": mask_sensitive_string(wanted),
        "filled": False,
        "executor": "rule.actions.strict_owned_async_dds_popup",
        "popup_attempts": [],
    }
    if not selector or not wanted:
        attempt["reason"] = "missing Mapping Identifier selector or value"
        return mask_sensitive_data(attempt)

    variants = [re.sub(r"\s+", " ", x).strip() for x in [
        wanted,
        re.sub(r"\s*\(\s*[0-9.]+\s*\)\s*$", "", wanted),
    ] if re.sub(r"\s+", " ", x).strip()]
    variants = list(dict.fromkeys(x.lower() for x in variants))
    if semantic_runtime_enabled(page):
        semantic_entry: Dict[str, Any] = {"attempt": 1, "executor": "semantic_dds_driver"}
        try:
            root = await get_active_form_root(page, "rule")
            selected = await select_dds_combobox(page, root, selector, wanted, phase="rule")
            semantic_entry["selected"] = bool(selected)
            if selected:
                finalized = await _finalize_rule_mapping_identifier_selection(
                    page, selector=selector, wanted=wanted, variants=variants
                )
                semantic_entry["finalize"] = mask_sensitive_data(finalized)
                if finalized.get("verified"):
                    attempt["filled"] = True
                    attempt["executor"] = "rule.actions.semantic_dds_driver"
                    attempt["selected_option"] = mask_sensitive_string(wanted)
                    attempt["popup_attempts"].append(semantic_entry)
                    return mask_sensitive_data(attempt)
            semantic_entry.setdefault("reason", "semantic DDS mapping selection/effect verification failed")
        except Exception as exc:
            semantic_entry["reason"] = "semantic DDS mapping selection failed closed"
            semantic_entry["error"] = mask_sensitive_string(str(exc))
        attempt["popup_attempts"].append(semantic_entry)
        attempt["reason"] = semantic_entry.get("reason") or "semantic DDS mapping selection failed closed"
        return mask_sensitive_data(attempt)
    probe_js = r"""
({selector,wanted,variants}) => {
  function clean(v){ return String(v||'').replace(/\s+/g,' ').trim(); }
  function low(v){ return clean(v).toLowerCase(); }
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(), st=getComputedStyle(el);
    return r.width>0 && r.height>0 && st.display!=='none' && st.visibility!=='hidden' && st.opacity!=='0';
  }
  const input=document.querySelector(selector);
  if(!input) return {control_found:false};
  const host=input.closest('dds-dropdown,app-generic-dropdown');
  const wrappers=[input,input.parentElement,input.closest('[aria-controls],[aria-owns]'),host].filter(Boolean);
  const ids=[];
  for(const node of wrappers){
    for(const attr of ['aria-controls','aria-owns']){
      for(const id of String(node.getAttribute&&node.getAttribute(attr)||'').split(/\s+/).filter(Boolean)){
        if(!ids.includes(id)) ids.push(id);
      }
    }
  }
  let lists=ids.map(id=>document.getElementById(id)).filter(Boolean);
  if(host){
    for(const el of Array.from(host.querySelectorAll('[role=listbox],.dds__dropdown__list'))){
      if(!lists.includes(el)) lists.push(el);
    }
  }
  lists.sort((a,b)=>(visible(b)?1:0)-(visible(a)?1:0));
  const list=lists[0]||null;
  const optionEls=list?Array.from(list.querySelectorAll('[role=option],button.dds__dropdown__item-option,.dds__dropdown__item-option')):[];
  const options=optionEls.map((el,index)=>{
    let token=el.getAttribute('data-hip-rule-map-option')||'';
    if(!token){ token=`hip-rule-map-${Date.now()}-${index}-${Math.random().toString(36).slice(2,8)}`; el.setAttribute('data-hip-rule-map-option',token); }
    return {
      text:clean(el.innerText||el.textContent),
      disabled:el.getAttribute('aria-disabled')==='true'||!!el.disabled,
      selected:el.getAttribute('aria-selected')==='true'||el.getAttribute('data-selected')==='true',
      id:el.id||'',
      pos:el.getAttribute('aria-posinset')||'',
      token
    };
  });
  const target=low(wanted);
  const vars=(variants||[]).map(low).filter(Boolean);
  let exact=options.find(o=>!o.disabled && low(o.text)===target);
  if(!exact){
    const folded=options.filter(o=>!o.disabled && vars.some(v=>low(o.text)===v || low(o.text).startsWith(v+'(')));
    if(folded.length===1) exact=folded[0];
  }
  const loadingRoots=[host,list].filter(Boolean);
  const loading=loadingRoots.some(root=>Array.from(root.querySelectorAll('[aria-busy=true],.dds__loading,.dds__spinner,[class*=loading],[class*=spinner]')).some(visible))
    || input.getAttribute('aria-busy')==='true'
    || (host&&host.getAttribute('aria-busy')==='true');
  const globalBlockingLoading=Array.from(document.querySelectorAll(
    '.dds__loading-indicator__container,.dds__loading-indicator__overlay,[role="progressbar"]'
  )).some(visible);
  const noOptions=!!(list && /no options found/i.test(clean(list.innerText||list.textContent)));
  let optionSelector='';
  if(exact){
    if(exact.id) optionSelector=`#${CSS.escape(exact.id)}`;
    else if(list&&list.id&&exact.pos) optionSelector=`#${CSS.escape(list.id)} [role="option"][aria-posinset="${CSS.escape(exact.pos)}"]`;
    else optionSelector=`[data-hip-rule-map-option="${CSS.escape(exact.token)}"]`;
  }
  return {
    control_found:true,
    control_value:String(input.value||''),
    expanded:input.getAttribute('aria-expanded')==='true',
    owned_ids:ids,
    list_found:!!list,
    list_id:(list&&list.id)||'',
    list_visible:visible(list),
    loading,
    global_blocking_loading:globalBlockingLoading,
    disabled:!!input.disabled || input.getAttribute('aria-disabled')==='true',
    no_options:noOptions,
    option_count:options.length,
    exact_option:exact||null,
    option_selector:optionSelector
  };
}
"""
    async def accept_selected_probe(entry: Dict[str, Any], probe: Dict[str, Any], *, evidence: str) -> bool:
        exact = (probe.get("exact_option") or {}) if isinstance(probe, dict) else {}
        selected = bool(exact.get("selected"))
        control_exact = _rule_mapping_value_matches(str(probe.get("control_value") or ""), variants)
        entry[f"{evidence}_selected"] = selected
        entry[f"{evidence}_control_exact"] = control_exact
        if not (selected and control_exact):
            return False
        finalized = await _finalize_rule_mapping_identifier_selection(
            page, selector=selector, wanted=wanted, variants=variants
        )
        entry[f"{evidence}_finalize"] = mask_sensitive_data(finalized)
        entry["selected_option"] = mask_sensitive_string(str(exact.get("text") or wanted))
        if not finalized.get("verified"):
            return False
        attempt["filled"] = True
        attempt["selector"] = str(finalized.get("selector") or selector)
        attempt["selected_option"] = entry["selected_option"]
        attempt["commit_recovered_from"] = evidence
        attempt["dom_events"] = [
            "click", "input", "owned-listbox-selection/state",
            "change", "blur", "fresh-control/rebind",
        ]
        attempt["popup_attempts"].append(entry)
        return True

    start = asyncio.get_running_loop().time()
    for open_no in range(1, 4):
        entry: Dict[str, Any] = {"attempt": open_no}
        try:
            loc = page.locator(selector).first
            if not await loc.count():
                rebound = await _wait_for_rule_control_selector(
                    page,
                    section="Actions",
                    label="Mapping Identifier Name (Version)",
                    occurrence=0,
                    timeout_ms=15000,
                )
                selector = str((rebound or {}).get("selector") or selector)
                entry["rebound_selector"] = selector
                loc = page.locator(selector).first
                if not await loc.count():
                    entry["reason"] = "mapping combobox no longer exists"
                    attempt["popup_attempts"].append(entry)
                    break
            await loc.scroll_into_view_if_needed(timeout=2500)
            await loc.click(timeout=3500)
            # Search the owned DDS control directly. This avoids scrolling all
            # 300+ options and triggers the portal's async server/client filter.
            try:
                await loc.fill(wanted, timeout=2500)
                entry["search_text_sent"] = True
            except Exception as exc:
                entry["search_fill_error"] = mask_sensitive_string(str(exc))
            deadline = asyncio.get_running_loop().time() + max(1.0, timeout_ms / 1000.0)
            last_probe: Dict[str, Any] = {}
            while asyncio.get_running_loop().time() < deadline:
                try:
                    probe = await page.evaluate(
                        probe_js,
                        {"selector": selector, "wanted": wanted, "variants": variants},
                    )
                    last_probe = probe if isinstance(probe, dict) else {}
                except Exception as exc:
                    last_probe = {"error": mask_sensitive_string(str(exc))}
                option_selector = str(last_probe.get("option_selector") or "")
                if await accept_selected_probe(entry, last_probe, evidence="pre_click_selected_state"):
                    return mask_sensitive_data(attempt)

                if option_selector and not last_probe.get("loading") and not last_probe.get("global_blocking_loading") and not last_probe.get("disabled"):
                    option = page.locator(option_selector).first
                    entry["selected_option"] = mask_sensitive_string(
                        str((last_probe.get("exact_option") or {}).get("text") or "")
                    )
                    click_error = ""
                    try:
                        await option.scroll_into_view_if_needed(timeout=3000)
                        await option.click(timeout=4000)
                        entry["option_click_sent"] = True
                    except Exception as exc:
                        # DDS can commit the selection but never return from its
                        # click handler.  A timeout is therefore evidence to
                        # re-probe, not automatic evidence of failure.
                        click_error = mask_sensitive_string(str(exc))
                        entry["option_click_error"] = click_error

                    try:
                        await page.wait_for_timeout(250)
                    except Exception:
                        pass
                    try:
                        post_click = await page.evaluate(
                            probe_js,
                            {"selector": selector, "wanted": wanted, "variants": variants},
                        )
                        post_click = post_click if isinstance(post_click, dict) else {}
                    except Exception as exc:
                        post_click = {"error": mask_sensitive_string(str(exc))}
                    entry["post_click_probe"] = mask_sensitive_data(post_click)
                    if await accept_selected_probe(entry, post_click, evidence="post_click_state"):
                        return mask_sensitive_data(attempt)
                    if not click_error:
                        # A completed Playwright click is sufficient activation
                        # evidence even when a lightweight test/fallback page
                        # cannot expose aria-selected in the probe. Verify only
                        # through the freshly rebound committed control value.
                        finalized = await _finalize_rule_mapping_identifier_selection(
                            page, selector=selector, wanted=wanted, variants=variants
                        )
                        entry["completed_click_finalize"] = mask_sensitive_data(finalized)
                        if finalized.get("verified"):
                            attempt["filled"] = True
                            attempt["selector"] = str(finalized.get("selector") or selector)
                            attempt["selected_option"] = entry.get("selected_option") or mask_sensitive_string(wanted)
                            attempt["commit_recovered_from"] = "completed_owned_option_click"
                            attempt["dom_events"] = [
                                "click", "input", "owned-listbox-option/click",
                                "change", "blur", "fresh-control/rebind",
                            ]
                            attempt["popup_attempts"].append(entry)
                            return mask_sensitive_data(attempt)

                    # If Playwright's actionability click timed out before DDS
                    # exposed selected state, dispatch one bounded DOM click on
                    # the already-owned exact option.  This cannot escape into
                    # the background Rule inventory because option_selector was
                    # derived from aria-controls/aria-owns.
                    if click_error:
                        try:
                            await option.dispatch_event("click")
                            entry["owned_option_dispatch_click_sent"] = True
                            await page.wait_for_timeout(250)
                        except Exception as exc:
                            entry["owned_option_dispatch_click_error"] = mask_sensitive_string(str(exc))
                        try:
                            dispatched = await page.evaluate(
                                probe_js,
                                {"selector": selector, "wanted": wanted, "variants": variants},
                            )
                            dispatched = dispatched if isinstance(dispatched, dict) else {}
                        except Exception as exc:
                            dispatched = {"error": mask_sensitive_string(str(exc))}
                        entry["post_dispatch_probe"] = mask_sensitive_data(dispatched)
                        if await accept_selected_probe(entry, dispatched, evidence="post_dispatch_state"):
                            return mask_sensitive_data(attempt)

                    # Exact filtering left one owned option.  Enter is the DDS
                    # keyboard contract and is safer than a page-global click.
                    try:
                        exact = last_probe.get("exact_option") or {}
                        if int(last_probe.get("option_count") or 0) == 1 and exact and not exact.get("disabled"):
                            press = getattr(loc, "press", None)
                            if callable(press):
                                await press("Enter", timeout=2500)
                            else:
                                keyboard = getattr(page, "keyboard", None)
                                if keyboard is not None:
                                    await keyboard.press("Enter")
                            entry["owned_single_option_enter_sent"] = True
                            await page.wait_for_timeout(250)
                    except Exception as exc:
                        entry["owned_single_option_enter_error"] = mask_sensitive_string(str(exc))
                    try:
                        after_enter = await page.evaluate(
                            probe_js,
                            {"selector": selector, "wanted": wanted, "variants": variants},
                        )
                        after_enter = after_enter if isinstance(after_enter, dict) else {}
                    except Exception as exc:
                        after_enter = {"error": mask_sensitive_string(str(exc))}
                    entry["post_enter_probe"] = mask_sensitive_data(after_enter)
                    if await accept_selected_probe(entry, after_enter, evidence="post_enter_state"):
                        return mask_sensitive_data(attempt)
                    # One owned click/dispatch/keyboard ladder per open cycle.
                    break
                if last_probe.get("no_options") and not last_probe.get("loading"):
                    # DDS sometimes reports no options for one render tick while
                    # the async map list is still being filtered. Give it a small
                    # grace period rather than immediately falling back globally.
                    await page.wait_for_timeout(350)
                else:
                    await page.wait_for_timeout(250)
            entry["last_probe"] = mask_sensitive_data(last_probe)
            entry["elapsed_ms"] = int((asyncio.get_running_loop().time() - start) * 1000)
        except Exception as exc:
            entry["reason"] = mask_sensitive_string(str(exc))
        attempt["popup_attempts"].append(entry)
        try:
            await close_open_dropdown(page, "rule")
        except Exception:
            pass
        try:
            await page.wait_for_timeout(350)
        except Exception:
            pass

    attempt["reason"] = "expected Mapping Identifier was not committed from its owned asynchronous DDS listbox"
    return mask_sensitive_data(attempt)


async def _wait_for_rule_control_selector(
    page: Page,
    *,
    section: str,
    label: str,
    occurrence: int = 0,
    timeout_ms: int = 45000,
) -> Dict[str, Any]:
    """Wait for a conditionally rendered Rule control and return a fresh selector.

    Action Type causes Angular to fetch mapping identifiers and mount the target
    DDS control only after the async response arrives.  A one-shot label scan can
    therefore miss a control that appears seconds later.  This loop also includes
    a strict direct fallback for the Actions ``formcontrolname=target`` control;
    it never searches the background Rules inventory or filter checkboxes.
    """
    deadline = asyncio.get_running_loop().time() + max(0.5, timeout_ms / 1000.0)
    last: Dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        found = await _find_rule_control_selector(
            page,
            section=section,
            label=label,
            occurrence=occurrence,
        )
        if isinstance(found, dict) and str(found.get("selector") or ""):
            found["waited_for_conditional_control"] = True
            return found
        last = found if isinstance(found, dict) else {}
        if section.strip().lower() == "actions" and label.strip().lower() == "mapping identifier name (version)":
            try:
                direct = await page.evaluate(r"""
() => {
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(), s=getComputedStyle(el);
    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
  }
  const roots=Array.from(document.querySelectorAll('app-configure-rules,app-generic-drawer,.dds__drawer,form'))
    .filter(visible)
    .filter(el=>/Create\s+Rule/i.test(String(el.innerText||el.textContent||'')));
  const root=roots[0]||document.querySelector('app-configure-rules');
  if(!root) return {reason:'Create Rule root not found'};
  const hosts=Array.from(root.querySelectorAll('dds-dropdown[formcontrolname="target"]'));
  for(const host of hosts){
    const label=String((host.querySelector('label,dds-label')||{}).innerText||host.textContent||'').replace(/\s+/g,' ').trim();
    const input=host.querySelector('input[role="combobox"]');
    if(input && visible(input) && /Mapping Identifier Name \(Version\)/i.test(label)){
      if(input.id) return {selector:`input#${CSS.escape(input.id)}`, label:'Mapping Identifier Name (Version)', section:'actions', method:'actions_formcontrol_target_direct'};
    }
  }
  return {reason:'Mapping Identifier target control not mounted yet'};
}
""")
                if isinstance(direct, dict) and str(direct.get("selector") or ""):
                    direct["waited_for_conditional_control"] = True
                    return direct
                if isinstance(direct, dict):
                    last = direct
            except Exception as exc:
                last = {"reason": mask_sensitive_string(str(exc))}
        await page.wait_for_timeout(250)
    return {
        **last,
        "reason": str(last.get("reason") or "conditional Rule control did not appear before timeout"),
        "wait_timeout_ms": timeout_ms,
        "label": label,
        "section": section,
        "occurrence": occurrence,
    }


async def _prepare_rule_condition_add_prerequisites(
    page: Page,
    input_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Commit base Rule and default Action values before Conditions ``+``.

    Two live constraints are handled here:

    * Mapping Identifier options load asynchronously after Action Type and can be
      far outside the visible DDS viewport.
    * The exact Rule Name may already exist in DEV.  HIP then keeps the Create
      Rule form invalid and silently ignores Conditions ``+``.  For structural
      row creation only, the agent temporarily uses a unique unsaved probe name,
      then restores the exact input name after all rows exist.
    """
    seed = extract_rule_seed(input_data)
    action = _extract_rule_action_row(input_data)
    attempts: List[Dict[str, Any]] = []
    required_fields: List[str] = [
        "rule_name",
        "document_type_name_version",
        "action_name",
        "action_type",
    ]
    required_status: Dict[str, bool] = {}
    exact_rule_name = str(seed.get("rule_name") or "").strip()
    transient_name: Dict[str, Any] = {
        "used": False,
        "exact_rule_name": mask_sensitive_string(exact_rule_name),
        "temporary_rule_name": "",
        "duplicate_before": {},
        "duplicate_after": {},
    }

    if not exact_rule_name:
        attempts.append({
            "field": "rule_name",
            "section": "Rule",
            "label": "Name",
            "filled": False,
            "required": True,
            "condition_add_prerequisite": True,
            "reason": "required Rule Name missing from input.json",
        })
        required_status["rule_name"] = False
    else:
        name_attempt = await _set_rule_control_by_label(
            page,
            section="Rule",
            label="Name",
            value=exact_rule_name,
            field="rule_name",
        )
        name_attempt["condition_add_prerequisite"] = True
        name_attempt["required"] = True
        name_attempt["structural_exact_name_attempt"] = True
        attempts.append(name_attempt)
        duplicate_before = await _wait_for_rule_name_duplicate_state(page, timeout_ms=7000)
        transient_name["duplicate_before"] = duplicate_before
        required_status["rule_name"] = bool(name_attempt.get("filled"))
        if duplicate_before.get("duplicate"):
            temporary = _temporary_rule_name_for_structure(exact_rule_name)
            temp_attempt = await _set_rule_control_by_label(
                page,
                section="Rule",
                label="Name",
                value=temporary,
                field="rule_name_structure_probe",
            )
            temp_attempt.update({
                "condition_add_prerequisite": True,
                "required": True,
                "transient_structure_only": True,
                "will_restore_exact_name": True,
            })
            attempts.append(temp_attempt)
            duplicate_after = await _wait_for_rule_name_duplicate_state(page, timeout_ms=5000)
            transient_name.update({
                "used": True,
                "temporary_rule_name": mask_sensitive_string(temporary),
                "duplicate_after": duplicate_after,
                "temporary_fill_attempt": temp_attempt,
            })
            current_temp = re.sub(r"\s+", " ", str(duplicate_after.get("value") or "")).strip().lower()
            expected_temp = re.sub(r"\s+", " ", temporary).strip().lower()
            required_status["rule_name"] = bool(
                temp_attempt.get("filled")
                and current_temp == expected_temp
                and not duplicate_after.get("duplicate")
            )

    specs = [
        (
            "Rule",
            "Document Type Name (Version)",
            str(seed.get("document_type_name_version") or ""),
            "document_type_name_version",
            True,
        ),
        ("Rule", "Description", str(seed.get("description") or ""), "description", False),
        ("Actions", "Name", str(action.get("action_name") or ""), "action_name", True),
        ("Actions", "Type", str(action.get("action_type") or ""), "action_type", True),
    ]
    for section, label, value, field, required in specs:
        if not value:
            if required:
                attempts.append({
                    "field": field,
                    "section": section,
                    "label": label,
                    "filled": False,
                    "required": True,
                    "condition_add_prerequisite": True,
                    "reason": "required Rule prerequisite missing from input.json",
                })
                required_status[field] = False
            continue
        attempt = await _set_rule_control_by_label(
            page,
            section=section,
            label=label,
            value=value,
            field=field,
        )
        attempt["condition_add_prerequisite"] = True
        attempt["required"] = required
        attempts.append(attempt)
        if required:
            required_status[field] = bool(attempt.get("filled"))

    mapping_value = str(action.get("mapping_identifier_name_version") or "").strip()
    if mapping_value:
        required_fields.append("mapping_identifier_name_version")
        # Action Type reveals and asynchronously populates this DDS control.
        # First use the ordinary label driver for already-mounted controls.  If
        # Angular has not mounted/populated the conditional target yet, enter the
        # long owned-control wait and strict async DDS selector.
        mapping_attempt = await _set_rule_control_by_label(
            page,
            section="Actions",
            label="Mapping Identifier Name (Version)",
            value=mapping_value,
            field="mapping_identifier_name_version",
        )
        if not mapping_attempt.get("filled"):
            mapping_control = await _wait_for_rule_control_selector(
                page,
                section="Actions",
                label="Mapping Identifier Name (Version)",
                occurrence=0,
                timeout_ms=60000,
            )
            mapping_selector = str((mapping_control or {}).get("selector") or "")
            if mapping_selector:
                mapping_attempt = await _select_rule_mapping_identifier_exact(
                    page,
                    selector=mapping_selector,
                    value=mapping_value,
                    field="mapping_identifier_name_version",
                    timeout_ms=60000,
                )
                mapping_attempt["conditional_control_wait"] = mask_sensitive_data(mapping_control)
            else:
                mapping_attempt = {
                    "field": "mapping_identifier_name_version",
                    "section": "Actions",
                    "label": "Mapping Identifier Name (Version)",
                    "selector": "",
                    "value_redacted": mask_sensitive_string(mapping_value),
                    "filled": False,
                    "reason": str((mapping_control or {}).get("reason") or "Mapping Identifier control did not appear"),
                    "conditional_control_wait": mask_sensitive_data(mapping_control),
                }
        mapping_attempt["condition_add_prerequisite"] = True
        mapping_attempt["required"] = True
        attempts.append(mapping_attempt)
        required_status["mapping_identifier_name_version"] = bool(mapping_attempt.get("filled"))

    missing_or_failed = [
        field for field in required_fields
        if not bool(required_status.get(field))
    ]
    validity = await _inspect_rule_create_form_validity(page)
    return mask_sensitive_data({
        "attempts": attempts,
        "required_fields": required_fields,
        "required_status": required_status,
        "missing_or_failed": missing_or_failed,
        "transient_rule_name": transient_name,
        "form_validity_after_prerequisites": validity,
        "pass": not missing_or_failed,
        "safety": (
            "No Save/Create/Submit action. A unique temporary Rule Name is used "
            "only when the exact existing name blocks structural Conditions +Add, "
            "and the exact input name must be restored before final evidence."
        ),
    })


async def _apply_rule_condition_row_adds(page: Page, input_data: Dict[str, Any], *, warnings: List[str]) -> Dict[str, Any]:
    """Create and fill all Rule condition rows from ``input.json``.

    The portal refuses ``Conditions +`` while the current row or the surrounding
    Rule form is invalid. The safe transaction is therefore incremental:

    1. Commit Rule Name, Document Type and the required default Action row.
    2. Commit Execute Action(s) When.
    3. Fill every already-visible condition row.
    4. Click the small Conditions ``+`` only after the form and last row are valid.
    5. Require an exact row-count transition of ``N -> N + 1``.
    6. Fill the new row and repeat until the input row count is reached.
    """
    rows = _extract_rule_condition_rows(input_data)
    cond = extract_rule_seed(input_data)
    execute_when = str(cond.get("condition_operation") or "one or more conditions are satisfied")
    initial_count = await _count_rule_condition_rows(page)
    audit: Dict[str, Any] = {
        "phase": "rule",
        "section": "Rule Conditions",
        "input_path": "objects.rule.conditions.rows",
        "row_count_from_input": len(rows),
        "initial_live_row_count": initial_count,
        "add_clicks_needed": max(0, len(rows) - initial_count),
        "row_values": mask_sensitive_data(rows),
        "execute_actions_when": mask_sensitive_string(execute_when),
        "fill_attempts": [],
        "clicks": [],
        "summary": {
            "planned_add_clicks": max(0, len(rows) - initial_count),
            "clicked": 0,
            "failed": 0,
            "filled_rows": 0,
            "exact_row_count_pass": False,
            "exact_input_pass": False,
        },
        "safety": "Fills the current valid Condition row, then clicks only the small row-level +Add next to Conditions and requires an exact +1 row-count effect.",
    }
    if not rows:
        audit["final_live_row_count"] = initial_count
        audit["summary"]["exact_row_count_pass"] = True
        audit["summary"]["exact_input_pass"] = True
        return mask_sensitive_data(audit)

    # Attribute options depend on Document Type. In addition, the live portal's
    # Create Condition handler is a no-op while required Rule/Action values are
    # blank. Commit the exact base Rule and default Action row first.
    prerequisite_result = await _prepare_rule_condition_add_prerequisites(page, input_data)
    audit["prerequisite_attempts"] = prerequisite_result.get("attempts") or []
    audit["prerequisite_result"] = prerequisite_result
    audit["transient_rule_name"] = prerequisite_result.get("transient_rule_name") or {}
    audit["fill_attempts"].extend(audit["prerequisite_attempts"])
    if not prerequisite_result.get("pass"):
        audit["summary"]["failed"] = 1
        audit["reason"] = (
            "Rule base/default Action prerequisites were not committed; "
            "Create Condition would be an ineffective no-op"
        )
        return mask_sensitive_data(audit)
    try:
        await page.wait_for_timeout(500)
    except Exception:
        pass

    execute_attempt = await _set_rule_control_by_label(
        page,
        section="Conditions",
        label="Execute Action(s) When",
        value=execute_when,
        field="condition_operation",
    )
    audit["fill_attempts"].append(execute_attempt)
    if not execute_attempt.get("filled"):
        warnings.append("Rule Execute Action(s) When could not be committed before Conditions +; row creation may be blocked.")

    live_count = await _count_rule_condition_rows(page)
    # A healthy Create Rule form normally starts with one row.  Fill every row
    # that already exists, but never invent extra values beyond input.json.
    for idx in range(min(live_count, len(rows))):
        row_attempts = await _fill_rule_condition_row(page, rows[idx], row_index=idx)
        audit["fill_attempts"].extend(row_attempts)
        if row_attempts and all(bool(a.get("filled")) for a in row_attempts):
            audit["summary"]["filled_rows"] += 1
        else:
            warnings.append(f"Rule Conditions row {idx + 1} could not be completed before Conditions +.")

    while live_count < len(rows):
        # Re-commit the last visible row immediately before Add.  This handles a
        # DDS rerender that may have replaced one of its conditional controls.
        if live_count > 0:
            row_attempts = await _fill_rule_condition_row(page, rows[live_count - 1], row_index=live_count - 1)
            audit["fill_attempts"].extend(row_attempts)
            if not row_attempts or not all(bool(a.get("filled")) for a in row_attempts):
                audit["summary"]["failed"] += 1
                audit["clicks"].append({
                    "click_index": len(audit["clicks"]) + 1,
                    "clicked": False,
                    "reason": f"current Condition row {live_count} is not exact; refusing Conditions +",
                    "row_count_before": live_count,
                    "row_count_after": live_count,
                    "exact_plus_one": False,
                })
                break
        entry = await _click_rule_condition_add_exact(
            page,
            expected_rows_prefix=rows[:live_count],
            max_click_attempts=2,
        )
        entry["click_index"] = len(audit["clicks"]) + 1
        audit["clicks"].append(entry)
        if not entry.get("exact_plus_one"):
            audit["summary"]["failed"] += 1
            break
        audit["summary"]["clicked"] += 1
        live_count = int(entry.get("row_count_after") or live_count)
        new_index = live_count - 1
        row_attempts = await _fill_rule_condition_row(page, rows[new_index], row_index=new_index)
        audit["fill_attempts"].extend(row_attempts)
        if row_attempts and all(bool(a.get("filled")) for a in row_attempts):
            audit["summary"]["filled_rows"] += 1

    # Restore the exact input Rule Name after all structural row additions.
    # The exact name may legitimately already exist in DEV; restoring it here
    # preserves the requested no-save form evidence without letting the duplicate
    # validation disable the earlier Conditions + transaction.
    name_restore_required = bool((audit.get("transient_rule_name") or {}).get("used"))
    name_restore_pass = True
    if name_restore_required:
        exact_name = str(cond.get("rule_name") or "").strip()
        restore_attempt = await _set_rule_control_by_label(
            page,
            section="Rule",
            label="Name",
            value=exact_name,
            field="rule_name",
        )
        restore_attempt.update({
            "transient_structure_restore": True,
            "expected_existing_name_validation": True,
        })
        audit["rule_name_restore_attempt"] = restore_attempt
        audit["fill_attempts"].append(restore_attempt)
        try:
            await page.wait_for_timeout(400)
        except Exception:
            pass
        restore_state = await _rule_name_duplicate_state(page)
        audit["rule_name_restore_state"] = restore_state
        actual_name = re.sub(r"\s+", " ", str(restore_state.get("value") or "")).strip().lower()
        expected_name = re.sub(r"\s+", " ", exact_name).strip().lower()
        name_restore_pass = bool(restore_attempt.get("filled") and actual_name == expected_name)
        audit["summary"]["exact_rule_name_restored"] = name_restore_pass
        if not name_restore_pass:
            audit["summary"]["failed"] += 1
            warnings.append("Temporary structural Rule Name could not be restored to the exact input name.")

    final_rows = await _inspect_rule_condition_rows(page)
    final_count = len(final_rows)
    exact_live_proof = _rule_condition_rows_exact(final_rows, rows)
    audit["final_live_row_count"] = final_count
    audit["final_live_rows"] = final_rows
    audit["exact_live_value_proof"] = exact_live_proof
    audit["summary"]["exact_row_count_pass"] = final_count == len(rows)
    audit["summary"]["distinct_row_identity_pass"] = bool(exact_live_proof.get("distinct_row_identity_pass"))
    audit["summary"]["exact_live_values_pass"] = bool(exact_live_proof.get("pass"))
    audit["summary"]["exact_input_pass"] = bool(
        audit["summary"]["exact_row_count_pass"]
        and bool(execute_attempt.get("filled"))
        and int(audit["summary"].get("filled_rows") or 0) >= len(rows)
        and int(audit["summary"].get("failed") or 0) == 0
        and bool(exact_live_proof.get("pass"))
        and bool(name_restore_pass)
    )
    if not audit["summary"]["exact_input_pass"]:
        warnings.append(
            f"Rule Conditions exact input transaction failed: input_rows={len(rows)} live_rows={final_count} filled_rows={audit['summary'].get('filled_rows', 0)}."
        )
    if final_count != len(rows):
        warnings.append(
            f"Rule Conditions row count mismatch after incremental +Add: input={len(rows)} live={final_count}; see repeatable_row_audit."
        )
    return mask_sensitive_data(audit)


async def _find_rule_control_selector(page: Page, *, section: str, label: str, occurrence: int = 0) -> Dict[str, Any]:
    """Return the nth visible control matching a label inside a Rule/Conditions/Actions section.

    Repeated Conditions controls are resolved from a physical Angular FormArray
    row first.  They must never fall back to the first matching global label,
    because that silently overwrites an earlier condition.
    """
    row_keys = {
        "condition type": "condition_type",
        "operator": "operator",
        "value": "value",
        "attribute name/unit": "attribute_name_unit",
        "attribute name": "attribute_name_unit",
    }
    normalized_label = re.sub(r"\s+", " ", str(label or "")).strip().lower()
    if str(section or "").strip().lower().startswith("condition") and normalized_label in row_keys:
        rows = await _inspect_rule_condition_rows(page)
        if occurrence < 0 or occurrence >= len(rows):
            return {
                "reason": "requested physical Conditions row does not exist",
                "label": label,
                "section": section,
                "occurrence": occurrence,
                "physical_row_count": len(rows),
                "rows": mask_sensitive_data(rows),
            }
        row = rows[occurrence]
        controls = row.get("controls") if isinstance(row.get("controls"), dict) else {}
        control = controls.get(row_keys[normalized_label]) if isinstance(controls, dict) else None
        if not isinstance(control, dict) or not str(control.get("selector") or ""):
            return {
                "reason": "control not found in requested physical Conditions row",
                "label": label,
                "section": section,
                "occurrence": occurrence,
                "row_identity": row.get("row_identity"),
                "available_controls": sorted(controls.keys()) if isinstance(controls, dict) else [],
            }
        return {
            **control,
            "section": "conditions",
            "occurrence": occurrence,
            "row_index": occurrence,
            "row_identity": row.get("row_identity"),
            "row_selector": row.get("row_selector"),
            "method": "conditions_formarray_row_exact",
        }
    js = r"""
({section, label, occurrence}) => {
  function norm(s){ return String(s||'').replace(/\s+/g,' ').trim(); }
  function low(s){ return norm(s).toLowerCase(); }
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const st=getComputedStyle(el); return r.width>0 && r.height>0 && st.display!=='none' && st.visibility!=='hidden'; }
  function path(el){
    if(!el) return '';
    if(el.id) return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;
    const parts=[]; let n=el;
    for(let d=0;n && d<8 && n.nodeType===1;d++,n=n.parentElement){
      let part=n.tagName.toLowerCase(); const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-/.test(c)).slice(0,3);
      if(cls.length) part+='.'+cls.map(c=>CSS.escape(c)).join('.');
      const p=n.parentElement; if(p){ const same=Array.from(p.children).filter(x=>x.tagName===n.tagName); if(same.length>1) part+=`:nth-of-type(${same.indexOf(n)+1})`; }
      parts.unshift(part);
    }
    return parts.join(' > ');
  }
  function labelFor(el){
    const id=el.id; let lab='';
    if(id){ const l=document.querySelector(`label[for="${CSS.escape(id)}"]`); if(l) lab=norm(l.innerText||l.textContent); }
    if(!lab){ const wrap=el.closest('label'); if(wrap) lab=norm(wrap.innerText||wrap.textContent); }
    if(!lab){ const group=el.closest('.dds__form__field,.dds__form-field,.dds__input,.dds__textarea,.dds__select,.dds__dropdown,.form-group,.field,div'); if(group){ const l=group.querySelector('label,.dds__label,.dds__form__label,.label'); if(l) lab=norm(l.innerText||l.textContent); } }
    return lab || norm(el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||'');
  }
  function sectionFor(el){
    const fs=el.closest('fieldset');
    if(fs){ const lg=fs.querySelector('legend'); if(lg) return low(lg.innerText||lg.textContent); }
    const ancestors=[]; let n=el.parentElement;
    for(let d=0;n && d<8;d++,n=n.parentElement) ancestors.push(n);
    for(const a of ancestors){
      const t=low(a.innerText||a.textContent);
      if(t.includes('conditions :') && !t.includes('actions :')) return 'conditions';
      if(t.includes('actions :')) return 'actions';
      if(t.includes('rule :')) return 'rule';
    }
    return '';
  }
  const wanted=low(label); const wantedSection=low(section);
  const root=Array.from(document.querySelectorAll('app-generic-drawer,.dds__drawer,form,main,body')).find(r => visible(r) && /Create Rule/i.test(norm(r.innerText||r.textContent))) || document.body;
  let els=Array.from(root.querySelectorAll('input:not([type=hidden]),textarea,select,[role=combobox],[contenteditable=true]')).filter(visible);
  let matches=[];
  for(const el of els){
    const lab=low(labelFor(el)); const sec=sectionFor(el); const r=el.getBoundingClientRect();
    const wantedParts = wanted.split(/[\/|]/).map(x=>x.trim()).filter(Boolean);
    const tiny = /^(name|type|value|version)$/i.test(lab);
    const labelMatch = lab === wanted || lab.includes(wanted) || wantedParts.some(p => lab === p || lab.includes(p) || (p.length >= 8 && p.includes(lab) && !tiny));
    const sectionText=low((el.closest('fieldset')||el.closest('app-generic-drawer')||root).innerText||'');
    const sectionMatch = !wantedSection || sec.includes(wantedSection) || wantedSection.includes(sec) || (wantedSection==='conditions' && sectionText.includes('conditions')) || (wantedSection==='actions' && sectionText.includes('actions')) || (wantedSection==='rule' && sectionText.includes('rule'));
    if(labelMatch && sectionMatch) matches.push({selector:path(el), label:labelFor(el), section:sec, value:el.value||el.textContent||'', x:r.x, y:r.y});
  }
  matches.sort((a,b)=>(a.y-b.y)||(a.x-b.x));
  return matches[occurrence] || {reason:'not found', label, section, occurrence, matches};
}
"""
    try:
        out = await page.evaluate(js, {"section": section, "label": label, "occurrence": occurrence})
        return out if isinstance(out, dict) else {}
    except Exception as exc:
        return {"reason": mask_sensitive_string(str(exc)), "label": label, "section": section, "occurrence": occurrence}


async def _set_rule_control_by_label(
    page: Page,
    *,
    section: str,
    label: str,
    value: str,
    occurrence: int = 0,
    field: str = "",
) -> Dict[str, Any]:
    found = await _find_rule_control_selector(page, section=section, label=label, occurrence=occurrence)
    selector = str((found or {}).get("selector") or "")
    attempt = {
        "field": field or label,
        "section": section,
        "label": label,
        "occurrence": occurrence,
        "selector": selector,
        "value_redacted": mask_sensitive_string(str(value)),
        "filled": False,
    }
    if not selector:
        attempt["reason"] = (found or {}).get("reason") or "control not found"
        attempt["candidates"] = mask_sensitive_data((found or {}).get("matches") or [])
        return attempt
    if section.strip().lower() == "conditions" and label.strip().lower() in {"attribute name/unit", "attribute name"}:
        return await _select_rule_condition_attribute_exact(
            page, selector=selector, value=str(value), occurrence=occurrence,
            field=field or label,
        )
    if section.strip().lower() == "actions" and label.strip().lower() == "mapping identifier name (version)":
        return await _select_rule_mapping_identifier_exact(
            page,
            selector=selector,
            value=str(value),
            field=field or label,
        )
    ok = False
    control_kind: Dict[str, Any] = {}
    try:
        loc = page.locator(selector).first
        control_kind = await loc.evaluate(
            """el => ({
              tag: (el.tagName || '').toLowerCase(),
              role: el.getAttribute('role') || '',
              type: (el.getAttribute('type') || '').toLowerCase(),
              ddsDropdown: !!el.closest('dds-dropdown,app-generic-dropdown'),
              expanded: el.getAttribute('aria-expanded') || ''
            })"""
        )
    except Exception:
        control_kind = {}
    is_dropdown = bool(
        str(control_kind.get("tag") or "") == "select"
        or str(control_kind.get("role") or "") == "combobox"
        or control_kind.get("ddsDropdown")
    )
    if is_dropdown:
        try:
            root = await get_active_form_root(page, "rule")
            ok = await select_dds_combobox(page, root, selector, str(value), phase="rule")
            attempt["executor"] = "dds_control_driver.select_dds_combobox"
        except Exception as exc:
            attempt["dropdown_error"] = mask_sensitive_string(str(exc))
            ok = False
    else:
        ok = await _set_control_value(page, selector, str(value))
        attempt["executor"] = "native_text_control"
    attempt["filled"] = bool(ok)
    attempt["control_kind"] = mask_sensitive_data(control_kind)
    attempt["dom_events"] = ["click", "option/select", "input", "change", "blur"]
    await page.wait_for_timeout(250 if is_dropdown else 150)
    return mask_sensitive_data(attempt)


async def _fill_rule_exact_input_rows(
    page: Page,
    input_data: Dict[str, Any],
    dummy_values: Dict[str, str],
    *,
    include_conditions: bool = True,
) -> List[Dict[str, Any]]:
    """Fill the Rule form to match input.json and the golden Rules screenshot.

    This is intentionally label/section driven because the Rule page has repeated
    labels: `Name` exists under Rule and Actions, and each Condition row repeats
    Condition Type/Operator/Value/Attribute Name-Unit.
    """
    attempts: List[Dict[str, Any]] = []
    condition_rows = _extract_rule_condition_rows(input_data)
    action = _extract_rule_action_row(input_data)
    cond_op = dummy_values.get("condition_operation") or "one or more conditions are satisfied"

    basic = [
        ("Rule", "Name", dummy_values.get("rule_name", ""), "rule_name"),
        ("Rule", "Document Type Name (Version)", dummy_values.get("document_type_name_version", ""), "document_type_name_version"),
        ("Rule", "Description", dummy_values.get("description", ""), "description"),
    ]
    if include_conditions:
        basic.append(("Conditions", "Execute Action(s) When", cond_op, "condition_operation"))
    for section, label, value, field in basic:
        if value:
            attempts.append(await _set_rule_control_by_label(page, section=section, label=label, value=value, field=field))

    if include_conditions:
        for idx, row in enumerate(condition_rows):
            attempts.append(await _set_rule_control_by_label(page, section="Conditions", label="Condition Type", value=row.get("condition_type") or "Attributes", occurrence=idx, field=f"conditions[{idx}].condition_type"))
            attempts.append(await _set_rule_control_by_label(page, section="Conditions", label="Operator", value=row.get("operator") or "Equals", occurrence=idx, field=f"conditions[{idx}].operator"))
            attempts.append(await _set_rule_control_by_label(page, section="Conditions", label="Value", value=row.get("value") or "", occurrence=idx, field=f"conditions[{idx}].value"))
            attempts.append(await _set_rule_control_by_label(page, section="Conditions", label="Attribute Name/Unit", value=row.get("attribute_name_unit") or "", occurrence=idx, field=f"conditions[{idx}].attribute_name_unit"))

    if action.get("action_name"):
        attempts.append(await _set_rule_control_by_label(page, section="Actions", label="Name", value=action.get("action_name") or "", field="action_name"))
    if action.get("action_type"):
        attempts.append(await _set_rule_control_by_label(page, section="Actions", label="Type", value=action.get("action_type") or "Route Document", field="action_type"))
    if action.get("mapping_identifier_name_version"):
        attempts.append(await _set_rule_control_by_label(page, section="Actions", label="Mapping Identifier Name (Version)", value=action.get("mapping_identifier_name_version") or "", field="mapping_identifier_name_version"))
    return attempts


class RuleKBFlow:
    def __init__(self, config: AppConfig, *, rules_url: str = RULES_URL, fill_dummy: bool = True, known_rule_id: str | None = None, write_heavy_evidence: bool = False, crawl_old_rules: bool = True, max_api_pages: int = 250, max_detail_rows: int | None = None, capture_deep_profiles: bool = True, max_deep_profile_rows: int | None = None):
        self.config = config
        self.rules_url = rules_url
        self.fill_dummy = fill_dummy
        self.known_rule_id = known_rule_id
        self.write_heavy_evidence = write_heavy_evidence
        self.crawl_old_rules = crawl_old_rules
        self.max_api_pages = max_api_pages
        # None means enrich every discovered Rule row.  Earlier builds
        # accidentally used max_api_pages as the detail/UI row cap, which could
        # stop the Rule KB before collecting full row-level information.
        self.max_detail_rows = max_detail_rows
        # Deep profile capture is the third phase: after inventory + ID learning it
        # parses/fetches `/api/rule/{id}/details` so every Rule can
        # carry ruleIdentifier/rootElement/schema/attributes/usage evidence.
        self.capture_deep_profiles = capture_deep_profiles
        self.max_deep_profile_rows = max_deep_profile_rows

    async def run(self, ctx: RunContext, input_json: str | None = None, *, browser_session: BrowserSession | None = None) -> Dict[str, Any]:
        run_dir = ctx.run_dir
        kb_dir = run_dir / "rule_kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        input_data = _read_json(input_json)
        previous_values = build_previous_interaction_values(input_data, known_rule_id=self.known_rule_id)
        seed = extract_rule_seed(input_data)
        dummy_values = build_dummy_fill_values(seed, exact=bool(input_data.get("_replicate_exact_input_values")))
        warnings: List[str] = []
        status = "completed"
        files: Dict[str, str] = {}

        def progress(phase: str, completed: int = 0, total: int = 0, detail: str = "", counts: Optional[Dict[str, Any]] = None) -> None:
            _write_rule_progress(kb_dir, phase=phase, completed=completed, total=total, detail=detail, counts=counts)

        progress("initializing", 0, 8, "Preparing browser, SSO, Rule API capture and output folders")
        async with browser_session_scope(self.config, run_dir, existing=browser_session, phase_name=run_dir.name) as browser:
            page = await browser.start() if browser.page is None else browser.page
            browser.set_stage("rule_kb_login")
            progress("login", 1, 8, "Opening browser and waiting for Dell SSO/Rules page")
            self.config.portal.base_url = self.rules_url
            await browser.goto_base_and_complete_sso(self.rules_url)
            browser.set_stage("rule_kb_open_rules")
            progress("open_rules", 2, 8, "Opening Rules link and waiting for page/API readiness")
            # Do not blindly re-navigate when SSO already landed on Rules.
            # A second immediate navigation can abort Angular remoteEntry/chunk loading and leave a blank page.
            if not _urls_same_path(page.url, self.rules_url):
                await browser.navigate(self.rules_url)
            else:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
            ready = await _wait_for_rule_listing_ready(page, browser, self.rules_url, warnings)
            progress("rule_page_ready", 3, 8, f"Rules ready={ready}; capturing listing/API evidence")
            await browser.save_dom_snapshot("rules_listing_before_add")
            await page.wait_for_timeout(1000)
            add_api_interactions: List[Dict[str, Any]] = []
            listing_buttons = await _evaluate_buttons(page)
            listing_ui_rows = await _collect_ui_rule_rows(page)
            listing_api_interactions, old_rules = collect_rule_api_interactions(browser.network_tab_events, stage_label="rule_listing_load")
            if self.crawl_old_rules and not old_rules:
                browser.set_stage("rule_kb_direct_summary_api_fallback")
                progress("direct_summary_api_fallback", 0, max(1, self.max_api_pages), "No list rows from network yet; calling read-only Rule summary API")
                direct_rows, direct_interactions, direct_audit = await _direct_fetch_rule_summary(page, max_pages=self.max_api_pages)
                if direct_interactions:
                    listing_api_interactions.extend(direct_interactions)
                    old_rules = _merge_rule_records([*old_rules, *direct_rows])
                    # Preserve these calls in the pagination audit so failures are visible in the upload summary.
                    pagination_audit_fallback_seed = direct_audit
                else:
                    pagination_audit_fallback_seed = []
            else:
                pagination_audit_fallback_seed = []
            pagination_audit: List[Dict[str, Any]] = list(pagination_audit_fallback_seed)
            paginated_rules: List[Dict[str, Any]] = []
            detail_enrichment_audit: List[Dict[str, Any]] = []
            detail_enrichment_report: Dict[str, Any] = {}
            ui_row_action_audit: List[Dict[str, Any]] = []
            ui_row_action_report: Dict[str, Any] = {}
            deep_profile_audit: List[Dict[str, Any]] = []
            deep_profile_report: Dict[str, Any] = {}
            if self.crawl_old_rules and listing_api_interactions:
                browser.set_stage("rule_kb_old_rule_api_pagination")
                progress("old_rule_api_pagination", 0, max(1, self.max_api_pages), f"Replaying observed listing/pagination APIs; rows={len(old_rules)}")
                paginated_rules, replay_audit = await _crawl_old_rule_inventory_from_apis(page, listing_api_interactions, max_pages=self.max_api_pages)
                pagination_audit.extend(replay_audit)
                old_rules = _merge_rule_records([*old_rules, *paginated_rules])
                browser.set_stage("rule_kb_old_rule_detail_id_enrichment")
                detail_limit = min(len(old_rules), int(self.max_detail_rows)) if self.max_detail_rows is not None else len(old_rules)
                progress("rule_detail_id_enrichment", 0, max(1, detail_limit), f"Trying read-only detail lookups for numeric ruleIds; old_rules={len(old_rules)}; detail_limit={detail_limit}")
                old_rules, detail_enrichment_audit, detail_enrichment_report = await _enrich_old_rules_with_detail_apis(
                    page, old_rules, listing_api_interactions, previous_values, max_details=detail_limit, kb_dir=kb_dir, progress_cb=progress
                )
                progress("rule_detail_id_enrichment_done", detail_limit, max(1, detail_limit), f"Detail enrichment done; ruleIds={detail_enrichment_report.get('rule_ids_found', 0)}/{detail_enrichment_report.get('total_old_rules', len(old_rules))}")
                # Second phase: repeat the real UI row/action detail flow, like Partner/System discovery.
                # This is the important phase for portals where the list API hides numeric ruleId.
                browser.set_stage("rule_kb_ui_row_action_id_learning")
                max_ui_rows = detail_limit
                progress("rule_ui_row_action_id_learning", 0, max(1, max_ui_rows), "Learning old Rule IDs from real UI row actions/details")
                old_rules, ui_row_action_audit, ui_row_action_report = await _learn_old_rule_ids_from_ui_row_actions(
                    page, browser, old_rules, previous_values, max_rows=max_ui_rows, kb_dir=kb_dir, progress_cb=progress
                )
                progress("rule_ui_row_action_id_learning_done", max_ui_rows, max(1, max_ui_rows), f"UI row-action ID learning done; ruleIds={ui_row_action_report.get('rule_ids_after_ui_phase', 0)}/{len(old_rules)}")
                # Merge both API and UI completion reports into the main report used by summaries.
                with_ids_after_ui = sum(1 for r in old_rules if r.get("rule_id"))
                detail_enrichment_report.update({
                    "ui_row_action_phase": ui_row_action_report,
                    "rule_ids_found": with_ids_after_ui,
                    "rule_ids_missing": max(0, len(old_rules) - with_ids_after_ui),
                    "completion_percent": round((with_ids_after_ui / len(old_rules) * 100), 2) if old_rules else 0.0,
                })
                if self.capture_deep_profiles:
                    browser.set_stage("rule_kb_deep_profile_enrichment")
                    deep_limit = min(len(old_rules), int(self.max_deep_profile_rows)) if self.max_deep_profile_rows is not None else len(old_rules)
                    progress("rule_deep_profile_enrichment", 0, max(1, deep_limit), f"Capturing full Rule profiles from details API; old_rules={len(old_rules)}; deep_limit={deep_limit}")
                    old_rules, deep_profile_audit, deep_profile_report = await _capture_rule_deep_profiles(
                        page, browser, old_rules, [*listing_api_interactions, *pagination_audit], max_profiles=deep_limit, kb_dir=kb_dir, progress_cb=progress
                    )
                    progress("rule_deep_profile_enrichment_done", deep_limit, max(1, deep_limit), f"Deep profile capture done; profiles={deep_profile_report.get('deep_profiles_captured', 0) + deep_profile_report.get('deep_profiles_partial', 0)}/{deep_profile_report.get('total_rules', len(old_rules))}")
            else:
                old_rules = _apply_known_rule_ids(old_rules, previous_values)
            # UI fallback rows are stored as evidence even when they cannot be normalized to IDs.
            progress("find_add_button", 4, 8, "Looking for + Add button after inventory capture")
            add = await _find_add_button(page)
            add_form_opened = False
            if add is None:
                status = "partial_success"
                warnings.append("Could not locate + Add button. Saved old Rule listing/API KB only.")
            else:
                browser.set_stage("rule_kb_click_add")
                progress("click_add_button", 5, 8, "Clicking + Add safely; will not save/create")
                before_add_event_count = len(browser.network_tab_events)
                add_form_opened = await _click_add_rule_with_overlay_recovery(
                    page, browser, add, kb_dir=kb_dir, warnings=warnings
                )
                add_api_interactions, add_api_rules = collect_rule_api_interactions(browser.network_tab_events[before_add_event_count:], stage_label="rule_add_click")
                old_rules = _merge_rule_records([*old_rules, *add_api_rules])
                if add_form_opened:
                    await browser.save_dom_snapshot("rule_add_form_opened")
                else:
                    status = "partial_success"
            async def _react_click_rule(add_loc, step_no):
                return await _click_add_rule_with_overlay_recovery(
                    page, browser, add_loc, kb_dir=kb_dir, warnings=warnings
                )
            entry_audit = await ensure_phase_form_entry(
                page=page, browser=browser, phase="rule", listing_url=self.rules_url,
                find_add=_find_add_button, is_form_open=_looks_like_rule_add_form,
                click_add=_react_click_rule, evidence_dir=kb_dir, max_steps=4,
                require_same_route=True,
            )
            files["phase_form_entry_react_json"] = str(kb_dir / "phase_form_entry_react.json")
            add_form_opened = bool(entry_audit.get("pass") and await _looks_like_rule_add_form(page))
            if entry_audit.get("pass"):
                status = "completed"
            browser.set_stage("rule_kb_capture_form")
            progress("capture_add_form", 6, 8, "Capturing Add Rule form controls/dropdowns/required fields")
            repeatable_row_audit = []
            if add_form_opened and self.fill_dummy:
                # Rules needs the small +Add beside `Conditions:` because
                # input.json can contain multiple condition rows.  This is a
                # mandatory transaction, not optional planning.  A failed/no-op
                # Create Condition click must stop the phase immediately; the
                # later generic/state-graph passes are forbidden from reusing row
                # 1 for row 2.
                condition_row_audit = await _apply_rule_condition_row_adds(page, input_data, warnings=warnings)
                repeatable_row_audit = [condition_row_audit]
                if not bool((condition_row_audit.get("summary") or {}).get("exact_input_pass")):
                    raise RuntimeError(
                        "Rule Conditions mandatory +Add/fill transaction failed; refusing row reuse: "
                        + mask_sensitive_string(json.dumps({
                            "summary": condition_row_audit.get("summary") or {},
                            "reason": condition_row_audit.get("reason") or "",
                            "prerequisite_result": condition_row_audit.get("prerequisite_result") or {},
                            "clicks": condition_row_audit.get("clicks") or [],
                            "exact_live_value_proof": condition_row_audit.get("exact_live_value_proof") or {},
                        }, ensure_ascii=False, default=str))
                    )
                repeatable_row_audit.append({
                    "phase": "rule",
                    "generic_rule_repeatable_plan_only": True,
                    "sections": build_repeatable_section_plan(input_data, "rule"),
                    "summary": {"clicked": 0, "failed": 0},
                    "note": "Generic Rule repeatable planner is recorded only. Rule Conditions are handled by the mandatory Conditions +Add transaction to avoid clicking the background listing container or reusing an earlier row.",
                })
            if add_form_opened:
                controls = await _evaluate_controls(page)
                stateful_controls = await capture_stateful_controls(page, "rule")
                if stateful_controls:
                    seen_selectors = {str(c.get("selector") or "") for c in controls if isinstance(c, dict)}
                    controls.extend(dict(c) for c in stateful_controls if str(c.get("selector") or "") not in seen_selectors)
                buttons = await _evaluate_buttons(page)
                dropdowns = await _collect_dropdown_options(page, controls)
            else:
                controls = []
                buttons = listing_buttons
                dropdowns = []
            # Commit the current input target branch before exploring alternate
            # Condition Type/Action Type values. Empty-form crawling cannot see
            # their conditional children and must not define the execution schema.
            exploration_knowledge: Dict[str, Any] = {
                "status": "deferred_until_target_branch_committed",
                "learning_order": "current input target branch first",
            }
            fill_attempts: List[Dict[str, Any]] = []
            state_graph: Dict[str, Any] = compile_phase_state_graph(input_data, "rule")
            target_branch_execution: Dict[str, Any] = {}
            target_branch_knowledge: Dict[str, Any] = {}
            required_fields: List[Dict[str, Any]] = []
            attr_rows = seed.get("attributes_to_configure") if isinstance(seed.get("attributes_to_configure"), list) else []
            attr_index = -1
            rule_identifier_derived_from_used = False
            for c in controls:
                attrs = {k: c.get(k) for k in ["name", "id", "placeholder", "ariaLabel"]}
                key = guess_field_key(c.get("label") or "", attrs)
                recommended = dummy_values.get(key or "", "")
                if key == "attribute_name":
                    attr_index += 1
                    if attr_rows:
                        recommended = _first_attr_value([attr_rows[min(attr_index, len(attr_rows) - 1)]], "attribute_name", recommended)
                elif key == "derived_from":
                    if attr_index < 0 and not rule_identifier_derived_from_used:
                        key = "rule_identifier_derived_from"
                        recommended = dummy_values.get("rule_identifier_derived_from", "TRANSACTION_ROOT_ELEMENT")
                        rule_identifier_derived_from_used = True
                    else:
                        key = "attribute_derived_from"
                        if attr_rows:
                            recommended = _first_attr_value([attr_rows[min(max(attr_index, 0), len(attr_rows) - 1)]], "attribute_derived_from", dummy_values.get("attribute_derived_from", "ELEMENT_IN_PAYLOAD"))
                elif key == "attribute_usage" and attr_rows:
                    recommended = _first_attr_value([attr_rows[min(max(attr_index, 0), len(attr_rows) - 1)]], "attribute_usage", dummy_values.get("attribute_usage", ""))
                elif key == "attribute_expression" and attr_rows:
                    recommended = _first_attr_value([attr_rows[min(max(attr_index, 0), len(attr_rows) - 1)]], "attribute_expression", dummy_values.get("attribute_expression", ""))
                c["mapped_rule_key"] = key
                c["recommended_value"] = recommended
                if c.get("required"):
                    required_fields.append(c)
            if self.fill_dummy and state_graph.get("nodes"):
                # Dropdown probing can close/detach the Rule drawer in DDS/Angular.
                # Before filling, verify the Create Rule form is still present; if not,
                # re-open + Add and re-evaluate foreground controls.
                try:
                    if not await _looks_like_rule_add_form(page):
                        fresh_add = await _find_add_button(page)
                        if fresh_add is not None:
                            reopened = await _click_add_rule_with_overlay_recovery(page, browser, fresh_add, kb_dir=kb_dir, warnings=warnings)
                            if reopened:
                                await browser.save_dom_snapshot("rule_add_form_reopened_before_fill")
                                controls = _filter_foreground_rule_controls(await _evaluate_controls(page))
                                reopened_stateful = await capture_stateful_controls(page, "rule")
                                if reopened_stateful:
                                    seen_selectors = {str(c.get("selector") or "") for c in controls if isinstance(c, dict)}
                                    controls.extend(dict(c) for c in reopened_stateful if str(c.get("selector") or "") not in seen_selectors)
                except Exception as exc:
                    warnings.append(f"Rule form re-open before fill skipped: {mask_sensitive_string(str(exc))}")
                browser.set_stage("rule_kb_fill_dummy_no_save")
                progress("fill_dummy_no_save", 0, len(controls), "Filling dummy values only; Save/Create/Submit blocked")
                # First perform exact, section-aware fill from input.json so repeated Rule
                # Conditions are filled row-by-row to match the golden Rules screenshot.
                # Conditions were already committed and proven above.  Do not
                # refill them here: a stale/failed row expansion must never let
                # the exact-input pass overwrite row 1 with row 2 values.
                exact_attempts = await _fill_rule_exact_input_rows(
                    page,
                    input_data,
                    dummy_values,
                    include_conditions=False,
                )
                fill_attempts.extend(exact_attempts)
                progress("fill_dummy_no_save", min(len(exact_attempts), len(controls)), len(controls), "Exact Rule input rows filled, including repeatable Conditions")
                # Then do a conservative generic pass for any remaining uniquely-mapped controls.
                # Its order comes from the persistent HIP Portal brain plan.
                controls = deterministic_sort_controls(controls, input_data, key_fields=("mapped_rule_key",), phase="rule")
                already_fields = {str(a.get("field") or "") for a in exact_attempts if a.get("filled")}
                for idx, c in enumerate(controls, start=1):
                    key = c.get("mapped_rule_key")
                    if not key or key in already_fields or key.startswith("condition_") or key in {"action_name", "action_type", "mapping_identifier_name_version", "document_type_name_version"}:
                        continue
                    value = c.get("recommended_value") or dummy_values.get(key or "")
                    if value in (None, ""):
                        continue
                    if str(c.get("type") or "").lower() == "file" or key == "schema_file":
                        fill_attempts.append(await attempt_upload_for_control(page, c, input_data, phase="rule", field_key=str(key or ""), desired_value=value))
                        continue
                    ok = await _set_control_value(page, c.get("selector", ""), str(value))
                    fill_attempts.append(annotate_plan_attempt({"field": key, "label": c.get("label"), "selector": c.get("selector"), "value_redacted": value, "filled": ok, "dom_events": ["input", "change", "blur"]}, input_data, str(key or ""), phase="rule"))
                    await page.wait_for_timeout(100)
                    if idx % 3 == 0 or idx == len(controls):
                        progress("fill_dummy_no_save", idx, len(controls), f"Dummy fill checkpoint {idx}/{len(controls)}")
                if not await _looks_like_rule_add_form(page):
                    raise RuntimeError("Create Rule form disappeared after specialized fill; refusing stale/listing evidence")

                autonomous_execution: Dict[str, Any] = {}
                if autonomous_phase_enabled(self.config, "rule"):
                    autonomous_cfg = getattr(self.config, "autonomous_form", None)
                    autonomous_execution = await execute_autonomous_phase_goal(
                        page=page, graph=state_graph, phase="rule", input_data=input_data,
                        config=self.config, output_dir=kb_dir / "autonomous_form_runtime",
                        prior_attempts=fill_attempts,
                        max_cycles=int(getattr(autonomous_cfg, "max_adaptive_cycles", 5) or 5),
                        repair=True, strict_live_execution=True,
                    )
                    target_branch_execution = autonomous_target_execution(autonomous_execution)
                    _write_json(kb_dir / "rule_autonomous_form_execution.json", autonomous_execution)
                else:
                    target_branch_execution = await execute_phase_state_graph(
                        page, state_graph, phase="rule", max_retries=2, repair=True, prior_attempts=fill_attempts, strict_live_execution=True
                    )
                _write_json(kb_dir / "rule_target_branch_execution.json", target_branch_execution)
                if not target_branch_execution.get("pass"):
                    raise RuntimeError(
                        "Rule target state graph did not commit exactly: "
                        + mask_sensitive_string(json.dumps((target_branch_execution.get("failed_attempts") or [])[:10], ensure_ascii=False, default=str))
                    )
                fill_attempts.extend(dict(a, execution_stage="state_graph_reconciliation") for a in target_branch_execution.get("attempts", []) if isinstance(a, dict))
                portal_form_dir = kb_dir.parent.parent / "portal_form_knowledge"
                portal_form_dir.mkdir(parents=True, exist_ok=True)
                target_branch_knowledge = build_target_branch_knowledge(state_graph, target_branch_execution)
                target_knowledge_file = portal_form_dir / "rule_target_branch_form_knowledge.json"
                target_branch_knowledge["knowledge_file"] = str(target_knowledge_file)
                safe_write_json(target_knowledge_file, target_branch_knowledge)
                await browser.save_dom_snapshot("rule_target_branch_before_exploration")
                try:
                    await browser.screenshot(kb_dir / "rule_target_branch_before_exploration.png", full_page=True)
                except Exception:
                    pass

                try:
                    live_controls = await _evaluate_controls(page)
                    live_buttons = await _evaluate_buttons(page)
                    live_dropdowns = await _collect_dropdown_options(page, live_controls)
                    exploration_knowledge = await run_portal_form_exploration(
                        page=page, phase="rule", section="Create Rule", input_data=input_data,
                        controls=live_controls, dropdowns=live_dropdowns, buttons=live_buttons,
                        repeatable_plan=build_repeatable_section_plan(input_data, "rule"),
                        repeatable_audit=repeatable_row_audit, output_dir=portal_form_dir,
                        config=self.config, allow_live_branching=True,
                    )
                    if exploration_knowledge.get("restore_errors") or str(exploration_knowledge.get("status") or "").startswith("failed"):
                        raise RuntimeError("Rule exploration could not restore the target parent values")
                    if autonomous_phase_enabled(self.config, "rule"):
                        autonomous_cfg = getattr(self.config, "autonomous_form", None)
                        restored_autonomous = await execute_autonomous_phase_goal(
                            page=page, graph=state_graph, phase="rule", input_data=input_data,
                            config=self.config, output_dir=kb_dir / "autonomous_form_runtime_restore",
                            prior_attempts=fill_attempts,
                            max_cycles=int(getattr(autonomous_cfg, "max_adaptive_cycles", 5) or 5),
                            repair=True, strict_live_execution=True,
                        )
                        restored_execution = autonomous_target_execution(restored_autonomous)
                        _write_json(kb_dir / "rule_autonomous_restore_execution.json", restored_autonomous)
                    else:
                        restored_execution = await execute_phase_state_graph(
                            page, state_graph, phase="rule", max_retries=2, repair=True, prior_attempts=fill_attempts, strict_live_execution=True
                        )
                    _write_json(kb_dir / "rule_post_exploration_restore_execution.json", restored_execution)
                    if not restored_execution.get("pass"):
                        raise RuntimeError("Rule target path failed after exploratory branches")
                    fill_attempts.extend(dict(a, execution_stage="post_exploration_restore") for a in restored_execution.get("attempts", []) if isinstance(a, dict))
                    target_branch_execution = restored_execution
                    target_branch_knowledge = build_target_branch_knowledge(state_graph, restored_execution)
                    target_branch_knowledge["knowledge_file"] = str(target_knowledge_file)
                    safe_write_json(target_knowledge_file, target_branch_knowledge)
                    exploration_knowledge["learning_order"] = "target branch first; alternatives second; deterministic target restore last"
                except Exception as exc:
                    raise RuntimeError("Rule state learning failed closed after target fill: " + mask_sensitive_string(str(exc)))

                if not await _looks_like_rule_add_form(page):
                    raise RuntimeError("Create Rule surface was lost before final evidence")
                await browser.save_dom_snapshot("rule_add_form_after_dummy_fill_no_save")
                try:
                    await browser.screenshot(kb_dir / "rule_add_form_after_dummy_fill_no_save.png", full_page=True)
                except Exception:
                    pass
                # Some Rule comboboxes become populated only after earlier required
                # fields receive dummy values. Capture once more and merge so the KB has
                # the fullest read-only Add-form dropdown evidence possible.
                try:
                    post_fill_controls = await _evaluate_controls(page)
                    post_fill_dropdowns = await _collect_dropdown_options(page, post_fill_controls)
                    dropdowns = _merge_dropdown_kb(dropdowns, post_fill_dropdowns)
                except Exception as exc:
                    warnings.append(f"Post-fill dropdown capture skipped: {mask_sensitive_string(str(exc))}")
            browser.set_stage("rule_kb_summarize")
            progress("summarize_and_write_outputs", 7, 8, "Writing compact KB, checkpoints and upload zip")
            network_api_interactions, all_api_rules = collect_rule_api_interactions(browser.network_tab_events, stage_label="full_rule_run")
            # Include direct fallback/pagination learned APIs in the final API KB, not only raw Network events.
            all_api_interactions = _dedupe_api_interactions([*listing_api_interactions, *add_api_interactions, *network_api_interactions])
            old_rules = _merge_rule_records(_apply_known_rule_ids([*old_rules, *all_api_rules], previous_values))
            wanted_rule_name = re.sub(r"\s+", " ", str(seed.get("rule_name") or "")).strip().lower()
            existing_matches = [
                r for r in old_rules
                if wanted_rule_name
                and re.sub(r"\s+", " ", str(r.get("rule_name") or "")).strip().lower() == wanted_rule_name
            ]
            existing_rule = existing_matches[0] if existing_matches else {}
            existing_object_resolution = mask_sensitive_data({
                "found": bool(existing_rule),
                "mode": "reuse_existing" if existing_rule else "not_found",
                "object_type": "rule",
                "rule_name": str(seed.get("rule_name") or ""),
                "rule_id": existing_rule.get("rule_id") if isinstance(existing_rule, dict) else None,
                "rule_version": existing_rule.get("rule_version") if isinstance(existing_rule, dict) else None,
                "source": "read_only_rule_inventory",
                "reason": (
                    "Exact Rule Name already exists; no-save form exploration may use a temporary unique structural name and restore the exact input name."
                    if existing_rule else
                    "No exact Rule Name match found in the read-only inventory."
                ),
            })
            with_ids = sum(1 for r in old_rules if r.get("rule_id"))
            if not detail_enrichment_report:
                detail_enrichment_report = {}
            detail_enrichment_report.update({
                "total_old_rules": len(old_rules),
                "rule_ids_found": with_ids,
                "rule_ids_missing": max(0, len(old_rules) - with_ids),
                "completion_percent": round((with_ids / len(old_rules) * 100), 2) if old_rules else 0.0,
                "final_report_note": "Counts are recalculated after final merge of listing, direct fallback, pagination, detail, UI-row-action, deep-profile, and full-run API evidence.",
                "deep_profile_phase": deep_profile_report,
            })
            rule_lookup = _build_rule_lookup(old_rules)
            network_relevant = []
            for e in browser.network_tab_events[-200:]:
                payload = e.model_dump() if hasattr(e, "model_dump") else getattr(e, "__dict__", {})
                url = str(payload.get("url") or "")
                if any(k in url.lower() for k in ["rule", "rule", "rule", "ruletype", "rule", "securelink"]):
                    payload.pop("response_body_text_redacted", None)
                    network_relevant.append(mask_sensitive_data(payload))
            kb = {
                "run_id": ctx.run_id,
                "captured_at": utc_now(),
                "url": self.rules_url,
                "safety": {
                    "save_clicked": False,
                    "create_clicked": False,
                    "submit_clicked": False,
                    "note": "The flow opens + Add and fills disposable dummy values only. It never clicks Save/Create/Submit.",
                },
                "previous_interaction_values": previous_values,
                "dummy_fill_values": dummy_values,
                "deterministic_plan_runtime": deterministic_plan_summary(input_data),
                "old_rules_inventory": old_rules,
                "existing_object_resolution": existing_object_resolution,
                "old_rule_id_lookup_by_name": rule_lookup,
                "rule_api_interactions": all_api_interactions,
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
                "autonomous_goal_runtime_enabled": autonomous_phase_enabled(self.config, "rule"),
                "autonomous_goal_runtime_policy": "live-goal-authoritative; phase-specific logic is advisory/structural acceleration only",
                "target_branch_knowledge": target_branch_knowledge,
                "repeatable_section_plan": build_repeatable_section_plan(input_data, "rule"),
                "repeatable_row_audit": repeatable_row_audit,
                "portal_form_exploration": exploration_knowledge,
                "network_events_relevant_compact": network_relevant,
                "warnings": warnings,
            }
            files["rule_form_kb_json"] = _write_json(kb_dir / "rule_form_kb.json", kb)
            files["old_rules_inventory_json"] = _write_json(kb_dir / "old_rules_inventory.json", old_rules)
            files["old_rule_id_lookup_by_name_json"] = _write_json(kb_dir / "old_rule_id_lookup_by_name.json", rule_lookup)
            files["rule_api_interactions_json"] = _write_json(kb_dir / "rule_api_interactions.json", all_api_interactions)
            files["rule_api_pagination_audit_json"] = _write_json(kb_dir / "rule_api_pagination_audit.json", pagination_audit)
            files["rule_detail_enrichment_audit_json"] = _write_json(kb_dir / "rule_detail_enrichment_audit.json", detail_enrichment_audit)
            files["rule_ui_row_action_enrichment_audit_json"] = _write_json(kb_dir / "rule_ui_row_action_enrichment_audit.json", ui_row_action_audit)
            files["rule_deep_profile_enrichment_audit_json"] = _write_json(kb_dir / "rule_deep_profile_enrichment_audit.json", deep_profile_audit)
            files["rule_deep_profile_report_json"] = _write_json(kb_dir / "rule_deep_profile_report.json", deep_profile_report)
            files["old_rules_deep_profiles_json"] = _write_json(kb_dir / "old_rules_deep_profiles.json", old_rules)
            files["rule_id_completion_report_json"] = _write_json(kb_dir / "rule_id_completion_report.json", detail_enrichment_report)
            files["old_rules_inventory_with_ids_json"] = _write_json(kb_dir / "old_rules_inventory_with_ids.json", old_rules)
            files["old_rules_inventory_with_ids_csv"] = _write_rules_inventory_csv(kb_dir / "old_rules_inventory_with_ids.csv", old_rules)
            files["listing_ui_rows_json"] = _write_json(kb_dir / "rule_listing_ui_rows.json", listing_ui_rows)
            files["old_rules_inventory_csv"] = _write_rules_inventory_csv(kb_dir / "old_rules_inventory.csv", old_rules)
            files["previous_values_json"] = _write_json(kb_dir / "rule_previous_interaction_values.json", previous_values)
            files["dropdowns_json"] = _write_json(kb_dir / "rule_dropdowns.json", dropdowns)
            files["required_fields_json"] = _write_json(kb_dir / "rule_required_fields.json", required_fields)
            files["dummy_fill_plan_json"] = _write_json(kb_dir / "rule_dummy_fill_plan.json", {"values": dummy_values, "attempts": fill_attempts})
            files["dom_events_json"] = _write_json(kb_dir / "rule_dom_events.json", _build_dom_event_kb(controls, dropdowns, buttons))
            files.update(_write_rule_api_flow_knowledge_graph_safe(kb_dir, kb))
            files["markdown"] = _write_markdown(kb_dir / "RULE_KB_SUMMARY.md", kb)
            files["csv"] = _write_controls_csv(kb_dir / "rule_form_controls.csv", controls)
            # Avoid huge evidence unless explicitly requested. Always write compact summaries before zipping.
            if self.write_heavy_evidence:
                await browser.flush_logs(force=True)
            else:
                await _write_compact_browser_summary(browser, kb_dir)
                browser._logs_flushed = True
            files["compact_action_sequence_json"] = str(kb_dir / "compact_action_sequence.json")
            files["compact_click_sequence_json"] = str(kb_dir / "compact_click_sequence.json")
            files["compact_network_summary_json"] = str(kb_dir / "compact_network_summary.json")
            files["upload_zip"] = _zip_summary(run_dir, kb_dir)
            progress("completed", 8, 8, "Rule KB summary zip created")
        counts = {
            "old_rules": len(json.loads((kb_dir / "old_rules_inventory.json").read_text(encoding="utf-8"))) if (kb_dir / "old_rules_inventory.json").exists() else 0,
            "old_rules_with_numeric_id": (json.loads((kb_dir / "rule_id_completion_report.json").read_text(encoding="utf-8")).get("rule_ids_found", 0) if (kb_dir / "rule_id_completion_report.json").exists() else 0),
            "api_interactions": len(json.loads((kb_dir / "rule_api_interactions.json").read_text(encoding="utf-8"))) if (kb_dir / "rule_api_interactions.json").exists() else 0,
            "form_controls": len(files) and len((json.loads((kb_dir / "rule_form_kb.json").read_text(encoding="utf-8"))).get("form_controls", [])),
            "required_fields": len(json.loads((kb_dir / "rule_required_fields.json").read_text(encoding="utf-8"))),
            "dropdowns": len(json.loads((kb_dir / "rule_dropdowns.json").read_text(encoding="utf-8"))),
            "deep_profiles_captured": (json.loads((kb_dir / "rule_deep_profile_report.json").read_text(encoding="utf-8")).get("deep_profiles_captured", 0) if (kb_dir / "rule_deep_profile_report.json").exists() else 0),
        }
        result = RuleKBResult(
            run_id=ctx.run_id,
            run_dir=str(run_dir),
            kb_dir=str(kb_dir),
            status=status,
            counts=counts,
            files=files,
            warnings=warnings,
            existing_object_resolution=existing_object_resolution,
        )
        (run_dir / "rule_kb_summary.json").write_text(json.dumps(result.__dict__, indent=2, ensure_ascii=False), encoding="utf-8")
        return result.__dict__



def _write_rule_progress(kb_dir: Path, *, phase: str, completed: int, total: int, detail: str = "", counts: Optional[Dict[str, Any]] = None) -> None:
    """Write a lightweight heartbeat/progress checkpoint for long Rule KB runs."""
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
        (kb_dir / "rule_progress.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        with (kb_dir / "rule_progress_events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        (kb_dir / "rule_progress_heartbeat.txt").write_text(f"{payload['timestamp']} | {phase} | {completed_i}/{total_i} | {percent}% | {payload['detail']}\n", encoding="utf-8")
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


def _write_rules_inventory_csv(path: Path, rows: List[Dict[str, Any]]) -> str:
    fields = [
        "rule_id", "rule_name", "rule_version", "status", "transaction_type", "format", "validation_type",
        "description", "rule_identifier", "rule_identifier_operation", "rule_identifier_derived_from", "root_element",
        "rule_version", "usage", "schema_file", "attribute_count", "deep_profile_status", "deep_profile_completeness_score",
        "deep_profile_source_url", "available_environments", "latest_dev_version", "created_by", "updated_by", "created_at", "updated_at",
        "source", "source_url",
    ]
    return safe_write_csv(path, fields, rows)


def _write_controls_csv(path: Path, controls: List[Dict[str, Any]]) -> str:
    fields = ["index", "mapped_rule_key", "label", "tag", "type", "role", "required", "disabled", "readonly", "name", "id", "placeholder", "selector", "recommended_value"]
    return safe_write_csv(path, fields, controls)


def _build_dom_event_kb(controls: List[Dict[str, Any]], dropdowns: List[Dict[str, Any]], buttons: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "text_inputs": [
            {"label": c.get("label"), "selector": c.get("selector"), "events": ["focus", "input", "change", "blur"], "mapped_rule_key": c.get("mapped_rule_key")}
            for c in controls if (c.get("tag") in {"input", "textarea"} and str(c.get("type") or "").lower() != "file")
        ],
        "file_inputs": [
            {"label": c.get("label"), "selector": c.get("selector"), "events": ["setInputFiles", "change"], "mapped_rule_key": c.get("mapped_rule_key")}
            for c in controls if str(c.get("type") or "").lower() == "file" or c.get("mapped_rule_key") == "schema_file"
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


def build_rule_api_flow_knowledge_graph(kb: Dict[str, Any]) -> Dict[str, Any]:
    """Build a compact Knowledge Graph for Rule API learning + Add-form KB.

    This is deliberately separate from the heavy browser/run KG. It focuses on:
    Rules page -> listing APIs -> pagination replay -> old Rule rows -> +Add -> form controls/dropdowns -> dummy fill.
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
    run_node = add_node(_kg_id("run", run_id), "RUN", run_id, captured_at=kb.get("captured_at"), mode="rule_api_learning")
    page_node = add_node(_kg_id("page", kb.get("url")), "PAGE", kb.get("url") or "Rules", url=kb.get("url"))
    add_edge(run_node, page_node, "RUN_OPENED_PAGE")

    safety_node = add_node(_kg_id("safety", run_id), "SAFETY_POLICY", "Do not save Rule", **(kb.get("safety") or {}))
    add_edge(run_node, safety_node, "RUN_ENFORCED_SAFETY")

    prev = kb.get("previous_interaction_values") or {}
    if prev:
        prev_node = add_node(_kg_id("previous_values", run_id), "PREVIOUS_INTERACTION_VALUES", "Uploaded/prior Rule values", **prev)
        add_edge(run_node, prev_node, "USED_PREVIOUS_VALUES")
        dm_vals = prev.get("rule_values_to_fill") or {}
        if dm_vals:
            rule_node = add_node(_kg_id("seed_rule", dm_vals.get("rule_name"), dm_vals.get("rule_identifier")), "RULE_SEED", dm_vals.get("rule_name") or dm_vals.get("rule_identifier") or "Rule seed", **dm_vals, known_ids=prev.get("known_ids"))
            add_edge(prev_node, rule_node, "CONTAINED_RULE_SEED")

    stage_nodes: Dict[str, str] = {}
    def stage_node(stage: str) -> str:
        if stage not in stage_nodes:
            stage_nodes[stage] = add_node(_kg_id("stage", run_id, stage), "STAGE", stage)
            add_edge(run_node, stage_nodes[stage], "RUN_STARTED_STAGE")
        return stage_nodes[stage]

    # API interactions from initial list load, pagination replay and +Add click.
    interactions = kb.get("rule_api_interactions") or []
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
            rule_rows_extracted=inter.get("rule_rows_extracted"),
        )
        add_edge(st, api_node, "STAGE_OBSERVED_API", order=idx)
        add_edge(page_node, api_node, "PAGE_TRIGGERED_API", stage=stage)
        endpoint_node = add_node(_kg_id("endpoint", inter.get("method"), inter.get("url")), "ENDPOINT", endpoint, method=inter.get("method"), url=inter.get("url"))
        add_edge(api_node, endpoint_node, "API_HIT_ENDPOINT")
        shape = inter.get("response_shape") or {}
        shape_node = add_node(_kg_id("response_shape", inter.get("url"), json.dumps(shape, sort_keys=True, default=str)), "RESPONSE_SHAPE", f"{shape.get('type','response')} rows={shape.get('row_count',0)}", **shape)
        add_edge(api_node, shape_node, "API_RETURNED_RESPONSE_SHAPE")
        for ridx, row in enumerate(inter.get("sample_rule_rows") or [], start=1):
            rec_node = add_node(_kg_id("rule_record", row.get("rule_id") or row.get("rule_name") or ridx), "RULE_RECORD", row.get("rule_name") or row.get("rule_identifier") or row.get("rule_id") or f"record {ridx}", **row)
            add_edge(api_node, rec_node, "API_RETURNED_RULE_RECORD", sample=True)

    # Old inventory records: link every normalized row but keep node props compact.
    for idx, row in enumerate(kb.get("old_rules_inventory") or [], start=1):
        compact = {k: row.get(k, "") for k in ["rule_id", "rule_name", "rule_version", "status", "rule_identifier", "root_element", "rule_version", "schema_file", "source", "source_url"]}
        rec_node = add_node(_kg_id("rule_record", compact.get("rule_id") or compact.get("rule_name") or idx), "RULE_RECORD", compact.get("rule_name") or compact.get("rule_identifier") or compact.get("rule_id") or f"Rule {idx}", **compact)
        add_edge(run_node, rec_node, "RUN_NORMALIZED_OLD_RULE")

    for idx, audit in enumerate(kb.get("pagination_replay_audit") or [], start=1):
        audit_node = add_node(_kg_id("pagination", audit.get("url"), idx), "PAGINATION_REPLAY", audit.get("url") or f"pagination {idx}", **{**audit, "order": idx})
        add_edge(stage_node("rule_kb_old_rule_api_pagination"), audit_node, "REPLAYED_PAGINATED_API", order=idx)

    for idx, audit in enumerate(kb.get("detail_enrichment_audit") or [], start=1):
        dm_label = audit.get("rule_name") or audit.get("rule_identifier") or f"detail enrichment {idx}"
        audit_node = add_node(_kg_id("detail_enrichment", dm_label, idx), "RULE_DETAIL_ENRICHMENT", dm_label, order=idx, resolved=audit.get("resolved"), attempts_count=len(audit.get("attempts") or []), rule_name=audit.get("rule_name"), rule_identifier=audit.get("rule_identifier"))
        add_edge(stage_node("rule_kb_old_rule_detail_id_enrichment"), audit_node, "TRIED_DETAIL_ID_ENRICHMENT", order=idx, resolved=audit.get("resolved"))
        for aidx, attempt in enumerate((audit.get("attempts") or [])[:8], start=1):
            api_attempt = add_node(_kg_id("detail_api_attempt", attempt.get("url"), idx, aidx), "DETAIL_API_ATTEMPT", attempt.get("url") or f"attempt {aidx}", **{**attempt, "order": aidx})
            add_edge(audit_node, api_attempt, "ATTEMPTED_READ_ONLY_DETAIL_API", status=attempt.get("status"), rule_id_found=attempt.get("rule_id_found"))

    for idx, audit in enumerate(kb.get("ui_row_action_enrichment_audit") or [], start=1):
        dm_label = audit.get("rule_name") or audit.get("rule_identifier") or f"ui row action {idx}"
        ui_node = add_node(_kg_id("ui_row_action_detail", dm_label, idx), "UI_ROW_ACTION_DETAIL", dm_label, order=idx, resolved=audit.get("resolved"), search_used=audit.get("search_used"), network_events_after_click=audit.get("network_events_after_click"), api_interactions_after_click=audit.get("api_interactions_after_click"), rule_name=audit.get("rule_name"), rule_identifier=audit.get("rule_identifier"), url_rule_id_candidate=audit.get("url_rule_id_candidate"), event_rule_id_candidate=audit.get("event_rule_id_candidate"))
        add_edge(stage_node("rule_kb_ui_row_action_id_learning"), ui_node, "REPEATED_UI_ROW_ACTION_TO_LEARN_ID", order=idx, resolved=audit.get("resolved"))
        click_info = audit.get("click") or {}
        click_node = add_node(_kg_id("ui_row_click", dm_label, idx), "UI_ACTION", click_info.get("reason") or click_info.get("url_after_click") or "row action", clicked=click_info.get("clicked"), row_hint=click_info.get("row_hint"), url_after_click=click_info.get("url_after_click"))
        add_edge(ui_node, click_node, "CLICKED_SAFE_ROW_ACTION", clicked=click_info.get("clicked"))
        for aidx, inter in enumerate((audit.get("api_interactions") or [])[:6], start=1):
            api_node = add_node(_kg_id("ui_row_api", inter.get("method"), inter.get("url"), idx, aidx), "API_INTERACTION", f"{inter.get('method','GET')} {inter.get('url','')}", order=aidx, method=inter.get("method"), url=inter.get("url"), status=inter.get("status"), rule_rows_extracted=inter.get("rule_rows_extracted"))
            add_edge(click_node, api_node, "ROW_ACTION_TRIGGERED_API", rows=inter.get("rule_rows_extracted"))

    # UI/form path.
    add_click_node = add_node(_kg_id("ui_action", run_id, "click_add"), "UI_ACTION", "Click + Add Rule", action="click_add_rule_do_not_save", save_clicked=False)
    add_edge(run_node, add_click_node, "RUN_PERFORMED_SAFE_UI_ACTION")
    add_edge(add_click_node, safety_node, "ACTION_GUARDED_BY_SAFETY")

    for idx, field in enumerate(kb.get("form_controls") or [], start=1):
        field_node = add_node(_kg_id("form_field", field.get("selector") or idx), "FORM_FIELD", field.get("label") or field.get("name") or field.get("id") or f"field {idx}", order=idx, field_label=field.get("label"), selector=field.get("selector"), tag=field.get("tag"), type=field.get("type"), role=field.get("role"), required=field.get("required"), mapped_rule_key=field.get("mapped_rule_key"), recommended_value=field.get("recommended_value"), dom_events_to_try=field.get("dom_events_to_try"))
        add_edge(add_click_node, field_node, "ADD_FORM_CONTAINED_FIELD", required=field.get("required"), mapped_key=field.get("mapped_rule_key"))
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
        "schema_version": "rule_api_flow_kg_v1",
        "created_at": utc_now(),
        "run_id": run_id,
        "url": kb.get("url"),
        "debug_questions_supported": [
            "Which API was triggered when opening Rules?",
            "Which API returned old Rule IDs?",
            "Which pagination URLs were replayed?",
            "Which endpoint returned ruleId/ruleName/dataFormatType/transactionType/validationType?",
            "Which read-only detail lookup attempts were used to find numeric ruleId?",
            "Which real UI row actions were clicked to learn old Rule IDs?",
            "Which fields/dropdowns appeared after + Add?",
            "Which DOM events are required to fill the Add Rule form?",
            "Which dummy values were filled without saving?",
        ],
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "api_interactions": len(interactions),
            "old_rules": len(kb.get("old_rules_inventory") or []),
            "old_rules_with_numeric_id": (kb.get("detail_enrichment_report") or {}).get("rule_ids_found"),
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


def _write_rule_api_flow_knowledge_graph(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    graph = build_rule_api_flow_knowledge_graph(kb)
    json_path = kb_dir / "rule_api_flow_knowledge_graph.json"
    mmd_path = kb_dir / "rule_api_flow_knowledge_graph.mmd"
    md_path = kb_dir / "rule_api_flow_knowledge_graph.md"
    html_path = kb_dir / "rule_api_flow_knowledge_graph.html"
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
        "# Rule API Flow Knowledge Graph",
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

    html_doc = f"""<!rule html><html><head><meta charset='utf-8'/>
<title>Rule API Flow Knowledge Graph</title>
<script type='module'>import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs'; mermaid.initialize({{startOnLoad:true,securityLevel:'loose'}});</script>
<style>body{{font-family:Arial,sans-serif;margin:28px;color:#222}}.card{{border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0}}pre{{background:#f6f8fa;padding:12px;overflow:auto;max-height:520px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #ddd;padding:6px}}</style>
</head><body><h1>Rule API Flow Knowledge Graph</h1>
<div class='card'><b>Run:</b> {html.escape(str(graph.get('run_id')))}<br/><b>URL:</b> {html.escape(str(graph.get('url')))}<br/><b>Nodes:</b> {len(graph.get('nodes', []))} <b>Edges:</b> {len(graph.get('edges', []))}</div>
<h2>Summary</h2><pre>{html.escape(json.dumps(summary, indent=2, ensure_ascii=False))}</pre>
<h2>Flow graph</h2><div class='mermaid'>{html.escape(mmd)}</div>
<h2>Node preview</h2><pre>{html.escape(json.dumps(graph.get('nodes', [])[:160], indent=2, ensure_ascii=False))}</pre>
<h2>Edge preview</h2><pre>{html.escape(json.dumps(graph.get('edges', [])[:220], indent=2, ensure_ascii=False))}</pre>
</body></html>"""
    html_path.write_text(html_doc, encoding="utf-8")
    return {
        "rule_api_flow_kg_json": str(json_path),
        "rule_api_flow_kg_mermaid": str(mmd_path),
        "rule_api_flow_kg_markdown": str(md_path),
        "rule_api_flow_kg_html": str(html_path),
    }


def _write_rule_api_flow_knowledge_graph_safe(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    """Export the reporting Knowledge Graph without replaying a completed portal phase.

    Exact live form execution and independent verification are authoritative.
    Serializer/report failures are preserved as warning artifacts and never
    cause the agent to reopen and refill an already-correct unsaved form.
    """
    status_path = kb_dir / "rule_api_flow_knowledge_graph_export_status.json"
    try:
        files = _write_rule_api_flow_knowledge_graph(kb_dir, kb)
        status = {
            "status": "ok",
            "pass": True,
            "non_blocking": True,
            "run_id": kb.get("run_id"),
            "files": files,
        }
        status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {**files, "rule_api_flow_kg_export_status": str(status_path)}
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
        return {"rule_api_flow_kg_export_status": str(status_path)}


def _write_markdown(path: Path, kb: Dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pv = kb.get("previous_interaction_values", {})
    dm = (pv.get("rule_values_to_fill") or {})
    lines = [
        "# HIP SecureLink Rule Form KB",
        "",
        f"Run ID: `{kb.get('run_id')}`",
        f"URL: `{kb.get('url')}`",
        "",
        "## Safety",
        "This KB run opens `+ Add`, fills dummy values, captures form/dropdown/DOM evidence, and never clicks Save/Create/Submit.",
        "",
        "## Previous Rule values from provided input",
        "| Field | Value |",
        "|---|---|",
    ]
    for k, v in dm.items():
        lines.append(f"| `{k}` | `{v}` |")
    lines += ["", "## Known/prior IDs", "| ID | Value |", "|---|---|"]
    for k, v in (pv.get("known_ids") or {}).items():
        lines.append(f"| `{k}` | `{v}` |")
    old_maps = kb.get("old_rules_inventory") or []
    id_report = kb.get("detail_enrichment_report") or {}
    lines += ["", "## Old Rules learned from listing/detail APIs", f"Total old Rule rows normalized: `{len(old_maps)}`", f"Numeric ruleIds found: `{id_report.get('rule_ids_found', 0)}` / `{id_report.get('total_old_rules', len(old_maps))}`", f"ID completion: `{id_report.get('completion_percent', 0)}%`", "", "| ruleId | Rule Name | Version | Status | Transaction Type | Data Format Type | Validation Type | Rule Identifier | Root Element | File | Source |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in old_maps[:300]:
        lines.append(f"| `{row.get('rule_id','')}` | `{row.get('rule_name','')}` | `{row.get('rule_version','')}` | `{row.get('status','')}` | `{row.get('transaction_type','')}` | `{row.get('format','')}` | `{row.get('validation_type','')}` | `{row.get('rule_identifier','')}` | `{row.get('root_element','')}` | `{row.get('schema_file','')}` | `{row.get('source','')}` |")
    if len(old_maps) > 300:
        lines.append(f"| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | `{len(old_maps)-300} more rows in old_rules_inventory.csv` |")
    ui_report = kb.get("ui_row_action_report") or {}
    lines += ["", "## Old Rule UI row/action ID learning", "This phase repeats the real portal interaction: search an old Rule row, click its safe View/Edit/Details action, capture the API triggered by that click, and parse numeric `ruleId` when exposed.", "", "```json", json.dumps(ui_report, indent=2, ensure_ascii=False), "```"]
    api_interactions = kb.get("rule_api_interactions") or []
    lines += ["", "## Rule API interactions learned", "| Method | Status | URL | Rows extracted | Response shape |", "|---|---:|---|---:|---|"]
    for inter in api_interactions[:80]:
        shape = inter.get('response_shape') or {}
        lines.append(f"| `{inter.get('method','')}` | `{inter.get('status','')}` | `{inter.get('url','')}` | `{inter.get('rule_rows_extracted',0)}` | `{shape.get('type','')} rows={shape.get('row_count',0)}` |")
    lines += ["", "## Required fields discovered", "| Label | Mapped key | Selector | Recommended dummy value |", "|---|---|---|---|"]
    for c in kb.get("required_fields", []):
        lines.append(f"| {c.get('label','')} | `{c.get('mapped_rule_key','')}` | `{c.get('selector','')}` | `{c.get('recommended_value','')}` |")
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
        kb_dir / "rule_form_kb.json",
        kb_dir / "old_rules_inventory.json",
        kb_dir / "old_rules_inventory.csv",
        kb_dir / "old_rule_id_lookup_by_name.json",
        kb_dir / "rule_api_interactions.json",
        kb_dir / "rule_api_pagination_audit.json",
        kb_dir / "rule_detail_enrichment_audit.json",
        kb_dir / "rule_ui_row_action_enrichment_audit.json",
        kb_dir / "rule_id_completion_report.json",
        kb_dir / "old_rules_inventory_with_ids.json",
        kb_dir / "old_rules_inventory_with_ids.csv",
        kb_dir / "rule_listing_ui_rows.json",
        kb_dir / "rule_previous_interaction_values.json",
        kb_dir / "rule_dropdowns.json",
        kb_dir / "rule_required_fields.json",
        kb_dir / "rule_dummy_fill_plan.json",
        kb_dir / "rule_dom_events.json",
        kb_dir / "rule_api_flow_knowledge_graph.json",
        kb_dir / "rule_api_flow_knowledge_graph.mmd",
        kb_dir / "rule_api_flow_knowledge_graph.md",
        kb_dir / "rule_api_flow_knowledge_graph.html",
        kb_dir / "RULE_KB_SUMMARY.md",
        kb_dir / "rule_form_controls.csv",
        kb_dir / "compact_action_sequence.json",
        kb_dir / "compact_click_sequence.json",
        kb_dir / "compact_network_summary.json",
    ]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in include:
            if p.exists():
                z.write(p, arcname=str(p.relative_to(run_dir)))
    return str(zip_path)
