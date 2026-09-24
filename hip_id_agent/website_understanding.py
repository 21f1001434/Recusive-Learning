from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _path_only(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
        return parsed.path or "/"
    except Exception:
        return str(url or "").split("?", 1)[0]


PAGE_MODEL_JS = r"""({maxControls,maxOptions,maxEvents,inspectionPrefix}) => {
  const clean = v => String(v ?? '').replace(/\s+/g,' ').trim();
  const text = el => clean(el?.innerText || el?.textContent || '');
  const visible = el => {
    if(!el || !el.isConnected) return false;
    const s=getComputedStyle(el), r=el.getBoundingClientRect();
    return s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||1)!==0 && r.width>0 && r.height>0;
  };
  const znum = el => { const z=parseInt(getComputedStyle(el).zIndex,10); return Number.isFinite(z)?z:0; };
  const labelFor = el => {
    if(!el) return '';
    const aria=clean(el.getAttribute?.('aria-label')); if(aria) return aria;
    const by=clean(el.getAttribute?.('aria-labelledby'));
    if(by){const t=by.split(/\s+/).map(id=>text(document.getElementById(id))).filter(Boolean).join(' '); if(t)return t;}
    if(el.id){const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if(l&&text(l))return text(l);}
    const wrapped=el.closest?.('label'); if(wrapped&&text(wrapped)) return text(wrapped);
    const ownRole=clean(el.getAttribute?.('role')).toLowerCase();
    const ownTag=(el.tagName||'').toLowerCase();
    const ownTitle=clean(el.getAttribute?.('title'));
    if(ownTitle && ['button','a'].includes(ownTag)) return ownTitle;
    if(['button','a','option'].includes(ownTag) || ['button','tab','option','menuitem'].includes(ownRole)){
      const ownText=text(el); if(ownText) return ownText;
    }
    const field=el.closest?.('dds-form-field,.dds__form-group,.form-group,fieldset,[role="group"],[formarrayname],[cdkdrag]');
    const lab=field?.querySelector?.('label,legend,.dds__label,[class*="label"]');
    return text(lab);
  };
  const headingFor = el => {
    let cur=el;
    for(let i=0; cur && i<8; i++,cur=cur.parentElement){
      const h=cur.querySelector?.(':scope > legend,:scope > h1,:scope > h2,:scope > h3,:scope > h4,:scope > [role="heading"]');
      if(h&&text(h))return text(h).slice(0,300);
      const aria=clean(cur.getAttribute?.('aria-label')); if(aria && /section|details|condition|action|routing|process|document|interface|validation/i.test(aria)) return aria.slice(0,300);
    }
    return '';
  };
  const semanticPath = el => {
    const out=[]; let cur=el;
    for(let i=0;cur&&i<8;i++,cur=cur.parentElement){
      const role=clean(cur.getAttribute?.('role'));
      const fc=clean(cur.getAttribute?.('formcontrolname'));
      const fa=clean(cur.getAttribute?.('formarrayname'));
      const label=labelFor(cur);
      out.push({tag:(cur.tagName||'').toLowerCase(),role,formControlName:fc,formArrayName:fa,label:label.slice(0,180)});
    }
    return out;
  };
  const surfaceCandidates=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"],dds-drawer,.dds__drawer,[class*="drawer"],[class*="modal"],form,[role="tabpanel"],main')]
    .filter(visible).slice(0,300);
  const surfaces=surfaceCandidates.map((el,index)=>{
    const r=el.getBoundingClientRect(), s=getComputedStyle(el);
    const modal=el.getAttribute('aria-modal')==='true' || el.getAttribute('role')==='dialog';
    const drawer=el.tagName?.toLowerCase()==='dds-drawer' || /drawer/i.test(String(el.className||''));
    const fixed=['fixed','absolute'].includes(s.position);
    const area=Math.max(0,r.width*r.height);
    const viewportArea=Math.max(1,innerWidth*innerHeight);
    const score=(modal?1000:0)+(drawer?700:0)+(fixed?180:0)+Math.min(160,Math.max(0,znum(el)))+Math.min(100,area/viewportArea*100);
    return {index,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',ariaModal:el.getAttribute('aria-modal')||'',label:(labelFor(el)||text(el.querySelector?.('h1,h2,h3,h4,[role="heading"]'))).slice(0,300),classes:String(el.className||'').slice(0,400),zIndex:znum(el),position:s.position,score:Math.round(score*100)/100,box:{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}};
  }).sort((a,b)=>b.score-a.score);
  const activeSurface=surfaces[0]||null;
  let activeEl=null;
  if(activeSurface){ activeEl=surfaceCandidates[activeSurface.index]||null; }
  const inActive = el => !activeEl || activeEl===el || activeEl.contains(el);

  const controlSelector='input,textarea,select,button,a[href],[role="button"],[role="combobox"],[role="textbox"],[role="checkbox"],[role="radio"],[role="switch"],[role="tab"],[role="option"],[contenteditable="true"],dds-dropdown,dds-input,dds-switch,dds-checkbox,dds-radio-button,dds-button';
  const raw=[...document.querySelectorAll(controlSelector)].slice(0,maxControls);
  const controls=raw.map((el,index)=>{
    const r=el.getBoundingClientRect(), s=getComputedStyle(el);
    const host=el.closest?.('dds-dropdown,dds-form-field,dds-input,dds-switch,[formcontrolname],[formarrayname]');
    const ownedIds=clean(el.getAttribute?.('aria-controls')||el.getAttribute?.('aria-owns')).split(/\s+/).filter(Boolean);
    const ownedSurfaces=ownedIds.map(id=>document.getElementById(id)).filter(Boolean);
    const options=ownedSurfaces.flatMap(surface=>[...surface.querySelectorAll('[role="option"],option,[role="menuitem"],li')]).slice(0,maxOptions).map((opt,oi)=>({index:oi,text:text(opt).slice(0,500),role:opt.getAttribute('role')||'',selected:opt.getAttribute('aria-selected')==='true'||opt.getAttribute('aria-checked')==='true'||!!opt.selected,disabled:opt.getAttribute('aria-disabled')==='true'||!!opt.disabled,visible:visible(opt),position:opt.getAttribute('aria-posinset')||''}));
    const id=`${inspectionPrefix}-${index}`; try{el.setAttribute('data-hip-inspection-token',id);}catch(_){ }
    const inlineEvents={};
    for(const a of [...(el.attributes||[])]){if(/^on/i.test(a.name))inlineEvents[a.name.toLowerCase()]=true;}
    const attrs={};
    for(const name of ['formcontrolname','formarrayname','ng-reflect-name','data-testid','data-test-id','aria-haspopup','aria-expanded','aria-selected','aria-checked','aria-invalid','aria-required','aria-controls','aria-owns','aria-labelledby']){
      const v=el.getAttribute?.(name); if(v!==null&&v!=='') attrs[name]=String(v).slice(0,300);
    }
    return {
      index,inspectionToken:id,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',type:el.getAttribute('type')||'',name:el.getAttribute('name')||'',
      formControlName:el.getAttribute('formcontrolname')||host?.getAttribute?.('formcontrolname')||'',formArrayName:el.getAttribute('formarrayname')||host?.getAttribute?.('formarrayname')||'',
      label:labelFor(el).slice(0,400),section:headingFor(el),placeholder:el.getAttribute('placeholder')||'',visible:visible(el),inActiveSurface:inActive(el),
      required:!!(el.required||el.getAttribute('aria-required')==='true'),disabled:!!(el.disabled||el.getAttribute('aria-disabled')==='true'),readOnly:!!el.readOnly,
      expanded:el.getAttribute('aria-expanded')||'',checked:!!el.checked,hasValue:typeof el.value==='string'?el.value.length>0:false,valueLength:typeof el.value==='string'?el.value.length:0,
      ownedIds,ownedSurfaceVisible:ownedSurfaces.some(visible),optionCount:options.length,options,inlineEvents,attrs,
      layout:{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height),zIndex:znum(el),position:s.position,pointerEvents:s.pointerEvents},
      semanticPath:semanticPath(el)
    };
  });

  const popupRoots=[...document.querySelectorAll('[role="listbox"],[role="menu"],.dds__dropdown__menu,.dds__popover,.cdk-overlay-pane,[class*="overlay"]')].slice(0,500);
  const popupGraph=popupRoots.map((el,index)=>{
    const owners=raw.filter(c=>clean(c.getAttribute?.('aria-controls')||c.getAttribute?.('aria-owns')).split(/\s+/).includes(el.id)).map(c=>({label:labelFor(c).slice(0,300),role:c.getAttribute('role')||'',formControlName:c.getAttribute('formcontrolname')||'',inspectionToken:c.getAttribute('data-hip-inspection-token')||''}));
    return {index,id:el.id||'',role:el.getAttribute('role')||'',visible:visible(el),zIndex:znum(el),owners,optionCount:el.querySelectorAll('[role="option"],option,[role="menuitem"],li').length,text:text(el).slice(0,4000)};
  });

  const tabs=[...document.querySelectorAll('[role="tab"],dds-tabs button,.dds__tabs button')].slice(0,300).map((el,index)=>({index,text:text(el).slice(0,300),selected:el.getAttribute('aria-selected')==='true',controls:el.getAttribute('aria-controls')||'',visible:visible(el),inActiveSurface:inActive(el)}));
  const accordions=[...document.querySelectorAll('[aria-expanded][aria-controls],.dds__accordion__button')].slice(0,600).map((el,index)=>({index,text:(labelFor(el)||text(el)).slice(0,300),expanded:el.getAttribute('aria-expanded')||'',controls:el.getAttribute('aria-controls')||'',visible:visible(el),inActiveSurface:inActive(el)}));
  const repeated=[...document.querySelectorAll('[formarrayname],[cdkdrag],.dds__table__row,[role="row"]')].slice(0,1000).map((el,index)=>({index,formArrayName:el.getAttribute('formarrayname')||'',visible:visible(el),text:text(el).slice(0,800),childControlCount:el.querySelectorAll(controlSelector).length,inActiveSurface:inActive(el)}));
  const forms=[...document.querySelectorAll('form')].slice(0,200).map((el,index)=>({index,visible:visible(el),inActiveSurface:inActive(el),nativeValid:typeof el.checkValidity==='function'?el.checkValidity():null,angularValid:el.classList.contains('ng-valid'),angularInvalid:el.classList.contains('ng-invalid'),angularPending:el.classList.contains('ng-pending'),controlCount:el.querySelectorAll(controlSelector).length}));
  const tables=[...document.querySelectorAll('table,[role="grid"],[role="table"]')].slice(0,100).map((el,index)=>({index,visible:visible(el),inActiveSurface:inActive(el),rowCount:el.querySelectorAll('tbody tr,[role="row"]').length,text:text(el).slice(0,1200)}));
  const observer=window.__hipMaximumObservability||{};
  return {
    schemaVersion:'hip.website-understanding.dom.v1',url:location.href,title:document.title,readyState:document.readyState,
    frameworkHints:{angular:!!document.querySelector('[ng-version],app-root'),dds:!!document.querySelector('dds-drawer,dds-dropdown,dds-form-field,.dds__button'),react:!!document.querySelector('[data-reactroot],[data-reactid]')},
    viewport:{width:innerWidth,height:innerHeight,screenX:window.screenX,screenY:window.screenY,outerWidth:window.outerWidth,outerHeight:window.outerHeight,devicePixelRatio:window.devicePixelRatio,scrollX,scrollY},
    activeSurface,surfaces,controls,popupGraph,tabs,accordions,repeated,forms,tables,
    recentEvents:(observer.events||[]).slice(-maxEvents),recentMutations:(observer.mutations||[]).slice(-maxEvents)
  };
}"""


class WebsiteUnderstandingEngine:
    """Read-only, current-generation website understanding for HIP.

    This layer runs before action planning. It describes DOM structure, foreground
    surface, layout, control ownership, dropdown options and observed event/state
    transitions. It never clicks, types or submits.
    """

    SCHEMA_VERSION = "hip.website-understanding.v1"

    def __init__(self, *, config: Any = None, browser: Any = None) -> None:
        self.config = config
        self.browser = browser
        policy = getattr(config, "portal_learning", None) if config is not None else None
        self.enabled = bool(getattr(policy, "deep_website_understanding_enabled", True))
        self.max_controls = max(200, int(getattr(policy, "website_understanding_max_controls", 3000)))
        self.max_options = max(100, int(getattr(policy, "website_understanding_max_options_per_control", 500)))
        self.max_events = max(50, int(getattr(policy, "website_understanding_max_events", 500)))
        self.listener_limit = max(0, int(getattr(policy, "website_understanding_event_listener_control_limit", 160)))
        self.capture_registered_listeners = bool(getattr(policy, "capture_registered_event_listener_types", True))

    async def capture(
        self,
        *,
        page: Any,
        phase: str,
        stage: str = "observe",
        output_dir: Optional[str | Path] = None,
        include_registered_listeners: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if not self.enabled or page is None:
            return {"schema_version": self.SCHEMA_VERSION, "available": False, "reason": "disabled_or_page_unavailable"}
        prefix = "hipinspect-" + uuid.uuid4().hex[:12]
        try:
            dom = await page.evaluate(
                PAGE_MODEL_JS,
                {
                    "maxControls": self.max_controls,
                    "maxOptions": self.max_options,
                    "maxEvents": self.max_events,
                    "inspectionPrefix": prefix,
                },
            )
            dom = dom if isinstance(dom, Mapping) else {"raw": dom}
        except Exception as exc:
            return {
                "schema_version": self.SCHEMA_VERSION,
                "available": False,
                "phase": phase,
                "stage": stage,
                "reason": "dom_capture_failed",
                "error": mask_sensitive_string(str(exc))[:1200],
            }

        listener_map: Dict[str, Any] = {}
        capture_listeners = self.capture_registered_listeners if include_registered_listeners is None else bool(include_registered_listeners)
        if capture_listeners and self.listener_limit:
            listener_map = await self._capture_registered_listener_types(page, dom)

        controls: List[Dict[str, Any]] = []
        for raw in dom.get("controls", []) or []:
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            token = str(row.get("inspectionToken") or "")
            if token and token in listener_map:
                row["registeredEventListeners"] = listener_map[token]
            row["semanticControlKey"] = self._semantic_control_key(row)
            row["affordances"] = self._affordances(row)
            controls.append(row)
        dom = dict(dom)
        dom["controls"] = controls

        catalog = self._build_option_catalog(controls)
        action_catalog = self._build_action_catalog(controls)
        active = dom.get("activeSurface") if isinstance(dom.get("activeSurface"), Mapping) else {}
        foreground_controls = [c for c in controls if c.get("visible") and c.get("inActiveSurface") and not c.get("disabled")]
        unresolved_owned = [c for c in controls if c.get("expanded") == "true" and c.get("ownedIds") and not c.get("ownedSurfaceVisible")]
        confidence = self._confidence(dom, foreground_controls, unresolved_owned)
        model = {
            "schema_version": self.SCHEMA_VERSION,
            "available": True,
            "phase": phase,
            "stage": stage,
            "captured_at": utc_now(),
            "url_path": _path_only(str(dom.get("url") or "")),
            "title": dom.get("title"),
            "framework_hints": dom.get("frameworkHints") or {},
            "viewport": dom.get("viewport") or {},
            "active_surface": active,
            "surface_stack": dom.get("surfaces") or [],
            "controls": controls,
            "foreground_control_count": len(foreground_controls),
            "popup_ownership_graph": dom.get("popupGraph") or [],
            "option_catalog": catalog,
            "action_catalog": action_catalog,
            "tabs": dom.get("tabs") or [],
            "accordions": dom.get("accordions") or [],
            "repeatable_regions": dom.get("repeated") or [],
            "forms": dom.get("forms") or [],
            "tables": dom.get("tables") or [],
            "recent_ui_events": dom.get("recentEvents") or [],
            "recent_dom_mutations": dom.get("recentMutations") or [],
            "registered_listener_capture": {
                "available": bool(listener_map),
                "candidate_count": len(listener_map),
                "method": "chromium_cdp_DOMDebugger.getEventListeners",
            },
            "understanding_gate": {
                "pass": confidence >= 0.72,
                "confidence": round(confidence, 3),
                "active_surface_identified": bool(active),
                "foreground_controls_identified": bool(foreground_controls),
                "expanded_controls_with_unresolved_owned_surface": [c.get("semanticControlKey") for c in unresolved_owned[:50]],
            },
            "safety": {
                "read_only": True,
                "customer_values_stored": False,
                "selectors_persisted_as_memory": False,
                "coordinates_persisted_as_memory": False,
            },
        }
        model["state_fingerprint"] = _hash({
            "path": model["url_path"],
            "active": {k: active.get(k) for k in ("tag", "role", "ariaModal", "label", "classes")},
            "controls": [
                {
                    "key": c.get("semanticControlKey"), "role": c.get("role"), "type": c.get("type"),
                    "section": c.get("section"), "required": c.get("required"), "expanded": c.get("expanded"),
                    "optionCount": c.get("optionCount"), "inActiveSurface": c.get("inActiveSurface"),
                }
                for c in controls
            ],
            "tabs": model["tabs"],
            "accordions": model["accordions"],
        })
        safe = mask_sensitive_data(model)
        if output_dir is not None:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            safe_write_json(out / "website_understanding.json", safe)
            safe_write_json(out / "website_option_catalog.json", safe.get("option_catalog") or {})
            safe_write_json(out / "website_action_catalog.json", safe.get("action_catalog") or [])
        await self._cleanup_tokens(page, prefix)
        return safe

    async def _capture_registered_listener_types(self, page: Any, dom: Mapping[str, Any]) -> Dict[str, Any]:
        # Chromium-only, read-only CDP evidence. Fail open on Edge/Playwright drift.
        context = getattr(page, "context", None)
        if context is None or not hasattr(context, "new_cdp_session"):
            return {}
        try:
            cdp = await context.new_cdp_session(page)
        except Exception:
            return {}
        result: Dict[str, Any] = {}
        controls = [c for c in dom.get("controls", []) or [] if isinstance(c, Mapping) and c.get("visible")]
        # Foreground controls first; event listeners on background rows are lower value.
        controls.sort(key=lambda c: (bool(c.get("inActiveSurface")), bool(c.get("required"))), reverse=True)
        try:
            for c in controls[: self.listener_limit]:
                token = str(c.get("inspectionToken") or "")
                if not token:
                    continue
                expr = f"document.querySelector('[data-hip-inspection-token=\"{token}\"]')"
                try:
                    ev = await cdp.send("Runtime.evaluate", {"expression": expr, "returnByValue": False, "silent": True})
                    obj_id = ((ev or {}).get("result") or {}).get("objectId")
                    if not obj_id:
                        continue
                    listeners = await cdp.send("DOMDebugger.getEventListeners", {"objectId": obj_id, "depth": 1, "pierce": True})
                    types: Dict[str, Dict[str, Any]] = {}
                    for row in (listeners or {}).get("listeners", []) or []:
                        typ = str(row.get("type") or "")
                        if not typ:
                            continue
                        slot = types.setdefault(typ, {"type": typ, "count": 0, "useCapture": False, "passive": False, "once": False})
                        slot["count"] += 1
                        slot["useCapture"] = bool(slot["useCapture"] or row.get("useCapture"))
                        slot["passive"] = bool(slot["passive"] or row.get("passive"))
                        slot["once"] = bool(slot["once"] or row.get("once"))
                    if types:
                        result[token] = sorted(types.values(), key=lambda x: x["type"])
                except Exception:
                    continue
        finally:
            try:
                await cdp.detach()
            except Exception:
                pass
        return result

    async def _cleanup_tokens(self, page: Any, prefix: str) -> None:
        try:
            await page.evaluate(
                "prefix=>document.querySelectorAll('[data-hip-inspection-token]').forEach(el=>{if((el.getAttribute('data-hip-inspection-token')||'').startsWith(prefix))el.removeAttribute('data-hip-inspection-token')})",
                prefix,
            )
        except Exception:
            pass

    def _semantic_control_key(self, c: Mapping[str, Any]) -> str:
        parts = [
            _norm(c.get("section")),
            _norm(c.get("label") or c.get("placeholder")),
            _norm(c.get("formControlName") or c.get("name")),
            _norm(c.get("role") or c.get("type") or c.get("tag")),
        ]
        return ".".join(p for p in parts if p)[:300]

    def _affordances(self, c: Mapping[str, Any]) -> List[str]:
        if c.get("disabled") or not c.get("visible"):
            return []
        role = _norm(c.get("role")); tag = _norm(c.get("tag")); typ = _norm(c.get("type")); label = _norm(c.get("label"))
        out: List[str] = []
        if typ == "file": out.append("upload_file")
        elif tag in {"input", "textarea"} or role in {"textbox", "searchbox"}: out.append("fill_text")
        if tag == "select" or role == "combobox" or tag == "dds_dropdown": out.extend(["open_options", "select_option"])
        if role in {"checkbox", "radio", "switch"} or typ in {"checkbox", "radio"}: out.append("toggle_or_select")
        if role == "tab": out.append("activate_tab")
        if c.get("expanded") in {"true", "false"}: out.append("toggle_expand")
        if tag in {"button", "a", "dds_button"} or role == "button":
            out.append("click")
            # Stage 5: expose semantic action affordances from the current live
            # accessible label. This remains read-only metadata; the transition
            # planner still has to prove the foreground control before execution.
            if label in {"add", "+ add", "new", "create new"}:
                out.append("open_add_form")
            if label.startswith("add ") and any(x in label for x in ("row", "condition", "attribute", "action", "step", "identifier", "target")):
                out.append("add_row")
            for semantic in ("edit", "save", "validate", "clone", "migrate", "deploy", "create", "next", "back", "close"):
                if label == semantic or label.startswith(semantic + " "):
                    out.append(semantic)
        if any(k in label for k in ("submit", "create", "save", "deploy", "delete", "remove", "publish", "migrate", "clone")):
            out.append("mutation_candidate")
        return sorted(set(out))

    def _build_option_catalog(self, controls: List[Mapping[str, Any]]) -> Dict[str, Any]:
        catalog: Dict[str, Any] = {}
        for c in controls:
            if not c.get("optionCount") and not c.get("ownedIds"):
                continue
            key = str(c.get("semanticControlKey") or f"control_{c.get('index')}")
            catalog[key] = {
                "label": c.get("label"),
                "section": c.get("section"),
                "role": c.get("role"),
                "expanded": c.get("expanded"),
                "owned_ids": c.get("ownedIds") or [],
                "owned_surface_visible": bool(c.get("ownedSurfaceVisible")),
                "options": [
                    {"text": o.get("text"), "role": o.get("role"), "selected": bool(o.get("selected")), "disabled": bool(o.get("disabled")), "visible": bool(o.get("visible"))}
                    for o in (c.get("options") or []) if isinstance(o, Mapping)
                ],
            }
        return catalog

    def _build_action_catalog(self, controls: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for c in controls:
            affordances = list(c.get("affordances") or [])
            if not affordances or not c.get("inActiveSurface"):
                continue
            rows.append({
                "semantic_control_key": c.get("semanticControlKey"),
                "label": c.get("label"),
                "section": c.get("section"),
                "role": c.get("role"),
                "required": bool(c.get("required")),
                "affordances": affordances,
                "option_count": int(c.get("optionCount") or 0),
                "registered_event_types": [x.get("type") for x in c.get("registeredEventListeners", []) if isinstance(x, Mapping)],
            })
        return rows

    def _confidence(self, dom: Mapping[str, Any], foreground: List[Mapping[str, Any]], unresolved: List[Mapping[str, Any]]) -> float:
        score = 0.0
        if dom.get("url"): score += 0.12
        if dom.get("activeSurface"): score += 0.24
        if foreground: score += 0.24
        if dom.get("surfaces"): score += 0.08
        if dom.get("popupGraph") is not None: score += 0.08
        if dom.get("forms") is not None: score += 0.06
        if dom.get("tabs") is not None and dom.get("accordions") is not None: score += 0.06
        if dom.get("recentEvents") is not None and dom.get("recentMutations") is not None: score += 0.06
        if unresolved: score -= min(0.20, 0.04 * len(unresolved))
        return max(0.0, min(1.0, score))
