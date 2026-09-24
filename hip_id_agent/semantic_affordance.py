from __future__ import annotations

"""Semantic UI affordance discovery for dynamic HIP/DDS controls.

The live portal frequently represents actions with icon-only buttons: ``+`` for an
additional row, chevrons for expansion, ellipsis/kebab icons for menus, pencils for
edit, copy icons for clone, and similar controls.  Text-only selector strategies are
therefore insufficient.

This module treats an action as an *affordance intent* and ranks actionable elements
from accessible name, title, SVG/icon metadata, ARIA state, local section context and
known risk.  Selection is still fail-closed: ambiguous candidates are rejected and
state-changing callers must verify the resulting portal effect.
"""

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .security import mask_sensitive_string


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _tokens(value: Any) -> set[str]:
    return {x for x in _norm(value).split() if x}


@dataclass(frozen=True)
class AffordanceSpec:
    intent: str
    synonyms: tuple[str, ...]
    icon_terms: tuple[str, ...]
    context_terms: tuple[str, ...] = ()
    mutation: bool = False
    expected_effect: str = "structural_change"


SPECS: Dict[str, AffordanceSpec] = {
    "open_add_form": AffordanceSpec(
        "open_add_form",
        ("+ add", "add", "new", "add new", "create new"),
        ("plus", "add", "plus circle", "circle plus"),
        ("data map", "document type", "rule", "transport profile", "biz flow", "listing", "configuration"),
        expected_effect="create_surface_opened",
    ),
    "add_row": AffordanceSpec(
        "add_row",
        ("add", "add row", "add item", "add condition", "add attribute", "add action", "add step", "+"),
        ("plus", "add", "add circle", "add row", "add item", "circle plus", "plus circle"),
        ("row", "condition", "attribute", "action", "step", "parameter", "identifier", "routing", "file name"),
        expected_effect="row_count_plus_one",
    ),
    "expand": AffordanceSpec(
        "expand", ("expand", "show", "details", "open"),
        ("chevron down", "caret down", "arrow down", "expand", "unfold", "angle down"),
        expected_effect="expanded_or_surface_revealed",
    ),
    "collapse": AffordanceSpec(
        "collapse", ("collapse", "hide"),
        ("chevron up", "caret up", "arrow up", "collapse", "fold", "angle up"),
        expected_effect="collapsed",
    ),
    "more_actions": AffordanceSpec(
        "more_actions", ("more", "more actions", "actions", "options", "menu"),
        ("ellipsis", "kebab", "more vert", "more horiz", "overflow", "three dots", "dots vertical", "dots horizontal"),
        expected_effect="menu_or_popover_opened",
    ),
    "edit": AffordanceSpec(
        "edit", ("edit", "modify"), ("edit", "pencil", "pen"), expected_effect="edit_surface_opened"
    ),
    "clone": AffordanceSpec(
        "clone", ("clone", "copy", "duplicate"), ("copy", "clone", "duplicate", "content copy"), mutation=True, expected_effect="mutation_surface_opened"
    ),
    "migrate": AffordanceSpec(
        "migrate", ("migrate", "migration", "move"), ("migrate", "transfer", "move"), mutation=True, expected_effect="mutation_surface_opened"
    ),
    "deploy": AffordanceSpec(
        "deploy", ("deploy", "deployment"), ("deploy", "rocket", "cloud upload"), mutation=True, expected_effect="mutation_surface_opened"
    ),
    "delete": AffordanceSpec(
        "delete", ("delete", "remove"), ("trash", "delete", "remove", "bin"), mutation=True, expected_effect="mutation_surface_opened"
    ),
    "save": AffordanceSpec(
        "save", ("save",), ("save", "disk", "floppy"), mutation=True, expected_effect="mutation_committed"
    ),
    "validate": AffordanceSpec(
        "validate", ("validate", "validation", "check", "verify"), ("check", "verify", "validation"), expected_effect="validation_result_observed"
    ),
    "create": AffordanceSpec(
        "create", ("create", "submit"), ("create", "check", "done"), mutation=True, expected_effect="mutation_committed"
    ),
    "next": AffordanceSpec(
        "next", ("next", "continue"), ("arrow right", "chevron right", "next"), expected_effect="surface_advanced"
    ),
    "back": AffordanceSpec(
        "back", ("back", "previous"), ("arrow left", "chevron left", "previous"), expected_effect="surface_changed"
    ),
    "close": AffordanceSpec(
        "close", ("close", "dismiss", "cancel"), ("close", "x", "times"), expected_effect="surface_closed"
    ),
    "search": AffordanceSpec(
        "search", ("search", "find"), ("search", "magnifier", "magnifying glass"), expected_effect="search_surface_or_results"
    ),
    "refresh": AffordanceSpec(
        "refresh", ("refresh", "reload"), ("refresh", "reload", "rotate", "sync"), expected_effect="page_refreshed"
    ),
    "filter": AffordanceSpec(
        "filter", ("filter", "filters"), ("filter", "funnel", "tune"), expected_effect="filter_surface_opened"
    ),
    "settings": AffordanceSpec(
        "settings", ("settings", "configure", "configuration"), ("settings", "gear", "cog", "tune"), expected_effect="settings_surface_opened"
    ),
    "retry": AffordanceSpec(
        "retry", ("retry", "try again"), ("retry", "refresh", "rotate"), expected_effect="surface_changed"
    ),
    "download": AffordanceSpec(
        "download", ("download", "export"), ("download", "file download", "arrow down tray"), expected_effect="download_started"
    ),
    "upload": AffordanceSpec(
        "upload", ("upload", "import"), ("upload", "file upload", "arrow up tray"), expected_effect="upload_surface_opened"
    ),
}


INTENT_ALIASES: Dict[str, str] = {
    "add": "add_row", "plus": "add_row", "add_item": "add_row", "add_condition": "add_row",
    "new": "open_add_form", "add_new": "open_add_form", "new_item": "open_add_form",
    "add_attribute": "add_row", "add_action": "add_row", "add_step": "add_row",
    "open_menu": "more_actions", "overflow": "more_actions", "menu": "more_actions",
    "details": "expand", "open_details": "expand", "expand_row": "expand",
    "previous": "back", "continue": "next", "reload": "refresh",
    "copy": "clone", "duplicate": "clone", "remove": "delete",
}

COMPOUND_MENU_INTENTS = frozenset({"edit", "clone", "migrate", "deploy", "delete", "download"})

# When a proven action opens a detached dialog/drawer/menu, these intents are
# normally continuations of that active surface rather than unrelated page-level
# controls.  The browser runtime therefore resolves them inside the active proven
# surface first and will not silently jump to a same-named global action while the
# child surface remains valid.
SURFACE_CONTINUATION_INTENTS = frozenset({
    "edit", "clone", "migrate", "deploy", "delete",
    "save", "create", "next", "back", "close", "retry",
    "upload", "download",
})
MAX_SURFACE_CHAIN_DEPTH = 8


def prefers_active_surface_chain(intent: str) -> bool:
    return canonical_intent(intent) in SURFACE_CONTINUATION_INTENTS


def requires_compound_menu_fallback(intent: str) -> bool:
    """Return True for actions that are commonly hidden behind a row overflow menu."""
    return canonical_intent(intent) in COMPOUND_MENU_INTENTS


def selector_looks_generation_volatile(selector: str) -> bool:
    """Detect selectors that likely encode Angular/DDS generated identity.

    Volatile selectors are not persisted as semantic identity. They may still be used
    for one immediate click after a fresh resolution, but callers should re-resolve
    the affordance before execution.
    """
    value = str(selector or "")
    return bool(re.search(
        r"#(?:dds|ng|mat|cdk|react|ember|generated)[-_:.]?[a-z0-9_-]*\d{2,}|"
        r"#[0-9a-f]{8}-[0-9a-f-]{20,}|#[a-z_-]*\d{6,}|"
        r":nth-(?:child|of-type)\s*\(",
        value, re.I
    ))


def canonical_intent(intent: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(intent or "").strip().lower()).strip("_")
    return INTENT_ALIASES.get(key, key)


def _candidate_text(candidate: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "text": _norm(candidate.get("text")),
        "aria": _norm(candidate.get("aria_label")),
        "title": _norm(candidate.get("title")),
        "icon": _norm(candidate.get("icon_signature")),
        "classes": _norm(candidate.get("classes")),
        "testid": _norm(candidate.get("testid")),
        "href": _norm(candidate.get("href")),
        "context": _norm(candidate.get("context")),
    }


def _contains_phrase(haystack: str, phrase: str) -> bool:
    p = _norm(phrase)
    if not p:
        return False
    if p == "+":
        return str(haystack or "").strip() == "+"
    return p in haystack


def rank_affordance_candidate(
    candidate: Mapping[str, Any],
    *,
    intent: str,
    aliases: Sequence[str] = (),
    allow_mutation: bool = False,
) -> Dict[str, Any]:
    """Score one candidate for a desired semantic affordance.

    Icon-only controls are accepted only when local context disambiguates them.  A
    bare global ``+`` is intentionally weak; a ``+`` inside a Rule Conditions
    section with matching aliases is strong.
    """
    canonical = canonical_intent(intent)
    spec = SPECS.get(canonical)
    if spec is None:
        return {"accepted": False, "score": -999, "reason": f"unsupported affordance intent: {canonical}"}
    if spec.mutation and not allow_mutation:
        return {"accepted": False, "score": -999, "reason": "mutation affordance requires governance authorization", "intent": canonical}

    f = _candidate_text(candidate)
    score = 0
    evidence: List[str] = []

    direct_fields = (("text", 95), ("aria", 90), ("title", 82), ("testid", 55), ("classes", 38), ("href", 26))
    for synonym in spec.synonyms:
        for field, weight in direct_fields:
            if _contains_phrase(f[field], synonym):
                score += weight
                evidence.append(f"{field}:{_norm(synonym)}")
                break

    for term in spec.icon_terms:
        if _contains_phrase(f["icon"], term) or _contains_phrase(f["classes"], term):
            score += 72
            evidence.append(f"icon:{_norm(term)}")
            break

    alias_hits = []
    for alias in aliases:
        a = _norm(alias)
        if a and (a in f["context"] or a in f["text"] or a in f["aria"] or a in f["title"]):
            alias_hits.append(a)
    if alias_hits:
        score += min(90, 28 * len(set(alias_hits)))
        evidence.extend(f"context:{a}" for a in sorted(set(alias_hits)))

    context_term_hits = [t for t in spec.context_terms if _norm(t) and _norm(t) in f["context"]]
    if context_term_hits:
        score += min(36, 12 * len(context_term_hits))
        evidence.extend(f"intent-context:{_norm(t)}" for t in context_term_hits)

    aria_expanded = str(candidate.get("aria_expanded") or "").lower()
    if canonical == "expand" and aria_expanded == "false":
        score += 85; evidence.append("aria-expanded:false")
    if canonical == "collapse" and aria_expanded == "true":
        score += 85; evidence.append("aria-expanded:true")

    # A raw plus glyph is too ambiguous unless it is locally scoped to the intended
    # repeatable section.  This is the key guard that prevents global toolbar + from
    # being mistaken for an inner row Add.
    raw_label = str(candidate.get("text") or candidate.get("aria_label") or candidate.get("title") or "").strip()
    icon_plus = any(x in f["icon"] for x in ("plus", "add"))
    if canonical == "add_row" and (raw_label == "+" or icon_plus):
        if alias_hits or context_term_hits:
            score += 48; evidence.append("scoped-plus")
        else:
            score -= 120; evidence.append("unscoped-plus-penalty")

    if not bool(candidate.get("visible", True)):
        score -= 500; evidence.append("not-visible")
    if not bool(candidate.get("enabled", True)):
        score -= 500; evidence.append("not-enabled")

    # Oversized click surfaces are usually containers, not actionable icon controls.
    try:
        width = float(candidate.get("width") or 0)
        height = float(candidate.get("height") or 0)
        if width > 420 or height > 140:
            score -= 80; evidence.append("oversized-surface")
    except Exception:
        pass

    accepted = score >= 70
    return {
        "accepted": accepted,
        "score": int(score),
        "intent": canonical,
        "mutation": spec.mutation,
        "expected_effect": spec.expected_effect,
        "evidence": evidence,
        "alias_hits": sorted(set(alias_hits)),
        "candidate": dict(candidate),
    }


async def inventory_actionable_affordances(
    page: Any, *, max_candidates: int = 240, within_surface_selector: str = ""
) -> List[Dict[str, Any]]:
    """Capture visible actionable elements without reading typed customer values.

    When ``within_surface_selector`` is supplied, candidates are restricted to that
    already-proven menu/popover/dialog surface. This is how detached Angular/CDK/DDS
    overlays inherit the provenance of the scoped control that opened them without
    pretending the detached overlay still contains the row/entity alias in its DOM
    ancestry.
    """
    js = r"""
({maxCandidates, withinSurfaceSelector}) => {
  const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
  const visible=el=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)>0};
  const stableId=id=>{id=String(id||'');if(!id||id.length>128)return false;if(/(?:^|[-_:])(dds|ng|mat|cdk|react|ember|generated)[-_:]?[a-z0-9_-]*\d{2,}/i.test(id))return false;if(/^[0-9a-f]{8}-[0-9a-f-]{20,}$/i.test(id))return false;if(/^[a-z_-]*\d{6,}$/i.test(id))return false;return true};
  const path=el=>{if(!el)return '';if(stableId(el.id))return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;const out=[];let n=el;for(let d=0;n&&d<9&&n.nodeType===1;d++,n=n.parentElement){let p=n.tagName.toLowerCase();const name=n.getAttribute('name');const testid=n.getAttribute('data-testid');if(name)p+=`[name="${CSS.escape(name)}"]`;else if(testid)p+=`[data-testid="${CSS.escape(testid)}"]`;else if(stableId(n.id))p+=`#${CSS.escape(n.id)}`;const par=n.parentElement;if(par){const same=Array.from(par.children).filter(x=>x.tagName===n.tagName);if(same.length>1)p+=`:nth-of-type(${same.indexOf(n)+1})`;}out.unshift(p);}return out.join(' > ')};
  const iconSig=el=>Array.from(el.querySelectorAll('svg,use,i,dds-icon,[class*=icon]')).map(x=>norm([
    x.getAttribute('name'),x.getAttribute('icon-name'),x.getAttribute('data-icon'),x.getAttribute('href'),x.getAttribute('xlink:href'),x.getAttribute('class'),x.getAttribute('aria-label'),x.getAttribute('title')
  ].filter(Boolean).join(' '))).filter(Boolean).join(' ').slice(0,500);
  const scope=withinSurfaceSelector ? document.querySelector(withinSurfaceSelector) : document;
  if(!scope)return [];
  const roots=Array.from(scope.querySelectorAll('button,a,[role=button],[role=menuitem],[role=option],[role=tab],dds-button,dds-link,[tabindex="0"]')).filter(visible).slice(0,Math.max(1,maxCandidates||240));
  return roots.map(el=>{
    const r=el.getBoundingClientRect();
    const local=el.closest('section,fieldset,table,tr,[role=row],[role=region],.dds__accordion-item,.dds__card,form,[role=dialog],dds-drawer,.dds__drawer')||el.parentElement;
    const heading=local?.querySelector('h1,h2,h3,h4,h5,legend,[role=heading]');
    const context=norm(`${heading?.textContent||''} ${local?.innerText||local?.textContent||''}`).slice(0,1800);
    return {
      selector:path(el), tag:(el.tagName||'').toLowerCase(), role:el.getAttribute('role')||'',
      text:norm(el.innerText||el.textContent||'').slice(0,300), aria_label:norm(el.getAttribute('aria-label')).slice(0,300), title:norm(el.getAttribute('title')).slice(0,300),
      icon_signature:iconSig(el), classes:norm(el.getAttribute('class')).slice(0,400), testid:norm(el.getAttribute('data-testid')).slice(0,200), href:norm(el.getAttribute('href')).slice(0,400),
      aria_expanded:el.getAttribute('aria-expanded'), aria_haspopup:el.getAttribute('aria-haspopup'),
      aria_controls:el.getAttribute('aria-controls')||'', aria_owns:el.getAttribute('aria-owns')||'',
      visible:true, enabled:!(el.disabled||el.getAttribute('aria-disabled')==='true'),
      width:Math.round(r.width), height:Math.round(r.height), x:Math.round(r.x), y:Math.round(r.y), context
    };
  });
}
"""
    try:
        rows = await page.evaluate(js, {"maxCandidates": int(max_candidates), "withinSurfaceSelector": str(within_surface_selector or "")})
        return [dict(x) for x in rows if isinstance(x, Mapping)] if isinstance(rows, list) else []
    except Exception:
        return []


async def resolve_semantic_affordance(
    page: Any,
    *,
    intent: str,
    aliases: Sequence[str] = (),
    allow_mutation: bool = False,
    max_candidates: int = 240,
    ambiguity_margin: int = 8,
    within_surface_selector: str = "",
) -> Dict[str, Any]:
    """Resolve one unambiguous actionable control for a semantic intent."""
    canonical = canonical_intent(intent)
    inventory = await inventory_actionable_affordances(
        page, max_candidates=max_candidates, within_surface_selector=within_surface_selector
    )
    ranked: List[Dict[str, Any]] = []
    for candidate in inventory:
        proof = rank_affordance_candidate(candidate, intent=canonical, aliases=aliases, allow_mutation=allow_mutation)
        if proof.get("accepted"):
            ranked.append(proof)
    ranked.sort(key=lambda x: int(x.get("score") or -999), reverse=True)
    if not ranked:
        return {"resolved": False, "intent": canonical, "reason": "no accepted semantic affordance candidate", "candidate_count": len(inventory)}
    top = ranked[0]
    if len(ranked) > 1 and int(top.get("score") or 0) - int(ranked[1].get("score") or 0) < int(ambiguity_margin):
        return {
            "resolved": False, "intent": canonical, "ambiguous": True,
            "reason": "top semantic affordance candidates are too close to choose safely",
            "candidates": ranked[:5],
        }
    candidate = dict(top.get("candidate") or {})
    return {
        "resolved": True,
        "intent": canonical,
        "selector": str(candidate.get("selector") or ""),
        "label": str(candidate.get("text") or candidate.get("aria_label") or candidate.get("title") or canonical),
        "expected_effect": top.get("expected_effect"),
        "mutation": bool(top.get("mutation")),
        "score": int(top.get("score") or 0),
        "evidence": list(top.get("evidence") or []),
        "alias_hits": list(top.get("alias_hits") or []),
        "candidate": candidate,
        "runner_up": ranked[1] if len(ranked) > 1 else None,
        "selector_generation_volatile": selector_looks_generation_volatile(str(candidate.get("selector") or "")),
        "within_surface_selector": str(within_surface_selector or ""),
    }


def status() -> Dict[str, Any]:
    return {
        "enabled": True,
        "schema_version": "hip.semantic-affordance.v2",
        "intents": sorted(SPECS),
        "icon_only_controls": True,
        "local_context_required_for_bare_plus": True,
        "ambiguity_fail_closed": True,
        "effect_verification_required": True,
        "mutation_governance_required": True,
        "compound_menu_intents": sorted(COMPOUND_MENU_INTENTS),
        "generation_safe_reresolution": True,
        "volatile_dynamic_id_avoidance": True,
        "detached_overlay_provenance_binding": True,
        "virtualized_surface_scrolling": True,
        "surface_scoped_target_resolution": True,
        "surface_continuity_lease": True,
        "stale_duplicate_surface_guard": True,
        "stable_target_double_read": True,
        "nested_surface_ancestry_chain": True,
        "surface_continuation_intents": sorted(SURFACE_CONTINUATION_INTENTS),
        "max_surface_chain_depth": MAX_SURFACE_CHAIN_DEPTH,
        "child_surface_provenance_inheritance": True,
    }


async def snapshot_affordance_surface(page: Any, *, selector: str = "") -> Dict[str, Any]:
    """Capture compact structural effect and active-overlay evidence.

    Overlay descriptors intentionally contain action/surface metadata rather than
    input values.  They let the runtime prove that a particular detached menu or
    popover appeared *after* a scoped opener was clicked.
    """
    js = r"""
({selector}) => {
  const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
  const visible=el=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)>0};
  const stableId=id=>{id=String(id||'');if(!id||id.length>128)return false;if(/(?:^|[-_:])(dds|ng|mat|cdk|react|ember|generated)[-_:]?[a-z0-9_-]*\d{2,}/i.test(id))return false;if(/^[0-9a-f]{8}-[0-9a-f-]{20,}$/i.test(id))return false;if(/^[a-z_-]*\d{6,}$/i.test(id))return false;return true};
  const path=el=>{if(!el)return '';if(stableId(el.id))return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;const out=[];let n=el;for(let d=0;n&&d<9&&n.nodeType===1;d++,n=n.parentElement){let p=n.tagName.toLowerCase();const testid=n.getAttribute('data-testid');const role=n.getAttribute('role');if(testid)p+=`[data-testid="${CSS.escape(testid)}"]`;else if(stableId(n.id))p+=`#${CSS.escape(n.id)}`;else if(role&&['menu','listbox','dialog'].includes(role))p+=`[role="${CSS.escape(role)}"]`;const par=n.parentElement;if(par){const same=Array.from(par.children).filter(x=>x.tagName===n.tagName);if(same.length>1)p+=`:nth-of-type(${same.indexOf(n)+1})`;}out.unshift(p);}return out.join(' > ')};
  const texts=sel=>Array.from(document.querySelectorAll(sel)).filter(visible).map(x=>norm(x.innerText||x.textContent||'').slice(0,180)).filter(Boolean).slice(0,30);
  const target=selector ? document.querySelector(selector) : null;
  const surfaceSelector='[role=menu],[role=listbox],.dds__menu,.dds__popover,[data-popper-placement],.mat-mdc-menu-panel,.mat-menu-panel,[role=dialog],dds-drawer,.dds__drawer,app-generic-drawer';
  const seen=new Set();
  const surfaces=[];
  for(const el of Array.from(document.querySelectorAll(surfaceSelector)).filter(visible)){
    const sel=path(el); if(!sel||seen.has(sel))continue; seen.add(sel);
    const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
    const role=el.getAttribute('role')||'';
    const kind=role|| (el.matches('dds-drawer,.dds__drawer,app-generic-drawer')?'drawer':el.matches('[role=dialog]')?'dialog':el.matches('[role=listbox]')?'listbox':'popover');
    const actions=Array.from(el.querySelectorAll('button,a,[role=button],[role=menuitem],[role=option],[tabindex="0"]')).filter(visible).map(x=>norm(x.innerText||x.textContent||x.getAttribute('aria-label')||x.getAttribute('title')||'')).filter(Boolean).slice(0,40);
    surfaces.push({
      selector:sel,id:el.id||'',role,kind,
      aria_label:norm(el.getAttribute('aria-label')),title:norm(el.getAttribute('title')),
      text:norm(el.innerText||el.textContent||'').slice(0,800),actions,
      item_count:actions.length,scroll_top:Math.round(el.scrollTop||0),scroll_height:Math.round(el.scrollHeight||0),client_height:Math.round(el.clientHeight||0),
      scrollable:(el.scrollHeight||0)>(el.clientHeight||0)+4,
      x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height),
      z_index:Number.parseInt(cs.zIndex||'0',10)||0
    });
  }
  return {
    url: location.href, title: document.title||'',
    dialogs: texts('[role=dialog],.dds__modal'),
    drawers: texts('dds-drawer,.dds__drawer,app-generic-drawer'),
    menus: texts('[role=menu],[role=listbox],.dds__menu,.dds__popover,[data-popper-placement],.mat-mdc-menu-panel,.mat-menu-panel'),
    headings: texts('h1,h2,h3,h4,legend,[role=heading]'),
    forms: Array.from(document.querySelectorAll('form')).filter(visible).length,
    expanded: Array.from(document.querySelectorAll('[aria-expanded=true]')).filter(visible).length,
    surfaces,
    target_exists: !!target,
    target_aria_expanded: target ? target.getAttribute('aria-expanded') : null,
    target_aria_pressed: target ? target.getAttribute('aria-pressed') : null,
  };
}
"""
    try:
        result = await page.evaluate(js, {"selector": selector})
        return dict(result) if isinstance(result, Mapping) else {}
    except Exception as exc:
        return {"error": mask_sensitive_string(str(exc))}


def bind_new_affordance_surface(
    *, before: Mapping[str, Any], after: Mapping[str, Any], opener_candidate: Mapping[str, Any] | None = None
) -> Dict[str, Any]:
    """Bind exactly one detached surface to the opener that caused it.

    Highest-confidence evidence is ``aria-controls``/``aria-owns`` from the scoped
    opener. Otherwise the surface must be genuinely new relative to the pre-click
    snapshot. Multiple equally plausible new surfaces fail closed.
    """
    opener = dict(opener_candidate or {})
    controlled = {str(x).strip() for x in (opener.get("aria_controls"), opener.get("aria_owns")) if str(x or "").strip()}
    before_surfaces = [dict(x) for x in (before.get("surfaces") or []) if isinstance(x, Mapping)]
    after_surfaces = [dict(x) for x in (after.get("surfaces") or []) if isinstance(x, Mapping)]
    before_keys = {(str(x.get("selector") or ""), str(x.get("id") or ""), str(x.get("kind") or "")) for x in before_surfaces}

    candidates: List[Dict[str, Any]] = []
    for surface in after_surfaces:
        sid = str(surface.get("id") or "")
        key = (str(surface.get("selector") or ""), sid, str(surface.get("kind") or ""))
        reason = ""
        confidence = 0
        if sid and sid in controlled:
            confidence = 100
            reason = "opener_aria_controls"
        elif key not in before_keys:
            # Prefer action-bearing menu/listbox/popover surfaces over incidental
            # dialogs/drawers when traversing an overflow action.
            kind = str(surface.get("kind") or "")
            confidence = 70 + (15 if kind in {"menu", "listbox", "popover"} else 0)
            confidence += min(10, int(surface.get("item_count") or 0))
            reason = "new_surface_delta"
        if confidence:
            candidates.append({"surface": surface, "confidence": confidence, "reason": reason})

    candidates.sort(key=lambda x: (int(x.get("confidence") or 0), int((x.get("surface") or {}).get("z_index") or 0)), reverse=True)
    if not candidates:
        return {"bound": False, "reason": "no newly opened actionable surface could be proven", "candidates": []}
    top = candidates[0]
    if len(candidates) > 1 and int(top["confidence"]) == int(candidates[1]["confidence"]):
        return {"bound": False, "ambiguous": True, "reason": "multiple new surfaces have equal provenance confidence", "candidates": candidates[:5]}
    surface = dict(top["surface"])
    selector = str(surface.get("selector") or "")
    if not selector:
        return {"bound": False, "reason": "proven surface has no usable selector", "candidate": top}
    surface_proof = {
        "selector": selector,
        "id": str(surface.get("id") or ""),
        "kind": str(surface.get("kind") or ""),
        "role": str(surface.get("role") or ""),
        "aria_label": str(surface.get("aria_label") or ""),
        "title": str(surface.get("title") or ""),
        "x": int(surface.get("x") or 0),
        "y": int(surface.get("y") or 0),
        "width": int(surface.get("width") or 0),
        "height": int(surface.get("height") or 0),
        "action_seed": [str(x) for x in (surface.get("actions") or [])[:12] if str(x).strip()],
        "opener_control_ids": sorted(controlled),
        "binding_reason": str(top["reason"]),
        "binding_confidence": int(top["confidence"]),
    }
    return {
        "bound": True, "selector": selector, "surface": surface,
        "confidence": int(top["confidence"]), "reason": str(top["reason"]),
        "opener_control_ids": sorted(controlled), "surface_proof": surface_proof,
    }



def bind_child_affordance_surface(
    *, parent_proof: Mapping[str, Any] | None, before: Mapping[str, Any],
    after: Mapping[str, Any], opener_candidate: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Bind a newly opened surface and carry forward parent provenance.

    A detached child dialog/drawer/menu often contains no entity-row text.  Trust is
    inherited only from the exact proven surface/action that caused the structural
    delta.  The ancestry record is bounded and contains structural identities only;
    customer-entered values are never stored.
    """
    parent = dict(parent_proof or {})
    bound = bind_new_affordance_surface(
        before=before, after=after, opener_candidate=opener_candidate,
    )
    if not bound.get("bound"):
        return bound

    parent_depth = int(parent.get("chain_depth") or 0)
    depth = parent_depth + 1 if parent else 1
    if depth > MAX_SURFACE_CHAIN_DEPTH:
        return {
            "bound": False,
            "reason": f"surface ancestry depth exceeds safety limit {MAX_SURFACE_CHAIN_DEPTH}",
            "max_depth": MAX_SURFACE_CHAIN_DEPTH,
            "candidate": bound,
        }

    ancestry = [dict(x) for x in (parent.get("ancestry") or []) if isinstance(x, Mapping)]
    if parent:
        ancestry.append({
            "selector": str(parent.get("selector") or ""),
            "id": str(parent.get("id") or ""),
            "kind": str(parent.get("kind") or ""),
            "role": str(parent.get("role") or ""),
            "chain_depth": parent_depth,
        })
    ancestry = ancestry[-MAX_SURFACE_CHAIN_DEPTH:]

    proof = dict(bound.get("surface_proof") or {})
    proof.update({
        "chain_depth": depth,
        "ancestry": ancestry,
        "parent_selector": str(parent.get("selector") or ""),
        "parent_id": str(parent.get("id") or ""),
        "parent_kind": str(parent.get("kind") or ""),
        "parent_role": str(parent.get("role") or ""),
        "parent_binding_confidence": int(parent.get("binding_confidence") or parent.get("last_rebind_score") or 0),
        "inherited_provenance": bool(parent),
    })
    out = dict(bound)
    out["surface_proof"] = proof
    out["chain_depth"] = depth
    out["ancestry"] = ancestry
    out["parent_proof"] = {
        "selector": str(parent.get("selector") or ""),
        "id": str(parent.get("id") or ""),
        "kind": str(parent.get("kind") or ""),
        "role": str(parent.get("role") or ""),
        "chain_depth": parent_depth,
    } if parent else None
    return out


def rebind_proven_affordance_surface(
    *, proof: Mapping[str, Any], snapshot: Mapping[str, Any], ambiguity_margin: int = 10
) -> Dict[str, Any]:
    """Re-prove a detached overlay after Angular/CDK may have replaced it.

    Provenance is a lease rather than a one-time selector decision. Exact opener
    controlled IDs are strongest. Otherwise we accept only one structurally
    continuous visible surface. Equal/near-equal replacements fail closed so a
    stale duplicate overlay can never silently inherit trust.
    """
    p = dict(proof or {})
    surfaces = [dict(x) for x in (snapshot.get("surfaces") or []) if isinstance(x, Mapping)]
    controlled = {str(x).strip() for x in (p.get("opener_control_ids") or []) if str(x).strip()}
    pid = str(p.get("id") or "").strip()
    pselector = str(p.get("selector") or "").strip()
    pkind = str(p.get("kind") or "").strip()
    prole = str(p.get("role") or "").strip()
    plabel = _norm(p.get("aria_label"))
    ptitle = _norm(p.get("title"))
    pactions = {_norm(x) for x in (p.get("action_seed") or []) if _norm(x)}

    ranked: List[Dict[str, Any]] = []
    for surface in surfaces:
        sid = str(surface.get("id") or "").strip()
        selector = str(surface.get("selector") or "").strip()
        kind = str(surface.get("kind") or "").strip()
        role = str(surface.get("role") or "").strip()
        score = 0
        evidence: List[str] = []
        if sid and sid in controlled:
            score += 140; evidence.append("opener_controlled_id")
        elif pid and sid == pid:
            score += 110; evidence.append("same_surface_id")
        if pselector and selector == pselector:
            score += 25; evidence.append("same_selector_hint")
        if pkind and kind == pkind:
            score += 28; evidence.append("same_kind")
        if prole and role == prole:
            score += 14; evidence.append("same_role")
        if plabel and _norm(surface.get("aria_label")) == plabel:
            score += 30; evidence.append("same_aria_label")
        if ptitle and _norm(surface.get("title")) == ptitle:
            score += 24; evidence.append("same_title")
        actions = {_norm(x) for x in (surface.get("actions") or []) if _norm(x)}
        overlap = len(pactions & actions)
        if overlap:
            score += min(24, overlap * 6); evidence.append(f"action_overlap:{overlap}")
        try:
            distance = abs(int(surface.get("x") or 0) - int(p.get("x") or 0)) + abs(int(surface.get("y") or 0) - int(p.get("y") or 0))
            size_delta = abs(int(surface.get("width") or 0) - int(p.get("width") or 0)) + abs(int(surface.get("height") or 0) - int(p.get("height") or 0))
            if distance <= 24:
                score += 18; evidence.append("same_anchor_geometry")
            elif distance <= 80:
                score += 8; evidence.append("near_anchor_geometry")
            if size_delta <= 32:
                score += 12; evidence.append("same_surface_size")
        except Exception:
            pass
        if score:
            ranked.append({"surface": surface, "score": score, "evidence": evidence})

    ranked.sort(key=lambda x: (int(x.get("score") or 0), int((x.get("surface") or {}).get("z_index") or 0)), reverse=True)
    if not ranked:
        return {"rebound": False, "reason": "proven surface is no longer visible", "candidates": []}
    top = ranked[0]
    top_score = int(top.get("score") or 0)
    if top_score < 55:
        return {"rebound": False, "reason": "no visible surface has sufficient provenance continuity", "candidates": ranked[:5]}
    if len(ranked) > 1 and top_score - int(ranked[1].get("score") or 0) < int(ambiguity_margin):
        return {"rebound": False, "ambiguous": True, "reason": "multiple visible surfaces match the provenance lease", "candidates": ranked[:5]}
    surface = dict(top.get("surface") or {})
    selector = str(surface.get("selector") or "")
    if not selector:
        return {"rebound": False, "reason": "rebound surface has no usable selector", "candidate": top}
    updated = dict(p)
    updated.update({
        "selector": selector, "id": str(surface.get("id") or p.get("id") or ""),
        "kind": str(surface.get("kind") or p.get("kind") or ""),
        "role": str(surface.get("role") or p.get("role") or ""),
        "aria_label": str(surface.get("aria_label") or p.get("aria_label") or ""),
        "title": str(surface.get("title") or p.get("title") or ""),
        "x": int(surface.get("x") or 0), "y": int(surface.get("y") or 0),
        "width": int(surface.get("width") or 0), "height": int(surface.get("height") or 0),
        "last_rebind_score": top_score,
    })
    return {
        "rebound": True, "selector": selector, "surface": surface, "surface_proof": updated,
        "score": top_score, "evidence": list(top.get("evidence") or []),
    }


async def _refresh_surface_lease(page: Any, proof: Mapping[str, Any]) -> Dict[str, Any]:
    snapshot = await snapshot_affordance_surface(page)
    rebound = rebind_proven_affordance_surface(proof=proof, snapshot=snapshot)
    rebound["snapshot"] = snapshot
    return rebound


async def refresh_affordance_surface_lease(page: Any, proof: Mapping[str, Any]) -> Dict[str, Any]:
    """Public lease refresh used by BrowserSession's nested-surface stack."""
    return await _refresh_surface_lease(page, proof)


async def resolve_affordance_in_proven_surface(
    page: Any, *, intent: str, surface_selector: str = "", allow_mutation: bool = False,
    max_scrolls: int = 8, max_candidates: int = 240, surface_proof: Mapping[str, Any] | None = None,
    stable_reads: int = 1, settle_ms: int = 70
) -> Dict[str, Any]:
    """Resolve an action only inside a continuously re-proven overlay.

    The surface selector is never trusted as a permanent identity. Before every
    semantic read and every scroll, the provenance lease is refreshed against the
    currently visible overlays. A destroyed/recreated CDK/DDS surface may be
    rebound only when continuity is unique. Once a target appears, callers may
    require multiple consistent reads before the action is returned for clicking.
    """
    initial_selector = str(surface_selector or "")
    lease_mode = bool(surface_proof)
    proof: Dict[str, Any] = dict(surface_proof or {})
    if not proof:
        if not initial_selector:
            return {"resolved": False, "reason": "surface selector unavailable", "intent": canonical_intent(intent)}
        proof = {"selector": initial_selector}
    elif initial_selector and not proof.get("selector"):
        proof["selector"] = initial_selector

    attempts: List[Dict[str, Any]] = []
    consecutive = 0
    scroll_count = 0
    last_identity: tuple[str, str, str] | None = None
    required_reads = max(1, int(stable_reads))

    max_iterations = max(1, int(max_scrolls) + required_reads + 1)
    for index in range(max_iterations):
        if lease_mode:
            lease = await _refresh_surface_lease(page, proof)
            if not lease.get("rebound"):
                return {
                    "resolved": False, "intent": canonical_intent(intent),
                    "reason": f"proven surface continuity lost: {lease.get('reason') or 'unresolved'}",
                    "surface_provenance": proof, "surface_rebind": lease, "attempts": attempts,
                }
            proof = dict(lease.get("surface_proof") or proof)
            selector = str(lease.get("selector") or proof.get("selector") or "")
        else:
            lease = {"rebound": True, "selector": str(proof.get("selector") or initial_selector), "score": None}
            selector = str(proof.get("selector") or initial_selector)
        if not selector:
            return {"resolved": False, "reason": "re-proven surface has no selector", "intent": canonical_intent(intent)}

        resolved = await resolve_semantic_affordance(
            page, intent=intent, aliases=(), allow_mutation=allow_mutation,
            max_candidates=max_candidates, within_surface_selector=selector,
        )
        attempt: Dict[str, Any] = {
            "index": index, "resolved": bool(resolved.get("resolved")),
            "reason": resolved.get("reason"), "surface_selector": selector,
            "surface_rebind_score": lease.get("score"),
        }
        attempts.append(attempt)
        if resolved.get("resolved"):
            identity = (
                canonical_intent(intent), _norm(resolved.get("label")),
                _norm((resolved.get("candidate") or {}).get("icon")),
            )
            consecutive = consecutive + 1 if identity == last_identity else 1
            last_identity = identity
            attempt["stable_read"] = consecutive
            if consecutive >= required_reads:
                resolved["surface_provenance"] = dict(proof)
                resolved["surface_provenance"].update({
                    "selector": selector, "scroll_attempts": scroll_count,
                    "stable_reads": consecutive, "lease_revalidated": lease_mode,
                })
                resolved["virtualized_surface_scrolling"] = scroll_count > 0
                resolved["surface_rebind_history"] = attempts
                return resolved
            try:
                await page.wait_for_timeout(max(0, int(settle_ms)))
            except Exception:
                pass
            # Stable target confirmation is a reread, not a scroll.
            continue

        consecutive = 0
        last_identity = None
        if scroll_count >= int(max_scrolls):
            break

        # Re-prove immediately before scrolling so a lazy-load replacement cannot
        # transfer the scroll to a stale/global duplicate surface.
        if lease_mode:
            pre_scroll = await _refresh_surface_lease(page, proof)
            attempt["pre_scroll_rebind"] = {
                "rebound": bool(pre_scroll.get("rebound")), "reason": pre_scroll.get("reason"),
                "score": pre_scroll.get("score"), "selector": pre_scroll.get("selector"),
            }
            if not pre_scroll.get("rebound"):
                break
            proof = dict(pre_scroll.get("surface_proof") or proof)
            selector = str(pre_scroll.get("selector") or proof.get("selector") or selector)
        try:
            moved = await page.evaluate(r"""
({selector}) => {
  const all=Array.from(document.querySelectorAll(selector));
  const visible=all.filter(el=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'});
  if(visible.length!==1)return {moved:false,reason:visible.length?'surface_ambiguous':'surface_missing',visible_count:visible.length};
  const el=visible[0]; const max=Math.max(0,(el.scrollHeight||0)-(el.clientHeight||0));
  if(max<=0)return {moved:false,reason:'not_scrollable'};
  const before=el.scrollTop||0; const step=Math.max(48,Math.floor((el.clientHeight||160)*0.72));
  el.scrollTop=Math.min(max,before+step); el.dispatchEvent(new Event('scroll',{bubbles:true}));
  return {moved:(el.scrollTop||0)>before,before,after:el.scrollTop||0,max};
}
""", {"selector": selector})
        except Exception as exc:
            moved = {"moved": False, "reason": mask_sensitive_string(str(exc))}
        attempt["scroll"] = moved
        if not isinstance(moved, Mapping) or not moved.get("moved"):
            break
        scroll_count += 1
        try:
            await page.wait_for_timeout(90)
        except Exception:
            pass

    return {
        "resolved": False, "intent": canonical_intent(intent),
        "reason": "target affordance not stably resolved inside continuously proven surface after bounded traversal",
        "within_surface_selector": str(proof.get("selector") or initial_selector),
        "surface_provenance": proof, "attempts": attempts,
    }


async def verify_affordance_target_membership(
    page: Any, *, target_selector: str, surface_proof: Mapping[str, Any]
) -> Dict[str, Any]:
    """Final fail-closed guard immediately before a surface-scoped click.

    The proven surface is rebound one last time and the target must have exactly one
    visible match contained by that exact surface. This prevents a global duplicate
    action from inheriting a stale target selector during an async overlay rewrite.
    """
    lease = await _refresh_surface_lease(page, surface_proof)
    if not lease.get("rebound"):
        return {"pass": False, "reason": f"surface continuity lost before click: {lease.get('reason') or 'unresolved'}", "surface_rebind": lease}
    selector = str(lease.get("selector") or "")
    target = str(target_selector or "")
    if not selector or not target:
        return {"pass": False, "reason": "surface or target selector unavailable before click", "surface_rebind": lease}
    try:
        membership = await page.evaluate(r"""
({surfaceSelector,targetSelector}) => {
  const visible=el=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)>0};
  const surfaces=Array.from(document.querySelectorAll(surfaceSelector)).filter(visible);
  const targets=Array.from(document.querySelectorAll(targetSelector)).filter(visible);
  if(surfaces.length!==1)return {pass:false,reason:'surface_not_unique',surface_count:surfaces.length,target_count:targets.length};
  const inside=targets.filter(t=>surfaces[0].contains(t));
  return {pass:targets.length===1&&inside.length===1,reason:(targets.length===1&&inside.length===1)?'target_inside_proven_surface':'target_not_unique_inside_surface',surface_count:1,target_count:targets.length,inside_count:inside.length};
}
""", {"surfaceSelector": selector, "targetSelector": target})
    except Exception as exc:
        membership = {"pass": False, "reason": mask_sensitive_string(str(exc))}
    return {
        "pass": bool(isinstance(membership, Mapping) and membership.get("pass")),
        "reason": (membership or {}).get("reason") if isinstance(membership, Mapping) else "membership_check_failed",
        "membership": membership, "surface_selector": selector,
        "surface_proof": dict(lease.get("surface_proof") or surface_proof),
        "surface_rebind": {k: v for k, v in lease.items() if k != "snapshot"},
    }


def verify_affordance_effect(
    *,
    intent: str,
    expected_effect: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> Dict[str, Any]:
    """Verify structural effects for generalized icon/button actions.

    Repeatable ``add_row`` uses the stronger exact row-count validator in
    :mod:`repeatable_rows`; this generic verifier covers expansion/menu/edit/navigation
    actions used by the adaptive/future-task layer.
    """
    canonical = canonical_intent(intent)
    checks: Dict[str, Any] = {}
    before_url, after_url = str(before.get("url") or ""), str(after.get("url") or "")
    checks["url_changed"] = bool(before_url and after_url and before_url != after_url)
    checks["dialog_delta"] = len(after.get("dialogs") or []) - len(before.get("dialogs") or [])
    checks["drawer_delta"] = len(after.get("drawers") or []) - len(before.get("drawers") or [])
    checks["menu_delta"] = len(after.get("menus") or []) - len(before.get("menus") or [])
    checks["heading_changed"] = list(before.get("headings") or []) != list(after.get("headings") or [])
    checks["expanded_delta"] = int(after.get("expanded") or 0) - int(before.get("expanded") or 0)
    checks["target_expanded"] = str(after.get("target_aria_expanded") or "").lower() == "true"
    checks["target_collapsed"] = str(after.get("target_aria_expanded") or "").lower() == "false"
    before_surfaces = set(str(x).strip().lower() for x in (list(before.get("headings") or []) + list(before.get("dialogs") or []) + list(before.get("drawers") or []) + list(before.get("menus") or [])) if str(x).strip())
    after_surfaces = set(str(x).strip().lower() for x in (list(after.get("headings") or []) + list(after.get("dialogs") or []) + list(after.get("drawers") or []) + list(after.get("menus") or [])) if str(x).strip())
    new_surfaces = sorted(after_surfaces - before_surfaces)
    checks["new_surfaces"] = new_surfaces[:20]

    if canonical == "expand":
        passed = checks["target_expanded"] or checks["expanded_delta"] > 0 or checks["drawer_delta"] > 0 or checks["dialog_delta"] > 0 or checks["heading_changed"]
    elif canonical == "collapse":
        passed = checks["target_collapsed"] or checks["expanded_delta"] < 0 or checks["drawer_delta"] < 0 or checks["dialog_delta"] < 0
    elif canonical == "more_actions":
        passed = checks["menu_delta"] > 0 or checks["dialog_delta"] > 0 or checks["drawer_delta"] > 0
    elif canonical in {"open_add_form", "edit", "clone", "migrate", "deploy", "delete", "upload", "filter", "settings"}:
        # Do not accept text that was already present before the click. A row may
        # already contain the word "Edit" or "Deploy"; only a newly opened/changed
        # surface, URL transition, or newly introduced semantic heading is proof.
        semantic_terms = set(_tokens(canonical)) | set(_tokens(expected_effect))
        new_surface_text = " ".join(new_surfaces)
        semantic_delta = any(term and term in new_surface_text for term in semantic_terms)
        passed = checks["url_changed"] or checks["dialog_delta"] > 0 or checks["drawer_delta"] > 0 or semantic_delta
    elif canonical in {"next", "back"}:
        passed = checks["url_changed"] or checks["heading_changed"] or checks["dialog_delta"] != 0 or checks["drawer_delta"] != 0
    elif canonical == "close":
        passed = checks["dialog_delta"] < 0 or checks["drawer_delta"] < 0 or checks["menu_delta"] < 0
    else:
        passed = checks["url_changed"] or checks["heading_changed"] or checks["dialog_delta"] != 0 or checks["drawer_delta"] != 0 or checks["menu_delta"] != 0 or checks["expanded_delta"] != 0
    return {"pass": bool(passed), "intent": canonical, "expected_effect": expected_effect, "checks": checks}
