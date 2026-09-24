from __future__ import annotations

import asyncio
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
from .deterministic_plan_runtime import ordered_keys as deterministic_ordered_keys, annotate_attempt as annotate_plan_attempt, plan_summary as deterministic_plan_summary
from .stateful_form_runtime import compile_phase_state_graph, execute_phase_state_graph, build_target_branch_knowledge, capture_stateful_controls
from .upload_assets import attempt_upload_for_control, find_upload_asset, parse_accept_extensions
from .dds_control_driver import active_form_root_info, assert_active_surface, close_open_dropdown, get_active_form_root, set_text_control as dds_set_text_control, select_dds_combobox, set_boolean_control, upload_file_control, semantic_runtime_enabled, open_control_for_discovery
from .phase_form_entry import ensure_phase_form_entry
from .autonomous_form_runtime import execute_autonomous_phase_goal, autonomous_phase_enabled, autonomous_target_execution
from .hip_surface_ground_truth import DATA_MAP_LISTING_SIGNATURE, DATA_MAP_CREATE_SIGNATURE, SITE_GROUND_TRUTH_VERSION

DATAMAPS_URL = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"

DEFAULT_DUMMY_DATA_MAP = {
    "map_identifier": "DUMMY_DELLCoXMLASNXX08C_UHAUL_KB",
    "map_identifier_version": "1",
    "status": "Enable",
    "map_name": "DUMMY_DELLCoXMLASNXX08C_KB",
    "map_class": "Transform_DUMMY_DELLCoXMLASNXX08C_KB",
    "contivo_version": "6.7",
    "map_data_file": "DUMMY_Transform_DELLCoXMLASNXX08C.jar",
    # Optional schema-validation uploads. Leave blank unless input.json explicitly
    # supplies a portal-compatible .xsd/.json/.edi/.txt asset. Earlier builds
    # injected XML sample payloads here and created false "uploaded" passes.
    "input_schema_file": "",
    "output_schema_file": "",
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
    "[role='switch']",
    "[role='checkbox']",
    "[contenteditable='true']",
    ".dds__input input:not([type=hidden])",
    ".dds__textarea textarea",
]

SAVE_UNSAFE = {"save", "submit", "create", "update", "delete", "remove", "disable", "enable", "archive"}


@dataclass
class DataMapKBResult:
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


def extract_data_map_seed(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the Data Map values already known from user/manual input."""
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    dm = (objects or {}).get("data_map") or input_data.get("data_map") or {}
    result = dict(DEFAULT_DUMMY_DATA_MAP)
    if isinstance(dm, dict):
        for k in DEFAULT_DUMMY_DATA_MAP:
            if dm.get(k) not in (None, ""):
                result[k] = dm.get(k)
    return result


def build_previous_interaction_values(input_data: Dict[str, Any], *, known_map_id: str | None = None) -> Dict[str, Any]:
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    dm = extract_data_map_seed(input_data)
    source_dt = (objects or {}).get("source_document_type") or {}
    target_dt = (objects or {}).get("target_document_type") or {}
    rule = (objects or {}).get("rule") or {}
    return {
        "source": "uploaded_input_json_and_prior_manual_context",
        "captured_at": utc_now(),
        "known_ids": {
            "map_id": known_map_id or "UNKNOWN_FROM_CURRENT_DATAMAP_KB_RUN",
            "target_document_type_id": "10483",  # from prior API phase note; confirm before final create if DEV differs
        },
        "data_map_values_to_fill": dm,
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
    """Build fill values. In full replication mode, use exact input.json values.

    The command still never clicks Save/Create/Submit, so exact values are safe for
    learning/replication. Older dummy prefixes caused screenshots to mismatch the
    human-approved golden references.
    """
    values = dict(DEFAULT_DUMMY_DATA_MAP)
    for key, value in (seed or {}).items():
        if value not in (None, ""):
            values[key] = str(value)
    if not exact:
        # Make obviously dummy unless exact replication is requested.
        if not values.get("map_identifier", "").upper().startswith("DUMMY"):
            values["map_identifier"] = f"DUMMY_{values['map_identifier']}_KB"
        if not values.get("map_name", "").upper().startswith("DUMMY"):
            values["map_name"] = f"DUMMY_{values['map_name']}_KB"
        if not values.get("map_class", "").upper().startswith("TRANSFORM_DUMMY"):
            values["map_class"] = f"Transform_DUMMY_{_safe_name(values.get('map_name','MAP'))}"
    return {k: str(v) for k, v in values.items()}


def _version_equivalent(left: Any, right: Any) -> bool:
    try:
        return float(str(left or "").strip()) == float(str(right or "").strip())
    except Exception:
        return str(left or "").strip().lower() == str(right or "").strip().lower()


def find_existing_data_map_match(rows: Iterable[Dict[str, Any]], seed: Dict[str, Any]) -> Dict[str, Any]:
    """Find the intended Data Map in the read-only listing inventory.

    A match is strong only when identifier and version agree. Map name/class are
    additionally checked when both the input and listing expose them. This allows
    a no-save run to treat the create-form duplicate warning as an existing-object
    reuse decision rather than a failed attempt to create a second object.
    """
    wanted_id = str(seed.get("map_identifier") or "").strip().lower()
    wanted_ver = seed.get("map_identifier_version")
    if not wanted_id:
        return {"found": False, "reason": "map identifier is blank"}
    candidates: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("map_identifier") or "").strip().lower() != wanted_id:
            continue
        row_ver = row.get("map_identifier_version") or row.get("latest_dev_version")
        if wanted_ver not in (None, "") and row_ver not in (None, "") and not _version_equivalent(wanted_ver, row_ver):
            continue
        candidates.append(row)
    if not candidates:
        return {"found": False, "reason": "no exact identifier/version match in read-only inventory"}
    best = candidates[0]
    mismatches: List[Dict[str, Any]] = []
    for key in ("map_name", "map_class"):
        expected = str(seed.get(key) or "").strip()
        actual = str(best.get(key) or "").strip()
        if expected and actual and expected.lower() != actual.lower():
            mismatches.append({"field": key, "expected": expected, "actual": actual})
    return {
        "found": not mismatches,
        "mode": "reuse_existing" if not mismatches else "conflicting_existing_object",
        "natural_key": {"map_identifier": seed.get("map_identifier"), "version": seed.get("map_identifier_version")},
        "match": best,
        "conflicts": mismatches,
        "evidence": "read_only_datamap_listing_inventory",
    }


def guess_field_key(label: str, attrs: Dict[str, Any]) -> Optional[str]:
    text = " ".join(str(x or "") for x in [label, attrs.get("name"), attrs.get("id"), attrs.get("placeholder"), attrs.get("ariaLabel")]).lower()
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if "inputschema" in compact or "input schema" in text:
        return "input_schema_file"
    if "outputschema" in compact or "output schema" in text:
        return "output_schema_file"
    if "mapdata" in compact or "map data" in text:
        return "map_data_file"
    patterns = [
        # Specific labels must come before generic "version"; otherwise
        # "Contivo version" maps to map_identifier_version.
        ("contivo_version", ["contivo version", "contivo"]),
        ("map_identifier_version", ["identifier version", "map identifier version"]),
        ("map_identifier", ["map identifier", "identifier"]),
        ("status", ["status"]),
        ("map_name", ["map name", "name"]),
        ("map_class", ["map class", "class"]),
        ("map_data_file", ["map data file", "data file", "upload", "jar", "file"]),
    ]
    for key, terms in patterns:
        if any(t in text for t in terms):
            return key
    return None


def _filter_foreground_datamap_controls(controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only Add/Create Map drawer controls, not background grid/cookie controls."""
    keep=[]
    wanted = [
        "map identifier", "map identifier version", "status", "enabled", "disabled",
        "map name", "map class", "contivo", "mapdata", "map data", "inputschema",
        "input schema", "outputschema", "output schema", "cross reference"
    ]
    noise = ["search", "table search", "filter by column", "items per page", "page", "cookie", "marketing", "statistical", "uncategorized", "provide comment"]
    for c in controls or []:
        label = str(c.get("label") or c.get("name") or c.get("id") or "").strip()
        text = re.sub(r"\s+", " ", label).lower()
        nm = str(c.get("name") or "").lower()
        cid = str(c.get("id") or "").lower()
        if any(n in text for n in noise) or any(n in nm or n in cid for n in ["pagination", "ot-group", "vendor", "auditmessage"]):
            continue
        if str(c.get("type") or "").lower() == "file" or cid.startswith("file-input-control") or any(w in text for w in wanted):
            keep.append(c)
    return keep


async def _evaluate_controls(page: Page) -> List[Dict[str, Any]]:
    js = r"""
() => {
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
    return (label || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().replace(/\s+/g, ' ');
  }
  const els = Array.from(document.querySelectorAll("input:not([type=hidden]), textarea, select, [role='combobox'], [role='switch'], [role='checkbox'], [contenteditable='true']"));
  return els.map((el, idx) => {
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
      accept: el.getAttribute('accept') || '',
      multiple: !!el.multiple,
      ariaInvalid: el.getAttribute('aria-invalid') || '',
      required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\*/.test(labelFor(el))),
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      readonly: !!el.readOnly,
      value: el.value || el.getAttribute('value') || '',
      checked: (typeof el.checked === 'boolean' ? !!el.checked : el.getAttribute('aria-checked') === 'true'),
      componentTag: ((el.closest && el.closest('dds-switch, dds-checkbox, dds-select, dds-input')) || el).tagName ? (((el.closest && el.closest('dds-switch, dds-checkbox, dds-select, dds-input')) || el).tagName || '').toLowerCase() : '',
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
    const txt = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ');
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
    """Return the page-level Data Maps + Add structural opener.

    Data Maps opens Create Map in-page.  A navigation link whose href points to
    another route is never a valid Add candidate.  Prefer the visible top-right
    button and reject row/menu/form-local Add controls.
    """
    try:
        all_candidates = page.locator("button, [role=button], a")
        count = min(await all_candidates.count(), 250)
    except Exception:
        count = 0
        all_candidates = None

    best: tuple[float, Locator] | None = None
    for idx in range(count):
        loc = all_candidates.nth(idx)
        try:
            if not await loc.is_visible(timeout=350) or not await loc.is_enabled(timeout=350):
                continue
            meta = await loc.evaluate(r"""
el => {
  const text=(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();
  const aria=(el.getAttribute('aria-label')||'').trim();
  const title=(el.getAttribute('title')||'').trim();
  const tag=(el.tagName||'').toLowerCase();
  const href=tag==='a' ? (el.href||el.getAttribute('href')||'') : '';
  const r=el.getBoundingClientRect();
  const insideRow=!!el.closest('tbody tr,[role=row],[role=menu],[role=menuitem],.dds__menu,.dds__dropdown__list');
  const insideForm=!!el.closest('form,[role=dialog],dds-drawer,.dds__drawer,.dds__modal,.modal-dialog');
  const host=el.closest('dds-button');
  const ddsKind=(host&&host.getAttribute('kind'))||'';
  const ddsSize=(host&&host.getAttribute('size'))||'';
  const toolbar=el.closest('.dds-table__ribbon__action-bar,.dds-table-bulk-actions-bar,[class*=ribbon],[class*=action-bar]');
  const region=(toolbar&&(toolbar.getAttribute('class')||toolbar.getAttribute('aria-label')||''))||'';
  const vw=Math.max(document.documentElement.clientWidth||0, window.innerWidth||0, 1);
  const vh=Math.max(document.documentElement.clientHeight||0, window.innerHeight||0, 1);
  return {text,aria,title,tag,href,insideRow,insideForm,ddsHost:!!host,ddsKind,ddsSize,region,x:r.x,y:r.y,w:r.width,h:r.height,vw,vh};
}
""")
            labels=[str(meta.get(k) or '').strip() for k in ('text','aria','title')]
            exact=next((v for v in labels if re.fullmatch(r"\+?\s*Add", v, flags=re.I)), '')
            if not exact:
                continue
            if meta.get('insideRow') or meta.get('insideForm'):
                continue
            href=str(meta.get('href') or '').strip()
            if str(meta.get('tag') or '') == 'a' and href:
                try:
                    target=urlparse(href)
                    current=urlparse(str(page.url or ''))
                    target_path=(target.path or current.path or '/').rstrip('/') or '/'
                    current_path=(current.path or '/').rstrip('/') or '/'
                    # In-page links (# / query) are acceptable; a route-changing
                    # anchor is not the Data Maps structural opener.
                    if target.netloc and current.netloc and target.netloc.lower()!=current.netloc.lower():
                        continue
                    if target_path.lower()!=current_path.lower():
                        continue
                except Exception:
                    continue
            x=float(meta.get('x') or 0); y=float(meta.get('y') or 0)
            vw=max(float(meta.get('vw') or 1),1.0); vh=max(float(meta.get('vh') or 1),1.0)
            score=0.0
            if str(meta.get('tag') or '') == 'button': score += 80.0
            elif str(meta.get('tag') or '') != 'a': score += 55.0
            else: score += 20.0
            if exact.lstrip().startswith('+'): score += 25.0
            # Supervised Dell evidence: page Add is a small tertiary DDS button
            # in the listing action ribbon. These are ranking signals, not brittle requirements.
            if meta.get('ddsHost'): score += 18.0
            if str(meta.get('ddsKind') or '').lower() == 'tertiary': score += 14.0
            if str(meta.get('ddsSize') or '').lower() == 'sm': score += 8.0
            if str(meta.get('region') or ''): score += 12.0
            # Data Maps' page-level Add is at the top-right of the listing.
            score += 35.0 * max(0.0, min(1.0, x / vw))
            score += 20.0 * max(0.0, min(1.0, 1.0 - (y / vh)))
            if best is None or score > best[0]:
                best=(score, loc)
        except Exception:
            continue
    return best[1] if best is not None else None



async def _looks_like_datamap_add_form(page: Page, expected_listing_url: str | None = None) -> bool:
    """Prove the actual same-route Create Map drawer seen in Dell HIP.

    A row expansion is explicitly rejected even though it exposes many Data Map
    fields. The create surface must be a visible modal drawer/dialog titled
    ``Create Map`` with at least one create-form field and no existing-row action
    cluster (Edit/Clone/Migrate) masquerading as the form.
    """
    try:
        if expected_listing_url and not _urls_same_path(str(page.url or ''), expected_listing_url):
            return False
        # Lightweight mock/offline compatibility. Real Playwright pages always
        # have evaluate() and therefore use the stricter witnessed drawer proof below.
        if not hasattr(page, "evaluate"):
            info = await active_form_root_info(page, "data_map")
            text = re.sub(r"\s+", " ", str(info.get("text") or "")).strip().lower()
            markers = ["map identifier", "map name", "map class", "contivo version", "map data", "status"]
            marker_count = sum(1 for x in markers if x in text)
            creation_intent = any(x in text for x in ["create map", "add map", "new map", "map details", "mapping details"])
            return bool(int(info.get("controls") or 0) >= 2 and ((creation_intent and marker_count >= 1) or marker_count >= 2))
        proof = await page.evaluate(r"""
() => {
  function visible(el){if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)!==0);}
  const surfaces=Array.from(document.querySelectorAll('app-generic-drawer,dds-drawer,[role="dialog"],.dds__drawer,.dds__modal')).filter(visible);
  const rows=surfaces.map(el=>{
    const text=(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();
    const low=text.toLowerCase();
    const dialog=el.matches('[role="dialog"]')?el:el.querySelector('[role="dialog"]');
    const modal=String((dialog&&dialog.getAttribute('aria-modal'))||el.getAttribute('aria-modal')||'').toLowerCase()==='true';
    const names=['map identifier','map name','map class','contivo version','map data','status','input schema validation','output schema validation'];
    const markerCount=names.filter(x=>low.includes(x)).length;
    const controls=Array.from(el.querySelectorAll('input:not([type=hidden]),textarea,select,[role=combobox],[role=switch],input[type=file],button')).filter(visible).length;
    const existingActions=['edit','clone','migrate'].filter(x=>new RegExp('(^|\\s)'+x+'(\\s|$)','i').test(text)).length;
    const envTabs=['dev','test1','test2','prod'].filter(x=>new RegExp('(^|\\s)'+x+'(\\s|$)','i').test(text)).length;
    const creationIntent=['create map','add map','new map','map details','mapping details'].some(x=>low.includes(x)); const terminal=['submit','cancel','save'].filter(x=>new RegExp('(^|\\s)'+x+'(\\s|$)','i').test(text)).length; return {title:low.includes('create map'),creationIntent,modal,markerCount,controls,existingActions,envTabs,terminal,text:text.slice(0,1200)};
  });
  rows.sort((a,b)=>(Number(b.creationIntent)-Number(a.creationIntent))||(b.markerCount-a.markerCount)||(b.controls-a.controls));
  return rows[0]||null;
}
""")
        if not isinstance(proof, dict):
            return False
        if not proof.get("modal"):
            return False
        marker_count = int(proof.get("markerCount") or 0)
        creation_intent = bool(proof.get("creationIntent"))
        if int(proof.get("controls") or 0) < 2 or not ((creation_intent and marker_count >= 1) or marker_count >= 2):
            return False
        if int(proof.get("existingActions") or 0) >= 2 and int(proof.get("envTabs") or 0) >= 2:
            return False
        return True
    except Exception:
        return False

async def _collect_dropdown_options(page: Page, controls: List[Dict[str, Any]], max_dropdowns: int = 30) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for c in controls[:max_dropdowns]:
        tag = c.get("tag")
        role = c.get("role")
        ctype = c.get("type")
        label = c.get("label") or c.get("name") or c.get("id") or f"control_{c.get('index')}"
        if tag == "select":
            results.append({"label": label, "selector": c.get("selector"), "kind": "select", "options": c.get("options") or [], "dom_event": "change"})
            continue
        if role != "combobox" and "select" not in str(c.get("selector", "")).lower() and ctype not in {"search", "text"}:
            continue
        # Only open things that look like dropdowns, not arbitrary text inputs.
        text = " ".join(str(x or "") for x in [label, c.get("ariaLabel"), c.get("placeholder"), c.get("selector")]).lower()
        if any(k in text for k in ["filter", "search", "sort"]):
            continue
        if not any(k in text for k in ["status", "version", "contivo", "type", "format", "select", "dropdown"]):
            continue
        try:
            loc = page.locator(c.get("selector")).first
            if not await loc.is_visible(timeout=1000):
                continue
            if not await open_control_for_discovery(page, str(c.get("selector") or ""), label=str(label), phase="data_map"):
                continue
            await page.wait_for_timeout(350)
            opts = await page.evaluate(r"""
() => Array.from(document.querySelectorAll('[role=option], .dds__dropdown__item, .dds__select__option, .dds__list-item, li, mat-option')).map((el, idx) => {
  const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {x:0,y:0,width:0,height:0};
  const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
  return {index: idx, text, value: el.getAttribute('value') || el.getAttribute('data-value') || '', visible: !!(r.width && r.height)};
}).filter(x => x.visible && x.text).slice(0, 120)
""")
            await close_open_dropdown(page, "data_map")
            results.append({"label": label, "selector": c.get("selector"), "kind": "combobox", "options": mask_sensitive_data(opts or []), "dom_event": "click->listbox option->change", "close_strategy": "safe_form_blank_click_no_escape"})
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
    js = r"""
({selector, value}) => {
  const el = document.querySelector(selector);
  if (!el) return {ok:false, reason:'not found'};
  if (el.disabled || el.getAttribute('aria-disabled') === 'true' || el.readOnly) return {ok:false, reason:'disabled/readonly'};
  const tag = (el.tagName || '').toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  if (type === 'file') return {ok:false, reason:'file input skipped'};
  if (tag === 'select') {
    const opts = Array.from(el.options || []);
    const match = opts.find(o => (o.text || '').trim().toLowerCase() === String(value).trim().toLowerCase()) || opts.find(o => String(o.value).trim().toLowerCase() === String(value).trim().toLowerCase()) || opts.find(o => (o.text || '').toLowerCase().includes(String(value).toLowerCase()));
    if (match) el.value = match.value;
    else el.value = value;
  } else if (el.getAttribute('contenteditable') === 'true') {
    el.textContent = value;
  } else {
    el.focus();
    el.value = value;
  }
  for (const ev of ['input','change','blur']) el.dispatchEvent(new Event(ev, {bubbles:true}));
  return {ok:true};
}
"""
    try:
        result = await page.evaluate(js, {"selector": selector, "value": value})
        return bool(result and result.get("ok"))
    except Exception:
        return False


DATAMAP_URL_HINTS = ["datamap", "data-map", "data_maps", "maps", "mapping", "securelink"]
DATAMAP_FIELD_KEYS = {
    "map_id": ["mapId", "map_id", "mappingId", "dataMapId", "id"],
    "map_identifier": ["mapIdentifier", "map_identifier", "identifier", "mapIdentifierName", "mappingIdentifier"],
    "map_identifier_version": ["mapIdentifierVersion", "map_identifier_version", "identifierVersion", "version", "mapVersion", "latestDevVersion", "latest_dev_version"],
    "status": ["status", "mapStatus", "state", "active", "enabled"],
    "map_name": ["mapName", "map_name", "name", "mappingName"],
    "map_class": ["mapClass", "map_class", "mapClassName", "className", "transformClass", "transformerClass", "mappingClass"],
    "contivo_version": ["contivoVersion", "contivo_version", "contivo", "mapContivoVersion"],
    "map_data_file": ["mapDataFile", "map_data_file", "mapFileName", "fileName", "jarName", "mapFile", "file"],
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


def _contains_datamap_hint(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = text.lower()
    return any(h in text for h in DATAMAP_URL_HINTS)


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
    if any(k in payload for k in ["mapId", "mapIdentifier", "mapName", "mapClass", "dataMapId"]):
        yield payload
    for key in ["items", "content", "data", "records", "results", "result", "rows", "maps", "dataMaps", "datamaps", "payload"]:
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


def extract_datamap_record(row: Dict[str, Any], *, source_url: str = "", source: str = "api") -> Optional[Dict[str, Any]]:
    """Normalize one row into the old Data Map inventory schema.

    Generic `id` is accepted only when another map-specific field is present,
    preventing unrelated SecureLink rows from being saved as maps.
    """
    if not isinstance(row, dict):
        return None
    values = {field: _first_present(row, keys) for field, keys in DATAMAP_FIELD_KEYS.items()}
    has_map_specific = any(values.get(k) not in (None, "") for k in ["map_identifier", "map_name", "map_class", "contivo_version", "map_data_file"])
    if not has_map_specific and not _contains_datamap_hint(source_url):
        return None
    if values.get("map_id") in (None, "") and not has_map_specific:
        return None
    normalized = {
        "map_id": str(values.get("map_id") or ""),
        "map_identifier": str(values.get("map_identifier") or ""),
        "map_identifier_version": str(values.get("map_identifier_version") or ""),
        "status": str(values.get("status") or ""),
        "map_name": str(values.get("map_name") or ""),
        "map_class": str(values.get("map_class") or ""),
        "contivo_version": str(values.get("contivo_version") or ""),
        "map_data_file": str(values.get("map_data_file") or ""),
        "created_by": str(values.get("created_by") or ""),
        "updated_by": str(values.get("updated_by") or ""),
        "created_at": str(values.get("created_at") or ""),
        "updated_at": str(values.get("updated_at") or ""),
        "available_environments": str(values.get("available_environments") or ""),
        "latest_dev_version": str(_first_present(row, ["latestDevVersion", "latest_dev_version"]) or values.get("map_identifier_version") or ""),
        "source": source,
        "source_url": mask_sensitive_string(source_url),
        "raw_row_compact": mask_sensitive_data(row),
    }
    # Do not save completely empty records.
    if not any(normalized.get(k) for k in ["map_id", "map_identifier", "map_name", "map_class", "map_data_file"]):
        return None
    return normalized


def extract_datamap_records_from_payload(payload: Any, *, source_url: str = "", source: str = "api") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in _iter_json_candidate_rows(payload):
        rec = extract_datamap_record(row, source_url=source_url, source=source)
        if rec:
            rows.append(rec)
    return _dedupe_datamap_records(rows)


def _dedupe_datamap_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = str(row.get("map_id") or "").strip()
        if not key:
            key = "|".join(str(row.get(k) or "").strip().lower() for k in ["map_identifier", "map_identifier_version", "map_name", "map_class"])
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


def collect_datamap_api_interactions(events: Iterable[Any], *, stage_label: str = "unknown") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extract compact API learning evidence and old map rows from captured network events."""
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
        is_relevant = _contains_datamap_hint(url) or _contains_datamap_hint(payload) or _contains_datamap_hint(request_body)
        if not is_relevant:
            continue
        rows = extract_datamap_records_from_payload(payload, source_url=url, source=f"network:{stage_label}") if payload is not None else []
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
            "datamap_rows_extracted": len(rows),
            "sample_datamap_rows": rows[:3],
        }))
    return interactions, _dedupe_datamap_records(inventory)


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
        key = "|".join(str(item.get(k) or "") for k in ["stage", "method", "url", "status", "datamap_rows_extracted"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _build_datamap_lookup(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    lookup: Dict[str, Any] = {"by_map_id": {}, "by_map_identifier": {}, "by_map_name": {}, "by_map_class": {}}
    for row in records:
        compact = {k: row.get(k, "") for k in ["map_id", "map_identifier", "map_identifier_version", "latest_dev_version", "status", "map_name", "map_class", "contivo_version", "map_data_file", "available_environments", "source_url"]}
        for field, bucket in [("map_id", "by_map_id"), ("map_identifier", "by_map_identifier"), ("map_name", "by_map_name"), ("map_class", "by_map_class")]:
            val = str(row.get(field) or "").strip()
            if val:
                lookup[bucket].setdefault(val, []).append(compact)
    return lookup


async def _collect_ui_datamap_rows(page: Page) -> List[Dict[str, Any]]:
    """Fallback: collect visible list/table/card text from the Data Maps page."""
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
      const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
      if (!text || !r.width || !r.height) continue;
      if (!/(map|identifier|contivo|class|version|status|jar)/i.test(text)) continue;
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


async def _wait_for_datamap_listing_ready(page: Page, browser: BrowserSession, datamaps_url: str, warnings: List[str], *, attempts: int = 4) -> bool:
    """Wait until the Data Maps SPA has loaded enough to expose rows, Add, or the mac-map API.

    This specifically protects against a live Dell behavior where SSO lands on the
    correct URL, then an immediate second navigation aborts Angular chunks and
    leaves a blank page.  We only reload when the page is blank and no Data Map
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
        interactions, rows = collect_datamap_api_interactions(browser.network_tab_events, stage_label=f"readiness_attempt_{attempt}")
        if rows:
            return True
        try:
            ui_rows = await _collect_ui_datamap_rows(page)
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
        if len(body_text) > 80 and re.search(r"data\s*map|map\s*identifier|securelink", body_text, re.I):
            return True
        # Recover from a blank shell or aborted remoteEntry/chunk load.
        if attempt < attempts:
            browser.set_stage(f"datamap_kb_retry_blank_listing_{attempt}")
            try:
                if _urls_same_path(page.url, datamaps_url):
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                else:
                    await page.goto(datamaps_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                warnings.append(f"Data Map listing retry {attempt} navigation/reload failed: {exc}")
    warnings.append("Data Map listing page did not expose rows/Add/API after retries; using direct summary API fallback if authenticated.")
    return False


async def _direct_fetch_datamap_summary(page: Page, *, max_pages: int = 25) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch the known read-only Data Map summary API when the SPA/network capture misses it."""
    urls: List[str] = [
        "https://developer.dell.com/inaas-gateway/hipService-svc/api/mac-map/summary",
        "/inaas-gateway/hipService-svc/api/mac-map/summary",
    ]
    # Common pagination shapes; the bare endpoint currently returns all rows in DEV,
    # but keep these as fallback for future backend changes.
    for page_no in range(max(0, min(max_pages, 250))):
        urls.append(f"/inaas-gateway/hipService-svc/api/mac-map/summary?page={page_no}&size=100")
        urls.append(f"/inaas-gateway/hipService-svc/api/mac-map/summary?pageIndex={page_no}&pageSize=100")
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
        body, meta = await _fetch_json_with_page(page, url, headers={"Referer": DATAMAPS_URL})
        page_rows = extract_datamap_records_from_payload(body, source_url=url, source="direct_summary_api_fallback") if body is not None else []
        meta = mask_sensitive_data({**meta, "source": "direct_summary_api_fallback", "datamap_rows_extracted": len(page_rows)})
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
            "request_headers_compact": {"Referer": DATAMAPS_URL, "Accept": "application/json, text/plain, */*"},
            "request_body_redacted": "",
            "response_shape": _compact_payload_shape(body),
            "datamap_rows_extracted": len(page_rows),
            "sample_datamap_rows": page_rows[:3],
        }))
        if page_rows:
            rows.extend(page_rows)
            # The bare endpoint returning rows is enough; paginated calls can be redundant.
            if "?" not in url:
                break
        # Stop paginated replay when a paginated URL returns no rows after we already found data.
        if "?" in url and not page_rows and rows:
            break
    return _dedupe_datamap_records(rows), interactions, audit


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


async def _crawl_old_datamap_inventory_from_apis(page: Page, interactions: List[Dict[str, Any]], *, max_pages: int = 20) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
                page_rows = extract_datamap_records_from_payload(body, source_url=url, source="api_pagination_replay")
                rows.extend(page_rows)
                if not page_rows and meta.get("row_count") == 0:
                    # Likely exhausted.
                    break
    return _dedupe_datamap_records(rows), audit



def _merge_datamap_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge duplicate map rows while preferring records that include numeric mapId/details."""
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = str(row.get("map_identifier") or "").strip().lower()
        version = str(row.get("map_identifier_version") or row.get("latest_dev_version") or "").strip().lower()
        name = str(row.get("map_name") or "").strip().lower()
        klass = str(row.get("map_class") or "").strip().lower()
        key = "|".join([ident, version, name, klass]).strip("|")
        if not key:
            key = str(row.get("map_id") or "").strip().lower()
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


def _known_map_id_by_identifier(previous_values: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    dm = (previous_values or {}).get("data_map_values_to_fill") or {}
    known = (previous_values or {}).get("known_ids") or {}
    mid = known.get("map_id")
    ident = dm.get("map_identifier")
    if ident and mid and str(mid).upper() not in {"UNKNOWN", "UNKNOWN_FROM_CURRENT_DATAMAP_KB_RUN"}:
        out[str(ident).strip().lower()] = str(mid)
    return out


def _apply_known_map_ids(records: List[Dict[str, Any]], previous_values: Dict[str, Any]) -> List[Dict[str, Any]]:
    known = _known_map_id_by_identifier(previous_values)
    patched = []
    for row in records:
        item = dict(row)
        ident = str(item.get("map_identifier") or "").strip().lower()
        if ident in known and not item.get("map_id"):
            item["map_id"] = known[ident]
            item["map_id_source"] = "known_prior_context"
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
        if "/api/mac-map" in path:
            prefix = path.split("/api/mac-map", 1)[0] + "/api/mac-map"
            bases.append(urlunparse((parsed.scheme, parsed.netloc, prefix, "", "", "")))
        if "/api/" in path and ("map" in path.lower() or "securelink" in path.lower()):
            # Keep the observed collection endpoint too; some APIs accept identifier filters on the summary route.
            bases.append(urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")))
    bases.append("https://developer.dell.com/inaas-gateway/hipService-svc/api/mac-map")
    out: List[str] = []
    seen: set[str] = set()
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _detail_query_params(row: Dict[str, Any]) -> Dict[str, str]:
    ident = str(row.get("map_identifier") or "").strip()
    version = str(row.get("map_identifier_version") or row.get("latest_dev_version") or "").strip()
    envs = str(row.get("available_environments") or "DEV").split(",")
    env = (envs[0] if envs else "DEV").strip() or "DEV"
    params = {"mapIdentifier": ident, "mapName": str(row.get("map_name") or ""), "environment": env}
    if version:
        params["version"] = version
        params["mapIdentifierVersion"] = version
    return {k: v for k, v in params.items() if v}


def _candidate_datamap_detail_urls(row: Dict[str, Any], interactions: List[Dict[str, Any]]) -> List[str]:
    ident = str(row.get("map_identifier") or "").strip()
    if not ident:
        return []
    map_id = str(row.get("map_id") or "").strip()
    version = str(row.get("map_identifier_version") or row.get("latest_dev_version") or "").strip()
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
        add("/versions", {"mapIdentifier": ident})
        add("/version", {"mapIdentifier": ident})
        add("/" + quote(ident, safe=""))
        if version:
            add("/" + quote(ident, safe="") + "/" + quote(version, safe=""))
        if map_id:
            add("/" + quote(map_id, safe=""))
            add("/detail/" + quote(map_id, safe=""))
            add("/details/" + quote(map_id, safe=""))
    # Dedupe preserving order.
    out: List[str] = []
    seen: set[str] = set()
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out[:18]


def _record_matches_map(row: Dict[str, Any], target: Dict[str, Any]) -> bool:
    ident = str(target.get("map_identifier") or "").strip().lower()
    name = str(target.get("map_name") or "").strip().lower()
    klass = str(target.get("map_class") or "").strip().lower()
    if ident and str(row.get("map_identifier") or "").strip().lower() == ident:
        return True
    if name and str(row.get("map_name") or "").strip().lower() == name:
        return True
    if klass and str(row.get("map_class") or "").strip().lower() == klass:
        return True
    return False


async def _enrich_old_datamaps_with_detail_apis(
    page: Page,
    records: List[Dict[str, Any]],
    interactions: List[Dict[str, Any]],
    previous_values: Dict[str, Any],
    *,
    max_details: int = 250,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
    timeout_ms: int = 5000,
    max_candidate_urls_per_map: int = 4,
    probe_rows: int = 15,
    stop_if_probe_finds_no_ids: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Best-effort read-only detail enrichment to discover numeric map IDs.

    Bounded by design. The previous implementation could try many speculative
    URL shapes for every map, which can look like a hang when the Dell gateway
    slowly returns 404/500/timeout. This version writes checkpoints and stops
    the expensive detail phase when the first probe batch proves that the
    visible/listing APIs do not expose numeric mapId through read-only detail
    endpoints. It still preserves every old Data Map row from the listing.
    """
    enriched: List[Dict[str, Any]] = _apply_known_map_ids(records, previous_values)
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
    by_key = _merge_datamap_records(enriched)
    limit = max(0, int(max_details or 0))
    candidates_rows = by_key[:limit]
    baseline_ids = sum(1 for r in by_key if r.get("map_id"))

    async def emit_progress(detail: str) -> None:
        if progress_cb:
            maybe = progress_cb(
                phase="datamap_detail_id_enrichment",
                completed=processed,
                total=len(candidates_rows),
                detail=detail,
                counts={
                    "old_datamaps": len(by_key),
                    "map_ids_found": sum(1 for r in by_key if r.get("map_id")),
                    "detail_found_this_run": detail_found,
                },
            )
            if asyncio.iscoroutine(maybe):
                await maybe

    for row in candidates_rows:
        processed += 1
        await emit_progress(f"checking {processed}/{len(candidates_rows)} {row.get('map_identifier') or row.get('map_name') or ''}")
        if row.get("map_id"):
            continue
        # Probe mode: once enough rows have been tested and no detail IDs were found,
        # stop wasting time on the remaining 200+ maps. This keeps the run moving
        # and records the limitation clearly in datamap_id_completion_report.json.
        if stop_if_probe_finds_no_ids and processed > max(1, int(probe_rows or 0)) and detail_found == 0:
            skipped_after_probe = max(0, len(candidates_rows) - processed + 1)
            stop_reason = (
                f"Stopped detail enrichment after probe_rows={probe_rows}: no numeric mapId was exposed by "
                "the attempted read-only detail endpoints. Listing inventory was kept; only numeric IDs remain blank."
            )
            break
        candidates = _candidate_datamap_detail_urls(row, interactions)[: max(1, int(max_candidate_urls_per_map or 1))]
        row_audit = {
            "map_identifier": row.get("map_identifier"),
            "map_name": row.get("map_name"),
            "attempts": [],
            "resolved": False,
            "candidate_url_count_used": len(candidates),
        }
        for url in candidates:
            body, meta = await _fetch_json_with_page(page, url, headers=headers, timeout_ms=timeout_ms)
            meta = mask_sensitive_data({**meta, "source": "datamap_detail_id_enrichment"})
            recs = extract_datamap_records_from_payload(body, source_url=url, source="api_detail_enrichment") if body is not None else []
            matched = None
            for rec in recs:
                if _record_matches_map(rec, row):
                    matched = rec
                    break
            if matched is None and len(recs) == 1:
                matched = recs[0]
            meta["datamap_rows_extracted"] = len(recs)
            meta["matched"] = bool(matched)
            meta["map_id_found"] = bool(matched and matched.get("map_id"))
            row_audit["attempts"].append(meta)
            if matched:
                merged = _merge_datamap_records([row, matched])[0]
                row.update(merged)
                if matched.get("map_id"):
                    row["map_id_source"] = "api_detail_enrichment"
                    row_audit["resolved"] = True
                    detail_found += 1
                    break
        audit.append(row_audit)
        # Checkpoint every five rows so a stopped run still has useful output.
        if kb_dir and (processed % 5 == 0 or processed == len(candidates_rows)):
            try:
                _write_json(kb_dir / "old_datamaps_inventory_with_ids.checkpoint.json", by_key)
                _write_json(kb_dir / "datamap_detail_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    merged_records = _merge_datamap_records(by_key)
    with_ids = sum(1 for r in merged_records if r.get("map_id"))
    report = {
        "total_old_datamaps": len(merged_records),
        "map_ids_found": with_ids,
        "map_ids_missing": max(0, len(merged_records) - with_ids),
        "detail_enrichment_processed": processed,
        "detail_enrichment_found_ids": detail_found,
        "known_prior_ids_applied": sum(1 for r in merged_records if r.get("map_id_source") == "known_prior_context"),
        "baseline_ids_before_detail_enrichment": baseline_ids,
        "detail_timeout_ms": timeout_ms,
        "max_candidate_urls_per_map": max_candidate_urls_per_map,
        "probe_rows": probe_rows,
        "skipped_after_probe": skipped_after_probe,
        "stop_reason": stop_reason,
        "completion_percent": round((with_ids / len(merged_records) * 100), 2) if merged_records else 0.0,
        "note": "Blank map_id means the visible/listing API did not expose numeric mapId and no read-only detail endpoint returned it during this run.",
    }
    if kb_dir:
        try:
            _write_json(kb_dir / "old_datamaps_inventory_with_ids.checkpoint.json", merged_records)
            _write_json(kb_dir / "datamap_detail_enrichment_audit.checkpoint.json", audit)
            _write_json(kb_dir / "datamap_id_completion_report.checkpoint.json", report)
        except Exception:
            pass
    return merged_records, audit, report



def _extract_numeric_map_id_from_url_or_payload(value: Any) -> str:
    """Extract a plausible numeric Data Map id from a URL/payload.

    We intentionally only trust numeric ids when the surrounding text has a map/mac-map
    hint. This prevents unrelated page ids or timestamps from becoming mapId.
    """
    if value in (None, ""):
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    low = text.lower()
    if not any(h in low for h in ["mac-map", "datamap", "data-map", "mapid", "map_id", "mappingid", "mapidentifier"]):
        return ""
    patterns = [
        r"(?:mapId|map_id|dataMapId|mappingId)[\"'=:\s]+(\d{1,10})",
        r"/mac-map/(?:detail/|details/|edit/|view/)?(\d{1,10})(?:[/?#]|$)",
        r"[?&](?:mapId|id|dataMapId|mappingId)=(\d{1,10})(?:&|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


async def _find_datamap_listing_search(page: Page) -> Optional[Locator]:
    """Find the visible Data Maps list search box, avoiding Filter buttons."""
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


async def _set_datamap_listing_search(page: Page, value: str) -> bool:
    loc = await _find_datamap_listing_search(page)
    if loc is None:
        return False
    if semantic_runtime_enabled(page):
        try:
            selector = await loc.evaluate("el => el.id ? ('#' + CSS.escape(el.id)) : ''")
            if not selector:
                return False
            if not await dds_set_text_control(page, None, selector, value, phase="data_map"):
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


async def _clear_datamap_listing_search(page: Page) -> None:
    loc = await _find_datamap_listing_search(page)
    if loc is None:
        return
    if semantic_runtime_enabled(page):
        try:
            selector = await loc.evaluate("el => el.id ? ('#' + CSS.escape(el.id)) : ''")
            if not selector:
                return
            if not await dds_set_text_control(page, None, selector, "", phase="data_map"):
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


async def _click_safe_datamap_row_action(page: Page, row_hint: str) -> Dict[str, Any]:
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
    # In the live Data Maps grid the only row-scoped button may be labelled
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
                    if not await open_control_for_discovery(page, str(b.get("selector") or ""), label="Data Map row action", phase="data_map"):
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
                        if not await open_control_for_discovery(page, str(opt.get("selector") or ""), label="Data Map row menu action", phase="data_map"):
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


async def _learn_old_datamap_ids_from_ui_row_actions(
    page: Page,
    browser: BrowserSession,
    records: List[Dict[str, Any]],
    previous_values: Dict[str, Any],
    *,
    max_rows: int = 250,
    kb_dir: Path | None = None,
    progress_cb: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Repeat the manually validated UI approach for old Data Maps.

    This mirrors the Partner/System strategy: use the live UI row, click the safe row
    action, capture the API that the UI itself triggers, then parse IDs from URL/body.
    It never clicks Save/Create/Submit/Delete.  It is checkpointed so a stopped run
    still preserves useful IDs/evidence.
    """
    rows = _merge_datamap_records(_apply_known_map_ids(records, previous_values))
    limit = min(len(rows), max(0, int(max_rows or 0)))
    audit: List[Dict[str, Any]] = []
    ids_before = sum(1 for r in rows if r.get("map_id"))
    found_this_phase = 0

    async def emit(i: int, detail: str) -> None:
        if progress_cb:
            maybe = progress_cb(
                phase="datamap_ui_row_action_id_learning",
                completed=i,
                total=max(1, limit),
                detail=detail,
                counts={"old_datamaps": len(rows), "map_ids_found": sum(1 for r in rows if r.get("map_id")), "ui_ids_found": found_this_phase},
            )
            if asyncio.iscoroutine(maybe):
                await maybe

    if limit <= 0:
        return rows, audit, {"ui_rows_attempted": 0, "ui_ids_found": 0, "note": "No old Data Map rows available for UI row-action learning."}

    for idx, row in enumerate(rows[:limit], start=1):
        hint = str(row.get("map_identifier") or row.get("map_name") or row.get("map_class") or "").strip()
        if not hint:
            continue
        await emit(idx, f"UI row detail learning {idx}/{limit}: {hint[:80]}")
        if row.get("map_id") and row.get("map_id_source") == "known_prior_context":
            audit.append({"map_identifier": row.get("map_identifier"), "skipped": True, "reason": "already_has_known_map_id"})
            continue
        before_count = len(browser.network_tab_events)
        searched = await _set_datamap_listing_search(page, hint)
        click_info: Dict[str, Any] = {"clicked": False, "reason": "search_failed"}
        if searched:
            try:
                click_info = await asyncio.wait_for(_click_safe_datamap_row_action(page, hint), timeout=8)
            except Exception as exc:
                click_info = {"clicked": False, "reason": f"ui_click_error: {exc}", "row_hint": hint}
        await page.wait_for_timeout(900)
        new_events = browser.network_tab_events[before_count:]
        interactions, api_rows = collect_datamap_api_interactions(new_events, stage_label="datamap_ui_row_action_detail")
        matched = None
        for rec in api_rows:
            if _record_matches_map(rec, row):
                matched = rec
                break
        if matched is None and len(api_rows) == 1:
            matched = api_rows[0]
        url_map_id = _extract_numeric_map_id_from_url_or_payload(page.url)
        event_url_map_id = ""
        for ev in new_events:
            d = _event_dict(ev)
            event_url_map_id = _extract_numeric_map_id_from_url_or_payload(d.get("url")) or event_url_map_id
            event_url_map_id = _extract_numeric_map_id_from_url_or_payload(d.get("response_body_redacted") or d.get("response_body_text_redacted")) or event_url_map_id
            if event_url_map_id:
                break
        row_audit = {
            "map_identifier": row.get("map_identifier"),
            "map_name": row.get("map_name"),
            "search_used": searched,
            "click": mask_sensitive_data(click_info),
            "network_events_after_click": len(new_events),
            "api_interactions_after_click": len(interactions),
            "api_rows_after_click": len(api_rows),
            "url_after_click": mask_sensitive_string(page.url),
            "url_map_id_candidate": url_map_id,
            "event_map_id_candidate": event_url_map_id,
            "resolved": False,
        }
        if matched:
            merged = _merge_datamap_records([row, matched])[0]
            row.update(merged)
        if not row.get("map_id"):
            expanded_text = ""
            try:
                expanded_text = str((click_info or {}).get("expanded_snapshot") or "")
            except Exception:
                expanded_text = ""
            expanded_map_id = _extract_numeric_map_id_from_url_or_payload(expanded_text)
            candidate = event_url_map_id or url_map_id or expanded_map_id
            if candidate:
                row["map_id"] = candidate
                row["map_id_source"] = "ui_row_action_network_or_url_or_expanded_dom"
            if expanded_text:
                row["ui_expanded_detail_text_compact"] = expanded_text[:1200]
        if row.get("map_id") and not row_audit["resolved"]:
            row_audit["resolved"] = True
            if str(row.get("map_id_source") or "").startswith("ui_row_action") or (matched and matched.get("map_id")):
                found_this_phase += 1
        row_audit["api_interactions"] = interactions[:10]
        audit.append(row_audit)
        # Return to the list and clear search so the next row starts clean.
        try:
            await close_open_dropdown(page, "data_map")
            await page.wait_for_timeout(250)
            if not _urls_same_path(page.url, DATAMAPS_URL):
                await page.go_back(timeout=4000)
                await page.wait_for_timeout(800)
        except Exception:
            pass
        await _clear_datamap_listing_search(page)
        if kb_dir and (idx % 5 == 0 or idx == limit):
            try:
                _write_json(kb_dir / "old_datamaps_inventory_with_ids.ui_checkpoint.json", rows)
                _write_json(kb_dir / "datamap_ui_row_action_enrichment_audit.checkpoint.json", audit)
            except Exception:
                pass

    rows = _merge_datamap_records(rows)
    with_ids = sum(1 for r in rows if r.get("map_id"))
    report = {
        "ui_rows_attempted": limit,
        "ui_ids_found_this_phase": found_this_phase,
        "map_ids_before_ui_phase": ids_before,
        "map_ids_after_ui_phase": with_ids,
        "map_ids_missing_after_ui_phase": max(0, len(rows) - with_ids),
        "completion_percent_after_ui_phase": round((with_ids / len(rows) * 100), 2) if rows else 0.0,
        "note": "UI row-action phase repeats the real portal interaction: search each old map, click safe View/Edit/Details/open row action, capture the API triggered by that click, and parse mapId when the API/URL exposes it.",
    }
    return rows, audit, report


class DataMapKBFlow:
    def __init__(self, config: AppConfig, *, datamaps_url: str = DATAMAPS_URL, fill_dummy: bool = True, known_map_id: str | None = None, write_heavy_evidence: bool = False, crawl_old_datamaps: bool = True, max_api_pages: int = 25, max_detail_rows: int | None = None):
        self.config = config
        self.datamaps_url = datamaps_url
        self.fill_dummy = fill_dummy
        self.known_map_id = known_map_id
        self.write_heavy_evidence = write_heavy_evidence
        self.crawl_old_datamaps = crawl_old_datamaps
        self.max_api_pages = max_api_pages
        self.max_detail_rows = max_detail_rows if max_detail_rows is not None else max_api_pages

    async def run(self, ctx: RunContext, input_json: str | None = None, *, browser_session: BrowserSession | None = None) -> Dict[str, Any]:
        run_dir = ctx.run_dir
        kb_dir = run_dir / "datamap_kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        input_data = _read_json(input_json)
        previous_values = build_previous_interaction_values(input_data, known_map_id=self.known_map_id)
        seed = extract_data_map_seed(input_data)
        dummy_values = build_dummy_fill_values(seed, exact=bool(input_data.get("_replicate_exact_input_values")))
        warnings: List[str] = []
        status = "completed"
        files: Dict[str, str] = {}
        existing_object_resolution: Dict[str, Any] = {"found": False, "mode": "create_no_save"}

        def progress(phase: str, completed: int = 0, total: int = 0, detail: str = "", counts: Optional[Dict[str, Any]] = None) -> None:
            _write_datamap_progress(kb_dir, phase=phase, completed=completed, total=total, detail=detail, counts=counts)

        progress("initializing", 0, 8, "Preparing browser, SSO, Data Map API capture and output folders")
        async with browser_session_scope(self.config, run_dir, existing=browser_session, phase_name=run_dir.name) as browser:
            page = await browser.start() if browser.page is None else browser.page
            browser.set_stage("datamap_kb_login")
            progress("login", 1, 8, "Opening browser and waiting for Dell SSO/Data Maps page")
            self.config.portal.base_url = self.datamaps_url
            await browser.goto_base_and_complete_sso(self.datamaps_url)
            browser.set_stage("datamap_kb_open_datamaps")
            progress("open_datamaps", 2, 8, "Opening Data Maps link and waiting for page/API readiness")
            # Do not blindly re-navigate when SSO already landed on Data Maps.
            # A second immediate navigation can abort Angular remoteEntry/chunk loading and leave a blank page.
            if not _urls_same_path(page.url, self.datamaps_url):
                await browser.navigate(self.datamaps_url)
            else:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
            ready = await _wait_for_datamap_listing_ready(page, browser, self.datamaps_url, warnings)
            progress("datamap_page_ready", 3, 8, f"Data Maps ready={ready}; capturing listing/API evidence")
            await browser.save_dom_snapshot("datamaps_listing_before_add")
            await page.wait_for_timeout(1000)
            add_api_interactions: List[Dict[str, Any]] = []
            listing_buttons = await _evaluate_buttons(page)
            listing_ui_rows = await _collect_ui_datamap_rows(page)
            listing_api_interactions, old_datamaps = collect_datamap_api_interactions(browser.network_tab_events, stage_label="datamap_listing_load")
            if self.crawl_old_datamaps and not old_datamaps:
                browser.set_stage("datamap_kb_direct_summary_api_fallback")
                progress("direct_summary_api_fallback", 0, max(1, self.max_api_pages), "No list rows from network yet; calling read-only Data Map summary API")
                direct_rows, direct_interactions, direct_audit = await _direct_fetch_datamap_summary(page, max_pages=self.max_api_pages)
                if direct_interactions:
                    listing_api_interactions.extend(direct_interactions)
                    old_datamaps = _merge_datamap_records([*old_datamaps, *direct_rows])
                    # Preserve these calls in the pagination audit so failures are visible in the upload summary.
                    pagination_audit_fallback_seed = direct_audit
                else:
                    pagination_audit_fallback_seed = []
            else:
                pagination_audit_fallback_seed = []
            pagination_audit: List[Dict[str, Any]] = list(pagination_audit_fallback_seed)
            paginated_datamaps: List[Dict[str, Any]] = []
            detail_enrichment_audit: List[Dict[str, Any]] = []
            detail_enrichment_report: Dict[str, Any] = {}
            ui_row_action_audit: List[Dict[str, Any]] = []
            ui_row_action_report: Dict[str, Any] = {}
            if self.crawl_old_datamaps and listing_api_interactions:
                browser.set_stage("datamap_kb_old_datamap_api_pagination")
                progress("old_datamap_api_pagination", 0, max(1, self.max_api_pages), f"Replaying observed listing/pagination APIs; rows={len(old_datamaps)}")
                paginated_datamaps, replay_audit = await _crawl_old_datamap_inventory_from_apis(page, listing_api_interactions, max_pages=self.max_api_pages)
                pagination_audit.extend(replay_audit)
                old_datamaps = _merge_datamap_records([*old_datamaps, *paginated_datamaps])
                browser.set_stage("datamap_kb_old_datamap_detail_id_enrichment")
                progress("datamap_detail_id_enrichment", 0, max(1, min(len(old_datamaps), int(self.max_detail_rows or 0))), f"Trying bounded read-only detail lookups for numeric mapIds; old_maps={len(old_datamaps)}")
                old_datamaps, detail_enrichment_audit, detail_enrichment_report = await _enrich_old_datamaps_with_detail_apis(
                    page, old_datamaps, listing_api_interactions, previous_values, max_details=self.max_detail_rows, kb_dir=kb_dir, progress_cb=progress
                )
                progress("datamap_detail_id_enrichment_done", min(len(old_datamaps), int(self.max_detail_rows or 0)), max(1, min(len(old_datamaps), int(self.max_detail_rows or 0))), f"Detail enrichment done; mapIds={detail_enrichment_report.get('map_ids_found', 0)}/{detail_enrichment_report.get('total_old_datamaps', len(old_datamaps))}")
                # Second phase: repeat the real UI row/action detail flow, like Partner/System discovery.
                # This is the important phase for portals where the list API hides numeric mapId.
                browser.set_stage("datamap_kb_ui_row_action_id_learning")
                max_ui_rows = min(len(old_datamaps), int(self.max_detail_rows or len(old_datamaps)))
                progress("datamap_ui_row_action_id_learning", 0, max(1, max_ui_rows), "Learning old Data Map IDs from real UI row actions/details")
                old_datamaps, ui_row_action_audit, ui_row_action_report = await _learn_old_datamap_ids_from_ui_row_actions(
                    page, browser, old_datamaps, previous_values, max_rows=max_ui_rows, kb_dir=kb_dir, progress_cb=progress
                )
                progress("datamap_ui_row_action_id_learning_done", max_ui_rows, max(1, max_ui_rows), f"UI row-action ID learning done; mapIds={ui_row_action_report.get('map_ids_after_ui_phase', 0)}/{len(old_datamaps)}")
                # Merge both API and UI completion reports into the main report used by summaries.
                with_ids_after_ui = sum(1 for r in old_datamaps if r.get("map_id"))
                detail_enrichment_report.update({
                    "ui_row_action_phase": ui_row_action_report,
                    "map_ids_found": with_ids_after_ui,
                    "map_ids_missing": max(0, len(old_datamaps) - with_ids_after_ui),
                    "completion_percent": round((with_ids_after_ui / len(old_datamaps) * 100), 2) if old_datamaps else 0.0,
                })
            else:
                old_datamaps = _apply_known_map_ids(old_datamaps, previous_values)
            # Resolve an exact pre-existing object before opening +Add. The no-save
            # exploration may still open the create form to learn controls, but a
            # duplicate warning is then classified as reuse evidence, not a create
            # failure, provided no other validation error remains.
            existing_object_resolution = find_existing_data_map_match(old_datamaps, seed)
            if existing_object_resolution.get("found"):
                progress("existing_object_resolved", 4, 8, "Exact Data Map already exists; downstream phases will reuse it while the create form is explored without saving")
            # v2.2.7: Data Maps Create Map is an IN-PAGE drawer/form, not a
            # separate create route.  There is exactly one governed opener
            # transaction here; do not pre-click an arbitrary Add before the
            # ReAct controller owns selection/effect verification.
            progress("find_add_button", 4, 8, "Finding the page-level top-right + Add; Create Map must open in-page")
            before_add_event_count = len(browser.network_tab_events)

            async def _datamap_form_open(current_page: Page) -> bool:
                return await _looks_like_datamap_add_form(current_page, self.datamaps_url)

            async def _click_datamap_page_add(add_locator: Locator, step_no: int) -> bool:
                browser.set_stage("datamap_kb_click_add")
                progress("click_add_button", 5, 8, f"Opening in-page Create Map via top-right + Add (attempt {step_no}); no navigation/create/save")
                await browser.click_and_wait(
                    action="structural_opener click_add_datamap_do_not_save",
                    locator=add_locator, selector="Data Maps top-right + Add", mutation_risk=False,
                )
                return True

            entry_audit = await ensure_phase_form_entry(
                page=page, browser=browser, phase="data_map", listing_url=self.datamaps_url,
                find_add=_find_add_button, is_form_open=_datamap_form_open,
                click_add=_click_datamap_page_add, evidence_dir=kb_dir, max_steps=4,
                require_same_route=True,
            )
            add_api_interactions, add_api_datamaps = collect_datamap_api_interactions(
                browser.network_tab_events[before_add_event_count:], stage_label="datamap_add_in_page"
            )
            old_datamaps = _merge_datamap_records([*old_datamaps, *add_api_datamaps])
            await browser.save_dom_snapshot("datamap_in_page_create_form_opened")
            files["phase_form_entry_react_json"] = str(kb_dir / "phase_form_entry_react.json")
            form_visible_for_capture = bool(entry_audit.get("pass") and await _datamap_form_open(page))
            if entry_audit.get("pass"):
                status = "completed"
            browser.set_stage("datamap_kb_capture_form")
            progress("capture_add_form", 6, 8, "Capturing Add Data Map form controls/dropdowns/required fields")
            repeatable_row_audit = []
            if form_visible_for_capture and self.fill_dummy:
                try:
                    repeatable_row_audit = [await apply_repeatable_row_adds(page, input_data, "data_map")]
                except Exception as exc:
                    warnings.append(f"Repeatable row Add planning skipped: {mask_sensitive_string(str(exc))}")
            controls = _filter_foreground_datamap_controls(await _evaluate_controls(page)) if form_visible_for_capture else []
            stateful_controls = await capture_stateful_controls(page, "data_map") if form_visible_for_capture else []
            if stateful_controls:
                seen_selectors = {str(c.get("selector") or "") for c in controls if isinstance(c, dict)}
                controls.extend(dict(c) for c in stateful_controls if str(c.get("selector") or "") not in seen_selectors)
            buttons = await _evaluate_buttons(page) if form_visible_for_capture else []
            dropdowns = await _collect_dropdown_options(page, controls) if form_visible_for_capture else []
            file_input_contracts: List[Dict[str, Any]] = []
            for row in controls:
                attrs = {k: row.get(k) for k in ["name", "id", "placeholder", "ariaLabel"]}
                mapped = guess_field_key(row.get("label") or "", attrs)
                if str(row.get("type") or "").lower() == "file" or mapped in {"map_data_file", "input_schema_file", "output_schema_file"}:
                    file_input_contracts.append({
                        "field": mapped,
                        "label": row.get("label") or row.get("name") or "",
                        "accept_raw": row.get("accept") or "",
                        "accepted_extensions": parse_accept_extensions(row.get("accept")),
                        "required": bool(row.get("required")),
                        "selection_policy": "explicit_input_only" if not row.get("required") else "required_compatible_asset",
                        "source": "live_create_map_form",
                    })
            # Same-run plan overlay: newly observed file contracts are consumed by
            # the upload executor immediately and persisted for Portal Brain/KB.
            input_data.setdefault("_runtime_plan_overlay", {})["data_map_file_input_contracts"] = file_input_contracts
            # Learn the current business path first. Empty-form branch crawling is
            # deferred because parent selections can reveal or replace child
            # controls. The final explored form must be restored from the same
            # deterministic target graph before phase evidence is accepted.
            exploration_knowledge: Dict[str, Any] = {
                "status": "deferred_until_target_branch_committed",
                "learning_order": "current input target branch first",
            }
            fill_attempts: List[Dict[str, Any]] = []
            target_branch_execution: Dict[str, Any] = {}
            target_branch_knowledge: Dict[str, Any] = {}
            autonomous_execution: Dict[str, Any] = {}
            state_graph: Dict[str, Any] = compile_phase_state_graph(input_data, "data_map")
            required_fields: List[Dict[str, Any]] = []
            for c in controls:
                attrs = {k: c.get(k) for k in ["name", "id", "placeholder", "ariaLabel"]}
                key = guess_field_key(c.get("label") or "", attrs)
                c["mapped_data_map_key"] = key
                c["recommended_value"] = dummy_values.get(key or "", "")
                if c.get("required"):
                    required_fields.append(c)
            if self.fill_dummy and state_graph.get("nodes"):
                browser.set_stage("datamap_kb_fill_dummy_no_save")
                progress("fill_dummy_no_save", 0, len(controls), "Filling dummy values only; Save/Create/Submit blocked")
                # V230: goal-driven autonomous execution.  The live form is re-observed
                # every cycle; there is no Data Map-specific selector/coordinate script and
                # no fixed field-click order.  Canonical field names come from input.json /
                # the API business contract, while the current DDS/Angular surface decides
                # how each control is located and operated.
                progress(
                    "autonomous_goal_execution", 0, max(1, len(state_graph.get("nodes") or [])),
                    "AutoWebGLM live observation + adaptive semantic binding + PyAutoGUI/Playwright execution; final Submit remains blocked",
                )
                autonomous_cfg = getattr(self.config, "autonomous_form", None)
                autonomous_enabled = autonomous_phase_enabled(self.config, "data_map")
                if autonomous_enabled:
                    autonomous_execution = await execute_autonomous_phase_goal(
                        page=page, graph=state_graph, phase="data_map", input_data=input_data,
                        config=self.config, output_dir=kb_dir / "autonomous_form_runtime",
                        prior_attempts=fill_attempts,
                        max_cycles=int(getattr(autonomous_cfg, "max_adaptive_cycles", 4) or 4),
                        repair=True, strict_live_execution=True,
                    )
                else:
                    deterministic_result = await execute_phase_state_graph(
                        page, state_graph, phase="data_map", max_retries=2, repair=True,
                        prior_attempts=fill_attempts, strict_live_execution=True,
                    )
                    autonomous_execution = {
                        "schema_version": "hip.autonomous-form-runtime.v1",
                        "phase": "data_map", "status": "disabled_fallback",
                        "pass": bool(deterministic_result.get("pass")),
                        "goal_driven": False, "adaptive": False,
                        "final_execution": deterministic_result,
                        "prior_attempts": list(deterministic_result.get("attempts") or []),
                    }
                _write_json(kb_dir / "datamap_autonomous_form_execution.json", autonomous_execution)
                fill_attempts = [
                    dict(a) for a in (autonomous_execution.get("prior_attempts") or [])
                    if isinstance(a, dict)
                ]
                target_branch_execution = autonomous_target_execution(autonomous_execution)
                _write_json(kb_dir / "datamap_target_branch_execution.json", target_branch_execution)
                if not autonomous_execution.get("pass") or not target_branch_execution.get("pass"):
                    summary = target_branch_execution.get("autonomous_failure_summary") or {
                        "reason": autonomous_execution.get("reason"),
                        "failed_attempts": (target_branch_execution.get("failed_attempts") or [])[:8],
                    }
                    raise RuntimeError(
                        "Data Map autonomous goal was not proven: "
                        + mask_sensitive_string(json.dumps(summary, ensure_ascii=False, default=str))
                    )
                progress(
                    "autonomous_goal_execution", len(state_graph.get("nodes") or []),
                    max(1, len(state_graph.get("nodes") or [])),
                    "Data Map target state achieved with authoritative physical interaction and exact read-back",
                )

                portal_form_dir = kb_dir.parent.parent / "portal_form_knowledge"
                portal_form_dir.mkdir(parents=True, exist_ok=True)
                target_branch_knowledge = build_target_branch_knowledge(state_graph, target_branch_execution)
                target_knowledge_file = portal_form_dir / "data_map_target_branch_form_knowledge.json"
                target_branch_knowledge["knowledge_file"] = str(target_knowledge_file)
                safe_write_json(target_knowledge_file, target_branch_knowledge)

                # Alternative parents/options are explored only after the requested
                # path is known to work. The deterministic graph then restores and
                # re-verifies the target path before final evidence.
                try:
                    live_controls = _filter_foreground_datamap_controls(await _evaluate_controls(page))
                    live_buttons = await _evaluate_buttons(page)
                    live_dropdowns = await _collect_dropdown_options(page, live_controls)
                    exploration_knowledge = await run_portal_form_exploration(
                        page=page, phase="data_map", section="Create Data Map", input_data=input_data,
                        controls=live_controls, dropdowns=live_dropdowns, buttons=live_buttons,
                        repeatable_plan=build_repeatable_section_plan(input_data, "data_map"),
                        repeatable_audit=repeatable_row_audit, output_dir=portal_form_dir, config=self.config,
                    )
                    if exploration_knowledge.get("restore_errors") or str(exploration_knowledge.get("status") or "").startswith("failed"):
                        raise RuntimeError("Data Map exploration could not restore every changed parent")
                    restored_execution = await execute_phase_state_graph(
                        page, state_graph, phase="data_map", max_retries=2, repair=True, prior_attempts=fill_attempts, strict_live_execution=True
                    )
                    _write_json(kb_dir / "datamap_post_exploration_restore_execution.json", restored_execution)
                    if not restored_execution.get("pass"):
                        raise RuntimeError("Data Map target path failed after exploratory branches")
                    fill_attempts.extend(
                        dict(a, execution_stage="post_exploration_restore")
                        for a in restored_execution.get("attempts", []) if isinstance(a, dict)
                    )
                    target_branch_execution = restored_execution
                    target_branch_knowledge = build_target_branch_knowledge(state_graph, restored_execution)
                    target_branch_knowledge["knowledge_file"] = str(target_knowledge_file)
                    safe_write_json(target_knowledge_file, target_branch_knowledge)
                    exploration_knowledge["learning_order"] = "target branch first; alternatives second; deterministic target restore last"
                except Exception as exc:
                    exploration_knowledge = {"status": "failed_closed", "reason": mask_sensitive_string(str(exc))}
                    raise

                final_gate = await assert_active_surface(page, "data_map")
                if final_gate.get("fatal"):
                    raise RuntimeError("Create Map surface lost before final evidence: " + "; ".join(final_gate.get("fatal") or []))
                await browser.save_dom_snapshot("datamap_add_form_after_dummy_fill_no_save")
                try:
                    await browser.screenshot(kb_dir / "datamap_add_form_after_dummy_fill_no_save.png", full_page=True)
                except Exception:
                    pass
            browser.set_stage("datamap_kb_summarize")
            progress("summarize_and_write_outputs", 7, 8, "Writing compact KB, checkpoints and upload zip")
            network_api_interactions, all_api_datamaps = collect_datamap_api_interactions(browser.network_tab_events, stage_label="full_datamap_run")
            # Include direct fallback/pagination learned APIs in the final API KB, not only raw Network events.
            all_api_interactions = _dedupe_api_interactions([*listing_api_interactions, *add_api_interactions, *network_api_interactions])
            old_datamaps = _merge_datamap_records(_apply_known_map_ids([*old_datamaps, *all_api_datamaps], previous_values))
            if not detail_enrichment_report:
                with_ids = sum(1 for r in old_datamaps if r.get("map_id"))
                detail_enrichment_report = {"total_old_datamaps": len(old_datamaps), "map_ids_found": with_ids, "map_ids_missing": max(0, len(old_datamaps)-with_ids), "completion_percent": round((with_ids/len(old_datamaps)*100), 2) if old_datamaps else 0.0}
            datamap_lookup = _build_datamap_lookup(old_datamaps)
            network_relevant = []
            for e in browser.network_tab_events[-200:]:
                payload = e.model_dump() if hasattr(e, "model_dump") else getattr(e, "__dict__", {})
                url = str(payload.get("url") or "")
                if any(k in url.lower() for k in ["datamap", "data-map", "maps", "securelink"]):
                    payload.pop("response_body_text_redacted", None)
                    network_relevant.append(mask_sensitive_data(payload))
            kb = {
                "run_id": ctx.run_id,
                "captured_at": utc_now(),
                "url": self.datamaps_url,
                "safety": {
                    "save_clicked": False,
                    "create_clicked": False,
                    "submit_clicked": False,
                    "note": "The flow opens + Add and fills disposable dummy values only. It never clicks Save/Create/Submit.",
                },
                "previous_interaction_values": previous_values,
                "dummy_fill_values": dummy_values,
                "existing_object_resolution": existing_object_resolution,
                "deterministic_plan_runtime": deterministic_plan_summary(input_data),
                "old_datamaps_inventory": old_datamaps,
                "old_datamap_id_lookup_by_name": datamap_lookup,
                "datamap_api_interactions": all_api_interactions,
                "listing_api_interactions": listing_api_interactions,
                "add_api_interactions": add_api_interactions,
                "pagination_replay_audit": pagination_audit,
                "detail_enrichment_audit": detail_enrichment_audit,
                "ui_row_action_enrichment_audit": ui_row_action_audit,
                "detail_enrichment_report": detail_enrichment_report,
                "ui_row_action_report": ui_row_action_report,
                "listing_ui_rows": listing_ui_rows,
                "listing_buttons_before_add": listing_buttons,
                "form_controls": controls,
                "file_input_contracts": file_input_contracts,
                "required_fields": required_fields,
                "dropdowns": dropdowns,
                "buttons_after_add": buttons,
                "dummy_fill_attempts": fill_attempts,
                "stateful_target_branch_graph": state_graph,
                "stateful_target_branch_execution": target_branch_execution,
                "autonomous_form_execution": autonomous_execution,
                "target_branch_knowledge": target_branch_knowledge,
                "repeatable_section_plan": build_repeatable_section_plan(input_data, "data_map"),
                "repeatable_row_audit": repeatable_row_audit,
                "portal_form_exploration": exploration_knowledge,
                "network_events_relevant_compact": network_relevant,
                "warnings": warnings,
            }
            files["datamap_form_kb_json"] = _write_json(kb_dir / "datamap_form_kb.json", kb)
            files["old_datamaps_inventory_json"] = _write_json(kb_dir / "old_datamaps_inventory.json", old_datamaps)
            files["old_datamap_id_lookup_by_name_json"] = _write_json(kb_dir / "old_datamap_id_lookup_by_name.json", datamap_lookup)
            files["datamap_api_interactions_json"] = _write_json(kb_dir / "datamap_api_interactions.json", all_api_interactions)
            files["datamap_api_pagination_audit_json"] = _write_json(kb_dir / "datamap_api_pagination_audit.json", pagination_audit)
            files["datamap_detail_enrichment_audit_json"] = _write_json(kb_dir / "datamap_detail_enrichment_audit.json", detail_enrichment_audit)
            files["datamap_ui_row_action_enrichment_audit_json"] = _write_json(kb_dir / "datamap_ui_row_action_enrichment_audit.json", ui_row_action_audit)
            files["datamap_id_completion_report_json"] = _write_json(kb_dir / "datamap_id_completion_report.json", detail_enrichment_report)
            files["old_datamaps_inventory_with_ids_json"] = _write_json(kb_dir / "old_datamaps_inventory_with_ids.json", old_datamaps)
            files["old_datamaps_inventory_with_ids_csv"] = _write_datamaps_inventory_csv(kb_dir / "old_datamaps_inventory_with_ids.csv", old_datamaps)
            files["listing_ui_rows_json"] = _write_json(kb_dir / "datamap_listing_ui_rows.json", listing_ui_rows)
            files["old_datamaps_inventory_csv"] = _write_datamaps_inventory_csv(kb_dir / "old_datamaps_inventory.csv", old_datamaps)
            files["previous_values_json"] = _write_json(kb_dir / "datamap_previous_interaction_values.json", previous_values)
            files["dropdowns_json"] = _write_json(kb_dir / "datamap_dropdowns.json", dropdowns)
            files["required_fields_json"] = _write_json(kb_dir / "datamap_required_fields.json", required_fields)
            files["file_input_contracts_json"] = _write_json(kb_dir / "datamap_file_input_contracts.json", file_input_contracts)
            files["dummy_fill_plan_json"] = _write_json(kb_dir / "datamap_dummy_fill_plan.json", {"values": dummy_values, "attempts": fill_attempts})
            if (kb_dir / "datamap_autonomous_form_execution.json").exists():
                files["autonomous_form_execution_json"] = str(kb_dir / "datamap_autonomous_form_execution.json")
            files["dom_events_json"] = _write_json(kb_dir / "datamap_dom_events.json", _build_dom_event_kb(controls, dropdowns, buttons))
            files.update(_write_datamap_api_flow_knowledge_graph_safe(kb_dir, kb))
            files["markdown"] = _write_markdown(kb_dir / "DATAMAP_KB_SUMMARY.md", kb)
            files["csv"] = _write_controls_csv(kb_dir / "datamap_form_controls.csv", controls)
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
            progress("completed", 8, 8, "Data Map KB summary zip created")
        counts = {
            "old_datamaps": len(json.loads((kb_dir / "old_datamaps_inventory.json").read_text(encoding="utf-8"))) if (kb_dir / "old_datamaps_inventory.json").exists() else 0,
            "old_datamaps_with_numeric_id": (json.loads((kb_dir / "datamap_id_completion_report.json").read_text(encoding="utf-8")).get("map_ids_found", 0) if (kb_dir / "datamap_id_completion_report.json").exists() else 0),
            "api_interactions": len(json.loads((kb_dir / "datamap_api_interactions.json").read_text(encoding="utf-8"))) if (kb_dir / "datamap_api_interactions.json").exists() else 0,
            "form_controls": len(files) and len((json.loads((kb_dir / "datamap_form_kb.json").read_text(encoding="utf-8"))).get("form_controls", [])),
            "required_fields": len(json.loads((kb_dir / "datamap_required_fields.json").read_text(encoding="utf-8"))),
            "dropdowns": len(json.loads((kb_dir / "datamap_dropdowns.json").read_text(encoding="utf-8"))),
        }
        result = DataMapKBResult(run_id=ctx.run_id, run_dir=str(run_dir), kb_dir=str(kb_dir), status=status, counts=counts, files=files, warnings=warnings, existing_object_resolution=existing_object_resolution)
        (run_dir / "datamap_kb_summary.json").write_text(json.dumps(result.__dict__, indent=2, ensure_ascii=False), encoding="utf-8")
        return result.__dict__



def _write_datamap_progress(kb_dir: Path, *, phase: str, completed: int, total: int, detail: str = "", counts: Optional[Dict[str, Any]] = None) -> None:
    """Write a lightweight heartbeat/progress checkpoint for long Data Map KB runs."""
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
        (kb_dir / "datamap_progress.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        with (kb_dir / "datamap_progress_events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        (kb_dir / "datamap_progress_heartbeat.txt").write_text(f"{payload['timestamp']} | {phase} | {completed_i}/{total_i} | {percent}% | {payload['detail']}\n", encoding="utf-8")
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


def _write_datamaps_inventory_csv(path: Path, rows: List[Dict[str, Any]]) -> str:
    fields = [
        "map_id", "map_identifier", "map_identifier_version", "status", "map_name", "map_class",
        "contivo_version", "map_data_file", "available_environments", "latest_dev_version", "created_by", "updated_by", "created_at", "updated_at",
        "source", "source_url",
    ]
    return safe_write_csv(path, fields, rows)


def _write_controls_csv(path: Path, controls: List[Dict[str, Any]]) -> str:
    fields = ["index", "mapped_data_map_key", "label", "tag", "type", "role", "required", "disabled", "readonly", "name", "id", "placeholder", "accept", "ariaInvalid", "selector", "recommended_value"]
    return safe_write_csv(path, fields, controls)


def _build_dom_event_kb(controls: List[Dict[str, Any]], dropdowns: List[Dict[str, Any]], buttons: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "text_inputs": [
            {"label": c.get("label"), "selector": c.get("selector"), "events": ["focus", "input", "change", "blur"], "mapped_data_map_key": c.get("mapped_data_map_key")}
            for c in controls if (c.get("tag") in {"input", "textarea"} and str(c.get("type") or "").lower() != "file")
        ],
        "file_inputs": [
            {"label": c.get("label"), "selector": c.get("selector"), "events": ["setInputFiles", "change"], "mapped_data_map_key": c.get("mapped_data_map_key")}
            for c in controls if str(c.get("type") or "").lower() == "file" or c.get("mapped_data_map_key") == "map_data_file"
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


def _kg_record_props(record: Any, graph_order: int) -> Dict[str, Any]:
    """Return collision-safe KG properties for evidence records.

    Runtime audit records may already contain keys such as ``order``, ``label``
    or ``type``.  Building a node with ``order=...`` and ``**record`` raises a
    Python duplicate-keyword ``TypeError`` before ``add_node`` can normalize
    anything.  Preserve the source record's own order and add a dedicated
    ``graph_order`` for deterministic rendering.
    """
    props: Dict[str, Any] = dict(record) if isinstance(record, dict) else {"value": record}
    props.setdefault("order", graph_order)
    props["graph_order"] = graph_order
    return props


def build_datamap_api_flow_knowledge_graph(kb: Dict[str, Any]) -> Dict[str, Any]:
    """Build a compact Knowledge Graph for Data Map API learning + Add-form KB.

    This is deliberately separate from the heavy browser/run KG. It focuses on:
    Data Maps page -> listing APIs -> pagination replay -> old map rows -> +Add -> form controls/dropdowns -> dummy fill.
    It stores only compact request/response shape and sample rows, never full raw network bodies.
    """
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    def add_node(node_id: str, node_type: str, display_label: str, **props: Any) -> str:
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

    run_id = str(kb.get("run_id") or "DATAMAP-KB")
    run_node = add_node(_kg_id("run", run_id), "RUN", run_id, captured_at=kb.get("captured_at"), mode="datamap_api_learning")
    page_node = add_node(_kg_id("page", kb.get("url")), "PAGE", kb.get("url") or "Data Maps", url=kb.get("url"))
    add_edge(run_node, page_node, "RUN_OPENED_PAGE")

    safety_node = add_node(_kg_id("safety", run_id), "SAFETY_POLICY", "Do not save Data Map", **(kb.get("safety") or {}))
    add_edge(run_node, safety_node, "RUN_ENFORCED_SAFETY")

    prev = kb.get("previous_interaction_values") or {}
    if prev:
        prev_node = add_node(_kg_id("previous_values", run_id), "PREVIOUS_INTERACTION_VALUES", "Uploaded/prior Data Map values", **prev)
        add_edge(run_node, prev_node, "USED_PREVIOUS_VALUES")
        dm_vals = prev.get("data_map_values_to_fill") or {}
        if dm_vals:
            map_node = add_node(_kg_id("seed_map", dm_vals.get("map_identifier"), dm_vals.get("map_name")), "DATAMAP_SEED", dm_vals.get("map_identifier") or dm_vals.get("map_name") or "Data Map seed", **dm_vals, known_ids=prev.get("known_ids"))
            add_edge(prev_node, map_node, "CONTAINED_DATAMAP_SEED")

    stage_nodes: Dict[str, str] = {}
    def stage_node(stage: str) -> str:
        if stage not in stage_nodes:
            stage_nodes[stage] = add_node(_kg_id("stage", run_id, stage), "STAGE", stage)
            add_edge(run_node, stage_nodes[stage], "RUN_STARTED_STAGE")
        return stage_nodes[stage]

    # API interactions from initial list load, pagination replay and +Add click.
    interactions = kb.get("datamap_api_interactions") or []
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
            datamap_rows_extracted=inter.get("datamap_rows_extracted"),
        )
        add_edge(st, api_node, "STAGE_OBSERVED_API", order=idx)
        add_edge(page_node, api_node, "PAGE_TRIGGERED_API", stage=stage)
        endpoint_node = add_node(_kg_id("endpoint", inter.get("method"), inter.get("url")), "ENDPOINT", endpoint, method=inter.get("method"), url=inter.get("url"))
        add_edge(api_node, endpoint_node, "API_HIT_ENDPOINT")
        shape = inter.get("response_shape") or {}
        shape_node = add_node(_kg_id("response_shape", inter.get("url"), json.dumps(shape, sort_keys=True, default=str)), "RESPONSE_SHAPE", f"{shape.get('type','response')} rows={shape.get('row_count',0)}", **shape)
        add_edge(api_node, shape_node, "API_RETURNED_RESPONSE_SHAPE")
        for ridx, row in enumerate(inter.get("sample_datamap_rows") or [], start=1):
            rec_node = add_node(_kg_id("datamap_record", row.get("map_id") or row.get("map_identifier") or ridx), "DATAMAP_RECORD", row.get("map_identifier") or row.get("map_name") or row.get("map_id") or f"record {ridx}", **row)
            add_edge(api_node, rec_node, "API_RETURNED_DATAMAP_RECORD", sample=True)

    # Old inventory records: link every normalized row but keep node props compact.
    for idx, row in enumerate(kb.get("old_datamaps_inventory") or [], start=1):
        compact = {k: row.get(k, "") for k in ["map_id", "map_identifier", "map_identifier_version", "status", "map_name", "map_class", "contivo_version", "map_data_file", "source", "source_url"]}
        rec_node = add_node(_kg_id("datamap_record", compact.get("map_id") or compact.get("map_identifier") or idx), "DATAMAP_RECORD", compact.get("map_identifier") or compact.get("map_name") or compact.get("map_id") or f"Data Map {idx}", **compact)
        add_edge(run_node, rec_node, "RUN_NORMALIZED_OLD_DATAMAP")

    for idx, audit in enumerate(kb.get("pagination_replay_audit") or [], start=1):
        audit_node = add_node(_kg_id("pagination", audit.get("url"), idx), "PAGINATION_REPLAY", audit.get("url") or f"pagination {idx}", **_kg_record_props(audit, idx))
        add_edge(stage_node("datamap_kb_old_datamap_api_pagination"), audit_node, "REPLAYED_PAGINATED_API", order=idx)

    for idx, audit in enumerate(kb.get("detail_enrichment_audit") or [], start=1):
        dm_label = audit.get("map_identifier") or audit.get("map_name") or f"detail enrichment {idx}"
        audit_node = add_node(_kg_id("detail_enrichment", dm_label, idx), "DATAMAP_DETAIL_ENRICHMENT", dm_label, order=idx, resolved=audit.get("resolved"), attempts_count=len(audit.get("attempts") or []), map_identifier=audit.get("map_identifier"), map_name=audit.get("map_name"))
        add_edge(stage_node("datamap_kb_old_datamap_detail_id_enrichment"), audit_node, "TRIED_DETAIL_ID_ENRICHMENT", order=idx, resolved=audit.get("resolved"))
        for aidx, attempt in enumerate((audit.get("attempts") or [])[:8], start=1):
            api_attempt = add_node(_kg_id("detail_api_attempt", attempt.get("url"), idx, aidx), "DETAIL_API_ATTEMPT", attempt.get("url") or f"attempt {aidx}", **_kg_record_props(attempt, aidx))
            add_edge(audit_node, api_attempt, "ATTEMPTED_READ_ONLY_DETAIL_API", status=attempt.get("status"), map_id_found=attempt.get("map_id_found"))

    for idx, audit in enumerate(kb.get("ui_row_action_enrichment_audit") or [], start=1):
        dm_label = audit.get("map_identifier") or audit.get("map_name") or f"ui row action {idx}"
        ui_node = add_node(_kg_id("ui_row_action_detail", dm_label, idx), "UI_ROW_ACTION_DETAIL", dm_label, order=idx, resolved=audit.get("resolved"), search_used=audit.get("search_used"), network_events_after_click=audit.get("network_events_after_click"), api_interactions_after_click=audit.get("api_interactions_after_click"), map_identifier=audit.get("map_identifier"), map_name=audit.get("map_name"), url_map_id_candidate=audit.get("url_map_id_candidate"), event_map_id_candidate=audit.get("event_map_id_candidate"))
        add_edge(stage_node("datamap_kb_ui_row_action_id_learning"), ui_node, "REPEATED_UI_ROW_ACTION_TO_LEARN_ID", order=idx, resolved=audit.get("resolved"))
        click_info = audit.get("click") or {}
        click_node = add_node(_kg_id("ui_row_click", dm_label, idx), "UI_ACTION", click_info.get("reason") or click_info.get("url_after_click") or "row action", clicked=click_info.get("clicked"), row_hint=click_info.get("row_hint"), url_after_click=click_info.get("url_after_click"))
        add_edge(ui_node, click_node, "CLICKED_SAFE_ROW_ACTION", clicked=click_info.get("clicked"))
        for aidx, inter in enumerate((audit.get("api_interactions") or [])[:6], start=1):
            api_node = add_node(_kg_id("ui_row_api", inter.get("method"), inter.get("url"), idx, aidx), "API_INTERACTION", f"{inter.get('method','GET')} {inter.get('url','')}", order=aidx, method=inter.get("method"), url=inter.get("url"), status=inter.get("status"), datamap_rows_extracted=inter.get("datamap_rows_extracted"))
            add_edge(click_node, api_node, "ROW_ACTION_TRIGGERED_API", rows=inter.get("datamap_rows_extracted"))

    # UI/form path.
    add_click_node = add_node(_kg_id("ui_action", run_id, "click_add"), "UI_ACTION", "Click + Add Data Map", action="structural_opener click_add_datamap_do_not_save", save_clicked=False)
    add_edge(run_node, add_click_node, "RUN_PERFORMED_SAFE_UI_ACTION")
    add_edge(add_click_node, safety_node, "ACTION_GUARDED_BY_SAFETY")

    for idx, field in enumerate(kb.get("form_controls") or [], start=1):
        field_node = add_node(_kg_id("form_field", field.get("selector") or idx), "FORM_FIELD", field.get("label") or field.get("name") or field.get("id") or f"field {idx}", order=idx, field_label=field.get("label"), selector=field.get("selector"), tag=field.get("tag"), type=field.get("type"), role=field.get("role"), required=field.get("required"), mapped_data_map_key=field.get("mapped_data_map_key"), recommended_value=field.get("recommended_value"), dom_events_to_try=field.get("dom_events_to_try"))
        add_edge(add_click_node, field_node, "ADD_FORM_CONTAINED_FIELD", required=field.get("required"), mapped_key=field.get("mapped_data_map_key"))
        if field.get("required"):
            add_edge(field_node, safety_node, "REQUIRED_BUT_NOT_SAVED")

    for idx, dd in enumerate(kb.get("dropdowns") or [], start=1):
        dd_node = add_node(_kg_id("dropdown", dd.get("selector") or dd.get("label") or idx), "DROPDOWN", dd.get("label") or f"dropdown {idx}", selector=dd.get("selector"), kind=dd.get("kind"), dom_event=dd.get("dom_event"), options_count=len(dd.get("options") or []), options=(dd.get("options") or [])[:50])
        add_edge(add_click_node, dd_node, "ADD_FORM_CONTAINED_DROPDOWN")

    for idx, fill in enumerate(kb.get("dummy_fill_attempts") or [], start=1):
        fill_node = add_node(_kg_id("dummy_fill", fill.get("field"), fill.get("selector"), idx), "DUMMY_FILL", fill.get("field") or f"dummy fill {idx}", **_kg_record_props(fill, idx))
        add_edge(add_click_node, fill_node, "DUMMY_VALUE_FILLED_WITHOUT_SAVE", filled=fill.get("filled"))
        add_edge(fill_node, safety_node, "DUMMY_FILL_DID_NOT_SAVE")

    graph = {
        "schema_version": "datamap_api_flow_kg_v1",
        "created_at": utc_now(),
        "run_id": run_id,
        "url": kb.get("url"),
        "debug_questions_supported": [
            "Which API was triggered when opening Data Maps?",
            "Which API returned old Data Map IDs?",
            "Which pagination URLs were replayed?",
            "Which endpoint returned mapId/mapIdentifier/mapClass?",
            "Which read-only detail lookup attempts were used to find numeric mapId?",
            "Which real UI row actions were clicked to learn old Data Map IDs?",
            "Which fields/dropdowns appeared after + Add?",
            "Which DOM events are required to fill the Add Data Map form?",
            "Which dummy values were filled without saving?",
        ],
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "api_interactions": len(interactions),
            "old_datamaps": len(kb.get("old_datamaps_inventory") or []),
            "old_datamaps_with_numeric_id": (kb.get("detail_enrichment_report") or {}).get("map_ids_found"),
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


def _write_datamap_api_flow_knowledge_graph(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    graph = build_datamap_api_flow_knowledge_graph(kb)
    json_path = kb_dir / "datamap_api_flow_knowledge_graph.json"
    mmd_path = kb_dir / "datamap_api_flow_knowledge_graph.mmd"
    md_path = kb_dir / "datamap_api_flow_knowledge_graph.md"
    html_path = kb_dir / "datamap_api_flow_knowledge_graph.html"
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
        "# Data Map API Flow Knowledge Graph",
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
<title>Data Map API Flow Knowledge Graph</title>
<script type='module'>import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs'; mermaid.initialize({{startOnLoad:true,securityLevel:'loose'}});</script>
<style>body{{font-family:Arial,sans-serif;margin:28px;color:#222}}.card{{border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0}}pre{{background:#f6f8fa;padding:12px;overflow:auto;max-height:520px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #ddd;padding:6px}}</style>
</head><body><h1>Data Map API Flow Knowledge Graph</h1>
<div class='card'><b>Run:</b> {html.escape(str(graph.get('run_id')))}<br/><b>URL:</b> {html.escape(str(graph.get('url')))}<br/><b>Nodes:</b> {len(graph.get('nodes', []))} <b>Edges:</b> {len(graph.get('edges', []))}</div>
<h2>Summary</h2><pre>{html.escape(json.dumps(summary, indent=2, ensure_ascii=False))}</pre>
<h2>Flow graph</h2><div class='mermaid'>{html.escape(mmd)}</div>
<h2>Node preview</h2><pre>{html.escape(json.dumps(graph.get('nodes', [])[:160], indent=2, ensure_ascii=False))}</pre>
<h2>Edge preview</h2><pre>{html.escape(json.dumps(graph.get('edges', [])[:220], indent=2, ensure_ascii=False))}</pre>
</body></html>"""
    html_path.write_text(html_doc, encoding="utf-8")
    return {
        "datamap_api_flow_kg_json": str(json_path),
        "datamap_api_flow_kg_mermaid": str(mmd_path),
        "datamap_api_flow_kg_markdown": str(md_path),
        "datamap_api_flow_kg_html": str(html_path),
    }


def _write_datamap_api_flow_knowledge_graph_safe(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    """Write the reporting KG without allowing export defects to abort a live phase.

    Form filling, exact-value verification and safety gates are authoritative.  A
    report serializer failure is preserved as an explicit warning artifact and
    the run continues to the next phase.
    """
    status_path = kb_dir / "datamap_api_flow_knowledge_graph_export_status.json"
    try:
        files = _write_datamap_api_flow_knowledge_graph(kb_dir, kb)
        status = {
            "status": "ok",
            "pass": True,
            "run_id": kb.get("run_id"),
            "files": files,
        }
        status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {**files, "datamap_api_flow_kg_export_status": str(status_path)}
    except Exception as exc:
        status = {
            "status": "warning",
            "pass": False,
            "non_blocking": True,
            "run_id": kb.get("run_id"),
            "error_type": type(exc).__name__,
            "error": mask_sensitive_string(str(exc)),
            "reason": "Knowledge Graph reporting export failed after portal execution; live phase evidence remains valid.",
        }
        status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {"datamap_api_flow_kg_export_status": str(status_path)}


def _write_markdown(path: Path, kb: Dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pv = kb.get("previous_interaction_values", {})
    dm = (pv.get("data_map_values_to_fill") or {})
    lines = [
        "# HIP SecureLink Data Map Form KB",
        "",
        f"Run ID: `{kb.get('run_id')}`",
        f"URL: `{kb.get('url')}`",
        "",
        "## Safety",
        "This KB run opens `+ Add`, fills dummy values, captures form/dropdown/DOM evidence, and never clicks Save/Create/Submit.",
        "",
        "## Previous Data Map values from provided input",
        "| Field | Value |",
        "|---|---|",
    ]
    for k, v in dm.items():
        lines.append(f"| `{k}` | `{v}` |")
    lines += ["", "## Known/prior IDs", "| ID | Value |", "|---|---|"]
    for k, v in (pv.get("known_ids") or {}).items():
        lines.append(f"| `{k}` | `{v}` |")
    old_maps = kb.get("old_datamaps_inventory") or []
    id_report = kb.get("detail_enrichment_report") or {}
    lines += ["", "## Old Data Maps learned from listing/detail APIs", f"Total old Data Map rows normalized: `{len(old_maps)}`", f"Numeric map IDs found: `{id_report.get('map_ids_found', 0)}` / `{id_report.get('total_old_datamaps', len(old_maps))}`", f"ID completion: `{id_report.get('completion_percent', 0)}%`", "", "| mapId | mapIdentifier | Version | Status | Map Name | Map Class | Contivo | File | Source |", "|---|---|---|---|---|---|---|---|---|"]
    for row in old_maps[:300]:
        lines.append(f"| `{row.get('map_id','')}` | `{row.get('map_identifier','')}` | `{row.get('map_identifier_version','')}` | `{row.get('status','')}` | `{row.get('map_name','')}` | `{row.get('map_class','')}` | `{row.get('contivo_version','')}` | `{row.get('map_data_file','')}` | `{row.get('source','')}` |")
    if len(old_maps) > 300:
        lines.append(f"| ... | ... | ... | ... | ... | ... | ... | ... | `{len(old_maps)-300} more rows in old_datamaps_inventory.csv` |")
    ui_report = kb.get("ui_row_action_report") or {}
    lines += ["", "## Old Data Map UI row/action ID learning", "This phase repeats the real portal interaction: search an old map row, click its safe View/Edit/Details action, capture the API triggered by that click, and parse numeric `mapId` when exposed.", "", "```json", json.dumps(ui_report, indent=2, ensure_ascii=False), "```"]
    api_interactions = kb.get("datamap_api_interactions") or []
    lines += ["", "## Data Map API interactions learned", "| Method | Status | URL | Rows extracted | Response shape |", "|---|---:|---|---:|---|"]
    for inter in api_interactions[:80]:
        shape = inter.get('response_shape') or {}
        lines.append(f"| `{inter.get('method','')}` | `{inter.get('status','')}` | `{inter.get('url','')}` | `{inter.get('datamap_rows_extracted',0)}` | `{shape.get('type','')} rows={shape.get('row_count',0)}` |")
    lines += ["", "## Required fields discovered", "| Label | Mapped key | Selector | Recommended dummy value |", "|---|---|---|---|"]
    for c in kb.get("required_fields", []):
        lines.append(f"| {c.get('label','')} | `{c.get('mapped_data_map_key','')}` | `{c.get('selector','')}` | `{c.get('recommended_value','')}` |")
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
    zip_path = run_dir / "UPLOAD_DATAMAP_KB_SUMMARY.zip"
    include = [
        kb_dir / "datamap_form_kb.json",
        kb_dir / "old_datamaps_inventory.json",
        kb_dir / "old_datamaps_inventory.csv",
        kb_dir / "old_datamap_id_lookup_by_name.json",
        kb_dir / "datamap_api_interactions.json",
        kb_dir / "datamap_api_pagination_audit.json",
        kb_dir / "datamap_detail_enrichment_audit.json",
        kb_dir / "datamap_ui_row_action_enrichment_audit.json",
        kb_dir / "datamap_id_completion_report.json",
        kb_dir / "old_datamaps_inventory_with_ids.json",
        kb_dir / "old_datamaps_inventory_with_ids.csv",
        kb_dir / "datamap_listing_ui_rows.json",
        kb_dir / "datamap_previous_interaction_values.json",
        kb_dir / "datamap_dropdowns.json",
        kb_dir / "datamap_required_fields.json",
        kb_dir / "datamap_dummy_fill_plan.json",
        kb_dir / "datamap_dom_events.json",
        kb_dir / "datamap_api_flow_knowledge_graph.json",
        kb_dir / "datamap_api_flow_knowledge_graph.mmd",
        kb_dir / "datamap_api_flow_knowledge_graph.md",
        kb_dir / "datamap_api_flow_knowledge_graph.html",
        kb_dir / "DATAMAP_KB_SUMMARY.md",
        kb_dir / "datamap_form_controls.csv",
        kb_dir / "compact_action_sequence.json",
        kb_dir / "compact_click_sequence.json",
        kb_dir / "compact_network_summary.json",
    ]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in include:
            if p.exists():
                z.write(p, arcname=str(p.relative_to(run_dir)))
    return str(zip_path)
