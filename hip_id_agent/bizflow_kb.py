from __future__ import annotations

import asyncio
import csv
import html
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse, quote

from playwright.async_api import Locator, Page

from .browser_session import BrowserSession, browser_session_scope
from .config import AppConfig
from .models import RunContext, utc_now
from .security import mask_sensitive_data, mask_sensitive_string
from .safe_io import safe_write_json, safe_write_csv
from .llm_form_planner import LLMFormPlanner, apply_llm_control_hints
from .section_judge import DualModelSectionJudge, SectionJudgePolicy, build_bizflow_section_expectation
from .repeatable_rows import apply_repeatable_row_adds, build_repeatable_section_plan
from .portal_form_exploration import run_portal_form_exploration, merge_section_knowledge
from .deterministic_plan_runtime import sort_controls as deterministic_sort_controls, annotate_attempt as annotate_plan_attempt, plan_summary as deterministic_plan_summary
from .dds_control_driver import active_form_root_info, assert_active_surface, close_open_dropdown, get_active_form_root, restore_filled_values, set_text_control as dds_set_text_control, select_dds_combobox, click_visible_tab, semantic_runtime_enabled, open_control_for_discovery
from .phase_form_entry import ensure_phase_form_entry, find_same_page_top_right_add, same_page_add_candidate
from .datamap_kb import _evaluate_controls, _evaluate_buttons, _find_add_button, _set_control_value
from .stateful_form_runtime import compile_phase_state_graph, execute_phase_state_graph, build_target_branch_knowledge, capture_stateful_controls
from .autonomous_form_runtime import execute_autonomous_phase_goal, autonomous_phase_enabled, autonomous_target_execution

BIZFLOWS_URL = "https://developer.dell.com/hybrid-integrations/bizexchange/bizflows"

BIZFLOW_URL_HINTS = [
    "bizflow", "biz-flow", "bizflows", "biz-flows", "businessflow", "business-flow",
    "flowdefinition", "flow-definition", "floworchestration", "bizexchange", "flow"
]

SAVE_UNSAFE = {"save", "submit", "create", "update", "delete", "remove", "disable", "enable", "archive", "deploy"}

BIZFLOW_FORM_TABS = [
    "Basic Details",
    "Source Details",
    "Target Details",
    "Configure Routing",
]

BIZFLOW_TAB_ALIASES = {
    "Basic Details": ["basic details", "flow details", "general details", "biz flow details", "flow information", "create biz flow"],
    "Source Details": ["source details", "source", "source configuration", "source information", "configure source"],
    "Target Details": ["target details", "target", "target configuration", "target information", "configure target", "configure target(s)", "target(s)"],
    "Configure Routing": ["configure routing", "routing", "route", "routing configuration"],
}

def bizflow_graph_section_for_tab(tab_name: str) -> str:
    """Map the portal's current tab label to the canonical state-graph section."""
    mapping = {
        "Basic Details": "Flow Details",
        "Flow Details": "Flow Details",
        "Source Details": "Configure Source",
        "Configure Source": "Configure Source",
        "Target Details": "Configure Target(s)",
        "Configure Target": "Configure Target(s)",
        "Configure Target(s)": "Configure Target(s)",
        "Configure Routing": "Configure Routing",
        "Configure Routing + Add": "Configure Routing",
    }
    return mapping.get(str(tab_name or ""), str(tab_name or ""))

DEFAULT_DUMMY_BIZFLOW = {
    "flow_name": "DUMMY_UHAUL_POASN_BIZFLOW_KB",
    "flow_identifier": "DUMMY_UHAUL_POASN_BIZFLOW_KB",
    "flow_version": "1",
    "status": "Enable",
    "environment": "DEV",
    "source_document_type": "XML_U-HAUL_ANS_IB",
    "target_document_type": "XML_DellAutoASN_10_U-HAUL_ANS_IB",
    "rule_name": "DUMMY_UHAUL_POASN_RULE_KB",
    "mapping_identifier": "DUMMY_DELLCoXMLASNXX08C_UHAUL_KB",
    "source_transport_profile": "UHAUL_POASN_SOURCE_TP_DUMMY",
    "target_transport_profile": "DELL_U-HAUL_POASN_TARGET_TP_DUMMY",
    "deployment_group": "dce-shared-sender",
    "process_step_type": "Translation",
    "flow_description": "Dummy BizFlow KB capture only - do not save",
    "template_name": "Translation",
    "primary_domain": "Customer Experience (CX)",
    "flow_type": "Inbound",
    "source_system": "UHAUL",
    "target_system": "AIC - DCE",
    "source_environment": "DEV",
    "target_environment": "DEV",
    "source_partner": "U-HAUL",
    "target_partner": "Dell",
    "route_name": "DUMMY_ROUTE_KB_ONLY",
    "routing_condition": "true",
    "route_action": "Configure Routing",
    "route_sequence": "1",
}

BIZFLOW_FIELD_KEYS = {
    "bizflow_id": ["bizFlowId", "bizflowId", "flowId", "flowDefinitionId", "businessFlowId", "id"],
    "flow_name": ["bizFlowName", "bizflowName", "flowName", "flowIdentifier", "flowDefinitionName", "name", "identifier"],
    "flow_identifier": ["flowIdentifier", "bizFlowIdentifier", "flowIdentifierName", "identifierName", "businessFlowIdentifier"],
    "flow_version": ["flowVersion", "bizFlowVersion", "version", "latestDevVersion", "latest_dev_version", "latestFlowVersion"],
    "flow_type": ["flowType", "bizFlowType", "businessFlowType", "type"],
    "primary_domains": ["primaryDomains", "primaryDomain", "domain", "domains"],
    "template_name": ["templateName", "flowTemplateName"],
    "template_version": ["templateVersion", "flowTemplateVersion"],
    "source_system": ["sourceSystem", "sourceSystemName", "senderSystem"],
    "target_systems": ["targetSystems", "targetSystem", "targetSystemNames", "receiverSystems"],
    "status": ["status", "state", "enabled", "active", "flowStatus", "health"],
    "environment": ["environment", "hipEnvironment", "env", "availableEnvironments"],
    "source_document_type": ["sourceDocumentType", "sourceDocType", "sourceDocumentTypeName", "sourceDocumentTypeNames", "source_doc_type"],
    "target_document_type": ["targetDocumentType", "targetDocType", "targetDocumentTypeName", "targetDocumentTypeNames", "target_doc_type"],
    "rule_name": ["ruleName", "rule", "ruleIdentifier", "ruleIdentifierName", "ruleId", "ruleNames"],
    "mapping_identifier": ["mapIdentifier", "mappingIdentifier", "dataMap", "dataMapName", "mapName", "mappingName", "mappingIdentifiers"],
    "source_transport_profile": ["sourceTransportProfile", "sourceTransportProfiles", "sourceTransportProfileName", "senderTransportProfile", "sourceTp"],
    "target_transport_profile": ["targetTransportProfile", "targetTransportProfiles", "targetTransportProfileName", "receiverTransportProfile", "targetTp"],
    "deployment_group": ["deploymentGroup", "deploymentGroupName", "deployment_group"],
    "created_by": ["createdBy", "created_by", "requestedBy"],
    "updated_by": ["updatedBy", "modifiedBy", "lastModifiedBy"],
    "created_at": ["createdAt", "createdDate", "createdOn"],
    "updated_at": ["updatedAt", "modifiedDate", "lastModifiedDate"],
}

@dataclass
class BizFlowKBResult:
    run_id: str
    run_dir: str
    kb_dir: str
    status: str
    counts: Dict[str, int] = field(default_factory=dict)
    files: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("_") or "unknown"


def _safe_json_load(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return None


def _event_dict(event: Any) -> Dict[str, Any]:
    if hasattr(event, "model_dump"):
        return event.model_dump()
    if hasattr(event, "dict"):
        return event.dict()
    return dict(getattr(event, "__dict__", {}) or {})


def _contains_bizflow_hint(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = text.lower()
    return any(h in text for h in BIZFLOW_URL_HINTS)


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


def _as_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        return ", ".join(_as_text(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        # Environment maps are common in BizFlow list payloads. Keep them compact but useful.
        parts: List[str] = []
        for k, v in value.items():
            if isinstance(v, dict) and v:
                inner = ", ".join(f"{ik}={iv}" for ik, iv in v.items() if iv not in (None, ""))
                parts.append(f"{k}({inner})" if inner else str(k))
            else:
                parts.append(str(k))
        return ", ".join(parts)
    return str(value)


def _iter_json_candidate_rows(payload: Any) -> Iterable[Dict[str, Any]]:
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
    if any(k in payload for k in ["bizFlowId", "bizflowId", "flowId", "flowName", "bizFlowName", "flowDefinitionId"]):
        yield payload
    for key in ["items", "content", "data", "records", "results", "result", "rows", "bizFlows", "bizflows", "flows", "flowDefinitions", "payload"]:
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


def extract_bizflow_record(row: Dict[str, Any], *, source_url: str = "", source: str = "api") -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    values = {field: _first_present(row, keys) for field, keys in BIZFLOW_FIELD_KEYS.items()}
    has_specific = any(values.get(k) not in (None, "") for k in [
        "flow_name", "flow_identifier", "source_document_type", "target_document_type", "rule_name", "mapping_identifier",
        "source_transport_profile", "target_transport_profile"
    ])
    if not has_specific and not _contains_bizflow_hint(source_url):
        return None
    normalized = {
        "bizflow_id": _as_text(values.get("bizflow_id")),
        "flow_name": _as_text(values.get("flow_name") or values.get("flow_identifier")),
        "flow_identifier": _as_text(values.get("flow_identifier") or values.get("flow_name")),
        "flow_version": _as_text(values.get("flow_version")),
        "flow_type": _as_text(values.get("flow_type")),
        "primary_domains": _as_text(values.get("primary_domains")),
        "template_name": _as_text(values.get("template_name")),
        "template_version": _as_text(values.get("template_version")),
        "source_system": _as_text(values.get("source_system")),
        "target_systems": _as_text(values.get("target_systems")),
        "status": _as_text(values.get("status")),
        "environment": _as_text(values.get("environment")),
        "source_document_type": _as_text(values.get("source_document_type")),
        "target_document_type": _as_text(values.get("target_document_type")),
        "rule_name": _as_text(values.get("rule_name")),
        "mapping_identifier": _as_text(values.get("mapping_identifier")),
        "source_transport_profile": _as_text(values.get("source_transport_profile")),
        "target_transport_profile": _as_text(values.get("target_transport_profile")),
        "deployment_group": _as_text(values.get("deployment_group")),
        "created_by": _as_text(values.get("created_by")),
        "updated_by": _as_text(values.get("updated_by")),
        "created_at": _as_text(values.get("created_at")),
        "updated_at": _as_text(values.get("updated_at")),
        "source": source,
        "source_url": mask_sensitive_string(source_url),
        "raw_row_compact": mask_sensitive_data(row),
    }
    if not any(normalized.get(k) for k in ["bizflow_id", "flow_name", "flow_identifier", "source_document_type", "target_document_type", "rule_name", "mapping_identifier"]):
        return None
    return normalized


def extract_bizflow_records_from_payload(payload: Any, *, source_url: str = "", source: str = "api") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in _iter_json_candidate_rows(payload):
        rec = extract_bizflow_record(row, source_url=source_url, source=source)
        if rec:
            rows.append(rec)
    return _dedupe_bizflow_records(rows)


def _bizflow_identity_key(row: Dict[str, Any]) -> str:
    """Stable de-dupe key for BizFlows.

    The UI grid normally exposes the flow name but not the numeric flowId. The gateway
    definition/list API exposes the same flow name with a numeric flowId. Prefer name-based
    identity so API+UI evidence does not double-count the same 249 rows as 498 rows.
    """
    name = str(row.get("flow_name") or row.get("flow_identifier") or "").strip().lower()
    if name and not _is_bizflow_header_text(name):
        return "name:" + re.sub(r"\s+", " ", name)
    bid = str(row.get("bizflow_id") or "").strip()
    if bid:
        return "id:" + bid
    return "|".join(str(row.get(k) or "").strip().lower() for k in ["flow_version", "source_document_type", "target_document_type", "source_transport_profile", "target_transport_profile"])


def _merge_bizflow_records(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Merge duplicate UI/API records, preferring API IDs and filling missing fields."""
    merged = dict(existing)
    incoming_has_id = bool(str(incoming.get("bizflow_id") or "").strip())
    existing_has_id = bool(str(existing.get("bizflow_id") or "").strip())
    if incoming_has_id and not existing_has_id:
        merged = dict(incoming)
        for k, v in existing.items():
            if merged.get(k) in (None, "", [], {}):
                merged[k] = v
        return merged
    for k, v in incoming.items():
        if merged.get(k) in (None, "", [], {}) and v not in (None, "", [], {}):
            merged[k] = v
        if k == "raw_row_compact" and not merged.get(k) and v:
            merged[k] = v
    return merged


def _dedupe_bizflow_records(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not _looks_like_listing_row(row):
            continue
        key = _bizflow_identity_key(row)
        if not key:
            continue
        if key in seen:
            out[seen[key]] = _merge_bizflow_records(out[seen[key]], row)
            continue
        seen[key] = len(out)
        out.append(row)
    return out


def _compact_payload_shape(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, list):
        sample = payload[0] if payload else None
        return {"type": "list", "row_count": len(payload), "sample_keys": sorted(list(sample.keys()))[:80] if isinstance(sample, dict) else []}
    if isinstance(payload, dict):
        rows = list(_iter_json_candidate_rows(payload))
        sample = rows[0] if rows else None
        return {"type": "dict", "wrapper_keys": sorted(list(payload.keys()))[:80], "row_count": len(rows), "sample_keys": sorted(list(sample.keys()))[:80] if isinstance(sample, dict) else []}
    return {"type": type(payload).__name__, "row_count": 0, "sample_keys": []}


def collect_bizflow_api_interactions(events: Iterable[Any], *, stage_label: str = "unknown") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    interactions: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    for ev in events:
        e = _event_dict(ev)
        url = str(e.get("url") or "")
        method = str(e.get("method") or "GET").upper()
        if not _contains_bizflow_hint(url):
            continue
        body = e.get("response_body_redacted")
        if body in (None, ""):
            body = e.get("response_body_text_redacted")
        if body in (None, ""):
            body = e.get("response_body")
        if body in (None, ""):
            body = e.get("body") or e.get("text")
        payload = _safe_json_load(body)
        extracted = extract_bizflow_records_from_payload(payload, source_url=url, source="api") if payload is not None else []
        if not extracted and payload is None and "flows/definition/list" in url:
            extracted = salvage_bizflow_records_from_truncated_body(body, source_url=url, source="api_truncated_gateway_list")
        rows.extend(extracted)
        interactions.append({
            "stage": stage_label,
            "method": method,
            "status": e.get("status"),
            "url": mask_sensitive_string(url),
            "resource_type": e.get("resource_type"),
            "mime_type": e.get("mime_type"),
            "request_body_present": bool(e.get("request_body_redacted")),
            "rows_extracted": len(extracted),
            "response_shape": _compact_payload_shape(payload) if payload is not None else {"type": "empty", "row_count": 0},
        })
    return interactions, _dedupe_bizflow_records(rows)


def _parse_page_count(text: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    # Supports "1 - 10 of 666 items".
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s+of\s+(\d+)\s+items", text or "", flags=re.I)
    if not m:
        return None, None, None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _is_bizflow_header_text(text: str) -> bool:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    return "flow name" in t and "flow type" in t and "source system" in t and "target" in t


def _looks_like_listing_row(row: Dict[str, Any]) -> bool:
    name = str(row.get("flow_name") or row.get("flow_identifier") or "").strip()
    if not name or _is_bizflow_header_text(name):
        return False
    return True


async def _collect_visible_bizflow_rows(page: Page) -> List[Dict[str, Any]]:
    js = r"""
() => {
  const rows = [];
  function clean(s){ return (s||'').trim().replace(/\s+/g,' '); }
  function path(el){
    const parts=[]; while(el && el.nodeType===1 && parts.length<6){ let p=el.tagName.toLowerCase(); if(el.id){p+='#'+CSS.escape(el.id); parts.unshift(p); break;} const cls=(el.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,2).map(c=>CSS.escape(c)).join('.'); if(cls)p+='.'+cls; const parent=el.parentElement; if(parent){ const sib=Array.from(parent.children).filter(x=>x.tagName===el.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(el)+1)+')'; } parts.unshift(p); el=parent;} return parts.join(' > ');
  }
  const trRows = Array.from(document.querySelectorAll('table tbody tr, .dds__table tbody tr, [role=row]')).filter(r => {
    const rect = r.getBoundingClientRect(); return rect.width>0 && rect.height>0 && clean(r.innerText).length>2;
  });
  for (const [idx, tr] of trRows.entries()) {
    const cells = Array.from(tr.querySelectorAll('td, [role=cell], .dds__td')).map(td => clean(td.innerText || td.textContent));
    const text = clean(tr.innerText || tr.textContent);
    rows.push({index: idx, cells, text, selector: path(tr), source:'visible_grid'});
  }
  return rows.slice(0, 200);
}
"""
    try:
        raw = await page.evaluate(js)
    except Exception:
        raw = []
    out: List[Dict[str, Any]] = []
    for r in raw or []:
        cells = [str(c or "").strip() for c in (r.get("cells") or [])]
        text = r.get("text") or ""
        if _is_bizflow_header_text(text):
            continue
        # DDS rows have an empty selection/action cell first, then:
        # Flow Name | Flow Type | Primary Domains | Source System | Target Systems | Available Environments
        useful = [c for c in cells if c and c.lower() not in {"view", "edit", "delete", "actions"} and not c.lower().startswith("page ")]
        name = useful[0] if useful else (text.split("\n")[0].strip() if text else "")
        if _is_bizflow_header_text(name):
            continue
        rec = {
            "bizflow_id": "",
            "flow_name": name,
            "flow_identifier": name,
            "flow_version": "",
            "flow_type": useful[1] if len(useful) > 1 else "",
            "primary_domains": useful[2] if len(useful) > 2 else "",
            "source_system": useful[3] if len(useful) > 3 else "",
            "target_systems": useful[4] if len(useful) > 4 else "",
            "status": "",
            "environment": useful[5] if len(useful) > 5 else "",
            "source_document_type": "",
            "target_document_type": "",
            "rule_name": "",
            "mapping_identifier": "",
            "source_transport_profile": "",
            "target_transport_profile": "",
            "deployment_group": "",
            "source": "visible_grid",
            "source_url": mask_sensitive_string(page.url),
            "raw_row_compact": mask_sensitive_data(r),
        }
        if rec["flow_name"]:
            out.append(rec)
    return _dedupe_bizflow_records(out)


async def _click_next_page(page: Page) -> bool:
    candidates = [
        "button[aria-label='Next']", "button[aria-label*='Next' i]", ".dds__pagination__next-page", "button:has-text('Next')", "[role=button]:has-text('Next')"
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).last
            if await loc.count() and await loc.is_visible(timeout=1200) and await loc.is_enabled(timeout=1200):
                if await _governed_bizflow_click(page, selector=sel, action_label="Next BizFlow page", timeout=2500):
                    await page.wait_for_timeout(1000)
                    return True
        except Exception:
            continue
    return False


async def crawl_bizflow_inventory_from_ui(page: Page, *, max_pages: int = 250, progress_cb=None) -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []
    seen_body = ""
    total_hint = None
    for page_no in range(1, max_pages + 1):
        try:
            await page.wait_for_timeout(800)
            body = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            body = ""
        _, _, total = _parse_page_count(body)
        total_hint = total_hint or total
        rows = await _collect_visible_bizflow_rows(page)
        all_rows.extend(rows)
        all_rows = _dedupe_bizflow_records(all_rows)
        if progress_cb:
            progress_cb("bizflow_ui_pagination_inventory", min(len(all_rows), total_hint or len(all_rows)), total_hint or len(all_rows) or 1, f"UI page {page_no}; rows={len(all_rows)}; total_hint={total_hint or 'unknown'}")
        if total_hint and len(all_rows) >= total_hint:
            break
        if body == seen_body and page_no > 1:
            break
        seen_body = body
        clicked = await _click_next_page(page)
        if not clicked:
            break
    return _dedupe_bizflow_records([r for r in all_rows if _looks_like_listing_row(r)])


async def fetch_bizflow_definition_list_from_api(page: Page, *, progress_cb=None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    parsed = urlparse(page.url or BIZFLOWS_URL)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    base = origin + "/inaas-gateway/hipService-svc/api/flows/definition/list"
    candidate_urls = [
        base,
        base + "?page=0&size=1000",
        base + "?page=0&size=500",
        base + "?pageNumber=0&pageSize=1000",
    ]
    interactions: List[Dict[str, Any]] = []
    best_rows: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    for url in candidate_urls:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        payload, meta = await _fetch_json_with_page(page, url)
        rows = extract_bizflow_records_from_payload(payload, source_url=url, source="definition_list_api") if payload is not None else []
        interactions.append({
            "stage": "bizflow_definition_list_direct",
            "method": "GET",
            "status": meta.get("status"),
            "url": mask_sensitive_string(url),
            "resource_type": "fetch",
            "mime_type": "application/json",
            "request_body_present": False,
            "rows_extracted": len(rows),
            "response_shape": _compact_payload_shape(payload) if payload is not None else {"type": "empty", "row_count": 0},
        })
        if len(rows) > len(best_rows):
            best_rows = rows
        # Most BizFlow deployments return all content in one response. If the response is complete, stop.
        if isinstance(payload, dict):
            total = payload.get("totalElements") or payload.get("total") or payload.get("totalItems")
            try:
                total_i = int(total)
            except Exception:
                total_i = 0
            if total_i and len(rows) >= total_i:
                break
        if len(rows) >= 200:
            break
    if progress_cb and best_rows:
        progress_cb("bizflow_definition_list_api", len(best_rows), len(best_rows), f"definition/list API rows={len(best_rows)}")
    return _dedupe_bizflow_records(best_rows), interactions


async def _ensure_bizflows_listing_page(page: Page, bizflows_url: str) -> Dict[str, Any]:
    """Ensure the browser is on Manage Biz Flow listing, not HIP Home.

    The portal sometimes bounces direct `/bizexchange/bizflows` navigation back
    to the HIP home carousel. This recovery clicks through the left-nav
    BizExchange menu and retries the direct SPA URL. It never clicks
    Save/Create/Submit.
    """
    audit: Dict[str, Any] = {"attempted": True, "steps": []}

    async def state() -> Dict[str, Any]:
        try:
            txt = await page.locator("body").inner_text(timeout=1500)
        except Exception:
            txt = ""
        low = re.sub(r"\s+", " ", txt).lower()
        listing = ("manage biz flow" in low or "biz flows" in low or "bizflows" in low) and bool(await _find_bizflow_add_button(page))
        return {
            "url": page.url,
            "home": "features & benefits" in low and "hybrid integration platform" in low,
            "listing": listing,
            "template_picker": "b2b-flow-pubsub-template" in low and "create biz flow" in low,
            "form": await _is_bizflow_form_surface(page),
        }

    for i in range(2):
        try:
            await page.goto(bizflows_url, wait_until="domcontentloaded", timeout=35000)
            await page.wait_for_timeout(2500 + 500*i)
            st = await state(); audit["steps"].append({"direct_goto": i+1, **st})
            if st.get("listing") or st.get("template_picker") or st.get("form"):
                audit["ok"] = True
                return audit
        except Exception as exc:
            audit["steps"].append({"direct_goto": i+1, "error": mask_sensitive_string(str(exc))})

    try:
        for sel in ["text=/^BizExchange$/i", "button:has-text('BizExchange')", "[role=button]:has-text('BizExchange')"]:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible(timeout=1500):
                    if await _governed_bizflow_click(page, selector=sel, action_label="Open BizExchange navigation", timeout=2500):
                        await page.wait_for_timeout(900)
                        audit["steps"].append({"clicked": "BizExchange"})
                        break
            except Exception:
                continue
        for sel in [r"text=/Biz\s*Flows?/i", r"text=/Manage\s*Biz\s*Flow/i", "a[href*='bizflows' i]", "[role=button]:has-text('Biz')"]:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible(timeout=1500):
                    if await _governed_bizflow_click(page, selector=sel, action_label="Open Biz Flows navigation", timeout=3000):
                        await page.wait_for_timeout(3000)
                        await _dismiss_bizflow_overlays(page)
                        st = await state(); audit["steps"].append({"clicked": sel, **st})
                        if st.get("listing") or st.get("template_picker") or st.get("form"):
                            audit["ok"] = True
                            return audit
            except Exception as exc:
                audit["steps"].append({"click_failed": sel, "error": mask_sensitive_string(str(exc))})
    except Exception as exc:
        audit["steps"].append({"left_nav_error": mask_sensitive_string(str(exc))})

    try:
        await page.evaluate("""url => { history.pushState({}, '', url); window.dispatchEvent(new PopStateEvent('popstate')); }""", bizflows_url)
        await page.wait_for_timeout(3500)
        st = await state(); audit["steps"].append({"pushstate": True, **st})
        if st.get("listing") or st.get("template_picker") or st.get("form"):
            audit["ok"] = True
            return audit
    except Exception as exc:
        audit["steps"].append({"pushstate_error": mask_sensitive_string(str(exc))})
    audit["ok"] = False
    return audit

async def _find_bizflow_add_button(page: Page) -> Optional[Locator]:
    # BizExchange uses DDS/web-component buttons. Prefer text/ARIA but also support icon-only plus/create buttons.
    await _dismiss_bizflow_overlays(page)
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)
    except Exception:
        pass
    shared = await find_same_page_top_right_add(page)
    if shared is not None:
        return shared

    # Golden truth says BizFlow must start from the Manage Biz Flow grid by
    # clicking the real toolbar "+ Add" link at top-right.  The Dell DDS grid can
    # render it as a span/a/dds-button without a stable role, and its neighboring
    # "Manage Columns" text previously caused the strict filter to reject it.
    # First resolve an exact visible + Add text node and climb to the nearest
    # clickable ancestor, explicitly excluding nav/footer/pagination/cookie areas.
    try:
        exact_add = await page.evaluate(r"""
() => {
  function visible(el){const r=el&&el.getBoundingClientRect?el.getBoundingClientRect():{width:0,height:0}; const s=el?getComputedStyle(el):null; return !!(r.width&&r.height&&s&&s.display!=='none'&&s.visibility!=='hidden');}
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){const parts=[]; let n=el; while(n&&n.nodeType===1&&parts.length<8){let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=(n.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,2).map(c=>'.'+CSS.escape(c)).join(''); p+=cls; const par=n.parentElement; if(par){const sib=Array.from(par.children).filter(x=>x.tagName===n.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(n)+1)+')';} parts.unshift(p); n=par;} return parts.join(' > ');}
  const unsafe='nav,header,footer,dds-pagination,.dds__pagination,#onetrust-consent-sdk,#onetrust-pc-sdk,#onetrust-banner-sdk';
  const nodes=Array.from(document.querySelectorAll('button,a,[role=button],dds-button,.dds__button,span,div'))
    .filter(visible)
    .map(el => ({el, label:clean(el.innerText||el.textContent||el.getAttribute('aria-label')||el.getAttribute('title')||'')}))
    .filter(x => /^\+\s*Add$/i.test(x.label) || /^Add$/i.test(x.label));
  const scored=[];
  for(const n of nodes){
    const click=n.el.closest('button,a,[role=button],dds-button,.dds__button') || n.el;
    if(!click || click.closest(unsafe)) continue;
    const r=click.getBoundingClientRect();
    let score=0; if(r.top < 260) score+=4; if(r.left > window.innerWidth*0.55) score+=5; if(click.closest('main,.app__content,.dds__container')) score+=3; if(/^\+\s*Add$/i.test(n.label)) score+=3;
    scored.push({selector:path(click), label:n.label, score, x:r.x, y:r.y});
  }
  scored.sort((a,b)=>b.score-a.score);
  return scored[0] || null;
}
""")
        if exact_add and exact_add.get("selector"):
            loc = page.locator(exact_add["selector"]).first
            if await loc.count() and await loc.is_visible(timeout=1000) and await loc.is_enabled(timeout=1000):
                if await same_page_add_candidate(page, loc):
                    return loc
    except Exception:
        pass

    selectors = [
        "main button:has-text('Add')", "main button:has-text('+ Add')",
        "main [role=button]:has-text('Add')", "main [role=button]:has-text('+ Add')",
        "main a:has-text('Add')", "main a:has-text('+ Add')",
        "main button[aria-label*='add' i]", "main button[aria-label*='create' i]", "main button[title*='add' i]", "main button[title*='create' i]",
        "main a[href*='create' i]", "main a[href*='add' i]",
        "main dds-button:has-text('Add')", "main dds-button[aria-label*='add' i]",
        "button[data-testid*='add' i]", "button[id*='add' i]", "[class*='toolbar' i] button",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=1000) and await loc.is_enabled(timeout=1000):
                if await same_page_add_candidate(page, loc):
                    return loc
        except Exception:
            continue
    js = r"""
() => {
  function clean(s){ return (s||'').trim().replace(/\s+/g,' '); }
  function path(el){
    const parts=[]; while(el && el.nodeType===1 && parts.length<7){
      let p=el.tagName.toLowerCase();
      if(el.id){ p += '#'+CSS.escape(el.id); parts.unshift(p); break; }
      const cls=(el.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.');
      if(cls) p += '.'+cls;
      const parent=el.parentElement; if(parent){ const sib=Array.from(parent.children).filter(x=>x.tagName===el.tagName); if(sib.length>1) p += ':nth-of-type('+(sib.indexOf(el)+1)+')'; }
      parts.unshift(p); el=parent;
    }
    return parts.join(' > ');
  }
  const bad=/pagination|next|previous|filter|search|clear|cancel|close|row|table|edit|delete|remove|deploy|manage|cookie|preference/i;
  const good=/\b(add|create|new)\b|^\+\s*add$|^\+$/i;
  const candidates=[];
  for(const el of Array.from(document.querySelectorAll('main button,main a,main [role=button],main dds-button,button,a,[role=button],dds-button'))){
    if(el.closest('#onetrust-pc-sdk,#onetrust-banner-sdk,#ot-sdk-btn-floating,.onetrust-pc-dark-filter')) continue;
    const r=el.getBoundingClientRect();
    if(r.width<8 || r.height<8) continue;
    const txt=clean(el.innerText || el.textContent || '');
    const aria=clean(el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('name') || el.getAttribute('data-testid') || el.id || '');
    const label=(txt+' '+aria).trim();
    const cls=(el.className||'').toString();
    const context=clean((el.closest('header,.toolbar,.dds__toolbar,.dds__action-menu,main,body')||document.body).innerText||'').slice(0,500);
    const exactAdd=/^\+?\s*Add$/i.test(label);
    if((bad.test(label) && !exactAdd) || /pagination|cookie|onetrust/i.test(cls)) continue;
    const hasPlus = !!el.querySelector('svg,path,use,[class*=plus],[name*=plus],dds-icon') || /\+/.test(label) || /add/i.test(cls+' '+aria);
    if(good.test(label) || (hasPlus && r.top < window.innerHeight*0.8)){
      let score=0; if(/create biz flow|create flow/i.test(label+' '+context))score+=8; if(/add/i.test(label+' '+aria+' '+cls))score+=6; if(/create/i.test(label))score+=5; if(/new/i.test(label))score+=3; if(/flow|biz/i.test(label+' '+context))score+=3; if(hasPlus)score+=2; if(el.closest('main'))score+=2; if(r.top < 260)score+=1;
      candidates.push({selector:path(el), label, score});
    }
  }
  candidates.sort((a,b)=>b.score-a.score);
  return candidates[0] || null;
}
"""
    try:
        cand = await page.evaluate(js)
        if cand and cand.get("selector"):
            loc = page.locator(cand["selector"]).first
            if await loc.count() and await loc.is_visible(timeout=1000) and await loc.is_enabled(timeout=1000):
                if await same_page_add_candidate(page, loc):
                    return loc
    except Exception:
        pass
    try:
        return await _find_add_button(page)
    except Exception:
        return None


def build_previous_interaction_values(input_data: Dict[str, Any]) -> Dict[str, Any]:
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    return {
        "source": "uploaded_input_json_and_prior_manual_context",
        "captured_at": utc_now(),
        "bizflow_values_to_fill": DEFAULT_DUMMY_BIZFLOW,
        "related_values": {
            "source_document_type_name": ((objects or {}).get("source_document_type") or {}).get("name"),
            "target_document_type_name": ((objects or {}).get("target_document_type") or {}).get("name"),
            "rule_name": ((objects or {}).get("rule") or {}).get("name"),
            "data_map": ((objects or {}).get("data_map") or {}).get("map_identifier"),
        }
    }


def _put_if_scalar(result: Dict[str, str], key: str, value: Any) -> None:
    if value not in (None, "") and not isinstance(value, (dict, list)):
        result[key] = str(value)


def build_dummy_fill_values(input_data: Dict[str, Any] | None = None) -> Dict[str, str]:
    """Build BizFlow fill values from input.json.

    In replication mode (`_replicate_exact_input_values`) the flow name, source,
    target, rule, map and routing values are copied from input.json exactly so the
    after-fill screenshot can match the golden UHAUL-POASN references. The command
    still blocks Save/Create/Submit/Deploy.
    """
    input_data = input_data or {}
    result = dict(DEFAULT_DUMMY_BIZFLOW)
    objects = input_data.get("objects") if isinstance(input_data, dict) else {}
    bf = (objects or {}).get("biz_flow") or (objects or {}).get("bizflow") or input_data.get("bizflow") if isinstance(input_data, dict) else {}
    exact = bool(input_data.get("_replicate_exact_input_values")) if isinstance(input_data, dict) else False
    if isinstance(bf, dict):
        # Flat values if already provided.
        for k, v in bf.items():
            if v not in (None, "") and not isinstance(v, (dict, list)):
                result[k] = str(v)
        fd = bf.get("flow_details") if isinstance(bf.get("flow_details"), dict) else {}
        _put_if_scalar(result, "flow_name", fd.get("business_flow_name") or fd.get("flow_name") or fd.get("name"))
        _put_if_scalar(result, "flow_version", fd.get("current_flow_version") or fd.get("version"))
        _put_if_scalar(result, "flow_description", fd.get("flow_description") or fd.get("description"))
        cs = bf.get("configure_source") if isinstance(bf.get("configure_source"), dict) else {}
        _put_if_scalar(result, "source_type", cs.get("source_type"))
        _put_if_scalar(result, "source_system", cs.get("source_application") or cs.get("source_system"))
        _put_if_scalar(result, "source_application", cs.get("source_application") or cs.get("source_system"))
        _put_if_scalar(result, "source_transport_profile", cs.get("source_transport_profile"))
        _put_if_scalar(result, "source_document_type", cs.get("document_type_name_version") or cs.get("source_document_type"))
        ct = bf.get("configure_targets") if isinstance(bf.get("configure_targets"), dict) else {}
        _put_if_scalar(result, "target_type", ct.get("target_type"))
        _put_if_scalar(result, "target_system", ct.get("target_application") or ct.get("target_system"))
        _put_if_scalar(result, "target_application", ct.get("target_application") or ct.get("target_system"))
        _put_if_scalar(result, "target_transport_profile", ct.get("target_transport_profile"))
        _put_if_scalar(result, "target_document_type", ct.get("document_type_name_version") or ct.get("target_document_type"))
        fis = bf.get("flow_identifiers") if isinstance(bf.get("flow_identifiers"), dict) else {}
        conds = fis.get("conditions") if isinstance(fis.get("conditions"), list) else []
        if conds:
            first = conds[0]
            _put_if_scalar(result, "flow_identifier", first.get("attribute_name"))
            _put_if_scalar(result, "condition_attribute", first.get("attribute_name"))
            _put_if_scalar(result, "condition_operator", first.get("operator"))
            _put_if_scalar(result, "flow_identifier_operator", first.get("operator"))
            _put_if_scalar(result, "condition_value", first.get("value"))
            # Preserve all rows for repeatable row filling/auditing.
            result["flow_identifier_rows_json"] = json.dumps(conds, ensure_ascii=False)
        steps = bf.get("process_steps") if isinstance(bf.get("process_steps"), list) else []
        if steps:
            first = steps[0]
            _put_if_scalar(result, "process_step_type", first.get("step_type"))
            _put_if_scalar(result, "process_step_name", first.get("step_name"))
            cfg = first.get("configuration") if isinstance(first.get("configuration"), dict) else {}
            _put_if_scalar(result, "rule_name", cfg.get("rule_version") or cfg.get("rule"))
            _put_if_scalar(result, "target_document_type", cfg.get("target_document_type_version") or result.get("target_document_type"))
            _put_if_scalar(result, "process_action", cfg.get("action") or "Mapping")
            dm_obj = (objects or {}).get("data_map") or {}
            _put_if_scalar(result, "mapping_identifier", cfg.get("mapping_identifier") or dm_obj.get("map_identifier") or result.get("mapping_identifier"))
        routing = bf.get("configure_routing") if isinstance(bf.get("configure_routing"), dict) else {}
        rule = routing.get("rule") if isinstance(routing.get("rule"), dict) else {}
        _put_if_scalar(result, "route_name", rule.get("name"))
        _put_if_scalar(result, "route_document_type", rule.get("document_type_name_version") or rule.get("document_type") or rule.get("document_type_name"))
        rconds = (routing.get("conditions") or {}).get("rows") if isinstance(routing.get("conditions"), dict) else []
        if isinstance(rconds, list) and rconds:
            first = rconds[0]
            _put_if_scalar(result, "routing_condition", first.get("attribute_name") or first.get("attribute_name_unit"))
            _put_if_scalar(result, "route_condition_attribute", first.get("attribute_name") or first.get("attribute_name_unit"))
            _put_if_scalar(result, "route_condition_operator", first.get("operator"))
            _put_if_scalar(result, "route_condition_value", first.get("value"))
            result["routing_condition_rows_json"] = json.dumps(rconds, ensure_ascii=False)
        actions = routing.get("actions") if isinstance(routing.get("actions"), dict) else {}
        _put_if_scalar(result, "route_action_name", actions.get("name"))
        _put_if_scalar(result, "route_action_type", actions.get("type") or "Route Document")
        _put_if_scalar(result, "target_transport_profile", actions.get("target") or result.get("target_transport_profile"))

    # For KB traversal in non-exact mode, use one existing BizFlow's referenced objects for dependent dropdowns.
    ref = input_data.get("_kb_reference_bizflow") if isinstance(input_data, dict) else None
    if isinstance(ref, dict) and not exact:
        ref_map = {
            "flow_type": ref.get("flow_type"),
            "primary_domain": ref.get("primary_domains"),
            "source_system": ref.get("source_system"),
            "target_system": ref.get("target_systems"),
            "source_document_type": ref.get("source_document_type"),
            "target_document_type": ref.get("target_document_type"),
            "source_transport_profile": ref.get("source_transport_profile"),
            "target_transport_profile": ref.get("target_transport_profile"),
            "template_name": ref.get("template_name"),
            "environment": "DEV",
        }
        for k, v in ref_map.items():
            if v not in (None, ""):
                result[k] = str(v)

    # Values for the nested Configure Routing rule drawer.  These controls are
    # not the same as the top-level BizFlow tabs and were previously left blank.
    result.setdefault("route_execute_when", "all conditions are satisfied")
    result.setdefault("route_condition_type", "Attributes")
    result.setdefault("route_action_type", "Route Document")
    result.setdefault("route_action_name", result.get("route_name") or "ROUTE_DOCUMENT_TO_TARGET")
    result.setdefault("route_rule_name", result.get("rule_name") or result.get("route_name") or "TARGET_ROUTING_RULE")
    result.setdefault("route_document_type", result.get("target_document_type") or result.get("source_document_type") or "")

    if not exact and not str(result.get("flow_name", "")).upper().startswith("DUMMY"):
        result["flow_name"] = f"DUMMY_{result['flow_name']}_KB"
    return {k: str(v) for k, v in result.items()}

def guess_field_key(label: str, attrs: Dict[str, Any]) -> Optional[str]:
    """Map BizFlow labels using the visible tab/section before generic words.

    The old matcher let generic `name` and `identifier` win before specific
    labels, causing `Document Type Name` and `Attribute Name` to be filled with
    the flow name.  This function is intentionally tab-aware.
    """
    text = " ".join(str(x or "") for x in [label, attrs.get("name"), attrs.get("id"), attrs.get("placeholder"), attrs.get("ariaLabel")]).lower()
    tab = str(attrs.get("bizflow_tab") or attrs.get("tab") or "").lower()
    compact = re.sub(r"[^a-z0-9]+", "", text)
    exact = re.sub(r"\s+", " ", str(label or "").strip().lower())

    # Nested Configure Routing opens a Target Routing Rule drawer.  Map that
    # drawer before any generic top-level BizFlow rules.
    if "routing" in tab and "add" in tab:
        name_attr = str(attrs.get("name") or "").lower()
        if "execute action" in text or "execute actions" in text:
            return "route_execute_when"
        if "condition type" in text:
            return "route_condition_type"
        if "document type" in text:
            return "route_document_type"
        if exact == "operator" or "operator" in text:
            return "route_condition_operator"
        if exact == "value" or name_attr == "value":
            return "route_condition_value"
        if exact == "type":
            return "route_action_type"
        if exact == "name" and name_attr == "rulename":
            return "rule_name"
        if exact == "name" and name_attr == "name":
            return "route_action_name"

    if "document type" in text:
        if any(x in tab for x in ["target"]):
            return "target_document_type"
        if any(x in tab for x in ["routing"]):
            return "route_document_type"
        if any(x in tab for x in ["source", "identifier", "flow identifier"]):
            return "source_document_type"
        if "target" in text:
            return "target_document_type"
        if "source" in text:
            return "source_document_type"
        return "source_document_type"
    if exact == "attribute name" or "attributename" in compact:
        if "routing" in tab:
            return "route_condition_attribute"
        return "condition_attribute"
    if "flow identifier operator" in text or exact == "operator":
        if "routing" in tab:
            return "route_condition_operator"
        return "condition_operator"
    if exact == "value" or compact.endswith("value"):
        if "routing" in tab:
            return "route_condition_value"
        return "condition_value"
    if "business flow name" in text or "biz flow name" in text or exact in {"flow name", "name"} and "flow" in tab:
        return "flow_name"
    if "flow description" in text or exact in {"description", "comments", "notes"}:
        return "flow_description"
    if "primary domain" in text:
        return "primary_domain"
    if "flow type" in text or "direction" in text:
        return "flow_type"
    if "source type" in text:
        return "source_type"
    if "target type" in text:
        return "target_type"
    if any(x in text for x in ["source application", "source system", "sender system"]):
        return "source_system"
    if any(x in text for x in ["target application", "target system", "receiver system"]):
        return "target_system"
    if any(x in text for x in ["source transport", "sender transport", "source tp"]):
        return "source_transport_profile"
    if any(x in text for x in ["target transport", "receiver transport", "target tp"]):
        return "target_transport_profile"
    if "routing condition" in text or "route condition" in text:
        return "routing_condition"
    if "route name" in text or "routing name" in text:
        return "route_name"
    if "action name" in text:
        return "route_action_name"
    if "action type" in text:
        return "route_action_type"
    if exact == "target" and "routing" in tab:
        return "target_transport_profile"
    if "step name" in text:
        return "process_step_name"
    if "step type" in text or "process step" in text:
        return "process_step_type"
    if exact == "action" and any(x in tab for x in ["process", "target"]):
        return "process_action"
    if "rule" in text:
        return "rule_name"
    if "mapping identifier" in text or "data map" in text or "mapping" in text:
        return "mapping_identifier"
    if "deployment group" in text:
        return "deployment_group"
    if "template" in text:
        return "template_name"
    if "environment" in text:
        return "environment"
    if "version" in text and "flow" in text:
        return "flow_version"
    if "flow identifier" in text:
        return "flow_identifier"
    return None


async def _collect_noninvasive_dropdowns(page: Page, controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Capture dropdown metadata without mutating the form.

    Earlier BizFlow form capture only counted combobox controls. This keeps that safe
    behavior, but also preserves any option lists already exposed in the DOM by the
    control evaluator. Interactive DDS option harvesting is handled by
    `_collect_bizflow_dropdowns_with_options` before dummy-fill.
    """
    dropdowns: List[Dict[str, Any]] = []
    for c in controls:
        tag = str(c.get("tag") or "").lower()
        role = str(c.get("role") or "").lower()
        label = c.get("label") or c.get("ariaLabel") or c.get("placeholder") or c.get("name") or c.get("id") or f"control_{c.get('index')}"
        is_dropdown = tag == "select" or role == "combobox" or "dropdown" in str(c.get("selector", "")).lower() or "select" in str(label).lower()
        if is_dropdown:
            dropdowns.append({
                "label": label,
                "selector": c.get("selector"),
                "kind": "select" if tag == "select" else "combobox",
                "options": c.get("options") or [],
                "option_count": len(c.get("options") or []),
                "dom_event": "non-invasive capture before dummy fill",
            })
    return dropdowns


async def _harvest_visible_dropdown_options(page: Page) -> List[str]:
    js = r"""
() => {
  function clean(s){ return (s||'').trim().replace(/\s+/g,' '); }
  const optionSelectors = [
    '[role=option]', '.dds__dropdown__item-option', '.dds__dropdown__option',
    '.dds__select__option', 'li[role=option]', '[class*=dropdown] li',
    '[class*=menu] [role=option]', '[class*=listbox] *'
  ];
  const seen = new Set();
  const out = [];
  for (const sel of optionSelectors) {
    for (const el of Array.from(document.querySelectorAll(sel))) {
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      if (el.closest('#onetrust-pc-sdk,#onetrust-banner-sdk,.onetrust-pc-dark-filter')) continue;
      const text = clean(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
      if (!text || /cookie|privacy|preference|marketing|functional|save|submit|delete|remove|deploy/i.test(text)) continue;
      const key = text.toLowerCase();
      if (!seen.has(key)) { seen.add(key); out.push(text); }
    }
  }
  return out.slice(0, 250);
}
"""
    try:
        options = await page.evaluate(js)
        return [str(o) for o in (options or []) if str(o).strip()]
    except Exception:
        return []


async def _collect_bizflow_dropdowns_with_options(page: Page, controls: List[Dict[str, Any]], *, tab_label: str = "") -> List[Dict[str, Any]]:
    """Capture dropdowns and best-effort option values for the active BizFlow tab.

    This is intentionally safe: it opens only combobox/select controls, reads visible
    option text, and never chooses an option unless the later dummy-fill logic decides
    to set a disposable value. Escape is used only to close an open listbox; if the
    BizFlow form surface disappears, the audit records it.
    """
    dropdowns = await _collect_noninvasive_dropdowns(page, controls)
    for d in dropdowns:
        d["bizflow_tab"] = tab_label or d.get("bizflow_tab") or "unknown"
        selector = str(d.get("selector") or "")
        if d.get("options"):
            d["option_capture_mode"] = "control_dom_options"
            d["option_count"] = len(d.get("options") or [])
            continue
        if not selector:
            d["option_capture_mode"] = "selector_missing"
            continue
        try:
            loc = page.locator(selector).first
            if await loc.count() and await loc.is_visible(timeout=700) and await loc.is_enabled(timeout=700):
                if not await open_control_for_discovery(page, selector, label=str(d.get("label") or "BizFlow dropdown"), phase="biz_flow"):
                    d["option_capture_mode"] = "semantic_open_rejected"
                    continue
                await page.wait_for_timeout(350)
                options = await _harvest_visible_dropdown_options(page)
                d["options"] = options
                d["option_count"] = len(options)
                d["option_capture_mode"] = "safe_open_read_options_no_select"
                # Close the popup/listbox without selecting. If this closes a drawer/form,
                # the audit records it and later tab navigation re-finds the form surface.
                try:
                    await close_open_dropdown(page, "transport_profile" if "transport" in __name__ else "biz_flow")
                    await page.wait_for_timeout(200)
                except Exception:
                    pass
                try:
                    d["form_still_visible_after_escape"] = await _is_bizflow_form_surface(page)
                except Exception:
                    d["form_still_visible_after_escape"] = None
        except Exception as exc:
            d["option_capture_error"] = str(exc)
            d.setdefault("option_capture_mode", "failed")

    return dropdowns


def _as_list_values(value: Any) -> List[str]:
    """Normalize list/scalar values from gateway/UI rows into clean strings."""
    out: List[str] = []
    if value in (None, ""):
        return out
    values = list(value) if isinstance(value, (list, tuple, set)) else [value]
    for v in values:
        if v in (None, ""):
            continue
        if isinstance(v, (list, tuple, set)):
            out.extend(_as_list_values(v))
            continue
        text = str(v).strip()
        if text:
            out.append(text)
    return out


def _unique_limited(values: List[str], limit: int = 250) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for v in values:
        text = str(v or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _flatten_bizflow_reference_rows(old_bizflows: List[Dict[str, Any]] | None, deep_profiles: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in old_bizflows or []:
        if isinstance(row, dict):
            rows.append(row)
    for prof in deep_profiles or []:
        if not isinstance(prof, dict):
            continue
        for key in ("inventory_row", "normalized_from_detail", "raw_detail_compact"):
            val = prof.get(key)
            if isinstance(val, dict):
                rows.append(val)
    return rows


def _derive_bizflow_dropdown_option_map(old_bizflows: List[Dict[str, Any]] | None = None, deep_profiles: List[Dict[str, Any]] | None = None, runtime_profiles: List[Dict[str, Any]] | None = None, previous_values: Dict[str, Any] | None = None) -> Dict[str, List[str]]:
    """Build option fallbacks for BizFlow form dropdowns from captured old flows.

    The portal's dependent DDS dropdowns sometimes do not expose option text until a
    previous valid selection is applied. This helper uses captured BizFlow inventory,
    deep-profile/runtime API data, and small stable operator/type enums, then marks the
    option source so final review can distinguish live DOM options from fallback-derived
    options.
    """
    rows = _flatten_bizflow_reference_rows(old_bizflows, deep_profiles)
    source_systems: List[str] = []
    target_systems: List[str] = []
    source_tps: List[str] = []
    target_tps: List[str] = []
    source_docs: List[str] = []
    target_docs: List[str] = []
    flow_types: List[str] = []
    domains: List[str] = []
    templates: List[str] = []
    environments: List[str] = []

    for row in rows:
        raw = row.get("raw_row_compact") if isinstance(row.get("raw_row_compact"), dict) else {}
        flow_types += _as_list_values(row.get("flow_type") or raw.get("flowType"))
        domains += _as_list_values(row.get("primary_domains") or raw.get("primaryDomains"))
        templates += _as_list_values(row.get("template_name") or raw.get("templateName"))
        source_systems += _as_list_values(row.get("source_system") or raw.get("sourceSystem"))
        target_systems += _as_list_values(row.get("target_systems") or raw.get("targetSystems"))
        source_tps += _as_list_values(row.get("source_transport_profile") or raw.get("sourceTransportProfiles"))
        target_tps += _as_list_values(row.get("target_transport_profile") or raw.get("targetTransportProfiles"))
        source_docs += _as_list_values(row.get("source_document_type") or raw.get("sourceDocumentTypeNames"))
        target_docs += _as_list_values(row.get("target_document_type") or raw.get("targetDocumentTypeNames"))
        env_obj = raw.get("availableEnvironments") if isinstance(raw, dict) else None
        if isinstance(env_obj, dict):
            environments += list(env_obj.keys())
        env_text = row.get("environment")
        if isinstance(env_text, str):
            environments += [e.strip().split("(")[0] for e in env_text.split(",") if e.strip()]

    for prof in runtime_profiles or []:
        if not isinstance(prof, dict):
            continue
        for env in prof.get("environments") or []:
            summary = (env or {}).get("runtime_summary") or {}
            for src in summary.get("source_details") or []:
                source_systems += _as_list_values(src.get("system"))
                source_tps += _as_list_values(src.get("transport_profile"))
            for tgt in summary.get("target_details") or []:
                target_systems += _as_list_values(tgt.get("system"))
                target_tps += _as_list_values(tgt.get("transport_profile"))
            if summary.get("selected_environment"):
                environments.append(str(summary.get("selected_environment")))

    prev = ((previous_values or {}).get("bizflow_values_to_fill") or {}) if isinstance(previous_values, dict) else {}
    source_systems += _as_list_values(prev.get("source_system"))
    target_systems += _as_list_values(prev.get("target_system"))
    source_tps += _as_list_values(prev.get("source_transport_profile"))
    target_tps += _as_list_values(prev.get("target_transport_profile"))
    source_docs += _as_list_values(prev.get("source_document_type"))
    target_docs += _as_list_values(prev.get("target_document_type"))
    flow_types += _as_list_values(prev.get("flow_type"))
    domains += _as_list_values(prev.get("primary_domain"))
    templates += _as_list_values(prev.get("template_name"))
    environments += _as_list_values(prev.get("environment"))

    return {
        "source_type": ["System", "Partner"],
        "target_type": ["System", "Partner"],
        "source_application": _unique_limited(source_systems),
        "target_application": _unique_limited(target_systems),
        "source_transport_profile": _unique_limited(source_tps),
        "target_transport_profile": _unique_limited(target_tps),
        "source_document_type": _unique_limited(source_docs),
        "target_document_type": _unique_limited(target_docs),
        "document_type": _unique_limited([*source_docs, *target_docs]),
        "flow_type": _unique_limited([*flow_types, "Inbound", "Outbound"]),
        "primary_domain": _unique_limited(domains),
        "template_name": _unique_limited(templates),
        "environment": _unique_limited([*environments, "DEV", "TEST1", "TEST2", "PROD"]),
        "flow_identifier_operator": ["Equals", "Not Equals", "Contains", "Starts With", "Ends With", "Regex"],
        "operator": ["Equals", "Not Equals", "Contains", "Starts With", "Ends With", "Regex"],
        "route_action": ["Map", "Passthrough", "Configure Routing", "Translation"],
        "mapping_identifier": _unique_limited(_as_list_values(prev.get("mapping_identifier"))),
        "rule_name": _unique_limited(_as_list_values(prev.get("rule_name"))),
    }


def _dropdown_option_key(label: str, tab_label: str = "") -> Optional[str]:
    text = f"{tab_label} {label}".lower()
    if "source type" in text:
        return "source_type"
    if "target type" in text:
        return "target_type"
    if "source application" in text or "source system" in text:
        return "source_application"
    if "target application" in text or "target system" in text:
        return "target_application"
    if "source transport" in text:
        return "source_transport_profile"
    if "target transport" in text:
        return "target_transport_profile"
    if "source" in text and "document type" in text:
        return "source_document_type"
    if "target" in text and "document type" in text:
        return "target_document_type"
    if "document type" in text:
        if "target details" in text:
            return "target_document_type"
        if "source details" in text:
            return "source_document_type"
        return "document_type"
    if "flow identifier operator" in text:
        return "flow_identifier_operator"
    if text.strip().endswith("operator") or " operator" in text:
        return "operator"
    if "primary domain" in text:
        return "primary_domain"
    if "flow type" in text:
        return "flow_type"
    if "template" in text:
        return "template_name"
    if "environment" in text:
        return "environment"
    if "mapping" in text or "map" in text:
        return "mapping_identifier"
    if "rule" in text:
        return "rule_name"
    if "action" in text or "route" in text:
        return "route_action"
    return None


def _enrich_bizflow_dropdown_options(dropdowns: List[Dict[str, Any]], old_bizflows: List[Dict[str, Any]] | None = None, deep_profiles: List[Dict[str, Any]] | None = None, runtime_profiles: List[Dict[str, Any]] | None = None, previous_values: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    """Fill empty dropdown option lists from captured old BizFlow evidence."""
    option_map = _derive_bizflow_dropdown_option_map(old_bizflows, deep_profiles, runtime_profiles, previous_values)
    enriched: List[Dict[str, Any]] = []
    for d in dropdowns or []:
        item = dict(d)
        current = _unique_limited(_as_list_values(item.get("options")))
        if current:
            item["options"] = current
            item["option_count"] = len(current)
            item.setdefault("option_capture_mode", item.get("option_capture_mode") or "live_or_control_options")
            item.setdefault("option_source", "live_dom_or_control")
            enriched.append(item)
            continue
        key = _dropdown_option_key(str(item.get("label") or ""), str(item.get("bizflow_tab") or ""))
        fallback = _unique_limited(option_map.get(key or "", [])) if key else []
        if fallback:
            item["options"] = fallback
            item["option_count"] = len(fallback)
            item["option_capture_mode"] = "fallback_from_captured_bizflow_inventory_runtime_or_safe_enum"
            item["option_source"] = key
        else:
            item.setdefault("options", [])
            item["option_count"] = 0
            item.setdefault("option_capture_mode", "empty_after_live_and_fallback")
        enriched.append(item)
    return enriched


_COOKIE_OR_CONSENT_RE = re.compile(r"onetrust|ot-sdk|ot-pc|cookie|vendor-search|chkbox-id|select-all-hosts|select-all-vendor|marketing|statistical|uncategorized", re.I)


def _is_cookie_or_consent_item(item: Dict[str, Any]) -> bool:
    text = " ".join(str(item.get(k) or "") for k in ["selector", "label", "name", "id", "placeholder", "text", "ariaLabel", "aria_label"])
    return bool(_COOKIE_OR_CONSENT_RE.search(text))


def _filter_bizflow_controls(controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    noise = re.compile(r"^(search|table search|items per page|page|filter by column name\.?)$", re.I)
    noise_sel = re.compile(r"pagination|dds__table__search|table-ribbon-search|filter", re.I)
    for c in controls:
        if _is_cookie_or_consent_item(c):
            continue
        label = str(c.get("label") or c.get("placeholder") or c.get("name") or "").strip()
        selector = str(c.get("selector") or "")
        if noise.match(label) or noise_sel.search(selector):
            continue
        out.append(c)
    return out


def _filter_bizflow_buttons(buttons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [b for b in buttons if not _is_cookie_or_consent_item(b)]


async def _dismiss_bizflow_overlays(page: Page) -> Dict[str, Any]:
    """Dismiss OneTrust/cookie overlays that can cover the BizFlow + Add button.

    These are site consent overlays, not HIP/BizFlow mutating actions. The helper first tries
    visible safe buttons, then hides leftover overlay containers so form capture does not mistake
    cookie controls for BizFlow controls.
    """
    audit: Dict[str, Any] = {"attempted": True, "clicked": [], "removed_overlay": False}
    selectors = [
        "button#accept-recommended-btn-handler",
        "button#onetrust-accept-btn-handler",
        "button#close-pc-btn-handler",
        "#onetrust-close-btn-container button",
        "button:has-text('Allow All')",
        "button:has-text('Accept All')",
        "button:has-text('Close preference center')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=500):
                text = ""
                try:
                    text = (await loc.inner_text(timeout=300)).strip()
                except Exception:
                    text = sel
                await loc.click(timeout=1000)
                audit["clicked"].append({"selector": sel, "text": text})
                await page.wait_for_timeout(350)
        except Exception:
            continue
    try:
        removed = await page.evaluate(r"""
() => {
  let removed=false;
  for (const sel of ['#onetrust-pc-sdk','#onetrust-banner-sdk','#ot-sdk-btn-floating','.onetrust-pc-dark-filter','.ot-sdk-row']) {
    for (const el of Array.from(document.querySelectorAll(sel))) {
      const r=el.getBoundingClientRect();
      if (r.width > 10 && r.height > 10) { el.style.display='none'; removed=true; }
    }
  }
  document.body.classList.remove('ot-sdk-show-settings');
  return removed;
}
""")
        audit["removed_overlay"] = bool(removed)
    except Exception:
        pass
    return audit


def _safe_bizflow_template_candidate(label: str, context: str = "") -> bool:
    """Return true only for the post-+Add template/card/link, never side-nav/home links."""
    text = re.sub(r"\s+", " ", f"{label or ''} {context or ''}".strip()).lower()
    label_l = re.sub(r"\s+", " ", (label or "").strip()).lower()
    if not label_l:
        return False
    if re.search(r"\b(home|dashboard|securelink|bizlink|document types?|rules?|maps?|transport profiles?|partners?|systems?|logout|profile|help|cookie|privacy|preference|manage)\b", label_l):
        return False
    if re.search(r"\b(save|submit|create\s*$|delete|remove|deploy|cancel|close|back|previous|pagination|next page)\b", label_l):
        return False
    return bool(re.search(r"template|b2b|pubsub|mapping|translation|passthrough|select|start|continue|next|flow|outbound|inbound", text))


async def _body_text_lower(page: Page, timeout: int = 1500) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=timeout)).lower()
    except Exception:
        return ""


async def _is_bizflow_template_picker_surface(page: Page) -> bool:
    txt = await _body_text_lower(page)
    return "create biz flow" in txt and ("search flow templates" in txt or "template" in txt or "flow template" in txt)


async def _is_bizflow_form_surface(page: Page) -> bool:
    try:
        gate = await assert_active_surface(page, "biz_flow")
        if gate.get("fatal"):
            return False
        text = str(gate.get("surface_text_sample") or "").lower()
        if "b2b-flow-pubsub-template" in text and not any(x in text for x in ["flow details", "configure source", "configure target", "configure routing"]):
            return False
        return True
    except Exception:
        txt = await _body_text_lower(page)
        if "create biz flow" not in txt:
            return False
        if "b2b-flow-pubsub-template" in txt and not any(x in txt for x in ["flow details", "configure source", "configure target", "configure routing"]):
            return False
        form_hints = [
            "flow details", "configure source", "configure target", "configure target(s)", "configure routing",
            "business flow name", "primary domain", "source document", "target document", "transport profile",
        ]
        return any(h in txt for h in form_hints)


async def _is_create_bizflow_surface(page: Page) -> bool:
    return await _is_bizflow_template_picker_surface(page) or await _is_bizflow_form_surface(page)


async def _wait_for_bizflow_form_surface(page: Page, timeout_ms: int = 7000) -> bool:
    deadline = max(1, timeout_ms // 500)
    for _ in range(deadline):
        if await _is_bizflow_form_surface(page):
            return True
        await page.wait_for_timeout(500)
    return await _is_bizflow_form_surface(page)


def _salvage_json_objects_from_array_prefix(text: str, array_key: str = "content") -> List[Dict[str, Any]]:
    """Recover complete JSON objects from a truncated gateway list body.

    The browser evidence body may be cut before the final closing brackets. This parser walks
    the `content` array prefix and extracts every complete object while respecting strings and
    nested braces.
    """
    if not text or f'"{array_key}"' not in text:
        return []
    start_key = text.find(f'"{array_key}"')
    start = text.find('[', start_key)
    if start < 0:
        return []
    out: List[Dict[str, Any]] = []
    depth = 0
    in_str = False
    esc = False
    obj_start: Optional[int] = None
    for i in range(start + 1, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == '{':
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == '}':
            if depth:
                depth -= 1
                if depth == 0 and obj_start is not None:
                    raw = text[obj_start:i+1]
                    try:
                        obj = json.loads(raw)
                        if isinstance(obj, dict):
                            out.append(obj)
                    except Exception:
                        pass
                    obj_start = None
        elif ch == ']' and depth == 0:
            break
    return out


def salvage_bizflow_records_from_truncated_body(body: Any, *, source_url: str = "", source: str = "api_truncated") -> List[Dict[str, Any]]:
    if not isinstance(body, str):
        return []
    if "flowId" not in body and "flowName" not in body:
        return []
    rows: List[Dict[str, Any]] = []
    for obj in _salvage_json_objects_from_array_prefix(body, "content"):
        rec = extract_bizflow_record(obj, source_url=source_url, source=source)
        if rec:
            rows.append(rec)
    return _dedupe_bizflow_records(rows)



def normalize_bizflow_tab_label(label: str) -> Optional[str]:
    text = re.sub(r"\s+", " ", (label or "").strip().lower())
    if not text:
        return None
    for canonical, aliases in BIZFLOW_TAB_ALIASES.items():
        if any(alias in text for alias in aliases):
            return canonical
    return None


def bizflow_multitab_plan() -> List[Dict[str, Any]]:
    return [
        {"tab": "Basic Details", "required_keys": ["flow_name", "flow_type", "primary_domain", "environment", "flow_description"]},
        {"tab": "Source Details", "required_keys": ["source_system", "source_document_type", "source_transport_profile"]},
        {"tab": "Target Details", "required_keys": ["target_system", "target_document_type", "target_transport_profile"]},
        {"tab": "Configure Routing", "required_keys": ["process_step_type", "mapping_identifier", "rule_name", "route_name"], "nested_add": True},
    ]


async def _governed_bizflow_click(
    page: Page, *, selector: str, action_label: str, timeout: int = 1500,
    mutation_risk: bool = False, structural_opener: bool = False,
) -> bool:
    """Execute a BizFlow control through the semantic BrowserSession gate.

    Raw Playwright remains available only for standalone utility/test pages that
    do not have a HIP BrowserSession attached. Under the semantic runtime, any
    semantic/MCP/effect failure is fail-closed and is never followed by force or
    DOM click fallback.
    """
    if not selector:
        return False
    loc = page.locator(selector).first
    try:
        if not (await loc.count() and await loc.is_visible(timeout=timeout) and await loc.is_enabled(timeout=timeout)):
            return False
    except Exception:
        return False
    session = getattr(page, "_hip_browser_session", None)
    governed = bool(semantic_runtime_enabled(page) and session is not None and hasattr(session, "click_and_wait"))
    if governed:
        previous_phase = getattr(session, "_active_phase_name", "")
        if not previous_phase:
            session._active_phase_name = "biz_flow"
        try:
            label = f"structural_opener {action_label}" if structural_opener else action_label
            await session.click_and_wait(
                action=label, locator=loc, selector=selector, mutation_risk=mutation_risk
            )
            return True
        finally:
            if not previous_phase:
                session._active_phase_name = previous_phase
    # Explicit legacy/standalone path only.
    try:
        await loc.click(timeout=timeout)
        return True
    except Exception:
        return False


async def _click_bizflow_template_link_after_add(page: Page) -> Dict[str, Any]:
    """Launch B2B-Flow-PubSub-Template from the template picker.

    The golden screenshots prove the correct path is: + Add -> template card ->
    overflow menu -> `Create Biz Flow`.  The Inbound/Outbound tags are rendered
    as disabled tags (`disable-tag-click`) and must not be treated as launch
    buttons.  This function therefore opens the overflow menu inside the exact
    B2B card and clicks the visible `Create Biz Flow` menu item.
    """
    audit: Dict[str, Any] = {"attempted": True, "clicked": False, "label": "", "selector": "", "surface_before": ""}
    await page.wait_for_timeout(900)
    if await _is_bizflow_form_surface(page):
        audit.update({"clicked": False, "label": "form already visible", "surface_before": "form"})
        return audit
    if not await _is_bizflow_template_picker_surface(page):
        audit.update({"clicked": False, "label": "template picker not visible", "surface_before": "unknown"})
        return audit
    audit["surface_before"] = "template_picker"

    # v2.2.4: live HIP can expose the template launcher as a real link/card
    # action directly after + Add (without requiring the overflow menu).  Prefer
    # that explicit link first.  Discovery is DOM-only; the physical click still
    # goes through the governed semantic/AutoWebGLM/Playwright-MCP executor.
    try:
        direct_link = await page.evaluate(r"""
() => {
  function visible(el){const r=el&&el.getBoundingClientRect?el.getBoundingClientRect():{width:0,height:0}; const s=el?getComputedStyle(el):null; return !!(r.width&&r.height&&s&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0');}
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){const parts=[]; let n=el; while(n&&n.nodeType===1&&parts.length<9){let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=(n.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.'); if(cls)p+='.'+cls; const par=n.parentElement; if(par){const sib=Array.from(par.children).filter(x=>x.tagName===n.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(n)+1)+')';} parts.unshift(p); n=par;} return parts.join(' > ');}
  const unsafe='nav,header,footer,.dds__side-nav,#onetrust-consent-sdk,#onetrust-pc-sdk,#onetrust-banner-sdk';
  const cards=Array.from(document.querySelectorAll('dds-card,.dds__card,.card,article,section,li,div.customCard'))
    .filter(visible).filter(el=>!el.closest(unsafe))
    .map(el=>({el,txt:clean(el.innerText||el.textContent||'')}))
    .filter(x=>/template|pubsub|biz\s*flow|b2b/i.test(x.txt));
  cards.sort((a,b)=>{
    const aa=/B2B-Flow-PubSub-Template/i.test(a.txt)?100:0;
    const bb=/B2B-Flow-PubSub-Template/i.test(b.txt)?100:0;
    const ar=a.el.getBoundingClientRect(), br=b.el.getBoundingClientRect();
    return (bb-aa) || ((ar.width*ar.height)-(br.width*br.height));
  });
  for(const row of cards){
    const actions=Array.from(row.el.querySelectorAll('a,button,[role=link],[role=button],dds-button')).filter(visible);
    const scored=[];
    for(const el of actions){
      const label=clean(el.innerText||el.textContent||el.getAttribute('aria-label')||el.getAttribute('title')||'');
      const low=label.toLowerCase();
      if(!label) continue;
      if(/^(inbound|outbound)$/i.test(label)) continue;
      if(/overflow|more actions|ellipsis|kebab/i.test(low)) continue;
      if(/delete|remove|deploy|save|submit/i.test(low)) continue;
      let score=0;
      if(/^create\s+biz\s+flow$/i.test(label)) score+=30;
      if(/use template|select template|start|open|continue|create biz flow/i.test(label)) score+=20;
      if(/B2B-Flow-PubSub-Template/i.test(label)) score+=18;
      const href=el.getAttribute('href')||'';
      if(href){
        try {
          const target=new URL(href, window.location.href);
          const norm=p=>(p||'/').replace(/\/+$/,'')||'/';
          if(target.origin!==window.location.origin || norm(target.pathname).toLowerCase()!==norm(window.location.pathname).toLowerCase()) continue;
        } catch(_) { continue; }
      }
      if(el.tagName.toLowerCase()==='a' || el.getAttribute('role')==='link') score+=8;
      if(href) score+=5;
      if(score>0) scored.push({selector:path(el),label,score,tag:el.tagName.toLowerCase(),href});
    }
    scored.sort((a,b)=>b.score-a.score);
    if(scored[0]) return {ok:true,...scored[0],method:'direct_template_card_link'};
  }
  return {ok:false, reason:'No direct template-card link/action found'};
}
""")
        audit["direct_link_candidate"] = direct_link
        if direct_link and direct_link.get("ok") and direct_link.get("selector"):
            if await _governed_bizflow_click(
                page, selector=direct_link["selector"],
                action_label=f"structural_opener launch BizFlow template link {direct_link.get('label') or 'template'}",
                timeout=3000, mutation_risk=False, structural_opener=True,
            ):
                audit.update({
                    "clicked": True, "label": direct_link.get("label", "template link"),
                    "selector": direct_link.get("selector", ""),
                    "method": "direct_template_card_link",
                })
                await page.wait_for_timeout(3200)
                if await _is_bizflow_form_surface(page):
                    audit["direct_link_form_verified"] = True
                    return audit
                audit["direct_link_form_verified"] = False
                # If the direct link changed the template surface but did not yet
                # expose the tabs, continue into the existing overflow/menu path.
    except Exception as exc:
        audit["direct_link_error"] = mask_sensitive_string(str(exc))[:900]

    try:
        # Resolve the smallest visible card containing the PubSub template and
        # then its overflow/action-menu button.  Do not ever return the page
        # container, tags, side-nav, or listing grid.
        cand = await page.evaluate(r"""
() => {
  function visible(el){
    const r=el&&el.getBoundingClientRect?el.getBoundingClientRect():{width:0,height:0};
    const s=el?getComputedStyle(el):null;
    return !!(r.width&&r.height&&s&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0');
  }
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){
    const parts=[];
    while(el&&el.nodeType===1&&parts.length<9){
      let p=el.tagName.toLowerCase();
      if(el.id){p+='#'+CSS.escape(el.id); parts.unshift(p); break;}
      const cls=(el.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.');
      if(cls)p+='.'+cls;
      const parent=el.parentElement;
      if(parent){const sib=Array.from(parent.children).filter(x=>x.tagName===el.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(el)+1)+')';}
      parts.unshift(p); el=parent;
    }
    return parts.join(' > ');
  }
  // Keep legacy safety test marker visible: home|dashboard.
  const unsafe='nav,header,footer,section.dds__side-nav,.dds__side-nav,#onetrust-consent-sdk,#onetrust-pc-sdk,#onetrust-banner-sdk,dds-drawer';
  const roots = Array.from(document.querySelectorAll('dds-card,.dds__card,.card,article,section,li,div.customCard'))
    .filter(visible)
    .filter(el => !el.closest(unsafe))
    .map(el => ({el, txt: clean(el.innerText||el.textContent||'')}))
    .filter(x => /B2B-Flow-PubSub-Template/i.test(x.txt));
  roots.sort((a,b)=>{
    const ar=a.el.getBoundingClientRect(), br=b.el.getBoundingClientRect();
    return (ar.width*ar.height) - (br.width*br.height);
  });
  const card = roots[0] && roots[0].el;
  if(!card) return {ok:false, reason:'B2B card not found'};
  const buttons = Array.from(card.querySelectorAll('button,[role=button],a,dds-button button')).filter(visible);
  const overflow = buttons.find(btn => {
    const label=clean(btn.innerText||btn.textContent||btn.getAttribute('aria-label')||btn.getAttribute('title')||'');
    const cls=String(btn.className||'')+' '+String(btn.getAttribute('class')||'')+' '+String(btn.querySelector('[class*=overflow]')?.className||'');
    return /overflow|action-menu|more|ellipsis/i.test(label+' '+cls) || btn.getAttribute('aria-haspopup') === 'true';
  });
  if(overflow) return {ok:true, selector:path(overflow), label:clean(overflow.innerText||overflow.getAttribute('aria-label')||'overflow'), method:'card_overflow_menu'};
  const direct = buttons.find(btn => /^Create\s+Biz\s+Flow$/i.test(clean(btn.innerText||btn.textContent||'')));
  if(direct) return {ok:true, selector:path(direct), label:'Create Biz Flow', method:'direct_create_bizflow'};
  return {ok:false, reason:'No overflow/create action inside B2B card', actions: buttons.map(b=>clean(b.innerText||b.textContent||b.getAttribute('aria-label')||'')).filter(Boolean).slice(0,12)};
}
""")
        audit["candidate"] = cand
        if not (cand and cand.get("ok") and cand.get("selector")):
            audit["reason"] = cand.get("reason") if isinstance(cand, dict) else "no candidate"
            return audit

        if not await _governed_bizflow_click(
            page, selector=cand["selector"],
            action_label=f"Open B2B template actions: {cand.get('label') or 'More Actions'}",
            timeout=2500, mutation_risk=False,
        ):
            audit["reason"] = "B2B template action menu could not be semantically dispatched"
            return audit
        audit.update({"overflow_clicked": True, "selector": cand.get("selector", ""), "label": cand.get("label", ""), "method": cand.get("method", "card_overflow_menu")})
        await page.wait_for_timeout(900)

        # Now click the visible menu item.  This is the correct wizard-launch
        # action, even though it contains the word Create; it is not the final
        # form Save/Create submit button.
        menu_cand = await page.evaluate(r"""
() => {
  function visible(el){ const r=el&&el.getBoundingClientRect?el.getBoundingClientRect():{width:0,height:0}; const s=el?getComputedStyle(el):null; return !!(r.width&&r.height&&s&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0'); }
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){ const parts=[]; while(el&&el.nodeType===1&&parts.length<9){ let p=el.tagName.toLowerCase(); if(el.id){p+='#'+CSS.escape(el.id); parts.unshift(p); break;} const cls=(el.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.'); if(cls)p+='.'+cls; const parent=el.parentElement; if(parent){const sib=Array.from(parent.children).filter(x=>x.tagName===el.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(el)+1)+')';} parts.unshift(p); el=parent;} return parts.join(' > '); }
  const menuRoots = Array.from(document.querySelectorAll('.dds__action-menu__menu,[role=menu],dds-action-menu,.dds__action-menu')).filter(visible);
  for(const root of menuRoots){
    const items = Array.from(root.querySelectorAll('button,[role=menuitem],dds-action-menu-item,div[role=none] button')).filter(visible);
    for(const item of items){
      const label=clean(item.innerText||item.textContent||item.getAttribute('aria-label')||'');
      if(/^Create\s+Biz\s+Flow$/i.test(label)) return {ok:true, selector:path(item), label, method:'visible_action_menu_item'};
    }
  }
  // Fallback: any visible button/menuitem with exact text, excluding final form containers.
  const all = Array.from(document.querySelectorAll('button,[role=menuitem],dds-action-menu-item button')).filter(visible);
  const item = all.find(el => /^Create\s+Biz\s+Flow$/i.test(clean(el.innerText||el.textContent||'')));
  return item ? {ok:true, selector:path(item), label:'Create Biz Flow', method:'global_exact_menu_item'} : {ok:false, reason:'Create Biz Flow menu item not visible'};
}
""")
        audit["menu_candidate"] = menu_cand
        if menu_cand and menu_cand.get("ok") and menu_cand.get("selector"):
            if not await _governed_bizflow_click(
                page, selector=menu_cand["selector"], action_label="Launch Create Biz Flow template wizard",
                timeout=2500, mutation_risk=False, structural_opener=True,
            ):
                audit["reason"] = "Create Biz Flow structural opener could not be semantically dispatched"
                return audit
            audit.update({"clicked": True, "label": menu_cand.get("label", "Create Biz Flow"), "menu_selector": menu_cand.get("selector", ""), "method": menu_cand.get("method", "visible_action_menu_item")})
            await page.wait_for_timeout(3500)
            if await _is_bizflow_form_surface(page):
                return audit
            audit["clicked_but_form_not_visible"] = True
            return audit
        audit["reason"] = menu_cand.get("reason") if isinstance(menu_cand, dict) else "Create Biz Flow menu item not found"
    except Exception as exc:
        audit["error"] = mask_sensitive_string(str(exc))[:700]
    return audit


async def _click_bizflow_tab(page: Page, tab_label: str) -> Dict[str, Any]:
    audit = {"tab": tab_label, "clicked": False, "selector": "", "method": ""}
    aliases = BIZFLOW_TAB_ALIASES.get(tab_label, [tab_label.lower()])
    # Prefer role=tab and exact/contains text.
    for alias in [tab_label, *aliases]:
        selectors = [
            f"[role=tab]:has-text('{alias}')",
            f"button:has-text('{alias}')",
            f"a:has-text('{alias}')",
            f".dds__tabs__tab:has-text('{alias}')",
            f".nav-link:has-text('{alias}')",
        ]
        for sel in selectors:
            try:
                if await _governed_bizflow_click(page, selector=sel, action_label=f"Open BizFlow tab {tab_label}", timeout=1000):
                    audit.update({"clicked": True, "selector": sel, "method": "selector"})
                    await page.wait_for_timeout(700)
                    return audit
            except Exception:
                continue
    js = r"""
({aliases}) => {
  function clean(s){ return (s||'').trim().replace(/\s+/g,' '); }
  function path(el){
    const parts=[]; while(el && el.nodeType===1 && parts.length<7){
      let p=el.tagName.toLowerCase(); if(el.id){p+='#'+CSS.escape(el.id); parts.unshift(p); break;}
      const cls=(el.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.'); if(cls)p+='.'+cls;
      const parent=el.parentElement; if(parent){const sib=Array.from(parent.children).filter(x=>x.tagName===el.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(el)+1)+')';}
      parts.unshift(p); el=parent;
    } return parts.join(' > ');
  }
  const lowerAliases = aliases.map(a=>String(a).toLowerCase());
  const candidates = Array.from(document.querySelectorAll('[role=tab],button,a,.dds__tabs__tab,.nav-link,li'));
  for (const el of candidates) {
    const r=el.getBoundingClientRect(); if(r.width<8 || r.height<8) continue;
    const label=clean(el.innerText||el.textContent||el.getAttribute('aria-label')||'');
    const low=label.toLowerCase();
    if(lowerAliases.some(a=>low.includes(a))) return {selector:path(el), label};
  }
  return null;
}
"""
    try:
        cand = await page.evaluate(js, {"aliases": aliases})
        if cand and cand.get("selector"):
            if await _governed_bizflow_click(page, selector=cand["selector"], action_label=f"Open BizFlow tab {tab_label}", timeout=1000):
                audit.update({"clicked": True, "selector": cand["selector"], "method": "js_discovery_semantic_dispatch", "label": cand.get("label", "")})
                await page.wait_for_timeout(700)
                return audit
    except Exception as exc:
        audit["error"] = str(exc)
    return audit



async def _current_bizflow_tab(page: Page) -> str:
    """Return the portal's currently selected BizFlow tab when provable."""
    try:
        label = await page.evaluate(r"""
() => {
  function visible(el){const r=el&&el.getBoundingClientRect?el.getBoundingClientRect():{width:0,height:0}; const s=el?getComputedStyle(el):null; return !!(r.width&&r.height&&s&&s.display!=='none'&&s.visibility!=='hidden');}
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  const selected = Array.from(document.querySelectorAll('[role=tab][aria-selected=true],.dds__tabs__tab--active,.dds__tabs__tab.active,.nav-link.active'))
    .filter(visible);
  for(const el of selected){
    const text=clean(el.innerText||el.textContent||el.getAttribute('aria-label')||'');
    if(text) return text;
  }
  const panels = Array.from(document.querySelectorAll('[role=tabpanel],dds-tab-panel,.dds__tabs__pane'))
    .filter(el=>visible(el) && el.getAttribute('aria-hidden')!=='true');
  for(const p of panels){
    const labelled=p.getAttribute('aria-labelledby');
    if(labelled){const tab=document.getElementById(labelled); if(tab){const text=clean(tab.innerText||tab.textContent||tab.getAttribute('aria-label')||''); if(text)return text;}}
  }
  return '';
}
""")
        return str(label or "").strip()
    except Exception:
        return ""


def _bizflow_tab_matches(requested: str, actual: str) -> bool:
    wanted = str(requested or "").strip()
    current = str(actual or "").strip()
    if not wanted or not current:
        return False
    aliases = [wanted, *BIZFLOW_TAB_ALIASES.get(wanted, [])]
    low = re.sub(r"\s+", " ", current.lower()).strip()
    return any(re.sub(r"\s+", " ", str(a).lower()).strip() in low or low in re.sub(r"\s+", " ", str(a).lower()).strip() for a in aliases if str(a).strip())


async def _ensure_bizflow_tab_open(page: Page, tab_label: str, *, max_steps: int = 3) -> Dict[str, Any]:
    """Bounded ReAct tab transition: observe -> click -> prove -> reobserve.

    A failed tab click must never be followed by filling controls from the prior
    tab.  The semantic click path already uses AutoWebGLM + Playwright MCP and
    evidence fusion; this wrapper adds effect proof and bounded autonomous retry.
    """
    audit: Dict[str, Any] = {"tab": tab_label, "pass": False, "steps": []}
    current = await _current_bizflow_tab(page)
    if _bizflow_tab_matches(tab_label, current):
        audit.update({"pass": True, "status": "already_active", "active_tab": current})
        return audit
    backend = getattr(page, "_hip_playwright_mcp_backend", None)
    for step_no in range(1, max(1, int(max_steps)) + 1):
        before = await _current_bizflow_tab(page)
        step: Dict[str, Any] = {"step": step_no, "observe": {"active_tab": before}, "plan": "open_requested_tab"}
        if backend is not None:
            try:
                found = await backend.find(text=tab_label)
                step["browser_find"] = {"text": str((found or {}).get("text") or "")[:2500], "fallback": bool((found or {}).get("fallback"))}
            except Exception as exc:
                step["browser_find"] = {"error": mask_sensitive_string(str(exc))[:800]}
        click = await _click_bizflow_tab(page, tab_label)
        step["action"] = mask_sensitive_data(click)
        await page.wait_for_timeout(650 + step_no * 150)
        after = await _current_bizflow_tab(page)
        step["effect"] = {"active_tab": after, "verified": _bizflow_tab_matches(tab_label, after)}
        audit["steps"].append(step)
        if step["effect"]["verified"]:
            audit.update({"pass": True, "status": "verified", "active_tab": after, "attempts": step_no})
            return audit
        await _dismiss_bizflow_overlays(page)
        await page.wait_for_timeout(250)
    audit.update({"status": "blocked", "error_code": "HIP_BIZFLOW_TAB_NOT_OPENED", "active_tab": await _current_bizflow_tab(page)})
    raise RuntimeError(f"HIP_BIZFLOW_TAB_NOT_OPENED: could not prove active BizFlow tab '{tab_label}' after bounded ReAct retries")

async def _click_bizflow_continue(page: Page) -> Dict[str, Any]:
    audit = {"attempted": True, "clicked": False, "label": ""}
    selectors = [
        "button:has-text('Next')", "button:has-text('Continue')", "button:has-text('Proceed')",
        "[role=button]:has-text('Next')", "[role=button]:has-text('Continue')", "[role=button]:has-text('Proceed')",
    ]
    bad = re.compile(r"save|submit|create|delete|remove|deploy|cancel|close", re.I)
    for sel in selectors:
        try:
            locs = page.locator(sel)
            n = min(await locs.count(), 10)
            for i in range(n):
                loc = locs.nth(i)
                txt = ""
                try: txt = (await loc.inner_text(timeout=500)).strip()
                except Exception: pass
                if bad.search(txt):
                    continue
                if await _governed_bizflow_click(page, selector=sel, action_label=txt or "Continue BizFlow wizard", timeout=1000):
                    audit.update({"clicked": True, "label": txt, "selector": sel})
                    await page.wait_for_timeout(1000)
                    return audit
        except Exception:
            continue
    js = r"""
() => {
  function clean(s){return (s||'').trim().replace(/\s+/g,' ')}
  function path(el){const parts=[]; while(el&&el.nodeType===1&&parts.length<8){let p=el.tagName.toLowerCase(); if(el.id){p+='#'+CSS.escape(el.id); parts.unshift(p); break;} const cls=(el.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.'); if(cls)p+='.'+cls; const parent=el.parentElement; if(parent){const sib=Array.from(parent.children).filter(x=>x.tagName===el.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(el)+1)+')';} parts.unshift(p); el=parent;} return parts.join(' > ');}
  const roots = Array.from(document.querySelectorAll('.dds__drawer, form, main, body')).filter(r=>/create biz flow|flow details|configure source|configure target|configure routing|businessFlowName/i.test(clean(r.innerText||'')));
  const root = roots[0] || document.body;
  const bad=/save|submit|create\s*$|delete|remove|deploy|cancel|close|pagination|next page|previous page|cookie|preference/i;
  const good=/^(next|continue|proceed|done|add source|add target)$/i;
  const cands=[];
  for(const el of Array.from(root.querySelectorAll('button,[role=button],a,dds-button,.dds__button'))){
    const r=el.getBoundingClientRect(); if(r.width<8||r.height<8) continue;
    const label=clean(el.innerText||el.textContent||el.getAttribute('aria-label')||el.getAttribute('title')||'');
    const cls=(el.className||'').toString();
    if(!label || bad.test(label+' '+cls)) continue;
    if(good.test(label)) cands.push({selector:path(el), label, score:(/next|continue|proceed/i.test(label)?10:1)+(r.top>window.innerHeight*0.5?2:0)});
  }
  cands.sort((a,b)=>b.score-a.score);
  return cands[0]||null;
}
"""
    try:
        cand = await page.evaluate(js)
        if cand and cand.get("selector"):
            if await _governed_bizflow_click(page, selector=cand["selector"], action_label=cand.get("label", "") or "Continue BizFlow wizard", timeout=1200):
                audit.update({"clicked": True, "label": cand.get("label", ""), "selector": cand.get("selector", ""), "method": "scoped_js_fallback_semantic_dispatch"})
                await page.wait_for_timeout(1000)
                return audit
    except Exception as exc:
        audit["js_error"] = str(exc)
    return audit


async def _click_configure_routing_add(page: Page) -> Dict[str, Any]:
    audit = {"attempted": True, "clicked": False, "label": "", "selector": ""}
    bad = re.compile(r"save|submit|create\s*$|delete|remove|deploy|cancel|close|pagination|next page|previous", re.I)
    js = r"""
() => {
  function clean(s){return (s||'').trim().replace(/\s+/g,' ')}
  function path(el){const parts=[]; while(el&&el.nodeType===1&&parts.length<8){let p=el.tagName.toLowerCase(); if(el.id){p+='#'+CSS.escape(el.id); parts.unshift(p); break;} const cls=(el.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.'); if(cls)p+='.'+cls; const parent=el.parentElement; if(parent){const sib=Array.from(parent.children).filter(x=>x.tagName===el.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(el)+1)+')';} parts.unshift(p); el=parent;} return parts.join(' > ');}
  const root = Array.from(document.querySelectorAll('main,.dds__drawer,form,body')).find(x=>/configure routing|routing/i.test(clean(x.innerText||''))) || document.body;
  const cands=[];
  for(const el of Array.from(root.querySelectorAll('button,[role=button],a,dds-button'))){
    const r=el.getBoundingClientRect(); if(r.width<8||r.height<8) continue;
    const label=clean(el.innerText||el.textContent||el.getAttribute('aria-label')||el.getAttribute('title')||'');
    const cls=(el.className||'').toString();
    if(/save|submit|create\s*$|delete|remove|deploy|cancel|close|pagination|next page|previous/i.test(label+' '+cls)) continue;
    const hasPlus= /(^|\s)\+(\s|$)|add/i.test(label) || !!el.querySelector('svg,path,use,[class*=plus],[name*=plus],dds-icon');
    const ctx=clean((el.closest('section,form,.dds__drawer,main')||root).innerText||'').slice(0,500);
    if(hasPlus && /routing|route/i.test(ctx)) cands.push({selector:path(el), label, score:(/add/i.test(label)?3:0)+(/routing|route/i.test(ctx)?5:0)});
  }
  cands.sort((a,b)=>b.score-a.score);
  return cands[0]||null;
}
"""
    try:
        cand = await page.evaluate(js)
        if cand and cand.get("selector"):
            txt = cand.get("label", "") or "Add"
            if not bad.search(txt) and await _governed_bizflow_click(
                page, selector=cand["selector"], action_label=f"structural_opener Configure Routing {txt}", timeout=1000
            ):
                audit.update({"clicked": True, "label": txt, "selector": cand.get("selector", "")})
                await page.wait_for_timeout(1000)
                return audit
    except Exception as exc:
        audit["error"] = str(exc)
    return audit


def _bizflow_obj(input_data: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return objects.biz_flow from the full input.json safely."""
    if not isinstance(input_data, dict):
        return {}
    objects = input_data.get("objects") if isinstance(input_data.get("objects"), dict) else {}
    bf = objects.get("biz_flow") or objects.get("bizflow") or input_data.get("biz_flow") or input_data.get("bizflow") or {}
    return bf if isinstance(bf, dict) else {}


def _list_of_dicts(value: Any) -> List[Dict[str, Any]]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def build_bizflow_nested_row_plan(input_data: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Build the exact nested +Add plan required by the BizFlow golden screens.

    Generic repeatable-row detection is not enough for BizFlow. The UHAUL golden
    screenshots and runtime evidence show the portal requires specific row-level
    Add clicks inside the wizard:

    * Configure Source -> `+ Add` beside Attribute / Flow Identifier rows.
    * Configure Target(s) -> `+ Add` beside Process Step.
    * Configure Routing -> top-level `+ Add`, then nested `+ Add` beside
      Conditions and `+ Add` beside Actions inside the routing rule drawer.
    """
    bf = _bizflow_obj(input_data)
    flow_rows = _list_of_dicts((bf.get("flow_identifiers") or {}).get("conditions") if isinstance(bf.get("flow_identifiers"), dict) else [])
    process_steps = _list_of_dicts(bf.get("process_steps"))
    routing = bf.get("configure_routing") if isinstance(bf.get("configure_routing"), dict) else {}
    route_rows = _list_of_dicts((routing.get("conditions") or {}).get("rows") if isinstance(routing.get("conditions"), dict) else [])
    actions_raw = routing.get("actions") if isinstance(routing, dict) else None
    action_rows = _list_of_dicts(actions_raw) or ([actions_raw] if isinstance(actions_raw, dict) and actions_raw else [])

    plan: List[Dict[str, Any]] = []
    if flow_rows:
        plan.append({
            "section": "Configure Source Attribute Rows",
            "tab": "Source Details",
            "aliases": ["attribute", "attribute name", "flow identifier", "condition"],
            "row_count_from_input": len(flow_rows),
            "add_clicks_needed": max(0, len(flow_rows) - 1),
            "row_values": mask_sensitive_data(flow_rows),
        })
    if process_steps:
        plan.append({
            "section": "Configure Target Process Step Rows",
            "tab": "Target Details",
            "aliases": ["process step", "process steps", "step", "mapping transformer", "enricher"],
            "row_count_from_input": len(process_steps),
            "add_clicks_needed": len(process_steps),
            "row_values": mask_sensitive_data(process_steps),
        })
    if route_rows:
        plan.append({
            "section": "Configure Routing Condition Rows",
            "tab": "Configure Routing + Add",
            "aliases": ["condition", "conditions", "execute action", "attribute"],
            "row_count_from_input": len(route_rows),
            "add_clicks_needed": max(0, len(route_rows) - 1),
            "row_values": mask_sensitive_data(route_rows),
        })
    if action_rows:
        plan.append({
            "section": "Configure Routing Action Rows",
            "tab": "Configure Routing + Add",
            "aliases": ["action", "actions", "route document", "target"],
            "row_count_from_input": len(action_rows),
            "add_clicks_needed": max(1, len(action_rows) - 1),
            "row_values": mask_sensitive_data(action_rows),
        })
    return plan



async def _scroll_bizflow_to_section(page: Page, aliases: List[str] | None = None, section: str = "") -> Dict[str, Any]:
    """Bring the relevant BizFlow subsection into view before searching buttons/fields.

    Many HIP/DDS child rows live below the current viewport. Earlier runs had a
    full DOM/dropdown inventory but still missed Process Step +Add because the
    target subsection was not visible when the row-add scanner ran. This helper
    is deliberately geometry based and safe: it only scrolls, it does not click.
    """
    aliases = [str(a) for a in (aliases or []) if str(a).strip()]
    patterns = list(aliases)
    if section:
        patterns.append(section)
    if not patterns:
        return {"scrolled": False, "reason": "no aliases"}
    try:
        root_info = await active_form_root_info(page, "biz_flow")
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        result = await page.evaluate(r"""
({rootSel, patterns}) => {
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const st=getComputedStyle(el); return !!(r.width && r.height && st.display!=='none' && st.visibility!=='hidden' && st.opacity!=='0'); }
  const root=document.querySelector(rootSel)||document.body;
  const pats=(patterns||[]).map(p=>String(p||'').toLowerCase()).filter(Boolean);
  const rx=new RegExp(pats.map(p=>p.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|'),'i');
  const els=Array.from(root.querySelectorAll('legend,h1,h2,h3,h4,h5,h6,label,span,div,button,dds-label')).filter(visible).map(el=>{const r=el.getBoundingClientRect(); return {el,t:clean(el.innerText||el.textContent||''),x:r.x,y:r.y,w:r.width,h:r.height};}).filter(x=>x.t && x.t.length<180 && rx.test(x.t));
  if(!els.length){
    const scroller=root.closest('.dds__drawer__body,.dds__modal__body') || root;
    try{ scroller.scrollBy({top:650,left:0,behavior:'instant'}); }catch(e){ window.scrollBy(0,650); }
    return {scrolled:true, fallback:true, matches:0};
  }
  // Prefer visible subsection labels below the wizard tab bar and near the left side.
  els.sort((a,b)=> (b.y-a.y) || (a.x-b.x));
  const hit=els[0];
  try{ hit.el.scrollIntoView({block:'center', inline:'nearest'}); }catch(e){ window.scrollTo(0, Math.max(0, hit.y + window.scrollY - window.innerHeight/2)); }
  return {scrolled:true, text:hit.t, x:hit.x, y:hit.y, matches:els.length};
}
""", {"rootSel": root_sel, "patterns": patterns})
        await page.wait_for_timeout(250)
        return result if isinstance(result, dict) else {"scrolled": True}
    except Exception as exc:
        return {"scrolled": False, "error": mask_sensitive_string(str(exc))}

async def _click_bizflow_section_add(page: Page, aliases: List[str], *, tab_label: str = "", section: str = "") -> Dict[str, Any]:
    """Click the row-level `+ Add` near a BizFlow section label.

    It scopes to the active Create Biz Flow form, only considers real clickable
    controls with Add/plus semantics, rejects large app-shell/header elements,
    and prefers buttons physically near labels like Attribute, Process Step,
    Conditions, and Actions.  It explicitly rejects DDS dropdown chevrons;
    those are option openers, not row creators.
    """
    audit: Dict[str, Any] = {"section": section, "tab": tab_label, "aliases": aliases, "clicked": False}
    audit["pre_scroll"] = await _scroll_bizflow_to_section(page, aliases, section)
    before_state = await _capture_bizflow_form_state(page, label=f"before +Add {tab_label}:{section}")
    try:
        root_info = await active_form_root_info(page, "biz_flow")
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        cands = await page.evaluate(r"""
({rootSel, aliases}) => {
  function clean(s){ return String(s||'').replace(/\s+/g,' ').trim(); }
  function low(s){ return clean(s).toLowerCase(); }
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(); const st=getComputedStyle(el);
    return !!(r.width && r.height && st.display!=='none' && st.visibility!=='hidden' && st.opacity!=='0');
  }
  function path(el){
    const parts=[]; let n=el;
    for(let depth=0; n && n.nodeType===1 && depth<8; depth++,n=n.parentElement){
      let p=n.tagName.toLowerCase();
      if(n.id){ p += '#'+CSS.escape(n.id); parts.unshift(p); break; }
      const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,3);
      if(cls.length) p += '.'+cls.map(c=>CSS.escape(c)).join('.');
      const parent=n.parentElement;
      if(parent){ const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName); if(same.length>1) p += ':nth-of-type('+(same.indexOf(n)+1)+')'; }
      parts.unshift(p);
    }
    return parts.join(' > ');
  }
  let root=document.querySelector(rootSel)||document.body;
  // If a DDS drawer/modal is open, it is the active BizFlow form surface.
  // Do not let background listing/table Add buttons compete with nested drawer +Add buttons.
  const visibleSurfaces=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,dds-modal,.dds__modal'))
    .filter(visible)
    .map(el=>{const r=el.getBoundingClientRect(); return {el, area:r.width*r.height, z:Number(getComputedStyle(el).zIndex||0)||0};})
    .sort((a,b)=> (a.z-b.z) || (a.area-b.area));
  if(visibleSurfaces.length) root=visibleSurfaces[visibleSurfaces.length-1].el;
  const aliasList=(aliases||[]).map(low).filter(Boolean);
  const addRx=/^(\+\s*)?add$/i;
  const bad=/\b(save|create(?!\s+biz\s+flow)|submit|delete|remove|deploy|enable|disable|confirm|publish|update|cancel|close|back|reset|next|previous|pagination|items per page)\b/i;
  const textNodes=Array.from(root.querySelectorAll('label,legend,h1,h2,h3,h4,h5,span,div,dds-label')).filter(visible).map(el=>{
    const r=el.getBoundingClientRect(); const t=clean(el.innerText||el.textContent||'');
    return {el,t,low:low(t),x:r.x,y:r.y,w:r.width,h:r.height,cx:r.x+r.width/2,cy:r.y+r.height/2};
  }).filter(x=>x.t && x.t.length<160);
  const raw=Array.from(root.querySelectorAll('button,a,[role="button"],dds-button,dds-link'));
  const cands=[];
  for(const holder of raw){
    if(!visible(holder)) continue;
    const click = holder.matches('button,a,[role="button"]') ? holder : (holder.querySelector('button,a,[role="button"]') || holder);
    if(!visible(click)) continue;
    const r=click.getBoundingClientRect();
    if(r.width>260 || r.height>80) continue;
    const cls=String(click.className||'')+' '+String(holder.className||'');
    const hostPath = path(click).toLowerCase();
    const rawLabel=clean(click.innerText||click.textContent||holder.innerText||holder.textContent||'');
    const aria=clean(click.getAttribute('aria-label')||holder.getAttribute('aria-label')||click.getAttribute('title')||holder.getAttribute('title')||'');
    // Critical: DDS dropdown chevrons/search buttons expose SVG/path/use nodes,
    // so older logic misclassified them as `+ Add`.  A row-creation Add must not
    // be inside a dropdown/search widget and must have explicit Add text/aria,
    // or be a small standalone plus-like button outside an input wrapper.
    const inPicker = !!click.closest('.dds__dropdown, dds-dropdown, .dds__search, dds-search, .dds__input-container, .dds__dropdown__input-wrapper');
    const isChevron = /dropdown__chevron|search__button|action-submit/i.test(cls) || /dropdown|search/.test(hostPath);
    if(inPicker || isChevron) continue;
    const explicitAdd = addRx.test(rawLabel) || /^add$/i.test(aria) || /\badd\b/i.test(aria);
    const standalonePlus = /(^|\s)\+(\s|$)/.test(rawLabel) || /\bplus\b/i.test(aria) || /add-circle|plus-circle|icon-plus/i.test(cls);
    const hasPlusIcon = !!click.querySelector('[class*="plus" i],[name*="plus" i],[aria-label*="plus" i],[title*="plus" i]');
    const isAdd = explicitAdd || (standalonePlus && !inPicker) || (hasPlusIcon && !inPicker && r.width<=64 && r.height<=64);
    const label=rawLabel || aria || (standalonePlus || hasPlusIcon ? 'Add' : '');
    if(!isAdd || bad.test(label) || bad.test(aria)) continue;
    const sectionEl=click.closest('fieldset,section,.dds__card,.dds__table,.dds__row,.dds__container,.dds__form__section,.dds__drawer,form') || root;
    const ctx=low(sectionEl.innerText||'').slice(0,2500);
    let aliasHits=aliasList.filter(a=>ctx.includes(a) || low(label).includes(a) || low(aria).includes(a)).length;
    let nearest=999999, nearestText='';
    for(const t of textNodes){
      const match=aliasList.some(a=>t.low.includes(a));
      if(!match) continue;
      const dist=Math.abs(t.cy-(r.y+r.height/2)) + Math.max(0, t.x-r.x)*2 + Math.max(0, r.x-(t.x+t.w+360))*0.25;
      if(dist<nearest){ nearest=dist; nearestText=t.t; }
    }
    if(nearest<999999) aliasHits++;
    let score=0;
    if(explicitAdd) score+=50;
    if(standalonePlus || hasPlusIcon) score+=10;
    score+=aliasHits*30;
    if(nearest<180) score+=40; else if(nearest<360) score+=20; else if(nearest<999999) score+=5;
    if(/attribute|condition|action|process|step|routing/i.test(ctx)) score+=8;
    if(click.closest('.app__header, header, nav, section.dds__side-nav')) score-=200;
    if(aliasList.length && aliasHits===0) score-=70;
    if(score>0) cands.push({selector:path(click), label, context:ctx.slice(0,320), score, alias_hits:aliasHits, nearest_label:nearestText, box:{x:r.x,y:r.y,width:r.width,height:r.height}, explicit_add: explicitAdd, standalone_plus: standalonePlus, rejected_dropdown_chevrons: true});
  }
  // Fallback: HIP sometimes renders row-level Add as an icon-only DDS control with no text/aria.
  // Use geometry: find a visible section anchor (Process Step, Conditions, Actions, Attribute),
  // then choose the nearest small clickable to the right or just above the empty table area.
  // This fallback still rejects dropdown/search widgets and app/header controls.
  const anchors=textNodes.filter(t=>aliasList.some(a=>t.low.includes(a)) || /no process steps added|no conditions added|no actions added/i.test(t.t));
  const geomRaw=Array.from(root.querySelectorAll('button,a,[role="button"],dds-button,dds-link,[class*="plus" i],[class*="add" i],[aria-label*="add" i],[title*="add" i]'));
  for(const holder of geomRaw){
    if(!visible(holder)) continue;
    const click = holder.matches('button,a,[role="button"]') ? holder : (holder.querySelector('button,a,[role="button"]') || holder);
    if(!visible(click)) continue;
    const r=click.getBoundingClientRect();
    if(r.width>180 || r.height>70 || r.width<8 || r.height<8) continue;
    const cls=String(click.className||'')+' '+String(holder.className||'');
    const hp=path(click).toLowerCase();
    const rawLabel=clean(click.innerText||click.textContent||holder.innerText||holder.textContent||'');
    const aria=clean(click.getAttribute('aria-label')||holder.getAttribute('aria-label')||click.getAttribute('title')||holder.getAttribute('title')||'');
    if(click.closest('.dds__dropdown, dds-dropdown, .dds__search, dds-search, .dds__input-container, .dds__dropdown__input-wrapper')) continue;
    if(/dropdown|search|pagination|previous|next|cancel|reset|submit|save|delete|remove|deploy/i.test(cls+' '+hp+' '+rawLabel+' '+aria)) continue;
    let best=999999, bestText='';
    for(const a of anchors){
      const cy=r.y+r.height/2;
      const dx = Math.max(0, a.x - (r.x+r.width)) + Math.max(0, r.x - (a.x+a.w+520));
      const dy = Math.abs((a.y+a.h/2)-cy);
      // Section-header plus may be to the right on the same line; table Add may be above/right of an empty-state row.
      const dist = dy + dx*0.25 + Math.max(0, a.y-r.y)*0.05;
      if(dist<best){ best=dist; bestText=a.t; }
    }
    const explicit = addRx.test(rawLabel) || /\badd\b/i.test(rawLabel+' '+aria);
    const plusish = /\+|plus|add-circle|icon-plus|dds__icon--add/i.test(rawLabel+' '+aria+' '+cls+' '+hp) || (r.width<=90 && r.height<=50 && anchors.length);
    if(!explicit && !plusish) continue;
    let score= explicit ? 120 : 55;
    if(best<90) score+=90; else if(best<180) score+=70; else if(best<360) score+=35; else score-=70;
    if(/no process steps added/i.test(bestText)) score+=80;
    if(/conditions?|actions?|attribute|process/i.test(bestText)) score+=50;
    if(score>0) cands.push({selector:path(click), label: rawLabel || aria || 'Add', context: bestText, score, alias_hits: 1, nearest_label: bestText, box:{x:r.x,y:r.y,width:r.width,height:r.height}, geometric_fallback:true, rejected_dropdown_chevrons:true});
  }
  // De-duplicate and sort.
  const seenSel=new Set();
  const uniq=[];
  for(const c of cands){ if(!c.selector || seenSel.has(c.selector)) continue; seenSel.add(c.selector); uniq.push(c); }
  uniq.sort((a,b)=>b.score-a.score || a.box.y-b.box.y || a.box.x-b.box.x);
  return uniq.slice(0,8);
}
""", {"rootSel": root_sel, "aliases": aliases})
        audit["candidates"] = cands if isinstance(cands, list) else []
        # Strict section intent filtering. Runtime evidence showed the generic
        # scorer could click Reset or the wrong section's Add because nearby text
        # contained an alias.  Keep only candidates whose nearest label/context
        # belongs to the requested BizFlow child section.
        try:
            sec_l = str(section or "").lower()
            def _cand_text(c: Dict[str, Any]) -> str:
                return (str(c.get("label") or "") + " " + str(c.get("nearest_label") or "") + " " + str(c.get("context") or "") + " " + str(c.get("selector") or "")).lower()
            def _ok_for_section(c: Dict[str, Any]) -> bool:
                txt = _cand_text(c)
                lab = str(c.get("label") or "").strip().lower()
                if lab in {"reset", "cancel", "back", "close"}:
                    return False
                if "source attribute" in sec_l:
                    return ("attribute" in txt or "attributes" in txt) and not any(x in txt for x in ["process step", "process steps", "actions :", "conditions :", "execute action"] )
                if "target process step" in sec_l:
                    if "add target" in lab or "dds-tabs-label" in txt or "target details" in lab:
                        return False
                    return ("process step" in txt or "process steps" in txt or "no process steps" in txt or "create process step" in txt) and "reset" not in txt
                if "routing condition" in sec_l:
                    return ("condition" in txt or "execute action" in txt or "create condition" in txt) and "actions :" not in txt and "action name" not in txt
                if "routing action" in sec_l:
                    return ("actions" in txt or "action name" in txt or "route document" in txt) and "condition" not in txt and "execute action" not in txt
                return True
            filtered = [c for c in (cands or []) if isinstance(c, dict) and _ok_for_section(c)]
            if filtered:
                cands = filtered
            else:
                audit["strict_section_filter_empty"] = True
        except Exception as exc:
            audit["strict_section_filter_error"] = mask_sensitive_string(str(exc))
    except Exception as exc:
        audit["error"] = mask_sensitive_string(str(exc))
        cands = []
    bad = re.compile(r"\b(save|submit|delete|remove|deploy|enable|disable|confirm|publish|update|cancel|close|reset|back)\b", re.I)
    sec_l_for_semantics = str(section or "").lower()
    before_process_rows = await _count_bizflow_process_step_rows(page) if "target process step" in sec_l_for_semantics else 0
    for cand in cands or []:
        selector = str((cand or {}).get("selector") or "")
        label = str((cand or {}).get("label") or "")
        if not selector or bad.search(label):
            continue
        audit["candidate"] = cand
        try:
            clicked = await _governed_bizflow_click(
                page, selector=selector,
                action_label=f"structural_opener Add BizFlow {section} row: {label or 'Add'}",
                timeout=1400, mutation_risk=False, structural_opener=True,
            )
            if not clicked:
                audit.setdefault("rejected_clicks", []).append({
                    "selector": selector, "label": label,
                    "reason": "semantic/governed dispatch did not execute",
                })
                continue
            await page.wait_for_timeout(900)
            after_state = await _capture_bizflow_form_state(page, label=f"after +Add {tab_label}:{section}")
            delta = _bizflow_state_delta(before_state, after_state)
            if "target process step" in sec_l_for_semantics:
                after_rows = await _count_bizflow_process_step_rows(page)
                audit.setdefault("semantic_row_counts", []).append({
                    "mode": "semantic", "before": before_process_rows, "after": after_rows,
                    "selector": selector, "label": label,
                })
                if after_rows <= before_process_rows:
                    audit.setdefault("rejected_clicks", []).append({
                        "selector": selector, "label": label,
                        "reason": "Process Step +Add did not increase app-process-step accordion row count",
                        "state_delta": delta,
                    })
                    continue
            audit.update({
                "clicked": True, "selector": selector, "label": label,
                "click_mode": "semantic_governed", "state_delta": delta,
            })
            return audit
        except Exception as exc:
            audit.setdefault("rejected_clicks", []).append({
                "selector": selector, "label": label,
                "reason": "semantic/governed dispatch failed closed",
                "error": mask_sensitive_string(str(exc)),
            })
            continue
    audit.setdefault("reason", "no safe row-level BizFlow +Add candidate clicked")
    after_state = await _capture_bizflow_form_state(page, label=f"after no +Add {tab_label}:{section}")
    audit["state_delta"] = _bizflow_state_delta(before_state, after_state)
    return audit


async def _apply_bizflow_nested_row_adds(page: Page, input_data: Dict[str, Any] | None, tab_label: str) -> Dict[str, Any]:
    """Apply the exact BizFlow nested Add clicks for the current tab."""
    plan_all = build_bizflow_nested_row_plan(input_data or {})
    wanted = []
    tab_norm = normalize_bizflow_tab_label(tab_label) or tab_label
    for item in plan_all:
        item_tab = str(item.get("tab") or "")
        item_tab_cmp = normalize_bizflow_tab_label(item_tab) or item_tab
        if item_tab_cmp == tab_norm or ("Routing" in item_tab and "Routing" in tab_label and "+ Add" in item_tab):
            wanted.append(item)
    audit: Dict[str, Any] = {"tab": tab_label, "sections": mask_sensitive_data(wanted), "clicks": [], "summary": {"planned_add_clicks": 0, "clicked": 0, "failed": 0}}
    for item in wanted:
        clicks_needed = max(0, int(item.get("add_clicks_needed") or 0))
        # Convert planned clicks to effect-based clicks.  Some BizFlow drawer
        # sections open with one empty row already present; clicking +Add anyway
        # creates duplicate rows (seen in 015538 for Actions).
        section_name = str(item.get("section") or "")
        try:
            desired_rows = max(0, int(item.get("row_count_from_input") or 0))
            existing_rows = 0
            if "Routing Action" in section_name:
                existing_rows = len(await _find_grid_rows_in_section(page, r"^Actions\s*:?$", phase="biz_flow", max_below_px=1200))
            elif "Routing Condition" in section_name:
                existing_rows = len(await _find_grid_rows_in_section(page, r"^Conditions\s*:?$", phase="biz_flow", max_below_px=1500))
            elif "Process Step" in section_name:
                existing_rows = await _count_bizflow_process_step_rows(page)
            if desired_rows and existing_rows:
                clicks_needed = max(0, desired_rows - existing_rows)
        except Exception:
            pass
        audit["summary"]["planned_add_clicks"] += clicks_needed
        aliases = [str(x) for x in (item.get("aliases") or [])]
        for i in range(clicks_needed):
            click = await _click_bizflow_section_add(page, aliases, tab_label=tab_label, section=str(item.get("section") or ""))
            click["click_index"] = i + 1
            audit["clicks"].append(click)
            if click.get("clicked"):
                audit["summary"]["clicked"] += 1
            else:
                audit["summary"]["failed"] += 1
    return mask_sensitive_data(audit)


async def _find_visible_controls_for_label(page: Page, label_pattern: str, *, phase: str = "biz_flow") -> List[Dict[str, Any]]:
    try:
        root_info = await active_form_root_info(page, phase)
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        rows = await page.evaluate(r"""
({rootSel, pattern}) => {
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0'); }
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){ const parts=[]; let n=el; for(let d=0;n&&n.nodeType===1&&d<8;d++,n=n.parentElement){ let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,3); if(cls.length)p+='.'+cls.map(c=>CSS.escape(c)).join('.'); const parent=n.parentElement; if(parent){const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName); if(same.length>1)p+=':nth-of-type('+(same.indexOf(n)+1)+')';} parts.unshift(p);} return parts.join(' > '); }
  let root=document.querySelector(rootSel)||document.body; const rx=new RegExp(pattern,'i');
  const surfaces=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,dds-modal,.dds__modal')).filter(visible);
  if(surfaces.length) root=surfaces[surfaces.length-1];
  const controls=Array.from(root.querySelectorAll('input:not([type=hidden]),textarea,select,[role="combobox"]')).filter(visible);
  const out=[];
  for(const c of controls){
    const id=c.id||''; let text=clean(c.getAttribute('aria-label')||c.getAttribute('placeholder')||c.getAttribute('name')||id||'');
    if(id){ for(const lab of Array.from(root.querySelectorAll(`label[for="${CSS.escape(id)}"]`))) text+=' '+clean(lab.innerText||lab.textContent||''); }
    const box=c.getBoundingClientRect();
    let bestLabel='', bestScore=999999;
    for(const el of Array.from(root.querySelectorAll('label,legend,span,div,dds-label'))){
      if(!visible(el)) continue; const t=clean(el.innerText||el.textContent||''); if(!t||t.length>110) continue;
      const r=el.getBoundingClientRect(); const cy=(box.top+box.bottom)/2; const ly=(r.top+r.bottom)/2;
      const xOverlap = Math.max(0, Math.min(box.right,r.right)-Math.max(box.left,r.left));
      const labelAbove = r.bottom <= box.top + 12 && r.bottom >= box.top - 95 && xOverlap >= Math.min(box.width,r.width)*0.25;
      const labelLeft = r.right <= box.left + 18 && Math.abs(cy-ly)<35 && (box.left-r.right)<180;
      const score = labelAbove ? Math.abs(box.top-r.bottom)+Math.abs((box.left+box.right-r.left-r.right)/2)*0.05 : labelLeft ? (box.left-r.right)+Math.abs(cy-ly) : 999999;
      if(score<bestScore){ bestScore=score; bestLabel=t; }
    }
    if(bestLabel) text+=' '+bestLabel;
    if(rx.test(text)) out.push({selector:path(c), text, label:text, x:box.x, y:box.y, width:box.width, height:box.height, role:c.getAttribute('role')||'', name:c.getAttribute('name')||'', value:c.value||''});
  }
  out.sort((a,b)=>a.y-b.y || a.x-b.x);
  return out;
}
""", {"rootSel": root_sel, "pattern": label_pattern})
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


async def _find_visible_controls_in_section(page: Page, section_pattern: str, label_pattern: str, *, phase: str = "biz_flow", max_below_px: int = 1200) -> List[Dict[str, Any]]:
    """Find controls only inside a visible BizFlow subsection.

    Generic label matching can accidentally bind a nested Process Step/Action
    fill to parent fields such as Target Type or Target Transport Profile.  This
    helper first anchors on the visible section caption (for example Process
    Step, Flow Identifier, Conditions, Actions) and then returns only controls
    below that anchor.  It is purposely DOM/geometry based because the HIP DDS
    markup changes between runs.
    """
    try:
        root_info = await active_form_root_info(page, phase)
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        rows = await page.evaluate(r"""
({rootSel, sectionPattern, labelPattern, maxBelowPx}) => {
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0'); }
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){ const parts=[]; let n=el; for(let d=0;n&&n.nodeType===1&&d<8;d++,n=n.parentElement){ let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,3); if(cls.length)p+='.'+cls.map(c=>CSS.escape(c)).join('.'); const parent=n.parentElement; if(parent){const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName); if(same.length>1)p+=':nth-of-type('+(same.indexOf(n)+1)+')';} parts.unshift(p);} return parts.join(' > '); }
  let root=document.querySelector(rootSel)||document.body;
  const surfaces=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,dds-modal,.dds__modal')).filter(visible);
  if(surfaces.length) root=surfaces[surfaces.length-1];
  const srx=new RegExp(sectionPattern,'i');
  const lrx=new RegExp(labelPattern,'i');
  const textEls=Array.from(root.querySelectorAll('legend,h1,h2,h3,h4,h5,h6,label,span,div,button,dds-label'))
    .filter(visible)
    .map(el=>{const r=el.getBoundingClientRect(); const t=clean(el.innerText||el.textContent||''); return {el,t,x:r.x,y:r.y,b:r.bottom,w:r.width,h:r.height};})
    .filter(x=>x.t && x.t.length <= 160);
  let anchors=textEls.filter(x=>srx.test(x.t));
  if(!anchors.length) return [];
  // Prefer section captions lower on the active tab over top nav/tab labels.
  anchors.sort((a,b)=>b.y-a.y || a.x-b.x);
  const anchor=anchors[0];
  const top=anchor.b - 8;
  let bottom=top + Number(maxBelowPx||1200);
  const nextSectionRx=/^(Flow Details|Source Details|Target Details|Configure Source|Configure Target|Configure Routing|Conditions|Actions|Process Steps?|Flow Identifier|Document Type Supported|Fallback Interface Details|Basic Details|Interface Details)$/i;
  const next=textEls.filter(x=>x.y>top+40 && x.y<bottom && nextSectionRx.test(x.t) && !srx.test(x.t)).sort((a,b)=>a.y-b.y)[0];
  if(next) bottom=Math.min(bottom,next.y-5);
  const controls=Array.from(root.querySelectorAll('input:not([type=hidden]),textarea,select,[role="combobox"]')).filter(visible);
  const out=[];
  for(const c of controls){
    const box=c.getBoundingClientRect();
    const cy=(box.top+box.bottom)/2;
    if(cy < top || cy > bottom) continue;
    const id=c.id||''; let text=clean(c.getAttribute('aria-label')||c.getAttribute('placeholder')||c.getAttribute('name')||id||'');
    if(id){ for(const lab of Array.from(root.querySelectorAll(`label[for="${CSS.escape(id)}"]`))) text+=' '+clean(lab.innerText||lab.textContent||''); }
    let bestLabel='', bestScore=999999;
    for(const item of textEls){
      const ly=item.y + item.h/2;
      if(item.y<top-120 || item.y>bottom+40) continue;
      const xOverlap = Math.max(0, Math.min(box.right,item.x+item.w)-Math.max(box.left,item.x));
      const labelAbove = (item.y+item.h) <= box.top + 14 && (item.y+item.h) >= box.top - 105 && xOverlap >= Math.min(box.width,item.w)*0.20;
      const labelLeft = (item.x+item.w) <= box.left + 20 && Math.abs(cy-ly)<35 && (box.left-(item.x+item.w))<220;
      const score = labelAbove ? Math.abs(box.top-(item.y+item.h))+Math.abs((box.left+box.right-item.x-item.x-item.w)/2)*0.04 : labelLeft ? (box.left-(item.x+item.w))+Math.abs(cy-ly) : 999999;
      if(score<bestScore){ bestScore=score; bestLabel=item.t; }
    }
    if(bestLabel) text+=' '+bestLabel;
    if(lrx.test(text)) out.push({selector:path(c), text, label:text, x:box.x, y:box.y, width:box.width, height:box.height, role:c.getAttribute('role')||'', name:c.getAttribute('name')||'', value:c.value||'', section_anchor:anchor.t, section_top:top, section_bottom:bottom});
  }
  out.sort((a,b)=>a.y-b.y || a.x-b.x);
  return out;
}
""", {"rootSel": root_sel, "sectionPattern": section_pattern, "labelPattern": label_pattern, "maxBelowPx": max_below_px})
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


async def _find_grid_rows_in_section(page: Page, section_pattern: str, *, phase: str = "biz_flow", max_below_px: int = 1800) -> List[List[Dict[str, Any]]]:
    """Return visible controls grouped by row under a BizFlow child section.

    DDS repeatable rows often do not repeat column labels on every row, so
    label-based lookup only finds the first row and later rows look like blank
    `Select` controls.  This geometry fallback anchors on the section legend
    (Attributes, Process Steps, Conditions, Actions) and groups controls by
    vertical position so deterministic row/column filling can proceed.
    """
    try:
        root_info = await active_form_root_info(page, phase)
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        rows = await page.evaluate(r"""
({rootSel, sectionPattern, maxBelowPx}) => {
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||'1')!==0); }
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){ const parts=[]; let n=el; for(let d=0;n&&n.nodeType===1&&d<8;d++,n=n.parentElement){ let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,3); if(cls.length)p+='.'+cls.map(c=>CSS.escape(c)).join('.'); const parent=n.parentElement; if(parent){const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName); if(same.length>1)p+=':nth-of-type('+(same.indexOf(n)+1)+')';} parts.unshift(p);} return parts.join(' > '); }
  let root=document.querySelector(rootSel)||document.body;
  const surfaces=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,dds-modal,.dds__modal')).filter(visible);
  if(surfaces.length) root=surfaces[surfaces.length-1];
  const srx=new RegExp(sectionPattern,'i');
  const textEls=Array.from(root.querySelectorAll('legend,h1,h2,h3,h4,h5,h6,label,span,div,dds-label'))
    .filter(visible)
    .map(el=>{const r=el.getBoundingClientRect(); const t=clean(el.innerText||el.textContent||''); return {el,t,x:r.x,y:r.y,b:r.bottom,w:r.width,h:r.height};})
    .filter(x=>x.t && x.t.length<=180 && srx.test(x.t));
  if(!textEls.length) return [];
  // Prefer section legends/captions over column labels.
  textEls.sort((a,b)=>{
    const aleg=/[:]|steps?|conditions?|actions?|attributes?/i.test(a.t)?0:1;
    const bleg=/[:]|steps?|conditions?|actions?|attributes?/i.test(b.t)?0:1;
    return aleg-bleg || a.y-b.y;
  });
  const anchor=textEls[0];
  let container=anchor.el.closest('fieldset,section,.dds__drawer,form,.dds__container') || root;
  const top=anchor.b-8;
  let bottom=top+Number(maxBelowPx||1800);
  const nextSectionRx=/^(Flow Details|Source Details|Target Details|Configure Source|Configure Target|Configure Routing|Conditions|Actions|Process Steps?|Attributes?)\s*:?$/i;
  const allLabels=Array.from(root.querySelectorAll('legend,h1,h2,h3,h4,h5,h6,label,span,div,dds-label'))
    .filter(visible).map(el=>{const r=el.getBoundingClientRect(); return {t:clean(el.innerText||el.textContent||''), y:r.y};})
    .filter(x=>x.t && x.y>top+35 && x.y<bottom && nextSectionRx.test(x.t) && !srx.test(x.t));
  if(allLabels.length) bottom=Math.min(bottom, allLabels.sort((a,b)=>a.y-b.y)[0].y-5);
  const controls=Array.from(container.querySelectorAll('input:not([type=hidden]),textarea,select,[role="combobox"]'))
    .filter(visible).map(c=>{const r=c.getBoundingClientRect(); const id=c.id||''; let label=clean(c.getAttribute('aria-label')||c.getAttribute('placeholder')||c.getAttribute('name')||id||''); if(id){ for(const lab of Array.from(root.querySelectorAll(`label[for="${CSS.escape(id)}"]`))) label+=' '+clean(lab.innerText||lab.textContent||''); } return {selector:path(c), label:clean(label), value:clean(c.value||c.getAttribute('aria-valuetext')||c.textContent||''), x:r.x,y:r.y,cx:r.x+r.width/2, cy:r.y+r.height/2,w:r.width,h:r.height,role:c.getAttribute('role')||'', disabled:!!c.disabled||c.getAttribute('aria-disabled')==='true'};})
    .filter(c=>c.cy>=top && c.cy<=bottom);
  controls.sort((a,b)=>a.cy-b.cy || a.x-b.x);
  const groups=[];
  for(const c of controls){
    let g=groups.find(g=>Math.abs(g.cy-c.cy)<28);
    if(!g){ g={cy:c.cy, items:[]}; groups.push(g); }
    g.items.push(c); g.cy=(g.cy*(g.items.length-1)+c.cy)/g.items.length;
  }
  return groups.map(g=>g.items.sort((a,b)=>a.x-b.x)).filter(items=>items.length>=2);
}
""", {"rootSel": root_sel, "sectionPattern": section_pattern, "maxBelowPx": max_below_px})
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _drop_controls_matching(rows: List[Dict[str, Any]], *bad_patterns: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    compiled = [re.compile(p, re.I) for p in bad_patterns if p]
    for row in rows or []:
        txt = str(row.get("text") or row.get("label") or "")
        if any(rx.search(txt) for rx in compiled):
            continue
        out.append(row)
    return out


async def _clear_bizflow_sticky_locks(page: Page, *, reason: str = "") -> None:
    """Disable global sticky restore while filling BizFlow repeatable rows.

    The 025930 evidence showed a selector was first used for Condition Type and
    then a stale sticky lock restored Attribute Name (Receiver) into that same
    Condition Type combobox.  BizFlow repeatable rows are deterministic and are
    filled in row/column order, so stale global locks are more dangerous than
    helpful here.  We clear them before/after each deterministic nested-row fill
    and rely on direct re-read/retry instead of blind restore.
    """
    try:
        await page.evaluate("""
(reason) => {
  window.__HIP_FILLED_VALUE_LOCKS = {};
  window.__HIP_LAST_BIZFLOW_LOCK_CLEAR = {reason:String(reason||''), ts:Date.now()};
  for (const el of Array.from(document.querySelectorAll('[data-hip-locked-value],[data-hip-locked-key],[data-hip-lock-conflict]'))) {
    el.removeAttribute('data-hip-locked-value');
    el.removeAttribute('data-hip-locked-key');
    el.removeAttribute('data-hip-locked-label');
    el.removeAttribute('data-hip-lock-conflict');
  }
}
""", reason)
    except Exception:
        pass


async def _wait_bizflow_loading_clear(page: Page, *, timeout_ms: int = 8000) -> None:
    """Wait only for a blocking BizFlow drawer overlay, not passive spinners.

    DDS leaves progress/aria-busy nodes mounted after the routing/process-step
    controls are usable. The old broad selector waited on those harmless nodes.
    """
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
    consecutive = 0
    while asyncio.get_event_loop().time() < deadline:
        try:
            state = await page.evaluate(r"""
() => {
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||'1')!==0 && (el.getAttribute('aria-hidden')||'').toLowerCase()!=='true'); }
  function area(r){ return Math.max(0,r.width)*Math.max(0,r.height); }
  function intersection(a,b){ const x1=Math.max(a.left,b.left),y1=Math.max(a.top,b.top),x2=Math.min(a.right,b.right),y2=Math.min(a.bottom,b.bottom); return Math.max(0,x2-x1)*Math.max(0,y2-y1); }
  const vw=Math.max(1,innerWidth||1), vh=Math.max(1,innerHeight||1), viewportArea=vw*vh;
  const surfaces=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,dds-modal,.dds__modal,[role="dialog"],form')).filter(visible);
  const surface=surfaces.sort((a,b)=>area(b.getBoundingClientRect())-area(a.getBoundingClientRect()))[0]||null;
  const sr=surface?surface.getBoundingClientRect():null;
  const candidates=Array.from(document.querySelectorAll('app-loadingindicator,.dds__loading-indicator__overlay,.dds__loading-indicator,.dds__loading,.dds__spinner,[aria-busy="true"],[role="progressbar"],dds-loading-indicator')).filter(visible);
  const blocking=[]; const passive=[];
  for(const el of candidates){
    const r=el.getBoundingClientRect(), st=getComputedStyle(el), cls=(el.className||'').toString(), role=(el.getAttribute('role')||'').toLowerCase(), busy=(el.getAttribute('aria-busy')||'').toLowerCase();
    const vr=area(r)/viewportArea;
    const cr=sr?intersection(r,sr)/Math.max(1,area(sr)):0;
    const named=/overlay|backdrop|loading-indicator/i.test(cls)||(el.tagName||'').toLowerCase()==='app-loadingindicator';
    const layer=['fixed','absolute','sticky'].includes((st.position||'').toLowerCase());
    const isBlocking=st.pointerEvents!=='none' && (vr>=0.08 || cr>=0.45) && (named || layer || role==='progressbar' || busy==='true');
    (isBlocking?blocking:passive).push({tag:(el.tagName||'').toLowerCase(),classes:cls.slice(0,200),viewport_ratio:vr,surface_cover_ratio:cr,pointer_events:st.pointerEvents});
  }
  const controls=surface?Array.from(surface.querySelectorAll('button:not([disabled]),input:not([disabled]),[role="combobox"]:not([aria-disabled="true"]),dds-dropdown')).filter(visible).length:0;
  return {active:blocking.length>0, blocking, passive, active_surface_controls:controls};
}
""")
            if not bool((state or {}).get("active")):
                return
            consecutive += 1
            if consecutive < 2:
                await page.wait_for_timeout(150)
                continue
        except Exception:
            return
        await page.wait_for_timeout(250)


async def _get_bizflow_routing_drawer_grid(page: Page) -> Dict[str, Any]:
    """Read controls from the active Configure Routing -> Create Rule drawer by geometry.

    This is intentionally independent of repeated labels.  DDS routing rows
    often render only one header row; later controls appear as unlabeled
    `Select` inputs.  We anchor on the visible `Conditions :` and `Actions :`
    captions inside the top-most drawer/modal and then group controls by row.
    """
    try:
        data = await page.evaluate(r"""
() => {
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||'1')!==0); }
  function path(el){ const parts=[]; let n=el; for(let d=0;n&&n.nodeType===1&&d<10;d++,n=n.parentElement){ let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,3); if(cls.length)p+='.'+cls.map(c=>CSS.escape(c)).join('.'); const parent=n.parentElement; if(parent){const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName); if(same.length>1)p+=':nth-of-type('+(same.indexOf(n)+1)+')';} parts.unshift(p);} return parts.join(' > '); }
  let surfaces=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,dds-modal,.dds__modal,[role="dialog"]')).filter(visible);
  surfaces=surfaces.map(el=>{const r=el.getBoundingClientRect(); return {el, area:r.width*r.height, z:Number(getComputedStyle(el).zIndex||0)||0, txt:clean(el.innerText||el.textContent||'')}})
    .filter(x=>/create rule|conditions|actions|execute action/i.test(x.txt))
    .sort((a,b)=>(a.z-b.z)||(a.area-b.area));
  const root=(surfaces.length?surfaces[surfaces.length-1].el:document.body);
  const labelEls=Array.from(root.querySelectorAll('legend,h1,h2,h3,h4,h5,h6,label,span,div,dds-label'))
    .filter(visible).map(el=>{const r=el.getBoundingClientRect(); return {el,t:clean(el.innerText||el.textContent||''),x:r.x,y:r.y,b:r.bottom,w:r.width,h:r.height};})
    .filter(x=>x.t && x.t.length<180);
  function anchor(rx){
    const hits=labelEls.filter(x=>rx.test(x.t));
    hits.sort((a,b)=>a.y-b.y || a.x-b.x);
    return hits[0] || null;
  }
  const condA=anchor(/^Conditions?\s*:/i) || anchor(/^Conditions?$/i);
  const actionsA=anchor(/^Actions?\s*:/i) || anchor(/^Actions?$/i);
  const execA=anchor(/Execute Action\(s\) When/i);
  const controls=Array.from(root.querySelectorAll('input:not([type=hidden]),textarea,select,[role="combobox"]')).filter(visible).map(c=>{
    const r=c.getBoundingClientRect(); const id=c.id||''; let label=clean(c.getAttribute('aria-label')||c.getAttribute('placeholder')||c.getAttribute('name')||id||'');
    if(id){ for(const lab of Array.from(root.querySelectorAll(`label[for="${CSS.escape(id)}"]`))) label+=' '+clean(lab.innerText||lab.textContent||''); }
    // add nearest small labels above/left
    for(const lab of labelEls){ const ly=lab.y+lab.h/2; const cy=r.y+r.height/2; const overlap=Math.max(0, Math.min(r.right, lab.x+lab.w)-Math.max(r.left, lab.x)); const above=(lab.y+lab.h)<=r.y+14 && (lab.y+lab.h)>=r.y-120 && overlap>=Math.min(r.width, lab.w)*0.15; const left=(lab.x+lab.w)<=r.x+30 && Math.abs(cy-ly)<40 && (r.x-(lab.x+lab.w))<260; if(above||left) label+=' '+lab.t; }
    return {selector:path(c), label:clean(label), value:clean(c.value||c.getAttribute('aria-valuetext')||c.getAttribute('title')||c.textContent||''), x:r.x,y:r.y,cx:r.x+r.width/2,cy:r.y+r.height/2,w:r.width,h:r.height, disabled:!!c.disabled||c.getAttribute('aria-disabled')==='true'};
  }).sort((a,b)=>a.cy-b.cy||a.x-b.x);
  function groupRows(items){
    const groups=[];
    for(const c of items){
      let g=groups.find(g=>Math.abs(g.cy-c.cy)<30);
      if(!g){g={cy:c.cy,items:[]}; groups.push(g);}
      g.items.push(c); g.cy=(g.cy*(g.items.length-1)+c.cy)/g.items.length;
    }
    return groups.map(g=>g.items.sort((a,b)=>a.x-b.x)).filter(r=>r.length>=2);
  }
  const condTop=condA?condA.b-5:0;
  const actionsTop=actionsA?actionsA.b-5:99999;
  const condCtrls=controls.filter(c=>c.cy>condTop && c.cy<actionsTop-5);
  let executeWhen=null;
  if(execA){ executeWhen=condCtrls.filter(c=>c.cy>execA.y-20 && c.cy<execA.b+80).sort((a,b)=>Math.abs(a.cy-(execA.b+25))-Math.abs(b.cy-(execA.b+25)))[0]||null; }
  const conditionCandidates=condCtrls.filter(c=>!executeWhen || c.selector!==executeWhen.selector).filter(c=>c.cy>(executeWhen?executeWhen.cy+35:condTop+40));
  const conditionRows=groupRows(conditionCandidates).map(r=>r.slice(0,4));
  const actionCtrls=actionsA?controls.filter(c=>c.cy>actionsTop && c.cy<actionsTop+900):[];
  const actionRows=groupRows(actionCtrls).map(r=>r.slice(0,3));
  return {condition_rows: conditionRows, action_rows: actionRows, execute_when: executeWhen, anchors:{conditions:condA?condA.t:'', actions:actionsA?actionsA.t:'', execute:execA?execA.t:''}, control_count:controls.length};
}
""")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _capture_bizflow_form_state(page: Page, *, label: str = "") -> Dict[str, Any]:
    """Capture a lightweight visible form state for dependency learning.

    A selector inventory alone is not enough for DDS forms: values and child
    fields appear only after earlier selections.  This snapshot records visible
    controls and their current values before/after Add and fill actions so the
    replay blueprint can learn actual state transitions.
    """
    try:
        root_info = await active_form_root_info(page, "biz_flow")
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        data = await page.evaluate(r"""
({rootSel, label}) => {
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||'1')!==0); }
  function clean(s){ return String(s||'').replace(/\s+/g,' ').trim(); }
  function path(el){ const parts=[]; let n=el; for(let d=0;n&&n.nodeType===1&&d<8;d++,n=n.parentElement){ let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,3); if(cls.length)p+='.'+cls.map(c=>CSS.escape(c)).join('.'); const parent=n.parentElement; if(parent){const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName); if(same.length>1)p+=':nth-of-type('+(same.indexOf(n)+1)+')';} parts.unshift(p);} return parts.join(' > '); }
  const root=document.querySelector(rootSel)||document.body;
  const controls=Array.from(root.querySelectorAll('input:not([type=hidden]),textarea,select,[role=combobox]')).filter(visible).map(c=>{
    const r=c.getBoundingClientRect(); const id=c.id||'';
    let text=clean(c.getAttribute('aria-label')||c.getAttribute('placeholder')||c.getAttribute('name')||id||'');
    if(id){ for(const lab of Array.from(root.querySelectorAll(`label[for="${CSS.escape(id)}"]`))) text+=' '+clean(lab.innerText||lab.textContent||''); }
    for(const el of Array.from(root.querySelectorAll('label,legend,span,dds-label'))){ if(!visible(el)) continue; const er=el.getBoundingClientRect(); const t=clean(el.innerText||el.textContent||''); if(!t||t.length>120) continue; if(Math.abs((er.top+er.bottom)/2-(r.top+r.bottom)/2)<75 && er.left<=r.right+60) text+=' '+t; }
    return {selector:path(c), label:clean(text), value:clean(c.value||c.getAttribute('aria-valuetext')||c.getAttribute('title')||c.textContent||''), x:r.x, y:r.y, w:r.width, h:r.height, role:c.getAttribute('role')||'', disabled:!!c.disabled||c.getAttribute('aria-disabled')==='true'};
  });
  const buttons=Array.from(root.querySelectorAll('button,a,[role=button],dds-button')).filter(visible).map(b=>{ const click=b.matches('button,a,[role=button]')?b:(b.querySelector('button,a,[role=button]')||b); const r=click.getBoundingClientRect(); return {selector:path(click), label:clean(click.innerText||click.textContent||click.getAttribute('aria-label')||click.getAttribute('title')||''), cls:String(click.className||''), x:r.x,y:r.y,w:r.width,h:r.height}; }).filter(b=>b.label || /plus|add|dropdown__chevron/.test(b.cls));
  return {label, root_selector: rootSel, control_count: controls.length, button_count: buttons.length, controls, add_buttons: buttons.filter(b=>/^add$/i.test(b.label) || /plus|add/.test(b.cls)).slice(0,20)};
}
""", {"rootSel": root_sel, "label": label})
        return data if isinstance(data, dict) else {"label": label, "controls": [], "add_buttons": []}
    except Exception as exc:
        return {"label": label, "error": mask_sensitive_string(str(exc)), "controls": [], "add_buttons": []}


def _bizflow_state_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    def by_sel(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        return {str(c.get("selector") or ""): c for c in (snapshot.get("controls") or []) if c.get("selector")}
    b = by_sel(before)
    a = by_sel(after)
    added = [a[k] for k in a.keys() - b.keys()]
    removed = [b[k] for k in b.keys() - a.keys()]
    changed = []
    for k in a.keys() & b.keys():
        if str(a[k].get("value") or "") != str(b[k].get("value") or ""):
            changed.append({"selector": k, "label": a[k].get("label") or b[k].get("label"), "before": b[k].get("value"), "after": a[k].get("value")})
    return {
        "before_controls": len(b),
        "after_controls": len(a),
        "added_controls": added[:20],
        "removed_controls": removed[:20],
        "changed_values": changed[:30],
    }


async def _fill_bizflow_selector(page: Page, selector: str, value: str, *, combo: bool, key: str, label: str, attempts: List[Dict[str, Any]], tab: str, row_index: int) -> None:
    before_state = await _capture_bizflow_form_state(page, label=f"before fill {tab}:{label}:{row_index}")
    if str(value or "").strip().lower().startswith("disabled"):
        attempts.append({"label": label, "key": key, "selector": selector or "", "value_used": value or "", "success": True, "filled": False, "skipped": True, "reason": "field disabled/not applicable by input", "bizflow_tab": tab, "row_index": row_index, "input_driven_row_fill": True, "state_delta": _bizflow_state_delta(before_state, before_state)})
        return
    if not selector or value in (None, ""):
        attempts.append({"label": label, "key": key, "selector": selector or "", "value_used": value or "", "success": False, "filled": False, "reason": "row-specific control not visible", "bizflow_tab": tab, "row_index": row_index, "input_driven_row_fill": True, "state_delta": _bizflow_state_delta(before_state, before_state)})
        return
    try:
        if combo:
            ok = await select_dds_combobox(page, await get_active_form_root(page, "biz_flow"), selector, str(value), phase="biz_flow")
            events = ["click", "option/select", "change", "blur"]
        else:
            ok = await dds_set_text_control(page, await get_active_form_root(page, "biz_flow"), selector, str(value))
            events = ["input", "change", "blur"]
    except Exception as exc:
        after_state = await _capture_bizflow_form_state(page, label=f"after failed fill {tab}:{label}:{row_index}")
        attempts.append({"label": label, "key": key, "selector": selector, "value_used": value, "success": False, "filled": False, "reason": mask_sensitive_string(str(exc)), "bizflow_tab": tab, "row_index": row_index, "input_driven_row_fill": True, "state_delta": _bizflow_state_delta(before_state, after_state)})
        return
    after_state = await _capture_bizflow_form_state(page, label=f"after fill {tab}:{label}:{row_index}")
    attempts.append({"label": label, "key": key, "selector": selector, "value_used": value, "success": ok, "filled": ok, "dom_events": events, "safety": "no Save/Create/Submit clicked", "scoped_to_active_root": True, "bizflow_tab": tab, "row_index": row_index, "input_driven_row_fill": True, "state_delta": _bizflow_state_delta(before_state, after_state)})
    # Do not run global sticky restore inside BizFlow repeatable rows.  The
    # 025930 evidence showed stale locks can write Receiver into Condition Type
    # after DDS reuses selectors.  Clear locks and let deterministic row/column
    # passes retry from live controls instead.
    await _clear_bizflow_sticky_locks(page, reason=f"after row fill {tab}:{label}")
    await page.wait_for_timeout(100)


async def _fill_bizflow_source_attribute_rows(page: Page, input_data: Dict[str, Any] | None, attempts: List[Dict[str, Any]], tab: str) -> None:
    bf = _bizflow_obj(input_data)
    rows = _list_of_dicts((bf.get("flow_identifiers") or {}).get("conditions") if isinstance(bf.get("flow_identifiers"), dict) else [])
    if not rows:
        return
    grid_rows = await _find_grid_rows_in_section(page, r"attributes?\s*:|flow\s*identifier", phase="biz_flow")
    if len(grid_rows) >= len(rows) and all(len(g) >= 4 for g in grid_rows[:len(rows)]):
        for i, row in enumerate(rows):
            g = grid_rows[i]
            await _fill_bizflow_selector(page, g[0].get("selector", ""), row.get("document_type_name_version") or "", combo=True, key="source_flow_identifier_document_type", label="Document Type Name (Version)", attempts=attempts, tab=tab, row_index=i)
            await _fill_bizflow_selector(page, g[1].get("selector", ""), row.get("attribute_name") or "", combo=True, key="source_flow_identifier_attribute", label="Attribute Name", attempts=attempts, tab=tab, row_index=i)
            await _fill_bizflow_selector(page, g[2].get("selector", ""), row.get("operator") or "", combo=True, key="source_flow_identifier_operator", label="Operator", attempts=attempts, tab=tab, row_index=i)
            await _fill_bizflow_selector(page, g[3].get("selector", ""), row.get("value") or "", combo=False, key="source_flow_identifier_value", label="Value", attempts=attempts, tab=tab, row_index=i)
        return
    doc_controls = await _find_visible_controls_for_label(page, r"document\s*type\s*name\s*\(?version\)?|document\s*type\s*name", phase="biz_flow")
    version_docs = [c for c in doc_controls if "version" in str(c.get("text") or "").lower()]
    if version_docs:
        doc_controls = version_docs
    attr_controls = await _find_visible_controls_for_label(page, r"attribute\s*name", phase="biz_flow")
    op_controls = [c for c in await _find_visible_controls_for_label(page, r"(^|\s)operator(\s|$)", phase="biz_flow") if "flow identifier operator" not in str(c.get("text") or "").lower()]
    value_controls = await _find_visible_controls_for_label(page, r"(^|\s)value(\s|$)", phase="biz_flow")
    for i, row in enumerate(rows):
        await _fill_bizflow_selector(page, (doc_controls[i] if i < len(doc_controls) else {}).get("selector", ""), row.get("document_type_name_version") or "", combo=True, key="source_flow_identifier_document_type", label="Document Type Name (Version)", attempts=attempts, tab=tab, row_index=i)
        await _fill_bizflow_selector(page, (attr_controls[i] if i < len(attr_controls) else {}).get("selector", ""), row.get("attribute_name") or "", combo=True, key="source_flow_identifier_attribute", label="Attribute Name", attempts=attempts, tab=tab, row_index=i)
        await _fill_bizflow_selector(page, (op_controls[i] if i < len(op_controls) else {}).get("selector", ""), row.get("operator") or "", combo=True, key="source_flow_identifier_operator", label="Operator", attempts=attempts, tab=tab, row_index=i)
        await _fill_bizflow_selector(page, (value_controls[i] if i < len(value_controls) else {}).get("selector", ""), row.get("value") or "", combo=False, key="source_flow_identifier_value", label="Value", attempts=attempts, tab=tab, row_index=i)



async def _count_bizflow_process_step_rows(page: Page) -> int:
    """Count process-step accordion rows anywhere in the active BizFlow form.

    The 012337 evidence showed the Process Step +Add did create an accordion
    row, but it stayed collapsed, so the visible-control snapshot still looked
    unchanged.  Counting the component rows directly is the correct MCP/CDP
    style verification for this DDS widget.
    """
    try:
        root_info = await active_form_root_info(page, "biz_flow")
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        n = await page.evaluate(r"""
(rootSel) => {
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||'1')!==0); }
  let root=document.querySelector(rootSel)||document.body;
  const surfaces=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,dds-modal,.dds__modal')).filter(visible);
  if(surfaces.length) root=surfaces[surfaces.length-1];
  // Count rows in active target panels first.  Hidden tab panels may keep old rows in DOM.
  const activePanels=Array.from(root.querySelectorAll('dds-tab-panel,.dds__tabs__pane,[role="tabpanel"]')).filter(p=>p.getAttribute('aria-hidden')!=='true' && visible(p));
  const scopes=activePanels.length ? activePanels : [root];
  let count=0;
  for(const scope of scopes){ count += scope.querySelectorAll('app-process-step dds-accordion-item,.cdk-drop-list dds-accordion-item').length; }
  if(count===0){ count=root.querySelectorAll('app-process-step dds-accordion-item,.cdk-drop-list dds-accordion-item').length; }
  return count;
}
""", root_sel)
        return int(n or 0)
    except Exception:
        return 0


async def _expand_bizflow_process_step_row(page: Page, row_index: int) -> Dict[str, Any]:
    """Expand the Nth Process Step accordion row before filling fields."""
    try:
        root_info = await active_form_root_info(page, "biz_flow")
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        result = await page.evaluate(r"""
({rootSel, rowIndex}) => {
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||'1')!==0); }
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){ const parts=[]; let n=el; for(let d=0;n&&n.nodeType===1&&d<8;d++,n=n.parentElement){ let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,3); if(cls.length)p+='.'+cls.map(c=>CSS.escape(c)).join('.'); const parent=n.parentElement; if(parent){const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName); if(same.length>1)p+=':nth-of-type('+(same.indexOf(n)+1)+')';} parts.unshift(p);} return parts.join(' > '); }
  let root=document.querySelector(rootSel)||document.body;
  const surfaces=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,dds-modal,.dds__modal')).filter(visible);
  if(surfaces.length) root=surfaces[surfaces.length-1];
  const activePanels=Array.from(root.querySelectorAll('dds-tab-panel,.dds__tabs__pane,[role="tabpanel"]')).filter(p=>p.getAttribute('aria-hidden')!=='true' && visible(p));
  const scopes=activePanels.length ? activePanels : [root];
  let items=[];
  for(const scope of scopes){ items=items.concat(Array.from(scope.querySelectorAll('app-process-step dds-accordion-item,.cdk-drop-list dds-accordion-item'))); }
  if(!items.length) items=Array.from(root.querySelectorAll('app-process-step dds-accordion-item,.cdk-drop-list dds-accordion-item'));
  const item=items[Number(rowIndex)||0];
  if(!item) return {found:false, row_count:items.length};
  try{ item.scrollIntoView({block:'center',inline:'nearest'}); }catch(e){}
  const btn=item.querySelector('.dds__accordion__button,button[aria-controls],button');
  const expanded=btn && btn.getAttribute('aria-expanded')==='true';
  return {found:true, clicked:false, expanded_before:expanded, row_count:items.length, header: clean(btn ? btn.innerText||btn.textContent||'' : ''), selector: btn ? path(btn) : ''};
}
""", {"rootSel": root_sel, "rowIndex": row_index})
        if isinstance(result, dict) and result.get("found") and not result.get("expanded_before") and result.get("selector"):
            clicked = await _governed_bizflow_click(
                page, selector=str(result.get("selector") or ""),
                action_label=f"Expand BizFlow Process Step row {row_index + 1}: {result.get('header') or 'Process Step'}",
                timeout=1400, mutation_risk=False, structural_opener=True,
            )
            result["clicked"] = bool(clicked)
            if not clicked:
                result["error"] = "Process Step accordion expansion failed semantic/governed dispatch"
        await page.wait_for_timeout(650)
        return result if isinstance(result, dict) else {"found": False}
    except Exception as exc:
        return {"found": False, "error": mask_sensitive_string(str(exc))}


async def _get_bizflow_process_step_row_controls(page: Page, row_index: int) -> Dict[str, str]:
    """Return semantic selectors for the expanded Process Step accordion row."""
    try:
        root_info = await active_form_root_info(page, "biz_flow")
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        data = await page.evaluate(r"""
({rootSel, rowIndex}) => {
  function visible(el){ if(!el || !el.getBoundingClientRect) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||'1')!==0); }
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){ const parts=[]; let n=el; for(let d=0;n&&n.nodeType===1&&d<9;d++,n=n.parentElement){ let p=n.tagName.toLowerCase(); if(n.id){p+='#'+CSS.escape(n.id); parts.unshift(p); break;} const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,3); if(cls.length)p+='.'+cls.map(c=>CSS.escape(c)).join('.'); const parent=n.parentElement; if(parent){const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName); if(same.length>1)p+=':nth-of-type('+(same.indexOf(n)+1)+')';} parts.unshift(p);} return parts.join(' > '); }
  let root=document.querySelector(rootSel)||document.body;
  const surfaces=Array.from(document.querySelectorAll('dds-drawer,.dds__drawer,dds-modal,.dds__modal')).filter(visible);
  if(surfaces.length) root=surfaces[surfaces.length-1];
  const activePanels=Array.from(root.querySelectorAll('dds-tab-panel,.dds__tabs__pane,[role="tabpanel"]')).filter(p=>p.getAttribute('aria-hidden')!=='true' && visible(p));
  const scopes=activePanels.length ? activePanels : [root];
  let items=[];
  for(const scope of scopes){ items=items.concat(Array.from(scope.querySelectorAll('app-process-step dds-accordion-item,.cdk-drop-list dds-accordion-item'))); }
  if(!items.length) items=Array.from(root.querySelectorAll('app-process-step dds-accordion-item,.cdk-drop-list dds-accordion-item'));
  const item=items[Number(rowIndex)||0];
  if(!item) return {row_count:items.length, controls:[], selectors:{}};
  // DDS puts some Mapping Configuration children outside the literal
  // dds-accordion-item but still inside the owning app-process-step component.
  // Scope to the component first, then fall back to the item.
  const scope=item.closest('app-process-step') || item;
  const controls=Array.from(scope.querySelectorAll('input:not([type=hidden]),textarea,select,[role="combobox"]')).filter(visible).map(c=>{
    const r=c.getBoundingClientRect(); const id=c.id||''; let label=clean(c.getAttribute('aria-label')||c.getAttribute('placeholder')||c.getAttribute('name')||id||'');
    if(id){ for(const lab of Array.from(root.querySelectorAll(`label[for="${CSS.escape(id)}"]`))) label+=' '+clean(lab.innerText||lab.textContent||''); }
    const wrap=c.closest('.dds__dropdown, .dds__input-text, .dds__form-control, .dds__col--md-6, .dds__col--md-4, .dds__col--md-3, div');
    if(wrap){ for(const lab of Array.from(wrap.querySelectorAll('label,dds-label,span')).slice(0,4)){ const t=clean(lab.innerText||lab.textContent||''); if(t && t.length<80) label+=' '+t; } }
    return {selector:path(c), label:clean(label), value:clean(c.value||''), role:c.getAttribute('role')||'', x:r.x,y:r.y,w:r.width,h:r.height, disabled:!!c.disabled||c.getAttribute('aria-disabled')==='true'};
  }).sort((a,b)=>a.y-b.y || a.x-b.x);
  const selectors={};
  for(const c of controls){ const l=c.label.toLowerCase();
    if(!selectors.step_type && /step\s*type/.test(l)) selectors.step_type=c.selector;
    else if(!selectors.step_name && /step\s*name/.test(l)) selectors.step_name=c.selector;
    else if(!selectors.action && /(^|\s)action(\s|$)/.test(l)) selectors.action=c.selector;
    else if(!selectors.target_document_type && /target\s*document|document\s*type\s*\(?version\)?/.test(l)) selectors.target_document_type=c.selector;
    else if(!selectors.rule && /rule/.test(l)) selectors.rule=c.selector;
    else if(!selectors.mapping_identifier && /mapping\s*identifier|mapping/.test(l)) selectors.mapping_identifier=c.selector;
    else if(!selectors.source_document_type && /source\s*document/.test(l)) selectors.source_document_type=c.selector;
  }
  // Positional fallback inside process row: Step Type then Step Name are the first row controls.
  if(!selectors.step_type && controls[0]) selectors.step_type=controls[0].selector;
  if(!selectors.step_name && controls[1]) selectors.step_name=controls[1].selector;
  // Mapping Transformer child fields often render as unlabeled Select controls
  // under the Mapping Configuration panel.  Use their geometry after the first
  // two header controls: Source Document Type(disabled), Action, Target Doc, Rule.
  const tail=controls.filter(c=>![selectors.step_type, selectors.step_name].includes(c.selector));
  if(!selectors.action){ const cand=tail.find(c=>/select/i.test(c.value||'') && c.x>250 && c.x<850); if(cand) selectors.action=cand.selector; }
  if(!selectors.target_document_type){ const cand=tail.find(c=>/selected|select/i.test(c.value||'') && c.x>=850); if(cand) selectors.target_document_type=cand.selector; }
  if(!selectors.rule){ const cand=tail.find(c=>/rule|select/i.test((c.label||'')+' '+(c.value||'')) && c.y>(controls[1]?.y||0)+60); if(cand) selectors.rule=cand.selector; }
  return {row_count:items.length, controls, selectors};
}
""", {"rootSel": root_sel, "rowIndex": row_index})
        if isinstance(data, dict):
            sels = data.get("selectors") if isinstance(data.get("selectors"), dict) else {}
            return {str(k): str(v) for k, v in sels.items() if v}
    except Exception:
        pass
    return {}

async def _fill_bizflow_process_step_rows(page: Page, input_data: Dict[str, Any] | None, attempts: List[Dict[str, Any]], tab: str) -> None:
    """Fill Configure Target -> Process Step rows from input.json.

    This is intentionally component-aware.  DDS renders Process Steps as
    collapsed accordion items.  A normal visible-control scan sees only the
    parent Target fields, so it incorrectly reports "row-specific control not
    visible" even after +Add actually created the row.  We therefore verify row
    creation by counting ``app-process-step dds-accordion-item`` nodes, expand
    the row, then fill controls inside that exact accordion item.
    """
    bf = _bizflow_obj(input_data)
    rows = _list_of_dicts(bf.get("process_steps"))
    if not rows:
        return

    await _scroll_bizflow_to_section(page, ["process step", "process steps"], "Configure Target Process Step Rows")
    # Self-heal: if the earlier nested-row Add plan did not create enough rows,
    # create the missing rows here and verify by component count, not click event.
    for _ in range(max(0, len(rows) + 1)):
        current = await _count_bizflow_process_step_rows(page)
        if current >= len(rows):
            break
        click = await _click_bizflow_section_add(page, ["process step", "process steps", "create process step"], tab_label=tab, section="Configure Target Process Step Rows")
        click["recovery_add_for_process_steps"] = True
        attempts.append({"label": "Process Steps +Add", "key": "process_step_add", "selector": click.get("selector", ""), "value_used": f"ensure {len(rows)} rows", "success": bool(click.get("clicked")), "filled": False, "bizflow_tab": tab, "row_index": current, "row_add_recovery": True, "audit": click})
        if not click.get("clicked"):
            break

    for i, row in enumerate(rows):
        cfg = row.get("configuration") if isinstance(row.get("configuration"), dict) else {}
        expanded = await _expand_bizflow_process_step_row(page, i)
        attempts.append({"label": "Expand Process Step", "key": "process_step_expand", "selector": expanded.get("selector", ""), "value_used": str(i + 1), "success": bool(expanded.get("found")), "filled": False, "bizflow_tab": tab, "row_index": i, "accordion_expand": expanded})
        controls = await _get_bizflow_process_step_row_controls(page, i)
        await _fill_bizflow_selector(page, controls.get("step_type", ""), row.get("step_type") or "", combo=True, key="process_step_type", label="Process Step Type", attempts=attempts, tab=tab, row_index=i)
        await page.wait_for_timeout(700)
        await _clear_bizflow_sticky_locks(page, reason=f"after process step type row {i+1}")
        # Step Type selection rerenders the accordion body and reveals child config.
        await _expand_bizflow_process_step_row(page, i)
        controls = await _get_bizflow_process_step_row_controls(page, i)
        await _fill_bizflow_selector(page, controls.get("step_name", ""), row.get("step_name") or "", combo=False, key="process_step_name", label="Process Step Name", attempts=attempts, tab=tab, row_index=i)
        await page.wait_for_timeout(350)
        controls = await _get_bizflow_process_step_row_controls(page, i)
        # The child Mapping Configuration panel can render outside the literal
        # accordion item and without stable repeated labels.  If component
        # scoping did not find it, fall back to the visible Mapping/Process Step
        # section by geometry.
        if not (controls.get("action") and controls.get("target_document_type")):
            await _scroll_bizflow_to_section(page, ["mapping configuration", "mapping transformer", "process step"], "Process Step Mapping Configuration")
            grid = await _find_grid_rows_in_section(page, r"mapping\s*(transformer\s*)?configuration|mapping\s*transformer|process\s*steps?", phase="biz_flow", max_below_px=1200)
            flat = [c for row2 in grid for c in row2]
            # Prefer unlabeled Select controls below the Step Type/Step Name header.
            if flat:
                xs = sorted(flat, key=lambda c: (float(c.get("y") or 0), float(c.get("x") or 0)))
                if not controls.get("action") and len(xs) >= 3:
                    controls["action"] = xs[2].get("selector", "")
                if not controls.get("target_document_type") and len(xs) >= 4:
                    controls["target_document_type"] = xs[3].get("selector", "")
                if not controls.get("rule") and len(xs) >= 5:
                    controls["rule"] = xs[4].get("selector", "")
                if not controls.get("mapping_identifier") and len(xs) >= 6:
                    controls["mapping_identifier"] = xs[5].get("selector", "")
            if not controls.get("action"):
                controls["action"] = ((_drop_controls_matching(await _find_visible_controls_in_section(page, r"mapping|process\s*step", r"(^|\s)action(\s|$)", phase="biz_flow", max_below_px=1600), r"rule", r"routing") or [{}])[0]).get("selector", "")
            if not controls.get("target_document_type"):
                controls["target_document_type"] = ((_drop_controls_matching(await _find_visible_controls_in_section(page, r"mapping|process\s*step", r"target\s*document|document\s*type", phase="biz_flow", max_below_px=1600), r"source\s*document") or [{}])[0]).get("selector", "")
            if not controls.get("rule"):
                controls["rule"] = ((_drop_controls_matching(await _find_visible_controls_in_section(page, r"mapping|process\s*step", r"(^|\s)rule(\s|$)", phase="biz_flow", max_below_px=1600), r"rule\s*type") or [{}])[0]).get("selector", "")
            if not controls.get("mapping_identifier"):
                controls["mapping_identifier"] = ((await _find_visible_controls_in_section(page, r"mapping|process\s*step", r"mapping\s*identifier|mapping", phase="biz_flow", max_below_px=1600)) or [{}])[0].get("selector", "")

        action_val = cfg.get("action") or ""
        if action_val:
            await _fill_bizflow_selector(page, controls.get("action", ""), action_val, combo=True, key="process_action", label="Action", attempts=attempts, tab=tab, row_index=i)
            await page.wait_for_timeout(700)
            await _wait_bizflow_loading_clear(page)
            controls = await _get_bizflow_process_step_row_controls(page, i)
        target_doc_val = cfg.get("target_document_type_version") or cfg.get("document_type_version") or ""
        if target_doc_val:
            await _fill_bizflow_selector(page, controls.get("target_document_type", ""), target_doc_val, combo=True, key="process_target_document_type", label="Target Document Type", attempts=attempts, tab=tab, row_index=i)
        if cfg.get("rule_version"):
            await _fill_bizflow_selector(page, controls.get("rule", ""), cfg.get("rule_version") or "", combo=True, key="process_rule", label="Rule", attempts=attempts, tab=tab, row_index=i)
        mapping_val = cfg.get("mapping_identifier") or (_bizflow_obj(input_data).get("mapping_identifier") if isinstance(_bizflow_obj(input_data), dict) else "")
        if mapping_val:
            await _fill_bizflow_selector(page, controls.get("mapping_identifier", ""), mapping_val, combo=True, key="process_mapping_identifier", label="Mapping Identifier", attempts=attempts, tab=tab, row_index=i)
        await _clear_bizflow_sticky_locks(page, reason=f"after full process step row {i+1}")


async def _fill_bizflow_routing_condition_rows(page: Page, input_data: Dict[str, Any] | None, attempts: List[Dict[str, Any]], tab: str) -> None:
    """Fill Configure Routing -> Conditions rows using active drawer geometry."""
    bf = _bizflow_obj(input_data)
    routing = bf.get("configure_routing") if isinstance(bf.get("configure_routing"), dict) else {}
    cond = routing.get("conditions") if isinstance(routing, dict) else {}
    rows = _list_of_dicts((cond or {}).get("rows") if isinstance(cond, dict) else [])
    if not rows:
        return
    await _clear_bizflow_sticky_locks(page, reason="before routing condition rows")
    await _wait_bizflow_loading_clear(page)
    await _scroll_bizflow_to_section(page, ["conditions", "condition", "execute action"], "Configure Routing Condition Rows")
    for i, row in enumerate(rows):
        grid = await _get_bizflow_routing_drawer_grid(page)
        condition_rows = grid.get("condition_rows") if isinstance(grid.get("condition_rows"), list) else []
        row_controls = condition_rows[i] if i < len(condition_rows) else []
        type_sel = row_controls[0].get("selector", "") if len(row_controls) > 0 else ""
        op_sel = row_controls[1].get("selector", "") if len(row_controls) > 1 else ""
        val_sel = row_controls[2].get("selector", "") if len(row_controls) > 2 else ""
        attr_sel = row_controls[3].get("selector", "") if len(row_controls) > 3 else ""
        if not type_sel:
            type_sel = ((_drop_controls_matching(await _find_visible_controls_in_section(page, r"conditions?|execute\s*action", r"condition\s*type|(^|\s)type(\s|$)", phase="biz_flow", max_below_px=1500), r"action\s*type", r"rule\s*type") or [{}])[i]).get("selector", "") if i < len(_drop_controls_matching(await _find_visible_controls_in_section(page, r"conditions?|execute\s*action", r"condition\s*type|(^|\s)type(\s|$)", phase="biz_flow", max_below_px=1500), r"action\s*type", r"rule\s*type") or []) else ""
        await _fill_bizflow_selector(page, type_sel, row.get("condition_type") or "Attributes", combo=True, key="route_condition_type", label="Condition Type", attempts=attempts, tab=tab, row_index=i)
        await page.wait_for_timeout(700)
        await _wait_bizflow_loading_clear(page)
        await _clear_bizflow_sticky_locks(page, reason=f"after routing condition type {i+1}")
        grid = await _get_bizflow_routing_drawer_grid(page)
        condition_rows = grid.get("condition_rows") if isinstance(grid.get("condition_rows"), list) else []
        row_controls = condition_rows[i] if i < len(condition_rows) else row_controls
        if len(row_controls) > 1:
            op_sel = row_controls[1].get("selector", "")
        if len(row_controls) > 2:
            val_sel = row_controls[2].get("selector", "")
        if len(row_controls) > 3:
            attr_sel = row_controls[3].get("selector", "")
        if not op_sel:
            ops = _drop_controls_matching(await _find_visible_controls_in_section(page, r"conditions?|execute\s*action", r"(^|\s)operator(\s|$)", phase="biz_flow", max_below_px=1500), r"flow\s*identifier")
            op_sel = (ops[i] if i < len(ops) else {}).get("selector", "")
        if not val_sel:
            vals = await _find_visible_controls_in_section(page, r"conditions?|execute\s*action", r"(^|\s)value(\s|$)", phase="biz_flow", max_below_px=1500)
            val_sel = (vals[i] if i < len(vals) else {}).get("selector", "")
        if not attr_sel:
            attrs = _drop_controls_matching(await _find_visible_controls_in_section(page, r"conditions?|execute\s*action", r"attribute\s*name|attribute\s*name\s*/\s*unit|attribute", phase="biz_flow", max_below_px=1500), r"document", r"action")
            attr_sel = (attrs[i] if i < len(attrs) else {}).get("selector", "")
        await _fill_bizflow_selector(page, op_sel, row.get("operator") or "", combo=True, key="route_condition_operator", label="Operator", attempts=attempts, tab=tab, row_index=i)
        await _fill_bizflow_selector(page, val_sel, row.get("value") or "", combo=False, key="route_condition_value", label="Value", attempts=attempts, tab=tab, row_index=i)
        await _fill_bizflow_selector(page, attr_sel, row.get("attribute_name") or "", combo=True, key="route_condition_attribute", label="Attribute Name/Unit", attempts=attempts, tab=tab, row_index=i)
        await _clear_bizflow_sticky_locks(page, reason=f"after routing condition row {i+1}")


async def _fill_bizflow_routing_action_rows(page: Page, input_data: Dict[str, Any] | None, attempts: List[Dict[str, Any]], tab: str) -> None:
    bf = _bizflow_obj(input_data)
    routing = bf.get("configure_routing") if isinstance(bf.get("configure_routing"), dict) else {}
    actions = routing.get("actions") if isinstance(routing, dict) else {}
    rows = _list_of_dicts(actions) or ([actions] if isinstance(actions, dict) and actions else [])
    if not rows:
        return
    await _clear_bizflow_sticky_locks(page, reason="before routing action rows")
    await _wait_bizflow_loading_clear(page)
    await _scroll_bizflow_to_section(page, ["actions", "action", "target"], "Configure Routing Action Rows")
    for i, row in enumerate(rows):
        grid = await _get_bizflow_routing_drawer_grid(page)
        action_rows = grid.get("action_rows") if isinstance(grid.get("action_rows"), list) else []
        row_controls = action_rows[i] if i < len(action_rows) else []
        name_sel = row_controls[0].get("selector", "") if len(row_controls) > 0 else ""
        type_sel = row_controls[1].get("selector", "") if len(row_controls) > 1 else ""
        target_sel = row_controls[2].get("selector", "") if len(row_controls) > 2 else ""
        if not name_sel:
            names = _drop_controls_matching(await _find_visible_controls_in_section(page, r"actions?", r"action\s*name|(^|\s)name(\s|$)", phase="biz_flow"), r"rule\s*name", r"business\s*flow")
            name_sel = (names[i] if i < len(names) else {}).get("selector", "")
        if not type_sel:
            types = _drop_controls_matching(await _find_visible_controls_in_section(page, r"actions?", r"action\s*type|(^|\s)type(\s|$)", phase="biz_flow"), r"condition\s*type", r"target\s*type", r"source\s*type")
            type_sel = (types[i] if i < len(types) else {}).get("selector", "")
        await _fill_bizflow_selector(page, name_sel, row.get("name") or "", combo=False, key="route_action_name", label="Action Name", attempts=attempts, tab=tab, row_index=i)
        await _fill_bizflow_selector(page, type_sel, row.get("type") or "Route Document", combo=True, key="route_action_type", label="Action Type", attempts=attempts, tab=tab, row_index=i)
        # Route Document can show a blocking Loading overlay before Target appears.
        await page.wait_for_timeout(1000)
        await _wait_bizflow_loading_clear(page, timeout_ms=12000)
        await _clear_bizflow_sticky_locks(page, reason=f"after routing action type {i+1}")
        for _ in range(4):
            grid = await _get_bizflow_routing_drawer_grid(page)
            action_rows = grid.get("action_rows") if isinstance(grid.get("action_rows"), list) else []
            row_controls = action_rows[i] if i < len(action_rows) else row_controls
            if len(row_controls) > 2:
                target_sel = row_controls[2].get("selector", "")
            if target_sel:
                break
            targets = _drop_controls_matching(await _find_visible_controls_in_section(page, r"actions?", r"(^|\s)target(\s|$)|target\s*transport|transport\s*profile|route\s*to", phase="biz_flow", max_below_px=1600), r"target\s*type", r"target\s*document", r"target\s*application")
            target_sel = (targets[i] if i < len(targets) else {}).get("selector", "")
            if target_sel:
                break
            await page.wait_for_timeout(750)
            await _wait_bizflow_loading_clear(page, timeout_ms=3000)
        await _fill_bizflow_selector(page, target_sel, row.get("target") or "", combo=True, key="route_action_target", label="Target", attempts=attempts, tab=tab, row_index=i)
        await _clear_bizflow_sticky_locks(page, reason=f"after routing action row {i+1}")


async def capture_and_fill_bizflow_multitab_form(page: Page, input_data: Dict[str, Any] | None = None, *, fill_dummy: bool = True, judge_output_dir: Path | None = None, config: AppConfig | None = None) -> Dict[str, Any]:
    dummy_values = build_dummy_fill_values(input_data or {})
    audit: Dict[str, Any] = {"template_link": {}, "tabs": [], "routing_nested_add": {}, "warnings": [], "section_judges": [], "blocked_at_section": "", "form_state_learning": {"snapshots": [], "purpose": "learn visible controls, dependency-created children and value regressions after each tab/add/fill"}}
    judge_policy = SectionJudgePolicy.from_payload((input_data or {}).get("_section_judge_config") if isinstance(input_data, dict) else None)
    section_judge = DualModelSectionJudge(judge_policy) if judge_policy.enabled else None
    judge_dir = Path(judge_output_dir or ".") / "section_judges"
    judge_dir.mkdir(parents=True, exist_ok=True)
    all_controls: List[Dict[str, Any]] = []
    all_dropdowns: List[Dict[str, Any]] = []
    all_required: List[Dict[str, Any]] = []
    all_buttons: List[Dict[str, Any]] = []
    all_attempts: List[Dict[str, Any]] = []
    repeatable_row_audits: List[Dict[str, Any]] = []
    exploration_graphs: List[Dict[str, Any]] = []
    exploration_root = (Path(judge_output_dir).parent.parent / "portal_form_knowledge") if judge_output_dir else Path("portal_form_knowledge")
    state_graph = compile_phase_state_graph(input_data or {}, "biz_flow")
    state_graph_executions: List[Dict[str, Any]] = []

    async def _execute_state_graph_section(tab_name: str, section_attempts: List[Dict[str, Any]], stage: str) -> Dict[str, Any]:
        graph_section = bizflow_graph_section_for_tab(tab_name)
        if autonomous_phase_enabled(config, "biz_flow"):
            autonomous_cfg = getattr(config, "autonomous_form", None) if config is not None else None
            safe_stage = re.sub(r"[^a-z0-9]+", "_", f"{tab_name}_{stage}".lower()).strip("_")
            autonomous = await execute_autonomous_phase_goal(
                page=page, graph=state_graph, phase="biz_flow", input_data=input_data or {},
                config=config, output_dir=exploration_root / "biz_flow_autonomous" / safe_stage,
                prior_attempts=section_attempts,
                max_cycles=int(getattr(autonomous_cfg, "max_adaptive_cycles", 5) or 5),
                repair=True, strict_live_execution=True, section=graph_section,
            )
            result = autonomous_target_execution(autonomous)
            result["autonomous_runtime"] = {
                "pass": bool(autonomous.get("pass")),
                "cycles": len(autonomous.get("cycles") or []),
                "autowebglm_used_for_live_observation": bool(autonomous.get("autowebglm_used_for_live_observation")),
            }
        else:
            result = await execute_phase_state_graph(
                page, state_graph, phase="biz_flow", section=graph_section,
                max_retries=2, repair=True, prior_attempts=section_attempts, strict_live_execution=True,
            )
        result["tab"] = tab_name
        result["graph_section"] = graph_section
        result["execution_stage"] = stage
        state_graph_executions.append(result)
        for item in result.get("attempts", []):
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            copied.setdefault("bizflow_tab", tab_name)
            copied["state_graph_execution"] = True
            copied["execution_stage"] = stage
            section_attempts.append(copied)
        if judge_output_dir:
            safe = re.sub(r"[^a-z0-9]+", "_", f"{tab_name}_{stage}".lower()).strip("_")
            safe_write_json(Path(judge_output_dir) / f"bizflow_state_graph_{safe}.json", result)
        return result

    async def judge_with_repairs(section_name: str, section_attempts: List[Dict[str, Any]], repair_callback) -> Dict[str, Any]:
        if section_judge is None:
            return {"pass": True, "status": "disabled", "section": section_name}
        expected = build_bizflow_section_expectation(input_data or {}, section_name)
        last: Dict[str, Any] = {}
        safe_name = re.sub(r"[^a-z0-9]+", "_", section_name.lower()).strip("_") or "section"
        for judge_attempt in range(1, judge_policy.max_repairs + 2):
            shot = judge_dir / f"{safe_name}_judge_attempt_{judge_attempt}.png"
            last = await section_judge.judge_live_section(
                page=page,
                phase="biz_flow",
                section=section_name,
                expected=expected,
                attempts=section_attempts,
                screenshot_path=shot,
                golden_screenshots=[],
                root_selector="body",
                attempt_number=judge_attempt,
            )
            safe_write_json(judge_dir / f"{safe_name}_judge_attempt_{judge_attempt}.json", last)
            audit["section_judges"].append(last)
            if last.get("pass"):
                return last
            if judge_attempt <= judge_policy.max_repairs:
                try:
                    await repair_callback(judge_attempt, last)
                    await page.wait_for_timeout(700)
                except Exception as exc:
                    audit["warnings"].append(f"Section repair failed for {section_name}: {mask_sensitive_string(str(exc))}")
        audit["blocked_at_section"] = section_name
        audit["warnings"].append(f"SECTION JUDGE BLOCKED progression at {section_name}; deterministic/text/vision approval was not obtained.")
        return last

    await _dismiss_bizflow_overlays(page)
    audit["surface_before_template"] = {
        "template_picker": await _is_bizflow_template_picker_surface(page),
        "form": await _is_bizflow_form_surface(page),
    }
    audit["template_link"] = await _click_bizflow_template_link_after_add(page)
    await _dismiss_bizflow_overlays(page)
    form_visible = await _wait_for_bizflow_form_surface(page, timeout_ms=9000)
    audit["surface_after_template"] = {
        "template_picker": await _is_bizflow_template_picker_surface(page),
        "form": form_visible,
    }
    if not form_visible:
        audit["warnings"].append("Create Biz Flow form tabs were not visible after + Add/template click; skipped cookie/listing/side-nav controls instead of recording false form evidence.")
        return {
            "controls": [],
            "dropdowns": [],
            "required_fields": [],
            "buttons": [],
            "dummy_fill_attempts": [],
            "audit": audit,
            "dummy_values": dummy_values,
        }
    # Capture initial page even if the link was not necessary/available.
    for step in bizflow_multitab_plan():
        tab = step["tab"]
        tab_audit = await _ensure_bizflow_tab_open(page, tab, max_steps=3)
        if fill_dummy and not step.get("nested_add"):
            try:
                # BizFlow row creation is not a generic repeatable table action.
                # The golden UHAUL screens require section-specific row buttons:
                # Source -> Attribute +Add, Target -> Process Step +Add.
                # Configure Routing is special: first click the routing table Add
                # to open the rule drawer, then create condition/action rows
                # inside that active drawer. Do not run nested row adds before
                # the drawer exists, otherwise the table Add is mistaken for a
                # Conditions/Actions row Add.
                repeatable_row_audits.append(await _apply_bizflow_nested_row_adds(page, input_data or {}, tab))
                repeatable_row_audits.append(await restore_filled_values(page, "biz_flow", reason=f"after nested Add planning {tab}"))
            except Exception as exc:
                audit["warnings"].append(f"BizFlow nested row Add planning skipped for {tab}: {mask_sensitive_string(str(exc))}")
        audit.setdefault("form_state_learning", {}).setdefault("snapshots", []).append(await _capture_bizflow_form_state(page, label=f"tab-open {tab}"))
        controls = _filter_bizflow_controls(await _evaluate_controls(page))
        stateful_controls = await capture_stateful_controls(page, "biz_flow")
        if stateful_controls:
            seen_selectors = {str(c.get("selector") or "") for c in controls if isinstance(c, dict)}
            controls.extend(dict(c) for c in stateful_controls if str(c.get("selector") or "") not in seen_selectors)
        buttons = _filter_bizflow_buttons(await _evaluate_buttons(page))
        dropdowns = await _collect_bizflow_dropdowns_with_options(page, controls, tab_label=tab)
        required = _required_fields(controls)
        # Explore only after the current tab's input.json path has been filled and
        # judged. Empty-tab crawling misses children revealed by Source/Target Type,
        # Process Step Type, Condition Type and Action Type.
        for c in controls:
            c.setdefault("bizflow_tab", tab)
        for d in dropdowns:
            d.setdefault("bizflow_tab", tab)
        for r in required:
            r.setdefault("bizflow_tab", tab)
        attempts: List[Dict[str, Any]] = []
        if fill_dummy and controls:
            attempts = await fill_dummy_no_save(page, controls, dummy_values, input_data)
            for a in attempts:
                a.setdefault("bizflow_tab", tab)
            if tab == "Source Details":
                await _fill_bizflow_source_attribute_rows(page, input_data or {}, attempts, tab)
                await restore_filled_values(page, "biz_flow", reason="after Configure Source attribute rows", attempts=attempts)
            if tab == "Target Details":
                await _fill_bizflow_process_step_rows(page, input_data or {}, attempts, tab)
                await restore_filled_values(page, "biz_flow", reason="after Configure Target process rows", attempts=attempts)

        async def _repair_current_tab(_repair_no: int, _judge_report: Dict[str, Any]) -> None:
            live_controls = _filter_bizflow_controls(await _evaluate_controls(page))
            live_stateful = await capture_stateful_controls(page, "biz_flow")
            if live_stateful:
                seen_selectors = {str(c.get("selector") or "") for c in live_controls if isinstance(c, dict)}
                live_controls.extend(dict(c) for c in live_stateful if str(c.get("selector") or "") not in seen_selectors)
            for cc in live_controls:
                cc.setdefault("bizflow_tab", tab)
            repair_attempts = await fill_dummy_no_save(page, live_controls, dummy_values, input_data) if live_controls else []
            for aa in repair_attempts:
                aa.setdefault("bizflow_tab", tab)
                aa["section_judge_repair"] = True
            attempts.extend(repair_attempts)
            if tab == "Source Details":
                repeatable_row_audits.append(await _apply_bizflow_nested_row_adds(page, input_data or {}, tab))
                await _fill_bizflow_source_attribute_rows(page, input_data or {}, attempts, tab)
            elif tab == "Target Details":
                repeatable_row_audits.append(await _apply_bizflow_nested_row_adds(page, input_data or {}, tab))
                await _fill_bizflow_process_step_rows(page, input_data or {}, attempts, tab)
            await restore_filled_values(page, "biz_flow", reason=f"section judge repair {tab}", attempts=attempts)
            await _execute_state_graph_section(tab, attempts, f"judge_repair_{_repair_no}")

        nested: Dict[str, Any] = {}
        # Gate each completed non-routing section before moving to the next tab.
        if not step.get("nested_add"):
            graph_gate = await _execute_state_graph_section(tab, attempts, "target_branch_before_judge")
            if not graph_gate.get("pass"):
                await _repair_current_tab(0, {"source": "state_graph", "failed_attempts": graph_gate.get("failed_attempts", [])})
                graph_gate = await _execute_state_graph_section(tab, attempts, "target_branch_after_repair")
            if not graph_gate.get("pass"):
                audit["blocked_at_section"] = tab
                raise RuntimeError(f"BizFlow deterministic state graph could not commit {tab}: {graph_gate.get('failed_attempts', [])}")
            section_gate = await judge_with_repairs(tab, attempts, _repair_current_tab)
            if not section_gate.get("pass"):
                controls = _filter_bizflow_controls(await _evaluate_controls(page))
                buttons = _filter_bizflow_buttons(await _evaluate_buttons(page))
                dropdowns = await _collect_bizflow_dropdowns_with_options(page, controls, tab_label=tab)
                required = _required_fields(controls)
            else:
                try:
                    learned = await run_portal_form_exploration(
                        page=page, phase="biz_flow", section=tab, input_data=input_data or {},
                        controls=_filter_bizflow_controls(await _evaluate_controls(page)),
                        dropdowns=await _collect_bizflow_dropdowns_with_options(page, _filter_bizflow_controls(await _evaluate_controls(page)), tab_label=tab),
                        buttons=_filter_bizflow_buttons(await _evaluate_buttons(page)),
                        repeatable_plan=build_repeatable_section_plan(input_data or {}, "biz_flow"),
                        repeatable_audit=repeatable_row_audits, output_dir=exploration_root,
                        config=config, allow_live_branching=True,
                    )
                    if learned.get("restore_errors") or str(learned.get("status") or "").startswith("failed"):
                        raise RuntimeError(f"{tab} exploration did not restore the target values")
                    learned["learning_order"] = "target branch first; alternative branches second; section rejudged"
                    exploration_graphs.append(learned)
                    await restore_filled_values(page, "biz_flow", reason=f"after target-first exploration {tab}", attempts=attempts)
                    restored_graph_gate = await _execute_state_graph_section(tab, attempts, "post_exploration_restore")
                    if not restored_graph_gate.get("pass"):
                        raise RuntimeError(f"{tab} deterministic target path failed after exploration")
                    post_explore_gate = await judge_with_repairs(tab, attempts, _repair_current_tab)
                    if not post_explore_gate.get("pass"):
                        raise RuntimeError(f"{tab} failed after restoring the explored parent branches")
                except Exception as exc:
                    raise RuntimeError("BizFlow target-first learning failed closed: " + mask_sensitive_string(str(exc)))

        # Configure Routing has its own + Add that opens/creates the route row/form.
        if step.get("nested_add"):
            nested = await _click_configure_routing_add(page)
            await restore_filled_values(page, "biz_flow", reason="after Configure Routing top-level Add")
            if fill_dummy:
                try:
                    repeatable_row_audits.append(await _apply_bizflow_nested_row_adds(page, input_data or {}, "Configure Routing + Add"))
                except Exception as exc:
                    audit["warnings"].append(f"BizFlow nested row Add planning skipped for Configure Routing + Add: {mask_sensitive_string(str(exc))}")
            audit.setdefault("form_state_learning", {}).setdefault("snapshots", []).append(await _capture_bizflow_form_state(page, label="routing-after-top-level-add"))
            nested_controls = _filter_bizflow_controls(await _evaluate_controls(page))
            nested_stateful_controls = await capture_stateful_controls(page, "biz_flow")
            if nested_stateful_controls:
                seen_selectors = {str(c.get("selector") or "") for c in nested_controls if isinstance(c, dict)}
                nested_controls.extend(dict(c) for c in nested_stateful_controls if str(c.get("selector") or "") not in seen_selectors)
            nested_buttons = _filter_bizflow_buttons(await _evaluate_buttons(page))
            nested_dropdowns = await _collect_bizflow_dropdowns_with_options(page, nested_controls, tab_label="Configure Routing + Add")
            nested_required = _required_fields(nested_controls)
            for c in nested_controls:
                c.setdefault("bizflow_tab", "Configure Routing + Add")
            nested_attempts: List[Dict[str, Any]] = []
            if fill_dummy and nested_controls:
                nested_attempts = await fill_dummy_no_save(page, nested_controls, dummy_values, input_data)
                for a in nested_attempts:
                    a.setdefault("bizflow_tab", "Configure Routing + Add")
                await _fill_bizflow_routing_condition_rows(page, input_data or {}, nested_attempts, "Configure Routing + Add")
                await _fill_bizflow_routing_action_rows(page, input_data or {}, nested_attempts, "Configure Routing + Add")
                await restore_filled_values(page, "biz_flow", reason="after Configure Routing actions rows", attempts=nested_attempts)

            async def _repair_routing(_repair_no: int, _judge_report: Dict[str, Any]) -> None:
                repeatable_row_audits.append(await _apply_bizflow_nested_row_adds(page, input_data or {}, "Configure Routing + Add"))
                live_nested = _filter_bizflow_controls(await _evaluate_controls(page))
                live_nested_stateful = await capture_stateful_controls(page, "biz_flow")
                if live_nested_stateful:
                    seen_selectors = {str(c.get("selector") or "") for c in live_nested if isinstance(c, dict)}
                    live_nested.extend(dict(c) for c in live_nested_stateful if str(c.get("selector") or "") not in seen_selectors)
                for cc in live_nested:
                    cc.setdefault("bizflow_tab", "Configure Routing + Add")
                extra = await fill_dummy_no_save(page, live_nested, dummy_values, input_data) if live_nested else []
                for aa in extra:
                    aa.setdefault("bizflow_tab", "Configure Routing + Add")
                    aa["section_judge_repair"] = True
                nested_attempts.extend(extra)
                await _fill_bizflow_routing_condition_rows(page, input_data or {}, nested_attempts, "Configure Routing + Add")
                await _fill_bizflow_routing_action_rows(page, input_data or {}, nested_attempts, "Configure Routing + Add")
                await restore_filled_values(page, "biz_flow", reason="routing section judge repair", attempts=nested_attempts)
                await _execute_state_graph_section("Configure Routing", nested_attempts, f"judge_repair_{_repair_no}")

            routing_graph_gate = await _execute_state_graph_section("Configure Routing", nested_attempts, "target_branch_before_judge")
            if not routing_graph_gate.get("pass"):
                await _repair_routing(0, {"source": "state_graph", "failed_attempts": routing_graph_gate.get("failed_attempts", [])})
                routing_graph_gate = await _execute_state_graph_section("Configure Routing", nested_attempts, "target_branch_after_repair")
            if not routing_graph_gate.get("pass"):
                audit["blocked_at_section"] = "Configure Routing"
                raise RuntimeError(f"BizFlow deterministic state graph could not commit Configure Routing: {routing_graph_gate.get('failed_attempts', [])}")
            routing_gate = await judge_with_repairs("Configure Routing", nested_attempts, _repair_routing)
            if routing_gate.get("pass"):
                try:
                    live_nested = _filter_bizflow_controls(await _evaluate_controls(page))
                    learned = await run_portal_form_exploration(
                        page=page, phase="biz_flow", section="Configure Routing + Add",
                        input_data=input_data or {}, controls=live_nested,
                        dropdowns=await _collect_bizflow_dropdowns_with_options(page, live_nested, tab_label="Configure Routing + Add"),
                        buttons=_filter_bizflow_buttons(await _evaluate_buttons(page)),
                        repeatable_plan=build_repeatable_section_plan(input_data or {}, "biz_flow"),
                        repeatable_audit=repeatable_row_audits, output_dir=exploration_root,
                        config=config, allow_live_branching=True,
                    )
                    if learned.get("restore_errors") or str(learned.get("status") or "").startswith("failed"):
                        raise RuntimeError("Configure Routing exploration did not restore target values")
                    learned["learning_order"] = "target routing first; alternative branches second; routing rejudged"
                    exploration_graphs.append(learned)
                    await restore_filled_values(page, "biz_flow", reason="after target-first routing exploration", attempts=nested_attempts)
                    restored_routing_graph = await _execute_state_graph_section("Configure Routing", nested_attempts, "post_exploration_restore")
                    if not restored_routing_graph.get("pass"):
                        raise RuntimeError("Configure Routing deterministic target path failed after exploration")
                    routing_gate = await judge_with_repairs("Configure Routing", nested_attempts, _repair_routing)
                    if not routing_gate.get("pass"):
                        raise RuntimeError("Configure Routing failed after branch restoration")
                except Exception as exc:
                    raise RuntimeError("BizFlow routing learning failed closed: " + mask_sensitive_string(str(exc)))
            controls = [*controls, *nested_controls]
            buttons = [*buttons, *nested_buttons]
            dropdowns = [*dropdowns, *nested_dropdowns]
            required = [*required, *nested_required]
            attempts = [*attempts, *nested_attempts]
            audit["routing_nested_add"] = nested
        all_controls.extend(controls)
        all_buttons.extend(buttons)
        all_dropdowns.extend(dropdowns)
        all_required.extend(required)
        all_attempts.extend(attempts)
        tab_info = {
            "tab": tab,
            "tab_click": tab_audit,
            "controls": len(controls),
            "dropdowns": len(dropdowns),
            "required_fields": len(required),
            "dummy_fill_attempts": len(attempts),
            "nested_add": nested,
            "section_judge_pass": not bool(audit.get("blocked_at_section")),
        }
        audit["tabs"].append(tab_info)
        # Fail closed: never move to the next section until the current section is approved.
        if audit.get("blocked_at_section"):
            tab_info["continue"] = {"clicked": False, "blocked_by_section_judge": True, "section": audit.get("blocked_at_section")}
            break
        # If the portal uses Next buttons instead of clickable tabs, advance after each approved non-routing tab.
        if tab != "Configure Routing":
            cont = await _click_bizflow_continue(page)
            tab_info["continue"] = cont
    repeatable_row_audits.append(await restore_filled_values(page, "biz_flow", reason="before final BizFlow screenshot/evidence", attempts=all_attempts))
    final_surface_gate = await assert_active_surface(page, "biz_flow")
    audit["final_surface_gate"] = final_surface_gate
    if final_surface_gate.get("fatal"):
        audit["warnings"].extend(final_surface_gate.get("fatal") or [])
    # Deduplicate controls by selector+label+tab to avoid exploding counts.
    seen=set(); controls_d=[]
    for c in all_controls:
        key=(c.get("bizflow_tab"), c.get("selector"), c.get("label") or c.get("name") or c.get("id"))
        if key in seen: continue
        seen.add(key); controls_d.append(c)
    seen=set(); dropdowns_d=[]
    for d in all_dropdowns:
        key=(d.get("bizflow_tab"), d.get("selector"), d.get("label"))
        if key in seen: continue
        seen.add(key); dropdowns_d.append(d)
    seen=set(); required_d=[]
    for r in all_required:
        key=(r.get("bizflow_tab"), r.get("selector"), r.get("label"))
        if key in seen: continue
        seen.add(key); required_d.append(r)
    exploration_knowledge: Dict[str, Any] = {}
    try:
        exploration_knowledge = merge_section_knowledge(
            exploration_graphs,
            phase="biz_flow",
            output_file=exploration_root / "biz_flow_form_knowledge.json",
        )
    except Exception as exc:
        audit["warnings"].append(f"BizFlow exploration knowledge merge warning: {mask_sensitive_string(str(exc))}")

    latest_by_section: Dict[str, Dict[str, Any]] = {}
    for execution in state_graph_executions:
        if isinstance(execution, dict):
            latest_by_section[str(execution.get("graph_section") or execution.get("section") or "all")] = execution
    combined_controls: List[Dict[str, Any]] = []
    combined_edges: List[Dict[str, Any]] = []
    combined_attempts: List[Dict[str, Any]] = []
    seen_controls = set()
    seen_edges = set()
    for execution in latest_by_section.values():
        combined_attempts.extend([dict(x) for x in execution.get("attempts", []) if isinstance(x, dict)])
        for control in execution.get("final_controls", []):
            if not isinstance(control, dict):
                continue
            key = (control.get("section"), control.get("row_signature"), control.get("row_index"), control.get("label"), control.get("name"))
            if key in seen_controls:
                continue
            seen_controls.add(key)
            combined_controls.append(control)
        for edge in execution.get("observed_dependency_edges", []):
            if not isinstance(edge, dict):
                continue
            key = edge.get("edge_id") or json.dumps(edge, sort_keys=True, default=str)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            combined_edges.append(edge)
    combined_failed = [x for execution in latest_by_section.values() for x in execution.get("failed_attempts", []) if isinstance(x, dict)]
    combined_stage_audits = [
        dict(execution.get("execution_stage_audit") or {})
        for execution in latest_by_section.values()
        if isinstance(execution, dict) and isinstance(execution.get("execution_stage_audit"), dict)
    ]
    combined_execution_stage_audit = {
        "schema_version": "hip.live-execution-stage-audit.v1",
        "phase": "biz_flow",
        "section": "all_tabs",
        "form_opened": bool(combined_stage_audits) and all(bool(x.get("form_opened")) for x in combined_stage_audits),
        "controls_discovered": bool(combined_stage_audits) and all(bool(x.get("controls_discovered")) for x in combined_stage_audits),
        "controls_bound": bool(combined_stage_audits) and all(bool(x.get("controls_bound")) for x in combined_stage_audits),
        "fields_filled_or_verified": bool(combined_stage_audits) and all(bool(x.get("fields_filled_or_verified")) for x in combined_stage_audits),
        "exact_execution_verified": bool(combined_stage_audits) and all(bool(x.get("exact_execution_verified")) for x in combined_stage_audits),
        "section_audits": combined_stage_audits,
    }
    combined_execution = {
        "schema_version": "hip.stateful-form-execution.v1",
        "phase": "biz_flow",
        "section": "all_tabs",
        "graph_id": state_graph.get("graph_id"),
        "strategy": state_graph.get("strategy"),
        "pass": bool(latest_by_section) and not combined_failed and all(bool(x.get("pass")) for x in latest_by_section.values()),
        "status": "pass" if latest_by_section and not combined_failed and all(bool(x.get("pass")) for x in latest_by_section.values()) else "failed",
        "attempts": combined_attempts,
        "failed_attempts": combined_failed,
        "strict_live_execution": True,
        "execution_stage_audit": combined_execution_stage_audit,
        "observed_dependency_edges": combined_edges,
        "final_controls": combined_controls,
        "section_executions": list(latest_by_section.values()),
    }
    target_branch_knowledge = build_target_branch_knowledge(state_graph, combined_execution)
    target_branch_knowledge["section_executions"] = [
        {"section": key, "pass": value.get("pass"), "status": value.get("status"), "failed_attempts": value.get("failed_attempts", [])}
        for key, value in latest_by_section.items()
    ]
    safe_write_json(exploration_root / "biz_flow_target_branch_form_knowledge.json", target_branch_knowledge)
    return {
        "controls": controls_d,
        "dropdowns": dropdowns_d,
        "required_fields": required_d,
        "buttons": all_buttons,
        "dummy_fill_attempts": all_attempts,
        "audit": audit,
        "repeatable_section_plan": build_repeatable_section_plan(input_data or {}, "biz_flow"),
        "repeatable_row_audit": repeatable_row_audits,
        "portal_form_exploration": exploration_knowledge,
        "stateful_target_branch_graph": state_graph,
        "stateful_target_branch_executions": state_graph_executions,
        "stateful_target_branch_execution": combined_execution,
        "autonomous_goal_runtime_enabled": autonomous_phase_enabled(config, "biz_flow"),
        "autonomous_goal_runtime_policy": "each visible BizFlow tab/section is autonomously observed, reconciled, verified, and rebound before progression",
        "target_branch_knowledge": target_branch_knowledge,
        "dummy_values": dummy_values,
        "deterministic_plan_runtime": deterministic_plan_summary(input_data or {}),
    }

async def _find_visible_control_by_label(page: Page, label_pattern: str, *, phase: str = "biz_flow") -> str:
    """Return selector for a visible input/select/textarea near a label text.

    HIP DDS sometimes renders dependent controls after a prior selection but the
    form-control inventory was captured before the dependency appeared.  This is
    especially common for Configure Routing's `Attribute Name/Unit` field after
    `Condition Type = Attributes`.  This helper searches the live active form,
    not the stale inventory.
    """
    try:
        root_info = await active_form_root_info(page, phase)
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    try:
        result = await page.evaluate(r"""
({rootSel, pattern}) => {
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
    return !!(r.width && r.height && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0');
  }
  function clean(s){return String(s||'').replace(/\s+/g,' ').trim();}
  function path(el){
    const parts=[];
    while(el && el.nodeType===1 && parts.length<9){
      let p=el.tagName.toLowerCase();
      if(el.id){p += '#'+CSS.escape(el.id); parts.unshift(p); break;}
      const cls=(el.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.');
      if(cls)p+='.'+cls;
      const parent=el.parentElement;
      if(parent){const sib=Array.from(parent.children).filter(x=>x.tagName===el.tagName); if(sib.length>1)p+=':nth-of-type('+(sib.indexOf(el)+1)+')';}
      parts.unshift(p); el=parent;
    }
    return parts.join(' > ');
  }
  const root = document.querySelector(rootSel) || document.body;
  const rx = new RegExp(pattern, 'i');
  const controls = Array.from(root.querySelectorAll('input:not([type=hidden]), textarea, select, [role=combobox]')).filter(visible);
  const scored=[];
  for(const c of controls){
    const aria=clean(c.getAttribute('aria-label')||c.getAttribute('placeholder')||c.getAttribute('name')||c.id||'');
    let text=aria;
    const id=c.id;
    if(id){
      for(const lab of Array.from(root.querySelectorAll(`label[for="${CSS.escape(id)}"]`))) text += ' ' + clean(lab.innerText||lab.textContent||'');
    }
    const box=c.getBoundingClientRect();
    for(const el of Array.from(root.querySelectorAll('label,legend,span,div,dds-label'))){
      if(!visible(el)) continue;
      const t=clean(el.innerText||el.textContent||'');
      if(!t || t.length>80) continue;
      const r=el.getBoundingClientRect();
      const dy=Math.abs((r.top+r.bottom)/2 - (box.top+box.bottom)/2);
      const leftOk = r.left <= box.right + 20;
      if(dy < 70 && leftOk) text += ' ' + t;
    }
    if(rx.test(text)) scored.push({selector:path(c), text, y:box.y, x:box.x});
  }
  scored.sort((a,b)=>a.y-b.y || a.x-b.x);
  return scored[0] || {selector:'', text:''};
}
""", {"rootSel": root_sel, "pattern": label_pattern})
        return str((result or {}).get("selector") or "")
    except Exception:
        return ""

async def _fill_post_dependency_bizflow_fields(page: Page, dummy_values: Dict[str, str], attempts: List[Dict[str, Any]], tab: str = "") -> None:
    """Fill controls that appear only after earlier dropdown selections.

    The 183828 evidence showed Configure Routing opened the nested rule drawer,
    selected Condition Type/Operator/Value, but the Attribute Name/Unit combobox
    appeared after the initial inventory and stayed blank in the screenshot.
    """
    if "routing" not in (tab or "").lower() and "route_condition_attribute" not in dummy_values:
        return
    attr_val = dummy_values.get("route_condition_attribute") or dummy_values.get("condition_attribute") or ""
    if attr_val:
        sel = await _find_visible_control_by_label(page, r"attribute\s*name(?:\s*/\s*unit)?|attribute", phase="biz_flow")
        if sel:
            ok = await select_dds_combobox(page, await get_active_form_root(page, "biz_flow"), sel, attr_val, phase="biz_flow")
            attempts.append({"label": "Attribute Name/Unit", "key": "route_condition_attribute", "selector": sel, "value_used": attr_val, "success": ok, "filled": ok, "dom_events": ["click", "option/select", "change", "blur"], "safety": "no Save/Create/Submit clicked", "scoped_to_active_root": True, "bizflow_tab": tab or "Configure Routing + Add", "post_dependency_fill": True})
    # After Route Document is selected, some builds reveal a routing action target
    # dropdown.  Only run this inside Configure Routing/Actions; doing it in
    # Target Details can accidentally overwrite the parent Target Type field.
    target_val = dummy_values.get("target_transport_profile") or dummy_values.get("route_action_target") or ""
    if target_val and ("routing" in (tab or "").lower() or "action" in (tab or "").lower()):
        controls = _drop_controls_matching(
            await _find_visible_controls_in_section(page, r"actions?", r"target\s*transport|transport\s*profile|route\s*to|(^|\s)target(\s|$)", phase="biz_flow"),
            r"target\s*type", r"target\s*document", r"target\s*application",
        )
        sel = (controls[0] if controls else {}).get("selector", "")
        if sel:
            ok = await select_dds_combobox(page, await get_active_form_root(page, "biz_flow"), sel, target_val, phase="biz_flow")
            attempts.append({"label": "Target", "key": "target_transport_profile", "selector": sel, "value_used": target_val, "success": ok, "filled": ok, "dom_events": ["click", "option/select", "change", "blur"], "safety": "no Save/Create/Submit clicked", "scoped_to_active_root": True, "bizflow_tab": tab or "Configure Routing + Add", "post_dependency_fill": True})

async def fill_dummy_no_save(page: Page, controls: List[Dict[str, Any]], dummy_values: Dict[str, str], input_data: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    attempts: List[Dict[str, Any]] = []
    combo_keys = {"flow_type", "primary_domain", "source_type", "target_type", "source_application", "target_application", "source_document_type", "target_document_type", "source_transport_profile", "target_transport_profile", "condition_attribute", "condition_operator", "flow_identifier_operator", "route_condition_attribute", "route_condition_operator", "route_condition_type", "route_execute_when", "route_action_type", "route_action_target", "route_document_type", "process_step_type", "process_action", "rule_name", "mapping_identifier"}
    # Optional Dell AIA / AutoGen planner.  This does not execute browser actions;
    # it ranks visible controls, suggests key mappings, and records a plan so the
    # MCP/Playwright executor can fill dependency-created fields correctly.
    tab_for_llm = ""
    try:
        tab_for_llm = str((controls[0] or {}).get("bizflow_tab") or "") if controls else ""
    except Exception:
        tab_for_llm = ""
    controls = deterministic_sort_controls(
        controls,
        input_data or {},
        key_fields=("llm_hint_key", "mapped_bizflow_key", "key"),
        section=tab_for_llm or None,
        phase="biz_flow",
    )
    planner = LLMFormPlanner.from_env()
    if planner is not None and controls:
        try:
            before_state = await _capture_bizflow_form_state(page, label=f"llm-before-generic-fill:{tab_for_llm}")
            llm_result = planner.plan_fill(
                phase="biz_flow",
                tab=tab_for_llm,
                controls=controls,
                input_values=dummy_values,
                row_plan=[],
                before_state=before_state,
                failures=[],
            )
            attempts.append({
                "label": "Dell AIA AutoGen form planner",
                "key": "llm_form_planner",
                "selector": "",
                "success": bool(llm_result.used),
                "filled": False,
                "planner_only": True,
                "provider": llm_result.as_audit().get("provider"),
                "plan_status": llm_result.status,
                "plan": llm_result.as_audit().get("plan"),
                "bizflow_tab": tab_for_llm,
                "safety": "LLM plans only; MCP/Playwright performs safe fills; no Save/Create/Submit clicked",
            })
            if llm_result.used:
                controls = apply_llm_control_hints(controls, llm_result.plan, set(dummy_values.keys()))
        except Exception as exc:
            attempts.append({"label": "Dell AIA AutoGen form planner", "key": "llm_form_planner", "success": False, "filled": False, "planner_only": True, "reason": mask_sensitive_string(str(exc)), "bizflow_tab": tab_for_llm})
    for c in controls:
        if c.get("disabled") or c.get("readonly") or not c.get("visible", True):
            continue
        label = str(c.get("label") or c.get("placeholder") or c.get("ariaLabel") or c.get("name") or c.get("id") or "")
        key = str(c.get("llm_hint_key") or "") if c.get("llm_hint_key") in dummy_values else guess_field_key(label, c)
        if not key or key not in dummy_values:
            continue
        selector = c.get("selector") or ""
        if str(c.get("type") or "").lower() == "file":
            attempts.append({"label": label, "key": key, "selector": selector, "attempted": False, "reason": "file input skipped"})
            continue
        role = str(c.get("role") or "").lower()
        if key == "condition_operator" and "flow identifier operator" in label.lower():
            # This visible field is the aggregate condition operator
            # (`all/one or more conditions are satisfied`), not a row operator
            # like Equals/Contains.  The real row field is labelled `Operator`
            # and is filled separately.
            continue
        tab_l = (tab_for_llm or "").lower()
        # Deep BizFlow repeatables are not safe to fill through the flat generic
        # control loop.  The 015538 evidence showed this created extra Actions
        # rows and then sticky-restore wrote the wrong value into the duplicate
        # row.  Let the deterministic row executors fill these fields by
        # section + row index after row creation/expansion is verified.
        if "routing" in tab_l and key in {
            "route_condition_type", "route_condition_operator", "route_condition_value",
            "route_condition_attribute", "route_action_name", "route_action_type",
            "route_action_target",
        }:
            continue
        if "target" in tab_l and key in {
            "process_step_type", "process_step_name", "process_action",
            "process_target_document_type", "process_rule", "process_mapping_identifier",
        }:
            continue
        if role == "combobox" or c.get("placeholder") == "Select" or key in combo_keys:
            ok = await select_dds_combobox(page, await get_active_form_root(page, "biz_flow"), selector, dummy_values[key], phase="biz_flow")
            events = ["click", "option/select", "change", "blur"]
        else:
            ok = await dds_set_text_control(page, await get_active_form_root(page, "biz_flow"), selector, dummy_values[key])
            events = ["input", "change", "blur"]
        attempts.append(annotate_plan_attempt({"label": label, "key": key, "selector": selector, "value_used": dummy_values[key], "success": ok, "filled": ok, "dom_events": events, "safety": "no Save/Create/Submit clicked", "scoped_to_active_root": True, "llm_hint_key": c.get("llm_hint_key"), "llm_hint_confidence": c.get("llm_hint_confidence"), "llm_hint_reason": c.get("llm_hint_reason")}, input_data or {}, str(key or ""), section=tab_for_llm or None, phase="biz_flow"))
        # Some DDS controls briefly clear when the next combobox/listbox opens.
        await restore_filled_values(page, "biz_flow", reason=f"after generic fill {key}", attempts=attempts)
        await page.wait_for_timeout(150)
    # Re-scan/fill dynamic fields that appear only after previous selections.
    tab = ""
    try:
        tab = str((controls[0] or {}).get("bizflow_tab") or "") if controls else ""
    except Exception:
        tab = ""
    await _fill_post_dependency_bizflow_fields(page, dummy_values, attempts, tab=tab)
    await restore_filled_values(page, "biz_flow", reason=f"after post dependency fill {tab}", attempts=attempts)
    return attempts


async def _fetch_json_with_page(page: Page, url: str) -> Tuple[Any, Dict[str, Any]]:
    js = r"""
async ({url}) => {
  try {
    const res = await fetch(url, {credentials:'include', headers:{'Accept':'application/json, text/plain, */*'}});
    const text = await res.text();
    let data = null; try { data = JSON.parse(text); } catch(e) { data = text; }
    return {ok: res.ok, status: res.status, url: res.url, data};
  } catch (e) { return {ok:false, status:0, url, error:String(e)}; }
}
"""
    try:
        meta = await page.evaluate(js, {"url": url})
    except Exception as exc:
        return None, {"ok": False, "error": str(exc), "url": mask_sensitive_string(url)}
    return meta.get("data"), {k: mask_sensitive_data(v) for k, v in meta.items() if k != "data"}



def _flow_version_from_row(row: Dict[str, Any], health_data: Any = None, version_data: Any = None, environment: str = "DEV") -> str:
    """Pick the active version for runtime deployment API calls."""
    env = (environment or "DEV").upper()
    try:
        if isinstance(health_data, dict):
            h = health_data.get(env) or health_data.get(environment) or {}
            if isinstance(h, dict) and h.get("activeVersion"):
                return str(h.get("activeVersion"))
    except Exception:
        pass
    try:
        if isinstance(version_data, dict):
            for ver, env_map in version_data.items():
                if isinstance(env_map, dict) and str(env_map.get(env) or "").upper() == "ACTIVE":
                    return str(ver)
    except Exception:
        pass
    raw = (
        row.get("flow_version")
        or row.get("latestFlowVersion")
        or (row.get("raw_row_compact") or {}).get("latestFlowVersion")
        or "1.0"
    )
    raw_s = str(raw).strip()
    return raw_s if "." in raw_s else f"{raw_s}.0"


def _summarize_runtime_api_payload(row: Dict[str, Any], environment: str, version: str, deployment_data: Any, health_data: Any, version_data: Any, links_data: Any) -> Dict[str, Any]:
    """Normalize gateway runtime APIs into the same shape as visible View data.

    The portal loads the expanded runtime screen from these gateway APIs:
    - /flows/definition/version
    - /flows/health
    - /flows/deployment
    - /links?sourceType=FLOW...
    This fallback is more complete than relying only on row click/open state.
    """
    basic: Dict[str, Any] = {}
    source_details: List[Dict[str, Any]] = []
    target_details: List[Dict[str, Any]] = []
    routing_services: List[Dict[str, Any]] = []
    flow_server_sections: List[Dict[str, Any]] = []
    interface_detail_sections: List[Dict[str, Any]] = []
    links_out: List[Dict[str, Any]] = []
    source_link_objects: List[Dict[str, Any]] = []

    if isinstance(deployment_data, dict):
        bd = deployment_data.get("basicDetail") or deployment_data.get("basicDetails") or {}
        if isinstance(bd, dict):
            for k, v in bd.items():
                nk = re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_")
                basic[nk] = v
        for k in ["flowType", "flowDirection", "active"]:
            if k in deployment_data:
                nk = re.sub(r"[^a-z0-9]+", "_", k.lower()).strip("_")
                basic.setdefault(nk, deployment_data.get(k))

        def walk_side(side_name: str, side_payload: Any) -> None:
            side_items = source_details if side_name == "source" else target_details
            if isinstance(side_payload, list):
                iterable = side_payload
            else:
                iterable = [side_payload]
            for item in iterable:
                if not isinstance(item, dict):
                    continue
                for system_name, profiles in item.items():
                    if not isinstance(profiles, dict):
                        side_items.append({"system": system_name, "raw": profiles})
                        continue
                    for tp_name, tp_payload in profiles.items():
                        record = {"system": system_name, "transport_profile": tp_name}
                        if isinstance(tp_payload, dict):
                            # Preserve non-service interface details too.
                            for kk, vv in tp_payload.items():
                                if kk != "flowServices":
                                    record[kk] = vv
                            services = tp_payload.get("flowServices") or []
                            if isinstance(services, dict):
                                services = [services]
                            for svc in services if isinstance(services, list) else []:
                                if not isinstance(svc, dict):
                                    continue
                                service_record = {
                                    "side": side_name,
                                    "system": system_name,
                                    "transport_profile": tp_name,
                                    "type": svc.get("type"),
                                    "service_name": svc.get("serviceName"),
                                    "service_health": svc.get("serviceHealth"),
                                    "deployment_group_name": svc.get("deploymentGroupName"),
                                    "service_manager_name": svc.get("serviceManagerName"),
                                    "service_manager_health": svc.get("serviceManagerHealth"),
                                    "kubernetes_namespace_name": svc.get("kobNamespaceName") or svc.get("kubernetesNamespaceName"),
                                    "data_center": svc.get("dataCenter"),
                                    "image_name": svc.get("imageName"),
                                    "no_of_instance": svc.get("noOfInstance") or svc.get("no_of_instance"),
                                    "uptime": svc.get("uptime"),
                                }
                                routing_services.append(service_record)
                                flow_server_sections.append(service_record)
                                interface_detail_sections.append(record)
                                for link in svc.get("links") or []:
                                    if isinstance(link, dict):
                                        links_out.append({
                                            "side": side_name,
                                            "system": system_name,
                                            "transport_profile": tp_name,
                                            "service_type": svc.get("type"),
                                            "label": link.get("label") or link.get("type"),
                                            "type": link.get("type"),
                                            "href": link.get("value") or link.get("href") or link.get("url"),
                                        })
                        side_items.append(record)

        walk_side("source", deployment_data.get("source"))
        walk_side("target", deployment_data.get("target"))

    # Add health/version state even when deployment details are absent.
    health_env = {}
    if isinstance(health_data, dict):
        health_env = health_data.get(environment) or health_data.get(str(environment).upper()) or {}
        if isinstance(health_env, dict):
            basic.setdefault("health", health_env.get("health"))
            basic.setdefault("active_version", health_env.get("activeVersion") or health_env.get("flowVersion"))
    if isinstance(version_data, dict):
        basic.setdefault("available_versions", version_data)

    if isinstance(links_data, dict):
        source_link_objects.append(links_data.get("source") or {})
        for link in links_data.get("links") or []:
            if isinstance(link, dict):
                lo = link.get("linkedObject") or {}
                source_link_objects.append({
                    "relation": link.get("relation"),
                    "metadata": link.get("metadata"),
                    "type": lo.get("type"),
                    "id": lo.get("id"),
                    "name": lo.get("name"),
                    "version": lo.get("version"),
                    "environment": lo.get("environment"),
                    "status": lo.get("status"),
                    "owner": lo.get("linkedObjectOwner"),
                })

    return {
        "page_url": BIZFLOWS_URL,
        "selected_environment": environment,
        "basic_runtime_fields": basic,
        "source_details": source_details,
        "target_details": target_details,
        "routing_services": routing_services,
        "flow_server_sections": flow_server_sections,
        "interface_detail_sections": interface_detail_sections,
        "links": links_out,
        "linked_objects": source_link_objects,
        "api_environment": environment,
        "api_flow_version": version,
        "capture_mode": "gateway_runtime_api",
    }


async def _capture_runtime_via_gateway_api(page: Page, row: Dict[str, Any], *, environment: str = "DEV") -> Dict[str, Any]:
    parsed = urlparse(BIZFLOWS_URL)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    flow_name = str(row.get("flow_name") or row.get("flow_identifier") or "").strip()
    audit: Dict[str, Any] = {"attempted": bool(flow_name), "flow_name": flow_name, "environment": environment, "calls": []}
    if not flow_name:
        return {"captured": False, "audit": audit, "environment": environment}

    version_url = f"{origin}/inaas-gateway/hipService-svc/api/flows/definition/version?flowName={quote(flow_name)}&deploymentStatus=true"
    health_url = f"{origin}/inaas-gateway/hipService-svc/api/flows/health?flowName={quote(flow_name)}"
    version_data, version_meta = await _fetch_json_with_page(page, version_url)
    audit["calls"].append({"name": "definition_version", **version_meta})
    health_data, health_meta = await _fetch_json_with_page(page, health_url)
    audit["calls"].append({"name": "health", **health_meta})
    version = _flow_version_from_row(row, health_data, version_data, environment)

    deployment_url = f"{origin}/inaas-gateway/hipService-svc/api/flows/deployment?flowName={quote(flow_name)}&flowVersion={quote(version)}&environment={quote(environment)}"
    deployment_data, deployment_meta = await _fetch_json_with_page(page, deployment_url)
    audit["calls"].append({"name": "deployment", **deployment_meta})

    links_url = f"{origin}/inaas-gateway/hipService-svc/api/links?sourceType=FLOW&sourceName={quote(flow_name)}&sourceVersion={quote(version)}&sourceEnvironment={quote(environment)}"
    links_data, links_meta = await _fetch_json_with_page(page, links_url)
    audit["calls"].append({"name": "links", **links_meta})

    ok_any = any(c.get("ok") for c in audit["calls"])
    has_runtime = isinstance(deployment_data, dict) and bool(deployment_data.get("basicDetail") or deployment_data.get("source") or deployment_data.get("target"))
    has_aux = isinstance(health_data, dict) or isinstance(version_data, dict) or isinstance(links_data, dict)
    summary = _summarize_runtime_api_payload(row, environment, version, deployment_data, health_data, version_data, links_data)
    return {
        "captured": bool(ok_any and (has_runtime or has_aux)),
        "environment": environment,
        "version": version,
        "audit": audit,
        "runtime_summary": summary,
        "raw_runtime_api_compact": mask_sensitive_data({
            "definition_version": version_data,
            "health": health_data,
            "deployment": deployment_data,
            "links": links_data,
        }),
    }



def _candidate_detail_urls(base_url: str, row: Dict[str, Any]) -> List[str]:
    parsed = urlparse(base_url or BIZFLOWS_URL)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    rid = str(row.get("bizflow_id") or "").strip()
    name = str(row.get("flow_name") or row.get("flow_identifier") or "").strip()
    urls: List[str] = []
    if rid:
        for p in [
            f"/inaas-gateway/hipService-svc/api/flows/definition/{quote(rid)}",
            f"/inaas-gateway/hipService-svc/api/flows/definition/{quote(rid)}/details",
            f"/inaas-gateway/hipService-svc/api/flows/definition?flowId={quote(rid)}",
            f"/inaas-gateway/hipService-svc/api/flows/definition/details?flowId={quote(rid)}",
            f"/inaas-gateway/hipService-svc/api/flows/definition/list?flowId={quote(rid)}",
            f"/api/bizflows/{quote(rid)}/details",
            f"/api/bizflow/{quote(rid)}/details",
            f"/api/flow/{quote(rid)}/details",
            f"/api/flows/{quote(rid)}/details",
            f"/api/bizflows/details?bizFlowId={quote(rid)}&hipEnvironment=DEV",
            f"/api/bizflows/details-in-order?bizFlowId={quote(rid)}&hipEnvironment=DEV",
        ]:
            urls.append(origin + p)
    if name:
        for p in [
            f"/inaas-gateway/hipService-svc/api/flows/definition/list?flowName={quote(name)}",
            f"/inaas-gateway/hipService-svc/api/flows/definition/details?flowName={quote(name)}",
            f"/api/bizflows/details?bizFlowName={quote(name)}&hipEnvironment=DEV",
            f"/api/bizflows/details-in-order?bizFlowName={quote(name)}&hipEnvironment=DEV",
            f"/api/bizexchange/bizflows/details?bizFlowName={quote(name)}&hipEnvironment=DEV",
        ]:
            urls.append(origin + p)
    return list(dict.fromkeys(urls))[:12]


async def enrich_deep_profiles(page: Page, rows: List[Dict[str, Any]], *, max_rows: Optional[int], progress_cb=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    targets = rows[:max_rows] if max_rows else rows
    for i, row in enumerate(targets, 1):
        if progress_cb:
            progress_cb("bizflow_deep_profile_enrichment", i - 1, len(targets), f"deep profile {i}/{len(targets)}: {row.get('flow_name') or row.get('bizflow_id')}")
        found = None
        attempts = []
        raw = row.get("raw_row_compact") if isinstance(row, dict) else None
        if str(row.get("bizflow_id") or "").strip() and (str(row.get("source") or "").endswith("api") or (isinstance(raw, dict) and raw.get("flowId"))):
            found = {
                "inventory_key": row.get("bizflow_id") or row.get("flow_name") or row.get("flow_identifier"),
                "inventory_row": row,
                "detail_source_url": row.get("source_url") or "definition/list API",
                "normalized_from_detail": row,
                "raw_detail_compact": mask_sensitive_data(raw or row),
                "status": "captured",
                "note": "Captured from BizFlow definition/list API payload; it already contains flowId, systems, document types, transport profiles and environment/version metadata.",
            }
        for url in ([] if found else _candidate_detail_urls(page.url, row)):
            payload, meta = await _fetch_json_with_page(page, url)
            extracted = extract_bizflow_records_from_payload(payload, source_url=url, source="detail_api") if payload is not None else []
            attempts.append({"url": mask_sensitive_string(url), "status": meta.get("status"), "ok": meta.get("ok"), "records": len(extracted)})
            # Accept any JSON object/list that contains bizflow/flow details, even if extractor cannot normalize all fields.
            if payload is not None and (extracted or _contains_bizflow_hint(payload)) and bool(meta.get("ok")):
                found = {
                    "inventory_key": row.get("bizflow_id") or row.get("flow_name") or row.get("flow_identifier"),
                    "inventory_row": row,
                    "detail_source_url": mask_sensitive_string(url),
                    "normalized_from_detail": extracted[0] if extracted else {},
                    "raw_detail_compact": mask_sensitive_data(payload),
                    "status": "captured",
                }
                break
            await page.wait_for_timeout(60)
        if found:
            profiles.append(found)
        else:
            profiles.append({"inventory_key": row.get("bizflow_id") or row.get("flow_name") or row.get("flow_identifier"), "inventory_row": row, "status": "missing", "attempts": attempts})
        audit.append({"row": row, "attempts": attempts, "captured": bool(found)})
    report = {
        "total_inventory_rows": len(rows),
        "attempted": len(targets),
        "captured": sum(1 for p in profiles if p.get("status") == "captured"),
        "missing": sum(1 for p in profiles if p.get("status") != "captured"),
        "capture_percent": round(100 * (sum(1 for p in profiles if p.get("status") == "captured") / max(1, len(targets))), 2),
        "audit": audit[:200],
    }
    return profiles, report



BIZFLOW_RUNTIME_LABELS = [
    "Deployment Status", "Deployment Date", "Deployed By", "Vulnerability", "Latest Version",
    "Type", "Service Name", "Service Health", "Deployment Group Name", "Service Manager Name",
    "Service Manager Health", "Kubernetes Namespace Name", "Kubernetes Namespace", "Data Center",
    "Image Name", "No Of Instance", "No of Instance", "Instances", "Uptime", "Source Interface", "Target Interface",
]


def _extract_label_value_from_text(text: str, label: str) -> str:
    if not text:
        return ""
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text).splitlines()]
    low_label = label.lower()
    for i, line in enumerate(lines):
        low = line.lower()
        if low == low_label and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt and nxt.lower() not in {l.lower() for l in BIZFLOW_RUNTIME_LABELS}:
                return nxt
        if low.startswith(low_label.lower()):
            val = re.sub(rf"^{re.escape(label)}\s*[:\-]?\s*", "", line, flags=re.I).strip()
            # Stop at the next known label if multiple key-values are on one line.
            for other in BIZFLOW_RUNTIME_LABELS:
                if other.lower() == low_label:
                    continue
                m = re.search(rf"\s{re.escape(other)}\s*[:\-]?\s*", val, flags=re.I)
                if m:
                    val = val[:m.start()].strip()
            if val:
                return val
    m = re.search(rf"{re.escape(label)}\s*[:\-]?\s*([^\n\r]+)", text, flags=re.I)
    if m:
        val = m.group(1).strip()
        for other in BIZFLOW_RUNTIME_LABELS:
            if other.lower() == low_label:
                continue
            om = re.search(rf"\s{re.escape(other)}\s*[:\-]?\s*", val, flags=re.I)
            if om:
                val = val[:om.start()].strip()
        return val
    return ""


def extract_runtime_key_values_from_text(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for label in BIZFLOW_RUNTIME_LABELS:
        val = _extract_label_value_from_text(text or "", label)
        if val:
            key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
            values[key] = val
    return values


def summarize_runtime_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the runtime/deployment fields shown on the BizFlow View screen.

    The screenshot shared by the user shows fields that are not present in the
    definition/list API: deployment status/date/by, vulnerability/latest version,
    source/target expanded sections, Flow Servers/Interface Details, TP-ROUTING and
    FLOW-ROUTING service cards. This parser extracts those labels from visible DOM
    snapshots and section text.
    """
    body_text = str(snapshot.get("body_text") or "")
    summary: Dict[str, Any] = {
        "page_url": snapshot.get("url"),
        "selected_environment": snapshot.get("selected_environment"),
        "basic_runtime_fields": extract_runtime_key_values_from_text(body_text),
        "source_details": [],
        "target_details": [],
        "routing_services": [],
        "flow_server_sections": [],
        "interface_detail_sections": [],
        "log_links": snapshot.get("links") or [],
    }
    for sec in snapshot.get("sections") or []:
        text = str(sec.get("text") or "")
        low = text.lower()
        parsed = extract_runtime_key_values_from_text(text)
        item = {"title": sec.get("keyword") or sec.get("title") or "", "text_excerpt": text[:2500], "fields": parsed}
        if "source details" in low or "source interface" in low:
            summary["source_details"].append(item)
        if "target details" in low or "target interface" in low:
            summary["target_details"].append(item)
        if "tp-routing" in low or "flow-routing" in low or "routing" in low and ("service name" in low or "deployment group" in low):
            if "tp-routing" in low:
                item["routing_type"] = "TP-ROUTING"
            elif "flow-routing" in low:
                item["routing_type"] = "FLOW-ROUTING"
            summary["routing_services"].append(item)
        if "flow servers" in low or "service manager" in low or "kubernetes namespace" in low:
            summary["flow_server_sections"].append(item)
        if "interface details" in low or "source interface" in low or "target interface" in low:
            summary["interface_detail_sections"].append(item)
    return summary


async def _is_bizflow_runtime_detail_surface(page: Page) -> bool:
    try:
        text = (await page.locator("body").inner_text(timeout=2500)).lower()
    except Exception:
        return False
    return any(x in text for x in ["deployment status", "source details", "target details", "flow servers", "interface details", "tp-routing", "flow-routing"])


async def _search_bizflow_by_name(page: Page, flow_name: str) -> Dict[str, Any]:
    audit = {"attempted": bool(flow_name), "searched": False, "selector": "", "value": flow_name}
    if not flow_name:
        return audit
    selectors = [
        "main input[type='search']", "main input[placeholder*='Search' i]", "input[type='search']",
        "input[aria-label*='Search' i]", "input[placeholder*='Search' i]",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=800) and await loc.is_enabled(timeout=800):
                if not await dds_set_text_control(page, None, sel, flow_name, phase="biz_flow"):
                    continue
                session = getattr(page, "_hip_browser_session", None)
                if semantic_runtime_enabled(page) and session is not None and hasattr(session, "press_and_log"):
                    await session.press_and_log(locator=page.locator(sel).first, key="Enter", selector=sel)
                else:
                    await page.locator(sel).first.press("Enter")
                await page.wait_for_timeout(1800)
                audit.update({"searched": True, "selector": sel})
                return audit
        except Exception:
            continue
    return audit


async def _find_view_button_for_flow(page: Page, flow_name: str) -> Dict[str, Any]:
    js = r"""
({flowName}) => {
  function clean(s){ return (s||'').trim().replace(/\s+/g,' '); }
  function path(el){
    const parts=[]; while(el && el.nodeType===1 && parts.length<8){
      let p=el.tagName.toLowerCase();
      if(el.id){ p += '#'+CSS.escape(el.id); parts.unshift(p); break; }
      const cls=(el.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>CSS.escape(c)).join('.');
      if(cls) p += '.'+cls;
      const parent=el.parentElement; if(parent){ const sib=Array.from(parent.children).filter(x=>x.tagName===el.tagName); if(sib.length>1) p += ':nth-of-type('+(sib.indexOf(el)+1)+')'; }
      parts.unshift(p); el=parent;
    }
    return parts.join(' > ');
  }
  const target = clean(flowName).toLowerCase();
  const rows = Array.from(document.querySelectorAll('table tbody tr,[role=row],.dds__table tbody tr')).filter(r => {
    const rect = r.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && clean(r.innerText || r.textContent).toLowerCase().includes(target);
  });
  for (const row of rows) {
    const buttons = Array.from(row.querySelectorAll('button,a,[role=button],dds-button'));
    const view = buttons.find(b => /\bview\b/i.test(clean(b.innerText || b.textContent || b.getAttribute('aria-label') || b.getAttribute('title') || '')));
    if (view) return {selector:path(view), label: clean(view.innerText || view.getAttribute('aria-label') || 'View'), method:'row_view'};
    const safe = buttons.find(b => !/edit|delete|remove|deploy|save|submit|create/i.test(clean(b.innerText || b.textContent || b.getAttribute('aria-label') || b.getAttribute('title') || '')));
    if (safe) return {selector:path(safe), label: clean(safe.innerText || safe.getAttribute('aria-label') || 'row action'), method:'row_safe_action'};
    const nameCellLink = Array.from(row.querySelectorAll('a,button,[role=button]')).find(b => clean(b.innerText || b.textContent).toLowerCase().includes(target));
    if (nameCellLink) return {selector:path(nameCellLink), label: clean(nameCellLink.innerText || 'flow name'), method:'name_cell'};
  }
  return null;
}
"""
    try:
        cand = await page.evaluate(js, {"flowName": flow_name})
        return cand or {}
    except Exception:
        return {}


async def _open_bizflow_view_detail(page: Page, bizflows_url: str, row: Dict[str, Any]) -> Dict[str, Any]:
    flow_name = str(row.get("flow_name") or row.get("flow_identifier") or "").strip()
    audit: Dict[str, Any] = {"flow_name": flow_name, "opened": False, "search": {}, "click": {}}
    try:
        await page.goto(bizflows_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1600)
        await _dismiss_bizflow_overlays(page)
    except Exception as exc:
        audit["navigation_error"] = str(exc)
    audit["search"] = await _search_bizflow_by_name(page, flow_name)
    cand = await _find_view_button_for_flow(page, flow_name)
    audit["click"] = cand or {}
    if cand.get("selector"):
        try:
            if await _governed_bizflow_click(page, selector=cand["selector"], action_label=f"View BizFlow {flow_name}", timeout=2000):
                await page.wait_for_timeout(2500)
                audit["opened"] = await _is_bizflow_runtime_detail_surface(page)
                return audit
        except Exception as exc:
            audit["click_error"] = str(exc)
    # Last safe fallback: click a non-mutating View button if the filtered grid has one row.
    try:
        view_sel = "main button:has-text('View'), main a:has-text('View'), main [role=button]:has-text('View')"
        if await _governed_bizflow_click(page, selector=view_sel, action_label=f"View BizFlow {flow_name}", timeout=1500):
            await page.wait_for_timeout(2500)
            audit["opened"] = await _is_bizflow_runtime_detail_surface(page)
            audit["click"] = {"selector": view_sel, "method": "fallback_view_semantic"}
    except Exception:
        pass
    return audit


async def _click_runtime_labels(page: Page, labels: List[str], *, stage: str) -> List[Dict[str, Any]]:
    audits: List[Dict[str, Any]] = []
    for label in labels:
        audit = {"label": label, "clicked": False, "stage": stage, "selector": ""}
        for sel in [
            f"button:has-text('{label}')", f"[role=button]:has-text('{label}')", f"[role=tab]:has-text('{label}')",
            f"a:has-text('{label}')", f".dds__accordion__header:has-text('{label}')", f".dds__tabs__tab:has-text('{label}')",
        ]:
            try:
                if await _governed_bizflow_click(page, selector=sel, action_label=f"Open BizFlow runtime section {label}", timeout=800):
                    await page.wait_for_timeout(650)
                    audit.update({"clicked": True, "selector": sel})
                    break
            except Exception:
                continue
        audits.append(audit)
    return audits


async def _collect_runtime_dom_snapshot(page: Page, *, flow_name: str, environment: str, stage: str) -> Dict[str, Any]:
    js = r"""
({flowName, environment, stage}) => {
  function clean(s){ return (s||'').trim().replace(/[ \t]+/g,' ').replace(/\n\s+/g,'\n').trim(); }
  const keywords = ['Basic Details','Source Details','Target Details','Flow Servers','Interface Details','TP-ROUTING','FLOW-ROUTING','Deployment Status','Deployment Date','Service Name','Service Health','Deployment Group Name','Service Manager Name','Kubernetes Namespace','Data Center','Image Name','No Of Instance','Uptime'];
  const sections = [];
  const candidates = Array.from(document.querySelectorAll('section,article,.dds__card,.card,.dds__accordion__item,.dds__accordion__content,.dds__tabs__pane,[role=tabpanel],main div'));
  const seen = new Set();
  for (const el of candidates) {
    if (el.closest('#onetrust-pc-sdk,#onetrust-banner-sdk,.onetrust-pc-dark-filter,nav,header,footer')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) continue;
    const text = clean(el.innerText || el.textContent || '');
    if (!text || text.length < 20) continue;
    const match = keywords.find(k => text.toLowerCase().includes(k.toLowerCase()));
    if (!match) continue;
    const key = match + '|' + text.slice(0,120);
    if (seen.has(key)) continue;
    seen.add(key);
    sections.push({keyword: match, text: text.slice(0,5000)});
    if (sections.length >= 80) break;
  }
  const links = Array.from(document.querySelectorAll('main a[href],a[href]')).map(a => ({
    text: clean(a.innerText || a.textContent || a.getAttribute('aria-label') || ''),
    href: a.href || '',
  })).filter(a => /log|transaction|application|deployment|view/i.test(a.text + ' ' + a.href)).slice(0,100);
  const envTabs = Array.from(document.querySelectorAll('[role=tab],button,a,.dds__tabs__tab')).map(e => clean(e.innerText || e.textContent || e.getAttribute('aria-label') || '')).filter(Boolean).filter(t => /^(DEV|TEST|TEST1|TEST2|UAT|PROD)$/i.test(t)).slice(0,20);
  return {
    flow_name: flowName,
    environment,
    stage,
    url: location.href,
    title: document.title,
    selected_environment: environment,
    environment_tabs_visible: Array.from(new Set(envTabs)),
    body_text: clean(document.body.innerText || '').slice(0,60000),
    sections,
    links,
    captured_at: new Date().toISOString(),
  };
}
"""
    try:
        return await page.evaluate(js, {"flowName": flow_name, "environment": environment, "stage": stage})
    except Exception as exc:
        return {"flow_name": flow_name, "environment": environment, "stage": stage, "error": str(exc), "captured_at": utc_now()}


async def capture_runtime_deployment_profiles(page: Page, browser: BrowserSession, rows: List[Dict[str, Any]], *, bizflows_url: str, max_rows: Optional[int], progress_cb=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Capture expanded BizFlow View/runtime/deployment data for each old BizFlow.

    This covers the fields visible in the user's DevTools screenshot: environment tabs,
    Basic/Source/Target expanded panels, Flow Servers, Interface Details, TP-ROUTING,
    FLOW-ROUTING, service health, service manager, Kubernetes namespace, image, uptime
    and log links. It performs only View/expand/tab clicks and blocks mutating actions.
    """
    targets = rows[:max_rows] if max_rows else rows
    profiles: List[Dict[str, Any]] = []
    for i, row in enumerate(targets, 1):
        flow_name = str(row.get("flow_name") or row.get("flow_identifier") or row.get("bizflow_id") or "").strip()
        if progress_cb:
            progress_cb("bizflow_runtime_deployment_capture", i - 1, len(targets), f"runtime profile {i}/{len(targets)}: {flow_name}")
        browser.set_stage("bizflow_runtime_deployment_capture")
        profile: Dict[str, Any] = {
            "inventory_key": row.get("bizflow_id") or flow_name,
            "inventory_row": row,
            "open_audit": {},
            "status": "missing",
            "environments": [],
            "runtime_network_event_count_before": len(getattr(browser, "network_tab_events", []) or []),
        }

        # Prefer direct gateway runtime APIs. They are the same APIs behind the expanded
        # View screen and avoid brittle row-click/open failures. UI View capture remains
        # as a fallback when APIs do not return data.
        api_runtime = await _capture_runtime_via_gateway_api(page, row, environment="DEV")
        profile["api_runtime_audit"] = api_runtime.get("audit", {})
        if api_runtime.get("captured"):
            profile["status"] = "captured"
            profile["environments"].append({
                "environment": api_runtime.get("environment") or "DEV",
                "click_audit": {"gateway_api": api_runtime.get("audit", {})},
                "runtime_summary": api_runtime.get("runtime_summary", {}),
                "raw_runtime_api_compact": api_runtime.get("raw_runtime_api_compact", {}),
                "version": api_runtime.get("version"),
                "capture_mode": "gateway_runtime_api",
            })
            profile["runtime_network_event_count_after"] = len(getattr(browser, "network_tab_events", []) or [])
            profiles.append(profile)
            continue

        open_audit = await _open_bizflow_view_detail(page, bizflows_url, row)
        profile["open_audit"] = open_audit
        if not open_audit.get("opened"):
            profiles.append(profile)
            continue
        # Capture whichever env tabs are visible. Always include DEV because it is the common default.
        try:
            visible_envs = await page.evaluate("""() => Array.from(document.querySelectorAll('[role=tab],button,a,.dds__tabs__tab')).map(e => (e.innerText||e.textContent||e.getAttribute('aria-label')||'').trim()).filter(t => /^(DEV|TEST|TEST1|TEST2|UAT|PROD)$/i.test(t))""")
        except Exception:
            visible_envs = []
        envs = []
        for env in ["DEV", *(visible_envs or [])]:
            env_s = str(env).strip().upper()
            if env_s and env_s not in envs:
                envs.append(env_s)
        for env in envs[:8]:
            env_audit = await _click_runtime_labels(page, [env], stage="environment")
            expand_audit = await _click_runtime_labels(page, ["Expand All", "Basic Details", "Source Details", "Target Details"], stage="expand_sections")
            first_snapshot = await _collect_runtime_dom_snapshot(page, flow_name=flow_name, environment=env, stage="expanded_all")
            flow_server_audit = await _click_runtime_labels(page, ["Flow Servers"], stage="flow_servers_tab")
            flow_server_snapshot = await _collect_runtime_dom_snapshot(page, flow_name=flow_name, environment=env, stage="flow_servers")
            interface_audit = await _click_runtime_labels(page, ["Interface Details"], stage="interface_details_tab")
            interface_snapshot = await _collect_runtime_dom_snapshot(page, flow_name=flow_name, environment=env, stage="interface_details")
            merged_text = "\n".join(str(x.get("body_text") or "") for x in [first_snapshot, flow_server_snapshot, interface_snapshot])
            merged_sections = []
            for snap in [first_snapshot, flow_server_snapshot, interface_snapshot]:
                merged_sections.extend(snap.get("sections") or [])
            merged_links = []
            for snap in [first_snapshot, flow_server_snapshot, interface_snapshot]:
                merged_links.extend(snap.get("links") or [])
            env_snapshot = {
                "flow_name": flow_name,
                "environment": env,
                "url": first_snapshot.get("url"),
                "body_text": merged_text[:100000],
                "sections": merged_sections,
                "links": merged_links,
                "stage_snapshots": {
                    "expanded_all": first_snapshot,
                    "flow_servers": flow_server_snapshot,
                    "interface_details": interface_snapshot,
                },
            }
            profile["environments"].append({
                "environment": env,
                "click_audit": {"env": env_audit, "expand": expand_audit, "flow_servers": flow_server_audit, "interface_details": interface_audit},
                "runtime_summary": summarize_runtime_snapshot(env_snapshot),
                "raw_runtime_snapshot_compact": mask_sensitive_data(env_snapshot),
            })
        profile["runtime_network_event_count_after"] = len(getattr(browser, "network_tab_events", []) or [])
        profile["status"] = "captured" if profile["environments"] else "missing"
        profiles.append(profile)
    captured = sum(1 for p in profiles if p.get("status") == "captured")
    report = {
        "total_inventory_rows": len(rows),
        "attempted": len(targets),
        "captured": captured,
        "missing": len(targets) - captured,
        "capture_percent": round(100 * captured / max(1, len(targets)), 2),
        "fields_targeted": BIZFLOW_RUNTIME_LABELS,
        "safety": "View/expand/tab clicks only; Save/Create/Submit/Delete/Deploy are never clicked.",
    }
    return profiles, report

def _required_fields(controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [c for c in controls if c.get("required")]


def _build_dom_event_kb(controls: List[Dict[str, Any]], dropdowns: List[Dict[str, Any]], buttons: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "controls_event_model": [{"label": c.get("label"), "selector": c.get("selector"), "events": c.get("dom_events_to_try", [])} for c in controls],
        "dropdown_event_model": dropdowns,
        "unsafe_buttons_blocked": [b for b in buttons if b.get("unsafe_for_kb_run")],
        "safe_buttons_seen": [b for b in buttons if not b.get("unsafe_for_kb_run")],
    }


def _write_json(path: Path, data: Any) -> str:
    return safe_write_json(path, data)


def _write_inventory_csv(path: Path, rows: List[Dict[str, Any]]) -> str:
    fields = ["bizflow_id", "flow_name", "flow_identifier", "flow_version", "flow_type", "primary_domains", "template_name", "template_version", "source_system", "target_systems", "status", "environment", "source_document_type", "target_document_type", "rule_name", "mapping_identifier", "source_transport_profile", "target_transport_profile", "deployment_group", "source", "source_url"]
    return safe_write_csv(path, fields, rows, extrasaction="ignore")


def _write_controls_csv(path: Path, controls: List[Dict[str, Any]]) -> str:
    fields = ["index", "label", "tag", "type", "role", "name", "id", "placeholder", "required", "disabled", "readonly", "value", "selector"]
    return safe_write_csv(path, fields, controls, extrasaction="ignore")


def _write_progress(kb_dir: Path, *, phase: str, completed: int, total: int, detail: str = "", counts: Optional[Dict[str, Any]] = None) -> None:
    payload = {"timestamp": utc_now(), "phase": phase, "completed": completed, "total": max(1, total), "percent": round(100 * completed / max(1, total), 2), "detail": detail, "counts": counts or {}}
    try:
        kb_dir.mkdir(parents=True, exist_ok=True)
        (kb_dir / "bizflow_progress.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        with (kb_dir / "bizflow_progress.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        (kb_dir / "bizflow_progress_heartbeat.txt").write_text(f"{payload['timestamp']} | {phase} | {completed}/{total} | {payload['percent']}% | {detail}\n", encoding="utf-8")
    except Exception:
        pass


def _write_api_flow_kg_safe(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    """Export the reporting Knowledge Graph without replaying a completed portal phase.

    Exact live form execution and independent verification are authoritative.
    Serializer/report failures are preserved as warning artifacts and never
    cause the agent to reopen and refill an already-correct unsaved form.
    """
    status_path = kb_dir / "bizflow_api_flow_knowledge_graph_export_status.json"
    try:
        files = _write_api_flow_kg(kb_dir, kb)
        status = {
            "status": "ok",
            "pass": True,
            "non_blocking": True,
            "run_id": kb.get("run_id"),
            "files": files,
        }
        status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {**files, "bizflow_api_flow_kg_export_status": str(status_path)}
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
        return {"bizflow_api_flow_kg_export_status": str(status_path)}


def _write_markdown(path: Path, kb: Dict[str, Any]) -> str:
    lines = [
        "# BizFlow KB Summary",
        "",
        f"Run ID: `{kb.get('run_id')}`",
        f"URL: `{kb.get('url')}`",
        "",
        "## Counts",
    ]
    for k, v in (kb.get("counts") or {}).items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Add form controls", "| Label | Required | Selector |", "|---|---:|---|"]
    for c in kb.get("form", {}).get("controls", [])[:100]:
        lines.append(f"| {c.get('label','')} | {c.get('required','')} | `{c.get('selector','')}` |")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _write_api_flow_kg(kb_dir: Path, kb: Dict[str, Any]) -> Dict[str, str]:
    nodes = [
        {"id": "page:bizflows", "type": "page", "label": "BizFlows"},
        {"id": "form:add_bizflow", "type": "form", "label": "Add BizFlow"},
        {"id": "inventory:old_bizflows", "type": "inventory", "label": f"Old BizFlows ({kb.get('counts',{}).get('old_bizflows',0)})"},
    ]
    edges = [
        {"source": "page:bizflows", "target": "inventory:old_bizflows", "relation": "LISTS"},
        {"source": "page:bizflows", "target": "form:add_bizflow", "relation": "OPENS_ADD_FORM"},
    ]
    for i, api in enumerate((kb.get("api_interactions") or [])[:80], 1):
        nid = f"api:{i}"
        nodes.append({"id": nid, "type": "api", "label": f"{api.get('method')} {api.get('status')}"})
        edges.append({"source": "page:bizflows", "target": nid, "relation": "OBSERVED_NETWORK_EVENT", "url": api.get("url")})
    graph = {"run_id": kb.get("run_id"), "url": kb.get("url"), "nodes": nodes, "edges": edges, "summary": kb.get("counts", {})}
    json_path = kb_dir / "bizflow_api_flow_knowledge_graph.json"
    _write_json(json_path, graph)
    mmd = ["graph TD"]
    for e in edges[:80]:
        mmd.append(f"  {re.sub('[^A-Za-z0-9_]', '_', e['source'])} -->|{e['relation']}| {re.sub('[^A-Za-z0-9_]', '_', e['target'])}")
    mmd_path = kb_dir / "bizflow_api_flow_knowledge_graph.mmd"
    mmd_path.write_text("\n".join(mmd), encoding="utf-8")
    md_path = kb_dir / "bizflow_api_flow_knowledge_graph.md"
    md_path.write_text("# BizFlow API Flow Knowledge Graph\n\n```mermaid\n" + "\n".join(mmd) + "\n```\n", encoding="utf-8")
    html_path = kb_dir / "bizflow_api_flow_knowledge_graph.html"
    html_path.write_text(f"<html><body><h1>BizFlow API Flow KG</h1><pre>{html.escape(json.dumps(graph, indent=2, ensure_ascii=False, default=str))}</pre></body></html>", encoding="utf-8")
    return {"bizflow_api_flow_kg_json": str(json_path), "bizflow_api_flow_kg_mermaid": str(mmd_path), "bizflow_api_flow_kg_markdown": str(md_path), "bizflow_api_flow_kg_html": str(html_path)}


async def _write_compact_browser_summary(browser: BrowserSession, kb_dir: Path) -> None:
    def asdict_or_dict(x):
        if hasattr(x, "__dict__"):
            return dict(x.__dict__)
        return dict(x)
    actions = [mask_sensitive_data(asdict_or_dict(a)) for a in browser.action_events[-200:]]
    clicks = [mask_sensitive_data(asdict_or_dict(c)) for c in browser.click_events[-200:]]
    net = [mask_sensitive_data(_event_dict(n)) for n in browser.network_tab_events[-250:]]
    _write_json(kb_dir / "compact_action_sequence.json", actions)
    _write_json(kb_dir / "compact_click_sequence.json", clicks)
    _write_json(kb_dir / "compact_network_summary.json", net)


def _zip_summary(run_dir: Path, kb_dir: Path) -> str:
    zip_path = run_dir / "UPLOAD_BIZFLOW_KB_SUMMARY.zip"
    include = [
        run_dir / "bizflow_kb_summary.json",
        kb_dir / "bizflow_progress.json",
        kb_dir / "bizflow_progress.jsonl",
        kb_dir / "bizflow_form_kb.json",
        kb_dir / "bizflow_tab_form_kb.json",
        kb_dir / "old_bizflows_inventory.json",
        kb_dir / "old_bizflows_inventory.csv",
        kb_dir / "old_bizflows_deep_profiles.json",
        kb_dir / "bizflow_deep_profile_report.json",
        kb_dir / "bizflow_runtime_deployment_profiles.json",
        kb_dir / "bizflow_runtime_deployment_report.json",
        kb_dir / "bizflow_form_controls.csv",
        kb_dir / "bizflow_dropdowns.json",
        kb_dir / "bizflow_required_fields.json",
        kb_dir / "bizflow_dummy_fill_plan.json",
        kb_dir / "bizflow_dom_events.json",
        kb_dir / "BIZFLOW_KB_SUMMARY.md",
        kb_dir / "compact_action_sequence.json",
        kb_dir / "compact_click_sequence.json",
        kb_dir / "compact_network_summary.json",
        kb_dir / "bizflow_api_flow_knowledge_graph.json",
        kb_dir / "bizflow_api_flow_knowledge_graph.mmd",
        kb_dir / "bizflow_api_flow_knowledge_graph.md",
        kb_dir / "bizflow_api_flow_knowledge_graph.html",
    ]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in include:
            if p.exists():
                z.write(p, arcname=str(p.relative_to(run_dir)))
    return str(zip_path)


class BizFlowKBFlow:
    def __init__(self, config: AppConfig, *, bizflows_url: str = BIZFLOWS_URL, fill_dummy: bool = True, write_heavy_evidence: bool = False, crawl_old_bizflows: bool = True, max_api_pages: int = 250, max_detail_rows: int | None = None, capture_deep_profiles: bool = True, max_deep_profile_rows: int | None = None, form_only: bool = False, capture_runtime_details: bool = True):
        self.config = config
        self.bizflows_url = bizflows_url
        self.fill_dummy = fill_dummy
        self.write_heavy_evidence = write_heavy_evidence
        self.crawl_old_bizflows = crawl_old_bizflows
        self.max_api_pages = max_api_pages
        self.max_detail_rows = max_detail_rows
        self.capture_deep_profiles = capture_deep_profiles
        self.max_deep_profile_rows = max_deep_profile_rows
        self.form_only = form_only
        self.capture_runtime_details = capture_runtime_details

    async def run(self, ctx: RunContext, input_json: str | None = None, *, browser_session: BrowserSession | None = None) -> Dict[str, Any]:
        input_data: Dict[str, Any] = {}
        if input_json:
            p = Path(input_json)
            if p.exists():
                input_data = json.loads(p.read_text(encoding="utf-8"))
        run_dir = ctx.run_dir
        kb_dir = run_dir / "bizflow_kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        warnings: List[str] = []
        files: Dict[str, str] = {}

        def progress(phase: str, completed: int, total: int, detail: str = "", counts: Optional[Dict[str, Any]] = None):
            width = 28
            pct = completed / max(1, total)
            filled = int(width * pct)
            print(f"[{'█'*filled}{'░'*(width-filled)}] {completed}/{total} {round(pct*100,2)}% | {phase} | {detail}")
            _write_progress(kb_dir, phase=phase, completed=completed, total=total, detail=detail, counts=counts)

        # BizFlow definition/list payload is large (~249 rows with nested fields).
        # Keep enough body text in memory so IDs/deep profiles are extracted from the real gateway API.
        try:
            self.config.extraction.max_network_body_chars = max(int(getattr(self.config.extraction, "max_network_body_chars", 0) or 0), 5_000_000)
        except Exception:
            pass
        progress("initializing", 0, 8, "Preparing browser, SSO, BizFlow API capture and output folders")
        old_bizflows: List[Dict[str, Any]] = []
        deep_profiles: List[Dict[str, Any]] = []
        deep_report: Dict[str, Any] = {}
        runtime_profiles: List[Dict[str, Any]] = []
        runtime_report: Dict[str, Any] = {}
        api_interactions: List[Dict[str, Any]] = []
        listing_ui_rows: List[Dict[str, Any]] = []
        controls: List[Dict[str, Any]] = []
        dropdowns: List[Dict[str, Any]] = []
        required_fields: List[Dict[str, Any]] = []
        buttons: List[Dict[str, Any]] = []
        fill_attempts: List[Dict[str, Any]] = []
        tab_form_kb: Dict[str, Any] = {}

        async with browser_session_scope(self.config, run_dir, existing=browser_session, phase_name=run_dir.name) as browser:
            progress("login", 1, 8, "Opening browser and waiting for Dell SSO/BizFlows page")
            self.config.portal.base_url = self.bizflows_url
            await browser.goto_base_and_complete_sso(self.bizflows_url)
            page = browser.page
            if not page:
                raise RuntimeError("Browser page not available")
            browser.set_stage("open_bizflows")
            progress("open_bizflows", 2, 8, "Opening BizFlows link and waiting for page/API readiness")
            await browser.navigate(self.bizflows_url)
            await page.wait_for_timeout(2500)
            nav_audit = await _ensure_bizflows_listing_page(page, self.bizflows_url)
            files["bizflow_navigation_audit_json"] = _write_json(kb_dir / "bizflow_navigation_audit.json", nav_audit)
            ready = bool(nav_audit.get("ok"))
            if not ready:
                warnings.append("BizFlow listing did not open from direct URL/left-nav recovery; refusing to capture HIP Home as a BizFlow form.")
            progress("bizflow_page_ready", 3, 8, f"BizFlows ready={ready}; capturing listing/API evidence")

            await browser.collect_dom_click_log()
            direct_rows_from_api, direct_api_interactions = await fetch_bizflow_definition_list_from_api(page, progress_cb=progress)
            api_interactions, rows_from_api = collect_bizflow_api_interactions([*browser.network_tab_events, *browser.network_records], stage_label="bizflow_listing")
            api_interactions = [*direct_api_interactions, *api_interactions]
            if not self.form_only and self.crawl_old_bizflows:
                browser.set_stage("bizflow_ui_inventory")
                listing_ui_rows = await crawl_bizflow_inventory_from_ui(page, max_pages=self.max_api_pages, progress_cb=progress)
                old_bizflows = _dedupe_bizflow_records([*direct_rows_from_api, *rows_from_api, *listing_ui_rows])
            else:
                listing_ui_rows = await _collect_visible_bizflow_rows(page)
                old_bizflows = _dedupe_bizflow_records([*direct_rows_from_api, *rows_from_api, *listing_ui_rows])

            if not self.form_only and self.capture_deep_profiles and old_bizflows:
                browser.set_stage("bizflow_deep_profiles")
                deep_profiles, deep_report = await enrich_deep_profiles(page, old_bizflows, max_rows=self.max_deep_profile_rows, progress_cb=progress)
            else:
                deep_profiles = []
                deep_report = {"total_inventory_rows": len(old_bizflows), "attempted": 0, "captured": 0, "missing": len(old_bizflows), "capture_percent": 0}

            # Capture the Add/Create BizFlow form BEFORE runtime deployment capture.
            # Runtime capture opens/navigates detail/deployment surfaces and can leave the UI
            # in a state where the + Add button is not visible. Capturing the form here
            # preserves the successful multi-tab form learning while runtime APIs are still
            # captured later for every BizFlow.
            runtime_profiles = []
            runtime_report = {"total_inventory_rows": len(old_bizflows), "attempted": 0, "captured": 0, "missing": len(old_bizflows), "capture_percent": 0}

            # Return to list before Add form, because pagination/detail attempts may move the page.
            browser.set_stage("find_add_button")
            progress("find_add_button", 4, 8, "Looking for + Add button after inventory capture")
            try:
                nav_audit2 = await _ensure_bizflows_listing_page(page, self.bizflows_url)
                files["bizflow_pre_add_navigation_audit_json"] = _write_json(kb_dir / "bizflow_pre_add_navigation_audit.json", nav_audit2)
            except Exception:
                pass
            add = await _find_bizflow_add_button(page)
            if add:
                browser.set_stage("click_add_button")
                progress("click_add_button", 5, 8, "Clicking + Add safely; will not save/create")
                await browser.click_and_wait(action="structural_opener click_add_bizflow", locator=add, selector="+ Add BizFlow")
                await page.wait_for_timeout(2000)
            else:
                warnings.append("Could not find + Add button on BizFlows page; form KB may be empty.")

            # BizFlow has a mandatory two-hop structural entry:
            # listing -> + Add -> template/card link/action -> multi-tab form.
            # Recover this autonomously before any tab filling; never let the
            # section judge run against the template picker or listing page.
            entry_audit = await ensure_phase_form_entry(
                page=page, browser=browser, phase="biz_flow", listing_url=self.bizflows_url,
                find_add=_find_bizflow_add_button, is_form_open=_is_bizflow_form_surface,
                after_add=_click_bizflow_template_link_after_add,
                is_intermediate_surface=_is_bizflow_template_picker_surface,
                evidence_dir=kb_dir, max_steps=5, settle_ms=1100,
                require_same_route=True,
            )
            files["phase_form_entry_react_json"] = str(kb_dir / "phase_form_entry_react.json")
            browser.set_stage("capture_add_form")
            progress("capture_add_form", 6, 8, "Capturing Add BizFlow multi-tab form, Add-link flow, tabs and Configure Routing + Add")
            form_input_data = dict(input_data or {})
            if old_bizflows:
                # Use existing references only to reveal dependent tabs/dropdowns.
                # The new flow name/description remain dummy and no mutating button is clicked.
                form_input_data["_kb_reference_bizflow"] = old_bizflows[0]
            tab_form_kb = await capture_and_fill_bizflow_multitab_form(page, form_input_data, fill_dummy=self.fill_dummy, judge_output_dir=kb_dir, config=self.config)
            controls = tab_form_kb.get("controls", [])
            buttons = tab_form_kb.get("buttons", [])
            dropdowns = tab_form_kb.get("dropdowns", [])
            previous_values_for_options = build_previous_interaction_values(form_input_data)
            dropdowns = _enrich_bizflow_dropdown_options(
                dropdowns,
                old_bizflows=old_bizflows,
                deep_profiles=deep_profiles,
                runtime_profiles=runtime_profiles,
                previous_values=previous_values_for_options,
            )
            tab_form_kb["dropdowns"] = dropdowns
            required_fields = tab_form_kb.get("required_fields", [])
            fill_attempts = tab_form_kb.get("dummy_fill_attempts", [])
            dummy_values = tab_form_kb.get("dummy_values", build_dummy_fill_values(input_data))
            if self.fill_dummy:
                browser.set_stage("fill_dummy_no_save")
                progress("fill_dummy_no_save", min(len(controls), len(fill_attempts)), max(1, len(controls)), f"Multi-tab dummy-fill attempts={len(fill_attempts)}; no save/create")
                # Keep BizFlow consistent with the other phases: after the last
                # safe dummy-fill action, capture a DOM snapshot and screenshot of
                # the visible wizard/form.  This artifact is used by the full
                # end-to-end verifier and optional vision model review.
                try:
                    await browser.save_dom_snapshot("bizflow_add_form_after_dummy_fill_no_save")
                    await browser.screenshot(kb_dir / "bizflow_add_form_after_dummy_fill_no_save.png", full_page=True)
                except Exception as exc:
                    warnings.append(f"Could not capture BizFlow filled-form screenshot: {exc}")
            if tab_form_kb.get("audit", {}).get("warnings"):
                warnings.extend(tab_form_kb.get("audit", {}).get("warnings", []))

            # Now capture runtime/deployment details. This runs after form capture so
            # expanded view/deployment navigation cannot prevent + Add discovery.
            if not self.form_only and self.capture_runtime_details and old_bizflows:
                browser.set_stage("bizflow_runtime_deployment_profiles")
                runtime_profiles, runtime_report = await capture_runtime_deployment_profiles(
                    page, browser, old_bizflows, bizflows_url=self.bizflows_url, max_rows=self.max_deep_profile_rows, progress_cb=progress
                )
            else:
                runtime_profiles = []
                runtime_report = {"total_inventory_rows": len(old_bizflows), "attempted": 0, "captured": 0, "missing": len(old_bizflows), "capture_percent": 0}

            await browser.collect_dom_click_log()
            await _write_compact_browser_summary(browser, kb_dir)
            if self.write_heavy_evidence:
                await browser.flush_logs()

        progress("summarize_and_write_outputs", 7, 8, "Writing compact KB, checkpoints and upload zip")
        counts = {
            "old_bizflows": len(old_bizflows),
            "old_bizflows_with_numeric_id": sum(1 for r in old_bizflows if str(r.get("bizflow_id") or "").isdigit()),
            "api_interactions": len(api_interactions),
            "form_controls": len(controls),
            "required_fields": len(required_fields),
            "dropdowns": len(dropdowns),
            "deep_profiles_captured": sum(1 for p in deep_profiles if p.get("status") == "captured"),
            "runtime_profiles_captured": sum(1 for p in runtime_profiles if p.get("status") == "captured"),
        }
        kb = {
            "run_id": ctx.run_id,
            "url": self.bizflows_url,
            "generated_at": utc_now(),
            "counts": counts,
            "previous_values": build_previous_interaction_values(input_data),
            "inventory": old_bizflows,
            "deep_profiles": deep_profiles,
            "deep_profile_report": deep_report,
            "runtime_profiles": runtime_profiles,
            "runtime_report": runtime_report,
            "form": {"controls": controls, "dropdowns": dropdowns, "required_fields": required_fields, "buttons": buttons},
            "bizflow_tab_form": tab_form_kb,
            "dummy_fill": {"values": build_dummy_fill_values(input_data), "attempts": fill_attempts, "safety": "Save/Create/Submit/Delete not clicked"},
            "repeatable_section_plan": tab_form_kb.get("repeatable_section_plan", []),
            "repeatable_row_audit": tab_form_kb.get("repeatable_row_audit", []),
            "portal_form_exploration": tab_form_kb.get("portal_form_exploration", {}),
            "stateful_target_branch_graph": tab_form_kb.get("stateful_target_branch_graph", {}),
            "stateful_target_branch_execution": tab_form_kb.get("stateful_target_branch_execution", {}),
            "stateful_target_branch_executions": tab_form_kb.get("stateful_target_branch_executions", []),
            "target_branch_knowledge": tab_form_kb.get("target_branch_knowledge", {}),
            "api_interactions": api_interactions,
            "warnings": warnings,
        }
        files["bizflow_form_kb_json"] = _write_json(kb_dir / "bizflow_form_kb.json", kb)
        files["bizflow_tab_form_kb_json"] = _write_json(kb_dir / "bizflow_tab_form_kb.json", tab_form_kb)
        files["old_bizflows_inventory_json"] = _write_json(kb_dir / "old_bizflows_inventory.json", old_bizflows)
        files["old_bizflows_inventory_csv"] = _write_inventory_csv(kb_dir / "old_bizflows_inventory.csv", old_bizflows)
        files["listing_ui_rows_json"] = _write_json(kb_dir / "bizflow_listing_ui_rows.json", listing_ui_rows)
        files["old_bizflows_deep_profiles_json"] = _write_json(kb_dir / "old_bizflows_deep_profiles.json", deep_profiles)
        files["bizflow_deep_profile_report_json"] = _write_json(kb_dir / "bizflow_deep_profile_report.json", deep_report)
        files["bizflow_api_interactions_json"] = _write_json(kb_dir / "bizflow_api_interactions.json", api_interactions)
        files["bizflow_runtime_profiles_json"] = _write_json(kb_dir / "bizflow_runtime_deployment_profiles.json", runtime_profiles)
        files["bizflow_runtime_report_json"] = _write_json(kb_dir / "bizflow_runtime_deployment_report.json", runtime_report)
        files["previous_values_json"] = _write_json(kb_dir / "bizflow_previous_interaction_values.json", build_previous_interaction_values(input_data))
        files["dropdowns_json"] = _write_json(kb_dir / "bizflow_dropdowns.json", dropdowns)
        files["required_fields_json"] = _write_json(kb_dir / "bizflow_required_fields.json", required_fields)
        files["dummy_fill_plan_json"] = _write_json(kb_dir / "bizflow_dummy_fill_plan.json", {"values": build_dummy_fill_values(input_data), "attempts": fill_attempts})
        files["dom_events_json"] = _write_json(kb_dir / "bizflow_dom_events.json", _build_dom_event_kb(controls, dropdowns, buttons))
        files["csv"] = _write_controls_csv(kb_dir / "bizflow_form_controls.csv", controls)
        files["markdown"] = _write_markdown(kb_dir / "BIZFLOW_KB_SUMMARY.md", kb)
        files.update(_write_api_flow_kg_safe(kb_dir, kb))
        result = BizFlowKBResult(run_id=ctx.run_id, run_dir=str(run_dir), kb_dir=str(kb_dir), status="completed", counts=counts, files=files, warnings=warnings)
        (run_dir / "bizflow_kb_summary.json").write_text(json.dumps(result.__dict__, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        files["upload_zip"] = _zip_summary(run_dir, kb_dir)
        result.files = files
        (run_dir / "bizflow_kb_summary.json").write_text(json.dumps(result.__dict__, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        progress("completed", 8, 8, "BizFlow KB summary zip created", counts=counts)
        return result.__dict__
