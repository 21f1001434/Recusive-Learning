from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import Locator, Page

from .security import mask_sensitive_string
from .semantic_affordance import selector_looks_generation_volatile
from .autonomous_transition_runtime import choose_dynamic_portal_option



def _world_model_for_page(page: Page):
    try:
        session = getattr(page, "_hip_browser_session", None)
        gate = getattr(session, "semantic_action_gate", None) if session is not None else None
        return getattr(gate, "world_model", None)
    except Exception:
        return None


def _semantic_resolution_label_section(resolution: Dict[str, Any]) -> tuple[str, str]:
    candidate = resolution.get("candidate") if isinstance(resolution.get("candidate"), dict) else resolution.get("candidate_before") if isinstance(resolution.get("candidate_before"), dict) else {}
    evidence = resolution.get("evidence") if isinstance(resolution.get("evidence"), dict) else {}
    label = str(candidate.get("label") or candidate.get("aria_label") or evidence.get("expected_label") or "")
    section = str(candidate.get("section") or evidence.get("expected_section") or "")
    return label, section


def _stage4_choose_single_option(
    page: Page, *, phase: str, resolution: Dict[str, Any], snapshot: Dict[str, Any], raw_value: str,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    options = [x for x in snapshot.get("options", []) if isinstance(x, dict)]
    label, section = _semantic_resolution_label_section(resolution)
    decision = choose_dynamic_portal_option(
        phase=phase, label=label or "DDS combobox", section=section, live_options=options,
        desired_value=raw_value, world_model=_world_model_for_page(page),
    )
    if not decision.get("pass"):
        return None, decision
    selected = str(decision.get("selected_option") or "")
    matches = [x for x in options if not x.get("disabled") and re.sub(r"\s+", " ", str(x.get("text") or "").strip()).lower() == re.sub(r"\s+", " ", selected.strip()).lower()]
    return (dict(matches[0]) if len(matches) == 1 else None), decision


def _record_stage4_portal_choice(
    page: Page, *, phase: str, resolution: Dict[str, Any], snapshot: Dict[str, Any], chosen_text: str, effect_type: str, confidence: float,
) -> None:
    wm = _world_model_for_page(page)
    if wm is None:
        return
    label, section = _semantic_resolution_label_section(resolution)
    candidate = resolution.get("candidate") if isinstance(resolution.get("candidate"), dict) else resolution.get("candidate_before") if isinstance(resolution.get("candidate_before"), dict) else {}
    try:
        wm.record_verified_portal_choice(
            phase=phase, action="select", control={**candidate, "label": label, "section": section},
            choice=chosen_text, available_options=[str(x.get("text") or "") for x in snapshot.get("options", []) if isinstance(x, dict)],
            effect_type=effect_type, effect_confidence=float(confidence or 0.0),
        )
    except Exception:
        pass

_PHASE_KEYWORDS = {
    "data_map": ["Create Map", "Map Identifier", "Map Data", "Input Schema Validation", "Output Schema Validation"],
    "source_document_type": ["Document Type Name", "Root Element", "Validation Type", "Attributes"],
    "target_document_type": ["Document Type Name", "Root Element", "Validation Type", "Attributes"],
    "rule": ["Create Rule", "Rule Name", "Conditions", "Actions"],
    "source_transport_profile": ["Create Transport Profile", "System Type", "Profile Usage", "Interface Type", "Existing Account", "Document Type Supported"],
    "target_transport_profile": ["Create Transport Profile", "System Type", "Profile Usage", "Interface Type", "Existing Account", "Document Type Supported"],
    "transport_profile": ["Create Transport Profile", "System Type", "Profile Usage", "Interface Type", "Existing Account", "Document Type Supported"],
    "biz_flow": ["Create Biz Flow", "Flow Details", "Configure Source", "Configure Target", "Configure Routing"],
}

_ROOT_JS = r"""
({phase, keywords}) => {
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
    return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||'1')!==0);
  }
  function cssPath(el){
    if(!el || !el.tagName) return 'body';
    if(el === document.body) return 'body';
    const parts=[]; let node=el;
    while(node && node.nodeType===1 && parts.length<8){
      let part=node.tagName.toLowerCase();
      if(node.id){ part += '#'+CSS.escape(node.id); parts.unshift(part); break; }
      const cls=(node.className||'').toString().trim().split(/\s+/).filter(Boolean).slice(0,3).map(c=>'.'+CSS.escape(c)).join('');
      part += cls;
      const parent=node.parentElement;
      if(parent){
        const same=Array.from(parent.children).filter(x=>x.tagName===node.tagName);
        if(same.length>1) part += `:nth-of-type(${same.indexOf(node)+1})`;
      }
      parts.unshift(part); node=parent;
    }
    return parts.join(' > ') || 'body';
  }
  const lowerPhase=(phase||'').toLowerCase();
  const roots=Array.from(document.querySelectorAll('[role="dialog"], dds-drawer, .dds__drawer, .dds__modal, .modal-dialog, form, app-create-biz-flow, app-bizflow, app-transport-profile, app-transport_profiles, app-data-map, app-datamap, main, body')).filter(visible);
  const kws=(keywords||[]).map(x=>String(x||'').toLowerCase());
  const rows=roots.map(el=>{
    const txt=(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();
    const low=txt.toLowerCase();
    let score=0;
    for(const kw of kws){ if(kw && low.includes(kw)) score += 5; }
    const controls=Array.from(el.querySelectorAll('input:not([type=hidden]), textarea, select, [role=combobox], input[type=file]')).filter(visible).length;
    score += Math.min(controls, 10);
    if(/create map|create transport profile|create biz flow|create rule/i.test(txt)) score += 20;
    if(lowerPhase.includes('biz') && /b2b-flow-pubsub-template/i.test(txt) && !/flow details|configure source|configure target|configure routing/i.test(txt)) score -= 40;
    if(/items per page|filter by column|table search/i.test(txt) && !/create map|create transport profile|flow details|configure source|configure target|configure routing/i.test(txt)) score -= 20;
    const r=el.getBoundingClientRect();
    return {selector:cssPath(el), text:txt.slice(0,3000), score, controls, area:Math.max(1,r.width*r.height)};
  }).filter(x=>x.score>0).sort((a,b)=>(b.score-a.score)||(a.area-b.area));
  return rows[0] || {selector:'body', text:(document.body.innerText||'').slice(0,3000), score:0, controls:0, area:0};
}
"""

async def active_form_root_info(page: Page, phase: str) -> Dict[str, Any]:
    try:
        phase_key = str(phase or "")
        if phase_key.startswith("universal_") or phase_key.startswith("dynamic_") or phase_key == "universal_task":
            keywords = []
        else:
            keywords = _PHASE_KEYWORDS.get(phase_key, _PHASE_KEYWORDS.get("transport_profile", []))
        return await page.evaluate(_ROOT_JS, {"phase": phase_key, "keywords": keywords})
    except Exception as exc:
        return {"selector": "body", "text": "", "score": 0, "controls": 0, "error": mask_sensitive_string(str(exc))}

async def get_active_form_root(page: Page, phase: str) -> Locator:
    info = await active_form_root_info(page, phase)
    return page.locator(str(info.get("selector") or "body")).first

async def active_form_text(page: Page, phase: str) -> str:
    info = await active_form_root_info(page, phase)
    return str(info.get("text") or "")

async def close_open_dropdown(page: Page, phase: str = "") -> None:
    """Close a DDS/listbox popup without clicking the page shell.

    Earlier builds clicked a blank/root location to dismiss dropdowns.  On the
    HIP Angular shell that click is captured as a mutating app-container click
    and can also close drawers.  This helper is intentionally non-clicking:
    it blurs the active element and dispatches focusout/change only.
    """
    try:
        await page.evaluate(r"""
() => {
  const el = document.activeElement;
  if (el && el !== document.body) {
    try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
    try { el.dispatchEvent(new FocusEvent('blur', {bubbles:true})); } catch(e) {}
    try { el.blur(); } catch(e) {}
  }
  for (const lb of Array.from(document.querySelectorAll('[role=listbox], .dds__dropdown__list, .dds__popover, .dds__menu'))) {
    const txt=(lb.innerText||lb.textContent||'').trim();
    if (!txt) continue;
    lb.setAttribute('data-hip-dropdown-blurred','true');
  }
  return true;
}
""")
        await page.wait_for_timeout(100)
    except Exception:
        pass

async def assert_active_surface(page: Page, phase: str) -> Dict[str, Any]:
    text = (await active_form_text(page, phase)).lower()
    fatal: List[str] = []
    if phase == "data_map":
        # V230: screenshots/old labels are priors, not a brittle title contract.
        # A live in-page drawer may be renamed (Create/Add/New Map) or progressively
        # reveal fields.  Accept a map-creation intent plus one map control, OR two
        # independent map-control markers inside the already selected active root.
        core = [
            "map identifier", "map name", "map class", "contivo version",
            "status", "map data", "input schema", "output schema",
        ]
        marker_count = sum(1 for x in core if x in text)
        creation_intent = any(x in text for x in ["create map", "add map", "new map", "map details", "mapping details"])
        if not ((creation_intent and marker_count >= 1) or marker_count >= 2):
            fatal.append("Data Map active form root is not a proven in-page map creation/edit surface.")
    elif phase in {"source_transport_profile", "target_transport_profile", "transport_profile"}:
        if "create transport profile" not in text or not any(x in text for x in ["interface type", "sftp haft", "existing account", "document type supported"]):
            fatal.append("Transport Profile active form root is not the expanded Create Transport Profile wizard.")
    elif phase == "biz_flow":
        if "b2b-flow-pubsub-template" in text and not any(x in text for x in ["flow details", "configure source", "configure target", "configure routing"]):
            fatal.append("BizFlow active surface is still the template picker, not the wizard.")
        elif not any(x in text for x in ["flow details", "configure source", "configure target", "configure routing", "business flow name"]):
            fatal.append("BizFlow wizard tabs are not visible.")
    elif str(phase or "").startswith("universal_") or str(phase or "") == "universal_task":
        # Universal tasks are intentionally page-family agnostic.  The active-root
        # selector above already prefers the smallest visible form/dialog/drawer
        # with controls.  Do not impose HIP-family title text; require only a live
        # form surface when a generic state transaction asks for one.
        info = await active_form_root_info(page, phase)
        if int(info.get("controls") or 0) <= 0:
            fatal.append("Universal task has no visible writable/verifyable form controls on the authoritative foreground surface.")
    return {"phase": phase, "fatal": fatal, "surface_text_sample": text[:500]}



# ---------------------------------------------------------------------------
# Sticky fill/value preservation helpers
# ---------------------------------------------------------------------------
# HIP/DDS comboboxes are Angular controlled. During option inventory, row-level
# +Add, or tab navigation, the portal can rerender a control and briefly reset a
# displayed value back to blank/Select.  These helpers record values that were
# intentionally filled and provide a best-effort restore pass before screenshots
# and before moving to the next nested BizFlow section.  They never click
# Save/Create/Submit; they only restore already-entered field values.

_EMPTY_CONTROL_VALUES = {"", "select", "select...", "choose", "--select--", "none", "null"}


async def _ensure_page_interactable(page: Page, *, action: str, selector: str = "") -> None:
    """Use the shared persistent-session auth/overlay guard when available."""
    session = getattr(page, "_hip_browser_session", None)
    if session is None:
        return
    try:
        await session.ensure_interactable(action=action, selector=selector, timeout_ms=25000)
    except AttributeError:
        return


def semantic_runtime_enabled(page: Page) -> bool:
    """Whether Layer-11 fail-closed semantic execution owns this page."""
    session = getattr(page, "_hip_browser_session", None)
    gate = getattr(session, "semantic_action_gate", None) if session is not None else None
    return bool(gate is not None and getattr(gate, "enabled", False))


def _last_session_executor(session: Any) -> str:
    """Return the executor that physically handled the latest BrowserSession action."""
    try:
        ev = (getattr(session, "action_events", None) or [])[-1]
        prov = getattr(ev, "execution_provenance", None) or {}
        if isinstance(prov, dict):
            return str(prov.get("actual_executor") or prov.get("executor") or "")
        target = str(getattr(ev, "target", "") or "")
        m = re.search(r"executor=([^] ]+)", target)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _remember_broker_execution(page: Page, *, action: str, selector: str, label: str, success: bool, executor: str = "", value_present: bool = False) -> Dict[str, Any]:
    row = {
        "action": str(action or ""),
        "selector": str(selector or ""),
        "label": str(label or ""),
        "success": bool(success),
        "executor": str(executor or ""),
        "value_present": bool(value_present),
        "authoritative": bool(success and (
            str(executor or "").startswith("pyautogui-mcp")
            or str(executor or "").startswith("playwright-mcp")
            or str(executor or "").startswith("python-playwright")
        )),
    }
    try:
        setattr(page, "_hip_last_control_execution", row)
        hist = getattr(page, "_hip_control_execution_history", None)
        if not isinstance(hist, list):
            hist = []
            setattr(page, "_hip_control_execution_history", hist)
        hist.append(dict(row))
        if len(hist) > 500:
            del hist[:-500]
    except Exception:
        pass
    return row


def read_last_control_execution(page: Page) -> Dict[str, Any]:
    row = getattr(page, "_hip_last_control_execution", None)
    return dict(row) if isinstance(row, dict) else {}


async def _with_phase(page: Page, phase: str):
    """Small async helper used only internally to annotate BrowserSession actions."""
    session = getattr(page, "_hip_browser_session", None)
    previous = str(getattr(session, "_active_phase_name", "") or "") if session is not None else ""
    if session is not None and phase:
        try:
            session._active_phase_name = phase
        except Exception:
            pass
    return session, previous


async def _restore_phase(session: Any, previous: str) -> None:
    if session is not None:
        try:
            session._active_phase_name = previous
        except Exception:
            pass


async def _broker_click(page: Page, selector: str, *, label: str, phase: str = "", mutation_risk: bool = False) -> bool:
    """Execute a HIP control click through the single governed interaction broker.

    Live pages MUST use BrowserSession so the real executor ladder is:
    AutoWebGLM -> semantic proof -> PyAutoGUI MCP primary -> Playwright MCP fallback
    -> Python Playwright compatibility fallback.  Raw DDS-driver clicks are only
    retained for standalone/offline utility tests that have no BrowserSession.
    """
    if not selector:
        return False
    loc = page.locator(selector).first
    session, previous = await _with_phase(page, phase)
    try:
        if session is not None and hasattr(session, "click_and_wait"):
            try:
                await session.click_and_wait(
                    action=label or "HIP Portal control", locator=loc, selector=selector,
                    mutation_risk=bool(mutation_risk),
                )
                _remember_broker_execution(
                    page, action="click", selector=selector, label=label, success=True,
                    executor=_last_session_executor(session) or "browser-session",
                )
                return True
            except Exception:
                _remember_broker_execution(
                    page, action="click", selector=selector, label=label, success=False,
                    executor=_last_session_executor(session) or "browser-session",
                )
                return False
        # Standalone/offline compatibility: when a Playwright MCP backend is
        # attached directly to the page, keep the same broker semantics instead
        # of dropping to an unobserved raw locator click.
        backend = getattr(page, "_hip_playwright_mcp_backend", None)
        if backend is not None and hasattr(backend, "click"):
            try:
                await backend.click(selector, element=label or "HIP Portal control")
                _remember_broker_execution(page, action="click", selector=selector, label=label, success=True, executor="playwright-mcp-offline")
                return True
            except Exception:
                if semantic_runtime_enabled(page):
                    _remember_broker_execution(page, action="click", selector=selector, label=label, success=False, executor="playwright-mcp-offline")
                    return False
        # Offline/non-governed compatibility only. Never reached on a live HIP BrowserSession.
        try:
            if hasattr(loc, "scroll_into_view_if_needed"):
                await loc.scroll_into_view_if_needed(timeout=1800)
            await loc.click(timeout=2500)
            _remember_broker_execution(page, action="click", selector=selector, label=label, success=True, executor="python-playwright-offline")
            return True
        except Exception:
            return False
    finally:
        await _restore_phase(session, previous)


async def _broker_fill(page: Page, selector: str, value: str, *, label: str, phase: str = "", action_type: str = "fill") -> bool:
    """Fill through BrowserSession so PyAutoGUI MCP is truly primary for form data."""
    if not selector:
        return False
    loc = page.locator(selector).first
    session, previous = await _with_phase(page, phase)
    try:
        if session is not None and hasattr(session, "fill_and_log"):
            try:
                await session.fill_and_log(locator=loc, value=str(value), selector=selector, action_type=action_type)
                _remember_broker_execution(
                    page, action=action_type, selector=selector, label=label, success=True,
                    executor=_last_session_executor(session) or "browser-session", value_present=bool(str(value)),
                )
                return True
            except Exception:
                _remember_broker_execution(
                    page, action=action_type, selector=selector, label=label, success=False,
                    executor=_last_session_executor(session) or "browser-session", value_present=bool(str(value)),
                )
                return False
        backend = getattr(page, "_hip_playwright_mcp_backend", None)
        if backend is not None and hasattr(backend, "fill"):
            try:
                await backend.fill(selector, str(value), element=label or "HIP Portal field", slowly=False)
                _remember_broker_execution(page, action=action_type, selector=selector, label=label, success=True, executor="playwright-mcp-offline", value_present=bool(str(value)))
                return True
            except Exception:
                if semantic_runtime_enabled(page):
                    _remember_broker_execution(page, action=action_type, selector=selector, label=label, success=False, executor="playwright-mcp-offline", value_present=bool(str(value)))
                    return False
        try:
            await loc.fill(str(value), timeout=2500)
            _remember_broker_execution(page, action=action_type, selector=selector, label=label, success=True, executor="python-playwright-offline", value_present=bool(str(value)))
            return True
        except Exception:
            return False
    finally:
        await _restore_phase(session, previous)


async def _broker_press(page: Page, selector: str, key: str, *, label: str, phase: str = "") -> bool:
    if not selector:
        return False
    loc = page.locator(selector).first
    session, previous = await _with_phase(page, phase)
    try:
        if session is not None and hasattr(session, "press_and_log"):
            try:
                await session.press_and_log(locator=loc, key=key, selector=selector)
                _remember_broker_execution(page, action="press", selector=selector, label=label, success=True, executor=_last_session_executor(session) or "browser-session")
                return True
            except Exception:
                return False
        backend = getattr(page, "_hip_playwright_mcp_backend", None)
        if backend is not None and hasattr(backend, "press"):
            try:
                await backend.press(selector, key, element=label or "HIP Portal field")
                _remember_broker_execution(page, action="press", selector=selector, label=label, success=True, executor="playwright-mcp-offline")
                return True
            except Exception:
                if semantic_runtime_enabled(page):
                    _remember_broker_execution(page, action="press", selector=selector, label=label, success=False, executor="playwright-mcp-offline")
                    return False
        try:
            await loc.press(key, timeout=1800)
            _remember_broker_execution(page, action="press", selector=selector, label=label, success=True, executor="python-playwright-offline")
            return True
        except Exception:
            return False
    finally:
        await _restore_phase(session, previous)


async def open_control_for_discovery(page: Page, selector: str, *, label: str = "HIP Portal dropdown", phase: str = "") -> bool:
    """Open a read-only control through the governed hybrid interaction broker.

    Discovery is still a real UI action: prove the semantic target, ask AutoWebGLM
    to align with it, dispatch through PyAutoGUI/Playwright, then prove that the
    expected dropdown/listbox effect occurred.  A standalone semantic runtime does
    not fall through to an unobserved raw click after its MCP executor fails.
    """
    if not selector:
        return False
    await _ensure_page_interactable(page, action="open control for read-only option discovery", selector=selector)
    try:
        resolution, _loc, resolved_selector, _revalidation = await _semantic_prepare(
            page, action="click", selector=selector, label=label, phase=phase
        )
        await _autowebglm_primary_gate(
            page, action="click", selector=resolved_selector, label=label
        )
        ok = await _broker_click(
            page, resolved_selector, label=label, phase=phase, mutation_risk=False
        )
        if not ok:
            return False
        effect = await _semantic_commit(
            page, resolution=resolution, action="click", exact_value_verified=False, phase=phase
        )
        return bool(effect.get("pass", True))
    except Exception:
        return False

def _norm_control_value(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())

def _looks_empty_control_value(value: str) -> bool:
    return _norm_control_value(value).lower() in _EMPTY_CONTROL_VALUES

async def _lock_filled_value(page: Page, selector: str, value: str, *, key: str = "", label: str = "", combo: bool = False) -> None:
    if not selector or value is None or _looks_empty_control_value(str(value)):
        return
    if semantic_runtime_enabled(page) and selector_looks_generation_volatile(selector):
        return
    try:
        await page.evaluate(r"""
({selector, value, key, label, combo}) => {
  window.__HIP_FILLED_VALUE_LOCKS = window.__HIP_FILLED_VALUE_LOCKS || {};
  const now = Date.now();
  const next = {selector, value:String(value||''), key:String(key||''), label:String(label||''), combo:!!combo, ts:now};
  const existing = window.__HIP_FILLED_VALUE_LOCKS[selector];
  // If two different semantic fills accidentally target the same DOM selector,
  // it means a generic label matcher picked the wrong control.  Do not keep a
  // sticky lock in that case, otherwise the restore pass causes the visible
  // "filled then unfilled/overwritten" behavior.
  if (existing && String(existing.value||'').trim() && String(existing.value||'').trim() !== next.value.trim()) {
    const oldKey = String(existing.key||'');
    const newKey = String(next.key||'');
    if (!oldKey || !newKey || oldKey !== newKey) {
      delete window.__HIP_FILLED_VALUE_LOCKS[selector];
      const el = document.querySelector(selector);
      if (el) {
        el.setAttribute('data-hip-lock-conflict', 'true');
        el.removeAttribute('data-hip-locked-value');
        el.removeAttribute('data-hip-locked-key');
        el.removeAttribute('data-hip-locked-label');
      }
      return false;
    }
  }
  window.__HIP_FILLED_VALUE_LOCKS[selector] = next;
  const el = document.querySelector(selector);
  if (el) {
    el.setAttribute('data-hip-locked-value', String(value||''));
    el.setAttribute('data-hip-locked-at', String(now));
    if (key) el.setAttribute('data-hip-locked-key', String(key));
    if (label) el.setAttribute('data-hip-locked-label', String(label));
  }
  return true;
}
""", {"selector": selector, "value": value, "key": key, "label": label, "combo": combo})
    except Exception:
        pass

async def _restore_previous_value(page: Page, selector: str, value: str) -> bool:
    if not selector or _looks_empty_control_value(value):
        return False
    try:
        res = await page.evaluate(r"""
({selector, value}) => {
  const el = document.querySelector(selector);
  if (!el || el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true') return false;
  const before = el.value || el.getAttribute('aria-valuetext') || el.textContent || '';
  el.scrollIntoView({block:'center', inline:'nearest'});
  el.focus();
  const tag = (el.tagName||'').toLowerCase();
  if (tag === 'select') {
    const target = String(value||'').trim().toLowerCase();
    const opts = Array.from(el.options||[]);
    const m = opts.find(o => (o.text||'').trim().toLowerCase() === target) || opts.find(o => String(o.value||'').trim().toLowerCase() === target);
    if (m) el.value = m.value; else el.value = value;
  } else if (el.getAttribute('contenteditable') === 'true') {
    el.textContent = value;
  } else {
    el.value = value;
  }
  for (const ev of ['input','change','blur']) el.dispatchEvent(new Event(ev, {bubbles:true}));
  return {ok:true, before, after: el.value || el.getAttribute('aria-valuetext') || el.textContent || ''};
}
""", {"selector": selector, "value": value})
        return bool(res and res.get("ok"))
    except Exception:
        return False

async def restore_filled_values(page: Page, phase: str = "", *, reason: str = "", attempts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Restore already-filled controls that regressed to blank/Select.

    This is a DOM/model preservation pass, not a new semantic fill.  It avoids
    the common "filled then unfilled" failure caused by DDS overlay close,
    virtualized row recreation, or tab transition.
    """
    audit: Dict[str, Any] = {"phase": phase, "reason": reason, "checked": 0, "restored": 0, "items": []}
    try:
        rows = await page.evaluate(r"""
() => {
  const locks = window.__HIP_FILLED_VALUE_LOCKS || {};
  function visible(el){
    if(!el || !el.getBoundingClientRect) return false;
    const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
    return !!(r.width && r.height && s.display!=='none' && s.visibility!=='hidden');
  }
  return Object.values(locks).map(lock => {
    const el = document.querySelector(lock.selector);
    const current = el ? (el.value || el.getAttribute('aria-valuetext') || el.getAttribute('title') || el.textContent || '') : '';
    return {...lock, exists: !!el, visible: visible(el), current: String(current||'')};
  });
}
""")
    except Exception as exc:
        audit["error"] = mask_sensitive_string(str(exc))
        return audit
    if not isinstance(rows, list):
        return audit
    for row in rows:
        selector = str(row.get("selector") or "")
        wanted = str(row.get("value") or "")
        current = str(row.get("current") or "")
        audit["checked"] += 1
        if not selector or not wanted or not row.get("exists"):
            continue
        if semantic_runtime_enabled(page) and selector_looks_generation_volatile(selector):
            audit["items"].append({"selector": selector, "restored": False, "skipped": "generation_volatile_selector", "key": row.get("key"), "label": row.get("label")})
            continue
        # Only restore if the field became empty/Select or visibly lost the intended value.
        cur_norm = _norm_control_value(current).lower()
        want_norm = _norm_control_value(wanted).lower()
        if cur_norm and cur_norm not in _EMPTY_CONTROL_VALUES and (cur_norm == want_norm or want_norm in cur_norm or cur_norm in want_norm):
            continue
        ok = await _restore_previous_value(page, selector, wanted)
        item = {"selector": selector, "value": wanted, "previous": current, "restored": ok, "key": row.get("key"), "label": row.get("label")}
        audit["items"].append(item)
        if attempts is not None:
            attempts.append({"label": row.get("label") or "sticky_restore", "key": row.get("key") or "sticky_restore", "selector": selector, "value_used": wanted, "previous_value": current, "success": ok, "filled": ok, "sticky_restore": True, "reason": reason, "safety": "restore only; no Save/Create/Submit clicked", "scoped_to_active_root": True})
        if ok:
            audit["restored"] += 1
    if audit["restored"]:
        try:
            await page.wait_for_timeout(150)
        except Exception:
            pass
    return audit

async def _autowebglm_primary_gate(
    page: Page, *, action: str, selector: str, label: str = "", value: str = ""
) -> Dict[str, Any]:
    session = getattr(page, "_hip_browser_session", None)
    if session is None or not hasattr(session, "_autowebglm_primary_decision"):
        return {"status": "bypassed", "framework": "autowebglm"}
    return await session._autowebglm_primary_decision(
        action=action, selector=selector, label=label or selector, value=value
    )

async def _semantic_prepare(
    page: Page, *, action: str, selector: str, label: str = "", phase: str = "", locator: Locator | None = None
) -> tuple[Dict[str, Any], Locator, str, Dict[str, Any]]:
    """Apply Layer-11 semantic proof/rebind without importing BrowserSession."""
    loc = locator or page.locator(selector).first
    session = getattr(page, "_hip_browser_session", None)
    if session is None or not hasattr(session, "_semantic_action_preflight"):
        return ({"pass": True, "status": "session_unavailable", "confidence": 1.0}, loc, selector, {"pass": True, "status": "not_required"})
    previous_phase = getattr(session, "_active_phase_name", "")
    if phase and not previous_phase:
        try:
            session._active_phase_name = phase
        except Exception:
            pass
    try:
        resolution = await session._semantic_action_preflight(
            action=action, locator=loc, selector=selector, label=label or selector
        )
        fresh_loc, fresh_selector, revalidation = await session._semantic_dispatch_target(
            resolution=resolution, locator=loc, selector=selector
        )
        trace = getattr(session, "mission_trace", None)
        trace_phase = str(getattr(session, "_active_phase_name", "") or phase or "")
        if trace is not None and trace_phase:
            try:
                trace.record_observation(
                    trace_phase,
                    summary=f"Semantic target {resolution.get('semantic_control_id') or 'unidentified'} approved for {action}",
                    source="semantic_action_gate",
                    details={
                        "semantic_control_id": resolution.get("semantic_control_id") or "",
                        "confidence": resolution.get("confidence"),
                        "margin": resolution.get("margin"),
                        "status": resolution.get("status") or "",
                        "revalidation": revalidation.get("status") or "",
                    },
                )
            except Exception:
                pass
        return resolution, fresh_loc, fresh_selector, revalidation
    finally:
        if phase and not previous_phase:
            try:
                session._active_phase_name = previous_phase
            except Exception:
                pass


async def _semantic_commit(
    page: Page, *, resolution: Dict[str, Any], action: str, exact_value_verified: bool, phase: str = ""
) -> Dict[str, Any]:
    session = getattr(page, "_hip_browser_session", None)
    if session is None or not hasattr(session, "_semantic_post_action_verify") or not resolution.get("semantic_control_id"):
        return {"pass": True, "status": "not_required", "confidence": 1.0}
    previous_phase = getattr(session, "_active_phase_name", "")
    if phase and not previous_phase:
        try:
            session._active_phase_name = phase
        except Exception:
            pass
    try:
        effect = await session._semantic_post_action_verify(
            resolution=resolution, action=action, exact_value_verified=exact_value_verified
        )
        trace = getattr(session, "mission_trace", None)
        trace_phase = str(getattr(session, "_active_phase_name", "") or phase or "")
        if trace is not None and trace_phase:
            try:
                trace.record_observation(
                    trace_phase,
                    summary=f"Semantic effect verified: {effect.get('effect_type') or 'effect'}",
                    source="semantic_action_gate",
                    details={
                        "semantic_control_id": resolution.get("semantic_control_id") or "",
                        "effect_pass": bool(effect.get("pass")),
                        "effect_type": effect.get("effect_type") or "",
                        "effect_confidence": effect.get("confidence"),
                    },
                )
            except Exception:
                pass
        return effect
    finally:
        if phase and not previous_phase:
            try:
                session._active_phase_name = previous_phase
            except Exception:
                pass

async def set_text_controls_batch(page: Page, items: List[Dict[str, Any]], *, phase: str = "") -> Dict[str, Any]:
    """Fill a batch without bypassing the governed physical interaction broker.

    V228's browser_fill_form optimization skipped BrowserSession, so a phase could
    report zero fills even when a downstream driver touched controls. V229 performs
    one brokered transaction per field so every fill has AutoWebGLM, semantic proof,
    PyAutoGUI-MCP-first execution, exact-value verification and trace provenance.
    """
    rows = [dict(x) for x in items if isinstance(x, dict) and x.get("selector") and x.get("value") is not None]
    if not rows:
        return {"pass": True, "status": "no_fields", "field_count": 0, "executor": "browser-session-broker"}
    results: List[Dict[str, Any]] = []
    for row in rows:
        selector = str(row.get("selector") or "")
        value = str(row.get("value") or "")
        label = str(row.get("label") or row.get("field") or "HIP Portal text field")
        ok = await set_text_control(page, None, selector, value, phase=phase)
        proof = read_last_control_execution(page)
        results.append({"label": label, "selector": selector, "exact_verified": bool(ok), "executor": proof.get("executor") or "", "authoritative": bool(proof.get("authoritative"))})
        if not ok:
            return {"pass": False, "status": "brokered_field_failed", "field_count": len(rows), "completed_count": sum(1 for x in results if x.get("exact_verified")), "executor": "browser-session-broker", "fields": results, "values_stored": False}
    return {"pass": True, "status": "verified", "field_count": len(rows), "completed_count": len(results), "executor": "browser-session-broker", "fields": results, "values_stored": False}


async def set_text_control(page: Page, root: Locator | None, selector: str, value: str, *, phase: str = "") -> bool:
    """Set one text control through the single governed BrowserSession broker."""
    if not selector:
        return False
    await _ensure_page_interactable(page, action="fill text control", selector=selector)
    loc = page.locator(selector).first
    if not await loc.count():
        return False
    # Explicitly expose the planner decision at the DDS layer too. BrowserSession
    # revalidates it again immediately before the physical dispatch.
    await _autowebglm_primary_gate(page, action="fill", selector=selector, label=f"HIP Portal text field ({phase or 'form'})", value=str(value))
    # if semantic_runtime_enabled(page): the broker is fail-closed; there is no
    # raw ``await page.evaluate`` value-setter fallback on a governed HIP page.
    # Playwright MCP ``find_ref`` remains an independent verifier inside BrowserSession.
    if not await _broker_fill(page, selector, str(value), label=f"HIP Portal text field ({phase or 'form'})", phase=phase, action_type="fill"):
        return False
    loc = page.locator(selector).first
    actual = await _read_control_value(loc) if await loc.count() else ""
    exact = _option_semantic_key(actual) == _option_semantic_key(value) or str(value).strip().lower() == str(actual).strip().lower()
    if exact:
        await _lock_filled_value(page, selector, str(value), combo=False)
    return bool(exact)

def _option_semantic_key(value: Any) -> str:
    """Normalize a DDS option/value without depending on presentation punctuation.

    HIP input contracts intentionally use stable enum-like values such as
    ``TRANSACTION_ROOT_ELEMENT`` and ``ELEMENT_IN_PAYLOAD`` while DDS renders
    human labels such as ``Transaction Root Element`` and ``Element In Payload``.
    Treat those as the same semantic option, but do not accept arbitrary
    substring matches.
    """
    text = str(value or "").strip().lower()
    text = re.sub(r"[_\-/]+", " ", text)
    text = re.sub(r"[^a-z0-9.()]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _combobox_value_variants(value: str) -> List[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    variants: List[str] = []

    def add(v: str) -> None:
        v = re.sub(r"\s+", " ", str(v or "").strip())
        if v and v.lower() not in {x.lower() for x in variants}:
            variants.append(v)

    add(raw)
    # Enum/value -> DDS display-label variants.
    if "_" in raw or "-" in raw:
        spaced = re.sub(r"[_-]+", " ", raw)
        add(spaced)
        add(" ".join(token.capitalize() if token else token for token in spaced.lower().split(" ")))
    # Some DDS lists render ``Name (1.0)`` while input data uses ``Name(1.0)``.
    add(re.sub(r"\s*\(\s*([0-9.]+)\s*\)\s*$", r" (\1)", raw))
    # Base-name fallbacks are retained only as *candidate* variants. The option
    # selector below requires the winning candidate to be unique, so a list with
    # multiple versions cannot be selected from the base name alone.
    add(re.sub(r"\s*\(\s*[0-9.]+\s*\)\s*$", "", raw))
    if "," in raw:
        add(raw.split(",", 1)[0])
    return variants


async def _read_control_value(locator: Locator) -> str:
    try:
        return str(await locator.evaluate("""el => {
          if (!el) return '';
          const tag=(el.tagName||'').toLowerCase();
          if (tag === 'select') return (el.options && el.selectedIndex >= 0) ? (el.options[el.selectedIndex].text || el.value || '') : (el.value || '');
          return el.value || el.getAttribute('aria-valuetext') || el.getAttribute('data-value') || el.getAttribute('title') || '';
        }"""))
    except Exception:
        try:
            return str(await locator.input_value(timeout=700))
        except Exception:
            return ""


def _value_matches_variants(current: Any, variants: List[str]) -> bool:
    key = _option_semantic_key(current)
    if not key or key in {"select", "select...", "choose", "select one", "none"}:
        return False
    return any(key == _option_semantic_key(v) for v in variants if _option_semantic_key(v))


async def _dds_single_select_snapshot(page: Page, selector: str) -> Dict[str, Any]:
    """Return commit evidence for one *owned* DDS single-select.

    DDS often portals the listbox outside ``dds-dropdown``.  The old collector
    searched only descendants of the component (or, during clicking, the whole
    page), which could both miss the real committed option and click an option
    belonging to another dropdown.  This snapshot follows ``aria-controls`` /
    ``aria-owns`` and assigns unique current-run tokens to the owned options.
    """
    try:
        result = await page.evaluate(r"""
(selector) => {
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim();
  const visible=el=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||'1')!==0);};
  const input=document.querySelector(selector);
  if(!input) return {found:false, options:[], selected_values:[], committed_candidates:[]};
  const dd=input.closest('dds-dropdown,app-generic-dropdown,[data-dds-dropdown]');
  const listId=input.getAttribute('aria-controls') || input.getAttribute('aria-owns') || '';
  let list=(listId && document.getElementById(listId)) || null;
  if(!list && dd) list=dd.querySelector('[role=listbox],.dds__dropdown__list,.dds__menu');
  const optionRoot=list || dd;
  const optionEls=optionRoot ? Array.from(optionRoot.querySelectorAll('[role=option],button.dds__dropdown__item-option,.dds__dropdown__item-option,li[role=option]')) : [];
  const options=optionEls.map((el,index)=>{
    let token=el.getAttribute('data-hip-single-option-token')||'';
    if(!token){token=`hip-single-opt-${Date.now()}-${index}-${Math.random().toString(36).slice(2,8)}`;el.setAttribute('data-hip-single-option-token',token);}
    const cls=el.classList||{contains:()=>false};
    const selected=el.getAttribute('aria-selected')==='true' || el.getAttribute('data-selected')==='true' || el.getAttribute('aria-checked')==='true' || cls.contains('dds__dropdown__item-selected') || cls.contains('dds__dropdown__item--selected');
    return {text:clean(el.innerText||el.textContent),selected,disabled:el.getAttribute('aria-disabled')==='true'||!!el.disabled,visible:visible(el),id:el.id||'',aria_posinset:el.getAttribute('aria-posinset')||'',token};
  }).filter(x=>x.text);
  const selectedOptions=options.filter(x=>x.selected).map(x=>x.text);
  const selectedLabels=[];
  if(dd){
    for(const el of Array.from(dd.querySelectorAll('.dds__tag,.dds__chip,[class*=selected-value],[class*=selection__label],[class*=dropdown__selection]'))){
      const t=clean(el.innerText||el.textContent);
      if(t && !/^\d+\s+selected$/i.test(t) && t.toLowerCase()!=='select all' && !selectedLabels.some(x=>x.toLowerCase()===t.toLowerCase())) selectedLabels.push(t);
    }
  }
  const rawValue=clean(input.value||input.getAttribute('aria-valuetext')||input.getAttribute('data-value')||input.getAttribute('title')||'');
  const committed=[];
  for(const t of [rawValue,...selectedOptions,...selectedLabels]) if(t && !committed.some(x=>x.toLowerCase()===t.toLowerCase())) committed.push(t);
  return {
    found:true,
    selector,
    input_value:rawValue,
    list_id:(list&&list.id)||listId,
    expanded:input.getAttribute('aria-expanded')==='true' || !!(list&&visible(list)),
    aria_activedescendant:input.getAttribute('aria-activedescendant')||'',
    readonly:!!input.readOnly,
    disabled:!!input.disabled||input.getAttribute('aria-disabled')==='true',
    options,
    selected_values:selectedOptions,
    selected_labels:selectedLabels,
    committed_candidates:committed,
  };
}
""", selector)
        return dict(result or {}) if isinstance(result, dict) else {}
    except Exception:
        return {}


def _single_select_snapshot_matches(snapshot: Dict[str, Any], variants: List[str]) -> bool:
    for value in snapshot.get("committed_candidates", []) if isinstance(snapshot, dict) else []:
        if _value_matches_variants(value, variants):
            return True
    return False


def _single_option_rank(text: str, raw_value: str, variants: List[str]) -> int:
    key = _option_semantic_key(text)
    raw_key = _option_semantic_key(raw_value)
    if key and raw_key and key == raw_key:
        return 100
    variant_keys = [_option_semantic_key(v) for v in variants if _option_semantic_key(v)]
    if key in variant_keys:
        # Earlier variants are stronger (raw/display-enum forms precede base-name).
        return max(60, 95 - variant_keys.index(key) * 5)
    # Only tolerate a version suffix difference when the non-version base is exact.
    text_base = re.sub(r"\s*\(\s*[0-9.]+\s*\)\s*$", "", key).strip()
    raw_base = re.sub(r"\s*\(\s*[0-9.]+\s*\)\s*$", "", raw_key).strip()
    if text_base and raw_base and text_base == raw_base:
        return 55
    return 0


def _choose_unique_single_option(snapshot: Dict[str, Any], raw_value: str, variants: List[str]) -> Dict[str, Any] | None:
    ranked: List[tuple[int, Dict[str, Any]]] = []
    for option in snapshot.get("options", []) if isinstance(snapshot, dict) else []:
        if not isinstance(option, dict) or option.get("disabled"):
            continue
        score = _single_option_rank(str(option.get("text") or ""), raw_value, variants)
        if score > 0:
            ranked.append((score, option))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return None
    best_score = ranked[0][0]
    best = [dict(option) for score, option in ranked if score == best_score]
    # Never guess between two equally good candidates (common when multiple
    # Document Type versions share the same base label).
    return best[0] if len(best) == 1 else None


def _single_option_selector(snapshot: Dict[str, Any], option: Dict[str, Any]) -> str:
    oid = str(option.get("id") or "").strip()
    if oid:
        return f'[id="{_css_attr_value(oid)}"]'
    token = str(option.get("token") or "").strip()
    if token:
        return f'[data-hip-single-option-token="{_css_attr_value(token)}"]'
    list_id = str(snapshot.get("list_id") or "").strip()
    pos = str(option.get("aria_posinset") or "").strip()
    if list_id and pos:
        return f'[id="{_css_attr_value(list_id)}"] [role="option"][aria-posinset="{_css_attr_value(pos)}"]'
    return ""


async def read_last_single_select_audit(page: Page) -> Dict[str, Any]:
    audit = getattr(page, "_hip_last_single_select_audit", None)
    return dict(audit) if isinstance(audit, dict) else {}


async def _set_single_select_audit(page: Page, audit: Dict[str, Any]) -> None:
    try:
        setattr(page, "_hip_last_single_select_audit", dict(audit))
    except Exception:
        pass


async def _combobox_value_matches(locator: Locator, variants: List[str], *, page: Page | None = None, selector: str = "") -> bool:
    current = await _read_control_value(locator)
    if _value_matches_variants(current, variants):
        return True
    if page is not None and selector:
        snapshot = await _dds_single_select_snapshot(page, selector)
        return _single_select_snapshot_matches(snapshot, variants)
    return False


async def _open_owned_single_select(page: Page, selector: str, backend: Any = None, *, phase: str = "") -> Dict[str, Any]:
    snapshot = await _dds_single_select_snapshot(page, selector)
    if snapshot.get("expanded") and snapshot.get("options"):
        return snapshot
    await _broker_click(page, selector, label="HIP Portal DDS combobox", phase=phase, mutation_risk=False)
    deadline = asyncio.get_running_loop().time() + 3.2
    last = snapshot
    while asyncio.get_running_loop().time() < deadline:
        last = await _dds_single_select_snapshot(page, selector)
        if last.get("options"):
            return last
        await page.wait_for_timeout(90)
    return last


async def _click_owned_single_option(page: Page, snapshot: Dict[str, Any], option: Dict[str, Any], backend: Any = None, *, phase: str = "") -> bool:
    """Click one uniquely-owned DDS option through BrowserSession/PyAutoGUI primary."""
    option_selector = _single_option_selector(snapshot, option)
    if not option_selector:
        return False
    return await _broker_click(page, option_selector, label=f"HIP Portal DDS option {option.get('text') or ''}", phase=phase, mutation_risk=False)


async def select_dds_combobox(page: Page, root: Locator | None, selector: str, value: str, *, phase: str = "") -> bool:
    """Select one DDS value using the combobox-owned listbox and prove commit.

    Key properties:
    * AutoWebGLM approves the vetted select intent;
    * BrowserSession executes PyAutoGUI MCP first on the same authenticated desktop;
    * Playwright MCP and deterministic Python Playwright remain verified fallbacks;
    * only the listbox owned by this combobox is searched/clicked;
    * enum input values (``ELEMENT_IN_PAYLOAD``) match human DDS labels;
    * a blank search textbox does not erase a valid selected-option/chip commit;
    * Enter is never pressed blindly when no unique exact candidate exists;
    * failure returns False so AgentQ/recovery can choose a different action.
    """
    if not selector or value in (None, ""):
        return False
    await _ensure_page_interactable(page, action=f"select DDS combobox {value}", selector=selector)
    raw_value = str(value)
    variants = _combobox_value_variants(raw_value)
    if not variants:
        return False
    backend = getattr(page, "_hip_playwright_mcp_backend", None)
    loc = page.locator(selector).first
    audit: Dict[str, Any] = {
        "selector": selector,
        "requested_value": raw_value,
        "variants": variants,
        "phase": phase,
        "success": False,
        "executor": "autowebglm->browser-session->pyautogui-mcp-primary->playwright-mcp-fallback",
        "attempts": [],
    }
    try:
        if not await loc.count():
            audit["reason"] = "combobox selector not found"
            await _set_single_select_audit(page, audit)
            return False
        semantic_resolution, loc, selector, semantic_revalidation = await _semantic_prepare(
            page, action="select", selector=selector,
            label=f"HIP Portal DDS combobox ({phase or 'form'})", phase=phase, locator=loc
        )
        audit["semantic_gate"] = {
            "semantic_control_id": semantic_resolution.get("semantic_control_id") or "",
            "confidence": semantic_resolution.get("confidence"),
            "margin": semantic_resolution.get("margin"),
            "status": semantic_resolution.get("status") or "",
            "revalidation": semantic_revalidation.get("status") or "",
        }
        chosen_for_memory = ""
        choice_snapshot: Dict[str, Any] = {}
        async def _semantic_success() -> bool:
            effect = await _semantic_commit(
                page, resolution=semantic_resolution, action="select", exact_value_verified=True, phase=phase
            )
            audit["semantic_effect"] = {
                "pass": bool(effect.get("pass")),
                "type": effect.get("effect_type") or "",
                "confidence": effect.get("confidence"),
            }
            if effect.get("pass") and chosen_for_memory and choice_snapshot:
                _record_stage4_portal_choice(
                    page, phase=phase, resolution=semantic_resolution, snapshot=choice_snapshot,
                    chosen_text=chosen_for_memory, effect_type=str(effect.get("effect_type") or "dropdown_value_committed"),
                    confidence=float(effect.get("confidence") or 0.0),
                )
            return True
        tag_name = str(await loc.evaluate("el => (el.tagName || '').toLowerCase()"))
        previous_snapshot = await _dds_single_select_snapshot(page, selector)
        if await _combobox_value_matches(loc, variants, page=page, selector=selector):
            committed = next((x for x in previous_snapshot.get("committed_candidates", []) if _value_matches_variants(x, variants)), raw_value)
            await _lock_filled_value(page, selector, str(committed or raw_value), combo=True)
            audit.update({"success": True, "reason": "already committed", "commit_snapshot": previous_snapshot})
            await _set_single_select_audit(page, audit)
            return await _semantic_success()

        awg_decision = await _autowebglm_primary_gate(
            page, action="select", selector=selector, label=f"HIP Portal DDS combobox ({phase or 'form'})", value=raw_value
        )
        audit["autowebglm_primary"] = {
            "status": awg_decision.get("status"),
            "framework": awg_decision.get("framework"),
            "command": awg_decision.get("command"),
            "aligned": awg_decision.get("aligned"),
        }

        if tag_name == "select":
            # Native selects also stay on the physical broker: click, Home, move to
            # the exact DOM-proven option index, Enter, then verify committed value.
            options = await loc.evaluate("el => Array.from(el.options||[]).map((o,i)=>({i,text:(o.text||'').trim(),value:String(o.value||''),disabled:!!o.disabled}))")
            chosen = None
            for row in options or []:
                if row.get("disabled"):
                    continue
                if _value_matches_variants(str(row.get("text") or ""), variants) or _value_matches_variants(str(row.get("value") or ""), variants):
                    if chosen is not None:
                        # Ambiguous exact semantic match: fail closed.
                        chosen = None
                        break
                    chosen = row
            if chosen is None:
                audit["reason"] = "native select had no unique exact option"
                await _set_single_select_audit(page, audit)
                return False
            if not await _broker_click(page, selector, label="HIP Portal native dropdown", phase=phase, mutation_risk=False):
                audit["reason"] = "native select broker click failed"
                await _set_single_select_audit(page, audit)
                return False
            if not await _broker_press(page, selector, "Home", label="native select home", phase=phase):
                audit["reason"] = "native select Home failed"
                await _set_single_select_audit(page, audit)
                return False
            for _ in range(int(chosen.get("i") or 0)):
                if not await _broker_press(page, selector, "ArrowDown", label="native select next option", phase=phase):
                    audit["reason"] = "native select ArrowDown failed"
                    await _set_single_select_audit(page, audit)
                    return False
            if not await _broker_press(page, selector, "Enter", label="native select commit", phase=phase):
                audit["reason"] = "native select Enter failed"
                await _set_single_select_audit(page, audit)
                return False
            await page.wait_for_timeout(180)
            if await _combobox_value_matches(loc, variants, page=page, selector=selector):
                await _lock_filled_value(page, selector, raw_value, combo=True)
                proof = read_last_control_execution(page)
                audit.update({"success": True, "reason": "physical native select committed", "selected_term": chosen.get("text"), "actual_executor": proof.get("executor") or "browser-session-broker"})
                await _set_single_select_audit(page, audit)
                return await _semantic_success()
            audit["reason"] = "native select physical commit did not verify"
            await _set_single_select_audit(page, audit)
            return False

        # Open this DDS control and inspect only its owned listbox.
        snapshot = await _open_owned_single_select(page, selector, backend, phase=phase)
        option = _choose_unique_single_option(snapshot, raw_value, variants)
        dynamic_decision: Dict[str, Any] = {}
        if option is None and snapshot.get("options"):
            option, dynamic_decision = _stage4_choose_single_option(
                page, phase=phase, resolution=semantic_resolution, snapshot=snapshot, raw_value=raw_value
            )
        audit["dynamic_option_decision"] = dynamic_decision
        audit["attempts"].append({
            "stage": "owned_listbox_initial",
            "option_count": len(snapshot.get("options", [])),
            "chosen_option": (option or {}).get("text"),
            "list_id": snapshot.get("list_id"),
            "dynamic_source": dynamic_decision.get("source") if dynamic_decision else "deterministic_exact_match",
        })
        if option is not None and await _click_owned_single_option(page, snapshot, option, backend, phase=phase):
            await page.wait_for_timeout(220)
            committed_snapshot = await _dds_single_select_snapshot(page, selector)
            if await _combobox_value_matches(loc, variants, page=page, selector=selector):
                committed = next((x for x in committed_snapshot.get("committed_candidates", []) if _value_matches_variants(x, variants)), option.get("text") or raw_value)
                await _lock_filled_value(page, selector, str(committed), combo=True)
                audit.update({"success": True, "reason": "owned DDS option committed", "chosen_option": option.get("text"), "commit_snapshot": committed_snapshot})
                chosen_for_memory = str(option.get("text") or committed or raw_value)
                choice_snapshot = dict(snapshot)
                await _set_single_select_audit(page, audit)
                return await _semantic_success()

        # Search only inside this combobox, then require one unique candidate.
        # Every click/type is dispatched by BrowserSession, so PyAutoGUI MCP is
        # genuinely first instead of being bypassed inside the DDS driver.
        for term in variants:
            try:
                await close_open_dropdown(page, phase)
                if not await _broker_click(page, selector, label="HIP Portal DDS combobox", phase=phase, mutation_risk=False):
                    audit["attempts"].append({"stage": "broker_open_failed", "term": term})
                    continue
                if not await _broker_fill(page, selector, term, label="HIP Portal DDS combobox search", phase=phase, action_type="search"):
                    audit["attempts"].append({"stage": "broker_search_fill_failed", "term": term})
                    continue
                await page.wait_for_timeout(320)
                searched = await _dds_single_select_snapshot(page, selector)
                option = _choose_unique_single_option(searched, raw_value, variants)
                dynamic_search_decision: Dict[str, Any] = {}
                if option is None and searched.get("options"):
                    option, dynamic_search_decision = _stage4_choose_single_option(
                        page, phase=phase, resolution=semantic_resolution, snapshot=searched, raw_value=raw_value
                    )
                audit["attempts"].append({
                    "stage": "brokered_owned_listbox_search", "term": term,
                    "option_count": len(searched.get("options", [])),
                    "chosen_option": (option or {}).get("text"), "list_id": searched.get("list_id"),
                    "dynamic_source": dynamic_search_decision.get("source") if dynamic_search_decision else "deterministic_exact_match",
                })
                if option is None:
                    continue
                if not await _click_owned_single_option(page, searched, option, backend, phase=phase):
                    continue
                await page.wait_for_timeout(220)
                committed_snapshot = await _dds_single_select_snapshot(page, selector)
                if await _combobox_value_matches(loc, variants, page=page, selector=selector):
                    committed = next((x for x in committed_snapshot.get("committed_candidates", []) if _value_matches_variants(x, variants)), option.get("text") or raw_value)
                    await _lock_filled_value(page, selector, str(committed), combo=True)
                    proof = read_last_control_execution(page)
                    audit.update({
                        "success": True, "reason": "BrowserSession broker search + owned DDS option committed",
                        "chosen_option": option.get("text"), "commit_snapshot": committed_snapshot,
                        "actual_executor": proof.get("executor") or "browser-session-broker",
                    })
                    chosen_for_memory = str(option.get("text") or committed or raw_value)
                    choice_snapshot = dict(searched)
                    await _set_single_select_audit(page, audit)
                    return await _semantic_success()
            except Exception as exc:
                audit["attempts"].append({"stage": "broker_search_exception", "term": term, "error": mask_sensitive_string(str(exc))})
                continue

        # No free-text/set-value fallback for a true DDS single-select.  Directly
        # assigning the search input can look filled while Angular still holds no
        # selected option—the exact failure mode seen in the live Document Type
        # runs.  Restore an earlier valid selection if search temporarily cleared it.
        previous_committed = next((x for x in previous_snapshot.get("committed_candidates", []) if x and _option_semantic_key(x) not in {"select", "choose"}), "")
        if previous_committed:
            try:
                await _restore_previous_value(page, selector, previous_committed)
            except Exception:
                pass
        await close_open_dropdown(page, phase)
        audit.update({"success": False, "reason": "no unique owned DDS option reached exact committed state", "final_snapshot": await _dds_single_select_snapshot(page, selector)})
        await _set_single_select_audit(page, audit)
        return False
    except Exception as exc:
        await close_open_dropdown(page, phase)
        audit.update({"success": False, "reason": "single-select exception", "error": mask_sensitive_string(str(exc))})
        await _set_single_select_audit(page, audit)
        return False

async def upload_file_control(page: Page, root: Locator | None, field_name_or_label: str, file_path: str | Path, *, phase: str = "") -> Dict[str, Any]:
    p = Path(str(file_path))
    audit: Dict[str, Any] = {"field": field_name_or_label, "asset_path": str(p), "filled": False, "upload_attempted": True}
    if not p.exists():
        audit["reason"] = "file path does not exist"
        return audit
    label = field_name_or_label.lower()
    stable: List[str] = []
    if "map" in label and "schema" not in label:
        stable += ['input[type="file"][name="mapData"]', 'input[type="file"][id*="mapData" i]', 'input[type="file"][id*="map" i]']
    if "input" in label or "source" in label:
        stable += ['input[type="file"][name="inputSchema"]', 'input[type="file"][id*="inputSchema" i]', 'input[type="file"][id*="source" i]']
    if "output" in label or "target" in label:
        stable += ['input[type="file"][name="outputSchema"]', 'input[type="file"][id*="outputSchema" i]', 'input[type="file"][id*="target" i]']
    stable.append('input[type="file"]')
    root_sel = None
    try:
        root_info = await active_form_root_info(page, phase or "data_map")
        root_sel = str(root_info.get("selector") or "body")
    except Exception:
        root_sel = "body"
    for sel in stable:
        scoped = f"{root_sel} {sel}" if root_sel and root_sel != "body" else sel
        try:
            locs = page.locator(scoped)
            n = await locs.count()
            for i in range(n):
                loc = locs.nth(i)
                semantic_resolution, loc, semantic_selector, semantic_revalidation = await _semantic_prepare(
                    page, action="upload", selector=scoped, label=field_name_or_label or "HIP file upload", phase=phase, locator=loc
                )
                await _autowebglm_primary_gate(
                    page, action="upload", selector=semantic_selector, label=field_name_or_label or "HIP file upload"
                )
                await loc.set_input_files(str(p), timeout=12000)
                await page.wait_for_timeout(350)
                visible = False
                try:
                    visible = bool(await page.evaluate("""
(name) => {
  const body=(document.body.innerText||document.body.textContent||'');
  const inputs=Array.from(document.querySelectorAll('input[type=file]')).map(i=>Array.from(i.files||[]).map(f=>f.name).join(' ')).join(' ');
  return body.includes(name) || inputs.includes(name);
}
""", p.name))
                except Exception:
                    pass
                if not visible:
                    raise RuntimeError("HIP file upload did not become visible/committed on the semantically resolved control")
                semantic_effect = await _semantic_commit(
                    page, resolution=semantic_resolution, action="upload", exact_value_verified=True, phase=phase
                )
                audit.update({
                    "filled": True, "success": True, "uploaded_file_name": p.name, "file_input_selector": semantic_selector,
                    "filename_visible_after_upload": visible, "reason": "uploaded into semantically verified active form input[type=file]",
                    "semantic_gate": {
                        "semantic_control_id": semantic_resolution.get("semantic_control_id") or "",
                        "confidence": semantic_resolution.get("confidence"), "margin": semantic_resolution.get("margin"),
                        "status": semantic_resolution.get("status") or "", "revalidation": semantic_revalidation.get("status") or "",
                    },
                    "semantic_effect": {"pass": bool(semantic_effect.get("pass")), "effect_type": semantic_effect.get("effect_type") or "", "confidence": semantic_effect.get("confidence")},
                })
                return audit
        except Exception as exc:
            audit["last_error"] = mask_sensitive_string(str(exc))
            continue
    audit["reason"] = "no usable input[type=file] found in active form root"
    return audit

async def click_visible_tab(page: Page, root: Locator | None, aliases: List[str], *, phase: str = "") -> Dict[str, Any]:
    audit = {"clicked": False, "alias": "", "selector": "", "executor": ""}
    for a in aliases:
        for sel in [f"[role=tab]:has-text('{a}')", f"button:has-text('{a}')", f"a:has-text('{a}')", f"text=/{re.escape(a)}/i"]:
            try:
                loc = page.locator(sel).first
                if not (await loc.count() and await loc.is_visible(timeout=800)):
                    continue
                if not await _broker_click(page, sel, label=f"HIP Portal tab {a}", phase=phase, mutation_risk=False):
                    continue
                proof = read_last_control_execution(page)
                await page.wait_for_timeout(650)
                audit.update({"clicked": True, "alias": a, "selector": sel, "executor": proof.get("executor") or "browser-session-broker", "authoritative": proof.get("authoritative")})
                return audit
            except Exception as exc:
                audit["last_error"] = mask_sensitive_string(str(exc))
    return audit


def _css_attr_value(value: Any) -> str:
    """Escape a string for use inside a double-quoted CSS attribute selector."""
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _multiselect_selected_values_from_snapshot(snapshot: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for option in snapshot.get("options", []) if isinstance(snapshot, dict) else []:
        if not isinstance(option, dict) or not option.get("selected"):
            continue
        text = re.sub(r"\s+", " ", str(option.get("text") or "").strip())
        if text and text.lower() != "select all" and text.lower() not in {x.lower() for x in out}:
            out.append(text)
    for text_value in snapshot.get("selected_labels", []) if isinstance(snapshot, dict) else []:
        text = re.sub(r"\s+", " ", str(text_value or "").strip())
        if re.fullmatch(r"\d+\s+selected", text, flags=re.I):
            continue
        if text and text.lower() != "select all" and text.lower() not in {x.lower() for x in out}:
            out.append(text)
    return out


def _find_multiselect_option(snapshot: Dict[str, Any], value: str) -> Optional[Dict[str, Any]]:
    target = re.sub(r"\s+", " ", str(value or "").strip()).lower()
    for option in snapshot.get("options", []) if isinstance(snapshot, dict) else []:
        if not isinstance(option, dict):
            continue
        text = re.sub(r"\s+", " ", str(option.get("text") or "").strip()).lower()
        if text == target:
            return dict(option)
    return None


def _unique_multiselect_option_selector(snapshot: Dict[str, Any], option: Dict[str, Any]) -> str:
    """Return a selector that resolves to exactly one DDS option.

    HIP DDS wraps each option in its own element.  A selector such as
    ``#list button:nth-of-type(1)`` therefore matches the first button inside
    *every* wrapper and triggers Playwright strict-mode violations.  DDS exposes
    ``aria-posinset`` on each option, which is stable and unique within the
    associated listbox, so prefer that contract.
    """
    option_id = str(option.get("id") or "").strip()
    if option_id:
        return f'[id="{_css_attr_value(option_id)}"]'
    list_id = str(snapshot.get("list_id") or "").strip()
    position = str(option.get("aria_posinset") or "").strip()
    if list_id and position:
        return (
            f'[id="{_css_attr_value(list_id)}"] '
            f'[role="option"][aria-posinset="{_css_attr_value(position)}"]'
        )
    token = str(option.get("token") or "").strip()
    if token:
        return f'[data-hip-option-token="{_css_attr_value(token)}"]'
    return ""


def _multiselect_delta(wanted: List[str], selected: List[str]) -> Dict[str, List[str]]:
    wanted_map = {str(x).strip().lower(): str(x).strip() for x in wanted if str(x).strip()}
    selected_map = {str(x).strip().lower(): str(x).strip() for x in selected if str(x).strip()}
    return {
        "missing": [wanted_map[k] for k in wanted_map if k not in selected_map],
        "extra": [selected_map[k] for k in selected_map if k not in wanted_map],
    }


async def _dds_multiselect_snapshot(page: Page, selector: str) -> Dict[str, Any]:
    try:
        result = await page.evaluate(r"""
(selector) => {
  const input=document.querySelector(selector);
  const dd=input && input.closest('dds-dropdown');
  if(!input || !dd) return {found:false, list_id:'', expanded:false, search_value:'', options:[], selected_labels:[]};
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim();
  const listId=input.getAttribute('aria-controls') || '';
  const list=(listId && document.getElementById(listId)) || dd.querySelector('[role=listbox],.dds__dropdown__list');
  const optionRoot=list || dd;
  const optionEls=Array.from(optionRoot.querySelectorAll('[role=option],button.dds__dropdown__item-option,.dds__dropdown__item-option'));
  const options=optionEls.map((el,index)=>{
    const iconSelected=!!el.querySelector('.dds__dropdown__item-selected,[class*="item-selected"]');
    const selected=el.getAttribute('aria-selected')==='true' || el.getAttribute('data-selected')==='true' || el.getAttribute('aria-checked')==='true' || iconSelected;
    let token=el.getAttribute('data-hip-option-token') || '';
    if(!token){token=`hip-opt-${Date.now()}-${index}-${Math.random().toString(36).slice(2,8)}`;el.setAttribute('data-hip-option-token',token);}
    return {
      text:clean(el.innerText||el.textContent),
      selected,
      disabled:el.getAttribute('aria-disabled')==='true' || !!el.disabled,
      id:el.id||'',
      aria_posinset:el.getAttribute('aria-posinset')||'',
      aria_setsize:el.getAttribute('aria-setsize')||'',
      token
    };
  });
  const selectedLabels=[];
  for(const el of Array.from(dd.querySelectorAll('.dds__tag,.dds__chip,[class*="selected-value"],[class*="selection__label"]'))){
    const text=clean(el.innerText||el.textContent);
    if(text && !/^\d+\s+selected$/i.test(text) && !selectedLabels.some(x=>x.toLowerCase()===text.toLowerCase())) selectedLabels.push(text);
  }
  const popup=dd.querySelector('.dds__dropdown__popup,[role=presentation]');
  const popupHidden=!!(popup && (popup.classList.contains('dds__dropdown__popup--hidden') || popup.getAttribute('aria-hidden')==='true'));
  const selectionMode = clean(dd.getAttribute('selection') || input.getAttribute('aria-multiselectable') || '');
  const isMultiple = selectionMode.toLowerCase()==='multiple' || input.getAttribute('aria-multiselectable')==='true' || !!dd.querySelector('.dds__dropdown--is-multiple');
  const loadingEls=Array.from(dd.querySelectorAll('[aria-busy=true],.dds__loading,.dds__spinner,[class*=loading],[class*=spinner]'));
  const visibleLoading=loadingEls.some(el=>{const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||'1')!==0);});
  const loading = input.getAttribute('aria-busy')==='true' || dd.getAttribute('aria-busy')==='true' || visibleLoading;
  const setSizes=options.map(x=>parseInt(x.aria_setsize||'0',10)).filter(x=>Number.isFinite(x)&&x>0);
  const declaredSetSize=setSizes.length?Math.max(...setSizes):0;
  const summary=clean(dd.innerText||dd.textContent);
  const countMatch=summary.match(/(\d+)\s+selected/i);
  return {
    found:true,
    list_id:(list&&list.id)||listId,
    expanded:input.getAttribute('aria-expanded')==='true' || !popupHidden,
    search_value:String(input.value||''),
    options,
    selected_labels:selectedLabels,
    summary_text:summary,
    selection_mode:isMultiple?'multiple':'single',
    loading,
    declared_set_size:declaredSetSize,
    visible_option_count:options.length,
    selected_count_summary:countMatch?parseInt(countMatch[1],10):null,
    has_select_all:options.some(x=>clean(x.text).toLowerCase()==='select all')
  };
}
""", selector)
        return dict(result or {}) if isinstance(result, dict) else {}
    except Exception:
        return {}


async def _selected_multiselect_values(page: Page, selector: str) -> List[str]:
    return _multiselect_selected_values_from_snapshot(await _dds_multiselect_snapshot(page, selector))


async def _open_dds_multiselect(page: Page, selector: str, backend: Any, *, phase: str = "") -> Dict[str, Any]:
    snapshot = await _dds_multiselect_snapshot(page, selector)
    if snapshot.get("expanded") and snapshot.get("options"):
        return snapshot
    if not await _broker_click(page, selector, label="HIP Portal DDS multi-select", phase=phase, mutation_risk=False):
        return snapshot
    await page.wait_for_timeout(220)
    return await _dds_multiselect_snapshot(page, selector)


async def _set_dds_multiselect_search(page: Page, selector: str, value: str, backend: Any, *, phase: str = "") -> None:
    """Set DDS search text through the physical broker without deleting selected chips."""
    loc = page.locator(selector).first
    try: current = str(await loc.input_value(timeout=1200) or "")
    except Exception: current = ""
    target = str(value or "")
    if current == target:
        return
    # BrowserSession.fill_and_log uses click + Ctrl+A + write for PyAutoGUI, which
    # is safe here because it edits the search text, not selected chips.
    await _broker_fill(page, selector, target, label="HIP Portal DDS multi-select search", phase=phase, action_type="search")
    await page.wait_for_timeout(220)


async def _click_dds_multiselect_option(page: Page, selector: str, value: str, backend: Any, *, phase: str = "") -> bool:
    if not selector:
        return False
    return await _broker_click(page, selector, label=f"HIP Portal multi-select option {value}", phase=phase, mutation_risk=False)


async def _publish_multiselect_audit(page: Page, audit: Dict[str, Any]) -> None:
    try:
        await page.evaluate("audit => { window.__HIP_LAST_MULTISELECT_AUDIT = audit; }", audit)
    except Exception:
        pass


async def read_last_multiselect_audit(page: Page) -> Dict[str, Any]:
    try:
        result = await page.evaluate("() => window.__HIP_LAST_MULTISELECT_AUDIT || {}")
        return dict(result or {}) if isinstance(result, dict) else {}
    except Exception:
        return {}


async def _wait_dds_multiselect_snapshot_stable(
    page: Page,
    selector: str,
    *,
    timeout_ms: int = 2600,
    stable_samples: int = 2,
) -> Dict[str, Any]:
    """Wait until selection and option-universe state stop changing."""
    deadline = asyncio.get_running_loop().time() + max(0.3, timeout_ms / 1000.0)
    previous = None
    stable = 0
    last: Dict[str, Any] = {}
    samples = 0
    while asyncio.get_running_loop().time() < deadline:
        samples += 1
        last = await _dds_multiselect_snapshot(page, selector)
        selected = sorted(x.lower() for x in _multiselect_selected_values_from_snapshot(last))
        options = sorted(
            (re.sub(r"\s+", " ", str(x.get("text") or "").strip()).lower(), bool(x.get("selected")), bool(x.get("disabled")))
            for x in last.get("options", []) if isinstance(x, dict)
        )
        digest = (tuple(selected), tuple(options), bool(last.get("loading")), str(last.get("search_value") or ""))
        if digest == previous and not last.get("loading"):
            stable += 1
        else:
            stable = 1 if not last.get("loading") else 0
        previous = digest
        if stable >= stable_samples:
            return {**last, "option_universe_stable": True, "stability_samples": samples, "consecutive_samples": stable}
        await page.wait_for_timeout(90)
    return {**last, "option_universe_stable": False, "stability_samples": samples, "consecutive_samples": stable}


async def set_checkbox_value(page: Page, selector: str, desired: bool, *, label: str = "", phase: str = "") -> bool:
    """Set a checkbox/switch using the PyAutoGUI-primary broker and exact state proof."""
    if not selector:
        return False
    await _ensure_page_interactable(page, action=f"set checkbox {label or selector}", selector=selector)
    loc = page.locator(selector).first
    if not await loc.count():
        return False
    async def checked() -> bool | None:
        target = page.locator(selector).first
        try:
            return bool(await target.is_checked(timeout=1000))
        except Exception:
            try:
                return str(await target.get_attribute("aria-checked") or "").lower() == "true"
            except Exception:
                return None
    current = await checked()
    if current is None:
        return False
    if bool(current) != bool(desired):
        if not await _broker_click(page, selector, label=f"HIP Portal checkbox {label or selector}", phase=phase, mutation_risk=False):
            return False
        await page.wait_for_timeout(180)
    return (await checked()) == bool(desired)


async def select_dds_multiselect(
    page: Page,
    root: Locator | None,
    selector: str,
    values: List[str],
    *,
    phase: str = "",
) -> bool:
    """Commit an exact set in a DDS ``selection=multiple`` dropdown.

    Contract:
    * one explicit exact option click per delta item;
    * no blind Enter, Select All, bulk clear, or empty-search Backspace;
    * previously requested selections are preserved after every additive click;
    * extra selections are removed only by clicking the exact selected option;
    * the popup is reopened for an authoritative aria/data-selected verification.
    """
    wanted: List[str] = []
    duplicate_inputs: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        low = text.lower()
        if not text:
            continue
        if low == "select all" or re.fullmatch(r"\d+\s+selected", text, flags=re.I):
            await _publish_multiselect_audit(page, {"pass": False, "reason": "presentation or Select All value is not an explicit business selection", "requested": values})
            return False
        if low in seen:
            duplicate_inputs.append(text)
            continue
        seen.add(low)
        wanted.append(text)
    audit: Dict[str, Any] = {
        "schema_version": "hip.multiselect-transaction.v2",
        "selector": selector,
        "phase": phase,
        "requested": wanted,
        "duplicate_requested_values": duplicate_inputs,
        "actions": [],
        "pass": False,
    }
    if not selector or not wanted or duplicate_inputs:
        audit["reason"] = "selector/values missing or duplicate requested values"
        await _publish_multiselect_audit(page, audit)
        return False
    await _ensure_page_interactable(page, action="select DDS multi-select exact set", selector=selector)
    loc = page.locator(selector).first
    if not await loc.count():
        audit["reason"] = "multi-select input not found"
        await _publish_multiselect_audit(page, audit)
        return False
    semantic_resolution, loc, selector, semantic_revalidation = await _semantic_prepare(
        page, action="select", selector=selector,
        label=f"HIP Portal DDS multi-select ({phase or 'form'})", phase=phase, locator=loc
    )
    audit["semantic_gate"] = {
        "semantic_control_id": semantic_resolution.get("semantic_control_id") or "",
        "confidence": semantic_resolution.get("confidence"),
        "margin": semantic_resolution.get("margin"),
        "status": semantic_resolution.get("status") or "",
        "revalidation": semantic_revalidation.get("status") or "",
    }
    backend = getattr(page, "_hip_playwright_mcp_backend", None)

    initial = await _open_dds_multiselect(page, selector, backend, phase=phase)
    initial = await _wait_dds_multiselect_snapshot_stable(page, selector)
    if not initial.get("selection_mode") and initial.get("found") and initial.get("options"):
        # Compatibility for injected/test backends that predate the richer DDS
        # snapshot. The public function itself is the multi-select-only executor,
        # and the shared semantic resolver has already required selection=multiple.
        initial["selection_mode"] = "multiple"
        initial["selection_mode_inference"] = "multiselect-executor-contract"
    audit["initial_snapshot"] = initial
    if not initial.get("found") or initial.get("selection_mode") != "multiple":
        audit["reason"] = "target control is not proven to be selection=multiple"
        await close_open_dropdown(page, phase)
        await _publish_multiselect_audit(page, audit)
        return False
    if not initial.get("option_universe_stable"):
        audit["reason"] = "multi-select option universe did not stabilize"
        await close_open_dropdown(page, phase)
        await _publish_multiselect_audit(page, audit)
        return False

    # Reconcile over bounded rerender cycles; every selector is reacquired from a
    # fresh snapshot because DDS often replaces option nodes after each click.
    for cycle in range(4):
        snapshot = await _open_dds_multiselect(page, selector, backend, phase=phase)
        snapshot = await _wait_dds_multiselect_snapshot_stable(page, selector)
        selected = _multiselect_selected_values_from_snapshot(snapshot)
        delta = _multiselect_delta(wanted, selected)
        audit["actions"].append({"cycle": cycle + 1, "selected_before": selected, "delta": delta})
        if not delta["missing"] and not delta["extra"]:
            break

        # Remove extras one at a time by exact selected-option click. Never use
        # search-input deletion because empty Backspace removes the last chip.
        for extra in list(delta["extra"]):
            snapshot = await _open_dds_multiselect(page, selector, backend, phase=phase)
            snapshot = await _wait_dds_multiselect_snapshot_stable(page, selector)
            before_selected = _multiselect_selected_values_from_snapshot(snapshot)
            option = _find_multiselect_option(snapshot, extra)
            if option is None:
                await _set_dds_multiselect_search(page, selector, extra, backend, phase=phase)
                snapshot = await _wait_dds_multiselect_snapshot_stable(page, selector)
                option = _find_multiselect_option(snapshot, extra)
            if option is None or not option.get("selected"):
                audit["reason"] = f"extra selected option not uniquely removable: {extra}"
                await close_open_dropdown(page, phase)
                await _publish_multiselect_audit(page, audit)
                return False
            option_selector = _unique_multiselect_option_selector(snapshot, option)
            if option_selector:
                await _autowebglm_primary_gate(
                    page, action="click", selector=option_selector, label=f"HIP Portal multi-select remove {extra}", value=extra
                )
            if not option_selector or not await _click_dds_multiselect_option(page, option_selector, extra, backend, phase=phase):
                audit["reason"] = f"failed explicit removal click: {extra}"
                await close_open_dropdown(page, phase)
                await _publish_multiselect_audit(page, audit)
                return False
            after = await _wait_dds_multiselect_snapshot_stable(page, selector)
            after_selected = _multiselect_selected_values_from_snapshot(after)
            preserved = {x.lower() for x in before_selected if x.lower() != extra.lower()}
            if extra.lower() in {x.lower() for x in after_selected} or not preserved.issubset({x.lower() for x in after_selected}):
                audit["reason"] = f"removal transaction changed unintended selections: {extra}"
                await close_open_dropdown(page, phase)
                await _publish_multiselect_audit(page, audit)
                return False
            audit["actions"].append({"action": "remove_extra", "value": extra, "selected_after": after_selected})

        for value in list(delta["missing"]):
            snapshot = await _open_dds_multiselect(page, selector, backend, phase=phase)
            snapshot = await _wait_dds_multiselect_snapshot_stable(page, selector)
            before_selected = _multiselect_selected_values_from_snapshot(snapshot)
            option = _find_multiselect_option(snapshot, value)
            if option is None:
                await _set_dds_multiselect_search(page, selector, value, backend, phase=phase)
                snapshot = await _wait_dds_multiselect_snapshot_stable(page, selector)
                option = _find_multiselect_option(snapshot, value)
            if option is None:
                audit["reason"] = f"exact option not found after stable search: {value}"
                await close_open_dropdown(page, phase)
                await _publish_multiselect_audit(page, audit)
                return False
            if option.get("disabled"):
                audit["reason"] = f"requested option is disabled: {value}"
                await close_open_dropdown(page, phase)
                await _publish_multiselect_audit(page, audit)
                return False
            if re.sub(r"\s+", " ", str(option.get("text") or "").strip()).lower() == "select all":
                audit["reason"] = "Select All is forbidden for explicit exact-set replay"
                await close_open_dropdown(page, phase)
                await _publish_multiselect_audit(page, audit)
                return False
            option_selector = _unique_multiselect_option_selector(snapshot, option)
            await _autowebglm_primary_gate(
                page, action="select", selector=selector, label=f"HIP Portal multi-select ({phase or 'form'})", value=value
            )
            if not option_selector or not await _click_dds_multiselect_option(page, option_selector, value, backend, phase=phase):
                audit["reason"] = f"failed explicit option click: {value}"
                await close_open_dropdown(page, phase)
                await _publish_multiselect_audit(page, audit)
                return False
            after = await _wait_dds_multiselect_snapshot_stable(page, selector)
            after_selected = _multiselect_selected_values_from_snapshot(after)
            before_set = {x.lower() for x in before_selected}
            after_set = {x.lower() for x in after_selected}
            if value.lower() not in after_set or not before_set.issubset(after_set):
                audit["reason"] = f"additive selection proof failed: {value}"
                await close_open_dropdown(page, phase)
                await _publish_multiselect_audit(page, audit)
                return False
            audit["actions"].append({"action": "add_missing", "value": value, "selected_after": after_selected})
            await _set_dds_multiselect_search(page, selector, "", backend, phase=phase)

    final_snapshot = await _open_dds_multiselect(page, selector, backend, phase=phase)
    final_snapshot = await _wait_dds_multiselect_snapshot_stable(page, selector)
    if not final_snapshot.get("selection_mode") and final_snapshot.get("found") and final_snapshot.get("options"):
        final_snapshot["selection_mode"] = "multiple"
        final_snapshot["selection_mode_inference"] = "multiselect-executor-contract"
    selected = _multiselect_selected_values_from_snapshot(final_snapshot)
    wanted_set = {x.lower() for x in wanted}
    selected_set = {x.lower() for x in selected}
    summary_count = final_snapshot.get("selected_count_summary")
    count_match = summary_count in (None, len(selected)) and len(selected) == len(wanted)
    exact = bool(
        final_snapshot.get("selection_mode") == "multiple"
        and final_snapshot.get("option_universe_stable")
        and wanted_set == selected_set
        and count_match
    )
    audit.update({
        "final_snapshot": final_snapshot,
        "selected": selected,
        "missing": [x for x in wanted if x.lower() not in selected_set],
        "extra": [x for x in selected if x.lower() not in wanted_set],
        "selected_count_match": count_match,
        "pass": exact,
        "reason": "exact normalized multi-select set committed" if exact else "final authoritative exact-set proof failed",
    })
    await close_open_dropdown(page, phase)
    await _publish_multiselect_audit(page, audit)
    try:
        await page.evaluate(r"""
(selector) => {const el=document.querySelector(selector);if(el){el.removeAttribute('data-hip-locked-value');el.removeAttribute('data-hip-locked-at');}}
""", selector)
    except Exception:
        pass
    if exact:
        effect = await _semantic_commit(
            page, resolution=semantic_resolution, action="select", exact_value_verified=True, phase=phase
        )
        audit["semantic_effect"] = {
            "pass": bool(effect.get("pass")),
            "type": effect.get("effect_type") or "",
            "confidence": effect.get("confidence"),
        }
        await _publish_multiselect_audit(page, audit)
    return exact


async def set_boolean_control(page: Page, root: Locator | None, selector: str, value: str | bool, *, phase: str = "") -> bool:
    """Set DDS switch/toggle state through the same governed physical broker."""
    if not selector:
        return False
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    truthy = {"1", "true", "yes", "on", "enable", "enabled", "not_disabled"}
    falsy = {"0", "false", "no", "off", "disable", "disabled", "not_enabled", "not_enable"}
    if isinstance(value, bool): desired = bool(value)
    elif normalized in truthy: desired = True
    elif normalized in falsy: desired = False
    else: return False
    await _ensure_page_interactable(page, action=f"set boolean control {desired}", selector=selector)
    async def checked() -> bool | None:
        loc = page.locator(selector).first
        if not await loc.count(): return None
        try: return bool(await loc.evaluate("el => !!el.checked || el.getAttribute('aria-checked')==='true' || el.getAttribute('data-checked')==='true'"))
        except Exception: return None
    current = await checked()
    if current is None: return False
    if current != desired:
        if not await _broker_click(page, selector, label=f"HIP Portal boolean control -> {desired}", phase=phase, mutation_risk=False): return False
        await page.wait_for_timeout(180)
    return (await checked()) == desired


_RADIO_TRUE = {"yes", "true", "enable", "enabled", "on", "y", "1"}
_RADIO_FALSE = {"no", "false", "disable", "disabled", "off", "n", "0"}


async def select_radio_option(page: Page, selector: str, value: str, *, phase: str = "") -> Optional[bool]:
    """Choose ``value`` inside the radio group of the resolved control.

    ``select_radio_value`` searches the whole section for the first radio whose
    text matches, so on a form with two Yes/No groups (Existing Account, Use
    Existing Folder) "No" could land in the wrong group.  This helper stays in
    the group of the radio the resolver bound (same ``name`` or the same
    radiogroup/fieldset) and clicks the option whose label or value means
    ``value``.  Returns None when ``selector`` is not a radio, so callers can
    fall back to the section-level search.
    """
    target = re.sub(r"\s+", " ", str(value or "").strip())
    if not selector or not target:
        return None
    try:
        found = await page.evaluate(r"""
({selector, value, trueWords, falseWords}) => {
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim().toLowerCase();
  const el=document.querySelector(selector);
  if(!el) return {status:'missing'};
  const isRadio=x=>(x.type||'').toLowerCase()==='radio'||x.getAttribute('role')==='radio';
  if(!isRadio(el)) return {status:'not_radio'};
  const name=el.getAttribute('name')||'';
  let group=[];
  if(name){const scope=el.form||el.closest('form')||document;group=Array.from(scope.querySelectorAll('input[type=radio],[role=radio]')).filter(x=>(x.getAttribute('name')||'')===name);}
  if(group.length<2){const holder=el.closest('[role=radiogroup],fieldset')||el.parentElement&&el.parentElement.parentElement;group=holder?Array.from(holder.querySelectorAll('input[type=radio],[role=radio]')):[el];}
  const want=clean(value);
  const intent=trueWords.includes(want)?true:falseWords.includes(want)?false:null;
  function css(n){if(n.id)return `${n.tagName.toLowerCase()}#${CSS.escape(n.id)}`;const p=[];let x=n;while(x&&x.nodeType===1&&p.length<9){let t=x.tagName.toLowerCase();const par=x.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===x.tagName);if(same.length>1)t+=`:nth-of-type(${same.indexOf(x)+1})`;}p.unshift(t);x=par;}return p.join(' > ');}
  const options=group.map(r=>{const lab=r.id?document.querySelector(`label[for="${CSS.escape(r.id)}"]`):r.closest('label');return {selector:css(r),label:clean((lab&&(lab.innerText||lab.textContent))||r.getAttribute('aria-label')||''),value:clean(r.value||r.getAttribute('data-value')||'')};});
  let pick=options.find(o=>o.label===want)||options.find(o=>o.value===want);
  if(!pick&&intent!==null)pick=options.find(o=>(intent?trueWords:falseWords).includes(o.label))||options.find(o=>(intent?trueWords:falseWords).includes(o.value));
  return pick?{status:'found',selector:pick.selector,label:pick.label,options:options.map(o=>o.label)}:{status:'no_option',options:options.map(o=>o.label)};
}
""", {"selector": selector, "value": target, "trueWords": sorted(_RADIO_TRUE), "falseWords": sorted(_RADIO_FALSE)})
    except Exception:
        return None
    status = str((found or {}).get("status") or "")
    if status in {"missing", "not_radio", ""}:
        return None
    if status != "found":
        return False
    option = str(found.get("selector") or "")
    loc = page.locator(option).first
    try:
        already = bool(await loc.evaluate("el => !!el.checked || el.getAttribute('aria-checked')==='true'"))
    except Exception:
        already = False
    if not already:
        await _autowebglm_primary_gate(page, action="click", selector=option, label=f"HIP Portal radio {target}", value=target)
        if not await _broker_click(page, option, label=f"HIP Portal radio {target}", phase=phase, mutation_risk=False):
            return False
        await page.wait_for_timeout(180)
    try:
        return bool(await page.locator(option).first.evaluate("el => !!el.checked || el.getAttribute('aria-checked')==='true'"))
    except Exception:
        return False


async def select_radio_value(page: Page, root: Locator | None, value: str, *, section: str = "", phase: str = "") -> bool:
    """Click and verify a visible radio by semantic label through BrowserSession."""
    target = re.sub(r"\s+", " ", str(value or "").strip())
    if not target: return False
    await _ensure_page_interactable(page, action=f"select radio {target}")
    try:
        found = await page.evaluate(r"""
({value,section}) => {
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim(); const low=clean(value).toLowerCase(), sec=clean(section).toLowerCase();
  function visible(el){const host=el.closest('label,.dds__radio-button,[role=radio]')||el;const r=host.getBoundingClientRect();const s=getComputedStyle(host);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden');}
  function css(el){if(el.id)return `${el.tagName.toLowerCase()}#${CSS.escape(el.id)}`;const p=[];let n=el;while(n&&n.nodeType===1&&p.length<8){let x=n.tagName.toLowerCase();const par=n.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===n.tagName);if(same.length>1)x+=`:nth-of-type(${same.indexOf(n)+1})`;}p.unshift(x);n=par;}return p.join(' > ');}
  const rows=Array.from(document.querySelectorAll('input[type=radio],[role=radio]')).filter(visible).map(el=>{const lab=el.id?document.querySelector(`label[for="${CSS.escape(el.id)}"]`):el.closest('label');const text=clean((lab&&(lab.innerText||lab.textContent))||el.getAttribute('aria-label')||el.value||'');const fs=el.closest('fieldset');const legend=clean(fs&&fs.querySelector('legend')&&(fs.querySelector('legend').innerText||fs.querySelector('legend').textContent));return {selector:css(el),text,section:legend};});
  return rows.find(x=>(!sec||x.section.toLowerCase().includes(sec)||sec.includes(x.section.toLowerCase()))&&(x.text.toLowerCase()===low||x.text.toLowerCase().includes(low)||low.includes(x.text.toLowerCase())))||null;
}
""", {"value": target, "section": section})
        if not found or not found.get("selector"): return False
        selector = str(found["selector"]); loc = page.locator(selector).first
        try: already = bool(await loc.evaluate("el => !!el.checked || el.getAttribute('aria-checked')==='true'"))
        except Exception: already = False
        if not already:
            await _autowebglm_primary_gate(page, action="click", selector=selector, label=f"HIP Portal radio {target}", value=target)
            if not await _broker_click(page, selector, label=f"HIP Portal radio {target}", phase=phase, mutation_risk=False): return False
            await page.wait_for_timeout(180)
        loc = page.locator(selector).first
        return bool(await loc.evaluate("el => !!el.checked || el.getAttribute('aria-checked')==='true'"))
    except Exception:
        return False
