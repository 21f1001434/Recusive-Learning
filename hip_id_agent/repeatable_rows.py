from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from .security import mask_sensitive_data, mask_sensitive_string
from .browser_use_bridge import get_page_bridge
from .semantic_affordance import resolve_semantic_affordance



async def _semantic_repeatable_add(page: Any, *, selector: str, section: str, phase: str) -> tuple[Any, str, Dict[str, Any], Dict[str, Any]]:
    loc = page.locator(selector).first
    session = getattr(page, "_hip_browser_session", None)
    if session is None or not hasattr(session, "_semantic_action_preflight"):
        return loc, selector, {"pass": True, "status": "session_unavailable", "confidence": 1.0}, {"pass": True, "status": "not_required"}
    previous_phase = getattr(session, "_active_phase_name", "")
    if phase and not previous_phase:
        session._active_phase_name = phase
    try:
        resolution = await session._semantic_action_preflight(
            action="click", locator=loc, selector=selector, label=f"Add {section} row"
        )
        loc, selector, revalidation = await session._semantic_dispatch_target(
            resolution=resolution, locator=loc, selector=selector
        )
        return loc, selector, resolution, revalidation
    finally:
        if phase and not previous_phase:
            session._active_phase_name = previous_phase


async def _semantic_repeatable_add_commit(page: Any, *, resolution: Dict[str, Any], phase: str) -> Dict[str, Any]:
    session = getattr(page, "_hip_browser_session", None)
    if session is None or not resolution.get("semantic_control_id") or not hasattr(session, "_semantic_post_action_verify"):
        return {"pass": True, "status": "not_required", "confidence": 1.0}
    previous_phase = getattr(session, "_active_phase_name", "")
    if phase and not previous_phase:
        session._active_phase_name = phase
    try:
        return await session._semantic_post_action_verify(
            resolution=resolution, action="click", exact_value_verified=False
        )
    finally:
        if phase and not previous_phase:
            session._active_phase_name = previous_phase

def _as_objects(input_data: Dict[str, Any]) -> Dict[str, Any]:
    objs = input_data.get("objects") if isinstance(input_data, dict) else {}
    return objs if isinstance(objs, dict) else {}


def _get_path(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _rows(data: Any) -> List[Any]:
    return data if isinstance(data, list) else []


def _section(name: str, path: str, rows: Sequence[Any], aliases: Sequence[str], *, phase: str, tab: str = "", notes: str = "") -> Optional[Dict[str, Any]]:
    rows_l = [r for r in rows if isinstance(r, (dict, list, str, int, float, bool))]
    if not rows_l:
        return None
    return {
        "section": name,
        "input_path": path,
        "phase": phase,
        "tab": tab,
        "row_count_from_input": len(rows_l),
        "add_clicks_needed": max(0, len(rows_l) - 1),
        "aliases": list(aliases),
        "row_values": mask_sensitive_data(rows_l),
        "notes": notes or "Row count is derived from input.json; click + Add until the portal has this many rows, then fill row-by-row in order.",
    }


def _phase_object(input_data: Dict[str, Any], phase: str) -> Dict[str, Any]:
    objs = _as_objects(input_data)
    if phase in {"source_document_type", "target_document_type"}:
        return objs.get("document_type") or objs.get(phase) or objs.get("source_document_type") or objs.get("target_document_type") or {}
    if phase in {"source_transport_profile", "target_transport_profile"}:
        return objs.get("transport_profile") or objs.get(phase) or objs.get("source_transport_profile") or objs.get("target_transport_profile") or {}
    return objs.get(phase) or objs.get("biz_flow" if phase == "bizflow" else phase) or {}


def build_repeatable_section_plan(input_data: Dict[str, Any], phase: str) -> List[Dict[str, Any]]:
    """Build repeatable-row instructions from the user's input.json.

    The plan is intentionally portal-agnostic: it tells the browser agent how many
    rows a section needs before filling values.  This covers places like Rules
    Conditions where input.json has two rows and the UI starts with one visible
    row, requiring one safe + Add click.
    """
    plan: List[Dict[str, Any]] = []
    objs = _as_objects(input_data)
    obj = _phase_object(input_data, phase)

    def add(item: Optional[Dict[str, Any]]) -> None:
        if item and item.get("row_count_from_input", 0) > 0:
            plan.append(item)

    phase_norm = str(phase or "").lower()
    if phase_norm == "data_map":
        add(_section("Cross Reference Map", "objects.data_map.crossReferenceMapList", _rows(_get_path(obj, "crossReferenceMapList") or _get_path(obj, "cross_reference_map_list")), ["cross reference", "xref", "reference map"], phase=phase_norm))

    if phase_norm in {"source_document_type", "target_document_type", "document_type"}:
        add(_section("Document Identifier", "objects.document_type.document_identifier.rows", _rows(_get_path(obj, "document_identifier.rows") or _get_path(obj, "documentIdentifier.rows")), ["document identifier", "identifier", "root element"], phase=phase_norm))
        add(_section("Attributes To Configure", "objects.document_type.attributes_to_configure", _rows(_get_path(obj, "attributes_to_configure") or _get_path(obj, "attributes") or _get_path(obj, "documentAttributes")), ["attributes to configure", "attribute", "attribute name", "usage", "expression"], phase=phase_norm))

    if phase_norm == "rule":
        add(_section("Rule Conditions", "objects.rule.conditions.rows", _rows(_get_path(obj, "conditions.rows") or _get_path(obj, "ruleConditions")), ["condition", "conditions", "rule condition", "execute actions when"], phase=phase_norm))
        add(_section("Rule Actions", "objects.rule.actions", _rows(_get_path(obj, "actions.rows") or _get_path(obj, "actions.actions") or (_get_path(obj, "actions") if isinstance(_get_path(obj, "actions"), list) else [])), ["action", "actions", "route document", "mapping"], phase=phase_norm))

    if phase_norm in {"source_transport_profile", "target_transport_profile", "transport_profile"}:
        add(_section("Interface Parameters", "objects.transport_profile.interface_details.parameters", _rows(_get_path(obj, "interface_details.parameters") or _get_path(obj, "interfaceDetails.parameters") or _get_path(obj, "parameters")), ["parameter", "parameters", "interface details", "interface parameter"], phase=phase_norm))
        add(_section("Document Type Details", "objects.transport_profile.document_type_details", _rows(_get_path(obj, "document_type_details") or _get_path(obj, "documentTypeDetails")), ["document type", "document type details"], phase=phase_norm))

    if phase_norm == "biz_flow":
        bf = obj
        add(_section("Flow Identifier Conditions", "objects.biz_flow.flow_identifiers.conditions", _rows(_get_path(bf, "flow_identifiers.conditions")), ["flow identifier", "condition", "conditions", "attribute name"], phase=phase_norm, tab="Configure Source"))
        add(_section("Configure Target Rows", "objects.biz_flow.configure_targets", _rows(_get_path(bf, "configure_targets.targets") or (_get_path(bf, "configure_targets") if isinstance(_get_path(bf, "configure_targets"), list) else [])), ["target", "configure target", "target details"], phase=phase_norm, tab="Configure Target"))
        add(_section("Process Steps", "objects.biz_flow.process_steps", _rows(_get_path(bf, "process_steps")), ["process step", "step", "mapping transformer", "enricher"], phase=phase_norm, tab="Configure Target"))
        for idx, step in enumerate(_rows(_get_path(bf, "process_steps")), start=1):
            cfg = step.get("configuration") if isinstance(step, dict) else {}
            add(_section(f"Process Step {idx} File Name Parts", f"objects.biz_flow.process_steps[{idx-1}].configuration.file_name_parts", _rows(_get_path(cfg or {}, "file_name_parts")), ["file name", "file name part", "target file name", "separator"], phase=phase_norm, tab="Configure Target"))
        add(_section("Routing Conditions", "objects.biz_flow.configure_routing.conditions.rows", _rows(_get_path(bf, "configure_routing.conditions.rows")), ["routing", "condition", "conditions", "configure routing"], phase=phase_norm, tab="Configure Routing + Add"))
        add(_section("Routing Actions", "objects.biz_flow.configure_routing.actions", _rows(_get_path(bf, "configure_routing.actions.rows") or _get_path(bf, "configure_routing.actions.actions") or (_get_path(bf, "configure_routing.actions") if isinstance(_get_path(bf, "configure_routing.actions"), list) else [])), ["routing", "action", "route document", "target"], phase=phase_norm, tab="Configure Routing + Add"))

    # Keep only true repeatables requiring Add clicks, but include single rows in saved plan via caller if needed.
    return [p for p in plan if int(p.get("row_count_from_input") or 0) > 1]


def _tab_matches(plan_tab: str, current_tab: Optional[str]) -> bool:
    if not current_tab or not plan_tab:
        return True
    a = re.sub(r"[^a-z0-9]+", " ", plan_tab.lower()).strip()
    b = re.sub(r"[^a-z0-9]+", " ", current_tab.lower()).strip()
    if a == b:
        return True
    # Source Details / Configure Source and Target Details / Configure Target are portal variants.
    aliases = {
        "configure source": {"source details", "configure source"},
        "source details": {"source details", "configure source"},
        "configure target": {"target details", "configure target", "configure targets"},
        "target details": {"target details", "configure target", "configure targets"},
        "configure routing add": {"configure routing", "configure routing add"},
    }
    return bool(aliases.get(a, {a}) & aliases.get(b, {b}))


async def _repeatable_row_count(page: Any, section: Dict[str, Any]) -> int:
    """Return the visible row count for a known repeatable section."""
    name = str(section.get("section") or "").lower()
    js = r"""
({name}) => {
  function visible(el){if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden');}
  const low=String(name||'').toLowerCase();
  if(low.includes('attributes to configure')){
    return Array.from(document.querySelectorAll('input[name="attributeName"], input[formcontrolname="attributeName"], input[placeholder*="Attribute Name" i]')).filter(visible).length;
  }
  if(low.includes('rule conditions') || low.includes('routing conditions') || low.includes('flow identifier')){
    const selectors='app-condition,.condition-row,[data-testid*="condition"],tr:has(input[placeholder*="Attribute" i])';
    return Array.from(document.querySelectorAll(selectors)).filter(visible).length;
  }
  if(low.includes('actions')){
    return Array.from(document.querySelectorAll('app-action,.action-row,[data-testid*="action"]')).filter(visible).length;
  }
  if(low.includes('process steps')){
    return Array.from(document.querySelectorAll('app-process-step,.process-step,[data-testid*="process-step"]')).filter(visible).length;
  }
  if(low.includes('file name parts')){
    return Array.from(document.querySelectorAll('[data-testid*="file-name"],.file-name-part-row,input[placeholder*="File Name" i]')).filter(visible).length;
  }
  return 0;
}
"""
    try:
        return int(await page.evaluate(js, {"name": name}) or 0)
    except Exception:
        return 0


async def _find_safe_add_button(page: Any, aliases: Sequence[str], *, phase: str = "") -> Dict[str, Any]:
    # Primary generalized affordance resolver.  This understands icon-only +/Add
    # controls from accessible name, SVG/icon metadata and local section context.
    # The specialized legacy resolver below remains as a compatibility fallback;
    # the caller still requires an exact N -> N+1 effect before accepting the click.
    try:
        semantic = await resolve_semantic_affordance(
            page, intent="add_row", aliases=list(aliases or []), allow_mutation=False, ambiguity_margin=8
        )
        if semantic.get("resolved") and semantic.get("selector"):
            return {
                "selector": semantic.get("selector"),
                "label": semantic.get("label") or "+",
                "context": str((semantic.get("candidate") or {}).get("context") or "")[:700],
                "score": semantic.get("score"),
                "alias_hits": semantic.get("alias_hits") or [],
                "source": "semantic-affordance-primary",
                "intent": "add_row",
                "expected_effect": "row_count_plus_one",
                "semantic_evidence": semantic.get("evidence") or [],
            }
        if semantic.get("ambiguous"):
            return {
                "ambiguous": True,
                "source": "semantic-affordance-primary",
                "intent": "add_row",
                "reason": semantic.get("reason"),
                "candidates": semantic.get("candidates") or [],
            }
    except Exception:
        pass
    """Resolve a true row-level Add control inside the active form section.

    Container divs/spans are never candidates. The candidate must be an
    actionable button/link, have a compact bounding box, live inside the
    current form, and be near the requested section heading.
    """
    js = r"""
({aliases, phase}) => {
  function norm(s){ return String(s||'').replace(/\s+/g,' ').trim(); }
  function clean(s){ return norm(s).toLowerCase(); }
  function visible(el){if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width>=12&&r.height>=12&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||'1')!==0);}
  function path(el){
    if(!el) return '';
    if(el.id) return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;
    const parts=[]; let n=el;
    for(let depth=0;n && depth<8 && n.nodeType===1;depth++,n=n.parentElement){
      let part=n.tagName.toLowerCase();
      const cls=Array.from(n.classList||[]).filter(c=>!/^ng-|^cdk-|^dds__focus/.test(c)).slice(0,2);
      if(cls.length) part+='.'+cls.map(c=>CSS.escape(c)).join('.');
      const parent=n.parentElement;
      if(parent){const same=Array.from(parent.children).filter(x=>x.tagName===n.tagName);if(same.length>1)part+=`:nth-of-type(${same.indexOf(n)+1})`;}
      parts.unshift(part);
    }
    return parts.join(' > ');
  }
  const aliasList=(aliases||[]).map(clean).filter(Boolean);
  const bad=/\b(save|create|submit|delete|remove|deploy|enable|disable|confirm|publish|update|cancel|close)\b/i;
  const phaseLow=clean(phase);
  const roots=Array.from(document.querySelectorAll('[role="dialog"],dds-drawer,.dds__drawer,.dds__modal,form,app-document-type-create,app-rule-create,app-transport-profile-create,app-create-biz-flow,main')).filter(visible);
  const activeRoots=roots.filter(root=>{
    const t=clean(root.innerText||root.textContent||'');
    if(phaseLow.includes('document_type')) return t.includes('create document type') && t.includes('attributes to configure');
    if(phaseLow==='rule') return t.includes('create rule');
    if(phaseLow.includes('transport_profile')) return t.includes('create transport profile');
    if(phaseLow==='biz_flow') return t.includes('create biz flow') || t.includes('configure source') || t.includes('configure target') || t.includes('configure routing');
    return true;
  });
  const root=activeRoots.sort((a,b)=>{const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();return ar.width*ar.height-br.width*br.height;})[0] || null;
  if(!root) return null;
  const actionable=Array.from(root.querySelectorAll('button,a,[role="button"],dds-button,dds-link')).filter(visible);
  const cands=[];
  for(const el of actionable){
    const r=el.getBoundingClientRect();
    if(r.width>260 || r.height>90) continue;
    const label=norm(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('name') || '');
    const lower=clean(label);
    const exactAdd=/^(\+\s*)?add$/i.test(label) || /^add\s+(row|attribute|condition|action|step)$/i.test(label);
    const plusOnly=/^\+$/.test(label) || /plus/i.test(el.getAttribute('aria-label')||'') || !!el.querySelector('[class*=plus],[name*=plus],svg use[href*=plus]');
    if(!(exactAdd||plusOnly) || bad.test(lower)) continue;
    let section=el.closest('section,fieldset,.dds__accordion-item,.dds__card,.dds__row,table,form,[role="region"]') || root;
    let ctx=clean(section.innerText||section.textContent||'').slice(0,2500);
    const aliasHits=aliasList.filter(a=>a && (ctx.includes(a)||lower.includes(a))).length;
    if(aliasList.length && !aliasHits) continue;
    let score=aliasHits*20 + (exactAdd?8:3);
    if(/attribute|condition|action|routing|target|step|file name|parameter/.test(ctx)) score+=4;
    if(el.closest('[role="dialog"],dds-drawer,.dds__drawer,.dds__modal,form')) score+=5;
    cands.push({selector:path(el), label, context:ctx.slice(0,500), score, alias_hits:aliasHits, tag:el.tagName.toLowerCase(), width:r.width, height:r.height});
  }
  cands.sort((a,b)=>b.score-a.score);
  return cands[0] || null;
}
"""
    try:
        cand = await page.evaluate(js, {"aliases": list(aliases or []), "phase": phase})
        return cand if isinstance(cand, dict) else {}
    except Exception as exc:
        return {"error": mask_sensitive_string(str(exc))}


async def _browser_use_recovery_evidence(page: Any) -> Dict[str, Any]:
    """Fetch state-only Browser-Use evidence; never performs a click/action."""
    bridge = get_page_bridge(page)
    if bridge is None:
        return {"available": False}
    try:
        return await bridge.recovery_context(max_elements=80)
    except Exception as exc:
        return {"available": False, "error": mask_sensitive_string(str(exc))}


async def _find_safe_add_button_structured_fallback(page: Any, aliases: Sequence[str], *, phase: str = "") -> Dict[str, Any]:
    """Browser-use-style broad element inventory for an icon-only/local + control.

    This fallback never trusts a global plus. It accepts only a compact actionable
    element whose local section context matches the intended repeatable section.
    The caller still requires an exact N -> N+1 row-count transition after click.
    """
    js = r"""
({aliases, phase}) => {
  const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
  const low=s=>norm(s).toLowerCase();
  const visible=el=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>=10&&r.height>=10&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)>0};
  const cssPath=el=>{if(!el)return '';if(el.id)return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;const out=[];let n=el;for(let d=0;n&&d<9&&n.nodeType===1;d++,n=n.parentElement){let p=n.tagName.toLowerCase();const nm=n.getAttribute('name');if(nm)p+=`[name="${CSS.escape(nm)}"]`;const par=n.parentElement;if(par){const same=Array.from(par.children).filter(x=>x.tagName===n.tagName);if(same.length>1)p+=`:nth-of-type(${same.indexOf(n)+1})`;}out.unshift(p);}return out.join(' > ')};
  const bad=/\b(save|create|submit|delete|remove|deploy|enable|disable|confirm|publish|update|cancel|close)\b/i;
  const a=(aliases||[]).map(low).filter(Boolean);
  const phaseLow=low(phase);
  const all=Array.from(document.querySelectorAll('button,a,[role=button],dds-button,[tabindex]')).filter(visible);
  const rows=[];
  for(const el of all){
    const r=el.getBoundingClientRect(); if(r.width>180||r.height>75)continue;
    const aria=norm(el.getAttribute('aria-label')); const title=norm(el.getAttribute('title')); const txt=norm(el.innerText||el.textContent);
    const icon=Array.from(el.querySelectorAll('svg,use,i,dds-icon,[class*=icon]')).map(x=>norm(`${x.getAttribute('href')||''} ${x.getAttribute('xlink:href')||''} ${x.getAttribute('name')||''} ${x.getAttribute('class')||''}`)).join(' ');
    const label=norm(`${txt} ${aria} ${title} ${icon}`); const ll=low(label);
    const plus=/(^|\s)\+(\s|$)|\bplus\b|\badd[-_ ]?(circle|row|item|condition|attribute|action|step)?\b/.test(ll);
    if(!plus||bad.test(ll))continue;
    let section=el.closest('section,fieldset,table,.dds__accordion-item,.dds__card,[role=region],app-condition,app-action,app-process-step,form')||el.parentElement;
    let ctx=low(section?.innerText||section?.textContent||'').slice(0,3500);
    if(ctx.length<10){const parent=el.parentElement?.parentElement;ctx=low(parent?.innerText||parent?.textContent||'').slice(0,3500);}
    const hits=a.filter(x=>ctx.includes(x)||ll.includes(x));
    if(a.length && hits.length===0)continue;
    let score=hits.length*30 + (aria?8:0)+(title?5:0)+(txt==='+'?12:0);
    if(/condition|attribute|action|routing|parameter|step|file name|identifier/.test(ctx))score+=8;
    if(phaseLow && ctx.includes(phaseLow.replaceAll('_',' ')))score+=3;
    rows.push({selector:cssPath(el),label:label.slice(0,300),context:ctx.slice(0,700),score,alias_hits:hits,tag:el.tagName.toLowerCase(),width:r.width,height:r.height,source:'structured-fallback'});
  }
  rows.sort((x,y)=>y.score-x.score);
  if(!rows.length)return null;
  // Refuse ambiguity: two equally strong plus controls in the same section.
  if(rows.length>1 && rows[0].score===rows[1].score && rows[0].selector!==rows[1].selector)return {ambiguous:true,candidates:rows.slice(0,5)};
  return rows[0];
}
"""
    try:
        cand = await page.evaluate(js, {"aliases": list(aliases or []), "phase": phase})
        return cand if isinstance(cand, dict) else {}
    except Exception as exc:
        return {"error": mask_sensitive_string(str(exc)), "source": "structured-fallback"}


async def _autowebglm_primary_add_gate(page: Any, *, selector: str, section: str, phase: str) -> Dict[str, Any]:
    """Keep AutoWebGLM as the primary policy even for icon-only repeatable Add.

    The exact row-count transition remains the execution authority; AutoWebGLM
    approves the semantic intent, while Playwright performs the local tool action.
    """
    session = getattr(page, "_hip_browser_session", None)
    if session is None or not hasattr(session, "_autowebglm_primary_decision"):
        return {"status": "bypassed", "framework": "autowebglm"}
    return await session._autowebglm_primary_decision(
        action="click", selector=selector,
        label=f"Add one repeatable row in {section or 'current section'}", value=""
    )


async def apply_repeatable_row_adds(page: Any, input_data: Dict[str, Any], phase: str, *, tab_label: Optional[str] = None, max_clicks: int = 12) -> Dict[str, Any]:
    """Create the exact number of repeatable rows required by input.json.

    Every click is effect-validated. A row is accepted only when the visible
    section row count increases by exactly one. No generic container element is
    ever clicked.
    """
    plan_all = build_repeatable_section_plan(input_data or {}, phase)
    plan = [p for p in plan_all if _tab_matches(str(p.get("tab") or ""), tab_label)]
    audit: Dict[str, Any] = {
        "phase": phase,
        "tab": tab_label or "",
        "sections": mask_sensitive_data(plan),
        "clicks": [],
        "summary": {"sections": len(plan), "planned_add_clicks": 0, "clicked": 0, "failed": 0, "exact_row_count_pass": True},
        "safety": "Row-level actionable +Add only; every click requires an exact +1 row-count effect.",
    }
    bad = re.compile(r"\b(save|create|submit|delete|remove|deploy|enable|disable|confirm|publish|update)\b", re.I)
    for section in plan:
        expected = int(section.get("row_count_from_input") or 0)
        before_initial = await _repeatable_row_count(page, section)
        # HIP forms usually render one initial row. If a section-specific count
        # cannot be measured, do not guess or click.
        section_audit = {"section": section.get("section"), "expected_row_count": expected, "initial_row_count": before_initial}
        if before_initial <= 0:
            section_audit.update({"pass": False, "reason": "section row count could not be measured on the active form", "browser_use_recovery_state": await _browser_use_recovery_evidence(page)})
            audit["summary"]["failed"] += 1
            audit["summary"]["exact_row_count_pass"] = False
            audit["clicks"].append(section_audit)
            continue
        if before_initial > expected:
            section_audit.update({"pass": False, "reason": "portal already has more rows than input.json; destructive removal is blocked"})
            audit["summary"]["failed"] += 1
            audit["summary"]["exact_row_count_pass"] = False
            audit["clicks"].append(section_audit)
            continue
        clicks_needed = min(max_clicks, max(0, expected - before_initial))
        audit["summary"]["planned_add_clicks"] += clicks_needed
        aliases = section.get("aliases") or [section.get("section") or "add"]
        for click_index in range(clicks_needed):
            current = await _repeatable_row_count(page, section)
            cand = await _find_safe_add_button(page, aliases, phase=phase)
            if not str((cand or {}).get("selector") or ""):
                cand = await _find_safe_add_button_structured_fallback(page, aliases, phase=phase)
            entry = {"section": section.get("section"), "input_path": section.get("input_path"), "click_index": click_index + 1, "before_row_count": current, "candidate": cand, "clicked": False}
            label = str((cand or {}).get("label") or "")
            selector = str((cand or {}).get("selector") or "")
            if not selector or bad.search(label) or bool((cand or {}).get("ambiguous")):
                entry["reason"] = "no unambiguous safe row-level actionable Add candidate found"
                entry["browser_use_recovery_state"] = await _browser_use_recovery_evidence(page)
                audit["summary"]["failed"] += 1
                audit["summary"]["exact_row_count_pass"] = False
                audit["clicks"].append(entry)
                break
            try:
                loc = page.locator(selector).first
                if await loc.count() and await loc.is_visible(timeout=1000) and await loc.is_enabled(timeout=1000):
                    loc, selector, semantic_resolution, semantic_revalidation = await _semantic_repeatable_add(
                        page, selector=selector, section=str(section.get("section") or "repeatable"), phase=phase
                    )
                    entry["semantic_gate"] = {
                        "semantic_control_id": semantic_resolution.get("semantic_control_id") or "",
                        "confidence": semantic_resolution.get("confidence"),
                        "margin": semantic_resolution.get("margin"),
                        "status": semantic_resolution.get("status") or "",
                        "revalidation": semantic_revalidation.get("status") or "",
                    }
                    entry["autowebglm_primary"] = await _autowebglm_primary_add_gate(
                        page, selector=selector, section=str(section.get("section") or ""), phase=phase
                    )
                    session = getattr(page, "_hip_browser_session", None)
                    if session is not None and hasattr(session, "click_and_wait"):
                        await session.click_and_wait(
                            action=f"Add {section.get('section') or 'repeatable'} row",
                            locator=loc, selector=selector, mutation_risk=False,
                        )
                        try:
                            prov = (getattr(session, "action_events", None) or [])[-1].execution_provenance or {}
                            entry["executor"] = str(prov.get("actual_executor") or "browser-session-broker")
                        except Exception:
                            entry["executor"] = "browser-session-broker"
                    else:
                        # Offline unit-test compatibility only; live HIP pages always
                        # have BrowserSession attached and therefore use PyAutoGUI MCP first.
                        await loc.click(timeout=2500)
                        entry["executor"] = "python-playwright-offline"
                    await page.wait_for_timeout(500)
                    after = await _repeatable_row_count(page, section)
                    entry["after_row_count"] = after
                    entry["effect_delta"] = after - current
                    if after == current + 1:
                        semantic_effect = await _semantic_repeatable_add_commit(page, resolution=semantic_resolution, phase=phase)
                        entry["semantic_effect"] = {
                            "pass": bool(semantic_effect.get("pass")),
                            "effect_type": semantic_effect.get("effect_type") or "",
                            "confidence": semantic_effect.get("confidence"),
                        }
                        entry["clicked"] = True
                        audit["summary"]["clicked"] += 1
                    else:
                        entry["reason"] = f"Add click did not create exactly one row (before={current}, after={after})"
                        audit["summary"]["failed"] += 1
                        audit["summary"]["exact_row_count_pass"] = False
                        audit["clicks"].append(entry)
                        break
                else:
                    entry["reason"] = "candidate not visible/enabled"
                    audit["summary"]["failed"] += 1
                    audit["summary"]["exact_row_count_pass"] = False
                    audit["clicks"].append(entry)
                    break
            except Exception as exc:
                entry["reason"] = mask_sensitive_string(str(exc))
                audit["summary"]["failed"] += 1
                audit["summary"]["exact_row_count_pass"] = False
                audit["clicks"].append(entry)
                break
            audit["clicks"].append(entry)
        final_count = await _repeatable_row_count(page, section)
        if final_count != expected:
            audit["summary"]["exact_row_count_pass"] = False
            audit["summary"]["failed"] += 1
            audit["clicks"].append({"section": section.get("section"), "expected_row_count": expected, "actual_row_count": final_count, "clicked": False, "reason": "final exact row count mismatch"})
    return mask_sensitive_data(audit)

