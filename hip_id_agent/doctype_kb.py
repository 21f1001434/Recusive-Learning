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
from .deterministic_plan_runtime import sort_controls as deterministic_sort_controls, annotate_attempt as annotate_plan_attempt, plan_summary as deterministic_plan_summary, state_graph as deterministic_state_graph, apply_live_exploration_overlay
from .upload_assets import attempt_upload_for_control
from .active_surface import inspect_doctype_create_surface, get_doctype_create_root
from .dds_control_driver import select_dds_combobox, close_open_dropdown, set_text_control as dds_set_text_control, semantic_runtime_enabled, open_control_for_discovery
from .phase_form_entry import ensure_phase_form_entry, find_same_page_top_right_add, same_page_add_candidate
from .autonomous_form_runtime import execute_autonomous_phase_goal, autonomous_phase_enabled
from .stateful_form_runtime import (
    compile_document_type_state_graph,
    execute_document_type_state_graph,
    build_target_branch_knowledge,
    capture_document_type_controls,
    build_document_type_form_state_model,
)

DOCTYPES_URL = "https://developer.dell.com/hybrid-integrations/securelink/doctypes"

DEFAULT_DUMMY_DOCUMENT_TYPE = {
    "document_type_name": "DUMMY_XML_DellAutoASN_10_UHAUL_KB",
    "document_type_version": "1",
    "status": "Enable",
    "transaction_type": "856",
    "format": "XML",
    "data_format_type": "XML",
    "description": "Dummy Document Type KB capture only",
    "validation_type": "Structure",
    "document_identifier": "DUMMY_DellAutoASN_KB",
    "document_identifier_operation": "All conditions are satisfied",
    "document_identifier_derived_from": "TRANSACTION_ROOT_ELEMENT",
    "root_element": "ShipmentNotice",
    "document_version": "1.0",
    "schema_file": "DUMMY_DocumentTypeSchema.xsd",
    "attribute_name": "Receiver",
    "attribute_derived_from": "ELEMENT_IN_PAYLOAD",
    "attribute_usage": "Flow Identifier Expression, Logging, Mapping, Routing",
    "attribute_expression": "/DummyRoot/Header/Receiver",
    "usage": "source",
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
class DocumentTypeKBResult:
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


def _extract_document_identifier_parts(value: Any) -> Dict[str, str]:
    """Normalize the portal/API documentIdentifier structure into KB-fillable parts.

    The live details API returns documentIdentifier as either a scalar/string or a
    dict such as::

        {"attributeList": [{"derivedFrom": "ELEMENT_IN_PAYLOAD",
                            "expression": "ISA*06", "value": "ABBVIE"}],
         "operator": "ONE"}

    Earlier builds only understood a `rows` array, so `attributeList` was preserved
    only inside raw_detail_compact and the normalized KB fields stayed blank.
    """
    out = {
        "document_identifier": "",
        "document_identifier_operation": "",
        "document_identifier_derived_from": "",
    }
    if isinstance(value, dict):
        out["document_identifier_operation"] = _scalar_text(value.get("operation") or value.get("operator"))
        rows = []
        for row_key in ("rows", "attributeList", "attributes", "identifierRows", "conditions"):
            if isinstance(value.get(row_key), list):
                rows = value.get(row_key) or []
                break
        if rows and isinstance(rows[0], dict):
            first = rows[0]
            out["document_identifier_derived_from"] = _scalar_text(
                first.get("derived_from") or first.get("derivedFrom") or first.get("source")
            )
            out["document_identifier"] = _scalar_text(
                first.get("value")
                or first.get("rootElement")
                or first.get("root_element")
                or first.get("identifier")
                or first.get("documentIdentifier")
            )
        if not out["document_identifier"]:
            out["document_identifier"] = _scalar_text(value.get("value") or value.get("rootElement") or value.get("root_element"))
    else:
        out["document_identifier"] = _scalar_text(value)
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


def extract_document_type_seed(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract Document Type values already known from user/manual input.

    The Document Type input often stores `document_identifier` as the portal payload
    shape: `{operation, rows:[{derived_from, value}]}`.  For the KB/form learner we
    preserve the original structure and also extract the first fillable value so the
    dummy form does not receive a raw Python dict string.
    """
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    candidates: List[Dict[str, Any]] = []
    for key in ["target_document_type", "source_document_type", "document_type"]:
        val = (objects or {}).get(key) or input_data.get(key) if isinstance(input_data, dict) else None
        if isinstance(val, dict):
            candidates.append(val)
    result = dict(DEFAULT_DUMMY_DOCUMENT_TYPE)
    for dt in candidates:
        for src_key, dst_key in [
            ("name", "document_type_name"),
            ("documentTypeName", "document_type_name"),
            ("document_type_name", "document_type_name"),
            ("version", "document_type_version"),
            ("documentTypeVersion", "document_type_version"),
            ("document_type_version", "document_type_version"),
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
            ("document_version", "document_version"),
            ("documentVersion", "document_version"),
            ("schema_file", "schema_file"),
            ("schemaFile", "schema_file"),
            ("usage", "usage"),
        ]:
            if dt.get(src_key) not in (None, ""):
                result[dst_key] = dt.get(src_key)
                if dst_key == "format":
                    result["data_format_type"] = dt.get(src_key)
        doc_identifier_value = dt.get("documentIdentifier") if dt.get("documentIdentifier") not in (None, "") else dt.get("document_identifier")
        if doc_identifier_value not in (None, ""):
            result["document_identifier_payload"] = doc_identifier_value
            result.update({k: v for k, v in _extract_document_identifier_parts(doc_identifier_value).items() if v})
        if dt.get("operation") not in (None, ""):
            result["document_identifier_operation"] = dt.get("operation")
        attrs = dt.get("attributes_to_configure") or dt.get("attributes") or dt.get("documentAttributes")
        if isinstance(attrs, list) and attrs:
            result["attributes_to_configure"] = attrs
            result["attribute_name"] = _first_attr_value(attrs, "attribute_name", result.get("attribute_name", ""))
            result["attribute_derived_from"] = _first_attr_value(attrs, "attribute_derived_from", result.get("attribute_derived_from", ""))
            result["attribute_usage"] = _first_attr_value(attrs, "attribute_usage", result.get("attribute_usage", ""))
            result["attribute_expression"] = _first_attr_value(attrs, "attribute_expression", result.get("attribute_expression", ""))
    return result

def build_previous_interaction_values(input_data: Dict[str, Any], *, known_document_type_id: str | None = None) -> Dict[str, Any]:
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    dm = extract_document_type_seed(input_data)
    source_dt = (objects or {}).get("source_document_type") or {}
    target_dt = (objects or {}).get("target_document_type") or {}
    rule = (objects or {}).get("rule") or {}
    return {
        "source": "uploaded_input_json_and_prior_manual_context",
        "captured_at": utc_now(),
        "known_ids": {
            "document_type_id": known_document_type_id or "UNKNOWN_FROM_CURRENT_DOCTYPE_KB_RUN",
            "target_document_type_id": "10483",  # from prior API phase note; confirm before final create if DEV differs
        },
        "document_type_values_to_fill": dm,
        "related_values": {
            "source_document_type_name": source_dt.get("name"),
            "source_document_type_version": source_dt.get("version"),
            "target_document_type_name": target_dt.get("name"),
            "target_document_type_version": target_dt.get("version"),
            "rule_name": rule.get("name"),
            "rule_version": rule.get("version"),
            "mapping_identifier_name_version": ((rule.get("actions") or {}) if isinstance(rule, dict) else {}).get("mapping_identifier_name_version"),
        },
    }


def build_dummy_fill_values(seed: Dict[str, Any], *, exact: bool = False) -> Dict[str, str]:
    """Use real-shaped but disposable scalar values.

    The function intentionally avoids stringifying nested payloads such as
    `document_identifier` or `attributes_to_configure`; it extracts the fillable
    field values instead.  These values are safe only because the flow never clicks
    Save/Create.
    """
    values = dict(DEFAULT_DUMMY_DOCUMENT_TYPE)
    for key, value in (seed or {}).items():
        if key in {"attributes_to_configure", "document_identifier_payload"}:
            continue
        if value not in (None, ""):
            values[key] = _scalar_text(value)
    attrs = (seed or {}).get("attributes_to_configure")
    if isinstance(attrs, list) and attrs:
        values["attribute_name"] = _first_attr_value(attrs, "attribute_name", values.get("attribute_name", "Receiver"))
        values["attribute_derived_from"] = _first_attr_value(attrs, "attribute_derived_from", values.get("attribute_derived_from", "ELEMENT_IN_PAYLOAD"))
        values["attribute_usage"] = _first_attr_value(attrs, "attribute_usage", values.get("attribute_usage", "Flow Identifier Expression, Logging, Mapping, Routing"))
        values["attribute_expression"] = _first_attr_value(attrs, "attribute_expression", values.get("attribute_expression", "/DummyRoot/Header/Receiver"))
    if not exact:
        if not values.get("document_type_name", "").upper().startswith("DUMMY"):
            values["document_type_name"] = f"DUMMY_{_safe_name(values['document_type_name'])}_KB"
        if not values.get("document_identifier", "").upper().startswith("DUMMY"):
            values["document_identifier"] = f"DUMMY_{_safe_name(values['document_identifier'])}_KB"
    if not values.get("root_element", ""):
        values["root_element"] = "DummyRoot"
    values["data_format_type"] = values.get("format") or values.get("data_format_type") or "XML"
    return {k: str(v) for k, v in values.items() if isinstance(v, (str, int, float, bool))}

def guess_field_key(label: str, attrs: Dict[str, Any]) -> Optional[str]:
    text = " ".join(str(x or "") for x in [label, attrs.get("name"), attrs.get("id"), attrs.get("placeholder"), attrs.get("ariaLabel")]).lower()
    text = re.sub(r"\s+", " ", text).strip()
    # Avoid mapping table/search/filter controls as Document Type data fields.
    if any(skip in text for skip in ["table search", "filter by column", "search"]):
        return None
    exact_label = str(label or "").strip().lower()
    exact_map = {
        "name": "document_type_name",
        "document type name": "document_type_name",
        "doc type name": "document_type_name",
        "version": "document_type_version",
        "document type version": "document_type_version",
        "transaction type": "transaction_type",
        "data format type": "format",
        "format": "format",
        "description": "description",
        "validation type": "validation_type",
        "operation": "document_identifier_operation",
        "document identifier": "document_identifier",
        "root element": "root_element",
        "attribute name": "attribute_name",
        "derived from": "derived_from",
        "usage": "attribute_usage",
        "expression": "attribute_expression",
        "document schema file": "schema_file",
        "schema file": "schema_file",
    }
    if exact_label in exact_map:
        return exact_map[exact_label]
    patterns = [
        ("document_type_name", ["document type name", "doctype name", "doc type name"]),
        ("attribute_name", ["attribute name"]),
        ("document_type_version", ["document type version", "doctype version", " version"]),
        ("transaction_type", ["transaction type", "transaction"]),
        ("format", ["data format", "format", "edi", "xml", "x12"]),
        ("validation_type", ["validation type", "validation"]),
        ("description", ["description"]),
        ("document_identifier_operation", ["operation"]),
        ("document_identifier", ["document identifier", "identifier"]),
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
  function findDocumentTypeFormRoot() {
    const candidates = Array.from(document.querySelectorAll('[role="dialog"], dds-drawer, .dds__drawer, .dds__modal, form, app-document-type-create, app-document-types, main'))
      .filter(isVisible)
      .map(el => {
        const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        const low = txt.toLowerCase();
        let score = 0;
        for (const term of ['create document type', 'document type details', 'document identifier', 'attributes to configure']) {
          if (low.includes(term)) score += 30;
        }
        for (const term of ['transaction type', 'data format type', 'operation', 'attribute name', 'validation type']) {
          if (low.includes(term)) score += 6;
        }
        if ((/items per page|filter by column|showing \d+ of \d+ columns/i.test(txt)) && !low.includes('create document type')) score -= 100;
        if (/^filters?\b/i.test(txt)) score -= 100;
        const r = el.getBoundingClientRect();
        return {el, score, area: Math.max(1, r.width * r.height)};
      })
      .filter(x => x.score >= 120)
      .sort((a, b) => (b.score - a.score) || (a.area - b.area));
    return candidates.length ? candidates[0].el : null;
  }
  const root = findDocumentTypeFormRoot();
  if (!root) return [];
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
    """Find the real Document Type toolbar + Add button.

    The target-document-type phase can land on a partially-rendered listing where
    generic text locators either miss the DDS web-component button or pick a
    background/listing control. Resolve the clickable ancestor from visible
    text/ARIA first, excluding nav/footer/pagination/cookie/manage-column areas.
    """
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
    except Exception:
        pass
    shared = await find_same_page_top_right_add(page)
    if shared is not None:
        return shared
    try:
        cand = await page.evaluate(r"""
() => {
  function visible(el){const r=el&&el.getBoundingClientRect?el.getBoundingClientRect():{width:0,height:0}; const s=el?getComputedStyle(el):null; return !!(r.width&&r.height&&s&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0');}
  function clean(v){return String(v||'').replace(/\s+/g,' ').trim();}
  function path(el){const parts=[]; let n=el; while(n&&n.nodeType===1&&parts.length<9){let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=(n.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>'.'+CSS.escape(c)).join(''); p+=cls; const par=n.parentElement; if(par){const sib=Array.from(par.children).filter(x=>x.tagName===n.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(n)+1)+')';} parts.unshift(p); n=par;} return parts.join(' > ');}
  const unsafe='nav,header,footer,dds-pagination,.dds__pagination,#onetrust-consent-sdk,#onetrust-pc-sdk,#onetrust-banner-sdk,.dds__modal,.dds__drawer';
  const bad=/manage columns|filter|search|clear|cancel|close|delete|remove|submit|save/i;
  const nodes=Array.from(document.querySelectorAll('button,a,[role=button],dds-button,.dds__button,span,div')).filter(visible);
  const scored=[];
  for(const el of nodes){
    const label=clean(el.innerText||el.textContent||el.getAttribute('aria-label')||el.getAttribute('title')||'');
    if(!/^\+?\s*Add$/i.test(label)) continue;
    const click=el.closest('button,a,[role=button],dds-button,.dds__button')||el;
    if(!click || click.closest(unsafe)) continue;
    const all=clean(click.innerText||click.textContent||click.getAttribute('aria-label')||label);
    if(bad.test(all) && !/^\+?\s*Add$/i.test(all)) continue;
    const r=click.getBoundingClientRect();
    let score=0; if(r.top<280)score+=4; if(r.left>window.innerWidth*0.45)score+=4; if(click.closest('main,.app__content,.dds__container'))score+=3; if(/^\+\s*Add$/i.test(label))score+=3;
    scored.push({selector:path(click), label, score, x:r.x, y:r.y});
  }
  scored.sort((a,b)=>b.score-a.score);
  return scored[0] || null;
}
""")
        if cand and cand.get("selector"):
            loc = page.locator(cand["selector"]).first
            if await loc.count() and await loc.is_visible(timeout=1000) and await loc.is_enabled(timeout=1000):
                if await same_page_add_candidate(page, loc):
                    return loc
    except Exception:
        pass
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
            if await loc.count() and await loc.is_visible(timeout=1200) and await loc.is_enabled(timeout=1200):
                if await same_page_add_candidate(page, loc):
                    return loc
        except Exception:
            continue
    return None


async def _force_open_doctypes_listing(page: Page, doctypes_url: str) -> bool:
    """Recover the Document Types listing when SPA routing leaves us on Home."""
    for attempt in range(3):
        try:
            await page.goto(doctypes_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000 + attempt * 700)
            if await _find_add_button(page):
                return True
        except Exception:
            pass
    try:
        for sel in ["text=/^SecureLink$/i", "button:has-text('SecureLink')", "[role=button]:has-text('SecureLink')"]:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible(timeout=1000):
                    if semantic_runtime_enabled(page):
                        if not await open_control_for_discovery(page, sel, label="Open SecureLink navigation", phase="document_type"):
                            continue
                    else:
                        await loc.click(timeout=1500)
                    await page.wait_for_timeout(800)
                    break
            except Exception:
                continue
        for sel in ["text=/Document\\s*Types?/i", "text=/Doc\\s*Types?/i", "a[href*='doctypes' i]", "[role=button]:has-text('Document')"]:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible(timeout=1500):
                    if semantic_runtime_enabled(page):
                        if not await open_control_for_discovery(page, sel, label="Open Document Types navigation", phase="document_type"):
                            continue
                    else:
                        await loc.click(timeout=2500)
                    await page.wait_for_timeout(2500)
                    return bool(await _find_add_button(page))
            except Exception:
                continue
    except Exception:
        pass
    return False


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


async def _looks_like_doctype_add_form(page: Page) -> bool:
    info = await inspect_doctype_create_surface(page)
    if info.get("pass"):
        return True
    # Lightweight test doubles used by the unit suite expose only this state.
    # Real Playwright pages never use this branch.
    if hasattr(page, "form_open"):
        return bool(getattr(page, "form_open"))
    return False


async def _save_doctype_surface_evidence(kb_dir: Path, name: str, info: Dict[str, Any]) -> str:
    try:
        return safe_write_json(kb_dir / f"{name}.json", mask_sensitive_data(info))
    except Exception:
        return ""


async def _dismiss_safe_doctype_overlays(page: Page) -> Dict[str, Any]:
    """Dismiss only non-mutating filter/listing overlays."""
    audit: Dict[str, Any] = {"attempted": False, "dismissed": False}
    try:
        info = await inspect_doctype_create_surface(page)
        if not info.get("filters_open"):
            return audit
        audit["attempted"] = True
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(350)
        audit["dismissed"] = not bool((await inspect_doctype_create_surface(page)).get("filters_open"))
    except Exception as exc:
        audit["error"] = mask_sensitive_string(str(exc))
    return audit



async def _confirm_doctype_discard_dialog(page: Page) -> Dict[str, Any]:
    """Confirm only an unsaved-change discard/leave dialog.

    This helper is intentionally narrow: it never clicks Save/Create/Submit/Delete
    and is used only to throw away the value-free structure-probe drawer before
    opening the one clean business-input form.
    """
    audit: Dict[str, Any] = {"detected": False, "confirmed": False}
    try:
        result = await page.evaluate(r"""
() => {
  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return !!(r.width && r.height && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || '1') !== 0);
  }
  function clean(v) { return String(v || '').replace(/\s+/g, ' ').trim(); }
  const dialogs = Array.from(document.querySelectorAll('[role=dialog], .dds__modal, dds-modal, app-generic-modal'))
    .filter(visible);
  const unsafe = /(save|create|submit|delete|remove|deploy|update|enable|disable)/i;
  const confirmRx = /^(discard|discard changes|leave|leave page|continue without saving|yes)$/i;
  for (const dialog of dialogs) {
    const body = clean(dialog.innerText || dialog.textContent);
    if (!/(unsaved|discard|leave.*without saving|changes.*lost)/i.test(body)) continue;
    const buttons = Array.from(dialog.querySelectorAll('button, [role=button], dds-button, a')).filter(visible);
    const button = buttons.find(el => {
      const label = clean(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title'));
      return !unsafe.test(label) && confirmRx.test(label);
    });
    if (!button) return {detected: true, confirmed: false, reason: 'safe_discard_button_not_found'};
    button.click();
    return {detected: true, confirmed: true, label: clean(button.innerText || button.textContent || button.getAttribute('aria-label'))};
  }
  return {detected: false, confirmed: false};
}
""")
        if isinstance(result, dict):
            audit.update(result)
        await page.wait_for_timeout(600)
    except Exception as exc:
        audit["error"] = mask_sensitive_string(str(exc))
    return audit


async def _discard_doctype_structure_probe_surface(
    page: Page,
    browser: BrowserSession,
    *,
    kb_dir: Path,
    warnings: List[str],
    stage: str,
) -> Dict[str, Any]:
    """Close the active Create Document Type drawer before a clean reopen.

    Navigating to the same Angular route does not reliably close a DDS drawer and
    can temporarily leave a blank SPA surface.  The structure-first lifecycle must
    therefore close the active drawer, prove the listing toolbar is back, and only
    then click + Add again.  One bounded retry handles a close click swallowed by
    an active DDS dropdown.
    """
    audit: Dict[str, Any] = {
        "schema_version": "hip.doctype-structure-probe-discard.v1",
        "stage": stage,
        "attempts": [],
        "pass": False,
    }
    initial = await inspect_doctype_create_surface(page)
    audit["initial_surface_pass"] = bool(initial.get("pass"))
    if not initial.get("pass"):
        add = await _find_add_button(page)
        if add is not None:
            audit.update({"pass": True, "already_closed": True, "reason": "listing_toolbar_visible"})
            return audit

    for attempt_no in range(1, 3):
        attempt: Dict[str, Any] = {"attempt": attempt_no}
        try:
            # Commit/close any active DDS popup first. Otherwise the first pointer
            # interaction can be consumed by the dropdown instead of the drawer.
            await close_open_dropdown(page, "source_document_type")
            await page.wait_for_timeout(250)
        except Exception as exc:
            attempt["dropdown_settle_error"] = mask_sensitive_string(str(exc))

        surface = await inspect_doctype_create_surface(page)
        attempt["surface_before_close"] = bool(surface.get("pass"))
        if not surface.get("pass"):
            add = await _find_add_button(page)
            if add is not None:
                attempt["listing_toolbar_visible"] = True
                audit["attempts"].append(attempt)
                audit.update({"pass": True, "reason": "drawer_already_closed"})
                return audit

        root = await get_doctype_create_root(page)
        if root is None:
            attempt["reason"] = "active_create_root_not_found"
            audit["attempts"].append(attempt)
            continue

        close_locator: Optional[Locator] = None
        selectors = [
            "button.dds__drawer__close[aria-label*='Close' i]",
            ".dds__drawer__close[aria-label*='Close' i]",
            "button:has-text('Back')",
            "[role=button]:has-text('Back')",
        ]
        for selector in selectors:
            try:
                candidates = root.locator(selector)
                count = await candidates.count()
                for index in range(min(count, 4)):
                    candidate = candidates.nth(index)
                    if await candidate.is_visible(timeout=600) and await candidate.is_enabled(timeout=600):
                        close_locator = candidate
                        attempt["close_selector"] = selector
                        attempt["close_index"] = index
                        break
                if close_locator is not None:
                    break
            except Exception:
                continue

        if close_locator is None:
            attempt["reason"] = "safe_drawer_close_not_found"
            audit["attempts"].append(attempt)
            continue

        try:
            await browser.log_automation_click(
                action=f"discard_doctype_structure_probe_{attempt_no}",
                locator=close_locator,
                selector="Create Document Type Back/Close",
                extra={"safe_no_save": True, "stage": stage},
            )
        except Exception:
            pass
        try:
            await close_locator.click(timeout=3500)
            attempt["close_clicked"] = True
        except Exception as exc:
            attempt["close_clicked"] = False
            attempt["close_error"] = mask_sensitive_string(str(exc))
            audit["attempts"].append(attempt)
            continue

        await page.wait_for_timeout(700)
        attempt["discard_dialog"] = await _confirm_doctype_discard_dialog(page)
        await page.wait_for_timeout(700)
        final_surface = await inspect_doctype_create_surface(page)
        add = await _find_add_button(page)
        attempt["surface_after_close"] = bool(final_surface.get("pass"))
        attempt["listing_toolbar_visible"] = add is not None
        audit["attempts"].append(attempt)
        if not final_surface.get("pass") and add is not None:
            audit.update({"pass": True, "reason": "drawer_discarded_and_listing_restored"})
            await _save_doctype_surface_evidence(kb_dir, f"doctype_surface_{stage}_discarded", audit)
            return audit

    warnings.append(
        "Create Document Type structure-probe drawer could not be safely discarded; "
        "the agent stopped before entering customer values."
    )
    audit["reason"] = "drawer_discard_not_committed"
    await _save_doctype_surface_evidence(kb_dir, f"doctype_surface_{stage}_discard_failed", audit)
    return audit


async def _ensure_doctype_create_surface(
    page: Page,
    browser: BrowserSession,
    *,
    doctypes_url: str,
    kb_dir: Path,
    warnings: List[str],
    stage: str,
    reopen: bool = True,
    force_reopen: bool = False,
) -> Dict[str, Any]:
    """Prove the Create Document Type form, reopening it safely if necessary."""
    initial = await inspect_doctype_create_surface(page)
    await _save_doctype_surface_evidence(kb_dir, f"doctype_surface_{stage}_initial", initial)
    if initial.get("pass") and not force_reopen:
        return {"pass": True, "reopened": False, "surface": initial}
    dismiss = await _dismiss_safe_doctype_overlays(page)
    after_dismiss = await inspect_doctype_create_surface(page)
    if after_dismiss.get("pass") and not force_reopen:
        await _save_doctype_surface_evidence(kb_dir, f"doctype_surface_{stage}_after_overlay_dismiss", after_dismiss)
        return {"pass": True, "reopened": False, "overlay_dismiss": dismiss, "surface": after_dismiss}
    if not reopen:
        return {"pass": False, "reopened": False, "overlay_dismiss": dismiss, "surface": after_dismiss}

    discard: Dict[str, Any] = {}
    if force_reopen and after_dismiss.get("pass"):
        discard = await _discard_doctype_structure_probe_surface(
            page, browser, kb_dir=kb_dir, warnings=warnings, stage=stage
        )
        if not discard.get("pass"):
            result = {
                "pass": False, "reopened": False, "reason": "active_drawer_discard_failed",
                "overlay_dismiss": dismiss, "discard": discard, "surface": after_dismiss,
            }
            await _save_doctype_surface_evidence(kb_dir, f"doctype_surface_{stage}_reopen_failed", result)
            return result
        after_dismiss = await inspect_doctype_create_surface(page)

    warnings.append(f"Document Type Create surface is being reopened during {stage}; reacquiring semantic controls.")
    add = await _find_add_button(page)
    if add is None:
        # Navigate only after an active drawer has been proven closed. Navigating
        # to the same Angular route while the drawer is open caused the live blank
        # surface observed in run 20260720-163756.
        await _force_open_doctypes_listing(page, doctypes_url)
        add = await _find_add_button(page)
    if add is None:
        result = {
            "pass": False, "reopened": False, "reason": "toolbar_add_not_found",
            "surface": after_dismiss, "discard": discard,
        }
        await _save_doctype_surface_evidence(kb_dir, f"doctype_surface_{stage}_reopen_failed", result)
        return result
    opened = await _click_add_doctype_with_overlay_recovery(page, browser, add, kb_dir=kb_dir, warnings=warnings)
    final = await inspect_doctype_create_surface(page)
    result = {
        "pass": bool(opened and final.get("pass")),
        "reopened": bool(opened),
        "overlay_dismiss": dismiss,
        "discard": discard,
        "surface": final,
    }
    await _save_doctype_surface_evidence(kb_dir, f"doctype_surface_{stage}_reopened", result)
    return result


async def _click_add_doctype_with_overlay_recovery(
    page: Page,
    browser: BrowserSession,
    add: Locator,
    *,
    kb_dir: Path,
    warnings: List[str],
) -> bool:
    """Open + Add for form learning without crashing on stale loading overlays."""
    attempts: List[str] = []
    for label in ["normal", "after_overlay_wait"]:
        try:
            await browser.wait_for_blocking_overlays_gone(timeout_ms=30000 if label == "normal" else 60000)
            await browser.click_and_wait(
                action=f"structural_opener click_add_doctype_do_not_save_{label}",
                locator=add,
                selector="Add Document Type",
            )
            await page.wait_for_timeout(1000)
            if await _looks_like_doctype_add_form(page):
                return True
            attempts.append(f"{label}: click returned but Add form was not detected")
        except Exception as exc:
            msg = mask_sensitive_string(str(exc))
            attempts.append(f"{label}: {msg}")
            try:
                await browser.save_dom_snapshot(f"doctype_add_click_failed_{label}")
            except Exception:
                pass
            await page.wait_for_timeout(1500)

    disabled = await _disable_stale_loading_overlays(page)
    if disabled:
        warnings.append(f"Stale Document Type loading overlay detected before + Add; disabled pointer-events on {disabled} overlay node(s) for read-only KB form capture.")
    try:
        # Final recovery remains governed: semantic proof -> AutoWebGLM ->
        # Playwright MCP -> effect verification. No HTMLElement.click fallback.
        await browser.click_and_wait(
            action="structural_opener click_add_doctype_do_not_save_semantic_overlay_recovery",
            locator=add, selector="Add Document Type", mutation_risk=False,
        )
        await page.wait_for_timeout(1500)
        if await _looks_like_doctype_add_form(page):
            return True
        attempts.append("semantic_overlay_recovery: Add form was not detected")
    except Exception as exc:
        attempts.append(f"semantic_overlay_recovery: {mask_sensitive_string(str(exc))}")

    warnings.append("Could not open + Add Document Type form after overlay-aware retries. Old Document Type inventory/API/UI-row KB is still saved; Add-form controls are skipped. Attempts: " + " | ".join(attempts[-4:]))
    try:
        await browser.screenshot(kb_dir / "doctype_add_click_failed_overlay.png", full_page=True)
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


def _filter_doctype_dropdown_options(label: str, options: List[Dict[str, Any]], portal_noise: List[str], option_allowlists: Dict[str, set]) -> List[Dict[str, Any]]:
    """Keep dropdown options scoped to Document Type form values.

    The Dell portal keeps header/navigation/footer/listing controls mounted while the Add form is open.
    Some DDS dropdown popups are rendered globally, so a naive document-wide option scan can mix
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


async def _collect_dropdown_options(page: Page, controls: List[Dict[str, Any]], max_dropdowns: int = 30, *, phase: str = "document_type") -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    dropdown_terms = [
        "status", "version", "transaction", "type", "format", "validation", "operation", "derived", "usage", "select", "dropdown"
    ]
    footer_noise = ["copyright", "privacy", "terms of use", "accessibility", "cookie", "all rights reserved"]
    portal_noise = [
        "home", "bizlink", "securelink", "bizexchange", "transtrack", "bizmon",
        "document type", "data map", "rules", "transport profile", "profile", "logout",
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
            opts = _filter_doctype_dropdown_options(label, c.get("options") or [], portal_noise, option_allowlists)
            results.append({"label": label, "selector": c.get("selector"), "kind": "select", "options": opts, "dom_event": "change"})
            continue
        if role != "combobox" and "select" not in str(c.get("selector", "")).lower():
            continue
        # Only open things that look like Document Type dropdowns, not arbitrary table filters/search boxes.
        text = " ".join(str(x or "") for x in [label, c.get("ariaLabel"), c.get("placeholder"), c.get("selector")]).lower()
        if any(k in text for k in ["filter", "search", "sort", "pagination", "page"]):
            continue
        if not any(k in text for k in dropdown_terms):
            continue
        try:
            loc = page.locator(c.get("selector")).first
            if not await loc.is_visible(timeout=1000) or not await loc.is_enabled(timeout=1000):
                continue
            if not await open_control_for_discovery(page, str(c.get("selector") or ""), label=str(label), phase=phase):
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
            await close_open_dropdown(page, phase)
            surface = await inspect_doctype_create_surface(page)
            if not surface.get("pass"):
                raise RuntimeError("Create Document Type surface was lost while cataloguing dropdown options")
            opts = _filter_doctype_dropdown_options(label, opts or [], portal_noise, option_allowlists)
            results.append({"label": label, "selector": c.get("selector"), "kind": "combobox", "options": mask_sensitive_data(opts or []), "dom_event": "click->listbox option->change"})
        except Exception as exc:
            results.append({"label": label, "selector": c.get("selector"), "kind": "combobox", "options": [], "error": mask_sensitive_string(str(exc)), "dom_event": "click attempted"})
    return results


def _annotate_control_occurrences(controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for control in controls:
        label = re.sub(r"\s+", " ", str(control.get("label") or control.get("ariaLabel") or control.get("placeholder") or "")).strip().lower()
        occurrence = counts.get(label, 0)
        control["semantic_label"] = label
        control["semantic_occurrence"] = occurrence
        counts[label] = occurrence + 1
    return controls


async def _reacquire_doctype_control(page: Page, control: Dict[str, Any]) -> str:
    """Resolve the current DOM selector after Angular/DDS rerenders."""
    selector = str(control.get("selector") or "")
    try:
        if selector:
            loc = page.locator(selector).first
            if await loc.count() and await loc.is_visible(timeout=400):
                return selector
    except Exception:
        pass
    root = await get_doctype_create_root(page)
    if root is None:
        return ""
    label = str(control.get("semantic_label") or control.get("label") or "").strip().lower()
    occurrence = int(control.get("semantic_occurrence") or 0)
    name = str(control.get("name") or "")
    try:
        rows = await root.locator('input:not([type=hidden]),textarea,select,[role="combobox"]').evaluate_all(r"""
(els) => {
  function visible(el){if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden');}
  function lab(el){if(el.id){const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`);if(l)return (l.innerText||l.textContent||'').replace(/\s+/g,' ').trim();}const w=el.closest('.dds__form__field,.dds__form-field,.form-group,.field,td,div');const l=w&&w.querySelector('label,.dds__label,.dds__form__label');return String((l&&(l.innerText||l.textContent))||el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||'').replace(/\s+/g,' ').trim();}
  function css(el){if(el.id)return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;const p=[];let n=el;while(n&&n.nodeType===1&&p.length<8){let x=n.tagName.toLowerCase();const par=n.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===n.tagName);if(same.length>1)x+=`:nth-of-type(${same.indexOf(n)+1})`;}p.unshift(x);n=par;}return p.join(' > ');}
  return els.filter(visible).map((el,index)=>({index,label:lab(el).toLowerCase(),name:el.getAttribute('name')||'',selector:css(el),role:el.getAttribute('role')||'',tag:el.tagName.toLowerCase()}));
}
""")
        if name:
            exact_name=[row for row in rows if row.get("name")==name]
            if exact_name:
                return str(exact_name[min(occurrence,len(exact_name)-1)].get("selector") or "")
        matches=[row for row in rows if row.get("label")==label]
        if not matches:
            matches=[row for row in rows if label and (label in str(row.get("label") or "") or str(row.get("label") or "") in label)]
        if matches:
            return str(matches[min(occurrence,len(matches)-1)].get("selector") or "")
    except Exception:
        return ""
    return ""


async def _set_doctype_control_value(page: Page, control: Dict[str, Any], value: str, *, phase: str) -> bool:
    selector = await _reacquire_doctype_control(page, control)
    if not selector:
        return False
    control["selector"] = selector
    role = str(control.get("role") or "").lower()
    tag = str(control.get("tag") or "").lower()
    ctype = str(control.get("type") or "").lower()
    if role == "combobox" or tag == "select":
        try:
            root = await get_doctype_create_root(page)
            ok = await select_dds_combobox(page, root, selector, str(value), phase=phase)
            await close_open_dropdown(page, phase)
            actual = ""
            loc = page.locator(selector).first
            try:
                actual = str(await loc.input_value(timeout=800) or "")
            except Exception:
                actual = str(await loc.get_attribute("aria-valuetext") or await loc.get_attribute("title") or "")
            return bool(ok and re.sub(r"\s+", " ", actual).strip().lower() == re.sub(r"\s+", " ", str(value)).strip().lower())
        except Exception:
            return False
    ok = await _set_control_value(page, selector, value, kind=ctype or "text")
    if not ok:
        return False
    try:
        loc = page.locator(selector).first
        actual = str(await loc.input_value(timeout=800) or "")
        return re.sub(r"\s+", " ", actual).strip() == re.sub(r"\s+", " ", str(value)).strip()
    except Exception:
        return ok


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


DOCTYPE_URL_HINTS = ["doctype", "doc-type", "doc_type", "document-type", "document_type", "documenttype", "document-types", "document", "securelink"]
DOCTYPE_FIELD_KEYS = {
    "document_type_id": ["documentTypeId", "document_type_id", "docTypeId", "doctypeId", "id"],
    "document_type_name": ["documentTypeName", "document_type_name", "docTypeName", "doctypeName", "name", "documentName"],
    "document_type_version": ["documentTypeVersion", "document_type_version", "docTypeVersion", "version", "latestDevVersion", "latest_dev_version"],
    "status": ["status", "state", "active", "enabled"],
    "transaction_type": ["transactionType", "transaction_type", "transaction", "txCode", "tx_code"],
    "format": ["dataFormatType", "data_format_type", "format", "documentFormat", "document_format", "documentTypeFormat", "type"],
    "validation_type": ["validationType", "validation_type", "validation"],
    "description": ["description", "documentDescription", "document_type_description"],
    "document_identifier": ["documentIdentifier", "document_identifier", "identifier", "documentTypeIdentifier"],
    "root_element": ["rootElement", "root_element", "rootTag", "rootNode", "elementName"],
    "document_version": ["documentVersion", "document_version", "standardVersion", "versionIdentifier"],
    "usage": ["usage", "usages", "documentUsage", "direction"],
    "schema_file": ["schemaFile", "xsdFile", "fileName", "schemaFileName", "documentFile", "file"],
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


def _contains_doctype_hint(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = text.lower()
    return any(h in text for h in DOCTYPE_URL_HINTS)


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
    if any(k in payload for k in ["documentTypeId", "docTypeId", "documentTypeName", "documentIdentifier", "rootElement", "dataFormatType", "transactionType", "validationType"]):
        yield payload
    for key in ["items", "content", "data", "records", "results", "result", "rows", "documentTypes", "documentTypeList", "docTypes", "doctypes", "payload", "documentTypeDetail", "documentTypeDetails", "document_type_detail", "docTypeDetail", "details"]:
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


def extract_doctype_record(row: Dict[str, Any], *, source_url: str = "", source: str = "api") -> Optional[Dict[str, Any]]:
    """Normalize one row into the old Document Type inventory schema.

    Generic `id` is accepted only when another Document-Type-specific field is
    present, preventing unrelated SecureLink rows from being saved as Document Types.
    """
    if not isinstance(row, dict):
        return None
    values = {field: _first_present(row, keys) for field, keys in DOCTYPE_FIELD_KEYS.items()}
    doc_identifier_parts = _extract_document_identifier_parts(values.get("document_identifier"))
    if doc_identifier_parts.get("document_identifier") and not isinstance(values.get("document_identifier"), str):
        values["document_identifier"] = doc_identifier_parts.get("document_identifier")
    has_doctype_specific = any(values.get(k) not in (None, "") for k in [
        "document_type_name", "document_identifier", "root_element", "document_version", "schema_file", "transaction_type", "format", "validation_type"
    ])
    if not has_doctype_specific and not _contains_doctype_hint(source_url):
        return None
    if values.get("document_type_id") in (None, "") and not has_doctype_specific:
        return None
    normalized = {
        "document_type_id": str(values.get("document_type_id") or ""),
        "document_type_name": str(values.get("document_type_name") or ""),
        "document_type_version": str(values.get("document_type_version") or ""),
        "status": str(values.get("status") or ""),
        "transaction_type": str(values.get("transaction_type") or ""),
        "format": str(values.get("format") or ""),
        "validation_type": str(values.get("validation_type") or ""),
        "description": str(values.get("description") or ""),
        "document_identifier": str(values.get("document_identifier") or ""),
        "document_identifier_operation": doc_identifier_parts.get("document_identifier_operation", ""),
        "document_identifier_derived_from": doc_identifier_parts.get("document_identifier_derived_from", ""),
        "root_element": str(values.get("root_element") or ""),
        "document_version": str(values.get("document_version") or ""),
        "usage": str(values.get("usage") or ""),
        "schema_file": str(values.get("schema_file") or ""),
        "created_by": str(values.get("created_by") or ""),
        "updated_by": str(values.get("updated_by") or ""),
        "created_at": str(values.get("created_at") or ""),
        "updated_at": str(values.get("updated_at") or ""),
        "available_environments": values.get("available_environments") or "",
        "latest_dev_version": str(_first_present(row, ["latestDevVersion", "latest_dev_version"]) or values.get("document_type_version") or ""),
        "source": source,
        "source_url": mask_sensitive_string(source_url),
        "raw_row_compact": mask_sensitive_data(row),
    }
    # Do not save completely empty records.
    if not any(normalized.get(k) for k in ["document_type_id", "document_type_name", "transaction_type", "format", "validation_type", "document_identifier", "root_element", "schema_file"]):
        return None
    return normalized

def extract_doctype_records_from_payload(payload: Any, *, source_url: str = "", source: str = "api") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in _iter_json_candidate_rows(payload):
        rec = extract_doctype_record(row, source_url=source_url, source=source)
        if rec:
            rows.append(rec)
    return _dedupe_doctype_records(rows)


DEEP_PROFILE_LIST_KEYS = {
    "attributes", "documentattributes", "documenttypeattributes", "attributedetails",
    "attributes_to_configure", "documentattribute", "identifiers", "documentidentifiers",
    "documentidentifierrows", "documentidentifierdetails", "rows",
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
    # Do not mistake document-identifier rows like {derivedFrom, value} for attributes.
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
    raw = _recursive_find_key(obj, ["documentIdentifier", "document_identifier", "documentIdentifiers", "documentIdentifierDetails", "identifierRows", "rows"])
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
                    "value": _scalar_text(_first_present(item, ["value", "rootElement", "root_element", "identifier", "documentIdentifier"])),
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
    """Return (document-type-detail-object, wrapper) pairs from API responses."""
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    if isinstance(payload, dict):
        for key in ["documentTypeDetail", "documentTypeDetails", "document_type_detail", "docTypeDetail", "details", "documentType", "data", "payload", "result"]:
            v = payload.get(key)
            if isinstance(v, dict):
                out.append((v, payload))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        out.append((item, payload))
        if any(k in payload for k in ["documentTypeId", "documentTypeName", "documentIdentifier", "rootElement", "dataFormatType", "transactionType", "validationType"]):
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
        "document_type_id", "document_type_name", "document_type_version", "transaction_type", "format",
        "validation_type", "description", "document_identifier", "root_element", "document_version", "schema_file",
        "document_identifier_operation", "document_identifier_derived_from",
    ]
    score = sum(1 for k in keys if profile.get(k) not in (None, "", [], {}))
    score += min(12, int(profile.get("attribute_count") or 0) * 2)
    score += min(6, len(profile.get("document_identifier_rows") or []) * 2)
    return score


def normalize_doctype_deep_profile(payload: Any, *, source_url: str = "", source: str = "api_deep_profile", base_row: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Normalize the full `/api/document-type/{id}/details` style payload.

    The details endpoint observed in the live run returns a wrapper with
    `documentTypeDetail`, `flowDetail`, `ruleDetail`, and `tpDetail`.  Previous
    builds only used that response to infer IDs; this parser preserves the actual
    configuration profile for the Document Type.
    """
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for detail, wrapper in _detail_payload_candidates(payload):
        if not isinstance(detail, dict):
            continue
        # If the wrapper has documentTypeDetail, use that as the primary object but
        # keep the wrapper to capture related flow/rule/transport usage evidence.
        values = {field: (_first_present(detail, keys) or _recursive_find_key(detail, keys, max_depth=4)) for field, keys in DOCTYPE_FIELD_KEYS.items()}
        if not values.get("document_type_id"):
            values["document_type_id"] = _extract_numeric_document_type_id_from_url_or_payload(source_url)
        if base_row:
            for k in ["document_type_id", "document_type_name", "document_type_version", "status", "transaction_type", "format", "validation_type", "available_environments", "latest_dev_version"]:
                if values.get(k) in (None, "", [], {}) and base_row.get(k) not in (None, "", [], {}):
                    values[k] = base_row.get(k)
        identifier_rows = _normalize_identifier_rows_from_detail(detail)
        doc_identifier_parts = _extract_document_identifier_parts(values.get("document_identifier"))
        if identifier_rows:
            if not doc_identifier_parts.get("document_identifier"):
                doc_identifier_parts["document_identifier"] = identifier_rows[0].get("value", "")
            if not doc_identifier_parts.get("document_identifier_derived_from"):
                doc_identifier_parts["document_identifier_derived_from"] = identifier_rows[0].get("derived_from", "")
        operation = _recursive_find_key(detail, ["operation", "operator", "documentIdentifierOperation", "conditionOperation", "document_identifier_operation"], max_depth=5)
        if operation not in (None, ""):
            doc_identifier_parts["document_identifier_operation"] = _scalar_text(operation)
        attrs = _normalize_attributes_from_detail(detail)
        related = {
            "flowDetail": _compact_related_detail(wrapper.get("flowDetail") if isinstance(wrapper, dict) else None),
            "ruleDetail": _compact_related_detail(wrapper.get("ruleDetail") if isinstance(wrapper, dict) else None),
            "tpDetail": _compact_related_detail(wrapper.get("tpDetail") if isinstance(wrapper, dict) else None),
        }
        profile = mask_sensitive_data({
            "document_type_id": _scalar_text(values.get("document_type_id")),
            "document_type_name": _scalar_text(values.get("document_type_name")),
            "document_type_version": _scalar_text(values.get("document_type_version")),
            "status": _scalar_text(values.get("status")),
            "transaction_type": _scalar_text(values.get("transaction_type")),
            "format": _scalar_text(values.get("format")),
            "validation_type": _scalar_text(values.get("validation_type")),
            "description": _scalar_text(values.get("description")),
            "document_identifier": _scalar_text(doc_identifier_parts.get("document_identifier") or values.get("document_identifier")),
            "document_identifier_operation": _scalar_text(doc_identifier_parts.get("document_identifier_operation")),
            "document_identifier_derived_from": _scalar_text(doc_identifier_parts.get("document_identifier_derived_from")),
            "document_identifier_rows": identifier_rows,
            "root_element": _scalar_text(values.get("root_element") or _recursive_find_key(detail, ["rootElement", "root_element", "rootTag"], max_depth=5)),
            "document_version": _scalar_text(values.get("document_version")),
            "usage": _normalize_usage(values.get("usage")),
            "schema_file": _scalar_text(values.get("schema_file")),
            "created_by": _scalar_text(values.get("created_by")),
            "updated_by": _scalar_text(values.get("updated_by")),
            "created_at": _scalar_text(values.get("created_at")),
            "updated_at": _scalar_text(values.get("updated_at")),
            "available_environments": values.get("available_environments") or (base_row or {}).get("available_environments", ""),
            "latest_dev_version": _scalar_text(_first_present(detail, ["latestDevVersion", "latest_dev_version"]) or (base_row or {}).get("latest_dev_version") or values.get("document_type_version")),
            "attributes": attrs,
            "attribute_count": len(attrs),
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


def extract_doctype_deep_profiles_from_payload(payload: Any, *, source_url: str = "", source: str = "api_deep_profile", base_row: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for detail, wrapper in _detail_payload_candidates(payload):
        payload_for_one = {"documentTypeDetail": detail}
        if isinstance(wrapper, dict):
            for key in ["flowDetail", "ruleDetail", "tpDetail"]:
                if key in wrapper:
                    payload_for_one[key] = wrapper[key]
        profile = normalize_doctype_deep_profile(payload_for_one, source_url=source_url, source=source, base_row=base_row)
        if profile:
            profiles.append(profile)
    # Also try the payload as-is in case it was already a single detail object.
    if not profiles:
        profile = normalize_doctype_deep_profile(payload, source_url=source_url, source=source, base_row=base_row)
        if profile:
            profiles.append(profile)
    seen = set()
    out = []
    for profile in profiles:
        key = (profile.get("document_type_id"), profile.get("document_type_name"), profile.get("document_type_version"), profile.get("source_url"))
        if key in seen:
            continue
        seen.add(key)
        out.append(profile)
    return out


def _record_matches_deep_profile(profile: Dict[str, Any], row: Dict[str, Any]) -> bool:
    for key in ["document_type_id", "document_type_name", "document_identifier", "root_element"]:
        a = str(profile.get(key) or "").strip().lower()
        b = str(row.get(key) or "").strip().lower()
        if a and b and a == b:
            return True
    return False


def _apply_deep_profile_to_record(row: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(row)
    for key in [
        "document_type_id", "document_type_name", "document_type_version", "status", "transaction_type", "format",
        "validation_type", "description", "document_identifier", "document_identifier_operation",
        "document_identifier_derived_from", "root_element", "document_version", "usage", "schema_file",
        "created_by", "updated_by", "created_at", "updated_at", "available_environments", "latest_dev_version",
    ]:
        if profile.get(key) not in (None, "", [], {}) and not merged.get(key):
            merged[key] = profile.get(key)
    if profile.get("document_type_id") and not merged.get("document_type_id"):
        merged["document_type_id_source"] = "deep_profile_api"
    merged["deep_profile_status"] = "captured" if _deep_profile_score(profile) >= 4 else "partial"
    merged["deep_profile_source_url"] = profile.get("source_url", "")
    merged["deep_profile_completeness_score"] = profile.get("deep_profile_completeness_score", _deep_profile_score(profile))
    merged["document_identifier_rows"] = profile.get("document_identifier_rows") or []
    merged["attributes"] = profile.get("attributes") or []
    merged["attribute_count"] = len(merged.get("attributes") or [])
    merged["related_usage"] = profile.get("related_usage") or {}
    merged["deep_profile"] = profile
    return merged


def _deep_profile_report(rows: List[Dict[str, Any]], attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    captured = sum(1 for r in rows if r.get("deep_profile_status") == "captured")
    partial = sum(1 for r in rows if r.get("deep_profile_status") == "partial")
    with_attrs = sum(1 for r in rows if int(r.get("attribute_count") or 0) > 0)
    with_identifier_rows = sum(1 for r in rows if r.get("document_identifier_rows"))
    with_schema = sum(1 for r in rows if r.get("schema_file"))
    return {
        "total_document_types": total,
        "deep_profiles_captured": captured,
        "deep_profiles_partial": partial,
        "deep_profiles_missing": max(0, total - captured - partial),
        "with_attributes": with_attrs,
        "with_document_identifier_rows": with_identifier_rows,
        "with_schema_file": with_schema,
        "attempts": len(attempts),
        "capture_percent": round(((captured + partial) / total * 100), 2) if total else 0.0,
        "note": "Deep profile capture parses `/api/document-type/{id}/details` payloads plus any row action network payloads. Missing means the portal/API did not expose the full profile during this run.",
    }


def _candidate_doctype_deep_profile_urls(row: Dict[str, Any], interactions: List[Dict[str, Any]]) -> List[str]:
    urls: List[str] = []
    doc_id = str(row.get("document_type_id") or "").strip()
    name = str(row.get("document_type_name") or "").strip()
    version = str(row.get("document_type_version") or row.get("latest_dev_version") or "").strip()
    env = _first_environment(row.get("available_environments"), "DEV")

    def add(url: str) -> None:
        if url and url not in urls:
            urls.append(url)

    if doc_id:
        for base in [
            "https://developer.dell.com/inaas-gateway/hipService-svc/api/document-type",
            "/inaas-gateway/hipService-svc/api/document-type",
        ]:
            prefix = "https://developer.dell.com" + base if base.startswith("/") else base
            add(f"{prefix}/{quote(doc_id, safe='')}/details")
            add(f"{prefix}/{quote(doc_id, safe='')}/detail")
            add(f"{prefix}/details/{quote(doc_id, safe='')}")
            add(f"{prefix}/details?documentTypeId={quote(doc_id, safe='')}")
    if name:
        for base in ["https://developer.dell.com/inaas-gateway/hipService-svc/api/document-type/details"]:
            q = {"documentTypeName": name, "environment": env}
            if version:
                q["version"] = version
                q["documentTypeVersion"] = version
            add(base + "?" + urlencode(q))
    # Reuse any observed detail endpoint shape from the session.
    for inter in interactions or []:
        u = str(inter.get("url") or "")
        if "/document-type/" not in u or "details" not in u:
            continue
        parsed = urlparse(u)
        if doc_id and re.search(r"/document-type/\d+/details", parsed.path):
            add(re.sub(r"(/document-type/)\d+(/details?)", rf"\g<1>{quote(doc_id, safe='')}\2", urlunparse(parsed._replace(query=""))))
    return urls[:10]


async def _capture_doctype_deep_profiles(
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
    """Capture full Document Type profiles for every learned row.

    This is a read-only pass. It first parses any detail payloads already observed
    during UI row-action learning, then calls the discovered `/details` endpoint by
    numeric documentTypeId for rows that still lack a deep profile.
    """
    rows = _merge_doctype_records(records)
    limit = min(len(rows), int(max_profiles)) if max_profiles is not None else len(rows)
    target_rows = rows[:limit]
    attempts: List[Dict[str, Any]] = []
    headers: Dict[str, Any] = {}
    for inter in interactions or []:
        for hk, hv in (inter.get("request_headers_compact") or {}).items():
            if str(hk).lower() in {"x-requester-id", "referer", "origin"} and hv:
                headers[hk] = hv
    headers.setdefault("Referer", DOCTYPES_URL)

    # Use already captured network bodies first. This avoids duplicate calls when the row-action phase already opened details.
    event_profiles: List[Dict[str, Any]] = []
    for ev in browser.network_tab_events:
        d = _event_dict(ev)
        url = str(d.get("url") or "")
        if "/document-type/" not in url and "document-type/details" not in url:
            continue
        payload = d.get("response_body_redacted") or _safe_json_load(d.get("response_body_text_redacted"))
        if payload is None:
            continue
        event_profiles.extend(extract_doctype_deep_profiles_from_payload(payload, source_url=url, source="network_deep_profile_reuse"))

    for idx, row in enumerate(target_rows, start=1):
        label = row.get("document_type_name") or row.get("document_type_id") or f"row {idx}"
        if progress_cb:
            maybe = progress_cb(
                phase="doctype_deep_profile_enrichment",
                completed=idx - 1,
                total=max(1, limit),
                detail=f"deep profile {idx}/{limit}: {str(label)[:80]}",
                counts={"deep_profiles_captured": sum(1 for r in rows if r.get("deep_profile_status") in {"captured", "partial"})},
            )
            if asyncio.iscoroutine(maybe):
                await maybe
        # Match already observed detail bodies.
        best = None
        for profile in event_profiles:
            if _record_matches_deep_profile(profile, row):
                if best is None or _deep_profile_score(profile) > _deep_profile_score(best):
                    best = profile
        row_attempt = {
            "document_type_id": row.get("document_type_id"),
            "document_type_name": row.get("document_type_name"),
            "used_existing_network_profile": bool(best),
            "attempts": [],
            "resolved": False,
        }
        if best:
            row.update(_apply_deep_profile_to_record(row, best))
            row_attempt["resolved"] = True
            row_attempt["source"] = best.get("source_url")
        if not row_attempt["resolved"]:
            for url in _candidate_doctype_deep_profile_urls(row, interactions):
                body, meta = await _fetch_json_with_page(page, url, headers=headers, timeout_ms=timeout_ms)
                profiles = extract_doctype_deep_profiles_from_payload(body, source_url=url, source="direct_deep_profile_api", base_row=row) if body is not None else []
                matched = None
                for profile in profiles:
                    if _record_matches_deep_profile(profile, row):
                        matched = profile
                        break
                if matched is None and len(profiles) == 1:
                    matched = profiles[0]
                row_attempt["attempts"].append(mask_sensitive_data({**meta, "profiles_extracted": len(profiles), "matched": bool(matched)}))
                if matched:
                    row.update(_apply_deep_profile_to_record(row, matched))
                    row_attempt["resolved"] = True
                    row_attempt["source"] = matched.get("source_url")
                    break
        attempts.append(row_attempt)
        if kb_dir and (idx % 10 == 0 or idx == limit):
            try:
                _write_json(kb_dir / "old_doctypes_deep_profiles.checkpoint.json", rows)
                _write_json(kb_dir / "doctype_deep_profile_enrichment_audit.checkpoint.json", attempts)
                _write_json(kb_dir / "doctype_deep_profile_report.checkpoint.json", _deep_profile_report(rows, attempts))
            except Exception:
                pass
    if progress_cb:
        maybe = progress_cb(
            phase="doctype_deep_profile_enrichment_done",
            completed=limit,
            total=max(1, limit),
            detail="Deep profile capture complete",
            counts=_deep_profile_report(rows, attempts),
        )
        if asyncio.iscoroutine(maybe):
            await maybe
    return _merge_doctype_records(rows), attempts, _deep_profile_report(rows, attempts)


def _dedupe_doctype_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = str(row.get("document_type_id") or "").strip()
        if not key:
            key = "|".join(str(row.get(k) or "").strip().lower() for k in ["document_type_name", "document_type_version", "document_identifier", "root_element"])
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
    if hasattr(event, "model_dump"):
        return event.model_dump()
    if hasattr(event, "dict"):
        return event.dict()
    return dict(getattr(event, "__dict__", {}) or {})


def collect_doctype_api_interactions(events: Iterable[Any], *, stage_label: str = "unknown") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extract compact API learning evidence and old Document Type rows from captured network events."""
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
        is_relevant = _contains_doctype_hint(url) or _contains_doctype_hint(payload) or _contains_doctype_hint(request_body)
        if not is_relevant:
            continue
        rows = extract_doctype_records_from_payload(payload, source_url=url, source=f"network:{stage_label}") if payload is not None else []
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
            "doctype_rows_extracted": len(rows),
            "sample_doctype_rows": rows[:3],
        }))
    return interactions, _dedupe_doctype_records(inventory)


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
        key = "|".join(str(item.get(k) or "") for k in ["stage", "method", "url", "status", "doctype_rows_extracted"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _build_doctype_lookup(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    lookup: Dict[str, Any] = {"by_document_type_id": {}, "by_document_type_name": {}, "by_document_identifier": {}, "by_root_element": {}}
    for row in records:
        compact = {k: row.get(k, "") for k in ["document_type_id", "document_type_name", "document_type_version", "latest_dev_version", "status", "transaction_type", "format", "validation_type", "document_identifier", "root_element", "document_version", "schema_file", "available_environments", "source_url"]}
        for field, bucket in [("document_type_id", "by_document_type_id"), ("document_type_name", "by_document_type_name"), ("document_identifier", "by_document_identifier"), ("root_element", "by_root_element")]:
            val = str(row.get(field) or "").strip()
            if val:
                lookup[bucket].setdefault(val, []).append(compact)
    return lookup


async def _collect_ui_doctype_rows(page: Page) -> List[Dict[str, Any]]:
    """Fallback: collect visible list/table/card text from the Document Types page."""
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
      if (!/(document\s*type|doctype|transaction|format|validation|identifier|root|version|status|environment|data\s*format)/i.test(text)) continue;
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


async def _wait_for_doctype_listing_ready(page: Page, browser: BrowserSession, doctypes_url: str, warnings: List[str], *, attempts: int = 4) -> bool:
    """Wait until the Document Types SPA has loaded enough to expose rows, Add, or the Document Type API.

    This specifically protects against a live Dell behavior where SSO lands on the
    correct URL, then an immediate second navigation aborts Angular chunks and
    leaves a blank page.  We only reload when the page is blank and no Document Type
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
        interactions, rows = collect_doctype_api_interactions(browser.network_tab_events, stage_label=f"readiness_attempt_{attempt}")
        if rows:
            return True
        try:
            ui_rows = await _collect_ui_doctype_rows(page)
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
        if len(body_text) > 80 and re.search(r"document\s*type|doc\s*type|doctype|root\s*element|securelink", body_text, re.I):
            return True
        # Recover from a blank shell or aborted remoteEntry/chunk load.
        if attempt < attempts:
            browser.set_stage(f"doctype_kb_retry_blank_listing_{attempt}")
            try:
                if _urls_same_path(page.url, doctypes_url):
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                else:
                    await page.goto(doctypes_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                warnings.append(f"Document Type listing retry {attempt} navigation/reload failed: {exc}")
    warnings.append("Document Type listing page did not expose rows/Add/API after retries; using direct summary API fallback if authenticated.")
    return False


async def _direct_fetch_doctype_summary(page: Page, *, max_pages: int = 25) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch the known read-only Document Type summary API when the SPA/network capture misses it."""
    urls: List[str] = [
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/document-type/summary",
        "/inaas-gateway/hipService-svc/api/document-type/summary",
        "/inaas-gateway/hipService-svc/api/document-types/summary",
        "/inaas-gateway/hipService-svc/api/doc-type/summary",
        "/inaas-gateway/hipService-svc/api/doctypes/summary",
        "/inaas-gateway/hipService-svc/api/documentType/summary",
    ]
    # Common pagination shapes; the bare endpoint currently returns all rows in DEV,
    # but keep these as fallback for future backend changes.
    for page_no in range(max(0, min(max_pages, 250))):
        urls.append(f"/inaas-gateway/hipService-svc/api/document-type/summary?page={page_no}&size=100")
        urls.append(f"/inaas-gateway/hipService-svc/api/document-type/summary?pageIndex={page_no}&pageSize=100")
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
        body, meta = await _fetch_json_with_page(page, url, headers={"Referer": DOCTYPES_URL})
        page_rows = extract_doctype_records_from_payload(body, source_url=url, source="direct_summary_api_fallback") if body is not None else []
        meta = mask_sensitive_data({**meta, "source": "direct_summary_api_fallback", "doctype_rows_extracted": len(page_rows)})
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
            "request_headers_compact": {"Referer": DOCTYPES_URL, "Accept": "application/json, text/plain, */*"},
            "request_body_redacted": "",
            "response_shape": _compact_payload_shape(body),
            "doctype_rows_extracted": len(page_rows),
            "sample_doctype_rows": page_rows[:3],
        }))
        if page_rows:
            rows.extend(page_rows)
            # The bare endpoint returning rows is enough; paginated calls can be redundant.
            if "?" not in url:
                break
        # Stop paginated replay when a paginated URL returns no rows after we already found data.
        if "?" in url and not page_rows and rows:
            break
    return _dedupe_doctype_records(rows), interactions, audit


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


async def _crawl_old_doctype_inventory_from_apis(page: Page, interactions: List[Dict[str, Any]], *, max_pages: int = 20) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
                page_rows = extract_doctype_records_from_payload(body, source_url=url, source="api_pagination_replay")
                rows.extend(page_rows)
                if not page_rows and meta.get("row_count") == 0:
                    # Likely exhausted.
                    break
    return _dedupe_doctype_records(rows), audit



def _merge_doctype_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge duplicate Document Type rows while preferring records that include numeric documentTypeId/details."""
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("document_type_name") or "").strip().lower()
        version = str(row.get("document_type_version") or row.get("latest_dev_version") or "").strip().lower()
        identifier = str(row.get("document_identifier") or "").strip().lower()
        root_element = str(row.get("root_element") or "").strip().lower()
        fmt = str(row.get("format") or "").strip().lower()
        transaction_type = str(row.get("transaction_type") or "").strip().lower()
        key = "|".join([name, version, identifier, root_element, fmt, transaction_type]).strip("|")
        if not key:
            key = str(row.get("document_type_id") or "").strip().lower()
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
                elif k == "raw_row_compact" and isinstance(existing.get(k), dict) and isinstance(v, dict):
                    existing[k] = {**existing[k], **v}
        # Prefer detail-enriched source marker when present, but keep original source_url if detail lacks one.
        if "detail" in str(row.get("source") or "") and row.get("source"):
            existing["source"] = row.get("source")
            existing["detail_source_url"] = row.get("source_url") or row.get("detail_source_url", "")
    return [merged[k] for k in order]

def _known_document_type_id_by_identifier(previous_values: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    dm = (previous_values or {}).get("document_type_values_to_fill") or {}
    known = (previous_values or {}).get("known_ids") or {}
    mid = known.get("document_type_id")
    ident = dm.get("document_type_name")
    if ident and mid and str(mid).upper() not in {"UNKNOWN", "UNKNOWN_FROM_CURRENT_DOCTYPE_KB_RUN"}:
        out[str(ident).strip().lower()] = str(mid)
    return out


def _apply_known_document_type_ids(records: List[Dict[str, Any]], previous_values: Dict[str, Any]) -> List[Dict[str, Any]]:
    known = _known_document_type_id_by_identifier(previous_values)
    patched = []
    for row in records:
        item = dict(row)
        ident = str(item.get("document_type_name") or "").strip().lower()
        if ident in known and not item.get("document_type_id"):
            item["document_type_id"] = known[ident]
            item["document_type_id_source"] = "known_prior_context"
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
        if "/api/" in path and any(x in path.lower() for x in ["document-type", "documenttype", "doc-type", "doctype"]):
            base_path = re.split(r"/(summary|detail|details|lookup|versions?|edit|view)(?:/|$)", path, maxsplit=1)[0]
            bases.append(urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", "")))
        if "/api/" in path and any(x in path.lower() for x in ["document", "doctype", "doc-type", "securelink"]):
            # Keep the observed collection endpoint too; some APIs accept identifier filters on the summary route.
            bases.append(urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")))
    bases.extend(["https://developer.dell.com/inaas-gateway/hipService-svc/api/document-type", "https://developer.dell.com/inaas-gateway/hipService-svc/api/doc-type", "https://developer.dell.com/inaas-gateway/hipService-svc/api/doctypes"])
    out: List[str] = []
    seen: set[str] = set()
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _detail_query_params(row: Dict[str, Any]) -> Dict[str, str]:
    ident = str(row.get("document_type_name") or "").strip()
    version = str(row.get("document_type_version") or row.get("latest_dev_version") or "").strip()
    env = _first_environment(row.get("available_environments"), "DEV")
    params = {"documentTypeName": ident, "documentIdentifier": str(row.get("document_identifier") or ""), "environment": env}
    if version:
        params["version"] = version
        params["documentTypeVersion"] = version
    return {k: v for k, v in params.items() if v}


def _candidate_doctype_detail_urls(row: Dict[str, Any], interactions: List[Dict[str, Any]]) -> List[str]:
    ident = str(row.get("document_type_name") or "").strip()
    if not ident:
        return []
    document_type_id = str(row.get("document_type_id") or "").strip()
    version = str(row.get("document_type_version") or row.get("latest_dev_version") or "").strip()
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
        add("", params)
        add("/summary", params)
        add("/detail", params)
        add("/details", params)
        add("/lookup", params)
        add("/versions", {"documentTypeName": ident})
        add("/version", {"documentTypeName": ident})
        add("/" + quote(ident, safe=""))
        if version:
            add("/" + quote(ident, safe="") + "/" + quote(version, safe=""))
        if document_type_id:
            add("/" + quote(document_type_id, safe=""))
            add("/detail/" + quote(document_type_id, safe=""))
            add("/details/" + quote(document_type_id, safe=""))
    # Dedupe preserving order.
    out: List[str] = []
    seen: set[str] = set()
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out[:18]


def _record_matches_doctype(row: Dict[str, Any], target: Dict[str, Any]) -> bool:
    ident = str(target.get("document_type_name") or "").strip().lower()
    name = str(target.get("document_identifier") or "").strip().lower()
    klass = str(target.get("root_element") or "").strip().lower()
    if ident and str(row.get("document_type_name") or "").strip().lower() == ident:
        return True
    if name and str(row.get("document_identifier") or "").strip().lower() == name:
        return True
    if klass and str(row.get("root_element") or "").strip().lower() == klass:
        return True
    return False


async def _enrich_old_doctypes_with_detail_apis(
    page: Page,
    records: List[Dict[str, Any]],
    interactions: List[Dict[str, Any]],
    previous_values: Dict[str, Any],
    *,
    max_details: int = 250,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
    timeout_ms: int = 5000,
    max_candidate_urls_per_doctype: int = 4,
    probe_rows: int = 15,
    stop_if_probe_finds_no_ids: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Best-effort read-only detail enrichment to discover numeric documentTypeIds.

    Bounded by design. The previous implementation could try many speculative
    URL shapes for every Document Type, which can look like a hang when the Dell gateway
    slowly returns 404/500/timeout. This version writes checkpoints and stops
    the expensive detail phase when the first probe batch proves that the
    visible/listing APIs do not expose numeric documentTypeId through read-only detail
    endpoints. It still preserves every old Document Type row from the listing.
    """
    enriched: List[Dict[str, Any]] = _apply_known_document_type_ids(records, previous_values)
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
    by_key = _merge_doctype_records(enriched)
    limit = max(0, int(max_details or 0))
    candidates_rows = by_key[:limit]
    baseline_ids = sum(1 for r in by_key if r.get("document_type_id"))

    async def emit_progress(detail: str) -> None:
        if progress_cb:
            maybe = progress_cb(
                phase="doctype_detail_id_enrichment",
                completed=processed,
                total=len(candidates_rows),
                detail=detail,
                counts={
                    "old_doctypes": len(by_key),
                    "document_type_ids_found": sum(1 for r in by_key if r.get("document_type_id")),
                    "detail_found_this_run": detail_found,
                },
            )
            if asyncio.iscoroutine(maybe):
                await maybe

    for row in candidates_rows:
        processed += 1
        await emit_progress(f"checking {processed}/{len(candidates_rows)} {row.get('document_type_name') or row.get('document_identifier') or ''}")
        if row.get("document_type_id"):
            continue
        # Probe mode: once enough rows have been tested and no detail IDs were found,
        # stop wasting time on the remaining 200+ Document Types. This keeps the run moving
        # and records the limitation clearly in doctype_id_completion_report.json.
        if stop_if_probe_finds_no_ids and processed > max(1, int(probe_rows or 0)) and detail_found == 0:
            skipped_after_probe = max(0, len(candidates_rows) - processed + 1)
            stop_reason = (
                f"Stopped detail enrichment after probe_rows={probe_rows}: no numeric documentTypeId was exposed by "
                "the attempted read-only detail endpoints. Listing inventory was kept; only numeric IDs remain blank."
            )
            break
        candidates = _candidate_doctype_detail_urls(row, interactions)[: max(1, int(max_candidate_urls_per_doctype or 1))]
        row_audit = {
            "document_type_name": row.get("document_type_name"),
            "document_identifier": row.get("document_identifier"),
            "attempts": [],
            "resolved": False,
            "candidate_url_count_used": len(candidates),
        }
        for url in candidates:
            body, meta = await _fetch_json_with_page(page, url, headers=headers, timeout_ms=timeout_ms)
            meta = mask_sensitive_data({**meta, "source": "doctype_detail_id_enrichment"})
            recs = extract_doctype_records_from_payload(body, source_url=url, source="api_detail_enrichment") if body is not None else []
            matched = None
            for rec in recs:
                if _record_matches_doctype(rec, row):
                    matched = rec
                    break
            if matched is None and len(recs) == 1:
                matched = recs[0]
            meta["doctype_rows_extracted"] = len(recs)
            meta["matched"] = bool(matched)
            meta["document_type_id_found"] = bool(matched and matched.get("document_type_id"))
            row_audit["attempts"].append(meta)
            if matched:
                merged = _merge_doctype_records([row, matched])[0]
                row.update(merged)
                if matched.get("document_type_id"):
                    row["document_type_id_source"] = "api_detail_enrichment"
                    row_audit["resolved"] = True
                    detail_found += 1
                    break
        audit.append(row_audit)
        # Checkpoint every five rows so a stopped run still has useful output.
        if kb_dir and (processed % 5 == 0 or processed == len(candidates_rows)):
            try:
                _write_json(kb_dir / "old_doctypes_inventory_with_ids.checkpoint.json", by_key)
                _write_json(kb_dir / "doctype_detail_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    merged_records = _merge_doctype_records(by_key)
    with_ids = sum(1 for r in merged_records if r.get("document_type_id"))
    report = {
        "total_old_doctypes": len(merged_records),
        "document_type_ids_found": with_ids,
        "document_type_ids_missing": max(0, len(merged_records) - with_ids),
        "detail_enrichment_processed": processed,
        "detail_enrichment_found_ids": detail_found,
        "known_prior_ids_applied": sum(1 for r in merged_records if r.get("document_type_id_source") == "known_prior_context"),
        "baseline_ids_before_detail_enrichment": baseline_ids,
        "detail_timeout_ms": timeout_ms,
        "max_candidate_urls_per_doctype": max_candidate_urls_per_doctype,
        "probe_rows": probe_rows,
        "skipped_after_probe": skipped_after_probe,
        "stop_reason": stop_reason,
        "completion_percent": round((with_ids / len(merged_records) * 100), 2) if merged_records else 0.0,
        "note": "Blank document_type_id means the visible/listing API did not expose numeric documentTypeId and no read-only detail endpoint returned it during this run.",
    }
    if kb_dir:
        try:
            _write_json(kb_dir / "old_doctypes_inventory_with_ids.checkpoint.json", merged_records)
            _write_json(kb_dir / "doctype_detail_enrichment_audit.checkpoint.json", audit)
            _write_json(kb_dir / "doctype_id_completion_report.checkpoint.json", report)
        except Exception:
            pass
    return merged_records, audit, report



def _extract_numeric_document_type_id_from_url_or_payload(value: Any) -> str:
    """Extract a plausible numeric Document Type id from a URL/payload.

    We intentionally only trust numeric ids when the surrounding text has a document-type/doctype
    hint. This prevents unrelated page ids or timestamps from becoming documentTypeId.
    """
    if value in (None, ""):
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    low = text.lower()
    if not any(h in low for h in ["document-type", "documenttype", "doc-type", "doctype", "documenttypeid", "document_type_id", "doctypeid"]):
        return ""
    patterns = [
        r"(?:documentTypeId|document_type_id|docTypeId|doctypeId)[\"'=:\s]+(\d{1,10})",
        r"/(?:document-type|documentType|doc-type|doctypes?)/(?:detail/|details/|edit/|view/)?(\d{1,10})(?:[/?#]|$)",
        r"[?&](?:documentTypeId|docTypeId|doctypeId|id)=(\d{1,10})(?:&|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


async def _find_doctype_listing_search(page: Page) -> Optional[Locator]:
    """Find the visible Document Types list search box, avoiding Filter buttons."""
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


async def _set_doctype_listing_search(page: Page, value: str) -> bool:
    loc = await _find_doctype_listing_search(page)
    if loc is None:
        return False
    if semantic_runtime_enabled(page):
        try:
            selector = await loc.evaluate("el => el.id ? ('#' + CSS.escape(el.id)) : ''")
            if not selector:
                return False
            if not await dds_set_text_control(page, None, selector, value, phase="document_type"):
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


async def _clear_doctype_listing_search(page: Page) -> None:
    loc = await _find_doctype_listing_search(page)
    if loc is None:
        return
    if semantic_runtime_enabled(page):
        try:
            selector = await loc.evaluate("el => el.id ? ('#' + CSS.escape(el.id)) : ''")
            if not selector:
                return
            if not await dds_set_text_control(page, None, selector, "", phase="document_type"):
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


async def _click_safe_doctype_row_action(page: Page, row_hint: str) -> Dict[str, Any]:
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
  const h = String(hint || '').toLowerCase();
  const rowSels = ['table tbody tr','[role=row]','.dds__data-table__row','.dds__table tr','.dds__card','.customCard','[class*=row]'];
  for (const sel of rowSels) {
    for (const row of Array.from(document.querySelectorAll(sel))) {
      const r = row.getBoundingClientRect ? row.getBoundingClientRect() : {width:0,height:0};
      const text = (row.innerText || row.textContent || '').trim().replace(/\s+/g,' ');
      if (!text || !r.width || !r.height || !text.toLowerCase().includes(h)) continue;
      const buttons = Array.from(row.querySelectorAll('button,a,[role=button]')).map((el, idx) => ({
        index:idx, selector:cssPath(el), text:(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim().replace(/\s+/g,' '),
        classes:(el.className||'').toString(), href:el.href || ''
      }));
      return {rowSelector: cssPath(row), rowText: text.slice(0,700), buttons};
    }
  }
  return null;
}
"""
    info = await page.evaluate(js_find_row, {"hint": row_hint})
    if not info:
        return {"clicked": False, "reason": "row_not_found", "row_hint": row_hint}
    buttons = info.get("buttons") or []
    # Treat DDS table row expansion as a safe read-only action.
    # In the live Document Types grid the only row-scoped button may be labelled
    # "Expand the row"; previous builds skipped it, so the real detail area
    # and any Edit/View action revealed after expansion were never inspected.
    safe_words = ["view", "detail", "details", "edit", "open", "show", "expand", "expanded"]
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
    clicked_direct = None
    for b in ordered[:6]:
        if score_button(b) <= 0:
            continue
        try:
            loc = page.locator(b.get("selector")).first
            if await loc.is_visible(timeout=800) and await loc.is_enabled(timeout=800):
                if semantic_runtime_enabled(page):
                    if not await open_control_for_discovery(page, str(b.get("selector") or ""), label="Document Type row action", phase="document_type"):
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

    # If an overflow menu opened, choose a safe read-only/edit action from the menu.
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
            if any(w in t for w in unsafe_words):
                continue
            if any(w in t for w in safe_words):
                loc = page.locator(opt.get("selector")).first
                if await loc.is_visible(timeout=800) and await loc.is_enabled(timeout=800):
                    if semantic_runtime_enabled(page):
                        if not await open_control_for_discovery(page, str(opt.get("selector") or ""), label="Document Type row menu action", phase="document_type"):
                            continue
                    else:
                        await loc.click(timeout=2500)
                    menu_clicked = opt
                    await page.wait_for_timeout(1200)
                    break
    except Exception:
        pass
    expanded_snapshot = None
    try:
        expanded_snapshot = await page.evaluate(r"""
({hint}) => {
  const h = String(hint || '').toLowerCase();
  const candidates = Array.from(document.querySelectorAll('[aria-expanded="true"], .dds__tr, .dds__tbody, dds-table-body-row, [role=row], [class*=expanded], [class*=detail], [class*=accordion]'));
  for (const el of candidates) {
    const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g,' ');
    if (text && text.toLowerCase().includes(h)) return text.slice(0, 1800);
  }
  return '';
}
""", {"hint": row_hint})
    except Exception:
        expanded_snapshot = None
    return {"clicked": True, "row_hint": row_hint, "row": info, "row_action_clicked": clicked_direct, "menu_action_clicked": menu_clicked, "expanded_snapshot": expanded_snapshot, "url_after_click": page.url}


async def _learn_old_doctype_ids_from_ui_row_actions(
    page: Page,
    browser: BrowserSession,
    records: List[Dict[str, Any]],
    previous_values: Dict[str, Any],
    *,
    max_rows: int = 250,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Repeat the manually validated UI approach for old Document Types.

    This mirrors the Partner/System strategy: use the live UI row, click the safe row
    action, capture the API that the UI itself triggers, then parse IDs from URL/body.
    It never clicks Save/Create/Submit/Delete.  It is checkpointed so a stopped run
    still preserves useful IDs/evidence.
    """
    rows = _merge_doctype_records(_apply_known_document_type_ids(records, previous_values))
    limit = min(len(rows), max(0, int(max_rows or 0)))
    audit: List[Dict[str, Any]] = []
    ids_before = sum(1 for r in rows if r.get("document_type_id"))
    found_this_phase = 0

    async def emit(i: int, detail: str) -> None:
        if progress_cb:
            maybe = progress_cb(
                phase="doctype_ui_row_action_id_learning",
                completed=i,
                total=max(1, limit),
                detail=detail,
                counts={"old_doctypes": len(rows), "document_type_ids_found": sum(1 for r in rows if r.get("document_type_id")), "ui_ids_found": found_this_phase},
            )
            if asyncio.iscoroutine(maybe):
                await maybe

    if limit <= 0:
        return rows, audit, {"ui_rows_attempted": 0, "ui_ids_found": 0, "note": "No old Document Type rows available for UI row-action learning."}

    for idx, row in enumerate(rows[:limit], start=1):
        hint = str(row.get("document_type_name") or row.get("document_identifier") or row.get("root_element") or "").strip()
        if not hint:
            continue
        await emit(idx, f"UI row detail learning {idx}/{limit}: {hint[:80]}")
        if row.get("document_type_id") and row.get("document_type_id_source") == "known_prior_context":
            audit.append({"document_type_name": row.get("document_type_name"), "skipped": True, "reason": "already_has_known_document_type_id"})
            continue
        before_count = len(browser.network_tab_events)
        searched = await _set_doctype_listing_search(page, hint)
        click_info: Dict[str, Any] = {"clicked": False, "reason": "search_failed"}
        if searched:
            try:
                click_info = await asyncio.wait_for(_click_safe_doctype_row_action(page, hint), timeout=8)
            except Exception as exc:
                click_info = {"clicked": False, "reason": f"ui_click_error: {exc}", "row_hint": hint}
        await page.wait_for_timeout(900)
        new_events = browser.network_tab_events[before_count:]
        interactions, api_rows = collect_doctype_api_interactions(new_events, stage_label="doctype_ui_row_action_detail")
        matched = None
        for rec in api_rows:
            if _record_matches_doctype(rec, row):
                matched = rec
                break
        if matched is None and len(api_rows) == 1:
            matched = api_rows[0]
        url_document_type_id = _extract_numeric_document_type_id_from_url_or_payload(page.url)
        event_url_document_type_id = ""
        for ev in new_events:
            d = _event_dict(ev)
            event_url_document_type_id = _extract_numeric_document_type_id_from_url_or_payload(d.get("url")) or event_url_document_type_id
            event_url_document_type_id = _extract_numeric_document_type_id_from_url_or_payload(d.get("response_body_redacted") or d.get("response_body_text_redacted")) or event_url_document_type_id
            if event_url_document_type_id:
                break
        row_audit = {
            "document_type_name": row.get("document_type_name"),
            "document_identifier": row.get("document_identifier"),
            "search_used": searched,
            "click": mask_sensitive_data(click_info),
            "network_events_after_click": len(new_events),
            "api_interactions_after_click": len(interactions),
            "api_rows_after_click": len(api_rows),
            "url_after_click": mask_sensitive_string(page.url),
            "url_document_type_id_candidate": url_document_type_id,
            "event_document_type_id_candidate": event_url_document_type_id,
            "resolved": False,
        }
        if matched:
            merged = _merge_doctype_records([row, matched])[0]
            row.update(merged)
        if not row.get("document_type_id"):
            expanded_text = ""
            try:
                expanded_text = str((click_info or {}).get("expanded_snapshot") or "")
            except Exception:
                expanded_text = ""
            expanded_document_type_id = _extract_numeric_document_type_id_from_url_or_payload(expanded_text)
            candidate = event_url_document_type_id or url_document_type_id or expanded_document_type_id
            if candidate:
                row["document_type_id"] = candidate
                row["document_type_id_source"] = "ui_row_action_network_or_url_or_expanded_dom"
            if expanded_text:
                row["ui_expanded_detail_text_compact"] = expanded_text[:1200]
        if row.get("document_type_id") and not row_audit["resolved"]:
            row_audit["resolved"] = True
            if str(row.get("document_type_id_source") or "").startswith("ui_row_action") or (matched and matched.get("document_type_id")):
                found_this_phase += 1
        row_audit["api_interactions"] = interactions[:10]
        audit.append(row_audit)
        # Return to the list and clear search so the next row starts clean.
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
            if not _urls_same_path(page.url, DOCTYPES_URL):
                await page.go_back(timeout=4000)
                await page.wait_for_timeout(800)
        except Exception:
            pass
        await _clear_doctype_listing_search(page)
        if kb_dir and (idx % 5 == 0 or idx == limit):
            try:
                _write_json(kb_dir / "old_doctypes_inventory_with_ids.ui_checkpoint.json", rows)
                _write_json(kb_dir / "doctype_ui_row_action_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    rows = _merge_doctype_records(rows)
    with_ids = sum(1 for r in rows if r.get("document_type_id"))
    report = {
        "ui_rows_attempted": limit,
        "ui_ids_found_this_phase": found_this_phase,
        "document_type_ids_before_ui_phase": ids_before,
        "document_type_ids_after_ui_phase": with_ids,
        "document_type_ids_missing_after_ui_phase": max(0, len(rows) - with_ids),
        "completion_percent_after_ui_phase": round((with_ids / len(rows) * 100), 2) if rows else 0.0,
        "note": "UI row-action phase repeats the real portal interaction: search each old Document Type, click safe View/Edit/Details/open row action, capture the API triggered by that click, and parse documentTypeId when the API/URL exposes it.",
    }
    return rows, audit, report


class DocumentTypeKBFlow:
    def __init__(self, config: AppConfig, *, doctypes_url: str = DOCTYPES_URL, fill_dummy: bool = True, known_document_type_id: str | None = None, write_heavy_evidence: bool = False, crawl_old_doctypes: bool = True, max_api_pages: int = 250, max_detail_rows: int | None = None, capture_deep_profiles: bool = True, max_deep_profile_rows: int | None = None):
        self.config = config
        self.doctypes_url = doctypes_url
        self.fill_dummy = fill_dummy
        self.known_document_type_id = known_document_type_id
        self.write_heavy_evidence = write_heavy_evidence
        self.crawl_old_doctypes = crawl_old_doctypes
        self.max_api_pages = max_api_pages
        # None means enrich every discovered Document Type row.  Earlier builds
        # accidentally used max_api_pages as the detail/UI row cap, which could
        # stop the Document Type KB before collecting full row-level information.
        self.max_detail_rows = max_detail_rows
        # Deep profile capture is the third phase: after inventory + ID learning it
        # parses/fetches `/api/document-type/{id}/details` so every Document Type can
        # carry documentIdentifier/rootElement/schema/attributes/usage evidence.
        self.capture_deep_profiles = capture_deep_profiles
        self.max_deep_profile_rows = max_deep_profile_rows

    async def run(self, ctx: RunContext, input_json: str | None = None, *, browser_session: BrowserSession | None = None) -> Dict[str, Any]:
        run_dir = ctx.run_dir
        kb_dir = run_dir / "doctype_kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        input_data = _read_json(input_json)
        previous_values = build_previous_interaction_values(input_data, known_document_type_id=self.known_document_type_id)
        seed = extract_document_type_seed(input_data)
        dummy_values = build_dummy_fill_values(seed, exact=bool(input_data.get("_replicate_exact_input_values")))
        warnings: List[str] = []
        status = "completed"
        files: Dict[str, str] = {}

        def progress(phase: str, completed: int = 0, total: int = 0, detail: str = "", counts: Optional[Dict[str, Any]] = None) -> None:
            _write_doctype_progress(kb_dir, phase=phase, completed=completed, total=total, detail=detail, counts=counts)

        progress("initializing", 0, 8, "Preparing browser, SSO, Document Type API capture and output folders")
        async with browser_session_scope(self.config, run_dir, existing=browser_session, phase_name=run_dir.name) as browser:
            page = await browser.start() if browser.page is None else browser.page
            browser.set_stage("doctype_kb_login")
            progress("login", 1, 8, "Opening browser and waiting for Dell SSO/Document Types page")
            self.config.portal.base_url = self.doctypes_url
            await browser.goto_base_and_complete_sso(self.doctypes_url)
            browser.set_stage("doctype_kb_open_doctypes")
            progress("open_doctypes", 2, 8, "Opening Document Types link and waiting for page/API readiness")
            # Do not blindly re-navigate when SSO already landed on Document Types.
            # A second immediate navigation can abort Angular remoteEntry/chunk loading and leave a blank page.
            if not _urls_same_path(page.url, self.doctypes_url):
                await browser.navigate(self.doctypes_url)
            else:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
            ready = await _wait_for_doctype_listing_ready(page, browser, self.doctypes_url, warnings)
            progress("doctype_page_ready", 3, 8, f"Document Types ready={ready}; capturing listing/API evidence")
            await browser.save_dom_snapshot("doctypes_listing_before_add")
            await page.wait_for_timeout(1000)
            add_api_interactions: List[Dict[str, Any]] = []
            listing_buttons = await _evaluate_buttons(page)
            listing_ui_rows = await _collect_ui_doctype_rows(page)
            listing_api_interactions, old_doctypes = collect_doctype_api_interactions(browser.network_tab_events, stage_label="doctype_listing_load")
            if self.crawl_old_doctypes and not old_doctypes:
                browser.set_stage("doctype_kb_direct_summary_api_fallback")
                progress("direct_summary_api_fallback", 0, max(1, self.max_api_pages), "No list rows from network yet; calling read-only Document Type summary API")
                direct_rows, direct_interactions, direct_audit = await _direct_fetch_doctype_summary(page, max_pages=self.max_api_pages)
                if direct_interactions:
                    listing_api_interactions.extend(direct_interactions)
                    old_doctypes = _merge_doctype_records([*old_doctypes, *direct_rows])
                    # Preserve these calls in the pagination audit so failures are visible in the upload summary.
                    pagination_audit_fallback_seed = direct_audit
                else:
                    pagination_audit_fallback_seed = []
            else:
                pagination_audit_fallback_seed = []
            pagination_audit: List[Dict[str, Any]] = list(pagination_audit_fallback_seed)
            paginated_doctypes: List[Dict[str, Any]] = []
            detail_enrichment_audit: List[Dict[str, Any]] = []
            detail_enrichment_report: Dict[str, Any] = {}
            ui_row_action_audit: List[Dict[str, Any]] = []
            ui_row_action_report: Dict[str, Any] = {}
            deep_profile_audit: List[Dict[str, Any]] = []
            deep_profile_report: Dict[str, Any] = {}
            if self.crawl_old_doctypes and listing_api_interactions:
                browser.set_stage("doctype_kb_old_doctype_api_pagination")
                progress("old_doctype_api_pagination", 0, max(1, self.max_api_pages), f"Replaying observed listing/pagination APIs; rows={len(old_doctypes)}")
                paginated_doctypes, replay_audit = await _crawl_old_doctype_inventory_from_apis(page, listing_api_interactions, max_pages=self.max_api_pages)
                pagination_audit.extend(replay_audit)
                old_doctypes = _merge_doctype_records([*old_doctypes, *paginated_doctypes])
                browser.set_stage("doctype_kb_old_doctype_detail_id_enrichment")
                detail_limit = min(len(old_doctypes), int(self.max_detail_rows)) if self.max_detail_rows is not None else len(old_doctypes)
                progress("doctype_detail_id_enrichment", 0, max(1, detail_limit), f"Trying read-only detail lookups for numeric documentTypeIds; old_doctypes={len(old_doctypes)}; detail_limit={detail_limit}")
                old_doctypes, detail_enrichment_audit, detail_enrichment_report = await _enrich_old_doctypes_with_detail_apis(
                    page, old_doctypes, listing_api_interactions, previous_values, max_details=detail_limit, kb_dir=kb_dir, progress_cb=progress
                )
                progress("doctype_detail_id_enrichment_done", detail_limit, max(1, detail_limit), f"Detail enrichment done; documentTypeIds={detail_enrichment_report.get('document_type_ids_found', 0)}/{detail_enrichment_report.get('total_old_doctypes', len(old_doctypes))}")
                # Second phase: repeat the real UI row/action detail flow, like Partner/System discovery.
                # This is the important phase for portals where the list API hides numeric documentTypeId.
                browser.set_stage("doctype_kb_ui_row_action_id_learning")
                max_ui_rows = detail_limit
                progress("doctype_ui_row_action_id_learning", 0, max(1, max_ui_rows), "Learning old Document Type IDs from real UI row actions/details")
                old_doctypes, ui_row_action_audit, ui_row_action_report = await _learn_old_doctype_ids_from_ui_row_actions(
                    page, browser, old_doctypes, previous_values, max_rows=max_ui_rows, kb_dir=kb_dir, progress_cb=progress
                )
                progress("doctype_ui_row_action_id_learning_done", max_ui_rows, max(1, max_ui_rows), f"UI row-action ID learning done; documentTypeIds={ui_row_action_report.get('document_type_ids_after_ui_phase', 0)}/{len(old_doctypes)}")
                # Merge both API and UI completion reports into the main report used by summaries.
                with_ids_after_ui = sum(1 for r in old_doctypes if r.get("document_type_id"))
                detail_enrichment_report.update({
                    "ui_row_action_phase": ui_row_action_report,
                    "document_type_ids_found": with_ids_after_ui,
                    "document_type_ids_missing": max(0, len(old_doctypes) - with_ids_after_ui),
                    "completion_percent": round((with_ids_after_ui / len(old_doctypes) * 100), 2) if old_doctypes else 0.0,
                })
                if self.capture_deep_profiles:
                    browser.set_stage("doctype_kb_deep_profile_enrichment")
                    deep_limit = min(len(old_doctypes), int(self.max_deep_profile_rows)) if self.max_deep_profile_rows is not None else len(old_doctypes)
                    progress("doctype_deep_profile_enrichment", 0, max(1, deep_limit), f"Capturing full Document Type profiles from details API; old_doctypes={len(old_doctypes)}; deep_limit={deep_limit}")
                    old_doctypes, deep_profile_audit, deep_profile_report = await _capture_doctype_deep_profiles(
                        page, browser, old_doctypes, [*listing_api_interactions, *pagination_audit], max_profiles=deep_limit, kb_dir=kb_dir, progress_cb=progress
                    )
                    progress("doctype_deep_profile_enrichment_done", deep_limit, max(1, deep_limit), f"Deep profile capture done; profiles={deep_profile_report.get('deep_profiles_captured', 0) + deep_profile_report.get('deep_profiles_partial', 0)}/{deep_profile_report.get('total_document_types', len(old_doctypes))}")
            else:
                old_doctypes = _apply_known_document_type_ids(old_doctypes, previous_values)
            # UI fallback rows are stored as evidence even when they cannot be normalized to IDs.
            progress("find_add_button", 4, 8, "Looking for + Add button after inventory capture")
            add = await _find_add_button(page)
            add_form_opened = False
            if add is None:
                warnings.append("Initial Document Type + Add lookup failed; reopening Document Types listing and retrying once.")
                await _force_open_doctypes_listing(page, self.doctypes_url)
                add = await _find_add_button(page)
            if add is None:
                status = "partial_success"
                warnings.append("Could not locate + Add button. Saved old Document Type listing/API KB only.")
            else:
                browser.set_stage("doctype_kb_click_add")
                progress("click_add_button", 5, 8, "Clicking + Add safely; will not save/create")
                before_add_event_count = len(browser.network_tab_events)
                add_form_opened = await _click_add_doctype_with_overlay_recovery(
                    page, browser, add, kb_dir=kb_dir, warnings=warnings
                )
                add_api_interactions, add_api_doctypes = collect_doctype_api_interactions(browser.network_tab_events[before_add_event_count:], stage_label="doctype_add_click")
                old_doctypes = _merge_doctype_records([*old_doctypes, *add_api_doctypes])
                if add_form_opened:
                    await browser.save_dom_snapshot("doctype_add_form_opened")
                else:
                    status = "partial_success"
            async def _react_click_doctype(add_loc, step_no):
                return await _click_add_doctype_with_overlay_recovery(
                    page, browser, add_loc, kb_dir=kb_dir, warnings=warnings
                )
            entry_audit = await ensure_phase_form_entry(
                page=page, browser=browser,
                phase=str(input_data.get("_full_dummy_fill_phase") or "document_type"),
                listing_url=self.doctypes_url, find_add=_find_add_button,
                is_form_open=_looks_like_doctype_add_form, click_add=_react_click_doctype,
                evidence_dir=kb_dir, max_steps=4, require_same_route=True,
            )
            files["phase_form_entry_react_json"] = str(kb_dir / "phase_form_entry_react.json")
            add_form_opened = bool(entry_audit.get("pass") and await _looks_like_doctype_add_form(page))
            if entry_audit.get("pass"):
                status = "completed"
            browser.set_stage("doctype_kb_capture_form")
            progress("capture_add_form", 6, 8, "Capturing Add Document Type form controls/dropdowns/required fields")
            repeatable_row_audit = []
            if add_form_opened:
                gate = await _ensure_doctype_create_surface(page, browser, doctypes_url=self.doctypes_url, kb_dir=kb_dir, warnings=warnings, stage="before_repeatable_rows")
                add_form_opened = bool(gate.get("pass"))
            phase_name = str(input_data.get("_full_dummy_fill_phase") or "document_type")
            controls: List[Dict[str, Any]] = []
            buttons = listing_buttons
            dropdowns: List[Dict[str, Any]] = []
            exploration_knowledge: Dict[str, Any] = {}
            target_branch_execution: Dict[str, Any] = {}
            target_branch_knowledge: Dict[str, Any] = {}
            fill_attempts: List[Dict[str, Any]] = []
            required_fields: List[Dict[str, Any]] = []
            state_graph = deterministic_state_graph(input_data) or compile_document_type_state_graph(input_data, phase_name)
            # Apply judge-approved same-family memory *before* deciding whether a
            # structure discovery pass is needed. Source and Target Document Type
            # have the same form contract, so a validated Source execution should
            # be replayed by semantic re-binding instead of rediscovering the form.
            session = getattr(page, "_hip_browser_session", None)
            flow_memory = getattr(session, "flow_pattern_memory", None) if session is not None else None
            if flow_memory is not None and hasattr(flow_memory, "apply_to_graph"):
                try:
                    state_graph = flow_memory.apply_to_graph(state_graph, phase=phase_name)
                except Exception as exc:
                    state_graph["flow_pattern_memory_match"] = {
                        "status": "error_fail_open", "validated_match": False,
                        "error": mask_sensitive_string(str(exc)),
                    }
            portal_form_dir = kb_dir.parent.parent / "portal_form_knowledge"
            portal_form_dir.mkdir(parents=True, exist_ok=True)

            # Document Type uses a strict structure-first lifecycle. Exploration is
            # completed before the business values are filled. Once exact filling
            # starts, no exploratory branch, option-catalogue click, or LLM action is
            # allowed to mutate the form again.
            if add_form_opened and self.fill_dummy:
                plan = input_data.get("_deterministic_plan") if isinstance(input_data.get("_deterministic_plan"), dict) else {}
                memory_match = state_graph.get("flow_pattern_memory_match") if isinstance(state_graph.get("flow_pattern_memory_match"), dict) else {}
                validated_replay = bool(
                    (str(plan.get("learned_verification_status") or "").lower() == "pass" and not (plan.get("knowledge_gaps") or []))
                    or state_graph.get("validated_replay")
                    or memory_match.get("validated_match")
                )
                replay_source_phase = str(((memory_match.get("best") or {}).get("phase") if isinstance(memory_match.get("best"), dict) else "") or "")
                sibling_replay = bool(validated_replay and replay_source_phase and replay_source_phase != phase_name)
                canonical_kb_path = Path(str(getattr(getattr(self.config, "brain", None), "unified_kb_path", "./knowledge_base/HIP_Unified_Deep_KB.json")))
                if not canonical_kb_path.is_absolute():
                    canonical_kb_path = (Path.cwd() / canonical_kb_path).resolve()
                canonical_deterministic_first = bool(
                    getattr(getattr(self.config, "brain", None), "auto_import_unified_kb", True)
                    and canonical_kb_path.is_file()
                )
                skip_prefill_discovery = bool(validated_replay or canonical_deterministic_first)
                structure_audit: Dict[str, Any] = {
                    "schema_version": "hip.doctype-structure-first.v1",
                    "phase": phase_name,
                    "learning_before_fill": True,
                    "target_fill_count_allowed": 1,
                    "validated_replay": validated_replay,
                    "sibling_replay": sibling_replay,
                    "replay_source_phase": replay_source_phase,
                    "canonical_deterministic_first": canonical_deterministic_first,
                    "skip_prefill_discovery": skip_prefill_discovery,
                }
                if skip_prefill_discovery:
                    # Deterministic scripts execute before discovery. The canonical
                    # reviewed KB is enough to attempt the exact form; discovery is
                    # reserved for recovery after a proven binding/drift failure.
                    # A validated sibling replay is even stronger and reuses the
                    # Source Document Type semantic interaction pattern directly.
                    # Re-bind the validated semantic pattern to the current clean
                    # drawer, create only the input-required rows, and fill once.
                    fast_status = (
                        "skipped_validated_same_family_replay" if validated_replay
                        else "skipped_canonical_deterministic_first"
                    )
                    exploration_knowledge = {
                        "status": fast_status,
                        "section": "Create Document Type",
                        "runtime_overlay_applied": False,
                        "reason": (
                            "judge-approved Document Type memory is available; no new dropdown/structure discovery is allowed"
                            if validated_replay else
                            "reviewed canonical Document Type contract is available; deterministic fill runs before any discovery"
                        ),
                        "source_phase": replay_source_phase,
                        "canonical_kb_path": str(canonical_kb_path),
                    }
                    structure_audit.update({
                        "status": fast_status,
                        "exploration_status": fast_status,
                        "learning_before_fill": False,
                        "memory_replay_only": bool(validated_replay),
                        "discovery_policy": "recovery_only_after_proven_deterministic_failure",
                    })
                    safe_write_json(kb_dir / "doctype_structure_first_audit.json", structure_audit)
                    row_audit = await apply_repeatable_row_adds(page, input_data, phase_name)
                    repeatable_row_audit.append(dict(row_audit, execution_stage="validated_memory_exact_target_form"))
                    if not row_audit.get("summary", {}).get("exact_row_count_pass"):
                        raise RuntimeError("Document Type deterministic-first path could not create the required repeatable rows")
                else:
                    try:
                        # Expose the complete repeatable-row schema without entering any
                        # business values. This lets the learner understand all row types,
                        # parent controls, children and option contracts first.
                        structure_rows = await apply_repeatable_row_adds(page, input_data, phase_name)
                        repeatable_row_audit = [dict(structure_rows, execution_stage="structure_probe")]
                        structure_audit["structure_repeatable_rows"] = structure_rows
                        if not structure_rows.get("summary", {}).get("exact_row_count_pass"):
                            raise RuntimeError("Document Type structure probe could not expose exact repeatable rows")

                        structure_controls = _annotate_control_occurrences(await _evaluate_controls(page))
                        structure_buttons = await _evaluate_buttons(page)
                        structure_dropdowns = await _collect_dropdown_options(page, structure_controls, phase=phase_name)
                        # Preserve the structure catalogue for reports. Do not reopen
                        # these controls after the exact form has been filled.
                        dropdowns = list(structure_dropdowns)
                        buttons = list(structure_buttons)
                        structure_audit.update({
                            "control_count": len(structure_controls),
                            "dropdown_count": len(structure_dropdowns),
                            "button_count": len(structure_buttons),
                        })

                        if validated_replay:
                            exploration_knowledge = {
                                "status": "validated_graph_structure_recheck",
                                "section": "Create Document Type",
                                "runtime_overlay_applied": False,
                                "control_registry": structure_controls,
                                "button_registry": structure_buttons,
                                "dropdown_registry": structure_dropdowns,
                                "reason": "judge-approved graph has no gaps; live structure was recaptured before exact fill",
                            }
                        else:
                            exploration_knowledge = await run_portal_form_exploration(
                                page=page,
                                phase=phase_name,
                                section="Create Document Type",
                                input_data=input_data,
                                controls=structure_controls,
                                dropdowns=structure_dropdowns,
                                buttons=structure_buttons,
                                repeatable_plan=build_repeatable_section_plan(input_data, phase_name),
                                repeatable_audit=repeatable_row_audit,
                                output_dir=portal_form_dir,
                                config=self.config,
                            )
                            # Exploration is advisory evidence. A restoration issue cannot
                            # cause a completed business form to be refilled because the
                            # business form has not been filled yet; we simply rebuild clean.
                            if str(exploration_knowledge.get("status") or "").startswith("failed") or exploration_knowledge.get("restore_errors"):
                                warnings.append("Document Type structure exploration completed with gaps; exact fill will use the deterministic input graph.")
                        structure_audit["exploration_status"] = exploration_knowledge.get("status")
                    except Exception as exc:
                        exploration_knowledge = {
                            "status": "failed_nonblocking_before_fill",
                            "runtime_overlay_applied": False,
                            "error": mask_sensitive_string(str(exc)),
                            "reason": "structure learning is fail-open; deterministic exact fill remains authoritative",
                        }
                        structure_audit["exploration_status"] = "failed_nonblocking_before_fill"
                        structure_audit["error"] = mask_sensitive_string(str(exc))
                        warnings.append(f"Document Type pre-fill structure learning warning: {mask_sensitive_string(str(exc))}")
                    safe_write_json(kb_dir / "doctype_structure_first_audit.json", structure_audit)

                    # Always discard the structure-probe surface and build one clean form.
                    # This is the only surface on which business input values are entered.
                    clean = await _ensure_doctype_create_surface(
                        page, browser, doctypes_url=self.doctypes_url, kb_dir=kb_dir,
                        warnings=warnings, stage="structure_learned_clean_target_form", force_reopen=True,
                    )
                    if not clean.get("pass"):
                        raise RuntimeError("Create Document Type form could not be rebuilt after structure learning")
                    row_audit = await apply_repeatable_row_adds(page, input_data, phase_name)
                    repeatable_row_audit.append(dict(row_audit, execution_stage="exact_target_form"))
                    if not row_audit.get("summary", {}).get("exact_row_count_pass"):
                        raise RuntimeError("Document Type exact target form could not create the required repeatable rows")

            if add_form_opened:
                gate = await _ensure_doctype_create_surface(
                    page, browser, doctypes_url=self.doctypes_url, kb_dir=kb_dir,
                    warnings=warnings, stage="before_target_branch_execution",
                )
                add_form_opened = bool(gate.get("pass"))

            if add_form_opened and self.fill_dummy and state_graph.get("nodes"):
                browser.set_stage("doctype_kb_target_branch_fill_once")
                progress(
                    "fill_target_branch",
                    0,
                    len(state_graph.get("nodes") or []),
                    "Structure learned; filling the exact current input.json path once, then freezing the form for verification",
                )
                autonomous_execution: Dict[str, Any] = {}
                if autonomous_phase_enabled(self.config, phase_name):
                    autonomous_cfg = getattr(self.config, "autonomous_form", None)
                    autonomous_execution = await execute_autonomous_phase_goal(
                        page=page, graph=state_graph, phase=phase_name, input_data=input_data,
                        config=self.config, output_dir=kb_dir / "autonomous_form_runtime",
                        max_cycles=int(getattr(autonomous_cfg, "max_adaptive_cycles", 5) or 5),
                        repair=True, strict_live_execution=True,
                        executor=execute_document_type_state_graph,
                    )
                    target_branch_execution = autonomous_execution.get("final_execution") or {}
                    _write_json(kb_dir / "doctype_autonomous_form_execution.json", autonomous_execution)
                else:
                    target_branch_execution = await execute_document_type_state_graph(
                        page, state_graph, phase=phase_name, max_retries=2
                    )
                # One clean retry is allowed only for an actual deterministic fill
                # failure. Exploration and model/judge disagreement can never trigger
                # this branch.
                if not target_branch_execution.get("pass"):
                    repair = await _ensure_doctype_create_surface(
                        page, browser, doctypes_url=self.doctypes_url, kb_dir=kb_dir,
                        warnings=warnings, stage="target_branch_clean_retry", force_reopen=True,
                    )
                    if repair.get("pass"):
                        row_repair = await apply_repeatable_row_adds(page, input_data, phase_name)
                        repeatable_row_audit.append(dict(row_repair, execution_stage="exact_fill_retry"))
                        if row_repair.get("summary", {}).get("exact_row_count_pass"):
                            if autonomous_phase_enabled(self.config, phase_name):
                                autonomous_cfg = getattr(self.config, "autonomous_form", None)
                                autonomous_execution = await execute_autonomous_phase_goal(
                                    page=page, graph=state_graph, phase=phase_name, input_data=input_data,
                                    config=self.config, output_dir=kb_dir / "autonomous_form_runtime_retry",
                                    max_cycles=int(getattr(autonomous_cfg, "max_adaptive_cycles", 5) or 5),
                                    repair=True, strict_live_execution=True,
                                    executor=execute_document_type_state_graph,
                                )
                                target_branch_execution = autonomous_execution.get("final_execution") or {}
                                _write_json(kb_dir / "doctype_autonomous_form_execution_retry.json", autonomous_execution)
                            else:
                                target_branch_execution = await execute_document_type_state_graph(
                                    page, state_graph, phase=phase_name, max_retries=2
                                )
                _write_json(kb_dir / "doctype_target_branch_execution.json", target_branch_execution)
                form_state_model = {
                    "schema_version": "hip.doctype-form-state-model-bundle.v2",
                    "phase": phase_name,
                    "graph_id": state_graph.get("graph_id"),
                    "initial": target_branch_execution.get("initial_form_state_model") or {},
                    "final": target_branch_execution.get("final_form_state_model") or {},
                    "completed_node_ids": target_branch_execution.get("completed_node_ids") or [],
                    "transaction_proofs": [
                        {
                            "node_id": attempt.get("node_id"),
                            "field": attempt.get("field"),
                            "input_path": attempt.get("input_path"),
                            "binding_diagnostics": attempt.get("binding_diagnostics") or {},
                            "transaction_proof": attempt.get("transaction_proof") or {},
                            "success": attempt.get("success"),
                        }
                        for attempt in target_branch_execution.get("attempts") or []
                        if isinstance(attempt, dict)
                    ],
                }
                files["doctype_form_state_model_json"] = _write_json(
                    kb_dir / "doctype_form_state_model.json", form_state_model
                )
                if not target_branch_execution.get("pass"):
                    failed = target_branch_execution.get("failed_attempts") or []
                    raise RuntimeError(
                        "Document Type target branch could not be committed exactly: "
                        + mask_sensitive_string(json.dumps(failed[:8], ensure_ascii=False, default=str))
                    )
                fill_attempts = [dict(a, execution_stage="target_branch_fill_once") for a in target_branch_execution.get("attempts", []) if isinstance(a, dict)]
                controls = [dict(c) for c in target_branch_execution.get("final_controls", []) if isinstance(c, dict)]
                progress(
                    "fill_target_branch",
                    len(state_graph.get("nodes") or []),
                    len(state_graph.get("nodes") or []),
                    "Exact Document Type form committed once and frozen; only read-only evidence and judges may run now",
                )

                target_branch_knowledge = build_target_branch_knowledge(state_graph, target_branch_execution)
                target_knowledge_file = portal_form_dir / f"{phase_name}_target_branch_form_knowledge.json"
                target_branch_knowledge["knowledge_file"] = str(target_knowledge_file)
                target_branch_knowledge["form_frozen_after_exact_fill"] = True
                target_branch_knowledge["version_policy"] = "verify_only_never_type"
                safe_write_json(target_knowledge_file, target_branch_knowledge)

                merged_knowledge_file = portal_form_dir / f"{phase_name}_form_knowledge.json"
                merge_inputs = [target_branch_knowledge]
                if isinstance(exploration_knowledge, dict) and exploration_knowledge.get("status") not in {"disabled"}:
                    merge_inputs.append(exploration_knowledge)
                merged_knowledge = merge_section_knowledge(merge_inputs, phase=phase_name, output_file=merged_knowledge_file)
                merged_knowledge["target_branch_graph"] = state_graph
                merged_knowledge["target_branch_execution"] = {
                    "pass": target_branch_execution.get("pass"),
                    "attempt_count": len(target_branch_execution.get("attempts") or []),
                    "failed_attempts": target_branch_execution.get("failed_attempts") or [],
                }
                merged_knowledge["learning_order"] = "learn complete form structure first; rebuild clean form; create exact rows; fill input graph once; freeze for verification"
                merged_knowledge["post_fill_exploration_allowed"] = False
                merged_knowledge["form_frozen_after_exact_fill"] = True
                safe_write_json(merged_knowledge_file, merged_knowledge)
                exploration_knowledge = merged_knowledge

            elif add_form_opened:
                # Read-only KB mode still captures the live empty form, but it is not
                # considered executable knowledge for fast filling.
                controls = _annotate_control_occurrences(await _evaluate_controls(page))
                buttons = await _evaluate_buttons(page)
                dropdowns = await _collect_dropdown_options(page, controls, phase=phase_name)

            # Compatibility projection for existing reports and CSVs. The semantic
            # identity is retained; dynamic selectors remain current-run evidence only.
            projected_controls: List[Dict[str, Any]] = []
            for c in controls:
                row = dict(c)
                key = row.get("semantic_key") or row.get("mapped_document_type_key")
                row["mapped_document_type_key"] = key
                node = next((n for n in state_graph.get("nodes", []) if n.get("field_key") == key and n.get("row_index") == row.get("row_index") and (not n.get("row_kind") or n.get("row_kind") == row.get("row_kind"))), None)
                if node:
                    row["recommended_value"] = node.get("expected_value")
                    row["input_path"] = node.get("input_path")
                    row["knowledge_node"] = node.get("node_id")
                projected_controls.append(row)
            controls = projected_controls
            required_fields = [
                {
                    "mapped_document_type_key": n.get("field_key"),
                    "label": ((n.get("semantic_locator") or {}).get("labels") or [n.get("field_key")])[0],
                    "section": n.get("section"), "row_kind": n.get("row_kind"), "row_index": n.get("row_index"),
                    "expected_value": n.get("expected_value"), "input_path": n.get("input_path"),
                    "required": True,
                }
                for n in state_graph.get("nodes", []) if isinstance(n, dict) and n.get("required")
            ]
            buttons = await _evaluate_buttons(page) if add_form_opened else listing_buttons
            if add_form_opened:
                exact_form_frozen = bool(self.fill_dummy and target_branch_execution.get("pass"))
                # Only dropdown exploration is forbidden after an exact fill.  The
                # final DOM/text/screenshot capture is read-only and mandatory for
                # deterministic, GPT and vision judges.  A previous condition
                # accidentally skipped both operations, leaving 29/29 execution
                # proof but no judgeable filled-form surface.
                if not exact_form_frozen:
                    try:
                        dropdowns = _merge_dropdown_kb(
                            dropdowns,
                            await _collect_dropdown_options(page, controls, phase=phase_name),
                        )
                    except Exception as exc:
                        warnings.append(f"Final stateful dropdown capture skipped: {mask_sensitive_string(str(exc))}")

                browser.set_stage("doctype_kb_lock_filled_form_evidence")
                final_surface = await inspect_doctype_create_surface(page)
                surface_file = await _save_doctype_surface_evidence(
                    kb_dir,
                    "doctype_surface_before_final_evidence",
                    final_surface,
                )
                if not final_surface.get("pass"):
                    raise RuntimeError(
                        "HIP_DOCTYPE_FILLED_FORM_EVIDENCE_LOST: exact fill completed but the active Create Document Type "
                        "surface was unavailable before read-only judge evidence capture"
                    )
                if self.fill_dummy and state_graph.get("nodes") and not target_branch_execution.get("pass"):
                    raise RuntimeError("Stateful Document Type graph did not pass; refusing false phase success")

                dom_paths = await browser.save_dom_snapshot("doctype_add_form_after_dummy_fill_no_save")
                screenshot_path = kb_dir / "doctype_add_form_after_dummy_fill_no_save.png"
                screenshot_error = ""
                screenshot_written = ""
                try:
                    screenshot_written = await browser.screenshot(screenshot_path, full_page=True)
                except Exception as exc:
                    screenshot_error = mask_sensitive_string(str(exc))
                    # Full-page capture can fail on a very tall repeated-row form.
                    # A viewport screenshot is still valid current-surface evidence
                    # and is preferable to allowing the vision gate to see no image.
                    try:
                        screenshot_written = await browser.screenshot(screenshot_path, full_page=False)
                        screenshot_error = ""
                    except Exception as fallback_exc:
                        screenshot_error = mask_sensitive_string(
                            f"full_page={exc}; viewport={fallback_exc}"
                        )

                evidence_lock = {
                    "schema_version": "hip.doctype-filled-form-evidence-lock.v1",
                    "phase": phase_name,
                    "exact_form_frozen": exact_form_frozen,
                    "target_execution_pass": bool(target_branch_execution.get("pass")),
                    "target_execution_attempt_count": len(target_branch_execution.get("attempts") or []),
                    "failed_attempt_count": len(target_branch_execution.get("failed_attempts") or []),
                    "surface_pass": bool(final_surface.get("pass")),
                    "surface_reason": final_surface.get("reason"),
                    "surface_file": str(surface_file or ""),
                    "dom_snapshot": dom_paths,
                    "screenshot": str(screenshot_written or ""),
                    "screenshot_exists": bool(screenshot_written and Path(screenshot_written).is_file()),
                    "screenshot_error": screenshot_error,
                    "post_fill_control_exploration_performed": False if exact_form_frozen else None,
                    "form_mutated_after_exact_fill": False,
                    "form_state_model_one_to_one": bool(
                        (target_branch_execution.get("final_form_state_model") or {}).get("one_to_one_pass")
                    ),
                    "form_state_model_missing_required": (target_branch_execution.get("final_form_state_model") or {}).get("missing_required") or [],
                    "form_state_model_ambiguous_nodes": (target_branch_execution.get("final_form_state_model") or {}).get("ambiguous_nodes") or [],
                    "unintended_mutation_count": sum(
                        len(((attempt.get("transaction_proof") or {}).get("protected_state_changes") or []))
                        for attempt in target_branch_execution.get("attempts") or []
                        if isinstance(attempt, dict)
                    ),
                    "judge_evidence_ready": bool(
                        final_surface.get("pass")
                        and dom_paths.get("html")
                        and dom_paths.get("text")
                        and screenshot_written
                        and Path(screenshot_written).is_file()
                    ),
                }
                evidence_lock_file = _write_json(
                    kb_dir / "doctype_filled_form_evidence_lock.json",
                    evidence_lock,
                )
                files["doctype_filled_form_evidence_lock_json"] = evidence_lock_file
                if dom_paths.get("html"):
                    files["doctype_after_fill_dom_html"] = dom_paths["html"]
                if dom_paths.get("text"):
                    files["doctype_after_fill_dom_text"] = dom_paths["text"]
                if screenshot_written:
                    files["doctype_after_fill_screenshot"] = str(screenshot_written)
                if not evidence_lock["judge_evidence_ready"]:
                    raise RuntimeError(
                        "HIP_DOCTYPE_FILLED_FORM_EVIDENCE_CAPTURE_FAILED: exact fill completed but the locked "
                        f"read-only evidence bundle is incomplete: {mask_sensitive_string(str(evidence_lock))}"
                    )
            browser.set_stage("doctype_kb_summarize")
            progress("summarize_and_write_outputs", 7, 8, "Writing compact KB, checkpoints and upload zip")
            network_api_interactions, all_api_doctypes = collect_doctype_api_interactions(browser.network_tab_events, stage_label="full_doctype_run")
            # Include direct fallback/pagination learned APIs in the final API KB, not only raw Network events.
            all_api_interactions = _dedupe_api_interactions([*listing_api_interactions, *add_api_interactions, *network_api_interactions])
            old_doctypes = _merge_doctype_records(_apply_known_document_type_ids([*old_doctypes, *all_api_doctypes], previous_values))
            with_ids = sum(1 for r in old_doctypes if r.get("document_type_id"))
            if not detail_enrichment_report:
                detail_enrichment_report = {}
            detail_enrichment_report.update({
                "total_old_doctypes": len(old_doctypes),
                "document_type_ids_found": with_ids,
                "document_type_ids_missing": max(0, len(old_doctypes) - with_ids),
                "completion_percent": round((with_ids / len(old_doctypes) * 100), 2) if old_doctypes else 0.0,
                "final_report_note": "Counts are recalculated after final merge of listing, direct fallback, pagination, detail, UI-row-action, deep-profile, and full-run API evidence.",
                "deep_profile_phase": deep_profile_report,
            })
            doctype_lookup = _build_doctype_lookup(old_doctypes)
            network_relevant = []
            for e in browser.network_tab_events[-200:]:
                payload = e.model_dump() if hasattr(e, "model_dump") else getattr(e, "__dict__", {})
                url = str(payload.get("url") or "")
                if any(k in url.lower() for k in ["doctype", "doc-type", "document-type", "documenttype", "document", "securelink"]):
                    payload.pop("response_body_text_redacted", None)
                    network_relevant.append(mask_sensitive_data(payload))
            kb = {
                "run_id": ctx.run_id,
                "captured_at": utc_now(),
                "url": self.doctypes_url,
                "safety": {
                    "save_clicked": False,
                    "create_clicked": False,
                    "submit_clicked": False,
                    "note": "The flow opens + Add and fills disposable dummy values only. It never clicks Save/Create/Submit.",
                },
                "previous_interaction_values": previous_values,
                "dummy_fill_values": dummy_values,
                "deterministic_plan_runtime": deterministic_plan_summary(input_data),
                "old_doctypes_inventory": old_doctypes,
                "old_doctype_id_lookup_by_name": doctype_lookup,
                "doctype_api_interactions": all_api_interactions,
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
                "autonomous_goal_runtime_enabled": autonomous_phase_enabled(self.config, str(input_data.get("_full_dummy_fill_phase") or "document_type")),
                "autonomous_goal_runtime_policy": "live-goal-authoritative; phase-specific logic is advisory/structural acceleration only",
                "target_branch_knowledge": target_branch_knowledge,
                "repeatable_section_plan": build_repeatable_section_plan(input_data, str(input_data.get("_full_dummy_fill_phase") or "document_type")),
                "repeatable_row_audit": repeatable_row_audit,
                "portal_form_exploration": exploration_knowledge,
                "network_events_relevant_compact": network_relevant,
                "warnings": warnings,
            }
            files["doctype_form_kb_json"] = _write_json(kb_dir / "doctype_form_kb.json", kb)
            files["old_doctypes_inventory_json"] = _write_json(kb_dir / "old_doctypes_inventory.json", old_doctypes)
            files["old_doctype_id_lookup_by_name_json"] = _write_json(kb_dir / "old_doctype_id_lookup_by_name.json", doctype_lookup)
            files["doctype_api_interactions_json"] = _write_json(kb_dir / "doctype_api_interactions.json", all_api_interactions)
            files["doctype_api_pagination_audit_json"] = _write_json(kb_dir / "doctype_api_pagination_audit.json", pagination_audit)
            files["doctype_detail_enrichment_audit_json"] = _write_json(kb_dir / "doctype_detail_enrichment_audit.json", detail_enrichment_audit)
            files["doctype_ui_row_action_enrichment_audit_json"] = _write_json(kb_dir / "doctype_ui_row_action_enrichment_audit.json", ui_row_action_audit)
            files["doctype_deep_profile_enrichment_audit_json"] = _write_json(kb_dir / "doctype_deep_profile_enrichment_audit.json", deep_profile_audit)
            files["doctype_deep_profile_report_json"] = _write_json(kb_dir / "doctype_deep_profile_report.json", deep_profile_report)
            files["old_doctypes_deep_profiles_json"] = _write_json(kb_dir / "old_doctypes_deep_profiles.json", old_doctypes)
            files["doctype_id_completion_report_json"] = _write_json(kb_dir / "doctype_id_completion_report.json", detail_enrichment_report)
            files["old_doctypes_inventory_with_ids_json"] = _write_json(kb_dir / "old_doctypes_inventory_with_ids.json", old_doctypes)
            files["old_doctypes_inventory_with_ids_csv"] = _write_doctypes_inventory_csv(kb_dir / "old_doctypes_inventory_with_ids.csv", old_doctypes)
            files["listing_ui_rows_json"] = _write_json(kb_dir / "doctype_listing_ui_rows.json", listing_ui_rows)
            files["old_doctypes_inventory_csv"] = _write_doctypes_inventory_csv(kb_dir / "old_doctypes_inventory.csv", old_doctypes)
            files["previous_values_json"] = _write_json(kb_dir / "doctype_previous_interaction_values.json", previous_values)
            files["dropdowns_json"] = _write_json(kb_dir / "doctype_dropdowns.json", dropdowns)
            files["required_fields_json"] = _write_json(kb_dir / "doctype_required_fields.json", required_fields)
            files["dummy_fill_plan_json"] = _write_json(kb_dir / "doctype_dummy_fill_plan.json", {"values": dummy_values, "attempts": fill_attempts, "state_graph": state_graph, "target_branch_execution": target_branch_execution})
            files["stateful_graph_json"] = _write_json(kb_dir / "doctype_stateful_target_graph.json", state_graph)
            files["stateful_execution_json"] = _write_json(kb_dir / "doctype_target_branch_execution.json", target_branch_execution)
            files["dom_events_json"] = _write_json(kb_dir / "doctype_dom_events.json", _build_dom_event_kb(controls, dropdowns, buttons))
            files.update(_write_doctype_api_flow_knowledge_graph_safe(kb_dir, kb))
            files["markdown"] = _write_markdown(kb_dir / "DOCTYPE_KB_SUMMARY.md", kb)
            files["csv"] = _write_controls_csv(kb_dir / "doctype_form_controls.csv", controls)
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
            progress("completed", 8, 8, "Document Type KB summary zip created")
        counts = {
            "old_doctypes": len(json.loads((kb_dir / "old_doctypes_inventory.json").read_text(encoding="utf-8"))) if (kb_dir / "old_doctypes_inventory.json").exists() else 0,
            "old_doctypes_with_numeric_id": (json.loads((kb_dir / "doctype_id_completion_report.json").read_text(encoding="utf-8")).get("document_type_ids_found", 0) if (kb_dir / "doctype_id_completion_report.json").exists() else 0),
            "api_interactions": len(json.loads((kb_dir / "doctype_api_interactions.json").read_text(encoding="utf-8"))) if (kb_dir / "doctype_api_interactions.json").exists() else 0,
            "form_controls": len(files) and len((json.loads((kb_dir / "doctype_form_kb.json").read_text(encoding="utf-8"))).get("form_controls", [])),
            "required_fields": len(json.loads((kb_dir / "doctype_required_fields.json").read_text(encoding="utf-8"))),
            "dropdowns": len(json.loads((kb_dir / "doctype_dropdowns.json").read_text(encoding="utf-8"))),
            "deep_profiles_captured": (json.loads((kb_dir / "doctype_deep_profile_report.json").read_text(encoding="utf-8")).get("deep_profiles_captured", 0) if (kb_dir / "doctype_deep_profile_report.json").exists() else 0),
        }
        result = DocumentTypeKBResult(run_id=ctx.run_id, run_dir=str(run_dir), kb_dir=str(kb_dir), status=status, counts=counts, files=files, warnings=warnings)
        (run_dir / "doctype_kb_summary.json").write_text(json.dumps(result.__dict__, indent=2, ensure_ascii=False), encoding="utf-8")
        return result.__dict__



def _write_doctype_progress(kb_dir: Path, *, phase: str, completed: int, total: int, detail: str = "", counts: Optional[Dict[str, Any]] = None) -> None:
    """Write a lightweight heartbeat/progress checkpoint for long Document Type KB runs."""
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
        (kb_dir / "doctype_progress.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        with (kb_dir / "doctype_progress_events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        (kb_dir / "doctype_progress_heartbeat.txt").write_text(f"{payload['timestamp']} | {phase} | {completed_i}/{total_i} | {percent}% | {payload['detail']}\n", encoding="utf-8")
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


def _write_doctypes_inventory_csv(path: Path, rows: List[Dict[str, Any]]) -> str:
    fields = [
        "document_type_id", "document_type_name", "document_type_version", "status", "transaction_type", "format", "validation_type",
        "description", "document_identifier", "document_identifier_operation", "document_identifier_derived_from", "root_element",
        "document_version", "usage", "schema_file", "attribute_count", "deep_profile_status", "deep_profile_completeness_score",
        "deep_profile_source_url", "available_environments", "latest_dev_version", "created_by", "updated_by", "created_at", "updated_at",
        "source", "source_url",
    ]
    return safe_write_csv(path, fields, rows)


def _write_controls_csv(path: Path, controls: List[Dict[str, Any]]) -> str:
    fields = ["index", "mapped_document_type_key", "label", "tag", "type", "role", "required", "disabled", "readonly", "name", "id", "placeholder", "selector", "recommended_value"]
    return safe_write_csv(path, fields, controls)


def _build_dom_event_kb(controls: List[Dict[str, Any]], dropdowns: List[Dict[str, Any]], buttons: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "text_inputs": [
            {"label": c.get("label"), "selector": c.get("selector"), "events": ["focus", "input", "change", "blur"], "mapped_document_type_key": c.get("mapped_document_type_key")}
            for c in controls if (c.get("tag") in {"input", "textarea"} and str(c.get("type") or "").lower() != "file")
        ],
        "file_inputs": [
            {"label": c.get("label"), "selector": c.get("selector"), "events": ["setInputFiles", "change"], "mapped_document_type_key": c.get("mapped_document_type_key")}
            for c in controls if str(c.get("type") or "").lower() == "file" or c.get("mapped_document_type_key") == "schema_file"
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


def build_doctype_api_flow_knowledge_graph(kb: Dict[str, Any]) -> Dict[str, Any]:
    """Build a compact Knowledge Graph for Document Type API learning + Add-form KB.

    This is deliberately separate from the heavy browser/run KG. It focuses on:
    Document Types page -> listing APIs -> pagination replay -> old Document Type rows -> +Add -> form controls/dropdowns -> dummy fill.
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

    run_id = str(kb.get("run_id") or "DOCTYPE-KB")
    run_node = add_node(_kg_id("run", run_id), "RUN", run_id, captured_at=kb.get("captured_at"), mode="doctype_api_learning")
    page_node = add_node(_kg_id("page", kb.get("url")), "PAGE", kb.get("url") or "Document Types", url=kb.get("url"))
    add_edge(run_node, page_node, "RUN_OPENED_PAGE")

    safety_node = add_node(_kg_id("safety", run_id), "SAFETY_POLICY", "Do not save Document Type", **(kb.get("safety") or {}))
    add_edge(run_node, safety_node, "RUN_ENFORCED_SAFETY")

    prev = kb.get("previous_interaction_values") or {}
    if prev:
        prev_node = add_node(_kg_id("previous_values", run_id), "PREVIOUS_INTERACTION_VALUES", "Uploaded/prior Document Type values", **prev)
        add_edge(run_node, prev_node, "USED_PREVIOUS_VALUES")
        dm_vals = prev.get("document_type_values_to_fill") or {}
        if dm_vals:
            doctype_node = add_node(_kg_id("seed_doctype", dm_vals.get("document_type_name"), dm_vals.get("document_identifier")), "DOCTYPE_SEED", dm_vals.get("document_type_name") or dm_vals.get("document_identifier") or "Document Type seed", **dm_vals, known_ids=prev.get("known_ids"))
            add_edge(prev_node, doctype_node, "CONTAINED_DOCTYPE_SEED")

    stage_nodes: Dict[str, str] = {}
    def stage_node(stage: str) -> str:
        if stage not in stage_nodes:
            stage_nodes[stage] = add_node(_kg_id("stage", run_id, stage), "STAGE", stage)
            add_edge(run_node, stage_nodes[stage], "RUN_STARTED_STAGE")
        return stage_nodes[stage]

    # API interactions from initial list load, pagination replay and +Add click.
    interactions = kb.get("doctype_api_interactions") or []
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
            doctype_rows_extracted=inter.get("doctype_rows_extracted"),
        )
        add_edge(st, api_node, "STAGE_OBSERVED_API", order=idx)
        add_edge(page_node, api_node, "PAGE_TRIGGERED_API", stage=stage)
        endpoint_node = add_node(_kg_id("endpoint", inter.get("method"), inter.get("url")), "ENDPOINT", endpoint, method=inter.get("method"), url=inter.get("url"))
        add_edge(api_node, endpoint_node, "API_HIT_ENDPOINT")
        shape = inter.get("response_shape") or {}
        shape_node = add_node(_kg_id("response_shape", inter.get("url"), json.dumps(shape, sort_keys=True, default=str)), "RESPONSE_SHAPE", f"{shape.get('type','response')} rows={shape.get('row_count',0)}", **shape)
        add_edge(api_node, shape_node, "API_RETURNED_RESPONSE_SHAPE")
        for ridx, row in enumerate(inter.get("sample_doctype_rows") or [], start=1):
            rec_node = add_node(_kg_id("doctype_record", row.get("document_type_id") or row.get("document_type_name") or ridx), "DOCTYPE_RECORD", row.get("document_type_name") or row.get("document_identifier") or row.get("document_type_id") or f"record {ridx}", **row)
            add_edge(api_node, rec_node, "API_RETURNED_DOCTYPE_RECORD", sample=True)

    # Old inventory records: link every normalized row but keep node props compact.
    for idx, row in enumerate(kb.get("old_doctypes_inventory") or [], start=1):
        compact = {k: row.get(k, "") for k in ["document_type_id", "document_type_name", "document_type_version", "status", "document_identifier", "root_element", "document_version", "schema_file", "source", "source_url"]}
        rec_node = add_node(_kg_id("doctype_record", compact.get("document_type_id") or compact.get("document_type_name") or idx), "DOCTYPE_RECORD", compact.get("document_type_name") or compact.get("document_identifier") or compact.get("document_type_id") or f"Document Type {idx}", **compact)
        add_edge(run_node, rec_node, "RUN_NORMALIZED_OLD_DOCTYPE")

    for idx, audit in enumerate(kb.get("pagination_replay_audit") or [], start=1):
        audit_node = add_node(_kg_id("pagination", audit.get("url"), idx), "PAGINATION_REPLAY", audit.get("url") or f"pagination {idx}", **{**audit, "order": idx})
        add_edge(stage_node("doctype_kb_old_doctype_api_pagination"), audit_node, "REPLAYED_PAGINATED_API", order=idx)

    for idx, audit in enumerate(kb.get("detail_enrichment_audit") or [], start=1):
        dm_label = audit.get("document_type_name") or audit.get("document_identifier") or f"detail enrichment {idx}"
        audit_node = add_node(_kg_id("detail_enrichment", dm_label, idx), "DOCTYPE_DETAIL_ENRICHMENT", dm_label, order=idx, resolved=audit.get("resolved"), attempts_count=len(audit.get("attempts") or []), document_type_name=audit.get("document_type_name"), document_identifier=audit.get("document_identifier"))
        add_edge(stage_node("doctype_kb_old_doctype_detail_id_enrichment"), audit_node, "TRIED_DETAIL_ID_ENRICHMENT", order=idx, resolved=audit.get("resolved"))
        for aidx, attempt in enumerate((audit.get("attempts") or [])[:8], start=1):
            api_attempt = add_node(_kg_id("detail_api_attempt", attempt.get("url"), idx, aidx), "DETAIL_API_ATTEMPT", attempt.get("url") or f"attempt {aidx}", **{**attempt, "order": aidx})
            add_edge(audit_node, api_attempt, "ATTEMPTED_READ_ONLY_DETAIL_API", status=attempt.get("status"), document_type_id_found=attempt.get("document_type_id_found"))

    for idx, audit in enumerate(kb.get("ui_row_action_enrichment_audit") or [], start=1):
        dm_label = audit.get("document_type_name") or audit.get("document_identifier") or f"ui row action {idx}"
        ui_node = add_node(_kg_id("ui_row_action_detail", dm_label, idx), "UI_ROW_ACTION_DETAIL", dm_label, order=idx, resolved=audit.get("resolved"), search_used=audit.get("search_used"), network_events_after_click=audit.get("network_events_after_click"), api_interactions_after_click=audit.get("api_interactions_after_click"), document_type_name=audit.get("document_type_name"), document_identifier=audit.get("document_identifier"), url_document_type_id_candidate=audit.get("url_document_type_id_candidate"), event_document_type_id_candidate=audit.get("event_document_type_id_candidate"))
        add_edge(stage_node("doctype_kb_ui_row_action_id_learning"), ui_node, "REPEATED_UI_ROW_ACTION_TO_LEARN_ID", order=idx, resolved=audit.get("resolved"))
        click_info = audit.get("click") or {}
        click_node = add_node(_kg_id("ui_row_click", dm_label, idx), "UI_ACTION", click_info.get("reason") or click_info.get("url_after_click") or "row action", clicked=click_info.get("clicked"), row_hint=click_info.get("row_hint"), url_after_click=click_info.get("url_after_click"))
        add_edge(ui_node, click_node, "CLICKED_SAFE_ROW_ACTION", clicked=click_info.get("clicked"))
        for aidx, inter in enumerate((audit.get("api_interactions") or [])[:6], start=1):
            api_node = add_node(_kg_id("ui_row_api", inter.get("method"), inter.get("url"), idx, aidx), "API_INTERACTION", f"{inter.get('method','GET')} {inter.get('url','')}", order=aidx, method=inter.get("method"), url=inter.get("url"), status=inter.get("status"), doctype_rows_extracted=inter.get("doctype_rows_extracted"))
            add_edge(click_node, api_node, "ROW_ACTION_TRIGGERED_API", rows=inter.get("doctype_rows_extracted"))

    # UI/form path.
    add_click_node = add_node(_kg_id("ui_action", run_id, "click_add"), "UI_ACTION", "Click + Add Document Type", action="click_add_doctype_do_not_save", save_clicked=False)
    add_edge(run_node, add_click_node, "RUN_PERFORMED_SAFE_UI_ACTION")
    add_edge(add_click_node, safety_node, "ACTION_GUARDED_BY_SAFETY")

    for idx, field in enumerate(kb.get("form_controls") or [], start=1):
        field_node = add_node(_kg_id("form_field", field.get("selector") or idx), "FORM_FIELD", field.get("label") or field.get("name") or field.get("id") or f"field {idx}", order=idx, field_label=field.get("label"), selector=field.get("selector"), tag=field.get("tag"), type=field.get("type"), role=field.get("role"), required=field.get("required"), mapped_document_type_key=field.get("mapped_document_type_key"), recommended_value=field.get("recommended_value"), dom_events_to_try=field.get("dom_events_to_try"))
        add_edge(add_click_node, field_node, "ADD_FORM_CONTAINED_FIELD", required=field.get("required"), mapped_key=field.get("mapped_document_type_key"))
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
        "schema_version": "doctype_api_flow_kg_v1",
        "created_at": utc_now(),
        "run_id": run_id,
        "url": kb.get("url"),
        "debug_questions_supported": [
            "Which API was triggered when opening Document Types?",
            "Which API returned old Document Type IDs?",
            "Which pagination URLs were replayed?",
            "Which endpoint returned documentTypeId/documentTypeName/dataFormatType/transactionType/validationType?",
            "Which read-only detail lookup attempts were used to find numeric documentTypeId?",
            "Which real UI row actions were clicked to learn old Document Type IDs?",
            "Which fields/dropdowns appeared after + Add?",
            "Which DOM events are required to fill the Add Document Type form?",
            "Which dummy values were filled without saving?",
        ],
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "api_interactions": len(interactions),
            "old_doctypes": len(kb.get("old_doctypes_inventory") or []),
            "old_doctypes_with_numeric_id": (kb.get("detail_enrichment_report") or {}).get("document_type_ids_found"),
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


def _write_doctype_api_flow_knowledge_graph(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    graph = build_doctype_api_flow_knowledge_graph(kb)
    json_path = kb_dir / "doctype_api_flow_knowledge_graph.json"
    mmd_path = kb_dir / "doctype_api_flow_knowledge_graph.mmd"
    md_path = kb_dir / "doctype_api_flow_knowledge_graph.md"
    html_path = kb_dir / "doctype_api_flow_knowledge_graph.html"
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
        "# Document Type API Flow Knowledge Graph",
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

    html_doc = f"""<!doctype html><html><head><meta charset='utf-8'/>
<title>Document Type API Flow Knowledge Graph</title>
<script type='module'>import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs'; mermaid.initialize({{startOnLoad:true,securityLevel:'loose'}});</script>
<style>body{{font-family:Arial,sans-serif;margin:28px;color:#222}}.card{{border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0}}pre{{background:#f6f8fa;padding:12px;overflow:auto;max-height:520px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #ddd;padding:6px}}</style>
</head><body><h1>Document Type API Flow Knowledge Graph</h1>
<div class='card'><b>Run:</b> {html.escape(str(graph.get('run_id')))}<br/><b>URL:</b> {html.escape(str(graph.get('url')))}<br/><b>Nodes:</b> {len(graph.get('nodes', []))} <b>Edges:</b> {len(graph.get('edges', []))}</div>
<h2>Summary</h2><pre>{html.escape(json.dumps(summary, indent=2, ensure_ascii=False))}</pre>
<h2>Flow graph</h2><div class='mermaid'>{html.escape(mmd)}</div>
<h2>Node preview</h2><pre>{html.escape(json.dumps(graph.get('nodes', [])[:160], indent=2, ensure_ascii=False))}</pre>
<h2>Edge preview</h2><pre>{html.escape(json.dumps(graph.get('edges', [])[:220], indent=2, ensure_ascii=False))}</pre>
</body></html>"""
    html_path.write_text(html_doc, encoding="utf-8")
    return {
        "doctype_api_flow_kg_json": str(json_path),
        "doctype_api_flow_kg_mermaid": str(mmd_path),
        "doctype_api_flow_kg_markdown": str(md_path),
        "doctype_api_flow_kg_html": str(html_path),
    }


def _write_doctype_api_flow_knowledge_graph_safe(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    """Export the reporting Knowledge Graph without replaying a completed portal phase.

    Exact live form execution and independent verification are authoritative.
    Serializer/report failures are preserved as warning artifacts and never
    cause the agent to reopen and refill an already-correct unsaved form.
    """
    status_path = kb_dir / "doctype_api_flow_knowledge_graph_export_status.json"
    try:
        files = _write_doctype_api_flow_knowledge_graph(kb_dir, kb)
        status = {
            "status": "ok",
            "pass": True,
            "non_blocking": True,
            "run_id": kb.get("run_id"),
            "files": files,
        }
        status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {**files, "doctype_api_flow_kg_export_status": str(status_path)}
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
        return {"doctype_api_flow_kg_export_status": str(status_path)}


def _write_markdown(path: Path, kb: Dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pv = kb.get("previous_interaction_values", {})
    dm = (pv.get("document_type_values_to_fill") or {})
    lines = [
        "# HIP SecureLink Document Type Form KB",
        "",
        f"Run ID: `{kb.get('run_id')}`",
        f"URL: `{kb.get('url')}`",
        "",
        "## Safety",
        "This KB run opens `+ Add`, fills dummy values, captures form/dropdown/DOM evidence, and never clicks Save/Create/Submit.",
        "",
        "## Previous Document Type values from provided input",
        "| Field | Value |",
        "|---|---|",
    ]
    for k, v in dm.items():
        lines.append(f"| `{k}` | `{v}` |")
    lines += ["", "## Known/prior IDs", "| ID | Value |", "|---|---|"]
    for k, v in (pv.get("known_ids") or {}).items():
        lines.append(f"| `{k}` | `{v}` |")
    old_maps = kb.get("old_doctypes_inventory") or []
    id_report = kb.get("detail_enrichment_report") or {}
    lines += ["", "## Old Document Types learned from listing/detail APIs", f"Total old Document Type rows normalized: `{len(old_maps)}`", f"Numeric documentTypeIds found: `{id_report.get('document_type_ids_found', 0)}` / `{id_report.get('total_old_doctypes', len(old_maps))}`", f"ID completion: `{id_report.get('completion_percent', 0)}%`", "", "| documentTypeId | Document Type Name | Version | Status | Transaction Type | Data Format Type | Validation Type | Document Identifier | Root Element | File | Source |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in old_maps[:300]:
        lines.append(f"| `{row.get('document_type_id','')}` | `{row.get('document_type_name','')}` | `{row.get('document_type_version','')}` | `{row.get('status','')}` | `{row.get('transaction_type','')}` | `{row.get('format','')}` | `{row.get('validation_type','')}` | `{row.get('document_identifier','')}` | `{row.get('root_element','')}` | `{row.get('schema_file','')}` | `{row.get('source','')}` |")
    if len(old_maps) > 300:
        lines.append(f"| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | `{len(old_maps)-300} more rows in old_doctypes_inventory.csv` |")
    ui_report = kb.get("ui_row_action_report") or {}
    lines += ["", "## Old Document Type UI row/action ID learning", "This phase repeats the real portal interaction: search an old Document Type row, click its safe View/Edit/Details action, capture the API triggered by that click, and parse numeric `documentTypeId` when exposed.", "", "```json", json.dumps(ui_report, indent=2, ensure_ascii=False), "```"]
    api_interactions = kb.get("doctype_api_interactions") or []
    lines += ["", "## Document Type API interactions learned", "| Method | Status | URL | Rows extracted | Response shape |", "|---|---:|---|---:|---|"]
    for inter in api_interactions[:80]:
        shape = inter.get('response_shape') or {}
        lines.append(f"| `{inter.get('method','')}` | `{inter.get('status','')}` | `{inter.get('url','')}` | `{inter.get('doctype_rows_extracted',0)}` | `{shape.get('type','')} rows={shape.get('row_count',0)}` |")
    lines += ["", "## Required fields discovered", "| Label | Mapped key | Selector | Recommended dummy value |", "|---|---|---|---|"]
    for c in kb.get("required_fields", []):
        lines.append(f"| {c.get('label','')} | `{c.get('mapped_document_type_key','')}` | `{c.get('selector','')}` | `{c.get('recommended_value','')}` |")
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
    zip_path = run_dir / "UPLOAD_DOCTYPE_KB_SUMMARY.zip"
    include = [
        kb_dir / "doctype_form_kb.json",
        kb_dir / "old_doctypes_inventory.json",
        kb_dir / "old_doctypes_inventory.csv",
        kb_dir / "old_doctype_id_lookup_by_name.json",
        kb_dir / "doctype_api_interactions.json",
        kb_dir / "doctype_api_pagination_audit.json",
        kb_dir / "doctype_detail_enrichment_audit.json",
        kb_dir / "doctype_ui_row_action_enrichment_audit.json",
        kb_dir / "doctype_id_completion_report.json",
        kb_dir / "old_doctypes_inventory_with_ids.json",
        kb_dir / "old_doctypes_inventory_with_ids.csv",
        kb_dir / "doctype_listing_ui_rows.json",
        kb_dir / "doctype_previous_interaction_values.json",
        kb_dir / "doctype_dropdowns.json",
        kb_dir / "doctype_required_fields.json",
        kb_dir / "doctype_dummy_fill_plan.json",
        kb_dir / "doctype_dom_events.json",
        kb_dir / "doctype_api_flow_knowledge_graph.json",
        kb_dir / "doctype_api_flow_knowledge_graph.mmd",
        kb_dir / "doctype_api_flow_knowledge_graph.md",
        kb_dir / "doctype_api_flow_knowledge_graph.html",
        kb_dir / "DOCTYPE_KB_SUMMARY.md",
        kb_dir / "doctype_form_controls.csv",
        kb_dir / "compact_action_sequence.json",
        kb_dir / "compact_click_sequence.json",
        kb_dir / "compact_network_summary.json",
    ]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in include:
            if p.exists():
                z.write(p, arcname=str(p.relative_to(run_dir)))
    return str(zip_path)
