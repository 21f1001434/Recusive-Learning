"""Maximum-observability evidence for autonomous HIP form execution.

The collector is intentionally read-only.  It captures enough structural state to
explain parent/child mounting, Angular rerenders, DDS ownership, row identity,
validation, network/resource timing and user-interface events without storing
credentials or authorisation values.  Customer values remain sourced from the
current input JSON; reusable memory stores only topology and interaction facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment

from .models import utc_now
from .safe_io import safe_write_json
from .security import is_secret_target, mask_sensitive_data, mask_sensitive_string
from .website_understanding import WebsiteUnderstandingEngine


_MUTATING_WORDS = {
    "save", "create", "submit", "delete", "deploy", "publish", "update",
    "remove", "enable", "disable", "confirm", "archive", "reset",
}


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _strip_query(url: str) -> str:
    try:
        parts = urlsplit(str(url or ""))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return str(url or "").split("?", 1)[0]


def flatten_input_paths(value: Any, prefix: str = "$") -> Dict[str, Any]:
    """Return scalar/empty-container paths while excluding runtime metadata.

    This is used only for coverage accounting.  Secret-bearing paths are retained
    as path names but their values are replaced, so the report can still prove
    that a provided input was mapped without exposing the input itself.
    """
    out: Dict[str, Any] = {}
    if isinstance(value, Mapping):
        if not value:
            out[prefix] = {}
            return out
        for key, child in value.items():
            key_text = str(key)
            if key_text.startswith("_mission_") or key_text.startswith("_runtime_"):
                continue
            child_prefix = f"{prefix}.{key_text}"
            if is_secret_target(key_text):
                out[child_prefix] = "***MASKED***"
            else:
                out.update(flatten_input_paths(child, child_prefix))
        return out
    if isinstance(value, list):
        if not value:
            out[prefix] = []
            return out
        for index, child in enumerate(value):
            out.update(flatten_input_paths(child, f"{prefix}[{index}]"))
        return out
    out[prefix] = mask_sensitive_data(value)
    return out


def _phase_scoped_input_leaves(
    *,
    phase: str,
    input_payload: Mapping[str, Any] | None,
    state_graph: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    payload = input_payload if isinstance(input_payload, Mapping) else {}
    graph = state_graph if isinstance(state_graph, Mapping) else {}
    object_path = str(graph.get("object_path") or f"$.objects.{phase}")
    objects = payload.get("objects") if isinstance(payload.get("objects"), Mapping) else {}
    phase_key = object_path.rsplit(".", 1)[-1] if object_path.startswith("$.objects.") else str(phase)
    obj = objects.get(phase_key) if isinstance(objects.get(phase_key), Mapping) else None
    if obj is None and isinstance(objects.get(str(phase)), Mapping):
        phase_key = str(phase)
        obj = objects.get(phase_key)
        object_path = f"$.objects.{phase_key}"
    if obj is None:
        return {}
    return flatten_input_paths(obj, object_path)


def build_input_coverage_contract(
    *,
    phase: str,
    input_payload: Mapping[str, Any] | None,
    state_graph: Mapping[str, Any] | None,
    verification: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a fail-closed, *complete* phase input-to-form contract.

    Earlier revisions only proved that every required graph node had an input
    path.  That could still miss a real input.json field entirely.  This version
    scopes the input to the current phase and requires every scalar leaf to be
    either bound to an executable/verification node or explicitly classified as
    non-mutable structural/display evidence in ``input_accounting``.
    """
    graph = dict(state_graph or {})
    nodes = [dict(x) for x in graph.get("nodes", []) if isinstance(x, Mapping)]
    actionable = {
        "fill_text", "select_single", "select_multi", "toggle", "upload", "upload_file",
        "select_radio", "fill_number", "fill_date", "repeatable_row",
    }
    required_nodes = [
        node for node in nodes
        if bool(node.get("required", True)) and _norm(node.get("action")) in actionable
    ]
    missing_input_path = [
        {
            "node_id": node.get("node_id"),
            "field_key": node.get("field_key"),
            "section": node.get("section"),
            "row_kind": node.get("row_kind"),
            "row_index": node.get("row_index"),
        }
        for node in required_nodes if not str(node.get("input_path") or "").strip()
    ]
    mapped: Dict[str, list[str]] = {}
    for node in nodes:
        path = str(node.get("input_path") or "").strip()
        if path:
            mapped.setdefault(path, []).append(str(node.get("node_id") or node.get("field_key") or ""))
    duplicate_bindings = {path: node_ids for path, node_ids in mapped.items() if len(set(node_ids)) > 1}

    accounting_rows = [dict(x) for x in graph.get("input_accounting", []) if isinstance(x, Mapping)]
    accounted: Dict[str, Dict[str, Any]] = {}
    for row in accounting_rows:
        path = str(row.get("input_path") or "").strip()
        if path:
            accounted[path] = row

    input_leaves = _phase_scoped_input_leaves(phase=phase, input_payload=input_payload, state_graph=graph)
    mapped_paths = set(mapped)
    accounted_paths = set(accounted)
    leaves = set(input_leaves)
    covered = leaves & (mapped_paths | accounted_paths)
    unmapped = sorted(leaves - mapped_paths - accounted_paths)
    stale_accounting = sorted((mapped_paths | accounted_paths) - leaves)

    graph_contract = graph.get("dependency_execution_contract") if isinstance(graph.get("dependency_execution_contract"), Mapping) else {}
    graph_pass = bool(graph_contract.get("pass", True))
    verification_status = str((verification or {}).get("status") or "").lower()
    verification_pass = verification_status not in {"failed", "fail", "blocked", "error"}
    required_mapping_pass = not missing_input_path
    complete_phase_input_pass = not unmapped
    contract_pass = bool(graph_pass and required_mapping_pass and complete_phase_input_pass and verification_pass)
    return mask_sensitive_data({
        "schema_version": "hip.input-control-coverage.v2",
        "phase": phase,
        "pass": contract_pass,
        "graph_pass": graph_pass,
        "verification_pass": verification_pass,
        "required_mapping_pass": required_mapping_pass,
        "complete_phase_input_pass": complete_phase_input_pass,
        "node_count": len(nodes),
        "required_actionable_node_count": len(required_nodes),
        "mapped_input_path_count": len(mapped_paths),
        "accounted_nonmutable_path_count": len(accounted_paths),
        "input_leaf_count": len(leaves),
        "exact_leaf_path_matches": len(covered),
        "exact_leaf_coverage_percent": round((100.0 * len(covered) / len(leaves)), 2) if leaves else 100.0,
        "missing_required_input_paths": missing_input_path,
        "duplicate_input_path_bindings": duplicate_bindings,
        "mapped_input_paths": sorted(mapped_paths),
        "accounted_input_paths": sorted(accounted_paths),
        "input_accounting": accounting_rows,
        "unmapped_input_leaf_paths": unmapped[:500],
        "stale_or_nonphase_mappings": stale_accounting[:500],
        "note": "Pass requires 100% accounting of the current phase input leaves: executable/verification node or explicit non-mutable structural/display disposition.",
        "values_stored": False,
    })


def sanitize_dom_structure(html: str, max_chars: int = 2_000_000) -> str:
    """Create a structural, upload-safe DOM snapshot.

    Scripts/styles and credential-bearing attributes are removed. Form values are
    replaced with presence/length markers, while labels, Angular/DDS attributes,
    ARIA ownership and hierarchy are retained for debugging.
    """
    soup = BeautifulSoup(str(html or ""), "html.parser")
    for node in soup.find_all(["script", "style", "noscript", "iframe"]):
        node.decompose()
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    for tag in soup.find_all(True):
        attrs = dict(tag.attrs or {})
        for name, raw in attrs.items():
            lname = str(name).lower()
            if is_secret_target(lname) or lname in {"srcdoc", "integrity", "nonce"}:
                tag.attrs.pop(name, None)
                continue
            if lname in {"href", "src", "action", "formaction"}:
                if isinstance(raw, str):
                    tag.attrs[name] = _strip_query(raw)
                continue
            if lname == "value":
                text = str(raw or "")
                tag.attrs[name] = f"***VALUE_REDACTED_LEN_{len(text)}***" if text else ""
        if tag.name == "textarea" and tag.string:
            length = len(str(tag.string))
            tag.string.replace_with(f"***VALUE_REDACTED_LEN_{length}***")
    text = mask_sensitive_string(str(soup))
    if len(text) > int(max_chars):
        text = text[: int(max_chars)] + "\n<!-- HIP DOM SNAPSHOT TRUNCATED -->"
    return text


def build_replay_readiness_report(
    *,
    phase: str,
    coverage: Mapping[str, Any],
    dependency_contract: Mapping[str, Any] | None,
    verification: Mapping[str, Any] | None,
    evidence_channels: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    dependency = dict(dependency_contract or {})
    evidence = dict(evidence_channels or {})
    checks = {
        "input_control_coverage": bool(coverage.get("pass")),
        "acyclic_dependency_contract": bool(dependency.get("pass", True)) and not bool(dependency.get("cycle_node_ids")),
        "ordered_dependency_nodes_present": bool(dependency.get("ordered_node_ids")) or not dependency,
        "deterministic_verification": str((verification or {}).get("status") or "").lower() not in {"failed", "fail", "blocked", "error"},
        "local_browser_evidence": bool(evidence.get("local_playwright", {}).get("available", True)),
    }
    optional_channels = ["playwright_mcp", "chrome_devtools_mcp"]
    optional_available = sum(bool(evidence.get(name, {}).get("available")) for name in optional_channels)
    score = sum(1 for passed in checks.values() if passed) / max(1, len(checks))
    score = min(1.0, score + (0.05 * optional_available))
    pass_value = all(checks.values())
    return {
        "schema_version": "hip.deterministic-replay-readiness.v1",
        "phase": phase,
        "pass": pass_value,
        "confidence": round(score, 3),
        "checks": checks,
        "optional_mcp_channels_available": optional_available,
        "replay_policy": "exploit only when pass=true; otherwise explore the earliest unresolved dependency with fresh evidence",
        "values_stored": False,
    }


class MaximumObservabilityCollector:
    """Read-only structural evidence collector used on attempts and successes."""

    def __init__(self, *, config: Any, root_dir: str | Path, browser: Any) -> None:
        self.config = config
        self.policy = getattr(config, "portal_learning", None)
        self.root_dir = Path(root_dir)
        self.browser = browser
        self.enabled = bool(getattr(self.policy, "maximum_observability_enabled", True))
        self.max_controls = max(500, int(getattr(self.policy, "maximum_observability_max_controls", 5000)))
        self.max_events = max(100, int(getattr(self.policy, "maximum_observability_max_events", 2500)))
        self.max_dom_chars = max(100_000, int(getattr(self.policy, "maximum_observability_max_dom_chars", 2_000_000)))
        self.capture_dom = bool(getattr(self.policy, "capture_sanitized_dom_structure", True))
        self.website_understanding = WebsiteUnderstandingEngine(config=config, browser=browser)

    async def install_observers(self, page: Any, *, phase: str, attempt: int) -> Dict[str, Any]:
        if not self.enabled or page is None:
            return {"status": "disabled"}
        result = await page.evaluate(
            r"""({phase, attempt, maxEvents}) => {
              const KEY = '__hipMaximumObservability';
              const safeText = value => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 300);
              const semantic = el => {
                if (!el) return {};
                const by = el.getAttribute?.('aria-labelledby');
                const byText = by ? by.split(/\s+/).map(id => safeText(document.getElementById(id)?.textContent)).filter(Boolean).join(' ') : '';
                const label = el.getAttribute?.('aria-label') || byText || (el.id ? safeText(document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.textContent) : '') || safeText(el.closest?.('label')?.textContent);
                const host = el.closest?.('[formcontrolname], dds-dropdown, dds-form-field, [cdkdrag], fieldset');
                return {
                  tag: el.tagName?.toLowerCase() || '', role: el.getAttribute?.('role') || '', type: el.getAttribute?.('type') || '',
                  label: safeText(label), name: el.getAttribute?.('name') || '', formControlName: el.getAttribute?.('formcontrolname') || host?.getAttribute?.('formcontrolname') || '',
                  expanded: el.getAttribute?.('aria-expanded') || '', checked: !!el.checked,
                  hasValue: typeof el.value === 'string' ? el.value.length > 0 : false,
                  valueLength: typeof el.value === 'string' ? el.value.length : 0
                };
              };
              if (window[KEY]?.observer) { try { window[KEY].observer.disconnect(); } catch (_) {} }
              const state = {phase, attempt, installedAt: Date.now(), mutations: [], events: [], maxEvents};
              const push = (bucket, row) => { bucket.push(row); if (bucket.length > maxEvents) bucket.splice(0, bucket.length - maxEvents); };
              state.observer = new MutationObserver(rows => {
                for (const row of rows) {
                  const target = row.target?.nodeType === 1 ? row.target : row.target?.parentElement;
                  push(state.mutations, {
                    at: Date.now(), type: row.type, attribute: row.attributeName || '', target: semantic(target),
                    added: row.addedNodes?.length || 0, removed: row.removedNodes?.length || 0
                  });
                }
              });
              state.observer.observe(document.documentElement, {subtree:true, childList:true, attributes:true, attributeFilter:['class','hidden','disabled','aria-expanded','aria-selected','aria-invalid','aria-busy','value']});
              for (const type of ['click','input','change','blur','focus','keydown','pointerdown']) {
                document.addEventListener(type, event => {
                  push(state.events, {at:Date.now(), type, key:type === 'keydown' ? safeText(event.key) : '', target:semantic(event.target)});
                }, true);
              }
              window[KEY] = state;
              return {status:'installed', phase, attempt, maxEvents};
            }""",
            {"phase": phase, "attempt": int(attempt), "maxEvents": self.max_events},
        )
        return mask_sensitive_data(result)

    async def capture(
        self,
        *,
        page: Any,
        phase: str,
        stage: str,
        output_dir: Path,
        input_payload: Mapping[str, Any] | None = None,
        state_graph: Mapping[str, Any] | None = None,
        verification: Mapping[str, Any] | None = None,
        evidence_channels: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if not self.enabled or page is None:
            return {"status": "disabled"}
        output_dir.mkdir(parents=True, exist_ok=True)
        snapshot = await page.evaluate(
            r"""({maxControls, maxEvents}) => {
              const text = el => String(el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
              const visible = el => {
                if (!el || !el.isConnected) return false;
                const s = getComputedStyle(el), r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0 && r.width > 0 && r.height > 0;
              };
              const labelFor = el => {
                const aria = el.getAttribute?.('aria-label'); if (aria) return aria.trim();
                const by = el.getAttribute?.('aria-labelledby');
                if (by) { const t = by.split(/\s+/).map(id => text(document.getElementById(id))).filter(Boolean).join(' '); if (t) return t; }
                if (el.id) { const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (lab && text(lab)) return text(lab); }
                const field = el.closest?.('dds-form-field,.dds__form-group,.form-group,fieldset,[role="group"],[cdkdrag]');
                const lab = field?.querySelector?.('label,legend,.dds__label,[class*="label"]');
                return text(lab);
              };
              const cssHint = el => {
                const fc = el.getAttribute?.('formcontrolname'); if (fc) return `[formcontrolname="${CSS.escape(fc)}"]`;
                const name = el.getAttribute?.('name'); if (name) return `[name="${CSS.escape(name)}"]`;
                return el.id ? `#${CSS.escape(el.id)}` : el.tagName?.toLowerCase() || '';
              };
              const ancestorPath = el => {
                const out = []; let cur = el;
                for (let i=0; cur && i<8; i++, cur=cur.parentElement) {
                  out.push({tag:cur.tagName?.toLowerCase()||'', id:cur.id||'', class:String(cur.className||'').slice(0,300), role:cur.getAttribute?.('role')||'', formControlName:cur.getAttribute?.('formcontrolname')||'', text:text(cur).slice(0,180)});
                }
                return out;
              };
              const all = [...document.querySelectorAll('input,textarea,select,button,[role="combobox"],[role="checkbox"],[role="radio"],[role="switch"],[contenteditable="true"],dds-dropdown,dds-switch,dds-checkbox,dds-radio-button,dds-button')].slice(0,maxControls);
              const controls = all.map((el,index) => {
                const r=el.getBoundingClientRect(), host=el.closest?.('dds-dropdown,dds-form-field,dds-input,dds-switch,[formcontrolname]');
                const owned=(el.getAttribute?.('aria-controls')||el.getAttribute?.('aria-owns')||'').split(/\s+/).filter(Boolean);
                const mountedOptions=owned.flatMap(id => [...(document.getElementById(id)?.querySelectorAll?.('[role="option"],option')||[])]).slice(0,500).map(opt=>({text:text(opt).slice(0,500),selected:opt.getAttribute('aria-selected')==='true'||!!opt.selected,disabled:opt.getAttribute('aria-disabled')==='true'||!!opt.disabled,pos:opt.getAttribute('aria-posinset')||''}));
                return {
                  index, visible:visible(el), tag:el.tagName.toLowerCase(), role:el.getAttribute('role')||'', type:el.getAttribute('type')||'', id:el.id||'', name:el.getAttribute('name')||'',
                  formControlName:el.getAttribute('formcontrolname')||host?.getAttribute?.('formcontrolname')||'', label:labelFor(el), placeholder:el.getAttribute('placeholder')||'', selectorHint:cssHint(el),
                  required:!!(el.required||el.getAttribute('aria-required')==='true'), disabled:!!(el.disabled||el.getAttribute('aria-disabled')==='true'), readOnly:!!el.readOnly,
                  invalid:el.getAttribute('aria-invalid')==='true'||el.classList?.contains('ng-invalid'), pending:el.classList?.contains('ng-pending'), expanded:el.getAttribute('aria-expanded')||'', checked:!!el.checked,
                  hasValue:typeof el.value==='string' ? el.value.length>0 : false, valueLength:typeof el.value==='string'?el.value.length:0,
                  ownedIds:owned, mountedOptions, classes:String(el.className||'').slice(0,500), box:{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)},
                  section:text(el.closest?.('fieldset')?.querySelector?.('legend')).slice(0,300), rowText:text(el.closest?.('[cdkdrag],.dds__row,[formarrayname] > div')).slice(0,500), ancestors:ancestorPath(el)
                };
              });
              const forms=[...document.querySelectorAll('form')].map((form,index)=>({index,visible:visible(form),classes:String(form.className||''),nativeValid:typeof form.checkValidity==='function'?form.checkValidity():null,angular:{valid:form.classList.contains('ng-valid'),invalid:form.classList.contains('ng-invalid'),pending:form.classList.contains('ng-pending')},invalidControls:controls.filter(c=>c.invalid&&c.ancestors.some(a=>a.tag==='form'&&a.class===String(form.className||''))).map(c=>c.label||c.formControlName).slice(0,300)}));
              const sections=[...document.querySelectorAll('fieldset,[role="group"],form,.dds__drawer,[role="dialog"],dds-drawer')].slice(0,800).map((el,index)=>({index,visible:visible(el),tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',label:text(el.querySelector?.('legend,h1,h2,h3,h4,[role="heading"]')).slice(0,400),classes:String(el.className||'').slice(0,500),formArrayName:el.getAttribute('formarrayname')||'',childControlCount:el.querySelectorAll?.('input,textarea,select,[role="combobox"],[formcontrolname]').length||0}));
              const actions=[...document.querySelectorAll('button,[role="button"],a[href]')].slice(0,1500).map((el,index)=>({index,visible:visible(el),text:text(el).slice(0,400),ariaLabel:el.getAttribute('aria-label')||'',disabled:!!(el.disabled||el.getAttribute('aria-disabled')==='true'),href:el.getAttribute('href')||'',classes:String(el.className||'').slice(0,400)}));
              const resources=performance.getEntriesByType('resource').slice(-1500).map(r=>({name:String(r.name||'').split('?')[0],initiatorType:r.initiatorType||'',duration:Math.round(r.duration||0),transferSize:r.transferSize||0,decodedBodySize:r.decodedBodySize||0}));
              const nav=performance.getEntriesByType('navigation').slice(-5).map(r=>({type:r.type||'',duration:Math.round(r.duration||0),domContentLoaded:Math.round(r.domContentLoadedEventEnd||0),loadEventEnd:Math.round(r.loadEventEnd||0),transferSize:r.transferSize||0}));
              const observer=window.__hipMaximumObservability||{};
              return {
                url:location.href,title:document.title,readyState:document.readyState,capturedAt:Date.now(),viewport:{width:innerWidth,height:innerHeight,scrollX,scrollY,devicePixelRatio},
                controls,forms,sections,actions,resources,navigationTiming:nav,
                links:[...document.querySelectorAll('a[href]')].slice(0,1200).map(a=>({text:text(a).slice(0,300),href:String(a.href||'').split('?')[0],visible:visible(a)})),
                alerts:[...document.querySelectorAll('[role="alert"],[aria-live],.error,.invalid-feedback,[class*="error"],[class*="validation"]')].filter(visible).slice(0,500).map(el=>text(el).slice(0,1000)),
                popups:[...document.querySelectorAll('[role="listbox"],[role="menu"],.dds__dropdown__menu,.dds__popover,.cdk-overlay-pane')].slice(0,300).map(el=>({visible:visible(el),id:el.id||'',role:el.getAttribute('role')||'',text:text(el).slice(0,5000),classes:String(el.className||'')})),
                storageKeyNames:{localStorage:Object.keys(localStorage).sort(),sessionStorage:Object.keys(sessionStorage).sort()},storageValuesCaptured:false,
                observer:{phase:observer.phase||'',attempt:observer.attempt||0,installedAt:observer.installedAt||0,mutations:(observer.mutations||[]).slice(-maxEvents),events:(observer.events||[]).slice(-maxEvents)}
              };
            }""",
            {"maxControls": self.max_controls, "maxEvents": self.max_events},
        )
        snapshot = mask_sensitive_data(snapshot if isinstance(snapshot, Mapping) else {"raw": snapshot})
        structural = {
            "url": _strip_query(str(snapshot.get("url") or "")),
            "title": snapshot.get("title"),
            "controls": [
                {
                    "tag": c.get("tag"), "role": c.get("role"), "type": c.get("type"),
                    "formControlName": c.get("formControlName"), "label": c.get("label"),
                    "required": c.get("required"), "section": c.get("section"),
                }
                for c in snapshot.get("controls", []) if isinstance(c, Mapping)
            ],
            "sections": snapshot.get("sections") or [],
        }
        snapshot["state_fingerprint"] = _hash(structural)
        snapshot["phase"] = phase
        snapshot["stage"] = stage
        snapshot["schema_version"] = "hip.maximum-observability-page-intelligence.v1"
        page_path = output_dir / "maximum_page_intelligence.json"
        safe_write_json(page_path, snapshot)

        # V233 Stage 1: richer pre-action website model. This is deliberately
        # read-only and current-generation; it never becomes an execution selector cache.
        try:
            website_model = await self.website_understanding.capture(
                page=page, phase=phase, stage=stage, output_dir=output_dir / "website_understanding"
            )
        except Exception as exc:
            website_model = {
                "schema_version": "hip.website-understanding.v1",
                "available": False,
                "reason": "capture_exception",
                "error": mask_sensitive_string(str(exc))[:1200],
            }
        website_gate = website_model.get("understanding_gate") if isinstance(website_model, Mapping) else {}

        coverage = build_input_coverage_contract(
            phase=phase,
            input_payload=input_payload,
            state_graph=state_graph,
            verification=verification,
        )
        coverage_path = output_dir / "input_control_coverage.json"
        safe_write_json(coverage_path, coverage)

        dependency = (state_graph or {}).get("dependency_execution_contract") if isinstance(state_graph, Mapping) else {}
        readiness = build_replay_readiness_report(
            phase=phase,
            coverage=coverage,
            dependency_contract=dependency if isinstance(dependency, Mapping) else {},
            verification=verification,
            evidence_channels=evidence_channels,
        )
        readiness_path = output_dir / "deterministic_replay_readiness.json"
        safe_write_json(readiness_path, readiness)

        dom_path = ""
        if self.capture_dom:
            try:
                html = await page.content()
                sanitized = sanitize_dom_structure(html, self.max_dom_chars)
                dom_file = output_dir / "sanitized_dom_structure.html"
                dom_file.write_text(sanitized, encoding="utf-8")
                dom_path = str(dom_file)
            except Exception as exc:
                dom_path = f"error:{mask_sensitive_string(str(exc))}"

        manifest = {
            "schema_version": "hip.maximum-observability-bundle.v1",
            "phase": phase,
            "stage": stage,
            "captured_at": utc_now(),
            "page_intelligence": str(page_path),
            "input_control_coverage": str(coverage_path),
            "deterministic_replay_readiness": str(readiness_path),
            "sanitized_dom_structure": dom_path,
            "state_fingerprint": snapshot.get("state_fingerprint"),
            "control_count": len(snapshot.get("controls") or []),
            "hidden_control_count": sum(1 for c in snapshot.get("controls") or [] if isinstance(c, Mapping) and not c.get("visible")),
            "mutation_event_count": len((snapshot.get("observer") or {}).get("mutations") or []),
            "ui_event_count": len((snapshot.get("observer") or {}).get("events") or []),
            "resource_count": len(snapshot.get("resources") or []),
            "coverage_pass": coverage.get("pass"),
            "replay_ready": readiness.get("pass"),
            "read_only": True,
            "storage_values_captured": False,
            "customer_values_promoted_to_memory": False,
            "website_understanding": str(output_dir / "website_understanding" / "website_understanding.json"),
            "website_understanding_available": bool(isinstance(website_model, Mapping) and website_model.get("available")),
            "website_understanding_pass": bool(isinstance(website_gate, Mapping) and website_gate.get("pass")),
            "website_understanding_confidence": (website_gate.get("confidence") if isinstance(website_gate, Mapping) else None),
        }
        manifest_path = output_dir / "maximum_observability_manifest.json"
        safe_write_json(manifest_path, manifest)
        manifest["manifest"] = str(manifest_path)
        return manifest
