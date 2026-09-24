from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, Iterable, List, Sequence

from playwright.async_api import Locator, Page

from .security import mask_sensitive_data, mask_sensitive_string

MUTATING_WORDS = {
    "save", "create", "submit", "delete", "deploy", "publish", "update", "confirm", "remove"
}

FORM_INTERACTION_RULES: List[Dict[str, Any]] = [
    {
        "id": "structural-parent-first",
        "rule": "When a target field is hidden or missing, inspect and reveal its structural parent before resolving the child.",
        "mandatory": True,
    },
    {
        "id": "dependency-dag-scheduler",
        "rule": "Compile the current input, live form knowledge and judge-validated memory into a dependency DAG; execute only nodes whose parents have committed exact values.",
        "mandatory": True,
    },
    {
        "id": "parent-value-commit-before-child",
        "rule": "A child control is eligible only after its expected parent value is exact, stable, event-proven and all parent-triggered loading has settled.",
        "mandatory": True,
    },
    {
        "id": "sequential-repeatable-row-transaction",
        "rule": "Complete and verify every required field in repeatable row N before creating or filling row N+1; row reuse and interleaved row mutation are forbidden.",
        "mandatory": True,
    },
    {
        "id": "explicit-widget-events",
        "rule": "Radio buttons, switches, dropdowns and multi-select options must be activated through explicit click/check actions that trigger portal JavaScript listeners; raw DOM value assignment is forbidden.",
        "mandatory": True,
    },
    {
        "id": "multi-select-exact-set",
        "rule": "Multi-select controls must finish with the exact normalized expected set: every requested value selected, no extra values, no duplicates, and selected-count evidence consistent with the set.",
        "mandatory": True,
    },
    {
        "id": "multi-select-additive-preservation",
        "rule": "While adding a multi-select option, all previously committed selections must remain selected; a click that removes another requested value is a failed transaction.",
        "mandatory": True,
    },
    {
        "id": "multi-select-safe-removal",
        "rule": "Unexpected multi-select values may be removed only by explicitly clicking the exact selected option or its exact remove control; Backspace/Delete on an empty search input and bulk clear are forbidden.",
        "mandatory": True,
    },
    {
        "id": "multi-select-select-all-guard",
        "rule": "Select All is forbidden unless the desired set is proven equal to the complete, fully loaded, enabled option universe; explicit item selection is the default.",
        "mandatory": True,
    },
    {
        "id": "typeahead-option-commit",
        "rule": "Typing into a searchable dropdown/typeahead is only a query; the value is committed only after clicking one unique exact option and verifying the collapsed control state.",
        "mandatory": True,
    },
    {
        "id": "virtualized-option-exact-match",
        "rule": "Lazy or virtualized option lists must be searched and scrolled until one exact normalized option is uniquely visible; partial contains-matches and blind Enter commits are forbidden.",
        "mandatory": True,
    },
    {
        "id": "radio-group-exclusivity",
        "rule": "Radio actions must prove the intended option is selected and every other option in the same semantic group is unselected.",
        "mandatory": True,
    },
    {
        "id": "checkbox-exact-state",
        "rule": "Checkboxes and switches must be changed with click/check actions and verified from checked/aria-checked state; direct property assignment is forbidden.",
        "mandatory": True,
    },
    {
        "id": "dependent-selection-reset-awareness",
        "rule": "When a parent selection changes, detect child resets or option-universe changes and rebind children only after the parent transaction is stable.",
        "mandatory": True,
    },
    {
        "id": "option-loading-complete",
        "rule": "Do not decide that an option is missing while the list is loading, filtering, paginating or reporting an incomplete aria-setsize; wait for a stable option universe first.",
        "mandatory": True,
    },
    {
        "id": "focus-containment",
        "rule": "Before keystrokes, prove focus belongs to the intended control; after each commit, verify focus or popup ownership did not move to another row or field.",
        "mandatory": True,
    },
    {
        "id": "file-upload-completion",
        "rule": "File upload succeeds only after set_input_files plus filename/size evidence and completion of any visible upload or validation indicator.",
        "mandatory": True,
    },
    {
        "id": "tab-accordion-activation",
        "rule": "Tabs and accordions must be activated explicitly and verified through aria-selected/aria-expanded plus visibility of the owned panel before child interaction.",
        "mandatory": True,
    },
    {
        "id": "repeatable-row-semantic-identity",
        "rule": "Repeatable rows are identified by section, row kind and committed semantic content, not only current DOM position; rerendered or reordered rows must be rebound.",
        "mandatory": True,
    },
    {
        "id": "read-only-generated-value-protection",
        "rule": "Portal-generated read-only values such as Version are verification-only and must remain unchanged throughout all later actions.",
        "mandatory": True,
    },
    {
        "id": "parent-child-visibility-gate",
        "rule": "After a parent action, wait for each eligible conditional child to become visible and uniquely bindable before any child read/write.",
        "mandatory": True,
    },
    {
        "id": "animation-stability",
        "rule": "Before input, wait until the target bounding box and computed animation state are stable.",
        "mandatory": True,
    },
    {
        "id": "active-surface-scope",
        "rule": "Resolve and interact only inside the authoritative active form/drawer/tab surface.",
        "mandatory": True,
    },
    {
        "id": "hit-test-before-action",
        "rule": "The target center must pass elementFromPoint hit-testing; intercepted controls are not actionable.",
        "mandatory": True,
    },
    {
        "id": "stale-node-rebind",
        "rule": "After any Angular/DDS rerender, reacquire the control by semantic identity instead of reusing a stale generated selector.",
        "mandatory": True,
    },
    {
        "id": "event-and-state-proof",
        "rule": "An action succeeds only when an explicit browser event or portal state mutation is observed and the exact expected value is stable.",
        "mandatory": True,
    },
    {
        "id": "state-proof-over-transport-timeout",
        "rule": "When a browser/MCP click times out after dispatch, re-probe the owned control and accept success only if the exact portal state is already committed; never repeat a successful action blindly.",
        "mandatory": True,
    },
    {
        "id": "committed-field-protection",
        "rule": "Previously committed fields must remain unchanged after every later action.",
        "mandatory": True,
    },
    {
        "id": "validation-gate",
        "rule": "Do not advance while the target control or its field group has a blocking invalid/error state.",
        "mandatory": True,
    },
    {
        "id": "repeatable-row-effect",
        "rule": "A row-level +Add action is valid only when the intended row count increases by exactly one.",
        "mandatory": True,
    },
    {
        "id": "learn-once-replay-fast",
        "rule": "After a structure fingerprint and bindings are judge-validated, use cached semantic bindings and adaptive short waits; do not re-explore known branches unless drift is detected.",
        "mandatory": True,
    },
]

DEFAULT_FORM_INTERACTION_POLICY: Dict[str, Any] = {
    "schema_version": "hip.form-interaction-policy.v2",
    "rules": FORM_INTERACTION_RULES,
    "widget_contracts": {
        "multi_select": {
            "comparison": "order-insensitive exact normalized set",
            "preserve_existing_requested_values": True,
            "remove_extras_by_explicit_exact_click_only": True,
            "forbid_empty_search_backspace": True,
            "forbid_bulk_clear": True,
            "select_all_default": "forbidden",
            "reopen_for_authoritative_verification": True,
            "require_multiple_selection_mode": True,
            "require_selected_count_match": True,
            "require_stable_option_universe": True,
        },
        "typeahead": {
            "typed_text_is_query_not_value": True,
            "exact_unique_option_click_required": True,
            "blind_enter_forbidden": True,
            "collapsed_state_verification_required": True,
        },
        "radio_group": {
            "explicit_click_required": True,
            "exactly_one_selected": True,
            "same_group_exclusivity_required": True,
        },
        "checkbox_or_switch": {
            "explicit_click_or_check_required": True,
            "direct_checked_assignment_forbidden": True,
            "checked_and_aria_checked_verification": True,
        },
        "file_upload": {
            "set_input_files_required": True,
            "filename_and_size_verification": True,
            "upload_completion_indicator_required_when_present": True,
        },
    },
    "learning_mode": {
        "poll_interval_ms": 160,
        "transaction_timeout_ms": 6500,
        "bbox_stable_samples": 3,
        "bbox_tolerance_px": 0.75,
        "child_visibility_timeout_ms": 5000,
        "post_commit_generation_timeout_ms": 6500,
        "post_commit_generation_stable_samples": 3,
        "minimum_binding_margin": 14,
    },
    "validated_fast_replay_mode": {
        "poll_interval_ms": 70,
        "transaction_timeout_ms": 3500,
        "bbox_stable_samples": 2,
        "bbox_tolerance_px": 0.75,
        "child_visibility_timeout_ms": 2600,
        "post_commit_generation_timeout_ms": 3500,
        "post_commit_generation_stable_samples": 2,
        "minimum_binding_margin": 24,
        "skip_known_branch_exploration": True,
    },
}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def derive_execution_profile(initial_model: Dict[str, Any], graph: Dict[str, Any]) -> Dict[str, Any]:
    """Choose safe learning or fast replay from the live one-to-one model.

    Fast replay is enabled only when the visible structure is unambiguous, every
    currently resolvable binding has a strong margin, and a structure fingerprint
    exists. Conditional children may remain deferred until their parent is clicked.
    """
    bindings = initial_model.get("bindings") if isinstance(initial_model.get("bindings"), list) else []
    resolved = [b for b in bindings if isinstance(b, dict) and b.get("status") == "resolved"]
    margins = [
        int(((b.get("binding") or {}).get("score_margin") or 0))
        for b in resolved
        if isinstance(b.get("binding"), dict)
    ]
    strong = [m for m in margins if m >= 24]
    memory_match = graph.get("flow_pattern_memory_match") if isinstance(graph.get("flow_pattern_memory_match"), dict) else {}
    memory_validated = bool(memory_match.get("validated_match"))
    live_has_ambiguity = bool(initial_model.get("ambiguous_nodes") or initial_model.get("duplicate_bindings"))
    memory_live_safe = bool(memory_validated and not live_has_ambiguity and all(m >= 14 for m in margins))
    explicit_validated = bool(
        graph.get("judge_validated_structure")
        or graph.get("validated_structure_fingerprint")
        or graph.get("replay_blueprint_validated")
        or graph.get("validated_replay")
        or memory_live_safe
    )
    live_structure_valid = bool(
        initial_model.get("one_to_one_pass")
        and initial_model.get("structure_fingerprint")
        and not initial_model.get("ambiguous_nodes")
        and not initial_model.get("duplicate_bindings")
        and resolved
        and len(strong) == len(margins)
    )
    fast = bool(explicit_validated or live_structure_valid)
    profile_key = "validated_fast_replay_mode" if fast else "learning_mode"
    profile = dict(DEFAULT_FORM_INTERACTION_POLICY[profile_key])
    return {
        "mode": "validated_fast_replay" if fast else "learning",
        "profile": profile,
        "explicit_validated_knowledge": explicit_validated,
        "validated_flow_pattern_memory": memory_validated,
        "flow_pattern_memory_live_safe": memory_live_safe,
        "flow_pattern_memory_match": memory_match,
        "live_structure_valid": live_structure_valid,
        "resolved_binding_count": len(resolved),
        "strong_binding_count": len(strong),
        "minimum_observed_margin": min(margins) if margins else None,
        "structure_fingerprint": initial_model.get("structure_fingerprint"),
        "policy_schema_version": DEFAULT_FORM_INTERACTION_POLICY["schema_version"],
    }


async def inspect_interaction_state(page: Page, selector: str) -> Dict[str, Any]:
    if not selector:
        return {"exists": False, "visible": False, "stable_candidate": False, "reason": "empty selector"}
    try:
        result = await page.evaluate(
            r"""
(selector) => {
  function css(el){
    if(!el||!el.tagName)return '';
    if(el.id)return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;
    const p=[];let n=el;
    while(n&&n.nodeType===1&&p.length<9){
      let x=n.tagName.toLowerCase();const par=n.parentElement;
      if(par){const same=Array.from(par.children).filter(y=>y.tagName===n.tagName);if(same.length>1)x+=`:nth-of-type(${same.indexOf(n)+1})`;}
      p.unshift(x);n=par;
    }
    return p.join(' > ');
  }
  const el=document.querySelector(selector);
  if(!el)return {exists:false,visible:false,reason:'not-found'};
  const host=el.closest('label,.dds__radio-button,.dds__checkbox,[role=radio],[role=checkbox]')||el;
  const r=host.getBoundingClientRect();const s=getComputedStyle(host);
  const visible=!!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||'1')!==0);
  const cx=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2));
  const cy=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));
  const hit=visible?document.elementFromPoint(cx,cy):null;
  const hitPass=!!(hit&&(hit===host||host.contains(hit)||hit.contains(host)||hit===el||el.contains(hit)||hit.contains(el)));
  const animations=Array.from(host.getAnimations?host.getAnimations({subtree:false}):[]).map(a=>({playState:a.playState,currentTime:a.currentTime}));
  const runningAnimation=animations.some(a=>a.playState==='running'||a.playState==='pending');
  const transitionMs=(s.transitionDuration||'').split(',').reduce((m,v)=>Math.max(m,parseFloat(v)||0),0)*1000;
  const animationMs=(s.animationDuration||'').split(',').reduce((m,v)=>Math.max(m,parseFloat(v)||0),0)*1000;
  const blockingValidation=el.getAttribute('aria-invalid')==='true'||!!el.closest('.dds__form-group--invalid,.dds__error,[class*=error]');
  // Field-owned validation text (aria-describedby or the field's own form group)
  // so callers can tell "object already exists" apart from a real input error.
  const msgs=[];
  const seeText=n=>{if(!n)return;const st=getComputedStyle(n);if(st.display==='none'||st.visibility==='hidden')return;const t=(n.innerText||n.textContent||'').replace(/\s+/g,' ').trim();if(t&&t.length<=300&&!msgs.includes(t))msgs.push(t);};
  for(const id of (el.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean)) seeText(document.getElementById(id));
  if(blockingValidation){
    const group=el.closest('.dds__form-group,.dds__input-text__container,dds-input,dds-textarea,[class*=form-field],[class*=field-container]')||el.parentElement;
    if(group) group.querySelectorAll('.dds__invalid-feedback,.dds__error-text,.dds__form__field__error,[class*=error-text],[class*=error-message],[role=alert]').forEach(seeText);
    if(!msgs.length&&group){const gt=(group.innerText||'').replace(/\s+/g,' ');const m=gt.match(/[^.|]{0,120}already exists?[^.|]{0,80}/i);if(m)msgs.push(m[0].trim());}
  }
  return {
    exists:true,visible,disabled:!!(el.disabled||el.getAttribute('aria-disabled')==='true'),readonly:!!el.readOnly,
    bbox:{x:r.x,y:r.y,width:r.width,height:r.height},pointerEvents:s.pointerEvents||'',
    hitTestPass:hitPass,hitSelector:hit?css(hit):'',runningAnimation,transitionMs,animationMs,
    ariaInvalid:el.getAttribute('aria-invalid')||'',
    blockingValidation,
    validationMessage:msgs.join(' | ').slice(0,600),
  };
}
""",
            selector,
        )
        return mask_sensitive_data(result or {})
    except Exception as exc:
        return {"exists": False, "visible": False, "reason": mask_sensitive_string(str(exc))}


_SCROLL_INTO_VIEW_JS = r"""
(el) => {
  if(!el||!el.getBoundingClientRect)return {scrolled:false,in_view:false,reason:'not-found'};
  const host=el.closest('label,.dds__radio-button,.dds__checkbox,[role=radio],[role=checkbox]')||el;
  const reachable=()=>{
    const r=host.getBoundingClientRect();
    if(!(r.width&&r.height))return false;
    const cx=r.left+r.width/2,cy=r.top+r.height/2;
    if(cx<0||cy<0||cx>=innerWidth||cy>=innerHeight)return false;
    const h=document.elementFromPoint(cx,cy);
    return !!(h&&(h===host||host.contains(h)||h.contains(host)||h===el||el.contains(h)||h.contains(el)));
  };
  if(reachable())return {scrolled:false,in_view:true};
  // 'instant' overrides the portal's CSS scroll-behavior:smooth, so the target
  // does not keep moving while its geometry is sampled.
  try{host.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});}
  catch(e){try{host.scrollIntoView(true);}catch(_){}}
  return {scrolled:true,in_view:reachable()};
}
"""


async def scroll_control_into_view(page: Page, selector: str) -> Dict[str, Any]:
    """Bring a below-the-fold or sticky-covered control to the viewport centre.

    The hit test below uses ``elementFromPoint``, which cannot see a control that
    is outside the viewport.  Long forms (Document Type identifier rows,
    attributes and validation) are mostly below the fold, so without this every
    click on them was reported as "target center intercepted".
    """
    if not selector:
        return {"scrolled": False, "in_view": False, "reason": "empty selector"}
    try:
        result = await page.evaluate(
            "(args) => { const fn = " + _SCROLL_INTO_VIEW_JS + "; return fn(document.querySelector(args.selector)); }",
            {"selector": selector},
        )
        return dict(result or {})
    except Exception as exc:
        return {"scrolled": False, "in_view": False, "reason": mask_sensitive_string(str(exc))}


async def scroll_locator_into_view(locator: Locator) -> Dict[str, Any]:
    try:
        return dict(await locator.evaluate(_SCROLL_INTO_VIEW_JS) or {})
    except Exception as exc:
        return {"scrolled": False, "in_view": False, "reason": mask_sensitive_string(str(exc))}


async def wait_for_stable_bounding_box(
    page: Page,
    selector: str,
    *,
    timeout_ms: int = 3500,
    stable_samples: int = 2,
    interval_ms: int = 70,
    tolerance_px: float = 0.75,
) -> Dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + max(0.2, timeout_ms / 1000.0)
    previous: Dict[str, Any] | None = None
    count = 0
    samples = 0
    last: Dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        samples += 1
        last = await inspect_interaction_state(page, selector)
        box = last.get("bbox") if isinstance(last.get("bbox"), dict) else None
        usable = bool(last.get("exists") and last.get("visible") and box and not last.get("runningAnimation"))
        if usable and previous:
            stable = all(abs(float(box.get(k, 0)) - float(previous.get(k, 0))) <= tolerance_px for k in ("x", "y", "width", "height"))
            count = count + 1 if stable else 1
        elif usable:
            count = 1
        else:
            count = 0
        previous = dict(box) if box else None
        if count >= stable_samples:
            return {"stable": True, "samples": samples, "consecutive_samples": count, "state": last}
        await page.wait_for_timeout(interval_ms)
    return {"stable": False, "samples": samples, "consecutive_samples": count, "state": last}




async def _semantic_structural_click(page: Page, selector: str, *, label: str) -> Dict[str, Any]:
    """Open one safe structural parent through AutoWebGLM + Layer-11 + Playwright MCP."""
    loc = page.locator(selector).first
    session = getattr(page, "_hip_browser_session", None)
    resolution: Dict[str, Any] = {"pass": True, "status": "session_unavailable"}
    revalidation: Dict[str, Any] = {"pass": True, "status": "not_required"}
    try:
        if session is not None and hasattr(session, "_semantic_action_preflight"):
            resolution = await session._semantic_action_preflight(
                action="click", locator=loc, selector=selector, label=label
            )
            loc, selector, revalidation = await session._semantic_dispatch_target(
                resolution=resolution, locator=loc, selector=selector
            )
        if session is not None and hasattr(session, "_autowebglm_primary_decision"):
            await session._autowebglm_primary_decision(
                action="click", selector=selector, label=label
            )
        backend = getattr(page, "_hip_playwright_mcp_backend", None)
        executor = "python-playwright-fallback"
        if backend is not None:
            try:
                await backend.click(selector, element=label)
                executor = "playwright-mcp"
            except Exception:
                await loc.click(timeout=2500)
        else:
            await loc.click(timeout=2500)
        await page.wait_for_timeout(120)
        effect: Dict[str, Any] = {"pass": True, "status": "not_required"}
        if session is not None and resolution.get("semantic_control_id") and hasattr(session, "_semantic_post_action_verify"):
            effect = await session._semantic_post_action_verify(
                resolution=resolution, action="click", exact_value_verified=False
            )
        return {
            "pass": True, "executor": executor,
            "semantic_control_id": resolution.get("semantic_control_id") or "",
            "semantic_confidence": resolution.get("confidence"),
            "semantic_revalidation": revalidation.get("status") or "",
            "semantic_effect": effect,
        }
    except Exception as exc:
        return {"pass": False, "error": mask_sensitive_string(str(exc))[:900]}

async def reveal_hidden_structural_parent(page: Page, node: Dict[str, Any]) -> Dict[str, Any]:
    """Find a hidden semantic target and explicitly click only its safe controller."""
    loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
    payload = {
        "labels": [str(x) for x in loc.get("labels", []) if str(x).strip()],
        "names": [str(x) for x in loc.get("names", []) if str(x).strip()],
        "placeholders": [str(x) for x in loc.get("placeholders", []) if str(x).strip()],
        "fieldKey": str(node.get("field_key") or ""),
        "rowIndex": node.get("row_index") if isinstance(node.get("row_index"), int) else None,
    }
    try:
        found = await page.evaluate(
            r"""
(args) => {
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim();
  const norm=v=>clean(v).toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'');
  function css(el){if(!el||!el.tagName)return '';if(el.id)return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;const p=[];let n=el;while(n&&n.nodeType===1&&p.length<9){let x=n.tagName.toLowerCase();const par=n.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===n.tagName);if(same.length>1)x+=`:nth-of-type(${same.indexOf(n)+1})`;}p.unshift(x);n=par;}return p.join(' > ');}
  function label(el){if(el.id){const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`);if(l)return clean(l.innerText||l.textContent);}return clean(el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||'');}
  function visible(el){if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||'1')!==0);}
  const labels=(args.labels||[]).map(norm),names=(args.names||[]).map(norm),ph=(args.placeholders||[]).map(norm),field=norm(args.fieldKey);
  const controls=Array.from(document.querySelectorAll('input,textarea,select,[role=combobox],[role=radio],[role=checkbox],[role=switch]'));
  const ranked=controls.map(el=>{const n=norm(el.getAttribute('name')),fc=norm(el.getAttribute('formcontrolname')||el.getAttribute('ng-reflect-name')),p=norm(el.getAttribute('placeholder')),l=norm(label(el));let score=0;if(field&&(field===n||field===fc))score+=120;if(names.includes(n)||names.includes(fc))score+=100;if(labels.includes(l))score+=70;if(ph.includes(p))score+=60;return {el,score,label:label(el),visible:visible(el)};}).filter(x=>x.score>0).sort((a,b)=>b.score-a.score);
  let best=ranked[0];  /* Repeated rows share labels: row N's control is the N-th top-scoring match in DOM order,     so a collapsed second Process Step is found even though step 1 is visible. */  if(best&&Number.isInteger(args.rowIndex)){const top=ranked.filter(x=>x.score===best.score).map(x=>x.el).sort((a,b)=>(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING)?-1:1);const el=top[args.rowIndex];if(el)best=ranked.find(x=>x.el===el);}  if(!best||best.visible)return {found:!!best,alreadyVisible:!!best,score:best?best.score:0};
  const target=best.el;
  let controller=null,kind='';
  const details=target.closest('details:not([open])');
  if(details){controller=details.querySelector(':scope > summary');kind='details-summary';}
  if(!controller){const panel=target.closest('[role=tabpanel][hidden],[role=tabpanel][aria-hidden=true]');if(panel){const labelled=panel.getAttribute('aria-labelledby');controller=(labelled&&document.getElementById(labelled))||document.querySelector(`[role=tab][aria-controls="${CSS.escape(panel.id||'__none__')}"]`);kind='tab';}}
  if(!controller){let a=target.parentElement;while(a&&a!==document.body){if(a.id){const c=document.querySelector(`[aria-controls="${CSS.escape(a.id)}"][aria-expanded=false]`);if(c){controller=c;kind='aria-controls';break;}}a=a.parentElement;}}
  if(!controller){let a=target.parentElement;while(a&&a!==document.body){const c=a.querySelector(':scope > button[aria-expanded=false],:scope > [role=button][aria-expanded=false]');if(c){controller=c;kind='accordion';break;}a=a.parentElement;}}
  if(!controller)return {found:true,alreadyVisible:false,score:best.score,targetSelector:css(target),controllerSelector:'',reason:'hidden target has no safe structural controller'};
  const text=clean(controller.innerText||controller.textContent||controller.getAttribute('aria-label')||'');
  return {found:true,alreadyVisible:false,score:best.score,targetSelector:css(target),controllerSelector:css(controller),controllerText:text,controllerKind:kind,controllerRole:controller.getAttribute('role')||controller.tagName.toLowerCase()};
}
""",
            payload,
        )
    except Exception as exc:
        return {"revealed": False, "reason": mask_sensitive_string(str(exc))}
    if not isinstance(found, dict) or not found.get("controllerSelector"):
        return {"revealed": bool(found and found.get("alreadyVisible")), "analysis": mask_sensitive_data(found or {})}
    text = str(found.get("controllerText") or "").lower()
    if any(word in text for word in MUTATING_WORDS):
        return {"revealed": False, "reason": "unsafe structural controller rejected", "analysis": mask_sensitive_data(found)}
    selector = str(found.get("controllerSelector") or "")
    click_result = await _semantic_structural_click(
        page, selector, label=f"HIP Portal structural parent {found.get('controllerText') or found.get('controllerKind') or ''}"
    )
    if click_result.get("pass"):
        return {"revealed": True, "controller": mask_sensitive_data(found), "execution": mask_sensitive_data(click_result)}
    return {"revealed": False, "reason": click_result.get("error") or "structural parent click failed", "analysis": mask_sensitive_data(found)}


def explicit_event_proof(summary: Dict[str, Any], action: str, *, executor_ok: bool) -> Dict[str, Any]:
    events = {_norm(x) for x in summary.get("event_types", []) if str(x).strip()}
    mutation_count = int(summary.get("mutation_count") or 0)
    state_changes = int(summary.get("state_change_count") or 0)
    click_seen = bool(events & {"click", "pointerup", "pointerdown", "mousedown", "mouseup", "keydown"})
    commit_seen = bool(events & {"input", "change", "focusout", "blur"}) or mutation_count > 0 or state_changes > 0
    requires_click = action in {"select_single", "select_multi", "select_radio", "toggle"}
    # The DDS drivers below use only browser click/check/press primitives for
    # radio/dropdown widgets. The injected observer is corroborating evidence,
    # but Angular can replace the observed node during the same click. In that
    # case the explicit executor contract plus exact stable portal state is still
    # admissible and the observer gap remains visible in the audit.
    observer_gap = bool(executor_ok and requires_click and not click_seen)
    if requires_click:
        passed = bool(executor_ok and (click_seen or observer_gap) and (commit_seen or observer_gap))
    else:
        passed = bool(executor_ok and (commit_seen or not events))
    return {
        "pass": passed,
        "requires_explicit_click": requires_click,
        "explicit_click_observed": click_seen,
        "commit_or_mutation_observed": commit_seen,
        "observer_gap_accepted_from_explicit_executor": observer_gap,
        "event_types": sorted(events),
        "mutation_count": mutation_count,
        "state_change_count": state_changes,
    }


def is_parent_node(graph: Dict[str, Any], node_id: str) -> bool:
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if isinstance(node, dict) and str(node_id) in {str(x) for x in node.get("depends_on", [])}:
            return True
    return False


# Relations the dependency contract adds only to order execution (finish one
# section or repeatable row before the next).  They never mean "the parent value
# reveals or enables the child", so they must not skip a child when the
# predecessor fails, and a parent commit must not wait for such a child to appear.
ORDERING_ONLY_RELATIONS = frozenset({"section_sequence_gate", "repeatable_row_sequence_gate", "field_sequence_gate"})


def _dependency_relations(graph: Dict[str, Any]) -> Dict[tuple, str]:
    relations: Dict[tuple, str] = {}
    for edge in graph.get("dependency_edges", []) if isinstance(graph.get("dependency_edges"), list) else []:
        if isinstance(edge, dict):
            relations.setdefault((str(edge.get("from") or ""), str(edge.get("to") or "")), str(edge.get("relation") or ""))
    return relations


def structural_dependencies(graph: Dict[str, Any], node: Dict[str, Any]) -> List[str]:
    """Return the dependencies whose failure makes *node* impossible to fill.

    A dependency without an edge record is treated as structural (fail closed).
    """
    relations = _dependency_relations(graph)
    node_id = str(node.get("node_id") or "")
    return [
        str(dep) for dep in node.get("depends_on", []) or []
        if relations.get((str(dep), node_id)) not in ORDERING_ONLY_RELATIONS
    ]


def ordering_only_dependencies(graph: Dict[str, Any], node: Dict[str, Any]) -> List[str]:
    structural = set(structural_dependencies(graph, node))
    return [str(dep) for dep in node.get("depends_on", []) or [] if str(dep) not in structural]


def eligible_child_nodes(
    graph: Dict[str, Any],
    parent_node_id: str,
    node_status: Dict[str, bool],
) -> List[Dict[str, Any]]:
    """Children that must become visible once *parent_node_id* has committed.

    A child qualifies only when this parent structurally reveals or enables it
    and every other structural parent has already committed.  A grandchild such
    as an attribute Expression (revealed by its row's Derived From) is gated when
    its own parent commits, not when Data Format Type or Name commits.
    """
    relations = _dependency_relations(graph)
    children: List[Dict[str, Any]] = []
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or "")
        deps = [str(x) for x in node.get("depends_on", [])]
        if parent_node_id not in deps:
            continue
        if relations.get((parent_node_id, node_id)) in ORDERING_ONLY_RELATIONS:
            continue
        other_deps = [
            d for d in deps
            if d != parent_node_id and relations.get((d, node_id)) not in ORDERING_ONLY_RELATIONS
        ]
        if any(node_status.get(d) is not True for d in other_deps):
            continue
        if node.get("expected_value") in (None, "", []):
            continue
        children.append(node)
    return children


def normalize_selection_values(values: Any) -> List[str]:
    """Normalize a selection list without losing the first portal spelling."""
    iterable = values if isinstance(values, (list, tuple, set)) else [values]
    result: List[str] = []
    seen: set[str] = set()
    for value in iterable:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        low = text.lower()
        if not text or low == "select all" or re.fullmatch(r"\d+\s+selected", text, flags=re.I):
            continue
        if low in seen:
            continue
        seen.add(low)
        result.append(text)
    return result


def multiselect_exact_set_proof(
    expected: Any,
    actual: Any,
    *,
    selection_mode: str = "",
    selected_count: Any = None,
    available_options: Any = None,
    option_universe_stable: bool | None = None,
) -> Dict[str, Any]:
    """Build a deterministic exact-set proof for DDS and checkbox multi-selects."""
    expected_values = normalize_selection_values(expected)
    actual_values = normalize_selection_values(actual)
    expected_map = {x.lower(): x for x in expected_values}
    actual_map = {x.lower(): x for x in actual_values}
    missing = [expected_map[k] for k in expected_map if k not in actual_map]
    extra = [actual_map[k] for k in actual_map if k not in expected_map]
    duplicate_expected = len(expected_values) != len([
        x for x in (expected if isinstance(expected, (list, tuple, set)) else [expected])
        if str(x or "").strip()
    ])
    mode_multiple = _norm(selection_mode) in {"multiple", "multi", "multiselect", "multi_select"}
    count_value = None
    try:
        count_value = int(selected_count) if selected_count is not None else len(actual_values)
    except (TypeError, ValueError):
        count_value = len(actual_values)
    count_match = count_value == len(actual_values) == len(expected_values)
    available = normalize_selection_values(available_options)
    unavailable = [x for x in expected_values if available and x.lower() not in {v.lower() for v in available}]
    stable = True if option_universe_stable is None else bool(option_universe_stable)
    passed = bool(mode_multiple and not missing and not extra and not duplicate_expected and count_match and not unavailable and stable)
    return {
        "pass": passed,
        "selection_mode_multiple": mode_multiple,
        "expected_values": expected_values,
        "actual_values": actual_values,
        "missing": missing,
        "extra": extra,
        "duplicate_expected_values": duplicate_expected,
        "selected_count": count_value,
        "selected_count_match": count_match,
        "option_universe_stable": stable,
        "expected_values_unavailable": unavailable,
        "comparison": "order-insensitive exact normalized set",
    }


async def universal_locator_preflight(
    page: Page,
    locator: Locator,
    *,
    action: str,
    selector: str = "",
    timeout_ms: int = 5000,
) -> Dict[str, Any]:
    """Apply the shared interaction policy to any HIP Portal form locator.

    This path is used by generic Partner/System/Account/Domain exploration and
    any newly introduced HIP page that does not yet have a phase-specific state
    graph.  It reveals safe structural parents, waits out animations, verifies
    hit-testing and returns a value-free form-family audit.
    """
    from .hip_form_catalog import classify_form_surface

    audit: Dict[str, Any] = {
        "schema_version": "hip.universal-form-interaction-preflight.v1",
        "action": action,
        "selector": selector,
        "structural_parent_revealed": False,
    }
    try:
        count = await locator.count()
    except Exception:
        count = 0
    if count < 1:
        audit.update({"pass": False, "reason": "target locator did not resolve"})
        return mask_sensitive_data(audit)

    target = locator.first
    try:
        visible = await target.is_visible(timeout=min(timeout_ms, 1500))
    except Exception:
        visible = False
    if not visible:
        try:
            parent = await target.evaluate(
                r"""
el => {
  function css(n){if(!n||!n.tagName)return '';if(n.id)return `${n.tagName.toLowerCase()}#${CSS.escape(n.id)}`;const p=[];while(n&&n.nodeType===1&&p.length<8){let x=n.tagName.toLowerCase();const par=n.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===n.tagName);if(same.length>1)x+=`:nth-of-type(${same.indexOf(n)+1})`;}p.unshift(x);n=par;}return p.join(' > ');}
  let controller=null,kind='';
  const details=el.closest('details:not([open])');
  if(details){controller=details.querySelector(':scope > summary');kind='details-summary';}
  if(!controller){const panel=el.closest('[role=tabpanel][hidden],[role=tabpanel][aria-hidden=true]');if(panel){const labelled=panel.getAttribute('aria-labelledby');controller=(labelled&&document.getElementById(labelled))||(panel.id&&document.querySelector(`[role=tab][aria-controls="${CSS.escape(panel.id)}"]`));kind='tab';}}
  if(!controller){let a=el.parentElement;while(a&&a!==document.body){if(a.id){const c=document.querySelector(`[aria-controls="${CSS.escape(a.id)}"][aria-expanded=false]`);if(c){controller=c;kind='aria-controls';break;}}a=a.parentElement;}}
  if(!controller){let a=el.parentElement;while(a&&a!==document.body){const c=a.querySelector(':scope > button[aria-expanded=false],:scope > [role=button][aria-expanded=false]');if(c){controller=c;kind='accordion';break;}a=a.parentElement;}}
  const text=controller?String(controller.innerText||controller.textContent||controller.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim():'';
  return {controllerSelector:css(controller),controllerKind:kind,controllerText:text};
}
"""
            )
        except Exception:
            parent = {}
        controller_selector = str((parent or {}).get("controllerSelector") or "")
        controller_text = str((parent or {}).get("controllerText") or "").lower()
        if controller_selector and not any(word in controller_text for word in MUTATING_WORDS):
            click_result = await _semantic_structural_click(
                page, controller_selector, label=f"HIP Portal structural parent {parent.get('controllerText') or parent.get('controllerKind') or ''}"
            )
            if click_result.get("pass"):
                audit["structural_parent_revealed"] = True
                audit["structural_parent"] = parent
                audit["structural_parent_execution"] = click_result
            else:
                audit.update({"pass": False, "reason": f"safe structural parent could not be opened: {click_result.get('error') or 'semantic click blocked'}"})
                return mask_sensitive_data(audit)
        try:
            visible = await target.is_visible(timeout=min(timeout_ms, 2200))
        except Exception:
            visible = False
    if not visible:
        audit.update({"pass": False, "reason": "target remained hidden after structural-parent analysis"})
        return mask_sensitive_data(audit)

    audit["scroll_into_view"] = await scroll_locator_into_view(target)

    try:
        stable = await target.evaluate(
            r"""
async (el) => {
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  let prev=null,stable=0,samples=0,last=null;
  const deadline=performance.now()+3500;
  while(performance.now()<deadline){
    samples++;const r=el.getBoundingClientRect();const s=getComputedStyle(el);
    const running=(el.getAnimations?el.getAnimations({subtree:false}):[]).some(a=>a.playState==='running'||a.playState==='pending');
    last={x:r.x,y:r.y,width:r.width,height:r.height,runningAnimation:running};
    if(r.width&&r.height&&!running&&prev&&['x','y','width','height'].every(k=>Math.abs(last[k]-prev[k])<=0.75))stable++;else stable=1;
    prev=last;if(stable>=2)return {stable:true,samples,consecutiveSamples:stable,bbox:last};await sleep(70);
  }
  return {stable:false,samples,consecutiveSamples:stable,bbox:last};
}
"""
        )
    except Exception as exc:
        stable = {"stable": False, "reason": mask_sensitive_string(str(exc))}
    audit["bounding_box_stability"] = stable
    if not stable.get("stable"):
        audit.update({"pass": False, "reason": "target bounding box/animation did not stabilize"})
        return mask_sensitive_data(audit)

    try:
        hit = await target.evaluate(
            r"""
el => {const r=el.getBoundingClientRect();const cx=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2)),cy=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));const h=document.elementFromPoint(cx,cy);return {pass:!!(h&&(h===el||el.contains(h)||h.contains(el))),tag:(h&&h.tagName||'').toLowerCase(),role:h&&h.getAttribute&&h.getAttribute('role')||''};}
"""
        )
    except Exception as exc:
        hit = {"pass": False, "reason": mask_sensitive_string(str(exc))}
    if not hit.get("pass"):
        # A sticky header/footer or a late layout shift can still cover the
        # centre; re-centre once before declaring the target intercepted.
        audit["scroll_into_view_retry"] = await scroll_locator_into_view(target)
        try:
            hit = await target.evaluate(
                r"""
el => {const r=el.getBoundingClientRect();const cx=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2)),cy=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));const h=document.elementFromPoint(cx,cy);return {pass:!!(h&&(h===el||el.contains(h)||h.contains(el))),tag:(h&&h.tagName||'').toLowerCase(),role:h&&h.getAttribute&&h.getAttribute('role')||''};}
"""
            )
        except Exception as exc:
            hit = {"pass": False, "reason": mask_sensitive_string(str(exc))}
    audit["hit_test"] = hit
    if not hit.get("pass"):
        audit.update({"pass": False, "reason": "target center is intercepted"})
        return mask_sensitive_data(audit)

    try:
        page_text = await page.locator("body").inner_text(timeout=1200)
    except Exception:
        page_text = ""
    try:
        url = page.url
    except Exception:
        url = ""
    classification = classify_form_surface(url=url, text=page_text[:12000], controls=[])
    audit["form_surface"] = classification
    audit["pass"] = True
    return mask_sensitive_data(audit)


def policy_manifest() -> Dict[str, Any]:
    from .hip_form_catalog import catalog_manifest
    payload = dict(DEFAULT_FORM_INTERACTION_POLICY)
    payload["form_family_catalog"] = catalog_manifest()
    payload["coverage"] = "all known HIP Portal forms and unknown /hybrid-integrations forms through generic fallback"
    payload["flow_pattern_memory"] = {
        "enabled": True,
        "judge_validated_only_for_fast_replay": True,
        "values_stored": False,
        "same_family_structural_similarity_required": True,
    }
    return mask_sensitive_data(payload)
