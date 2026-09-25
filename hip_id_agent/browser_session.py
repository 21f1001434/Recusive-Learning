from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Mapping
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.async_api import BrowserContext, CDPSession, Locator, Page, Response, TimeoutError as PlaywrightTimeoutError, async_playwright

from .config import AppConfig
from .models import ActionEvent, ClickEvent, NetworkRecord, NetworkTabEvent, utc_now
from .security import is_secret_target, mask_sensitive_data, mask_sensitive_string, safe_json_loads
from .safe_io import safe_write_json, safe_write_bytes, safe_mkdir
from .form_interaction_policy import universal_locator_preflight
from .browser_use_bridge import BrowserUseStateBridge, register_page_bridge
from .autowebglm_bridge import AutoWebGLMRecoveryBridge
from .langchain_browser_toolkit import LangChainBrowserToolkitBridge
from .pyautogui_tool import PyAutoGUIFallbackTool, PyAutoGUIUnavailable
from .vision_runtime import VisionRuntimeBridge
from .semantic_control import SemanticActionGate
from .agent_live_view import AgentLiveViewRecorder
from .hip_semantic_tools import classify_action_risk
from .semantic_affordance import (
    resolve_semantic_affordance, snapshot_affordance_surface, verify_affordance_effect,
    requires_compound_menu_fallback, canonical_intent, bind_new_affordance_surface,
    resolve_affordance_in_proven_surface, verify_affordance_target_membership,
    bind_child_affordance_surface, refresh_affordance_surface_lease,
    prefers_active_surface_chain, MAX_SURFACE_CHAIN_DEPTH,
)

def canonical_effect_required(intent: str) -> bool:
    key = re.sub(r"[^a-z0-9]+", "_", str(intent or "").strip().lower()).strip("_")
    return key not in {"search", "refresh", "download", "save", "create"}


CLICK_LISTENER_SCRIPT = r"""
(() => {
  if (window.__HIP_CLICK_LOG_INSTALLED) return;
  window.__HIP_CLICK_LOG_INSTALLED = true;
  window.__HIP_CLICK_LOG = window.__HIP_CLICK_LOG || [];
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 6) {
      let part = el.tagName.toLowerCase();
      if (el.id) { part += '#' + el.id; parts.unshift(part); break; }
      const cls = (el.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0, 3).join('.');
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
  document.addEventListener('click', (ev) => {
    const el = ev.target && ev.target.closest ? ev.target.closest('button,a,input,select,textarea,[role],td,tr,div,span') : ev.target;
    if (!el) return;
    const actionable = ev.target && ev.target.closest ? ev.target.closest('button,a,input[type="button"],input[type="submit"],[role="button"],[role="menuitem"],dds-button') : null;
    const actionText = actionable ? String(actionable.innerText || actionable.value || actionable.getAttribute('aria-label') || actionable.getAttribute('title') || '').replace(/\s+/g,' ').trim().toLowerCase() : '';
    const actionRole = actionable ? String(actionable.getAttribute('role') || '').toLowerCase() : '';
    const actionSelector = actionable ? cssPath(actionable).toLowerCase() : '';
    const safeExact = new Set(['add','+ add','continue','next','back','cancel','close','done','proceed']);
    const normalize = s => String(s||'').toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'');
    const createBizFlowLauncher = actionText === 'create biz flow' && (actionRole === 'menuitem' || actionSelector.includes('action-menu'));
    const structuralMarker = window.__HIP_STRUCTURAL_OPENER || null;
    const structuralOpener = !!(createBizFlowLauncher || (structuralMarker && structuralMarker.enabled));
    const auth = window.__HIP_MUTATION_AUTH || {enabled:false, allowed_labels:[], task_id:''};
    const allowed = new Set(Array.isArray(auth.allowed_labels) ? auth.allowed_labels : []);
    const normalizedAction = normalize(actionText);
    const mutating = /\b(save|create|submit|delete|remove|deploy|publish|update|enable|disable|confirm)\b/.test(actionText);
    const authorized = !!(mutating && auth.enabled && allowed.has(normalizedAction));
    const blocked = !!(actionable && mutating && !safeExact.has(actionText) && !structuralOpener && !authorized);
    if (blocked) {
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
    }
    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {x:0,y:0,width:0,height:0};
    window.__HIP_CLICK_LOG.push({
      timestamp: new Date().toISOString(), url: location.href, tag: (el.tagName || '').toLowerCase(),
      text: (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0, 500),
      id: el.id || '', classes: (el.className || '').toString().slice(0, 300), href: el.href || '',
      role: el.getAttribute('role') || '', ariaLabel: el.getAttribute('aria-label') || '', selector: cssPath(el),
      boundingBox: {x:r.x, y:r.y, width:r.width, height:r.height},
      safety_blocked: blocked, safety_authorized: authorized, safety_structural_opener: structuralOpener,
      safety_authorization_task_id: String(auth.task_id || ''),
      safety_structural_action_label: structuralMarker ? String(structuralMarker.action_label || '') : '',
      safety_action_text: actionText, safety_action_selector: actionSelector
    });
    if (structuralMarker && structuralMarker.enabled) window.__HIP_STRUCTURAL_OPENER = null;
  }, true);
})();
"""

DOM_EVENT_OBSERVER_SCRIPT = r"""
(() => {
  if (window.__HIP_DOM_EVENT_OBSERVER_INSTALLED) return;
  window.__HIP_DOM_EVENT_OBSERVER_INSTALLED = true;
  window.__HIP_DOM_EVENT_LOG = window.__HIP_DOM_EVENT_LOG || [];
  window.__HIP_DOM_MUTATION_LOG = window.__HIP_DOM_MUTATION_LOG || [];
  window.__HIP_DOM_EVENT_SEQ = Number(window.__HIP_DOM_EVENT_SEQ || 0);
  window.__HIP_DOM_MUTATION_SEQ = Number(window.__HIP_DOM_MUTATION_SEQ || 0);
  const MAX_EVENTS = 6000;
  const MAX_MUTATIONS = 6000;
  const ATTRS = new Set(['aria-expanded','aria-hidden','aria-disabled','aria-required','disabled','hidden','required','readonly','checked','selected','value','class','style']);

  function clean(v, n=500) { return String(v == null ? '' : v).replace(/\s+/g, ' ').trim().slice(0, n); }
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && parts.length < 8) {
      let part = n.tagName.toLowerCase();
      if (n.id) { part += '#' + CSS.escape(n.id); parts.unshift(part); break; }
      const name = n.getAttribute && n.getAttribute('name');
      if (name) part += `[name="${String(name).replace(/"/g,'\\"')}"]`;
      const parent = n.parentElement;
      if (parent) {
        const peers = Array.from(parent.children).filter(x => x.tagName === n.tagName);
        if (peers.length > 1) part += `:nth-of-type(${peers.indexOf(n)+1})`;
      }
      parts.unshift(part);
      n = parent;
    }
    return parts.join(' > ');
  }
  function labelFor(el) {
    if (!el) return '';
    if (el.id) {
      try {
        const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lab && clean(lab.innerText || lab.textContent)) return clean(lab.innerText || lab.textContent);
      } catch (_) {}
    }
    const own = el.closest && el.closest('label');
    if (own && clean(own.innerText || own.textContent)) return clean(own.innerText || own.textContent);
    const group = el.closest && el.closest('.dds__form-group,.dds__input-text__container,app-generic-dropdown,dds-dropdown,[class*=form-field],[class*=field-container],[role=row],tr');
    if (group) {
      const lab = group.querySelector(':scope > label,:scope > .dds__label,label,.dds__label');
      if (lab && clean(lab.innerText || lab.textContent)) return clean(lab.innerText || lab.textContent);
    }
    return clean(el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name')));
  }
  function sectionFor(el) {
    let n = el;
    while (n && n !== document.body) {
      const h = n.querySelector && n.querySelector(':scope > h1,:scope > h2,:scope > h3,:scope > h4,:scope > legend,:scope > [role=tab][aria-selected=true]');
      if (h && clean(h.innerText || h.textContent)) return clean(h.innerText || h.textContent);
      n = n.parentElement;
    }
    return '';
  }
  function rowSignature(el) {
    const row = el && el.closest && el.closest('tr,[role=row],.dds__row,[class*=condition-row],[class*=attribute-row],[class*=form-row]');
    return row ? clean(row.innerText || row.textContent, 800) : '';
  }
  function safeValue(el) {
    if (!el) return {kind:'none'};
    const type = clean(el.type || '').toLowerCase();
    const name = clean(el.name || el.id || el.getAttribute && el.getAttribute('aria-label')).toLowerCase();
    const secret = type === 'password' || /(password|passwd|secret|token|client[_ -]?secret|otp|authorization)/.test(name);
    if (secret) return {kind:'redacted', redacted:true, length:String(el.value || '').length};
    if (type === 'file') {
      const files = Array.from(el.files || []).map(f => clean((f.name || '').split(/[\\/]/).pop(), 250));
      return {kind:'files', files};
    }
    if (type === 'checkbox' || type === 'radio') return {kind:type, checked:!!el.checked, value:clean(el.value)};
    if (el.tagName && el.tagName.toLowerCase() === 'select') {
      return {kind:'select', values:Array.from(el.selectedOptions || []).map(o => clean(o.textContent || o.value))};
    }
    return {kind:'value', value:clean(el.value != null ? el.value : el.textContent)};
  }
  function summary(el) {
    if (!el || el.nodeType !== 1) return null;
    const role = clean(el.getAttribute && el.getAttribute('role'));
    const type = clean(el.getAttribute && el.getAttribute('type'));
    return {
      tag: clean(el.tagName).toLowerCase(), selector: cssPath(el), id: clean(el.id), name: clean(el.getAttribute && el.getAttribute('name')),
      role, type, label: labelFor(el), section: sectionFor(el), row_signature: rowSignature(el),
      aria_expanded: clean(el.getAttribute && el.getAttribute('aria-expanded')),
      aria_hidden: clean(el.getAttribute && el.getAttribute('aria-hidden')),
      aria_disabled: clean(el.getAttribute && el.getAttribute('aria-disabled')),
      required: !!(el.required || (el.getAttribute && el.getAttribute('aria-required') === 'true')),
      disabled: !!(el.disabled || (el.getAttribute && el.getAttribute('aria-disabled') === 'true')),
      hidden: !!(el.hidden || (el.getAttribute && el.getAttribute('aria-hidden') === 'true')),
      value: safeValue(el)
    };
  }
  function boundedPush(arr, row, max) {
    arr.push(row);
    if (arr.length > max) arr.splice(0, Math.max(100, arr.length - max));
  }
  const eventTypes = ['pointerdown','click','focusin','focusout','input','change','keydown'];
  for (const eventType of eventTypes) {
    document.addEventListener(eventType, ev => {
      const raw = ev.target;
      const el = raw && raw.closest ? raw.closest('input,select,textarea,button,[role=combobox],[role=listbox],[role=option],[role=radio],[role=checkbox],[contenteditable=true],dds-dropdown,dds-radio-button,dds-checkbox') || raw : raw;
      if (!el) return;
      const rec = {
        seq: ++window.__HIP_DOM_EVENT_SEQ, timestamp: new Date().toISOString(), type: eventType, url: location.href,
        stage: clean(document.documentElement.getAttribute('data-hip-stage')), target: summary(el),
        key: eventType === 'keydown' ? clean(ev.key, 60) : '',
        default_prevented: !!ev.defaultPrevented, trusted: !!ev.isTrusted
      };
      boundedPush(window.__HIP_DOM_EVENT_LOG, rec, MAX_EVENTS);
    }, true);
  }

  function controlDescendants(node) {
    if (!node || node.nodeType !== 1) return [];
    const out = [];
    const selector = 'input,select,textarea,button,[role=combobox],[role=listbox],[role=option],[role=radio],[role=checkbox],[contenteditable=true]';
    if (node.matches && node.matches(selector)) out.push(node);
    if (node.querySelectorAll) out.push(...Array.from(node.querySelectorAll(selector)).slice(0, 30));
    return out.slice(0, 30).map(summary).filter(Boolean);
  }

  function structuralKinds(node, direction) {
    if (!node || node.nodeType !== 1) return [];
    const roots = [node];
    if (node.querySelectorAll) roots.push(...Array.from(node.querySelectorAll('[role=dialog],[role=listbox],[role=row],mat-dialog-container,.mat-mdc-dialog-container,.dds__drawer,[class*=drawer],[class*=spinner],[class*=loading],[class*=progress],[aria-busy=true]')).slice(0, 50));
    const out = [];
    const seen = new Set();
    const add = (kind, el) => {
      const key = kind + '|' + cssPath(el);
      if (seen.has(key)) return;
      seen.add(key);
      out.push({kind, direction, target: summary(el)});
    };
    for (const el of roots) {
      if (!el || !el.matches) continue;
      const role = clean(el.getAttribute('role')).toLowerCase();
      const cls = clean(el.className || '').toLowerCase();
      const busy = clean(el.getAttribute('aria-busy')).toLowerCase();
      if (role === 'dialog' || el.matches('mat-dialog-container,.mat-mdc-dialog-container')) add('dialog_' + direction, el);
      if (role === 'listbox') add('listbox_' + direction, el);
      if (role === 'row' || /condition-row|attribute-row|form-row|dds__row/.test(cls)) add('row_' + direction, el);
      if (/drawer/.test(cls)) add('drawer_' + direction, el);
      if (/spinner|loading|progress/.test(cls) || busy === 'true') add('spinner_' + direction, el);
    }
    return out.slice(0, 60);
  }

  function attributeStructuralEvents(m) {
    if (!m || m.type !== 'attributes' || !m.target || m.target.nodeType !== 1) return [];
    const el = m.target;
    const attr = String(m.attributeName || '');
    const now = clean(el.getAttribute(attr)).toLowerCase();
    const before = clean(m.oldValue).toLowerCase();
    const target = summary(el);
    if (attr === 'aria-expanded' && before !== now) return [{kind: now === 'true' ? 'accordion_expanded' : 'accordion_collapsed', direction:'changed', target}];
    if ((attr === 'disabled' || attr === 'aria-disabled') && before !== now) return [{kind: (el.disabled || now === 'true') ? 'field_disabled' : 'field_enabled', direction:'changed', target}];
    if (attr === 'readonly' && before !== now) return [{kind: el.readOnly ? 'field_readonly' : 'field_editable', direction:'changed', target}];
    if (attr === 'aria-busy' && before !== now) return [{kind: now === 'true' ? 'spinner_started' : 'spinner_stopped', direction:'changed', target}];
    return [];
  }

  function recordRouteChange(source, beforeUrl) {
    const afterUrl = location.href;
    if (String(beforeUrl || '') === String(afterUrl || '')) return;
    boundedPush(window.__HIP_DOM_MUTATION_LOG, {
      seq: ++window.__HIP_DOM_MUTATION_SEQ, timestamp: new Date().toISOString(), type: 'route', url: afterUrl,
      stage: clean(document.documentElement && document.documentElement.getAttribute('data-hip-stage')),
      target: null, attribute: '', old_value: clean(beforeUrl, 1200), new_value: clean(afterUrl, 1200),
      added_controls: [], removed_controls: [],
      structural_events: [{kind:'route_changed', direction:'changed', source:clean(source,80), from:clean(beforeUrl,1200), to:clean(afterUrl,1200)}]
    }, MAX_MUTATIONS);
  }
  if (!window.__HIP_ROUTE_OBSERVER_INSTALLED) {
    window.__HIP_ROUTE_OBSERVER_INSTALLED = true;
    for (const method of ['pushState','replaceState']) {
      const original = history[method];
      if (typeof original === 'function') {
        history[method] = function(...args) {
          const before = location.href;
          const result = original.apply(this, args);
          recordRouteChange(method, before);
          return result;
        };
      }
    }
    window.addEventListener('popstate', () => {
      const before = window.__HIP_LAST_ROUTE_URL || '';
      recordRouteChange('popstate', before);
      window.__HIP_LAST_ROUTE_URL = location.href;
    });
    window.__HIP_LAST_ROUTE_URL = location.href;
  }

  const observer = new MutationObserver(records => {
    for (const m of records) {
      if (m.type === 'attributes' && !ATTRS.has(m.attributeName)) continue;
      const target = m.target && m.target.nodeType === 1 ? summary(m.target) : null;
      const addedNodes = m.type === 'childList' ? Array.from(m.addedNodes || []) : [];
      const removedNodes = m.type === 'childList' ? Array.from(m.removedNodes || []) : [];
      const structuralEvents = [
        ...addedNodes.flatMap(n => structuralKinds(n, 'opened')),
        ...removedNodes.flatMap(n => structuralKinds(n, 'closed')),
        ...attributeStructuralEvents(m)
      ].slice(0, 80);
      const rec = {
        seq: ++window.__HIP_DOM_MUTATION_SEQ, timestamp: new Date().toISOString(), type: m.type, url: location.href,
        stage: clean(document.documentElement.getAttribute('data-hip-stage')), target,
        attribute: m.attributeName || '', old_value: clean(m.oldValue, 800), new_value: m.type === 'attributes' ? clean(m.target.getAttribute(m.attributeName), 800) : '',
        added_controls: m.type === 'childList' ? addedNodes.flatMap(controlDescendants).slice(0, 40) : [],
        removed_controls: m.type === 'childList' ? removedNodes.flatMap(controlDescendants).slice(0, 40) : [],
        structural_events: structuralEvents
      };
      if (rec.type === 'childList' && rec.added_controls.length === 0 && rec.removed_controls.length === 0 && rec.structural_events.length === 0) continue;
      boundedPush(window.__HIP_DOM_MUTATION_LOG, rec, MAX_MUTATIONS);
    }
  });
  const start = () => {
    if (!document.documentElement || window.__HIP_DOM_MUTATION_OBSERVER_STARTED) return;
    window.__HIP_DOM_MUTATION_OBSERVER_STARTED = true;
    observer.observe(document.documentElement, {subtree:true, childList:true, attributes:true, attributeOldValue:true, attributeFilter:Array.from(ATTRS)});
  };
  start();
  if (!window.__HIP_DOM_MUTATION_OBSERVER_STARTED) document.addEventListener('DOMContentLoaded', start, {once:true});
})();
"""



def _hash_value(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


class BrowserSession:
    """HIP browser session with dual MCP execution and evidence.

    Python Playwright keeps the mature deterministic portal locators for discovery,
    exact state reading and standalone legacy compatibility. When Layer 11 is active,
    the same Chrome/Edge instance is exposed over CDP: official Playwright MCP is the
    sole governed physical executor and Chrome DevTools MCP is an independent witness.
    A semantic rejection or MCP execution failure cannot fall through to a raw locator;
    exact post-action state/effect verification is mandatory before the mission advances.
    """

    def __init__(self, config: AppConfig, run_dir: Path):
        self.config = config
        self.flow_pattern_memory = None
        self.agentq_controller = None
        # Optional first-class human mission trace. Full mission flows attach a
        # MissionTraceLedger here so every browser action can be reflected in the
        # live step card without exposing planner chain-of-thought.
        self.mission_trace = None
        self.session_root_dir = Path(run_dir)
        self.run_dir = Path(run_dir)
        self.network_dir = run_dir / "network"
        self.console_dir = run_dir / "console"
        self.clicks_dir = run_dir / "clicks"
        self.actions_dir = run_dir / "actions"
        self.dom_dir = run_dir / "dom_snapshots"
        self.dom_events_dir = run_dir / "dom_events"
        for d in [self.network_dir, self.console_dir, self.clicks_dir, self.actions_dir, self.dom_dir, self.dom_events_dir]:
            d.mkdir(parents=True, exist_ok=True)
        self._playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.network_records: List[NetworkRecord] = []
        self.network_tab_events: List[NetworkTabEvent] = []
        self.console_messages: List[Dict[str, Any]] = []
        self.click_events: List[ClickEvent] = []
        self.action_events: List[ActionEvent] = []
        self.dom_event_records: List[Dict[str, Any]] = []
        self.dom_mutation_records: List[Dict[str, Any]] = []
        self.dom_transition_records: List[Dict[str, Any]] = []
        self._cdp_requests: Dict[str, Dict[str, Any]] = {}
        self._cdp_sessions: List[CDPSession] = []
        self._observed_pages: set[int] = set()
        self._current_stage = "startup"
        self.backend_name = "playwright"
        self._logs_flushed = False
        self._closed = False
        self.mcp_backend = None
        self.playwright_mcp_backend = None
        self.pyautogui_mcp_backend = None
        self.browser_use_bridge: Optional[BrowserUseStateBridge] = None
        self.browser_use_capabilities: Dict[str, Any] = {}
        self.autowebglm_bridge = AutoWebGLMRecoveryBridge(
            getattr(config, "autowebglm", None), aia_config=getattr(config, "aia", None), app_config=config
        ) if getattr(config, "autowebglm", None) is not None else None
        self.autowebglm_capabilities: Dict[str, Any] = self.autowebglm_bridge.status() if self.autowebglm_bridge else {"enabled": False}
        self.langchain_browser_toolkit = LangChainBrowserToolkitBridge(
            getattr(config, "langchain_browser_toolkit", None)
        ) if getattr(config, "langchain_browser_toolkit", None) is not None else None
        self.langchain_browser_capabilities: Dict[str, Any] = {"enabled": bool(self.langchain_browser_toolkit)}
        self.vision_runtime = VisionRuntimeBridge(
            getattr(config, "vision_runtime", None), aia_config=getattr(config, "aia", None)
        ) if getattr(config, "vision_runtime", None) is not None else None
        self.vision_capabilities: Dict[str, Any] = self.vision_runtime.status() if self.vision_runtime else {"enabled": False}
        self.semantic_action_gate = SemanticActionGate(
            getattr(config, "semantic_understanding", None),
            run_dir=self.run_dir,
            memory_root=Path(getattr(config.reporting, "memory_dir", "./data/hip_memory")),
            world_model_config=getattr(config, "brain", None),
        )
        self.semantic_understanding_capabilities: Dict[str, Any] = self.semantic_action_gate.status()
        self.agent_live_view = AgentLiveViewRecorder(config=config, run_dir=self.run_dir)
        self.mcp_capabilities: Dict[str, Any] = {}
        self.playwright_mcp_capabilities: Dict[str, Any] = {}
        self.pyautogui_mcp_capabilities: Dict[str, Any] = {}
        # Playwright MCP may be installed yet attached to the wrong/stale tab.
        # It is eligible for form actions only after same-surface verification.
        self._playwright_mcp_surface_healthy = False
        self.session_id = f"hip-browser-{uuid.uuid4().hex[:12]}"
        self._start_count = 0
        self._borrow_count = 0
        self._sso_prompt_count = 0
        self._authenticated_once = False
        self._reauth_count = 0
        self._sso_wait_in_progress = False
        self._phase_history: List[Dict[str, Any]] = []
        self._active_phase_name = ""
        # During an explicit phase handoff the browser is still physically bound
        # to the completed source phase evidence directory until the destination
        # phase borrows the persistent session.  Keep a trace-only phase override
        # so navigation to the next HIP module is shown under the destination card
        # instead of being misreported as a source-phase click.
        self._trace_phase_override = ""
        self._active_target_url = ""
        self._phase_transition_count = 0
        self._page_recovery_count = 0
        self._overlay_recovery_count = 0
        self._loading_watchdog_first_seen: Dict[str, float] = {}
        self._loading_watchdog_refresh_counts: Dict[str, int] = {}
        self._loading_watchdog_candidate_counts: Dict[str, int] = {}
        self._loading_watchdog_last_fingerprints: Dict[str, str] = {}
        self._loading_watchdog_ignored_passive: set[str] = set()
        self._loading_watchdog_last_vision: Dict[str, Any] = {}
        self._loading_watchdog_replay_required: Dict[str, str] = {}
        self._loading_watchdog_sequence = 0
        self._autonomous_health_sequence = 0
        self._autonomous_health_signatures: set[str] = set()
        # Future-task mutation authorization is empty by default and is never
        # enabled by discovery/configuration missions.  A semantic future task
        # may populate it only after the three-part explicit authorization gate.
        self._portal_mutation_authorization: Dict[str, Any] = {
            "enabled": False, "allowed_labels": [], "task_id": "", "confirmed_at": ""
        }
        # Proven nested UI surfaces (menu -> dialog -> child drawer/listbox).  The
        # stack contains structural provenance only and is pruned/re-proven before
        # every continuation action. It never stores customer-entered field values.
        self._semantic_surface_chain: List[Dict[str, Any]] = []
        # Last physical click-dispatch evidence. Mutation callers use this to
        # distinguish a pre-dispatch failure from a click that may already have
        # reached HIP. Structural metadata only; no field values are stored.
        self._last_click_dispatch: Dict[str, Any] = {}
        # Mutation dispatch is serialized within the persistent browser. Once a
        # write-capable click has physically been attempted, a quarantine remains
        # armed until authoritative reconciliation clears it. This prevents a
        # second agent/coroutine from issuing another write while the first outcome
        # is still unknown. Structural metadata only; no customer values.
        self._mutation_dispatch_lock = asyncio.Lock()
        self._mutation_quarantine: Dict[str, Any] = {}
        # Browser-Use WebUI-style browser/session evidence and own-browser CDP state.
        self._external_browser = None
        self._owns_browser_context = True
        self._cdp_endpoint = ""
        self._cdp_health: Dict[str, Any] = {}
        self._selected_browser: Dict[str, Any] = {}
        self._browser_launch_attempts: List[Dict[str, Any]] = []
        self._mission_browser_locked = False
        self._recovery_intelligence_cooldown_until = 0.0
        self._recovery_intelligence_lock = asyncio.Lock()
        self._recovery_intelligence_attach_attempted = False
        # v2.1 Layer 6: executor-channel recovery never replaces the authenticated
        # browser.  Only MCP clients may be restarted against the same CDP endpoint,
        # and the exact HIP tab/route must be uniquely re-proven before continuing.
        self._executor_rebind_lock = asyncio.Lock()
        self._executor_rebind_count = 0
        self._executor_rebind_phase_counts: Dict[str, int] = {}
        self._last_executor_rebind: Dict[str, Any] = {}
        self._trace_started = False
        self._trace_path = ""
        self._video_dir = ""
        self._download_dir = ""
        self.download_records: List[Dict[str, Any]] = []
        self.browser_session_history: List[Dict[str, Any]] = []
        self.pyautogui_tool = PyAutoGUIFallbackTool(config, self.run_dir)

    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def _resolve_browser_use_dir(self, configured: str, default_leaf: str) -> Path:
        raw = str(configured or "").strip()
        if raw:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = self.session_root_dir / path
        else:
            path = self.session_root_dir / "browser_use" / default_leaf
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _safe_artifact_name(name: str, fallback: str = "artifact.bin") -> str:
        base = Path(str(name or fallback)).name
        clean = re.sub(r"[^A-Za-z0-9._() -]+", "_", base).strip(" .")
        return clean[:180] or fallback

    def _unique_artifact_path(self, directory: Path, name: str) -> Path:
        candidate = directory / self._safe_artifact_name(name)
        if not candidate.exists():
            return candidate
        stem, suffix = candidate.stem, candidate.suffix
        for idx in range(1, 10000):
            alt = directory / f"{stem}_{idx}{suffix}"
            if not alt.exists():
                return alt
        return directory / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

    async def _save_download(self, download: Any) -> None:
        cfg = getattr(self.config, "browser_use", None)
        if cfg is None or not bool(getattr(cfg, "save_downloads", True)):
            return
        directory = self._resolve_browser_use_dir(str(getattr(cfg, "download_dir", "") or ""), "downloads")
        suggested = "download.bin"
        try:
            suggested = str(download.suggested_filename or suggested)
        except Exception:
            pass
        destination = self._unique_artifact_path(directory, suggested)
        record: Dict[str, Any] = {
            "timestamp": utc_now(),
            "stage": self._current_stage,
            "suggested_filename": self._safe_artifact_name(suggested),
            "saved_path": str(destination),
            "status": "pending",
            "url": "",
        }
        try:
            try:
                record["url"] = self._evidence_url(str(download.url or ""))
            except Exception:
                pass
            await download.save_as(str(destination))
            record["status"] = "saved"
            try:
                record["size_bytes"] = destination.stat().st_size
            except Exception:
                pass
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = mask_sensitive_string(str(exc))
        self.download_records.append(mask_sensitive_data(record))
        safe_write_json(directory / "download_manifest.json", {
            "schema_version": "hip.browser-downloads.v1",
            "session_id": self.session_id,
            "downloads": self.download_records,
        })

    async def _capture_browser_session_history(self, event: str, extra: Optional[Dict[str, Any]] = None) -> None:
        cfg = getattr(self.config, "browser_use", None)
        if cfg is None or not bool(getattr(cfg, "save_session_history", True)):
            return
        state: Dict[str, Any]
        if self.browser_use_bridge is not None and self.browser_use_capabilities.get("attached"):
            try:
                # Phase transitions/actions need compact recovery context, not the
                # entire Browser-Use state tree. This keeps LLM/evidence context
                # bounded while preserving the actionable semantic surface.
                if str(event) in {"phase_transition", "action_finished"}:
                    state = await self.browser_use_recovery_context(max_elements=120)
                else:
                    state = await self.browser_use_state_snapshot()
            except Exception as exc:
                state = {"available": False, "error": mask_sensitive_string(str(exc))}
        else:
            pages: List[Dict[str, Any]] = []
            try:
                for page in list(self.context.pages if self.context else []):
                    title = ""
                    try:
                        title = await page.title()
                    except Exception:
                        pass
                    pages.append({"url": self._evidence_url(str(page.url or "")), "title": mask_sensitive_string(title)})
            except Exception:
                pass
            state = {"available": bool(pages), "pages": pages}
        entry = mask_sensitive_data({
            "timestamp": utc_now(),
            "event": str(event),
            "stage": self._current_stage,
            "phase": self._trace_phase_override or self._active_phase_name,
            "state": state,
            "extra": extra or {},
        })
        self.browser_session_history.append(entry)
        limit = max(10, int(getattr(cfg, "max_history_entries", 250) or 250))
        if len(self.browser_session_history) > limit:
            self.browser_session_history = self.browser_session_history[-limit:]
        directory = self._resolve_browser_use_dir(str(getattr(cfg, "history_dir", "") or ""), "history")
        safe_write_json(directory / "session_history.json", {
            "schema_version": "hip.browser-use-session-history.v1",
            "session_id": self.session_id,
            "history": self.browser_session_history,
            "downloads": self.download_records,
            "phase_history": self._phase_history,
        })

    async def _start_playwright_trace(self) -> None:
        cfg = getattr(self.config, "browser_use", None)
        if self.context is None or cfg is None or not bool(getattr(cfg, "capture_playwright_trace", False)):
            return
        directory = self._resolve_browser_use_dir(str(getattr(cfg, "trace_dir", "") or ""), "traces")
        self._trace_path = str(directory / f"{self.session_id}.zip")
        try:
            await self.context.tracing.start(
                screenshots=bool(getattr(cfg, "trace_screenshots", True)),
                snapshots=bool(getattr(cfg, "trace_snapshots", True)),
                sources=bool(getattr(cfg, "trace_sources", False)),
            )
            self._trace_started = True
        except Exception as exc:
            self._trace_started = False
            safe_write_json(directory / f"{self.session_id}.trace_error.json", {"error": mask_sensitive_string(str(exc))})

    async def _stop_playwright_trace(self) -> None:
        if not self._trace_started or self.context is None:
            return
        try:
            await asyncio.wait_for(self.context.tracing.stop(path=self._trace_path), timeout=30)
        except Exception as exc:
            try:
                safe_write_json(Path(self._trace_path).with_suffix(".error.json"), {"error": mask_sensitive_string(str(exc))})
            except Exception:
                pass
        finally:
            self._trace_started = False

    def _managed_browser_candidates(self) -> List[Dict[str, Any]]:
        """Return startup browser candidates in deterministic preference order.

        The configured branded browser is always first.  The default HIP policy is
        Chrome -> Edge -> Playwright Chromium.  Fallback is startup-only; once Dell
        SSO/mission execution starts the selected browser is locked for the mission.
        Browser-specific persistent profiles are never mixed.
        """
        portal = self.config.portal
        base_profile = Path(portal.browser_user_data_dir)
        candidates: List[Dict[str, Any]] = []

        def add(name: str, *, profile: Path, channel: str = "", executable: str = "") -> None:
            key = (name, str(profile), channel, executable)
            if any((c.get("name"), c.get("profile"), c.get("channel"), c.get("executable_path")) == key for c in candidates):
                return
            candidates.append({
                "name": name, "profile": str(profile), "channel": channel,
                "executable_path": executable,
            })

        explicit_edge = str(getattr(portal, "edge_executable_path", "") or "").strip()
        explicit_chrome = str(getattr(portal, "chrome_executable_path", "") or "").strip()
        configured_channel = str(getattr(portal, "chromium_channel", "") or "").strip()
        primary = configured_channel.lower()

        if primary == "chrome":
            add("chrome", profile=base_profile, executable=explicit_chrome) if explicit_chrome else add("chrome", profile=base_profile, channel="chrome")
        elif primary == "msedge":
            add("edge", profile=base_profile, executable=explicit_edge) if explicit_edge else add("edge", profile=base_profile, channel="msedge")
        elif configured_channel:
            add(configured_channel, profile=base_profile, channel=configured_channel)
        elif explicit_chrome:
            add("chrome", profile=base_profile, executable=explicit_chrome)
        elif explicit_edge:
            add("edge", profile=base_profile, executable=explicit_edge)
        else:
            add("playwright_chromium", profile=base_profile)

        if bool(getattr(portal, "allow_browser_fallback", True)):
            if primary != "chrome" and bool(getattr(portal, "fallback_to_chrome", True)):
                chrome_profile = Path(str(getattr(portal, "chrome_user_data_dir", "./data/chrome_profile") or "./data/chrome_profile"))
                add("chrome", profile=chrome_profile, executable=explicit_chrome) if explicit_chrome else add("chrome", profile=chrome_profile, channel="chrome")
            if primary != "msedge" and bool(getattr(portal, "fallback_to_edge", True)):
                edge_profile = Path(str(getattr(portal, "edge_user_data_dir", "./data/edge_profile") or "./data/edge_profile"))
                add("edge", profile=edge_profile, executable=explicit_edge) if explicit_edge else add("edge", profile=edge_profile, channel="msedge")
            if bool(getattr(portal, "fallback_to_playwright_chromium", True)):
                chromium_profile = Path(str(getattr(portal, "chromium_user_data_dir", "./data/chromium_profile") or "./data/chromium_profile"))
                add("playwright_chromium", profile=chromium_profile)
        return candidates

    async def _launch_managed_context_with_fallback(
        self, *, common_kwargs: Dict[str, Any], needs_local_cdp: bool
    ) -> BrowserContext:
        if self._mission_browser_locked:
            raise RuntimeError("HIP_BROWSER_SWITCH_PROHIBITED_AFTER_MISSION_START")
        attempts: List[Dict[str, Any]] = []
        for candidate in self._managed_browser_candidates():
            kwargs = dict(common_kwargs)
            profile = Path(str(candidate.get("profile") or self.config.portal.browser_user_data_dir))
            safe_mkdir(profile, parents=True, exist_ok=True)
            kwargs["user_data_dir"] = str(profile)
            kwargs.pop("channel", None); kwargs.pop("executable_path", None)
            if candidate.get("executable_path"):
                kwargs["executable_path"] = str(candidate["executable_path"])
            elif candidate.get("channel"):
                kwargs["channel"] = str(candidate["channel"])
            attempt: Dict[str, Any] = {
                "browser": candidate.get("name"), "profile": str(profile),
                "channel": candidate.get("channel") or "",
                "explicit_executable": bool(candidate.get("executable_path")),
                "status": "starting",
            }
            context = None
            try:
                context = await self._playwright.chromium.launch_persistent_context(**kwargs)
                self.context = context
                if needs_local_cdp:
                    health = await self._wait_for_cdp_ready(
                        timeout_seconds=float(getattr(self.config.portal, "startup_cdp_probe_seconds", 8.0) or 8.0)
                    )
                    attempt["cdp_health"] = health
                    if not health.get("ok"):
                        raise RuntimeError("remote debugging endpoint did not become healthy")
                    self._cdp_health = health
                attempt["status"] = "selected"
                attempts.append(attempt)
                self._browser_launch_attempts = attempts
                self._selected_browser = {
                    **attempt, "fallback_used": len(attempts) > 1,
                    "switch_allowed_after_mission_start": False,
                }
                safe_write_json(self.session_root_dir / "browser_startup_selection.json", {
                    "schema_version": "hip.browser-startup-selection.v1",
                    "attempts": attempts, "selected": self._selected_browser,
                })
                return context
            except Exception as exc:
                attempt["status"] = "failed"
                attempt["error"] = mask_sensitive_string(str(exc))[:1000]
                attempts.append(attempt)
                if context is not None:
                    try:
                        await asyncio.wait_for(context.close(), timeout=5)
                    except Exception:
                        pass
                self.context = None
                # Allow the fixed debugging port to be released before the next
                # branded browser candidate starts.
                await asyncio.sleep(0.4)
        self._browser_launch_attempts = attempts
        safe_write_json(self.session_root_dir / "browser_startup_selection.json", {
            "schema_version": "hip.browser-startup-selection.v1", "attempts": attempts,
            "selected": None, "status": "failed",
        })
        raise RuntimeError(
            "Could not start a HIP browser with a healthy CDP endpoint. "
            + "; ".join(f"{a.get('browser')}: {a.get('error','failed')}" for a in attempts)
        )

    async def start(self) -> Page:
        if self.page is not None and not self._closed:
            return self.page
        self._start_count += 1
        self._playwright = await async_playwright().start()
        browser_use_cfg = getattr(self.config, "browser_use", None)
        port = int(getattr(self.config.mcp, "playwright_mcp_remote_debugging_port", 9237) or 9237)
        configured_cdp = str(getattr(browser_use_cfg, "cdp_url", "") or "").strip() if browser_use_cfg else ""
        use_own_browser = bool(browser_use_cfg and getattr(browser_use_cfg, "use_own_browser", False))
        self._cdp_endpoint = configured_cdp if (use_own_browser and configured_cdp) else f"http://127.0.0.1:{port}"

        if use_own_browser:
            if not configured_cdp:
                raise RuntimeError("browser_use.use_own_browser=true requires browser_use.cdp_url pointing to an already-running Chrome/Edge/Chromium remote-debugging endpoint")
            try:
                self._external_browser = await self._playwright.chromium.connect_over_cdp(configured_cdp)
                contexts = list(self._external_browser.contexts)
                if not contexts:
                    raise RuntimeError("the CDP browser has no reusable browser context")
                self.context = contexts[0]
                self._owns_browser_context = False
            except Exception as exc:
                if not bool(getattr(browser_use_cfg, "fail_open_if_unavailable", True)):
                    raise RuntimeError(f"Could not attach HIP executor to own Chrome over CDP {configured_cdp!r}: {exc}") from exc
                # Fail-open means fall back to the normal managed persistent Chrome,
                # never to an ungoverned Browser-Use Agent.
                self._external_browser = None
                self._owns_browser_context = True
                use_own_browser = False
                self._cdp_endpoint = f"http://127.0.0.1:{port}"

        if not use_own_browser:
            launch_kwargs: Dict[str, Any] = {
                "headless": self.config.portal.headless,
                "viewport": {"width": 1440, "height": 950},
                "accept_downloads": True,
                "slow_mo": self.config.portal.slow_mo_ms,
                "args": list(self.config.portal.launch_args),
            }
            if browser_use_cfg and bool(getattr(browser_use_cfg, "record_video", False)):
                video_dir = self._resolve_browser_use_dir(str(getattr(browser_use_cfg, "record_video_dir", "") or ""), "recordings")
                self._video_dir = str(video_dir)
                launch_kwargs["record_video_dir"] = str(video_dir)
                launch_kwargs["record_video_size"] = {
                    "width": max(320, int(getattr(browser_use_cfg, "record_video_width", 1440) or 1440)),
                    "height": max(240, int(getattr(browser_use_cfg, "record_video_height", 950) or 950)),
                }
            needs_local_cdp = bool(getattr(self.config.mcp, "use_playwright_mcp", False)) or bool(
                browser_use_cfg and getattr(browser_use_cfg, "enabled", False) and getattr(browser_use_cfg, "attach_same_browser", True)
            )
            if needs_local_cdp:
                remote_arg = f"--remote-debugging-port={port}"
                if not any(str(arg).startswith("--remote-debugging-port=") for arg in launch_kwargs["args"]):
                    launch_kwargs["args"].append(remote_arg)
            self.context = await self._launch_managed_context_with_fallback(
                common_kwargs=launch_kwargs, needs_local_cdp=needs_local_cdp
            )
        assert self.context is not None
        self.context.set_default_timeout(self.config.portal.timeout_ms)
        await self.context.add_init_script(CLICK_LISTENER_SCRIPT)
        await self.context.add_init_script(DOM_EVENT_OBSERVER_SCRIPT)
        self.context.on("page", lambda p: asyncio.create_task(self._observe_new_page(p)))
        await self._start_playwright_trace()
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        await self._observe_new_page(self.page)
        # Persistent-browser/SSO stability rule: the primary Playwright context is the lifecycle
        # owner.  Verify the DevTools endpoint before starting any MCP client, and
        # do not attach Browser-Use/LangChain at startup unless explicitly requested.
        # Browser-Use uses its own cdp-use websocket and was observed reconnecting for
        # minutes during corporate SSO; it is recovery perception, not a prerequisite
        # for deterministic form filling.
        if not self._cdp_health:
            self._cdp_health = await self._wait_for_cdp_ready(timeout_seconds=10.0)
        if getattr(self.config.mcp, "browser_backend", "playwright") == "mcp":
            await self._start_required_mcp_backends()
        browser_use_cfg = getattr(self.config, "browser_use", None)
        if browser_use_cfg is not None and bool(getattr(browser_use_cfg, "startup_attach", False)):
            await self._start_browser_use_bridge()
        await self._start_auxiliary_browser_intelligence(startup=True)
        await self._capture_browser_session_history("session_started", {"own_browser": not self._owns_browser_context, "cdp_endpoint": self._cdp_endpoint, "cdp_health": self._cdp_health})
        self._mission_browser_locked = True
        self._write_session_manifest(status="started")
        return self.page

    async def _probe_cdp_endpoint(self) -> Dict[str, Any]:
        endpoint = str(self._cdp_endpoint or "").rstrip("/")
        if not endpoint:
            return {"ok": False, "error": "missing CDP endpoint"}

        def _read() -> Dict[str, Any]:
            with urlopen(endpoint + "/json/version", timeout=2.5) as response:
                raw = response.read(262144)
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            ws = str(payload.get("webSocketDebuggerUrl") or "")
            browser = str(payload.get("Browser") or "")
            return {
                "ok": bool(ws.startswith("ws://") or ws.startswith("wss://")),
                "browser": browser[:200],
                "protocol_version": str(payload.get("Protocol-Version") or "")[:80],
                "websocket_present": bool(ws),
                "endpoint": self._evidence_url(endpoint),
            }

        try:
            return await asyncio.wait_for(asyncio.to_thread(_read), timeout=3.5)
        except Exception as exc:
            return {"ok": False, "endpoint": self._evidence_url(endpoint), "error": mask_sensitive_string(str(exc))[:500]}

    async def _wait_for_cdp_ready(self, *, timeout_seconds: float = 10.0) -> Dict[str, Any]:
        deadline = asyncio.get_event_loop().time() + max(1.0, float(timeout_seconds or 10.0))
        last: Dict[str, Any] = {"ok": False, "error": "not probed"}
        while asyncio.get_event_loop().time() < deadline:
            last = await self._probe_cdp_endpoint()
            if last.get("ok"):
                return last
            await asyncio.sleep(0.25)
        safe_write_json(self.run_dir / "mcp_runtime" / "cdp_endpoint_unready.json", last)
        return last

    async def _ensure_recovery_intelligence_attached(self) -> None:
        """Lazily attach optional CDP perception clients after normal browser startup.

        Deterministic Playwright/MCP form execution must remain usable even when an
        optional Browser-Use or LangChain websocket is unavailable.
        """
        async with self._recovery_intelligence_lock:
            now = asyncio.get_running_loop().time()
            if now < float(self._recovery_intelligence_cooldown_until or 0.0):
                return
            # Fast path when both enabled bridges are already attached.
            bu_cfg = getattr(self.config, "browser_use", None)
            lc_cfg = getattr(self.config, "langchain_browser_toolkit", None)
            bu_needed = bool(
                bu_cfg and getattr(bu_cfg, "enabled", False)
                and not (bool(getattr(bu_cfg, "safe_managed_browser_mode", True)) and self._owns_browser_context)
            )
            lc_needed = bool(lc_cfg and getattr(lc_cfg, "enabled", False))
            bu_ready = (not bu_needed) or bool(self.browser_use_bridge and self.browser_use_capabilities.get("attached"))
            lc_ready = (not lc_needed) or bool(self.langchain_browser_capabilities.get("attached"))
            if bu_ready and lc_ready:
                return

            self._cdp_health = await self._wait_for_cdp_ready(timeout_seconds=5.0)
            if not self._cdp_health.get("ok"):
                # Fail open: browser intelligence is advisory.  Never stall the HIP
                # deterministic executor because an auxiliary CDP websocket is down.
                self.browser_use_capabilities = {
                    **dict(self.browser_use_capabilities or {}), "enabled": bu_needed,
                    "attached": False, "deferred": True, "error": "CDP endpoint not healthy",
                }
                self.langchain_browser_capabilities = {
                    **dict(self.langchain_browser_capabilities or {}), "enabled": lc_needed,
                    "attached": False, "deferred": True, "error": "CDP endpoint not healthy",
                }
                return

            if bu_needed and not bu_ready:
                try:
                    await asyncio.wait_for(
                        self._start_browser_use_bridge(),
                        timeout=max(2.0, float(getattr(bu_cfg, "attach_timeout_seconds", 8.0) or 8.0) + 1.0),
                    )
                except Exception as exc:
                    self.browser_use_capabilities = {
                        "enabled": True, "available": False, "attached": False,
                        "error": mask_sensitive_string(str(exc))[:1000],
                    }
            if lc_needed and not lc_ready and self.langchain_browser_toolkit is not None and self._playwright is not None:
                try:
                    self.langchain_browser_capabilities = await asyncio.wait_for(
                        self.langchain_browser_toolkit.start(playwright=self._playwright, cdp_url=self._cdp_endpoint),
                        timeout=max(2.0, float(getattr(lc_cfg, "attach_timeout_seconds", 8.0) or 8.0) + 1.0),
                    )
                except Exception as exc:
                    self.langchain_browser_capabilities = {
                        "enabled": True, "available": False, "attached": False,
                        "read_only": True, "error": mask_sensitive_string(str(exc))[:1000],
                    }
            self._recovery_intelligence_attach_attempted = True

    async def _start_browser_use_bridge(self) -> None:
        cfg = getattr(self.config, "browser_use", None)
        if cfg is None or not bool(getattr(cfg, "enabled", False)):
            return
        if bool(getattr(cfg, "safe_managed_browser_mode", True)) and self._owns_browser_context:
            # Never attach Browser-Use core to the HIP-owned Chrome lifecycle.
            # Browser-Use-style recovery state is generated non-invasively from
            # the already-authenticated Playwright page instead.
            self.browser_use_capabilities = {
                "enabled": True, "available": True, "attached": False,
                "provider": "hip_safe_playwright_observer", "read_only": True,
                "non_invasive": True, "managed_browser_protected": True,
            }
            safe_write_json(self.run_dir / "browser_use" / "capabilities.json", self.browser_use_capabilities)
            return
        port = int(getattr(self.config.mcp, "playwright_mcp_remote_debugging_port", 9237) or 9237)
        endpoint = self._cdp_endpoint or str(getattr(cfg, "cdp_url", "") or "").strip() or f"http://127.0.0.1:{port}"
        self.browser_use_bridge = BrowserUseStateBridge(
            enabled=True,
            cdp_url=endpoint,
            keep_alive=bool(getattr(cfg, "keep_alive", True)),
            max_state_chars=int(getattr(cfg, "max_state_chars", 60000) or 60000),
            attach_timeout_seconds=float(getattr(cfg, "attach_timeout_seconds", 8.0) or 8.0),
            snapshot_timeout_seconds=float(getattr(cfg, "snapshot_timeout_seconds", 4.0) or 4.0),
        )
        self.browser_use_capabilities = await self.browser_use_bridge.start()
        safe_write_json(self.run_dir / "browser_use" / "capabilities.json", self.browser_use_capabilities)
        if self.browser_use_capabilities.get("attached"):
            for p in list(self.context.pages if self.context else []):
                register_page_bridge(p, self.browser_use_bridge)
        elif not bool(getattr(cfg, "fail_open_if_unavailable", True)):
            raise RuntimeError(f"Browser-Use same-browser attachment failed: {self.browser_use_capabilities.get('error') or 'unknown error'}")

    async def _browser_use_style_safe_snapshot(self, *, max_elements: int = 120) -> Dict[str, Any]:
        """Browser-Use-style perception without a second browser lifecycle.

        This reads the same live Playwright page that HIP owns, returning only
        value-free semantic controls. It cannot close tabs, reset a SessionManager,
        or start a CDP reconnect loop.
        """
        try:
            page = await self._ensure_active_page(self._active_target_url)
            state = await capture_semantic_state(page)
            controls = []
            for row in list(state.get("controls") or [])[: max(1, int(max_elements))]:
                if not isinstance(row, Mapping):
                    continue
                controls.append({
                    "label": row.get("label") or row.get("aria_label") or "",
                    "role": row.get("role") or row.get("tag") or "",
                    "section": row.get("section") or "",
                    "enabled": bool(row.get("enabled", True)),
                    "visible": bool(row.get("visible", True)),
                    "expanded": row.get("expanded"),
                    "semantic_control_id": row.get("semantic_control_id") or "",
                })
            tabs = []
            if self.context is not None:
                for tab in list(self.context.pages or []):
                    try:
                        if tab.is_closed():
                            continue
                        tabs.append({"url": self._evidence_url(str(tab.url or ""))})
                    except Exception:
                        continue
            return mask_sensitive_data({
                "available": True, "provider": "hip_safe_playwright_observer",
                "browser_use_style": True, "browser_use_core_attached": False,
                "managed_browser_protected": True, "read_only": True,
                "url": self._evidence_url(str(page.url or "")), "tabs": tabs,
                "interactive_elements": controls, "interactive_element_count": len(controls),
                "state": {
                    "dom_generation": state.get("dom_generation"),
                    "control_count": state.get("control_count"),
                    "visible_dialog_count": state.get("visible_dialog_count"),
                    "visible_drawer_count": state.get("visible_drawer_count"),
                    "visible_listbox_count": state.get("visible_listbox_count"),
                },
                "policy": "Browser-Use-style perception only; HIP Playwright lifecycle remains authoritative",
            })
        except Exception as exc:
            return {"available": False, "provider": "hip_safe_playwright_observer", "error": mask_sensitive_string(str(exc))[:1000]}

    async def browser_use_state_snapshot(self) -> Dict[str, Any]:
        cfg = getattr(self.config, "browser_use", None)
        if bool(getattr(cfg, "safe_managed_browser_mode", True)) and self._owns_browser_context and self.browser_use_bridge is None:
            return await self._browser_use_style_safe_snapshot(max_elements=120)
        await self._ensure_recovery_intelligence_attached()
        if self.browser_use_bridge is None:
            return {"available": False, "reason": "browser-use bridge not attached"}
        cfg = getattr(self.config, "browser_use", None)
        timeout = max(1.0, float(getattr(cfg, "snapshot_timeout_seconds", 4.0) or 4.0))
        try:
            return await asyncio.wait_for(self.browser_use_bridge.state_snapshot(), timeout=timeout + 1.0)
        except Exception as exc:
            return {"available": False, "reason": "browser-use snapshot failed open", "error": mask_sensitive_string(str(exc))[:1000]}

    async def browser_use_recovery_context(self, *, max_elements: int = 120) -> Dict[str, Any]:
        cfg = getattr(self.config, "browser_use", None)
        if bool(getattr(cfg, "safe_managed_browser_mode", True)) and self._owns_browser_context and self.browser_use_bridge is None:
            return await self._browser_use_style_safe_snapshot(max_elements=max_elements)
        await self._ensure_recovery_intelligence_attached()
        if self.browser_use_bridge is None:
            return {"available": False, "reason": "browser-use bridge not attached"}
        cfg = getattr(self.config, "browser_use", None)
        timeout = max(1.0, float(getattr(cfg, "snapshot_timeout_seconds", 4.0) or 4.0))
        try:
            result = await asyncio.wait_for(self.browser_use_bridge.recovery_context(max_elements=max_elements), timeout=timeout + 2.0)
            if not result.get("available", False):
                raise RuntimeError(str(result.get("snapshot_error") or result.get("reason") or "Browser-Use snapshot unavailable"))
            return result
        except Exception as exc:
            # Browser-Use is advisory.  A broken cdp-use websocket must never hold
            # the mission in a reconnect loop; detach it and use a cooldown while
            # deterministic Playwright/MCP/vision continue on the same browser.
            try:
                await self.browser_use_bridge.stop()
            except Exception:
                pass
            self.browser_use_capabilities = {
                **dict(self.browser_use_capabilities or {}), "attached": False,
                "deferred": True, "error": mask_sensitive_string(str(exc))[:1000],
            }
            self._recovery_intelligence_cooldown_until = asyncio.get_running_loop().time() + 30.0
            return {
                "available": False, "reason": "browser-use detached after bounded recovery failure; deterministic executor continues",
                "cooldown_seconds": 30, "error": mask_sensitive_string(str(exc))[:1000],
            }

    async def _start_auxiliary_browser_intelligence(self, *, startup: bool = False) -> None:
        """Initialize optional recovery planners; CDP toolkits are lazy by default."""
        if self.autowebglm_bridge is not None:
            self.autowebglm_capabilities = self.autowebglm_bridge.status()
            safe_write_json(self.run_dir / "browser_intelligence" / "autowebglm_capabilities.json", self.autowebglm_capabilities)
        if self.langchain_browser_toolkit is not None and self._playwright is not None:
            lc_cfg = getattr(self.config, "langchain_browser_toolkit", None)
            if (not startup) or bool(getattr(lc_cfg, "startup_attach", False)):
                self.langchain_browser_capabilities = await self.langchain_browser_toolkit.start(
                    playwright=self._playwright, cdp_url=self._cdp_endpoint
                )
            else:
                self.langchain_browser_capabilities = {
                    "enabled": bool(getattr(lc_cfg, "enabled", True)), "available": False,
                    "attached": False, "read_only": True, "deferred_until_recovery": True,
                }
            safe_write_json(self.run_dir / "browser_intelligence" / "langchain_toolkit_capabilities.json", self.langchain_browser_capabilities)
        if self.vision_runtime is not None:
            self.vision_capabilities = self.vision_runtime.status()
            safe_write_json(self.run_dir / "browser_intelligence" / "vision_capabilities.json", self.vision_capabilities)

    async def browser_intelligence_recovery_context(
        self, *, task: str, include_autowebglm_proposal: bool = True,
        expected_intent: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Aggregate independent recovery perception without changing the page.

        Browser-Use, LangChain and AutoWebGLM all inspect the same authenticated
        Chrome. Their output is advisory; the deterministic phase executor and
        AgentQ reward gate decide/verify any eventual action.
        """
        await self._ensure_recovery_intelligence_attached()
        payload: Dict[str, Any] = {
            "schema_version": "hip.browser-intelligence-recovery.v1",
            "task": str(task or "")[:2000],
            "same_authenticated_browser": True,
        }
        try:
            payload["browser_use"] = await self.browser_use_recovery_context(max_elements=220)
        except Exception as exc:
            payload["browser_use"] = {"available": False, "error": mask_sensitive_string(str(exc))[:1000]}
        if self.langchain_browser_toolkit is not None:
            try:
                payload["langchain_browser_toolkit"] = await self.langchain_browser_toolkit.recovery_context()
            except Exception as exc:
                payload["langchain_browser_toolkit"] = {"available": False, "error": mask_sensitive_string(str(exc))[:1000]}
        else:
            payload["langchain_browser_toolkit"] = {"available": False, "reason": "not configured"}
        if self.vision_runtime is not None and self.page is not None:
            try:
                payload["vision"] = await self.vision_runtime.recovery_context(page=self.page, task=task)
                self.vision_capabilities = self.vision_runtime.status()
            except Exception as exc:
                payload["vision"] = {"available": False, "error": mask_sensitive_string(str(exc))[:1000]}
        else:
            payload["vision"] = {"available": False, "reason": "not configured"}
        if self.autowebglm_bridge is not None and self.page is not None:
            history = [getattr(x, "__dict__", x) for x in list(self.action_events or [])[-80:]]
            try:
                if include_autowebglm_proposal and expected_intent:
                    payload["autowebglm"] = await self.autowebglm_bridge.propose(
                        page=self.page, task=task, history=history, expected_intent=expected_intent
                    )
                else:
                    observation = await self.autowebglm_bridge.build_observation(
                        page=self.page, task=task, history=history, expected_intent=expected_intent
                    )
                    payload["autowebglm"] = {
                        "status": "observation_only_no_vetted_intent" if not expected_intent else "observation_only",
                        "action_generation_allowed": False,
                        "reason": "Recovery context is perception-only until the deterministic HIP executor supplies a vetted expected_intent.",
                        "observation": observation,
                    }
            except Exception as exc:
                payload["autowebglm"] = {"status": "unavailable", "error": mask_sensitive_string(str(exc))[:1000]}
        else:
            payload["autowebglm"] = {"available": False, "reason": "not configured"}
        safe_payload = mask_sensitive_data(payload)
        # Keep recovery context large enough to be useful but bounded for the LLM.
        # Trim the heaviest perception strings first while preserving action/status
        # metadata and AgentQ-relevant history.
        max_chars = int(getattr(getattr(self.config, "expert_skills", None), "recovery_context_max_chars", 128000) or 128000)
        try:
            raw = json.dumps(safe_payload, ensure_ascii=False, default=str)
            if len(raw) > max_chars:
                safe_payload["context_truncated"] = True
                awg = safe_payload.get("autowebglm") if isinstance(safe_payload.get("autowebglm"), dict) else {}
                obs = awg.get("observation") if isinstance(awg.get("observation"), dict) else awg
                if isinstance(obs, dict) and isinstance(obs.get("simplified_html"), str):
                    obs["simplified_html"] = obs["simplified_html"][:42000]
                lc = safe_payload.get("langchain_browser_toolkit") if isinstance(safe_payload.get("langchain_browser_toolkit"), dict) else {}
                for key, cap in (("extract_text", 16000), ("get_elements", 22000), ("extract_hyperlinks", 6000)):
                    row = lc.get(key) if isinstance(lc.get(key), dict) else None
                    if row is not None and isinstance(row.get("result"), str):
                        row["result"] = row["result"][:cap]
                        row["truncated"] = True
                bu = safe_payload.get("browser_use") if isinstance(safe_payload.get("browser_use"), dict) else {}
                if isinstance(bu.get("interactive_elements"), list):
                    bu["interactive_elements"] = bu["interactive_elements"][:140]
                raw = json.dumps(safe_payload, ensure_ascii=False, default=str)
                if len(raw) > max_chars and isinstance(obs, dict) and isinstance(obs.get("simplified_html"), str):
                    obs["simplified_html"] = obs["simplified_html"][:22000]
        except Exception:
            pass
        return safe_payload

    def _session_phase_metrics(self) -> Dict[str, Any]:
        ordered_unique: List[str] = []
        attempts_by_phase: Dict[str, int] = {}
        for entry in self._phase_history:
            phase = str(entry.get("phase") or "").strip()
            if not phase:
                continue
            attempts_by_phase[phase] = attempts_by_phase.get(phase, 0) + 1
            if phase not in ordered_unique:
                ordered_unique.append(phase)
        return {
            "phase_attempt_borrow_count": self._borrow_count,
            "unique_phases_reached": ordered_unique,
            "unique_phase_count": len(ordered_unique),
            "phase_retry_count": max(0, self._borrow_count - len(ordered_unique)),
            "attempts_by_phase": attempts_by_phase,
        }

    def _write_session_manifest(self, *, status: str) -> None:
        payload = {
            "schema_version": "hip.browser-session.v2",
            "session_id": self.session_id,
            "status": status,
            "start_count": self._start_count,
            "borrow_count": self._borrow_count,
            "sso_prompt_count": self._sso_prompt_count,
            "reauth_count": self._reauth_count,
            "authenticated_once": self._authenticated_once,
            "current_url": self._evidence_url(str(self.page.url if self.page else "")),
            "browser_use": mask_sensitive_data(self.browser_use_capabilities),
            "autowebglm": mask_sensitive_data(self.autowebglm_capabilities),
            "langchain_browser_toolkit": mask_sensitive_data(self.langchain_browser_capabilities),
            "vision_runtime": mask_sensitive_data(self.vision_capabilities),
            "browser_channel": str(getattr(self.config.portal, "chromium_channel", "") or ""),
            "selected_browser": mask_sensitive_data(self._selected_browser),
            "browser_launch_attempts": mask_sensitive_data(self._browser_launch_attempts),
            "browser_switch_allowed_during_mission": False,
            "own_browser_cdp_attachment": not self._owns_browser_context,
            "cdp_endpoint": self._evidence_url(self._cdp_endpoint),
            "browser_use_artifacts": {
                "record_video_dir": self._video_dir,
                "trace_path": self._trace_path,
                "download_count": len(self.download_records),
                "history_entries": len(self.browser_session_history),
            },
            "phase_history": self._phase_history,
            **self._session_phase_metrics(),
            "single_context_contract": True,
            "note": "One persistent Microsoft Edge context is reused across all phases in a full run. SSO is requested only when the live session is not authenticated.",
        }
        try:
            safe_write_json(self.session_root_dir / "browser_session_manifest.json", payload)
        except Exception:
            pass

    def _bind_evidence_run_dir(self, run_dir: Path) -> None:
        """Point all phase-local evidence writers at the borrowed phase directory."""
        self.run_dir = Path(run_dir)
        self.network_dir = self.run_dir / "network"
        self.console_dir = self.run_dir / "console"
        self.clicks_dir = self.run_dir / "clicks"
        self.actions_dir = self.run_dir / "actions"
        self.dom_dir = self.run_dir / "dom_snapshots"
        self.dom_events_dir = self.run_dir / "dom_events"
        for directory in [
            self.network_dir,
            self.console_dir,
            self.clicks_dir,
            self.actions_dir,
            self.dom_dir,
            self.dom_events_dir,
            self.run_dir / "mcp_runtime",
        ]:
            directory.mkdir(parents=True, exist_ok=True)

    def _reset_phase_evidence_buffers(self) -> None:
        """Start a clean evidence window while preserving the browser/context."""
        self.network_records = []
        self.network_tab_events = []
        self.console_messages = []
        self.click_events = []
        self.action_events = []
        self.dom_event_records = []
        self.dom_mutation_records = []
        self.dom_transition_records = []
        self._cdp_requests = {}
        self._logs_flushed = False

    async def _ensure_active_page(self, expected_url: str = "") -> Page:
        """Recover a usable page inside the same persistent browser context."""
        # Unit-test and injected-session callers may provide an already usable page
        # without a real BrowserContext. Preserve that supported contract.
        if self.context is None and self.page is not None:
            return self.page
        if self.context is None:
            await self.start()
        assert self.context is not None
        current = self.page
        try:
            current_closed = current is None or current.is_closed()
        except Exception:
            current_closed = current is None
        if current_closed:
            pages = []
            try:
                pages = [p for p in self.context.pages if not p.is_closed()]
            except Exception:
                pages = list(self.context.pages or [])
            self.page = pages[-1] if pages else await self.context.new_page()
            self._page_recovery_count += 1
            await self._observe_new_page(self.page)
        if expected_url:
            await self._adopt_best_page_for_url(expected_url)
        assert self.page is not None
        return self.page

    async def _ensure_page_observers(self) -> None:
        page = await self._ensure_active_page(self._active_target_url)
        for script in (CLICK_LISTENER_SCRIPT, DOM_EVENT_OBSERVER_SCRIPT):
            try:
                await page.evaluate(script)
            except Exception:
                # add_init_script already covers the next document. A failed evaluate
                # here usually means the current document is navigating.
                pass

    async def _dismiss_transient_ui(self, *, next_phase: str = "") -> Dict[str, Any]:
        """Safely close dropdowns/drawers left by the previous no-save phase."""
        page = await self._ensure_active_page(self._active_target_url)
        audit: Dict[str, Any] = {
            "status": "ok",
            "from_phase": self._active_phase_name,
            "next_phase": next_phase,
            "url": self._evidence_url(str(page.url or "")),
            "escape_presses": 0,
            "cancel_clicked": False,
            "discard_confirmed": False,
            "warnings": [],
        }
        try:
            await page.evaluate("""
() => {
  const el = document.activeElement;
  if (el && el !== document.body) {
    try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch (_) {}
    try { el.blur(); } catch (_) {}
  }
}
""")
        except Exception:
            pass
        for _ in range(3):
            try:
                await page.keyboard.press("Escape")
                audit["escape_presses"] += 1
                await page.wait_for_timeout(80)
            except Exception:
                break

        # Only abandon a visible Create/Add form at a phase boundary. This is a
        # no-save cleanup and never clicks Submit/Create/Save.
        try:
            body = (await page.locator("body").inner_text(timeout=2000)).lower()
        except Exception:
            body = ""
        create_surface = any(token in body for token in [
            "create map", "create document type", "create rule",
            "create transport profile", "create biz flow",
        ])
        if create_surface and next_phase and next_phase != self._active_phase_name:
            cancel = page.locator(
                "form button, [role='dialog'] button, dds-drawer button, .dds__drawer button"
            ).filter(has_text=re.compile(r"^\s*(cancel|close|back)\s*$", re.I)).first
            try:
                if await cancel.count() and await cancel.is_visible(timeout=1000):
                    try:
                        await cancel.click(timeout=3500)
                    except Exception:
                        await cancel.evaluate("el => el.click()")
                    audit["cancel_clicked"] = True
                    await page.wait_for_timeout(250)
            except Exception as exc:
                audit["warnings"].append(mask_sensitive_string(f"phase-boundary cancel failed: {exc}"))

            # Handle only an explicit discard/leave confirmation for the no-save
            # form. Never accept a generic confirmation unrelated to abandoning it.
            try:
                dialogs = page.locator("[role='dialog'], .dds__modal, .modal-dialog")
                count = await dialogs.count()
                for idx in range(count):
                    dialog = dialogs.nth(idx)
                    if not await dialog.is_visible(timeout=300):
                        continue
                    text = (await dialog.inner_text(timeout=1000)).lower()
                    if not any(x in text for x in ["unsaved", "discard", "leave", "changes will be lost"]):
                        continue
                    discard = dialog.locator("button").filter(
                        has_text=re.compile(r"^\s*(discard|leave|yes)\s*$", re.I)
                    ).first
                    if await discard.count() and await discard.is_visible(timeout=500):
                        await discard.click(timeout=2500)
                        audit["discard_confirmed"] = True
                        break
            except Exception as exc:
                audit["warnings"].append(mask_sensitive_string(f"discard confirmation cleanup failed: {exc}"))

        try:
            safe_write_json(self.run_dir / "phase_boundary_cleanup.json", audit)
        except Exception:
            pass
        return audit

    async def prepare_borrowed_phase(self, phase_run_dir: Path, phase_name: str) -> None:
        """Finalize the previous phase and bind the persistent session to the next."""
        next_phase = str(phase_name or Path(phase_run_dir).name)
        if self._active_phase_name:
            try:
                await self._dismiss_transient_ui(next_phase=next_phase)
            finally:
                try:
                    await self.flush_logs(force=True)
                except Exception:
                    pass
        self._reset_phase_evidence_buffers()
        self._bind_evidence_run_dir(Path(phase_run_dir))
        self._active_phase_name = next_phase
        self._phase_transition_count += 1
        await self._ensure_active_page()
        await self._ensure_page_observers()
        self.register_borrowed_phase(Path(phase_run_dir), next_phase, already_bound=True)
        cfg = getattr(self.config, "browser_use", None)
        if cfg is not None and bool(getattr(cfg, "capture_state_on_phase_transition", True)):
            await self._capture_browser_session_history("phase_transition", {"phase": next_phase})

    def register_borrowed_phase(self, phase_run_dir: Path, phase_name: str, *, already_bound: bool = False) -> None:
        if not already_bound:
            self._reset_phase_evidence_buffers()
            self._bind_evidence_run_dir(Path(phase_run_dir))
            self._active_phase_name = str(phase_name or Path(phase_run_dir).name)
        self._borrow_count += 1
        self._logs_flushed = False
        entry = {
            "phase": str(phase_name or ""),
            "phase_run_dir": str(phase_run_dir),
            "borrow_index": self._borrow_count,
            "session_id": self.session_id,
            "current_url": self._evidence_url(str(self.page.url if self.page else "")),
            "timestamp": utc_now(),
        }
        self._phase_history.append(entry)
        try:
            safe_write_json(phase_run_dir / "browser_session_reuse.json", {
                "status": "reused",
                "single_persistent_context": True,
                **entry,
            })
        except Exception:
            pass
        self._write_session_manifest(status="active")

    async def _observe_new_page(self, page: Page) -> None:
        pid = id(page)
        if pid in self._observed_pages:
            return
        self._observed_pages.add(pid)
        try:
            setattr(page, "_hip_browser_session", self)
            if self.playwright_mcp_backend is not None:
                setattr(page, "_hip_playwright_mcp_backend", self.playwright_mcp_backend)
            if self.autowebglm_bridge is not None:
                setattr(page, "_hip_autowebglm_bridge", self.autowebglm_bridge)
            if self.semantic_action_gate is not None:
                setattr(page, "_hip_semantic_action_gate", self.semantic_action_gate)
            if self.browser_use_bridge is not None and self.browser_use_capabilities.get("attached"):
                register_page_bridge(page, self.browser_use_bridge)
        except Exception:
            pass
        try:
            page.on("download", lambda download: asyncio.create_task(self._save_download(download)))
        except Exception:
            pass
        await self._attach_observers(page)
        await self._start_cdp_network(page)

    async def _start_required_mcp_backends(self) -> None:
        """Attach both Chrome DevTools MCP and official Playwright MCP.

        Playwright MCP connects to the exact browser launched above through CDP. This
        is essential: launching a second MCP-owned browser would not share Dell SSO or
        the active HIP form state.
        """
        errors: List[str] = []
        strict_mcp_runtime = bool(getattr(self.config.mcp, "strict_runtime_required", True))
        port = int(getattr(self.config.mcp, "playwright_mcp_remote_debugging_port", 9237) or 9237)
        endpoint = self._cdp_endpoint or f"http://127.0.0.1:{port}"
        if getattr(self.config.mcp, "use_chrome_devtools_mcp", True):
            try:
                from .chrome_devtools_mcp import ChromeDevToolsMCPBackend
                backend = ChromeDevToolsMCPBackend.from_config(self.config, self.run_dir / "mcp_runtime" / "chrome_devtools", cdp_endpoint=endpoint)
                await backend.start()
                self.mcp_backend = backend
                tool_names = sorted(getattr(backend.client, "tools", {}) or {})
                self.mcp_capabilities = {
                    "required": strict_mcp_runtime,
                    "available": True,
                    "backend": backend.name,
                    "tool_count": len(tool_names),
                    "tools": tool_names,
                    "cdp_endpoint": endpoint,
                    "same_browser_attachment_requested": True,
                    "launch_args": getattr(backend, "launch_args", []),
                }
                safe_write_json(self.run_dir / "mcp_runtime" / "chrome_devtools_mcp_capabilities.json", self.mcp_capabilities)
            except Exception as exc:
                self.mcp_capabilities = {
                    "required": strict_mcp_runtime,
                    "available": False,
                    "backend": "chrome-devtools-mcp",
                    "error": mask_sensitive_string(str(exc)),
                }
                safe_write_json(self.run_dir / "mcp_runtime" / "chrome_devtools_mcp_capabilities.json", self.mcp_capabilities)
                if strict_mcp_runtime:
                    errors.append(f"Chrome DevTools MCP: {exc}")

        if getattr(self.config.mcp, "use_playwright_mcp", True):
            try:
                from .playwright_mcp import PlaywrightMCPBackend
                pw_backend = PlaywrightMCPBackend.from_config(
                    self.config,
                    self.run_dir / "mcp_runtime" / "playwright",
                    cdp_endpoint=endpoint,
                )
                await pw_backend.start()
                self.playwright_mcp_backend = pw_backend
                if self.page is not None:
                    try:
                        setattr(self.page, "_hip_playwright_mcp_backend", pw_backend)
                    except Exception:
                        pass
                tool_names = sorted(getattr(pw_backend.client, "tools", {}) or {})
                self.playwright_mcp_capabilities = {
                    "required": bool(strict_mcp_runtime and getattr(self.config.mcp, "playwright_mcp_required_when_require_mcp", True)),
                    "available": True,
                    "backend": pw_backend.name,
                    "cdp_endpoint": endpoint,
                    "same_browser_as_deterministic_executor": True,
                    "tool_count": len(tool_names),
                    "tools": tool_names,
                }
                safe_write_json(self.run_dir / "mcp_runtime" / "playwright_mcp_capabilities.json", self.playwright_mcp_capabilities)
                # Prove the MCP sees the current HIP tab before any form is filled.
                try:
                    snap = await pw_backend.snapshot(boxes=True, depth=5)
                    safe_write_json(self.run_dir / "mcp_runtime" / "playwright" / "startup_snapshot.json", snap)
                except Exception as snap_exc:
                    self.playwright_mcp_capabilities["startup_snapshot_warning"] = mask_sensitive_string(str(snap_exc))
                    if strict_mcp_runtime:
                        errors.append(f"Playwright MCP attached but startup snapshot failed: {snap_exc}")
            except Exception as exc:
                self._playwright_mcp_surface_healthy = False
                self.playwright_mcp_capabilities = {
                    "required": bool(strict_mcp_runtime and getattr(self.config.mcp, "playwright_mcp_required_when_require_mcp", True)),
                    "available": False,
                    "backend": "playwright-mcp",
                    "error": mask_sensitive_string(str(exc)),
                }
                safe_write_json(self.run_dir / "mcp_runtime" / "playwright_mcp_capabilities.json", self.playwright_mcp_capabilities)
                if strict_mcp_runtime and getattr(self.config.mcp, "playwright_mcp_required_when_require_mcp", True):
                    errors.append(f"Playwright MCP: {exc}")

        py_cfg = getattr(self.config, "pyautogui", None)
        if bool(py_cfg is not None and getattr(py_cfg, "enabled", True) and getattr(py_cfg, "mcp_enabled", True)) and self.pyautogui_mcp_backend is None:
            py_required = bool(getattr(py_cfg, "mcp_required", False))
            if bool(getattr(py_cfg, "windows_only", True)) and os.name != "nt":
                self.pyautogui_mcp_capabilities = {
                    "required": py_required, "available": False, "backend": "pyautogui-mcp",
                    "reason": "windows_desktop_required",
                }
            elif bool(getattr(self.config.portal, "headless", False)):
                self.pyautogui_mcp_capabilities = {
                    "required": py_required, "available": False, "backend": "pyautogui-mcp",
                    "reason": "headed_desktop_required",
                }
            else:
                try:
                    from .pyautogui_mcp import PyAutoGUIMCPBackend
                    desktop_backend = PyAutoGUIMCPBackend.from_config(
                        self.config, self.run_dir / "mcp_runtime" / "pyautogui"
                    )
                    await desktop_backend.start()
                    self.pyautogui_mcp_backend = desktop_backend
                    self.pyautogui_tool.set_mcp_backend(desktop_backend)
                    self.pyautogui_mcp_capabilities = desktop_backend.capability_status()
                    self.pyautogui_mcp_capabilities["required"] = py_required
                except Exception as exc:
                    self.pyautogui_mcp_backend = None
                    self.pyautogui_tool.set_mcp_backend(None)
                    self.pyautogui_mcp_capabilities = {
                        "required": py_required, "available": False, "backend": "pyautogui-mcp",
                        "error": mask_sensitive_string(str(exc)),
                    }
                    if py_required:
                        errors.append(f"PyAutoGUI MCP: {exc}")
            safe_write_json(
                self.run_dir / "mcp_runtime" / "pyautogui_mcp_capabilities.json",
                self.pyautogui_mcp_capabilities,
            )

        self.backend_name = "+".join([
            name for name, ok in [
                ("playwright-mcp", bool(self.playwright_mcp_backend)),
                ("chrome-devtools-mcp", bool(self.mcp_backend)),
                ("pyautogui-mcp", bool(self.pyautogui_mcp_backend)),
                ("python-playwright", True),
            ] if ok
        ])
        safe_write_json(
            self.run_dir / "mcp_runtime" / "dual_mcp_runtime.json",
            {
                "backend_name": self.backend_name,
                "playwright_mcp": self.playwright_mcp_capabilities,
                "chrome_devtools_mcp": self.mcp_capabilities,
                "pyautogui_mcp": self.pyautogui_mcp_capabilities,
                "policy": "Adaptive runtime: PyAutoGUI MCP is preferred for physical interaction; Playwright MCP is deterministic fallback; Python Playwright remains the final governed fallback. Chrome DevTools and HIP Intelligence are independent witnesses when available. Explicit strict MCP mode requires the complete configured witness stack.",
                "strict_mcp_runtime": strict_mcp_runtime,
                "executor_quorum": {
                    "pyautogui_mcp": bool(self.pyautogui_mcp_backend),
                    "playwright_mcp": bool(self.playwright_mcp_backend),
                    "playwright_mcp_same_surface": bool(self._playwright_mcp_surface_healthy),
                    "python_playwright": True,
                    "at_least_one_executor": True,
                },
                "errors": [mask_sensitive_string(x) for x in errors],
            },
        )
        if errors:
            raise RuntimeError(
                "Required dual MCP runtime could not be started: " + " | ".join(errors) +
                ". Install dependencies with `npm install` or `bun install`; the shipped MCP runner auto-selects npx first and Bun as a fallback."
            )

    async def _mcp_snapshot_after_action(self, action_id: str) -> None:
        if not self.playwright_mcp_backend or not getattr(self.config.mcp, "playwright_mcp_snapshot_after_action", True):
            return
        try:
            snap = await self.playwright_mcp_backend.snapshot(boxes=True, depth=8)
            safe_write_json(self.run_dir / "mcp_runtime" / "playwright" / "action_snapshots" / f"{action_id}.json", snap)
        except Exception as exc:
            safe_write_json(
                self.run_dir / "mcp_runtime" / "playwright" / "action_snapshots" / f"{action_id}.error.json",
                {"error": mask_sensitive_string(str(exc))},
            )

    def _mcp_safe_selector(self, selector: str) -> bool:
        text = (selector or "").strip()
        if not text or len(text) > 1000:
            return False
        # Playwright MCP accepts ordinary CSS selectors.  Human descriptions such as
        # "Data Maps top-right + Add" are *not* selectors even though older code
        # accidentally treated them as safe.  The live action broker canonicalizes
        # every Locator to a unique DOM selector before it asks MCP to act.
        bad = ["text=/", ">>", "locator(", "get_by_", "getBy", "nth=", ".."]
        if any(token in text for token in bad):
            return False
        # V244: DDS option tokens are ephemeral attributes injected by our own
        # snapshot collector. Some Playwright MCP implementations parse these
        # through a restricted selector grammar and reject otherwise-valid CSS.
        # Keep them on the already-proven direct Playwright locator path instead
        # of sending them through the MCP selector re-parser.
        if "data-hip-single-option-token" in text:
            return False
        if any(ch.isspace() for ch in text) and not any(tok in text for tok in ("[", ".", "#", ">", ":")):
            return False
        if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", text):
            # Bare CSS type selectors are accepted only for real/common HTML tags
            # or custom elements (which must contain a hyphen). This prevents a
            # friendly label such as "Add" from being mistaken for CSS.
            html_tags = {
                "a","button","input","textarea","select","option","label","form",
                "div","span","table","tbody","thead","tr","td","th","ul","ol",
                "li","nav","main","section","article","dialog","body","html"
            }
            return text.lower() in html_tags or "-" in text
        return bool(re.search(r"[#.\[\]>:+~]", text))

    async def _canonical_selector_for_locator(self, locator: Locator, selector_hint: str = "") -> Dict[str, Any]:
        """Return a unique, same-generation CSS selector for a proven Locator.

        HIP callers historically passed friendly descriptions (for example
        ``Data Maps top-right + Add``) in the ``selector`` field.  PyAutoGUI can use
        the Locator's bounding box, but Playwright MCP requires an actual selector.
        This method converts the *already-proven* Locator into a unique CSS selector
        immediately before dispatch, so either executor can perform the same action.

        No selector produced here is persisted as long-term portal knowledge.  An
        nth-of-type path is allowed only as an immediate same-DOM-generation fallback.
        """
        hint = str(selector_hint or "").strip()
        try:
            result = await locator.first.evaluate(
                r"""(el, hint) => {
                  const root = el.ownerDocument || document;
                  const escAttr = (v) => String(v ?? '').replace(/\\/g,'\\\\').replace(/"/g,'\\"');
                  const unique = (sel) => {
                    if (!sel) return false;
                    try {
                      const nodes = root.querySelectorAll(sel);
                      return nodes.length === 1 && nodes[0] === el;
                    } catch (_) { return false; }
                  };
                  if (hint && unique(hint)) return {selector:hint, strategy:'caller_exact', count:1};

                  const tag = (el.tagName || '').toLowerCase();
                  const candidates = [];
                  const pushAttr = (attr) => {
                    const value = el.getAttribute && el.getAttribute(attr);
                    if (value) candidates.push(`${tag}[${attr}="${escAttr(value)}"]`);
                  };

                  if (el.id) candidates.push(`#${CSS.escape(el.id)}`);
                  ['data-testid','data-test','data-qa','data-cy','data-control-id','name','aria-label','title','placeholder','role'].forEach(pushAttr);

                  // Dell DDS commonly wraps the actionable native button/input in a
                  // semantic component.  Include stable host attributes before using
                  // a positional path.
                  const host = el.closest && el.closest('dds-button,dds-select,dds-input,dds-checkbox,dds-radio-button,dds-switch');
                  if (host) {
                    const htag = (host.tagName || '').toLowerCase();
                    const attrs = ['kind','size','role','aria-label','name'];
                    let hostSel = htag;
                    for (const a of attrs) {
                      const v = host.getAttribute && host.getAttribute(a);
                      if (v) hostSel += `[${a}="${escAttr(v)}"]`;
                    }
                    if (tag) {
                      const aria = el.getAttribute && el.getAttribute('aria-label');
                      const child = aria ? `${tag}[aria-label="${escAttr(aria)}"]` : tag;
                      candidates.unshift(`${hostSel} ${child}`);
                    }
                  }

                  for (const sel of candidates) {
                    if (unique(sel)) return {selector:sel, strategy:'stable_attribute', count:1};
                  }

                  // Immediate same-generation fallback.  This is intentionally not
                  // learned/persisted because Angular may rerender it later.
                  const parts = [];
                  let node = el;
                  let depth = 0;
                  while (node && node.nodeType === 1 && depth < 12) {
                    const ntag = (node.tagName || '').toLowerCase();
                    if (!ntag) break;
                    let part = ntag;
                    if (node.id) {
                      part = `#${CSS.escape(node.id)}`;
                      parts.unshift(part);
                      break;
                    }
                    const parent = node.parentElement;
                    if (parent) {
                      const same = Array.from(parent.children).filter(x => (x.tagName || '').toLowerCase() === ntag);
                      if (same.length > 1) part += `:nth-of-type(${same.indexOf(node)+1})`;
                    }
                    parts.unshift(part);
                    const path = parts.join(' > ');
                    if (unique(path)) return {selector:path, strategy:'same_generation_path', count:1};
                    node = parent; depth += 1;
                  }
                  const finalSel = parts.join(' > ');
                  return {selector: unique(finalSel) ? finalSel : '', strategy:'unresolved', count:0};
                }""",
                hint,
            )
            if isinstance(result, dict):
                selector = str(result.get("selector") or "").strip()
                result["mcp_safe"] = bool(self._mcp_safe_selector(selector))
                result["selector_hint"] = hint
                return result
        except Exception as exc:
            return {
                "selector": "", "strategy": "error", "count": 0, "mcp_safe": False,
                "selector_hint": hint, "error": mask_sensitive_string(str(exc))[:500],
            }
        return {"selector": "", "strategy": "unresolved", "count": 0, "mcp_safe": False, "selector_hint": hint}

    async def close(self) -> None:
        """Close Playwright without hanging the export during shutdown.

        Long inventory runs can leave many async response observers in flight.  If the
        user stops the script, Playwright's node driver may emit EPIPE while Python is
        also closing transports.  Shutdown must therefore be best-effort and bounded.
        Logs are flushed only once; the main inventory flow already flushes them before
        final reporting.
        """
        if self._closed:
            return
        self._closed = True
        self._write_session_manifest(status="closing")
        try:
            if not self._logs_flushed:
                await asyncio.wait_for(self.flush_logs(), timeout=45)
        except Exception as exc:
            try:
                self.console_messages.append({"timestamp": utc_now(), "type": "shutdown_warning", "text": mask_sensitive_string(f"best-effort log flush skipped during close: {exc}"), "stage": self._current_stage})
            except Exception:
                pass
        try:
            await self._capture_browser_session_history("session_closing")
        except Exception:
            pass
        try:
            await self._stop_playwright_trace()
        except Exception:
            pass
        try:
            if self.langchain_browser_toolkit is not None:
                await self.langchain_browser_toolkit.stop()
        except Exception:
            pass
        try:
            if self.browser_use_bridge is not None:
                await self.browser_use_bridge.stop()
        except Exception:
            pass
        try:
            if self.playwright_mcp_backend is not None:
                await asyncio.wait_for(self.playwright_mcp_backend.close(), timeout=15)
        except Exception:
            pass
        try:
            if self.mcp_backend is not None:
                await asyncio.wait_for(self.mcp_backend.close(), timeout=10)
        except Exception:
            pass
        try:
            if self.pyautogui_mcp_backend is not None:
                await asyncio.wait_for(self.pyautogui_mcp_backend.close(), timeout=10)
        except Exception:
            pass
        finally:
            self.pyautogui_mcp_backend = None
            try:
                self.pyautogui_tool.set_mcp_backend(None)
            except Exception:
                pass
        try:
            keep_external_open = bool(
                (not self._owns_browser_context)
                and getattr(getattr(self.config, "browser_use", None), "keep_browser_open", True)
            )
            if self.context and not keep_external_open:
                await asyncio.wait_for(self.context.close(), timeout=20)
        except Exception:
            pass
        try:
            if self._playwright:
                await asyncio.wait_for(self._playwright.stop(), timeout=20)
        except Exception:
            pass
        self._write_session_manifest(status="closed")

    async def restart(self, *, reason: str = "") -> Dict[str, Any]:
        """Relaunch the persistent Chrome context after a crash or disconnect.

        The same user-data-dir is reused so existing Dell SSO cookies survive.
        Learned memory references (flow pattern memory, AgentQ controller) and
        session counters are preserved; only the Playwright/Chrome processes and
        MCP attachments are recycled.  This method never mutates the portal.
        """
        info: Dict[str, Any] = {
            "schema_version": "hip.browser-session-restart.v1",
            "reason": mask_sensitive_string(str(reason))[:500],
            "previous_start_count": self._start_count,
        }
        try:
            await self.close()
        except Exception as exc:
            info["close_error"] = mask_sensitive_string(str(exc))
        # Reset lifecycle state so start() performs a full relaunch.
        self._closed = False
        self._logs_flushed = False
        self.page = None
        self.context = None
        self._playwright = None
        self.mcp_backend = None
        self.playwright_mcp_backend = None
        self.pyautogui_mcp_backend = None
        self.pyautogui_mcp_capabilities = {}
        self.pyautogui_tool.set_mcp_backend(None)
        self.browser_use_bridge: Optional[BrowserUseStateBridge] = None
        self.browser_use_capabilities: Dict[str, Any] = {}
        await self.start()
        self._page_recovery_count += 1
        info.update({
            "status": "restarted",
            "start_count": self._start_count,
            "single_persistent_user_data_dir": True,
            "sso_cookies_reused_from_persistent_profile": True,
        })
        self._write_session_manifest(status="restarted")
        return info

    def set_stage(self, stage: str) -> None:
        self._current_stage = stage

    def _request_failure_text(self, req: Any) -> str:
        """Return Playwright request failure text across Playwright versions.

        Playwright Python versions differ here: older versions exposed a
        failure object with `.error_text`; current versions can return a
        plain string/dict-like value through the API wrapper. This helper
        must never raise because it runs inside an event listener.
        """
        try:
            failure = req.failure
        except Exception:
            failure = None
        if not failure:
            return "request failed"
        if isinstance(failure, str):
            return failure
        if isinstance(failure, dict):
            return str(failure.get("errorText") or failure.get("error_text") or failure)
        return str(getattr(failure, "error_text", None) or getattr(failure, "errorText", None) or failure)

    def _record_request_failed(self, req: Any) -> None:
        """Safe request-failed observer; never lets pyee listener exceptions kill evidence capture."""
        try:
            self.network_records.append(NetworkRecord(
                url=mask_sensitive_string(getattr(req, "url", "")),
                method=getattr(req, "method", ""),
                status=None,
                error=mask_sensitive_string(self._request_failure_text(req)),
            ))
        except Exception as exc:
            self.console_messages.append({
                "timestamp": utc_now(),
                "type": "observer_error",
                "text": mask_sensitive_string(f"requestfailed observer error: {exc}"),
                "url": getattr(req, "url", ""),
                "stage": self._current_stage,
            })

    async def _attach_observers(self, page: Page) -> None:
        try:
            await page.add_init_script(CLICK_LISTENER_SCRIPT)
            await page.add_init_script(DOM_EVENT_OBSERVER_SCRIPT)
            await page.evaluate(CLICK_LISTENER_SCRIPT)
            await page.evaluate(DOM_EVENT_OBSERVER_SCRIPT)
        except Exception:
            pass
        page.on("console", lambda msg: self.console_messages.append({"timestamp": utc_now(), "type": msg.type, "text": mask_sensitive_string(msg.text), "url": page.url, "stage": self._current_stage}))
        page.on("pageerror", lambda exc: self.console_messages.append({"timestamp": utc_now(), "type": "pageerror", "text": mask_sensitive_string(str(exc)), "url": page.url, "stage": self._current_stage}))
        page.on("requestfailed", lambda req: self._record_request_failed(req))
        page.on("response", lambda resp: asyncio.create_task(self._capture_response(resp)))

    async def _start_cdp_network(self, page: Page) -> None:
        if not self.config.extraction.capture_cdp_network or not self.context:
            return
        try:
            cdp = await self.context.new_cdp_session(page)
            self._cdp_sessions.append(cdp)
            await cdp.send("Network.enable", {"maxTotalBufferSize": 100000000, "maxResourceBufferSize": 20000000})
            cdp.on("Network.requestWillBeSent", lambda ev: self._on_cdp_request(ev, page))
            cdp.on("Network.responseReceived", lambda ev: self._on_cdp_response(ev, page))
            cdp.on("Network.loadingFinished", lambda ev: asyncio.create_task(self._on_cdp_loading_finished(ev, page, cdp)))
            cdp.on("Network.loadingFailed", lambda ev: self._on_cdp_loading_failed(ev, page))
        except Exception as exc:
            self.console_messages.append({"timestamp": utc_now(), "type": "cdp_error", "text": f"CDP network capture unavailable: {exc}", "stage": self._current_stage, "url": page.url})

    def _on_cdp_request(self, ev: Dict[str, Any], page: Page) -> None:
        req = ev.get("request", {}) or {}
        request_id = ev.get("requestId", "")
        self._cdp_requests[request_id] = {
            "request_id": request_id,
            "url": mask_sensitive_string(req.get("url", "")),
            "method": req.get("method", ""),
            "request_headers": mask_sensitive_data(req.get("headers", {}) or {}),
            "request_body_redacted": mask_sensitive_data(safe_json_loads(req.get("postData", "")) or mask_sensitive_string(req.get("postData", ""))),
            "resource_type": ev.get("type", "unknown"),
            "timestamp": utc_now(),
            "page_context": page.url,
            "initiator": mask_sensitive_data(ev.get("initiator")),
            "stage": self._current_stage,
            "cdp_session_id": id(page),
        }

    def _on_cdp_response(self, ev: Dict[str, Any], page: Page) -> None:
        request_id = ev.get("requestId", "")
        resp = ev.get("response", {}) or {}
        base = self._cdp_requests.setdefault(request_id, {"request_id": request_id, "timestamp": utc_now(), "page_context": page.url})
        base.update({
            "url": mask_sensitive_string(resp.get("url", base.get("url", ""))),
            "status": int(resp.get("status", 0) or 0),
            "mime_type": resp.get("mimeType"),
            "response_headers": mask_sensitive_data(resp.get("headers", {}) or {}),
            "resource_type": ev.get("type", base.get("resource_type", "unknown")),
            "page_context": page.url,
            "stage": self._current_stage,
        })

    async def _on_cdp_loading_finished(self, ev: Dict[str, Any], page: Page, cdp: CDPSession) -> None:
        request_id = ev.get("requestId", "")
        base = self._cdp_requests.get(request_id)
        if not base:
            return
        body_text = None
        body_json = None
        capture_status = "not_requested"
        truncated = False
        mime = str(base.get("mime_type") or "").lower()
        status = int(base.get("status") or 0)
        resource_type = str(base.get("resource_type") or "").lower()
        method = str(base.get("method") or "GET").upper()
        # Form APIs frequently return JSON under text/plain or vendor MIME types.
        # Capture all XHR/fetch responses and all mutating/validation responses,
        # not only responses whose Content-Type contains the literal word json.
        should_fetch = self.config.extraction.capture_network_json and (
            "json" in mime
            or resource_type in {"xhr", "fetch"}
            or method in {"POST", "PUT", "PATCH", "DELETE"}
            or status >= 400
        )
        if should_fetch:
            capture_status = "requested"
            try:
                body_resp = await cdp.send("Network.getResponseBody", {"requestId": request_id})
                raw = body_resp.get("body", "") or ""
                if len(raw) > self.config.extraction.max_network_body_chars:
                    raw = raw[: self.config.extraction.max_network_body_chars] + "...<truncated>"
                    truncated = True
                parsed = safe_json_loads(raw)
                if parsed is not None:
                    body_json = mask_sensitive_data(parsed)
                body_text = mask_sensitive_string(raw)
                capture_status = "captured"
            except Exception as exc:
                capture_status = "unavailable:" + mask_sensitive_string(str(exc))[:300]
        self.network_tab_events.append(NetworkTabEvent(
            request_id=request_id,
            url=mask_sensitive_string(str(base.get("url", ""))),
            method=str(base.get("method", "")),
            status=base.get("status"),
            resource_type=str(base.get("resource_type", "unknown")),
            request_headers=mask_sensitive_data(base.get("request_headers", {})),
            response_headers=mask_sensitive_data(base.get("response_headers", {})),
            request_body_redacted=mask_sensitive_data(base.get("request_body_redacted")),
            response_body_redacted=body_json,
            response_body_text_redacted=body_text,
            response_body_capture_status=capture_status,
            response_body_truncated=truncated,
            encoded_data_length=int(ev.get("encodedDataLength") or 0),
            mime_type=base.get("mime_type"),
            timestamp=base.get("timestamp", utc_now()),
            page_context=str(base.get("page_context", page.url)),
            stage=str(base.get("stage", self._current_stage)),
            initiator=mask_sensitive_data(base.get("initiator")),
        ))

    def _on_cdp_loading_failed(self, ev: Dict[str, Any], page: Page) -> None:
        request_id = ev.get("requestId", "")
        base = self._cdp_requests.get(request_id, {})
        self.network_tab_events.append(NetworkTabEvent(
            request_id=request_id,
            url=mask_sensitive_string(str(base.get("url", ""))),
            method=str(base.get("method", "")),
            resource_type=str(base.get("resource_type", ev.get("type", "unknown"))),
            request_headers=mask_sensitive_data(base.get("request_headers", {})),
            request_body_redacted=mask_sensitive_data(base.get("request_body_redacted")),
            page_context=page.url,
            stage=self._current_stage,
            error=mask_sensitive_string(ev.get("errorText", "loading failed")),
            initiator=mask_sensitive_data(base.get("initiator")),
        ))

    async def _capture_response(self, resp: Response) -> None:
        if not self.config.extraction.capture_network_json:
            return
        try:
            ct = resp.headers.get("content-type", "")
            status = resp.status
            if "json" not in ct.lower() and status < 400:
                return
            body = None
            try:
                text = await resp.text()
                body = text[: self.config.extraction.max_network_body_chars]
            except Exception:
                body = None
            self.network_records.append(NetworkRecord(
                url=mask_sensitive_string(resp.url), method=resp.request.method, status=status,
                request_post_data=mask_sensitive_string(resp.request.post_data or "") if resp.request.post_data else None,
                response_body=mask_sensitive_string(body or "") if body else None,
                content_type=ct,
            ))
        except Exception:
            return

    async def _dom_excerpt(self, limit: int = 1200) -> str:
        if not self.page:
            return ""
        try:
            txt = await self.page.locator("body").inner_text(timeout=1500)
            return mask_sensitive_string(txt[:limit])
        except Exception:
            return ""

    async def _begin_action(self, typ: str, target: str, value: Optional[str] = None, screenshot_before: bool = False) -> ActionEvent:
        before_url = self.page.url if self.page else ""
        aid = f"act-{len(self.action_events)+1:05d}"
        was_secret = bool(value is not None and (is_secret_target(target) or is_secret_target(value)))
        value_redacted = "***MASKED***" if was_secret else (mask_sensitive_string(value or "") if value is not None else None)
        shot = None
        if screenshot_before and self.page:
            try:
                shot = await self.screenshot(self.actions_dir / f"{aid}_before.png")
            except Exception:
                shot = None
        return ActionEvent(
            action_id=aid, type=typ, target=target, value_redacted=value_redacted,
            value_hash=_hash_value(value), page_url_before=before_url, timestamp_start=utc_now(),
            screenshot_before=shot, dom_excerpt=await self._dom_excerpt(900), stage=self._current_stage, backend=self.backend_name,
            was_secret=was_secret, network_event_start_index=len(self.network_tab_events),
            network_request_ids_before=list(self._cdp_requests.keys()),
        )

    async def _finish_action(self, ev: ActionEvent, success: bool = True, error: Optional[str] = None, screenshot_after: bool = False) -> None:
        ev.timestamp_end = utc_now()
        ev.page_url_after = self.page.url if self.page else ""
        ev.success = success
        ev.error = mask_sensitive_string(error or "") if error else None
        ev.network_event_end_index = len(self.network_tab_events)
        start = max(0, int(getattr(ev, "network_event_start_index", 0) or 0))
        completed_ids = [n.request_id for n in self.network_tab_events[start:ev.network_event_end_index] if getattr(n, "request_id", "")]
        before_ids = set(getattr(ev, "network_request_ids_before", []) or [])
        request_ids = [request_id for request_id in self._cdp_requests.keys() if request_id not in before_ids]
        # Preserve request order and include both still-in-flight and already
        # completed requests. This is the authoritative UI->API causality set.
        ev.network_events_triggered = list(dict.fromkeys(request_ids + completed_ids))
        if screenshot_after and self.page:
            try:
                ev.screenshot_after = await self.screenshot(self.actions_dir / f"{ev.action_id}_after.png")
            except Exception:
                pass
        self.action_events.append(ev)
        trace = getattr(self, "mission_trace", None)
        if trace is not None:
            try:
                trace.record_action(ev, phase=self._trace_phase_override or self._active_phase_name)
            except Exception:
                # Trace is evidence-only and must never break the browser mission.
                pass
        live_view = getattr(self, "agent_live_view", None)
        if live_view is not None:
            try:
                await live_view.record_result(page=self.page, event=ev)
            except Exception:
                # Human live-view telemetry must never break portal execution.
                pass
        cfg = getattr(self.config, "browser_use", None)
        if cfg is not None and bool(getattr(cfg, "capture_state_after_actions", False)):
            await self._capture_browser_session_history("action_finished", {
                "action_id": ev.action_id,
                "success": bool(success),
                "label": getattr(ev, "label", ""),
            })

    @staticmethod
    def _evidence_url(url: str) -> str:
        """Return a URL safe for logs by dropping query/fragment credentials."""
        try:
            parsed = urlparse(str(url or ""))
            if not parsed.scheme:
                return str(url or "").split("?", 1)[0].split("#", 1)[0]
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        except Exception:
            return str(url or "").split("?", 1)[0].split("#", 1)[0]

    @staticmethod
    def _surface_url_matches(left: str, right: str) -> bool:
        """Compare browser surfaces without volatile query strings or fragments."""
        try:
            a = urlparse(str(left or ""))
            b = urlparse(str(right or ""))
            if not a.scheme or not b.scheme:
                return BrowserSession._evidence_url(left).lower() == BrowserSession._evidence_url(right).lower()
            a_host = (a.hostname or "").lower()
            b_host = (b.hostname or "").lower()
            a_path = (a.path or "/").rstrip("/").lower() or "/"
            b_path = (b.path or "/").rstrip("/").lower() or "/"
            return a_host == b_host and a_path == b_path
        except Exception:
            return False

    def _is_sso_transition_url(self, url: str) -> bool:
        """Recognize Dell/corporate authentication pages as temporary navigation states."""
        text = str(url or "").strip().lower()
        if not text:
            return False
        try:
            parsed = urlparse(text)
            host = (parsed.hostname or "").lower()
            path = (parsed.path or "").lower()
        except Exception:
            host, path = "", text
        known_auth_hosts = (
            "myaccess.dell.com",
            "login.microsoftonline.com",
            "login.windows.net",
            "dell.okta.com",
        )
        if host in known_auth_hosts or host.startswith("login.") or host.startswith("auth."):
            return True
        haystack = f"{host}{path}"
        return any(str(k or "").lower() in haystack for k in self.config.portal.sso_login_url_keywords)

    async def _adopt_best_page_for_url(self, expected_url: str) -> Optional[Page]:
        """Adopt the most relevant local tab after SSO redirects or popup transitions."""
        if not self.context:
            return self.page
        try:
            pages = [p for p in self.context.pages if not getattr(p, "is_closed", lambda: False)()]
        except Exception:
            pages = list(getattr(self.context, "pages", []) or [])
        if not pages:
            return self.page
        expected = urlparse(str(expected_url or ""))
        expected_host = (expected.hostname or "").lower()
        expected_path = (expected.path or "/").rstrip("/").lower() or "/"

        def rank(item: tuple[int, Page]) -> tuple[int, int]:
            idx, page = item
            try:
                current = urlparse(str(page.url or ""))
                host = (current.hostname or "").lower()
                path = (current.path or "/").rstrip("/").lower() or "/"
                raw = str(page.url or "").lower()
            except Exception:
                host, path, raw = "", "/", ""
            score = 0
            if host == expected_host and path == expected_path:
                score = 100
            elif host == expected_host and any(p.lower() in raw for p in self.config.portal.sso_success_url_patterns):
                score = 80
            elif any(p.lower() in raw for p in self.config.portal.sso_success_url_patterns):
                score = 60
            elif raw and not self._is_sso_transition_url(raw) and raw != "about:blank":
                score = 20
            if page is self.page:
                score += 1
            return score, idx

        selected = max(enumerate(pages), key=rank)[1]
        if selected is not self.page:
            self.page = selected
            await self._observe_new_page(selected)
        return self.page

    async def _accepted_navigation_state(self, target_url: str) -> str:
        """Return target, sso_redirect, or empty for the current browser state."""
        await self._adopt_best_page_for_url(target_url)
        if await self._navigation_page_is_usable(target_url):
            return "target"
        current_url = str(self.page.url if self.page else "")
        if self._is_sso_transition_url(current_url):
            return "sso_redirect"
        return ""

    async def _finish_accepted_navigation(self, ev: ActionEvent, target_url: str, *, note: str = "") -> bool:
        state = await self._accepted_navigation_state(target_url)
        if state == "target":
            await self.wait_ready()
            await self._verify_dual_mcp_same_surface(target_url)
            await self._mcp_snapshot_after_action(ev.action_id)
            await self._finish_action(ev, True, error=note or None)
            return True
        if state == "sso_redirect":
            current_url = str(self.page.url if self.page else "")
            evidence = {
                "status": "deferred_for_sso",
                "requested_url": self._evidence_url(target_url),
                "current_url": self._evidence_url(current_url),
                "reason": "Dell SSO redirect is a temporary authentication surface; final dual-MCP verification runs after SSO completes.",
            }
            safe_write_json(self.run_dir / "mcp_runtime" / "sso_navigation_transition.json", evidence)
            message = "SSO redirect accepted; final dual-MCP same-surface verification deferred until authenticated HIP return"
            if note:
                message = f"{message}; {note}"
            await self._finish_action(ev, True, error=message)
            return True
        return False

    def _phase_surface_contract(self, target_url: str) -> Dict[str, Any]:
        """Return the deterministic KB contract for a HIP module surface."""
        target_path = (urlparse(str(target_url or "")).path or "").strip("/").lower()
        expected_by_path = {
            "datamaps": ["data map", "map identifier", "mapping"],
            "doctypes": ["document type", "doctype", "document identifier"],
            "rules": ["rule", "condition", "action"],
            "transportprofiles": ["transport profile", "profile usage", "interface type"],
            "bizflows": ["biz flow", "configure source", "source details", "manage biz flow"],
        }
        for key, terms in expected_by_path.items():
            if key in target_path:
                return {
                    "module_key": key,
                    "expected_terms": terms,
                    "target_path": target_path,
                    "source": "validated HIP phase URL/surface KB",
                }
        return {
            "module_key": "generic",
            "expected_terms": [],
            "target_path": target_path,
            "source": "generic authenticated-route contract",
        }

    async def _navigation_page_is_usable(self, target_url: str) -> bool:
        """Return True only when the *requested* HIP module is actually usable.

        A previous implementation accepted a different logged-in HIP page because
        generic portal text was treated as proof of the requested target. That made
        Data Maps look usable for Document Types and caused the dual-MCP gate to fail
        before the agent retried navigation. The target path is now mandatory.
        """
        if not self.page:
            return False
        try:
            current_url = (self.page.url or "").strip()
        except Exception:
            current_url = ""
        if not self._same_target_path(target_url, current_url):
            return False

        body = ""
        try:
            body = (await self.page.locator("body").inner_text(timeout=3000)).strip().lower()
        except Exception:
            body = ""
        if not body or len(body) < 20:
            return False

        ready_ok = False
        try:
            ready_state = str(await self.page.evaluate("() => document.readyState"))
            ready_ok = ready_state in {"interactive", "complete"}
        except Exception:
            ready_ok = False

        login_words = ["sign in", "login", "single sign", "password", "authenticator", "verify your identity"]
        if any(w in body for w in login_words) and not any(p.lower() in body for p in self.config.portal.sso_positive_texts):
            return False

        contract = self._phase_surface_contract(target_url)
        terms = list(contract.get("expected_terms") or [])
        surface_ok = any(term in body for term in terms) if terms else True
        if not surface_ok and ready_ok:
            # V244: Dell Angular route text can lag behind route commit while the
            # actual module is already mounted and interactive. Accept structural
            # proof only on the exact requested path; downstream form/judge gates
            # still require exact field/control evidence before any phase can pass.
            try:
                structural = await self.page.evaluate(r'''() => {
                  const visible=el=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden');};
                  const roots=[...document.querySelectorAll('app-root,[ng-version],[class*=angular],main')].filter(visible).length;
                  const controls=[...document.querySelectorAll('input,select,textarea,button,[role=combobox],[role=button],dds-dropdown')].filter(visible).length;
                  return {roots,controls};
                }''')
                surface_ok = int((structural or {}).get("roots") or 0) > 0 and int((structural or {}).get("controls") or 0) >= 2
            except Exception:
                surface_ok = False
        return bool(ready_ok and surface_ok)

    @staticmethod
    def is_executor_transport_disconnect(message: str) -> bool:
        """Recognize MCP/CDP transport failures that are safe to *reattach*, not replay.

        This deliberately excludes generic portal timeouts.  Rebinding is reserved
        for explicit websocket/stdio/CDP transport failures so business-state errors
        are never hidden behind a reconnect attempt.
        """
        text = str(message or "").lower()
        markers = (
            "websocket handler exited unexpectedly",
            "websocket reconnect",
            "opening handshake",
            "connection closed",
            "closed while receiving",
            "mcp connection",
            "mcp transport",
            "stdio transport",
            "broken pipe",
            "connection reset by peer",
            "target page, context or browser has been closed",
            "cdp websocket",
            "devtools websocket",
            "failed to open new tab - no browser is open",
            "no browser is open",
            "browser not connected",
            "reconnection attempts failed",
            "connectionrefusederror",
            "remote computer refused the network connection",
        )
        return any(marker in text for marker in markers)

    async def _unique_authenticated_page_for_rebind(self, expected_url: str) -> Dict[str, Any]:
        """Select exactly one local tab for reconnect recovery; fail closed on twins."""
        if self.context is None:
            return {"pass": False, "code": "HIP_RECONNECT_NO_BROWSER_CONTEXT"}
        try:
            pages = [p for p in self.context.pages if not p.is_closed()]
        except Exception:
            pages = list(getattr(self.context, "pages", []) or [])
        rows: List[Dict[str, Any]] = []
        matches: List[Page] = []
        for page in pages:
            try:
                url = str(page.url or "")
            except Exception:
                url = ""
            match = self._surface_url_matches(expected_url, url)
            rows.append({"url": self._evidence_url(url), "target_match": match})
            if match:
                matches.append(page)
        fail_ambiguous = bool(getattr(self.config.mcp, "executor_rebind_fail_on_ambiguous_tabs", True))
        if len(matches) > 1 and fail_ambiguous:
            return {
                "pass": False, "code": "HIP_RECONNECT_AMBIGUOUS_TABS",
                "message": "More than one live browser tab matches the requested HIP route; reconnect will not guess.",
                "pages": rows,
            }
        if not matches:
            await self._adopt_best_page_for_url(expected_url)
            current = str(self.page.url if self.page else "")
            if not self._surface_url_matches(expected_url, current):
                return {
                    "pass": False, "code": "HIP_RECONNECT_TARGET_TAB_NOT_FOUND",
                    "message": "The authenticated browser is alive but no tab is on the phase route that was active before disconnect.",
                    "pages": rows, "current_url": self._evidence_url(current),
                }
            selected = self.page
        else:
            selected = matches[0]
            if selected is not self.page:
                self.page = selected
                await self._observe_new_page(selected)
        assert self.page is not None
        current_url = str(self.page.url or "")
        if self._is_sso_transition_url(current_url):
            return {
                "pass": False, "code": "HIP_RECONNECT_AUTH_SURFACE_ACTIVE",
                "message": "Reconnect found Dell SSO instead of the authenticated HIP phase; explicit re-authentication is required.",
                "current_url": self._evidence_url(current_url), "pages": rows,
            }
        try:
            logged_in = await self._looks_logged_in(self.page)
        except Exception:
            logged_in = False
        if self._authenticated_once and not logged_in:
            return {
                "pass": False, "code": "HIP_RECONNECT_AUTH_NOT_PROVEN",
                "message": "The previous session had authenticated, but the recovered tab no longer proves authenticated HIP state.",
                "current_url": self._evidence_url(current_url), "pages": rows,
            }
        return {
            "pass": True, "current_url": self._evidence_url(current_url),
            "pages": rows, "unique_target_match": True, "authenticated": bool(logged_in or not self._authenticated_once),
        }

    async def _restart_mcp_clients_on_same_cdp(self) -> Dict[str, Any]:
        """Restart MCP *clients only* against the locked browser's existing CDP port."""
        endpoint = str(self._cdp_endpoint or "")
        closed: Dict[str, Any] = {}
        for name, backend in (("playwright_mcp", self.playwright_mcp_backend), ("chrome_devtools_mcp", self.mcp_backend)):
            if backend is None:
                closed[name] = "not_attached"
                continue
            try:
                await asyncio.wait_for(backend.close(), timeout=5.0)
                closed[name] = "closed"
            except Exception as exc:
                closed[name] = f"close_warning:{mask_sensitive_string(str(exc))[:300]}"
        self.playwright_mcp_backend = None
        self.mcp_backend = None
        self._playwright_mcp_surface_healthy = False
        self.playwright_mcp_capabilities = {}
        self.mcp_capabilities = {}
        await self._start_required_mcp_backends()
        return {
            "pass": bool(self.playwright_mcp_backend is not None),
            "cdp_endpoint": endpoint, "closed": closed,
            "playwright_mcp_attached": bool(self.playwright_mcp_backend is not None),
            "chrome_devtools_mcp_attached": bool(self.mcp_backend is not None),
            "browser_restarted": False, "browser_switched": False,
        }

    async def recover_same_browser_executor_bindings(
        self, expected_url: str, *, phase: str = "", checkpoint_passed: bool = False
    ) -> Dict[str, Any]:
        """Reattach Playwright/DevTools MCP to the *same* authenticated browser.

        A transport disconnect is not a reason to relaunch Edge/Chrome or to repeat a
        completed form.  Recovery therefore proves the CDP endpoint, uniquely binds
        the local target tab, restarts the two MCP clients, and requires all three
        executors to agree on that route before returning ``pass=True``.
        """
        if not bool(getattr(self.config.mcp, "executor_rebind_enabled", True)):
            return {"pass": False, "code": "HIP_EXECUTOR_REBIND_DISABLED"}
        phase_key = str(phase or self._active_phase_name or "unknown")
        async with self._executor_rebind_lock:
            used = int(self._executor_rebind_phase_counts.get(phase_key, 0))
            max_attempts = max(1, int(getattr(self.config.mcp, "executor_rebind_max_attempts_per_phase", 2) or 2))
            if used >= max_attempts:
                result = {
                    "pass": False, "code": "HIP_EXECUTOR_REBIND_BUDGET_EXHAUSTED",
                    "phase": phase_key, "attempts_used": used, "max_attempts": max_attempts,
                    "browser_restarted": False, "browser_switched": False,
                }
                self._last_executor_rebind = result
                return result
            self._executor_rebind_phase_counts[phase_key] = used + 1
            self._executor_rebind_count += 1
            result: Dict[str, Any] = {
                "schema_version": "hip.same-browser-executor-rebind.v1",
                "phase": phase_key, "rebind_index": self._executor_rebind_count,
                "phase_rebind_attempt": used + 1,
                "expected_url": self._evidence_url(expected_url),
                "checkpoint_passed_before_disconnect": bool(checkpoint_passed),
                "browser_locked": bool(self._mission_browser_locked),
                "browser_restarted": False, "browser_switched": False,
                "selected_browser": mask_sensitive_data(dict(self._selected_browser or {})),
            }
            try:
                timeout = max(2.0, float(getattr(self.config.mcp, "executor_rebind_timeout_seconds", 12.0) or 12.0))
                health = await self._wait_for_cdp_ready(timeout_seconds=min(timeout, 8.0))
                result["cdp_health"] = health
                if not health.get("ok"):
                    raise RuntimeError("HIP_RECONNECT_CDP_ENDPOINT_UNHEALTHY: existing browser CDP endpoint did not recover")
                local = await self._unique_authenticated_page_for_rebind(expected_url)
                result["local_tab"] = local
                if not local.get("pass"):
                    raise RuntimeError(f"{local.get('code')}: {local.get('message') or 'tab recovery failed'}")
                result["mcp_restart"] = await asyncio.wait_for(self._restart_mcp_clients_on_same_cdp(), timeout=timeout)
                surface = await self._verify_dual_mcp_same_surface(
                    expected_url, timeout_seconds=min(timeout, 8.0), fail_closed=False, require_expected_target=True
                )
                result["surface_verification"] = mask_sensitive_data(surface)
                if not surface.get("pass"):
                    raise RuntimeError("HIP_RECONNECT_SURFACE_NOT_REPROVEN: MCP clients did not agree with Python Playwright on the recovered HIP tab")
                result.update({
                    "pass": True, "code": "HIP_EXECUTOR_REBIND_OK",
                    "resume_policy": "continue_read_only_verification_without_phase_replay" if checkpoint_passed else "resume_same_phase_from_deterministic_state",
                })
                trace = getattr(self, "mission_trace", None)
                if trace is not None and phase_key in getattr(trace, "phases", []):
                    try:
                        trace.record_observation(
                            phase_key,
                            summary="Browser transport recovered; AutoWebGLM/Playwright MCP rebound to the same authenticated HIP tab",
                            source="same_browser_executor_rebind",
                            details={
                                "playwright_mcp": True, "browser_switched": False,
                                "checkpoint_passed": bool(checkpoint_passed),
                                "current_url": local.get("current_url") or "",
                            },
                        )
                    except Exception:
                        pass
            except Exception as exc:
                result.update({
                    "pass": False,
                    "code": str(exc).split(":", 1)[0] if str(exc).startswith("HIP_") else "HIP_EXECUTOR_REBIND_FAILED",
                    "error": mask_sensitive_string(str(exc))[:1600],
                    "resume_policy": "fail_closed_no_browser_switch",
                })
                trace = getattr(self, "mission_trace", None)
                if trace is not None and phase_key in getattr(trace, "phases", []):
                    try:
                        trace.record_warning(phase_key, f"Executor reconnect blocked: {result['code']}")
                    except Exception:
                        pass
            self._last_executor_rebind = mask_sensitive_data(result)
            try:
                safe_write_json(self.run_dir / "mcp_runtime" / "same_browser_executor_rebind.json", self._last_executor_rebind)
            except Exception:
                pass
            return self._last_executor_rebind

    async def _verify_dual_mcp_same_surface(
        self,
        expected_url: str,
        *,
        timeout_seconds: Optional[float] = None,
        fail_closed: Optional[bool] = None,
        require_expected_target: bool = True,
    ) -> Dict[str, Any]:
        """Verify executor agreement and, optionally, requested-target commitment.

        The old gate conflated two different questions:
        1. Do Python Playwright and both MCPs see the same actual tab?
        2. Is that actual tab the requested HIP module?

        When every executor still saw Data Maps while Document Types was requested,
        the runtime called it an MCP disagreement and aborted. The ReAct controller
        now treats that as a route-not-committed observation and replans navigation.
        """
        await self._adopt_best_page_for_url(expected_url)
        timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else getattr(self.config.mcp, "dual_mcp_same_surface_timeout_seconds", 30.0)
        )
        poll = max(0.1, float(getattr(self.config.mcp, "dual_mcp_same_surface_poll_seconds", 1.0)))
        strict_mcp_runtime = bool(getattr(self.config.mcp, "strict_runtime_required", True))
        requested_fail_closed = (
            bool(getattr(self.config.mcp, "require_dual_mcp_same_surface", True))
            if fail_closed is None
            else bool(fail_closed)
        )
        # In adaptive mode an optional MCP witness is never allowed to strand a
        # healthy Python Playwright/PyAutoGUI mission. A mismatched Playwright MCP
        # is quarantined from form dispatch until it proves the same tab again.
        should_fail = bool(requested_fail_closed and strict_mcp_runtime)
        deadline = asyncio.get_event_loop().time() + max(0.0, timeout)
        attempts: List[Dict[str, Any]] = []
        final: Dict[str, Any] = {}

        while True:
            await self._adopt_best_page_for_url(expected_url)
            local_url = str(self.page.url if self.page else "")
            target_match = self._surface_url_matches(expected_url, local_url)
            executor_agreement = True
            attempt: Dict[str, Any] = {
                "expected_url": self._evidence_url(expected_url),
                "python_playwright_url": self._evidence_url(local_url),
                "python_target_match": target_match,
                "target_match": target_match,
                "require_expected_target": bool(require_expected_target),
            }

            if self.mcp_backend is not None:
                try:
                    selected = await self.mcp_backend.select_page_for_url(local_url)
                    attempt["chrome_devtools_mcp"] = mask_sensitive_data(selected)
                    selected_url = str(((selected.get("selected") or {}).get("url") if isinstance(selected, dict) else "") or "")
                    chrome_matches_actual = bool(selected.get("pass")) and (
                        not selected_url or self._surface_url_matches(local_url, selected_url)
                    )
                    attempt["chrome_matches_actual"] = chrome_matches_actual
                    if not chrome_matches_actual:
                        executor_agreement = False
                except Exception as exc:
                    attempt["chrome_devtools_mcp"] = {"pass": False, "error": mask_sensitive_string(str(exc))}
                    attempt["chrome_matches_actual"] = False
                    executor_agreement = False

            if self.playwright_mcp_backend is not None:
                try:
                    mcp_url = await self.playwright_mcp_backend.get_current_url()
                    matched = self._surface_url_matches(local_url, mcp_url)
                    attempt["playwright_mcp"] = {
                        "pass": matched,
                        "current_url": self._evidence_url(mcp_url),
                    }
                    attempt["playwright_mcp_matches_actual"] = matched
                    if not matched:
                        executor_agreement = False
                except Exception as exc:
                    attempt["playwright_mcp"] = {"pass": False, "error": mask_sensitive_string(str(exc))}
                    attempt["playwright_mcp_matches_actual"] = False
                    executor_agreement = False

            attempt["raw_mcp_witness_agreement"] = executor_agreement
            local_surface_ok = bool(target_match if require_expected_target else local_url)
            attempt["same_actual_surface"] = bool(executor_agreement if strict_mcp_runtime else local_surface_ok)
            attempt["pass"] = bool(
                executor_agreement and local_surface_ok
                if strict_mcp_runtime
                else local_surface_ok
            )
            attempt["runtime_mode"] = "strict_mcp" if strict_mcp_runtime else "adaptive_hybrid"
            attempt["degraded_optional_witness"] = bool(not strict_mcp_runtime and not executor_agreement)
            attempts.append(attempt)
            final = dict(attempt)
            if attempt.get("pass"):
                break
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(poll)

        # Playwright MCP is allowed to perform form actions only when this exact
        # authenticated Python-Playwright surface has been independently matched.
        self._playwright_mcp_surface_healthy = bool(
            self.playwright_mcp_backend is not None
            and final.get("playwright_mcp_matches_actual") is True
        )
        final["playwright_mcp_dispatch_eligible"] = self._playwright_mcp_surface_healthy
        final["attempt_count"] = len(attempts)
        final["attempts"] = attempts
        safe_write_json(self.run_dir / "mcp_runtime" / "dual_mcp_same_surface.json", mask_sensitive_data(final))
        if should_fail and not final.get("pass"):
            concise = {
                "expected_url": final.get("expected_url"),
                "python_playwright_url": final.get("python_playwright_url"),
                "target_match": final.get("target_match"),
                "same_actual_surface": final.get("same_actual_surface"),
                "attempt_count": final.get("attempt_count"),
                "chrome_devtools_mcp": final.get("chrome_devtools_mcp"),
                "playwright_mcp": final.get("playwright_mcp"),
            }
            code = "HIP_MCP_SURFACE_DRIFT" if not final.get("same_actual_surface") else "HIP_ROUTE_NOT_COMMITTED"
            raise RuntimeError(f"{code}: dual-MCP target gate failed after settling: {concise}")
        return final

    async def _observe_react_navigation_state(self, target_url: str) -> Dict[str, Any]:
        """Collect one bounded observation for the navigation ReAct loop."""
        page = await self._ensure_active_page(target_url)
        current_url = str(page.url or "")
        sso_transition = self._is_sso_transition_url(current_url)
        logged_in = False if sso_transition else await self._looks_logged_in(page)
        target_match = self._surface_url_matches(target_url, current_url)
        target_usable = await self._navigation_page_is_usable(target_url) if target_match else False
        use_mcp_during_sso = bool(getattr(self.config.mcp, "use_browser_mcp_for_sso", False))
        if (sso_transition or not logged_in) and not use_mcp_during_sso:
            # Dell SSO is owned by the primary persistent Playwright Edge context.
            # Auxiliary CDP executors are deliberately not part of the login gate;
            # they are re-verified only after the authenticated HIP surface returns.
            dual = {
                "pass": True, "same_actual_surface": True,
                "python_playwright_url": self._evidence_url(current_url),
                "deferred_for_sso": True,
            }
        else:
            dual = await self._verify_dual_mcp_same_surface(
                target_url,
                timeout_seconds=0.0,
                fail_closed=False,
                require_expected_target=False,
            )
        return {
            "target_url": self._evidence_url(target_url),
            "current_url": self._evidence_url(current_url),
            "target_match": target_match,
            "target_usable": target_usable,
            "logged_in": logged_in,
            "sso_transition": sso_transition,
            "same_actual_surface": bool(dual.get("same_actual_surface", dual.get("pass", True))),
            "dual_mcp": mask_sensitive_data({
                "python_playwright_url": dual.get("python_playwright_url"),
                "chrome_devtools_mcp": dual.get("chrome_devtools_mcp"),
                "playwright_mcp": dual.get("playwright_mcp"),
            }),
            "kb_contract": self._phase_surface_contract(target_url),
        }

    def _plan_react_navigation_action(self, observation: Dict[str, Any], step: int) -> Dict[str, Any]:
        """Return a concise, auditable plan without exposing private reasoning."""
        if observation.get("sso_transition"):
            return {
                "action": "await_sso",
                "basis": "live browser is on a Dell/corporate authentication surface",
                "expected_effect": "authenticated HIP context becomes available",
            }
        if not observation.get("logged_in"):
            return {
                "action": "navigate_target",
                "basis": "no authenticated HIP surface is active; opening the requested route should initiate SSO",
                "expected_effect": "requested HIP route or Dell SSO surface opens in the same context",
            }
        if observation.get("target_match") and observation.get("target_usable") and not observation.get("same_actual_surface"):
            return {
                "action": "resync_mcp",
                "basis": "target page is correct but browser executors disagree",
                "expected_effect": "both MCPs attach to the Python Playwright tab",
            }
        if not observation.get("target_match"):
            action = "navigate_target" if step == 0 else "cleanup_and_navigate" if step == 1 else "force_route_commit"
            return {
                "action": action,
                "basis": "validated phase URL/surface KB says the current module is not the requested module",
                "expected_effect": "requested HIP route commits in the same authenticated context",
            }
        return {
            "action": "wait_and_reobserve",
            "basis": "target URL committed but Angular surface/observers are not ready",
            "expected_effect": "target controls and event observers become usable",
        }

    async def _execute_react_navigation_action(self, action: Dict[str, Any], target_url: str) -> Dict[str, Any]:
        name = str(action.get("action") or "")
        result: Dict[str, Any] = {"action": name, "success": False}
        try:
            if name == "await_sso":
                result.update({"success": True, "status": "sso_required"})
                return result
            if name == "resync_mcp":
                await self._consolidate_session_pages(target_url)
                await self._verify_dual_mcp_same_surface(
                    target_url,
                    timeout_seconds=float(getattr(self.config.mcp, "dual_mcp_same_surface_timeout_seconds", 30.0)),
                    require_expected_target=True,
                )
                result["success"] = True
                return result
            if name == "cleanup_and_navigate":
                await self._dismiss_transient_ui(next_phase=self._active_phase_name)
                await self._consolidate_session_pages(target_url)
                await self.navigate(target_url)
                result["success"] = True
                return result
            if name == "force_route_commit":
                page = await self._ensure_active_page(target_url)
                await page.evaluate("url => window.location.assign(url)", target_url)
                state = await self._wait_for_navigation_acceptance(target_url, timeout_seconds=35.0)
                result.update({"success": state == "target", "navigation_state": state})
                return result
            if name == "wait_and_reobserve":
                await self.wait_for_blocking_overlays_gone(timeout_ms=5000)
                await self._ensure_page_observers()
                await asyncio.sleep(0.5)
                result["success"] = True
                return result
            await self.navigate(target_url)
            result["success"] = True
            return result
        except Exception as exc:
            result["error"] = mask_sensitive_string(str(exc))
            return result

    async def _react_ensure_target_surface(self, target_url: str, *, max_steps: int = 4) -> Dict[str, Any]:
        """Plan → Act → Observe → Judge until the requested HIP route is ready."""
        trace: Dict[str, Any] = {
            "schema_version": "hip.navigation-react.v1",
            "phase": self._active_phase_name,
            "target_url": self._evidence_url(target_url),
            "knowledge_sources": [
                "current input phase URL",
                "validated HIP module surface contract",
                "persistent browser session state",
                "Playwright MCP and Chrome DevTools MCP observations",
            ],
            "steps": [],
            "pass": False,
        }
        trace_path = self.run_dir / "mcp_runtime" / "navigation_react_trace.json"
        for step in range(max(1, int(max_steps))):
            observation = await self._observe_react_navigation_state(target_url)
            judge = {
                "target_committed": bool(observation.get("target_match")),
                "target_usable": bool(observation.get("target_usable")),
                "executors_agree": bool(observation.get("same_actual_surface")),
            }
            judge["pass"] = all(judge.values())
            row: Dict[str, Any] = {
                "step": step + 1,
                "observation": mask_sensitive_data(observation),
                "judge": judge,
            }
            if judge["pass"]:
                row["plan"] = {"action": "accept_target", "basis": "all deterministic and dual-MCP gates passed"}
                trace["steps"].append(row)
                trace["pass"] = True
                trace["final_url"] = observation.get("current_url")
                safe_write_json(trace_path, trace)
                return trace
            plan = self._plan_react_navigation_action(observation, step)
            row["plan"] = plan
            action_result = await self._execute_react_navigation_action(plan, target_url)
            row["action_result"] = mask_sensitive_data(action_result)
            trace["steps"].append(row)
            safe_write_json(trace_path, trace)
            if action_result.get("status") == "sso_required":
                trace["status"] = "sso_required"
                safe_write_json(trace_path, trace)
                return trace

        final_observation = await self._observe_react_navigation_state(target_url)
        trace["final_observation"] = mask_sensitive_data(final_observation)
        trace["error_code"] = (
            "HIP_MCP_SURFACE_DRIFT"
            if final_observation.get("target_match") and not final_observation.get("same_actual_surface")
            else "HIP_ROUTE_NOT_COMMITTED"
        )
        safe_write_json(trace_path, trace)
        raise RuntimeError(
            f"{trace['error_code']}: ReAct navigation controller could not reach {self._evidence_url(target_url)}; "
            f"final observation={mask_sensitive_data(final_observation)}"
        )

    def _same_target_path(self, target_url: str, current_url: str) -> bool:
        try:
            target = urlparse(str(target_url or ""))
            current = urlparse(str(current_url or ""))
            target_path = (target.path or "/").rstrip("/").lower() or "/"
            current_path = (current.path or "/").rstrip("/").lower() or "/"
            return bool(
                (target.hostname or "").lower() == (current.hostname or "").lower()
                and target_path == current_path
            )
        except Exception:
            return False

    async def _wait_for_navigation_acceptance(self, target_url: str, *, timeout_seconds: float = 30.0) -> str:
        """Let Dell/Angular finish a route before issuing a second navigation."""
        deadline = asyncio.get_event_loop().time() + max(1.0, float(timeout_seconds))
        last_state = ""
        while asyncio.get_event_loop().time() < deadline:
            await self._ensure_active_page(target_url)
            last_state = await self._accepted_navigation_state(target_url)
            if last_state:
                return last_state
            try:
                current = str(self.page.url if self.page else "")
                # When the URL already committed to the requested module, wait for
                # the Angular surface instead of aborting its remote-entry chunks
                # with another goto.
                if self._same_target_path(target_url, current):
                    await self.wait_for_blocking_overlays_gone(timeout_ms=1500)
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return last_state

    async def _consolidate_session_pages(self, expected_url: str = "") -> Dict[str, Any]:
        """Keep one authoritative portal tab and close only blank/stale SSO tabs."""
        await self._ensure_active_page(expected_url)
        audit: Dict[str, Any] = {"kept_url": self._evidence_url(str(self.page.url if self.page else "")), "closed": []}
        if self.context is None:
            return audit
        for page in list(self.context.pages):
            if page is self.page:
                continue
            try:
                url = str(page.url or "")
                closeable = url == "about:blank" or (
                    self._authenticated_once and self._is_sso_transition_url(url)
                )
                if closeable:
                    await page.close()
                    audit["closed"].append(self._evidence_url(url))
            except Exception:
                continue
        try:
            safe_write_json(self.run_dir / "mcp_runtime" / "session_page_consolidation.json", audit)
        except Exception:
            pass
        return audit

    async def capture_phase_progress_marker(self, phase: str = "") -> Dict[str, Any]:
        """Return a value-free structural fingerprint for active no-progress detection.

        The marker intentionally stores only a SHA-256 signature plus counters.  The
        DOM basis includes control *state* (filled/not-filled, checked, expanded,
        selected, disabled) but never the underlying user-entered values.
        """
        page = await self._ensure_active_page(self._active_target_url)
        basis: Dict[str, Any] = {}
        try:
            basis = await page.evaluate(r"""
() => {
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const norm = (v) => String(v || '').replace(/\s+/g, ' ').trim().toLowerCase().slice(0, 120);
  const selector = [
    'input','textarea','select','button','a[href]','[role="button"]','[role="menuitem"]',
    '[role="option"]','[role="combobox"]','[role="checkbox"]','[role="radio"]',
    '[aria-expanded]','dds-button','dds-input','dds-select','dds-dropdown'
  ].join(',');
  const nodes = Array.from(document.querySelectorAll(selector)).filter(visible).slice(0, 320);
  const controls = nodes.map((el) => {
    const tag = (el.tagName || '').toLowerCase();
    const type = norm(el.getAttribute && el.getAttribute('type'));
    const label = norm((el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('title'))) || el.innerText || '');
    const role = norm(el.getAttribute && el.getAttribute('role'));
    let hasValue = false;
    let selectedCount = 0;
    try {
      if ('value' in el) hasValue = String(el.value || '').length > 0;
      if (tag === 'select' && el.selectedOptions) selectedCount = el.selectedOptions.length;
    } catch (_) {}
    return [
      tag, role, type, label,
      el.disabled === true || el.getAttribute?.('aria-disabled') === 'true' ? 1 : 0,
      el.checked === true || el.getAttribute?.('aria-checked') === 'true' ? 1 : 0,
      norm(el.getAttribute && el.getAttribute('aria-expanded')),
      norm(el.getAttribute && el.getAttribute('aria-selected')),
      hasValue ? 1 : 0,
      selectedCount
    ];
  });
  const surfaces = Array.from(document.querySelectorAll('[role="dialog"],[role="menu"],[role="listbox"],dds-drawer,.dds__drawer,.cdk-overlay-pane'))
    .filter(visible).slice(0, 40).map(el => [
      (el.tagName || '').toLowerCase(),
      norm(el.getAttribute && el.getAttribute('role')),
      norm(el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('id'))),
      norm(el.getAttribute && el.getAttribute('aria-modal'))
    ]);
  return {
    route: String(location.pathname || '') + String(location.search || ''),
    ready: document.readyState,
    controls,
    surfaces,
    active: document.activeElement ? [
      (document.activeElement.tagName || '').toLowerCase(),
      norm(document.activeElement.getAttribute && document.activeElement.getAttribute('role')),
      norm(document.activeElement.getAttribute && (document.activeElement.getAttribute('aria-label') || document.activeElement.getAttribute('name')))
    ] : []
  };
}
""")
        except Exception as exc:
            basis = {
                "route": str(getattr(page, "url", "") or ""),
                "observation_error": mask_sensitive_string(str(exc))[:300],
            }
        raw = json.dumps(basis, sort_keys=True, ensure_ascii=False, default=str)
        signature = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
        successful_fills = sum(1 for ev in self.action_events if getattr(ev, "type", "") == "fill" and bool(getattr(ev, "success", False)))
        successful_clicks = sum(1 for ev in self.action_events if getattr(ev, "type", "") == "click" and bool(getattr(ev, "success", False)))
        route = ""
        try:
            route = urlparse(str(page.url or "")).path
        except Exception:
            route = str((basis or {}).get("route") or "")
        return {
            "schema_version": "hip.phase-progress-marker.v1",
            "phase": str(phase or self._active_phase_name or ""),
            "signature": signature,
            "route": route,
            "action_count": len(self.action_events),
            "successful_fill_count": successful_fills,
            "successful_click_count": successful_clicks,
            "dom_transition_count": len(self.dom_transition_records),
            # Form-executor heartbeat (field/retry token, no values).
            "executor_progress": str(((getattr(page, "_hip_executor_progress", None) or {}).get("token")) or ""),
            "values_stored": False,
        }

    async def handoff_to_next_phase(
        self,
        *,
        from_phase: str,
        to_phase: str,
        to_url: str,
        exact_checkpoint_passed: bool,
        allow_from_blocked: bool = False,
    ) -> Dict[str, Any]:
        """Actively leave one phase and prove the next HIP module before handoff.

        AutoWebGLM remains the planning authority for phase work.  The transition
        itself is deterministic orchestration: close the prior no-save surface,
        navigate through Playwright MCP first, and require Python Playwright plus
        both MCP observers to agree on the next authenticated route.
        """
        result: Dict[str, Any] = {
            "schema_version": "hip.phase-handoff.v1",
            "from_phase": str(from_phase),
            "to_phase": str(to_phase),
            "to_url": self._evidence_url(str(to_url or "")),
            "exact_checkpoint_passed": bool(exact_checkpoint_passed),
            "allow_from_blocked": bool(allow_from_blocked),
            "planner": "AutoWebGLM",
            "primary_navigation_executor": "Playwright MCP",
            "browser_switched": False,
            "pass": False,
        }
        trace = getattr(self, "mission_trace", None)
        previous_trace_override = self._trace_phase_override
        try:
            if not exact_checkpoint_passed and not allow_from_blocked:
                raise RuntimeError("HIP_PHASE_HANDOFF_SOURCE_NOT_VERIFIED: source phase has no exact completion proof")
            if trace is not None:
                try:
                    trace.record_transition(from_phase, to_phase, status="starting", details={
                        "exact_checkpoint_passed": bool(exact_checkpoint_passed),
                        "browser_switched": False,
                    })
                except Exception:
                    pass
            result["cleanup"] = await self._dismiss_transient_ui(next_phase=to_phase)
            # Cleanup above belongs to the source phase.  From the first route
            # navigation onward, human-readable trace events belong to the
            # destination phase even though the borrowed evidence directory is
            # not rebound until prepare_borrowed_phase().
            self._trace_phase_override = str(to_phase or "")
            await self.navigate(to_url)
            if not await self._navigation_page_is_usable(to_url):
                # navigate() may legitimately stop on Dell SSO. Handoff is stricter:
                # it returns only after the requested authenticated module is usable.
                await self.goto_base_and_complete_sso(to_url)
            if not await self._navigation_page_is_usable(to_url):
                raise RuntimeError("HIP_PHASE_HANDOFF_TARGET_NOT_USABLE: requested next HIP module did not become usable")
            surface = await self._verify_dual_mcp_same_surface(
                to_url,
                timeout_seconds=min(12.0, max(4.0, float(getattr(self.config.mcp, "dual_mcp_same_surface_timeout_seconds", 12.0) or 12.0))),
                fail_closed=True,
                require_expected_target=True,
            )
            result["surface_verification"] = mask_sensitive_data(surface)
            if not surface.get("pass"):
                raise RuntimeError("HIP_PHASE_HANDOFF_MCP_SURFACE_MISMATCH: next route was not independently proven by both MCPs")
            result.update({
                "pass": True,
                "code": "HIP_PHASE_HANDOFF_OK",
                "current_url": self._evidence_url(str(self.page.url if self.page else "")),
                "next_policy": "begin_next_phase_without_reopening_previous_phase",
            })
            if trace is not None:
                try:
                    transition_status = "complete" if exact_checkpoint_passed else "continued_from_blocked"
                    trace.record_transition(from_phase, to_phase, status=transition_status, details={
                        "code": result["code"],
                        "source_phase_exact_checkpoint_passed": bool(exact_checkpoint_passed),
                        "continued_only_to_collect_remaining_phase_evidence": bool(not exact_checkpoint_passed),
                        "primary_navigation_executor": "Playwright MCP",
                        "browser_switched": False,
                    })
                    trace.record_observation(
                        to_phase,
                        summary=(
                            f"Verified route handoff from {from_phase} to {to_phase}"
                            if exact_checkpoint_passed
                            else f"Route continued from blocked {from_phase}; source phase remains incomplete"
                        ),
                        source="mission_transition",
                        details={
                            "current_url": result.get("current_url") or "",
                            "source_phase_exact_checkpoint_passed": bool(exact_checkpoint_passed),
                            "dual_mcp_surface_pass": bool((result.get("surface_verification") or {}).get("pass")),
                            "browser_switched": False,
                        },
                    )
                except Exception:
                    pass
        except Exception as exc:
            result.update({
                "pass": False,
                "code": str(exc).split(":", 1)[0] if str(exc).startswith("HIP_") else "HIP_PHASE_HANDOFF_FAILED",
                "error": mask_sensitive_string(str(exc))[:1600],
                "next_policy": "next phase may perform its own bounded route recovery; previous phase is never replayed solely for handoff failure",
            })
            if trace is not None:
                try:
                    trace.record_transition(from_phase, to_phase, status="blocked", details={"code": result["code"]})
                except Exception:
                    pass
        finally:
            self._trace_phase_override = previous_trace_override
        try:
            safe_write_json(self.run_dir / "phase_handoff.json", result)
        except Exception:
            pass
        return result

    async def navigate(self, url: str) -> None:
        await self._ensure_active_page(url)
        if not self.page:
            raise RuntimeError("Browser session not started")
        self._active_target_url = str(url or "")
        ev = await self._begin_action("navigate", url)
        errors: List[str] = []
        timeout_ms = int(getattr(self.config.portal, "timeout_ms", 45000) or 45000)
        try:
            current_url = str(getattr(self.page, "url", "") or "")
            if self._same_target_path(url, current_url):
                state = await self._wait_for_navigation_acceptance(
                    url,
                    timeout_seconds=min(30.0, max(8.0, timeout_ms / 1000.0)),
                )
                if state:
                    await self._finish_accepted_navigation(ev, url, note="navigation skipped; requested HIP module already active and usable")
                    return

            # A previous no-save form or open DDS overlay must not leak into the
            # next module or trigger an unsaved-change route guard.
            await self._dismiss_transient_ui(next_phase=self._active_phase_name)

            # Playwright MCP is the primary navigation executor. A redirect to the
            # Dell SSO surface is accepted as a temporary state; it is not subjected
            # to the final HIP same-surface gate until authentication returns.
            if self.playwright_mcp_backend is not None and getattr(self.config.mcp, "playwright_mcp_primary_for_safe_actions", True):
                try:
                    await self.playwright_mcp_backend.navigate(url)
                    state = await self._wait_for_navigation_acceptance(
                        url,
                        timeout_seconds=min(35.0, max(10.0, timeout_ms / 1000.0)),
                    )
                    if state and await self._finish_accepted_navigation(ev, url, note="Playwright MCP navigation"):
                        return
                    errors.append("Playwright MCP navigate returned but neither target HIP nor Dell SSO was usable")
                except Exception as exc:
                    errors.append(f"Playwright MCP navigate failed: {exc}")
                    state = await self._wait_for_navigation_acceptance(url, timeout_seconds=15.0)
                    if state and await self._finish_accepted_navigation(ev, url, note="MCP navigation raised after browser redirect"):
                        return

            # Do not issue a second goto after the first executor has already
            # committed the exact requested URL. Repeated same-URL navigation was
            # the source of the old 4-5 minute timeout/ERR_ABORTED cascade.
            current_url = str(self.page.url if self.page else "")
            if self._same_target_path(url, current_url):
                state = await self._wait_for_navigation_acceptance(url, timeout_seconds=30.0)
                if state and await self._finish_accepted_navigation(ev, url, note="target URL committed; duplicate fallback navigation suppressed"):
                    return
                errors.append("target URL committed but Angular target surface did not become usable")
                raise TimeoutError("Navigation reached the requested URL but the target Angular surface did not become usable; duplicate goto suppressed")

            # Direct Playwright is the bounded fallback for corporate SSO/network
            # edge cases. ERR_ABORTED is common when Dell replaces the document with
            # an SSO redirect, so inspect the browser state before retrying.
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if await self._finish_accepted_navigation(ev, url, note="domcontentloaded navigation"):
                    return
                errors.append("domcontentloaded goto returned a non-target, non-SSO surface")
            except Exception as exc:
                errors.append(f"domcontentloaded goto failed: {exc}")
                if await self._finish_accepted_navigation(ev, url, note="domcontentloaded exception tolerated because target page is usable after redirect"):
                    return

            # Retry using the lighter 'commit' lifecycle. This avoids aborting when
            # Dell/SSO leaves subresources pending but the document itself committed.
            try:
                await self.page.goto(url, wait_until="commit", timeout=max(15000, min(timeout_ms, 30000)))
                if await self._finish_accepted_navigation(ev, url, note="commit navigation"):
                    return
                errors.append("commit goto returned a non-target, non-SSO surface")
            except Exception as exc:
                errors.append(f"commit goto failed: {exc}")
                if await self._finish_accepted_navigation(ev, url, note="commit exception tolerated because target page is usable after redirect"):
                    return

            # Last browser-side fallback. It is useful when Playwright's goto waits
            # on a request that never resolves, but location.assign succeeds.
            try:
                await self.page.evaluate("url => { window.location.assign(url); }", url)
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=max(15000, min(timeout_ms, 30000)))
                except Exception as exc:
                    errors.append(f"location.assign load wait failed: {exc}")
                if await self._finish_accepted_navigation(ev, url, note="location.assign fallback"):
                    return
            except Exception as exc:
                errors.append(f"location.assign failed: {exc}")
                if await self._finish_accepted_navigation(ev, url, note="location.assign exception tolerated because target page is usable after redirect"):
                    return

            raise TimeoutError("Navigation failed after retries: " + " | ".join(errors[-5:]))
        except Exception as exc:
            await self._finish_action(ev, False, str(exc), screenshot_after=True)
            raise

    async def goto_base_and_complete_sso(self, target_url: Optional[str] = None) -> None:
        """Reuse one authenticated session and reach the requested HIP module.

        Navigation is controlled by a bounded ReAct loop: plan from the validated
        module KB, act through the current browser/MCP stack, observe all executors,
        and judge exact target commitment before the phase starts. SSO is requested
        only when the live observation is genuinely unauthenticated.
        """
        target_url = str(target_url or self.config.portal.base_url)
        self._active_target_url = target_url
        # A module navigation invalidates any detached overlay ancestry from the
        # previous page. The next semantic surface must establish fresh provenance.
        self._semantic_surface_chain = []
        page = await self._ensure_active_page(target_url)
        await self._consolidate_session_pages(target_url)

        # First try to route within the current authenticated context. This covers
        # both a session authenticated earlier in this run and a Chrome profile that
        # was already logged in before the run started.
        was_already_authenticated = bool(self._authenticated_once)
        initial = await self._react_ensure_target_surface(target_url)
        if initial.get("pass"):
            self._authenticated_once = True
            self._write_session_manifest(status="authenticated_reused" if was_already_authenticated else "authenticated")
            return

        if initial.get("status") != "sso_required":
            raise RuntimeError(
                f"HIP_ROUTE_NOT_COMMITTED: unexpected ReAct navigation result for {self._evidence_url(target_url)}: {initial}"
            )

        if self._sso_wait_in_progress:
            raise RuntimeError("Dell SSO wait is already active for this persistent browser session")

        self._sso_wait_in_progress = True
        was_authenticated = self._authenticated_once
        self._sso_prompt_count += 1
        if was_authenticated:
            self._reauth_count += 1
        self._write_session_manifest(status="awaiting_sso_reauth" if was_authenticated else "awaiting_sso")
        print("\nSSO/login required. Complete Dell SSO in the opened browser window.")
        print("The same Microsoft Edge session will be reused for every remaining HIP phase. Do not close the browser.\n")
        deadline = asyncio.get_event_loop().time() + self.config.portal.sso_timeout_seconds
        try:
            while asyncio.get_event_loop().time() < deadline:
                await self._adopt_best_page_for_url(target_url)
                await self.collect_dom_click_log()
                page = await self._ensure_active_page(target_url)
                if await self._looks_logged_in(page):
                    remaining = max(1.0, deadline - asyncio.get_event_loop().time())
                    try:
                        result = await self._react_ensure_target_surface(target_url, max_steps=4)
                    except RuntimeError as exc:
                        # A newly authenticated Angular shell may still be settling.
                        # Keep the same context and re-observe until the SSO deadline.
                        safe_write_json(
                            self.run_dir / "mcp_runtime" / "sso_post_login_react_warning.json",
                            {"error": mask_sensitive_string(str(exc)), "remaining_seconds": remaining},
                        )
                        await asyncio.sleep(1.0)
                        continue
                    if result.get("pass"):
                        self._authenticated_once = True
                        await self._consolidate_session_pages(target_url)
                        self._write_session_manifest(status="reauthenticated" if was_authenticated else "authenticated")
                        safe_write_json(
                            self.run_dir / "mcp_runtime" / "sso_completion_gate.json",
                            {
                                "status": "ok",
                                "target_url": self._evidence_url(target_url),
                                "final_url": self._evidence_url(str(self.page.url if self.page else "")),
                                "react_navigation_pass": True,
                                "prompt_index": self._sso_prompt_count,
                                "reauthentication": was_authenticated,
                            },
                        )
                        return
                await asyncio.sleep(2)
        finally:
            self._sso_wait_in_progress = False
        raise TimeoutError("SSO did not complete within configured timeout. The persistent browser was left open for evidence capture.")

    async def _looks_logged_in(self, page: Page) -> bool:
        url = page.url.lower()
        if any(k.lower() in url for k in self.config.portal.sso_login_url_keywords):
            return False
        if any(p.lower() in url for p in self.config.portal.sso_success_url_patterns):
            return True
        try:
            body = (await page.locator("body").inner_text(timeout=3000)).lower()
        except Exception:
            return False
        negative = ["sign in", "login", "single sign", "password", "authenticator"]
        if any(n in body for n in negative) and not any(p in body for p in self.config.portal.sso_positive_texts):
            return False
        return any(p in body for p in self.config.portal.sso_positive_texts)

    def _loading_watchdog_key(self) -> str:
        current_url = str(self.page.url if self.page else self._active_target_url or "")
        path = (urlparse(current_url).path or "").strip("/").lower()
        return f"{self._active_phase_name or 'standalone'}::{path or 'unknown'}"

    async def _current_loading_state(self, target_selector: str = "") -> Dict[str, Any]:
        """Classify only *blocking* portal loading as active.

        Dell pages often leave passive ``aria-busy``, progressbar, or spinner
        nodes in the DOM after the form is already usable. Earlier versions
        treated any such node as a global loading state and waited even while a
        human could interact with the page. This classifier uses geometry,
        pointer interception, active-surface coverage, and target hit-testing.
        """
        page = await self._ensure_active_page(self._active_target_url)
        min_viewport_ratio = float(
            getattr(self.config.portal, "loading_watchdog_min_viewport_ratio", 0.08) or 0.08
        )
        min_surface_ratio = float(
            getattr(self.config.portal, "loading_watchdog_min_surface_cover_ratio", 0.45) or 0.45
        )
        try:
            state = await page.evaluate(
                r"""
({targetSelector, minViewportRatio, minSurfaceRatio}) => {
  const selectors = [
    'app-loadingindicator',
    'app-loadingindicator .dds__loading-indicator__overlay',
    '.dds__loading-indicator__overlay',
    '.dds__loading-indicator',
    '[class*="loading-indicator"]',
    '[class*="spinner"]',
    '[role="progressbar"]',
    '[aria-busy="true"]'
  ];
  const viewport = {
    width: Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1),
    height: Math.max(1, window.innerHeight || document.documentElement.clientHeight || 1)
  };
  const viewportArea = viewport.width * viewport.height;
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const style=getComputedStyle(el); const rect=el.getBoundingClientRect();
    const ariaHidden=(el.getAttribute('aria-hidden')||'').toLowerCase();
    return rect.width>0 && rect.height>0 && style.display!=='none' &&
      style.visibility!=='hidden' && Number(style.opacity||'1')!==0 && ariaHidden!=='true';
  };
  const disabled = (el) => !!(el && (el.disabled || el.getAttribute('disabled')!==null ||
    (el.getAttribute('aria-disabled')||'').toLowerCase()==='true'));
  const intersectArea = (a,b) => {
    const x1=Math.max(a.left,b.left), y1=Math.max(a.top,b.top);
    const x2=Math.min(a.right,b.right), y2=Math.min(a.bottom,b.bottom);
    return Math.max(0,x2-x1)*Math.max(0,y2-y1);
  };
  const safeQuery = (selector) => {
    if (!selector) return null;
    try { return document.querySelector(selector); } catch (_) { return null; }
  };
  const target=safeQuery(targetSelector);
  let targetVisible=false, targetEnabled=false, targetHit=false, targetTop=null;
  if (target && visible(target)) {
    targetVisible=true; targetEnabled=!disabled(target);
    const r=target.getBoundingClientRect();
    const x=Math.max(0,Math.min(viewport.width-1,r.left+r.width/2));
    const y=Math.max(0,Math.min(viewport.height-1,r.top+r.height/2));
    targetTop=document.elementFromPoint(x,y);
    targetHit=!!(targetTop && (
      targetTop===target || target.contains(targetTop) || targetTop.contains(target) ||
      (targetTop.closest && targetTop.closest('label') && targetTop.closest('label').contains(target))
    ));
  }

  const controlSelector='button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[role="button"]:not([aria-disabled="true"]),[role="combobox"]:not([aria-disabled="true"]),dds-button,dds-dropdown';
  const surfaceSelector='form,[role="dialog"],dds-drawer,.dds__drawer,.drawer,.modal-dialog,main,[role="main"]';
  const surfaces=Array.from(document.querySelectorAll(surfaceSelector)).filter(visible);
  let activeSurface=null, activeSurfaceControls=0;
  for (const surface of surfaces) {
    const count=Array.from(surface.querySelectorAll(controlSelector)).filter(visible).length;
    if (count>activeSurfaceControls) { activeSurface=surface; activeSurfaceControls=count; }
  }
  const enabledControls=Array.from(document.querySelectorAll(controlSelector)).filter(el => visible(el) && !disabled(el));
  const pageOperable=!!(targetVisible && targetEnabled && targetHit) || enabledControls.length>0;
  const surfaceRect=activeSurface ? activeSurface.getBoundingClientRect() : null;
  const rows=[]; const seen=new Set();
  for (const el of Array.from(document.querySelectorAll(selectors.join(',')))) {
    if (!el || seen.has(el) || !visible(el)) continue;
    seen.add(el);
    const style=getComputedStyle(el); const rect=el.getBoundingClientRect();
    const ariaBusy=(el.getAttribute('aria-busy')||'').toLowerCase();
    const role=(el.getAttribute('role')||'').toLowerCase();
    const text=(el.innerText||el.textContent||el.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();
    const classes=(el.className||'').toString();
    const looksLoading=/loading|spinner|progress|please wait|processing/i.test(`${classes} ${text}`) || ariaBusy==='true' || role==='progressbar';
    if (!looksLoading) continue;
    const area=Math.max(0,rect.width)*Math.max(0,rect.height);
    const viewportRatio=Math.min(1,area/viewportArea);
    const surfaceCoverRatio=surfaceRect ? Math.min(1,intersectArea(rect,surfaceRect)/Math.max(1,surfaceRect.width*surfaceRect.height)) : 0;
    const position=(style.position||'').toLowerCase();
    const zRaw=parseInt(style.zIndex||'0',10); const zIndex=Number.isFinite(zRaw)?zRaw:0;
    const blocksPointer=style.pointerEvents!=='none';
    const overlayNamed=/overlay|backdrop|loading-indicator/i.test(classes) || (el.tagName||'').toLowerCase()==='app-loadingindicator';
    const fixedLayer=position==='fixed' || position==='sticky' || position==='absolute';
    const topInside=!!(targetTop && (el===targetTop || el.contains(targetTop)));
    const targetBlocked=!!(targetVisible && !targetHit && topInside);
    const coversPage=viewportRatio>=minViewportRatio;
    const coversSurface=surfaceCoverRatio>=minSurfaceRatio;
    const globalBlocking=blocksPointer && (targetBlocked ||
      ((overlayNamed || fixedLayer || zIndex>=10) && (coversPage || coversSurface)) ||
      (ariaBusy==='true' && !pageOperable && (coversPage || coversSurface)));
    rows.push({
      tag:(el.tagName||'').toLowerCase(), id:el.id||'', classes:classes.slice(0,300),
      text:text.slice(0,300), role, aria_busy:ariaBusy,
      aria_hidden:(el.getAttribute('aria-hidden')||'').toLowerCase(),
      pointer_events:style.pointerEvents, blocks_pointer:blocksPointer,
      position, z_index:zIndex, viewport_ratio:Number(viewportRatio.toFixed(4)),
      surface_cover_ratio:Number(surfaceCoverRatio.toFixed(4)),
      target_blocked:targetBlocked, blocking:globalBlocking,
      rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}
    });
  }
  const blocking=rows.filter(x=>x.blocking);
  const passive=rows.filter(x=>!x.blocking);
  const signature=(items) => items.map(x => `${x.tag}|${x.classes}|${x.role}|${x.position}|${Math.round(x.rect.width/10)*10}x${Math.round(x.rect.height/10)*10}`).sort().join('||').slice(0,4000);
  return {
    active:blocking.length>0,
    classification:blocking.length ? 'hard_blocking' : (passive.length ? 'passive_indicator' : 'ready'),
    recommended_action:blocking.length ? 'wait' : 'proceed',
    url:location.href,
    ready_state:document.readyState,
    page_operable:pageOperable,
    enabled_control_count:enabledControls.length,
    active_surface_control_count:activeSurfaceControls,
    target_selector:targetSelector||'',
    target_found:!!target,
    target_visible:targetVisible,
    target_enabled:targetEnabled,
    target_hit_test_pass:targetHit,
    target_blocked:!!(targetVisible && !targetHit),
    overlay_count:blocking.length,
    passive_indicator_count:passive.length,
    overlays:blocking,
    passive_indicators:passive,
    blocking_fingerprint:signature(blocking),
    passive_fingerprint:signature(passive),
    false_positive_avoided:passive.length>0 && blocking.length===0 && pageOperable
  };
}
""",
                {
                    "targetSelector": str(target_selector or ""),
                    "minViewportRatio": min_viewport_ratio,
                    "minSurfaceRatio": min_surface_ratio,
                },
            )
            return state if isinstance(state, dict) else {"active": False, "overlays": []}
        except Exception as exc:
            return {
                "active": False,
                "classification": "observation_error",
                "recommended_action": "proceed_with_locator_verification",
                "status": "observation_error",
                "error": mask_sensitive_string(str(exc)),
                "overlays": [],
                "passive_indicators": [],
                "url": self._evidence_url(str(page.url or "")),
            }

    async def _record_passive_loading_indicator(
        self,
        *,
        reason: str,
        state: Dict[str, Any],
        target_selector: str = "",
    ) -> None:
        """Audit a false loading marker once without delaying the action."""
        fingerprint = str(state.get("passive_fingerprint") or "")
        signature = hashlib.sha256(
            f"{self._loading_watchdog_key()}|{target_selector}|{fingerprint}".encode("utf-8", errors="ignore")
        ).hexdigest()[:20]
        if not fingerprint or signature in self._loading_watchdog_ignored_passive:
            return
        self._loading_watchdog_ignored_passive.add(signature)
        self._autonomous_health_sequence += 1
        payload = {
            "schema_version": "hip.autonomous-page-health.v1",
            "sequence": self._autonomous_health_sequence,
            "phase": self._active_phase_name,
            "reason": reason,
            "decision": "proceed",
            "classification": "passive_loading_indicator_ignored",
            "target_selector": target_selector,
            "current_url": self._evidence_url(str(self.page.url if self.page else "")),
            "page_operable": bool(state.get("page_operable")),
            "target_hit_test_pass": bool(state.get("target_hit_test_pass")),
            "passive_indicators": mask_sensitive_data(state.get("passive_indicators") or []),
            "explanation": "A spinner/progress/aria-busy marker was visible in the DOM but did not cover the active form or intercept the requested control.",
        }
        try:
            out = self.run_dir / "mcp_runtime" / "autonomous_page_health"
            out.mkdir(parents=True, exist_ok=True)
            safe_write_json(out / f"passive_loading_{self._autonomous_health_sequence:04d}.json", payload)
        except Exception:
            pass

    async def assess_autonomous_page_health(self, *, target_selector: str = "", reason: str = "") -> Dict[str, Any]:
        """Return a concise action-oriented page-health decision.

        This is the deterministic observation/judge used before browser actions.
        It prevents false waits, catches route drift early, and delegates only a
        genuine blocker to the five-minute vision-confirmed watchdog/self-heal controller.
        """
        page = await self._ensure_active_page(self._active_target_url)
        current_url = str(page.url or "")
        if self._is_sso_transition_url(current_url):
            return {"decision": "reauthenticate", "status": "auth_expired", "current_url": self._evidence_url(current_url)}
        route_match = True
        if self._active_target_url:
            route_match = self._same_target_path(self._active_target_url, current_url)
        state = await self._current_loading_state(target_selector=target_selector)
        if not route_match:
            decision = "route_recover"
        elif state.get("active"):
            decision = "wait"
        elif state.get("page_operable") or state.get("target_hit_test_pass") or not target_selector:
            decision = "proceed"
        else:
            decision = "observe"
        result = {
            "decision": decision,
            "status": str(state.get("classification") or "unknown"),
            "phase": self._active_phase_name,
            "reason": reason,
            "current_url": self._evidence_url(current_url),
            "target_url": self._evidence_url(self._active_target_url),
            "route_match": route_match,
            "loading": mask_sensitive_data(state),
        }
        signature = hashlib.sha256(
            json.dumps(mask_sensitive_data({"decision": decision, "status": result["status"], "route_match": route_match, "target": target_selector}), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:20]
        if decision != "proceed" and signature not in self._autonomous_health_signatures:
            self._autonomous_health_signatures.add(signature)
            self._autonomous_health_sequence += 1
            try:
                out = self.run_dir / "mcp_runtime" / "autonomous_page_health"
                out.mkdir(parents=True, exist_ok=True)
                safe_write_json(out / f"health_decision_{self._autonomous_health_sequence:04d}.json", result)
            except Exception:
                pass
        return result

    async def _capture_loading_watchdog_evidence(
        self,
        *,
        reason: str,
        state: Dict[str, Any],
        elapsed_seconds: float,
        refresh_index: int,
        status: str,
    ) -> Dict[str, Any]:
        self._loading_watchdog_sequence += 1
        evidence_dir = self.run_dir / "mcp_runtime" / "loading_watchdog"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        stem = f"loading_watchdog_{self._loading_watchdog_sequence:04d}_{status}"
        payload: Dict[str, Any] = {
            "schema_version": "hip.loading-watchdog.v1",
            "status": status,
            "phase": self._active_phase_name,
            "reason": reason,
            "elapsed_seconds": round(float(elapsed_seconds), 3),
            "threshold_seconds": float(getattr(self.config.portal, "loading_watchdog_timeout_seconds", 120) or 120),
            "refresh_index": int(refresh_index),
            "current_url": self._evidence_url(str(self.page.url if self.page else "")),
            "target_url": self._evidence_url(self._active_target_url),
            "loading_state": mask_sensitive_data(state),
            "network_event_count": len(self.network_tab_events),
            "console_message_count": len(self.console_messages),
            "recent_console": mask_sensitive_data(self.console_messages[-25:]),
            "recent_network": mask_sensitive_data([asdict(x) if hasattr(x, "__dataclass_fields__") else x for x in self.network_tab_events[-25:]]),
        }
        try:
            if self.playwright_mcp_backend is not None:
                snap = await self.playwright_mcp_backend.snapshot(boxes=False, depth=6)
                payload["playwright_mcp_snapshot_excerpt"] = str((snap or {}).get("text") or "")[:12000]
                payload["playwright_mcp_url"] = self._evidence_url(await self.playwright_mcp_backend.get_current_url())
        except Exception as exc:
            payload["playwright_mcp_error"] = mask_sensitive_string(str(exc))
        try:
            if self.mcp_backend is not None:
                payload["chrome_devtools_mcp_url"] = self._evidence_url(await self.mcp_backend.get_current_url())
        except Exception as exc:
            payload["chrome_devtools_mcp_error"] = mask_sensitive_string(str(exc))
        try:
            shot = evidence_dir / f"{stem}.png"
            await self.screenshot(shot, full_page=True)
            payload["screenshot"] = str(shot)
        except Exception as exc:
            payload["screenshot_error"] = mask_sensitive_string(str(exc))
        safe_write_json(evidence_dir / f"{stem}.json", payload)
        return payload

    async def _active_unsaved_form_surface(self) -> Dict[str, Any]:
        """Detect a visible HIP Create/Wizard surface whose unsaved state must survive.

        Route URLs are often unchanged when Dell opens a drawer. Reloading that URL
        therefore returns to the listing and destroys all entered values. The loading
        watchdog must never refresh such a surface.
        """
        page = await self._ensure_active_page(self._active_target_url)
        try:
            result = await page.evaluate(r"""
() => {
  const visible=(el)=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||'1')!==0)};
  const roots=Array.from(document.querySelectorAll('[role=dialog],dds-drawer,.dds__drawer,.dds__modal,.modal-dialog,form,[role=tabpanel],app-addmap,app-create-biz-flow,app-transport-profile'))
    .filter(visible);
  const candidates=roots.map(el=>{
    const text=(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();
    const controls=Array.from(el.querySelectorAll('input:not([type=hidden]),textarea,select,[role=combobox],[role=radio],[role=checkbox],input[type=file]')).filter(visible).length;
    const createMarker=/\b(create|add|configure)\b/i.test(text) && /\b(cancel|submit|save|create|next|back)\b/i.test(text);
    const phaseMarker=/create map|create document type|create rule|create transport profile|flow details|configure source|configure target|configure routing|create biz flow/i.test(text);
    return {text:text.slice(0,1200),controls,createMarker,phaseMarker,tag:(el.tagName||'').toLowerCase(),classes:(el.className||'').toString().slice(0,240)};
  }).filter(x=>x.controls>=2 && (x.phaseMarker || x.createMarker)).sort((a,b)=>b.controls-a.controls);
  return {active:candidates.length>0, candidate:candidates[0]||null, candidate_count:candidates.length};
}
""")
            return result if isinstance(result, dict) else {"active": False}
        except Exception as exc:
            return {"active": False, "error": mask_sensitive_string(str(exc))}

    async def _neutralize_stale_dds_loading_overlay(self, *, target_selector: str, reason: str) -> Dict[str, Any]:
        """Safely remove only a stale DDS loading veil, never form values.

        This recovery is allowed only when a visible unsaved Create/Wizard surface and
        the requested enabled control already exist. It changes presentation-only
        overlay styles/classes; no business control value is assigned.
        """
        page = await self._ensure_active_page(self._active_target_url)
        surface = await self._active_unsaved_form_surface()
        if not surface.get("active"):
            return {"pass": False, "status": "no_unsaved_form_surface", "surface": surface}
        try:
            result = await page.evaluate(r"""
({selector}) => {
  const visible=(el)=>{if(!el||!el.getBoundingClientRect)return false;const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||'1')!==0)};
  let target=null; try{target=selector?document.querySelector(selector):null}catch(_){target=null}
  if(!target || !visible(target) || target.disabled || target.getAttribute('aria-disabled')==='true') return {pass:false,status:'target_not_ready'};
  const overlays=Array.from(document.querySelectorAll('.dds__loading-indicator__overlay,app-loadingindicator .dds__loading-indicator__overlay'))
    .filter(visible);
  if(!overlays.length) return {pass:false,status:'no_dds_overlay'};
  const changed=[];
  for(const el of overlays){
    const cls=(el.className||'').toString();
    if(!/dds__loading-indicator__overlay/.test(cls)) continue;
    el.setAttribute('data-hip-stale-overlay-neutralized','true');
    el.setAttribute('aria-hidden','true');
    el.style.setProperty('pointer-events','none','important');
    el.style.setProperty('display','none','important');
    changed.push({tag:(el.tagName||'').toLowerCase(),classes:cls.slice(0,240)});
  }
  document.body.classList.remove('dds__loading-indicator__overlay--overflow-hidden');
  document.body.classList.remove('dds__overlay--overflow-hidden');
  const r=target.getBoundingClientRect();
  const x=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2));
  const y=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));
  const top=document.elementFromPoint(x,y);
  const hit=!!(top&&(top===target||target.contains(top)||top.contains(target)));
  return {pass:changed.length>0&&hit,status:changed.length?(hit?'neutralized':'target_still_blocked'):'no_matching_overlay',changed_count:changed.length,changed,target_hit_test_pass:hit};
}
""", {"selector": str(target_selector or "")})
        except Exception as exc:
            result = {"pass": False, "status": "neutralization_error", "error": mask_sensitive_string(str(exc))}
        audit = {
            "schema_version": "hip.stale-overlay-recovery.v1",
            "phase": self._active_phase_name,
            "reason": reason,
            "target_selector": target_selector,
            "surface": mask_sensitive_data(surface),
            "result": mask_sensitive_data(result),
            "form_values_mutated": False,
            "page_refreshed": False,
        }
        try:
            out = self.run_dir / "mcp_runtime" / "loading_watchdog"
            out.mkdir(parents=True, exist_ok=True)
            safe_write_json(out / f"stale_overlay_recovery_{self._loading_watchdog_sequence + 1:04d}.json", audit)
        except Exception:
            pass
        if result.get("pass"):
            await self._ensure_page_observers()
        return {**result, "audit": audit}

    async def refresh_current_page_preserving_session(self, *, reason: str = "loading_watchdog") -> Dict[str, Any]:
        """Refresh the active page without replacing the persistent authenticated context."""
        page = await self._ensure_active_page(self._active_target_url)
        current_url = str(page.url or self._active_target_url or "")
        ev = await self._begin_action("refresh", current_url)
        audit: Dict[str, Any] = {
            "status": "started",
            "reason": reason,
            "phase": self._active_phase_name,
            "url_before": self._evidence_url(current_url),
            "same_persistent_context": True,
        }
        try:
            # Python Playwright performs the exact reload. Both MCPs independently
            # observe the effect before the repaired phase is allowed to continue.
            await page.reload(
                wait_until="domcontentloaded",
                timeout=int(getattr(self.config.portal, "timeout_ms", 45000) or 45000),
            )
        except Exception as exc:
            audit["reload_warning"] = mask_sensitive_string(str(exc))
            # A Playwright timeout may occur after the browser has already committed
            # the reload. Keep the page when its URL remains usable.
        page = await self._ensure_active_page(current_url)
        if self._is_sso_transition_url(str(page.url or "")):
            await self._finish_action(ev, False, "refresh redirected to SSO")
            raise RuntimeError("HIP_AUTH_SESSION_EXPIRED: page refresh redirected the persistent browser to Dell SSO")
        await self._ensure_page_observers()
        try:
            await self.collect_dom_click_log()
        except Exception:
            pass
        try:
            await self._verify_dual_mcp_same_surface(
                current_url,
                timeout_seconds=min(20.0, float(getattr(self.config.mcp, "dual_mcp_same_surface_timeout_seconds", 30.0))),
                require_expected_target=False,
            )
            audit["dual_mcp_observation"] = "pass"
        except Exception as exc:
            audit["dual_mcp_observation"] = "warning"
            audit["dual_mcp_warning"] = mask_sensitive_string(str(exc))
        audit.update({
            "status": "refreshed",
            "url_after": self._evidence_url(str(page.url or "")),
        })
        safe_write_json(
            self.run_dir / "mcp_runtime" / "loading_watchdog" / f"page_refresh_{self._loading_watchdog_sequence:04d}.json",
            audit,
        )
        await self._finish_action(ev, True, error=f"safe page refresh: {reason}")
        return audit

    async def _confirm_loading_with_vision(
        self, *, state: Dict[str, Any], elapsed_seconds: float, reason: str
    ) -> Dict[str, Any]:
        """Require multimodal proof before a five-minute loading refresh.

        DOM geometry determines when the watchdog starts. Vision is an independent
        second signal used only at the refresh boundary so a stale aria-busy node
        cannot cause an unnecessary Edge reload.
        """
        cfg = getattr(self.config, "vision_runtime", None)
        required = bool(getattr(self.config.portal, "loading_watchdog_vision_confirm_before_refresh", True))
        if not required:
            return {"available": False, "confirmed_blocking_loading": True, "reason": "vision confirmation disabled by portal policy"}
        if self.vision_runtime is None or cfg is None or not bool(getattr(cfg, "use_for_loading_watchdog", True)):
            return {"available": False, "confirmed_blocking_loading": False, "reason": "vision loading watchdog is not configured"}
        try:
            result = await self.vision_runtime.classify_loading_page(
                page=self.page, dom_state=state, elapsed_seconds=elapsed_seconds, reason=reason
            )
        except Exception as exc:
            result = {"available": False, "confirmed_blocking_loading": False, "error": mask_sensitive_string(str(exc))[:1000]}
        key = self._loading_watchdog_key()
        self._loading_watchdog_last_vision[key] = mask_sensitive_data(result)
        try:
            safe_write_json(
                self.run_dir / "mcp_runtime" / "loading_watchdog" / f"vision_loading_{self._loading_watchdog_sequence:04d}.json",
                {"reason": reason, "elapsed_seconds": elapsed_seconds, "vision": result, "phase": self._active_phase_name},
            )
        except Exception:
            pass
        return result

    async def wait_for_portal_loading_complete(
        self,
        *,
        reason: str = "portal readiness",
        timeout_seconds: Optional[float] = None,
        refresh_on_timeout: bool = True,
        target_selector: str = "",
    ) -> bool:
        """Wait only for a confirmed blocking loader; ignore passive markers.

        A loader must be geometrically blocking for consecutive observations
        before the five-minute timer starts. This avoids false waits caused by
        persistent DDS spinner/progressbar/aria-busy nodes that remain in the
        DOM while the form is already usable.
        """
        if not self.page:
            return True
        default_threshold = float(getattr(self.config.portal, "loading_watchdog_timeout_seconds", 300) or 300)
        vision_cfg = getattr(self.config, "vision_runtime", None)
        if vision_cfg is not None and bool(getattr(vision_cfg, "use_for_loading_watchdog", True)):
            default_threshold = max(default_threshold, float(getattr(vision_cfg, "loading_refresh_after_seconds", 300) or 300))
        threshold = max(
            1.0,
            float(timeout_seconds if timeout_seconds is not None else default_threshold),
        )
        poll_seconds = max(0.05, float(getattr(self.config.portal, "loading_watchdog_poll_seconds", 1.0) or 1.0))
        max_refreshes = max(0, int(getattr(self.config.portal, "loading_watchdog_max_refreshes_per_phase", 1) or 0))
        required_samples = max(1, int(getattr(self.config.portal, "loading_watchdog_consecutive_blocking_samples", 2) or 2))
        key = self._loading_watchdog_key()
        loop = asyncio.get_running_loop()
        while True:
            page = await self._ensure_active_page(self._active_target_url)
            if self._is_sso_transition_url(str(page.url or "")):
                raise RuntimeError("HIP_AUTH_SESSION_EXPIRED: Dell redirected the browser to SSO while waiting for portal loading")
            try:
                state = await self._current_loading_state(target_selector=target_selector)
            except TypeError:
                # Backward-compatible with injected tests/custom sessions that
                # monkeypatch the older no-argument observation method.
                state = await self._current_loading_state()
            now = loop.time()
            if not bool(state.get("active")):
                self._loading_watchdog_candidate_counts.pop(key, None)
                self._loading_watchdog_last_fingerprints.pop(key, None)
                started = self._loading_watchdog_first_seen.pop(key, None)
                if state.get("passive_indicator_count") or state.get("classification") == "passive_indicator":
                    await self._record_passive_loading_indicator(
                        reason=reason,
                        state=state,
                        target_selector=target_selector,
                    )
                if started is not None:
                    elapsed = max(0.0, now - started)
                    if elapsed >= 1.0 or self._loading_watchdog_refresh_counts.get(key, 0):
                        await self._capture_loading_watchdog_evidence(
                            reason=reason,
                            state=state,
                            elapsed_seconds=elapsed,
                            refresh_index=self._loading_watchdog_refresh_counts.get(key, 0),
                            status="completed",
                        )
                return True

            fingerprint = str(state.get("blocking_fingerprint") or state.get("fingerprint") or "blocking")
            prior = self._loading_watchdog_last_fingerprints.get(key)
            count = self._loading_watchdog_candidate_counts.get(key, 0)
            count = count + 1 if prior == fingerprint else 1
            self._loading_watchdog_last_fingerprints[key] = fingerprint
            self._loading_watchdog_candidate_counts[key] = count
            if count < required_samples:
                await asyncio.sleep(poll_seconds)
                continue

            started = self._loading_watchdog_first_seen.setdefault(key, now)
            elapsed = max(0.0, now - started)
            if elapsed < threshold:
                await asyncio.sleep(min(poll_seconds, max(0.05, threshold - elapsed)))
                continue

            refresh_count = self._loading_watchdog_refresh_counts.get(key, 0)
            await self._capture_loading_watchdog_evidence(
                reason=reason,
                state=state,
                elapsed_seconds=elapsed,
                refresh_index=refresh_count,
                status="timeout_before_vision_confirmation" if refresh_on_timeout and refresh_count < max_refreshes else "timeout_after_refresh",
            )

            # If an unsaved HIP form is open, first try the existing safe in-place
            # stale-overlay recovery. Only a loader that survives this repair AND
            # is confirmed visually is allowed to trigger the five-minute reload.
            unsaved_surface = await self._active_unsaved_form_surface()
            if unsaved_surface.get("active"):
                recovery = await self._neutralize_stale_dds_loading_overlay(
                    target_selector=target_selector, reason=reason
                )
                if recovery.get("pass"):
                    self._loading_watchdog_candidate_counts.pop(key, None)
                    self._loading_watchdog_last_fingerprints.pop(key, None)
                    self._loading_watchdog_first_seen.pop(key, None)
                    await self._capture_loading_watchdog_evidence(
                        reason=reason, state=await self._current_loading_state(target_selector=target_selector),
                        elapsed_seconds=elapsed, refresh_index=refresh_count, status="stale_overlay_neutralized_before_vision_refresh"
                    )
                    return True

            vision = await self._confirm_loading_with_vision(state=state, elapsed_seconds=elapsed, reason=reason)
            vision_available = bool(vision.get("available"))
            vision_confirmed = bool(vision.get("confirmed_blocking_loading"))
            vision_fail_closed = bool(getattr(self.config.portal, "loading_watchdog_vision_fail_closed", True))
            if not vision_confirmed:
                await self._capture_loading_watchdog_evidence(
                    reason=reason, state=state, elapsed_seconds=elapsed, refresh_index=refresh_count,
                    status="vision_rejected_refresh" if vision_available else "vision_unavailable_refresh_blocked",
                )
                if vision_available:
                    # Visual evidence says the DOM marker is not a truly blocking loader.
                    # Treat it as passive for this action and continue instead of looping.
                    self._loading_watchdog_candidate_counts.pop(key, None)
                    self._loading_watchdog_last_fingerprints.pop(key, None)
                    self._loading_watchdog_first_seen.pop(key, None)
                    return True
                if vision_fail_closed:
                    return False

            if not refresh_on_timeout or refresh_count >= max_refreshes:
                return False

            # The user requires a refresh after >5 minutes when vision independently
            # confirms that the loading surface is still blocking.  If an unsaved
            # drawer is open, preserve a value-free checkpoint and force the outer
            # deterministic phase controller to replay from input.json after reload.
            if unsaved_surface.get("active"):
                try:
                    safe_write_json(
                        self.run_dir / "mcp_runtime" / "loading_watchdog" / f"unsaved_replay_checkpoint_{self._loading_watchdog_sequence:04d}.json",
                        {
                            "phase": self._active_phase_name,
                            "target_url": self._evidence_url(self._active_target_url),
                            "reason": reason,
                            "elapsed_seconds": elapsed,
                            "vision": vision,
                            "surface": mask_sensitive_data(unsaved_surface),
                            "replay_source": "current input.json + deterministic expert skill; customer values are not stored in this checkpoint",
                        },
                    )
                except Exception:
                    pass
                await self.refresh_current_page_preserving_session(
                    reason=f"vision-confirmed blocking loader persisted for {elapsed:.1f}s on unsaved form; reload then deterministic replay"
                )
                self._loading_watchdog_refresh_counts[key] = refresh_count + 1
                self._loading_watchdog_replay_required[key] = "vision_confirmed_loading_refresh_unsaved_form"
                return False

            await self.refresh_current_page_preserving_session(
                reason=f"vision-confirmed blocking loading persisted for {elapsed:.1f}s during {reason}"
            )
            self._loading_watchdog_refresh_counts[key] = refresh_count + 1
            self._loading_watchdog_first_seen[key] = loop.time()
            self._loading_watchdog_candidate_counts[key] = 0
            self._loading_watchdog_last_fingerprints.pop(key, None)

    async def wait_for_blocking_overlays_gone(
        self,
        *,
        timeout_ms: int = 10000,
        target_selector: str = "",
    ) -> bool:
        """Wait briefly for a confirmed pointer-blocking overlay.

        This uses the same geometry/hit-test classifier as the watchdog. Passive
        inline spinners and persistent aria-busy markers never fail this gate.
        """
        if not self.page:
            return True
        deadline = asyncio.get_running_loop().time() + max(0.05, float(timeout_ms) / 1000.0)
        last_state: Dict[str, Any] = {}
        while True:
            try:
                last_state = await self._current_loading_state(target_selector=target_selector)
            except TypeError:
                last_state = await self._current_loading_state()
            if not bool(last_state.get("active")):
                if last_state.get("passive_indicator_count") or last_state.get("classification") == "passive_indicator":
                    await self._record_passive_loading_indicator(
                        reason="blocking overlay gate",
                        state=last_state,
                        target_selector=target_selector,
                    )
                return True
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.2)

        diagnostics = {
            "status": "timeout",
            "timeout_ms": timeout_ms,
            "target_selector": target_selector,
            "phase": self._active_phase_name,
            "loading_state": mask_sensitive_data(last_state),
        }
        try:
            safe_write_json(
                self.run_dir / "mcp_runtime" / f"blocking_overlay_{len(self.action_events)+1:05d}.json",
                diagnostics,
            )
        except Exception:
            pass
        return False

    async def ensure_interactable(self, *, action: str = "", selector: str = "", timeout_ms: int = 20000) -> None:
        page = await self._ensure_active_page(self._active_target_url)
        if self._is_sso_transition_url(str(page.url or "")):
            raise RuntimeError(
                "HIP_AUTH_SESSION_EXPIRED: Dell redirected the persistent browser to SSO during "
                f"{action or 'a portal action'}"
            )

        if bool(getattr(self.config.portal, "autonomous_page_health_enabled", True)):
            health = await self.assess_autonomous_page_health(
                target_selector=selector,
                reason=action or selector or "portal action",
            )
            decision = str(health.get("decision") or "")
            if decision == "reauthenticate":
                raise RuntimeError("HIP_AUTH_SESSION_EXPIRED: autonomous page-health judge detected an authentication surface")
            if decision == "route_recover" and self._active_target_url:
                await self._react_ensure_target_surface(self._active_target_url, max_steps=4)

        ready = await self.wait_for_portal_loading_complete(
            reason=action or selector or "portal action",
            refresh_on_timeout=True,
            target_selector=selector,
        )
        if not ready:
            key = self._loading_watchdog_key()
            replay_reason = self._loading_watchdog_replay_required.pop(key, "")
            if replay_reason:
                raise RuntimeError(
                    "HIP_VISION_LOADING_REFRESH_REPLAY_REQUIRED: a blocking loading surface persisted for more than five minutes, "
                    "the vision model confirmed it, Microsoft Edge was refreshed, and the current phase must be replayed deterministically from input.json"
                )
            surface = await self._active_unsaved_form_surface()
            if surface.get("active"):
                raise RuntimeError(
                    "HIP_PORTAL_STALE_OVERLAY_UNRECOVERED_FORM_PRESERVED: a confirmed loading overlay remained, "
                    "but the active unsaved HIP form was preserved and was not refreshed while preparing "
                    f"{action or selector or 'the requested control'}"
                )
            raise RuntimeError(
                "HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH: confirmed blocking portal loading remained active after the "
                "five-minute vision-confirmed watchdog refresh while preparing "
                f"{action or selector or 'the requested control'}"
            )
        ok = await self.wait_for_blocking_overlays_gone(
            timeout_ms=min(timeout_ms, 3000),
            target_selector=selector,
        )
        if not ok:
            raise RuntimeError(
                "HIP_PORTAL_BLOCKING_OVERLAY: a confirmed loading overlay still intercepts "
                f"{action or selector or 'the requested control'}"
            )

    async def wait_ready(self) -> None:
        if not self.page:
            return
        ev = await self._begin_action("wait", "portal_ready")
        try:
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=self.config.portal.timeout_ms)
            except Exception:
                # Angular may commit the page after Playwright's load-state timeout.
                # The loading watchdog below remains the source of truth.
                pass
            if hasattr(self, "_loading_watchdog_first_seen"):
                ready = await self.wait_for_portal_loading_complete(
                    reason=f"phase {getattr(self, '_active_phase_name', '') or 'standalone'} readiness",
                    refresh_on_timeout=True,
                )
            else:
                # Preserve lightweight injected/test sessions that predate the
                # watchdog fields while production sessions always use it.
                ready = await self.wait_for_blocking_overlays_gone(timeout_ms=5000)
            if not ready:
                raise RuntimeError(
                    "HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH: portal loading remained active after "
                    "waiting five minutes, confirming the loading surface with the vision model, and refreshing Microsoft Edge"
                )
            await asyncio.sleep(0.25)
            await self._finish_action(ev, True)
        except Exception as exc:
            await self._finish_action(ev, False, str(exc))
            raise

    async def screenshot(self, path: Path, full_page: bool = True) -> str:
        if not self.page:
            raise RuntimeError("Browser session not started")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = await self.page.screenshot(full_page=full_page)
            local_path = safe_write_bytes(path, data)
        except Exception:
            await self.page.screenshot(path=str(path), full_page=full_page)
            local_path = str(path)
        # Independent Playwright MCP screenshot evidence from the same tab.
        if self.playwright_mcp_backend is not None:
            try:
                mcp_path = await self.playwright_mcp_backend.screenshot(f"mcp_{path.name}", full_page=full_page)
                safe_write_json(path.with_suffix(path.suffix + ".playwright_mcp.json"), {"local": local_path, "playwright_mcp": mcp_path})
            except Exception as exc:
                safe_write_json(path.with_suffix(path.suffix + ".playwright_mcp.error.json"), {"error": mask_sensitive_string(str(exc))})
        return local_path

    async def _observe_form_memory_action(
        self, *, action_type: str, selector: str, audit: Dict[str, Any], success: bool
    ) -> None:
        memory = getattr(self, "flow_pattern_memory", None)
        if memory is None or not hasattr(memory, "observe_action"):
            return
        surface = audit.get("form_surface") if isinstance(audit.get("form_surface"), dict) else {}
        semantic_target = ""
        try:
            if self.page and selector:
                semantic_target = await self.page.locator(selector).first.evaluate(
                    r"""el => [el.getAttribute('formcontrolname')||el.getAttribute('ng-reflect-name')||el.getAttribute('name')||'', el.getAttribute('role')||'', el.getAttribute('aria-label')||'', (el.tagName||'').toLowerCase()].join('|')"""
                )
        except Exception:
            semantic_target = selector
        try:
            memory.observe_action({
                "form_family": surface.get("family") or "generic_hip_form",
                "structure_fingerprint": surface.get("structure_fingerprint") or "",
                "action_type": action_type,
                "semantic_target": semantic_target or selector,
                "success": success,
                "event_proof": {
                    "structural_parent_revealed": bool(audit.get("structural_parent_revealed")),
                    "bounding_box_stable": bool((audit.get("bounding_box_stability") or {}).get("stable")),
                    "hit_test_pass": bool((audit.get("hit_test") or {}).get("pass")),
                },
            })
        except Exception:
            return

    async def log_automation_click(self, *, action: str, locator: Locator, selector: str = "", screenshot_name: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> None:
        if not self.page:
            return
        before_url = self.page.url
        details: Dict[str, Any] = {}
        try:
            details = await locator.first.evaluate("""
(el) => {
  const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {x:0,y:0,width:0,height:0};
  return {tag:(el.tagName||'').toLowerCase(), text:(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0,500), id:el.id||'', classes:(el.className||'').toString().slice(0,300), href:el.href||'', role:el.getAttribute('role')||'', ariaLabel:el.getAttribute('aria-label')||'', boundingBox:{x:r.x,y:r.y,width:r.width,height:r.height}};
}
""")
        except Exception:
            details = {}
        screenshot = None
        if screenshot_name and self.config.extraction.screenshot_each_click:
            try:
                screenshot = await self.screenshot(self.clicks_dir / screenshot_name)
            except Exception:
                screenshot = None
        payload_extra = {"stage": self._current_stage, "before_url": before_url}
        if extra:
            payload_extra.update(mask_sensitive_data(extra))
        self.click_events.append(ClickEvent(
            timestamp=utc_now(), source="automation", url=before_url, action=action, selector=selector,
            text=details.get("text"), tag=details.get("tag"), element_id=details.get("id"),
            classes=details.get("classes"), href=details.get("href"), role=details.get("role"),
            aria_label=details.get("ariaLabel"), bounding_box=details.get("boundingBox", {}), screenshot=screenshot,
            extra=payload_extra,
        ))

    def set_portal_mutation_authorization(self, *, enabled: bool, allowed_labels: Optional[List[str]] = None, task_id: str = "") -> None:
        """Temporarily authorize only explicitly named portal mutation actions.

        This does not read environment variables or confirmation phrases itself;
        callers must pass the external three-part gate before enabling it.
        """
        labels = [re.sub(r"[^a-z0-9]+", "_", str(x or "").strip().lower()).strip("_") for x in (allowed_labels or []) if str(x or "").strip()]
        self._portal_mutation_authorization = {
            "enabled": bool(enabled),
            "allowed_labels": sorted(set(labels)),
            "task_id": str(task_id or ""),
            "confirmed_at": utc_now() if enabled else "",
        }
        safe_write_json(self.run_dir / "safety" / "portal_mutation_authorization.json", mask_sensitive_data(self._portal_mutation_authorization))

    def clear_portal_mutation_authorization(self) -> None:
        self.set_portal_mutation_authorization(enabled=False, allowed_labels=[], task_id="")

    async def _sync_page_click_safety_context(self, *, action: str, selector: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Synchronize server-side authorization with the capture-phase DOM guard.

        This prevents the page listener from blocking a legitimately authorized
        Save/Create/Deploy while still recording the provenance. Structural wizard
        openers are one-shot and are cleared by the click listener itself.
        """
        if not self.page:
            return
        auth = mask_sensitive_data(dict(self._portal_mutation_authorization or {}))
        text = str((details or {}).get("text") or action or "").strip().lower()
        role = str((details or {}).get("role") or "").strip().lower()
        action_l = str(action or "").lower()
        selector_l = str(selector or "").lower()
        structural = bool(
            (text == "create biz flow" and (role == "menuitem" or "action-menu" in selector_l or "template" in action_l or "launch" in action_l))
            or ("structural_opener" in action_l)
        )
        try:
            await self.page.evaluate(
                """({auth, structural, actionLabel}) => {
                  window.__HIP_MUTATION_AUTH = auth || {enabled:false,allowed_labels:[],task_id:''};
                  window.__HIP_STRUCTURAL_OPENER = structural ? {enabled:true, action_label:String(actionLabel||'')} : null;
                  return true;
                }""",
                {"auth": auth, "structural": structural, "actionLabel": str(action or "")[:240]},
            )
        except Exception:
            pass

    async def _assert_safe_click(self, *, action: str, locator: Locator, selector: str = "") -> Dict[str, Any]:
        """Block final mutations before either MCP or Python can click them."""
        details: Dict[str, Any] = {}
        try:
            details = await locator.first.evaluate(r"""el => ({
              text:String(el.innerText||el.value||el.getAttribute('aria-label')||el.getAttribute('title')||'').replace(/\s+/g,' ').trim(),
              role:el.getAttribute('role')||'', type:el.getAttribute('type')||'',
              tag:(el.tagName||'').toLowerCase(), href:el.getAttribute('href')||''
            })""")
        except Exception:
            details = {}
        text = str(details.get("text") or action or "").strip().lower()
        role = str(details.get("role") or "").strip().lower()
        sel = str(selector or "").lower()
        action_l = str(action or "").lower()
        allow = False
        if text in {"add", "+ add", "continue", "next", "back", "cancel", "close"}:
            allow = True
        if text == "create biz flow" and (role == "menuitem" or "action-menu" in sel or "template" in action_l or "launch" in action_l):
            allow = True
        blocked = re.search(r"\b(save|create|submit|delete|remove|deploy|publish|update|enable|disable|confirm)\b", text)
        selector_blocked = re.search(r"(save|submit|delete|deploy|publish|update)(?:[-_]|\b)", sel)
        if (blocked or selector_blocked) and not allow:
            auth = self._portal_mutation_authorization if isinstance(getattr(self, "_portal_mutation_authorization", None), dict) else {}
            normalized_candidates = {
                re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_"),
                re.sub(r"[^a-z0-9]+", "_", str(action or "").strip().lower()).strip("_"),
            }
            allowed_labels = set(auth.get("allowed_labels") or [])
            explicitly_authorized = bool(auth.get("enabled")) and bool(normalized_candidates & allowed_labels)
            if not explicitly_authorized:
                finding = {"action": action, "selector": selector, "element": details, "reason": "blocked final mutation before click", "authorization": mask_sensitive_data(auth)}
                safe_write_json(self.run_dir / "safety" / f"blocked_click_{len(self.action_events)+1:05d}.json", finding)
                raise RuntimeError(f"Safety guard blocked mutating click: {text or selector or action}")
            safe_write_json(self.run_dir / "safety" / f"authorized_mutating_click_{len(self.action_events)+1:05d}.json", {"action": action, "selector": selector, "element": details, "authorization": mask_sensitive_data(auth)})
        return details

    async def _read_locator_committed_value(self, locator: Locator) -> Optional[str]:
        """Read the committed value of an already-resolved field without inventing selectors."""
        try:
            loc = locator.first
            try:
                value = await loc.input_value(timeout=2500)
                return str(value)
            except Exception:
                pass
            try:
                return str(await loc.get_attribute("value", timeout=1500) or "")
            except Exception:
                pass
            try:
                return str(await loc.text_content(timeout=1500) or "")
            except Exception:
                return None
        except Exception:
            return None

    @staticmethod
    def _exact_fill_value_matches(actual: Optional[str], expected: str) -> bool:
        if actual is None:
            return False
        normalize = lambda x: re.sub(r"\s+", " ", str(x or "").strip())
        return normalize(actual) == normalize(expected)

    async def _verify_exact_fill_commit(self, locator: Locator, expected: str, *, selector: str = "") -> Dict[str, Any]:
        """Require a stable exact field value before a fill is declared successful.

        Angular/DDS may momentarily echo typed text and then rerender the input.  We
        therefore read the resolved field twice and accept success only when the
        exact value remains committed across the settle interval.
        """
        first = await self._read_locator_committed_value(locator)
        await asyncio.sleep(0.12)
        second = await self._read_locator_committed_value(locator)
        return {
            "pass": self._exact_fill_value_matches(first, expected) and self._exact_fill_value_matches(second, expected),
            "selector": selector,
            "expected_length": len(str(expected)),
            "first": mask_sensitive_string(str(first or "")),
            "second": mask_sensitive_string(str(second or "")),
        }

    def _record_autowebglm_primary_outcome(
        self, *, decision: Mapping[str, Any], success: bool, reward: Optional[float] = None, drift: bool = False
    ) -> Dict[str, Any]:
        bridge = getattr(self, "autowebglm_bridge", None)
        if bridge is None or not hasattr(bridge, "record_downstream_outcome"):
            return {"updated": False, "reason": "bridge_unavailable"}
        try:
            return bridge.record_downstream_outcome(
                decision=decision or {}, success=bool(success), reward=reward, drift=bool(drift)
            )
        except Exception as exc:
            return {"updated": False, "reason": "reward_error_fail_open", "error": mask_sensitive_string(str(exc))[:500]}


    async def _autowebglm_primary_decision(
        self, *, action: str, selector: str, label: str = "", value: str = ""
    ) -> Dict[str, Any]:
        bridge = getattr(self, "autowebglm_bridge", None)
        page = getattr(self, "page", None)
        if bridge is None or page is None or not bool(getattr(bridge, "primary_framework", False)):
            return {"status": "bypassed", "framework": "autowebglm"}
        history = [
            {
                "action": getattr(ev, "action_type", ""),
                "label": getattr(ev, "target", ""),
                "status": getattr(ev, "status", ""),
            }
            for ev in list(getattr(self, "action_events", []) or [])[-40:]
        ]
        world_hints = []
        try:
            wm = getattr(getattr(self, "semantic_action_gate", None), "world_model", None)
            if wm is not None:
                world_hints = wm.planner_hints(
                    phase=self._active_phase_name or "standalone",
                    action=action, label=label or selector, section="", limit=4,
                )
        except Exception:
            world_hints = []
        hint_text = ""
        if world_hints:
            # Value-free semantic memory only. The current live target still has to
            # pass the semantic gate; memory never emits a selector or coordinates.
            compact = [
                {
                    "trust": h.get("trust"), "confidence": h.get("confidence"),
                    "action_family": h.get("action_family"),
                    "control": h.get("control"), "effect_type": h.get("effect_type"),
                }
                for h in world_hints
            ]
            hint_text = f" Prior verified semantic website memory={json.dumps(compact, ensure_ascii=False, default=str)[:4000]}."
        task = (
            f"HIP phase={self._active_phase_name or 'standalone'}; perform vetted browser intent "
            f"{action} on {label or selector}." + hint_text
        )
        try:
            decision = await bridge.primary_decide(
                page=page,
                task=task,
                action=action,
                selector=selector,
                label=label or selector,
                value=value,
                history=history,
                learning=str(getattr(self, "replay_policy_mode", "exploration") or "exploration") != "exploitation",
                complex_task=True,
            )
        except Exception as exc:
            decision = {
                "status": "deterministic_protocol_fallback",
                "framework": "autowebglm_primary",
                "reason": f"primary decision exception: {mask_sensitive_string(str(exc))[:800]}",
                "aligned": True,
                "tool_adapter_required": True,
            }
        if isinstance(decision, dict):
            decision["world_model_hints"] = mask_sensitive_data(world_hints)
            decision["world_model_is_advisory"] = True
            decision["live_reproof_required"] = True
        try:
            safe_write_json(
                self.run_dir / "browser_intelligence" / "autowebglm_primary" / f"{len(self.action_events):05d}_{action}.json",
                decision,
            )
        except Exception:
            pass
        if decision.get("status") == "rejected" and bool(getattr(getattr(self.config, "autowebglm", None), "require_intent_alignment", True)):
            raise RuntimeError(f"HIP_AUTOWEBGLM_PRIMARY_INTENT_REJECTED: {decision.get('reason') or 'unaligned action'}")
        return decision

    def website_world_model_summary(self, *, phase: str = "") -> Dict[str, Any]:
        """Return value-free persistent website memory for UI/recovery diagnostics."""
        try:
            wm = getattr(getattr(self, "semantic_action_gate", None), "world_model", None)
            return wm.summary(phase=phase or self._active_phase_name or "") if wm is not None else {"enabled": False}
        except Exception as exc:
            return {"enabled": False, "error": mask_sensitive_string(str(exc))[:500]}

    def website_world_model_recommendations(self, *, phase: str = "", current_state: Optional[Mapping[str, Any]] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Advisory learned transitions; every recommendation requires live re-proof."""
        try:
            wm = getattr(getattr(self, "semantic_action_gate", None), "world_model", None)
            return wm.recommend_actions(phase=phase or self._active_phase_name or "standalone", current_state=current_state, limit=limit) if wm is not None else []
        except Exception:
            return []

    async def _semantic_action_preflight(
        self, *, action: str, locator: Locator, selector: str = "", label: str = "",
        action_id: str = "", expected_value: Any = None,
    ) -> Dict[str, Any]:
        """Prove one semantic target through DOM + both MCPs before execution."""
        gate = getattr(self, "semantic_action_gate", None)
        if gate is None or not bool(getattr(gate, "enabled", False)):
            return {"pass": True, "status": "disabled", "confidence": 1.0, "candidate": {}}
        hip_mcp = getattr(getattr(self, "agentq_controller", None), "mcp_backend", None)
        intended_label = label or selector or action
        risk = classify_action_risk(
            intended_label,
            structural_opener=bool((self._portal_mutation_authorization or {}).get("structural_opener")),
        )
        result = await gate.resolve(
            page=self.page,
            locator=locator,
            action=action,
            selector=selector,
            label=intended_label,
            phase=self._active_phase_name or "standalone",
            playwright_mcp=self.playwright_mcp_backend,
            devtools_mcp=self.mcp_backend,
            hip_intelligence_mcp=hip_mcp,
            vision_runtime=self.vision_runtime,
        )
        result["action_risk"] = risk
        result["mutation_authorized"] = bool((self._portal_mutation_authorization or {}).get("enabled"))
        # Medium-confidence targets are re-observed.  Lower-but-plausible targets
        # receive one explicit browser_find-first rediscovery pass.  Neither path
        # performs a physical action before the target is re-proven.
        recovery_status = str(result.get("status") or "")
        if not result.get("pass") and recovery_status in {"reobserve", "rediscover"}:
            try:
                if recovery_status == "rediscover" and self.playwright_mcp_backend is not None:
                    await self.playwright_mcp_backend.find(text=intended_label)
                    await asyncio.sleep(0.25)
                else:
                    await asyncio.sleep(0.15)
                result = await gate.resolve(
                    page=self.page, locator=locator, action=action, selector=selector,
                    label=intended_label, phase=self._active_phase_name or "standalone",
                    playwright_mcp=self.playwright_mcp_backend, devtools_mcp=self.mcp_backend,
                    hip_intelligence_mcp=hip_mcp, vision_runtime=self.vision_runtime,
                )
                result["action_risk"] = risk
                result["mutation_authorized"] = bool((self._portal_mutation_authorization or {}).get("enabled"))
                result["reobserved"] = recovery_status == "reobserve"
                result["rediscovered"] = recovery_status == "rediscover"
            except Exception as exc:
                result = {
                    **dict(result or {}),
                    "reobserved": recovery_status == "reobserve",
                    "rediscovered": recovery_status == "rediscover",
                    "reobserve_error": mask_sensitive_string(str(exc))[:800],
                    "action_risk": risk,
                }
        live_view = getattr(self, "agent_live_view", None)
        if live_view is not None:
            try:
                await live_view.record_selection(
                    page=self.page, locator=locator, action_id=action_id,
                    phase=self._active_phase_name or "standalone", action=action,
                    intent=intended_label, expected_value=expected_value, resolution=result,
                )
            except Exception:
                pass
        if not result.get("pass"):
            raise RuntimeError(
                "HIP_SEMANTIC_ACTION_BLOCKED: "
                + str(result.get("reason") or result.get("status") or "semantic target not proven")
            )
        return result

    async def _semantic_dispatch_target(
        self, *, resolution: Dict[str, Any], locator: Locator, selector: str
    ) -> tuple[Locator, str, Dict[str, Any]]:
        """Re-prove the fingerprint after Angular/DDS rerender and rebind safely."""
        gate = getattr(self, "semantic_action_gate", None)
        cfg = getattr(self.config, "semantic_understanding", None)
        if gate is None or not resolution.get("semantic_control_id") or not bool(getattr(cfg, "revalidate_before_dispatch", True)):
            return locator, selector, {"pass": True, "status": "not_required"}
        proof = await gate.revalidate(page=self.page, resolution=resolution)
        if not proof.get("pass"):
            raise RuntimeError(
                f"HIP_SEMANTIC_TARGET_DRIFT: {proof.get('status')} match_count={proof.get('match_count')}"
            )
        candidate = proof.get("candidate") if isinstance(proof.get("candidate"), dict) else {}
        fresh_selector = str(candidate.get("selector") or "")
        use_fresh = bool(
            getattr(cfg, "prefer_stable_semantic_selector_for_mcp", True)
            and fresh_selector
            and not bool(candidate.get("selector_generation_volatile"))
        )
        if use_fresh:
            try:
                fresh = self.page.locator(fresh_selector).first
                if await fresh.count():
                    return fresh, fresh_selector, proof
            except Exception:
                pass
        return locator, selector, proof

    async def _semantic_post_action_verify(
        self, *, resolution: Dict[str, Any], action: str, exact_value_verified: bool = False
    ) -> Dict[str, Any]:
        gate = getattr(self, "semantic_action_gate", None)
        if gate is None or not resolution.get("semantic_control_id"):
            return {"pass": True, "status": "not_required", "confidence": 1.0}
        effect = await gate.verify_and_learn(
            page=self.page,
            resolution=resolution,
            action=action,
            phase=self._active_phase_name or "standalone",
            exact_value_verified=exact_value_verified,
        )
        if bool(getattr(gate, "require_effect", True)) and not effect.get("pass"):
            raise RuntimeError(
                "HIP_SEMANTIC_EFFECT_NOT_PROVEN: "
                + str(effect.get("effect_type") or "no semantic state change")
            )
        return effect

    async def _try_pyautogui_click(self, *, action: str, locator: Locator, selector: str, dom_cursor: Dict[str, int]) -> Dict[str, Any]:
        tool = getattr(self, "pyautogui_tool", None)
        if tool is None or not tool.enabled() or not self.page:
            return {"pass": False, "reason": "pyautogui_unavailable", "dispatch_attempted": False}
        dispatched = False
        result: Dict[str, Any] = {}
        try:
            result = await tool.click_locator(self.page, locator, action=action, selector=selector)
            dispatched = bool(result.get("pass"))
            verify = {"pass": True, "events": 0}
            if bool(getattr(getattr(self.config, "pyautogui", None), "verify_after_click_event", True)):
                window = await self.collect_dom_event_window(dom_cursor)
                events = window.get("events") or []
                trusted_clicks = [e for e in events if str(e.get("type") or "").lower() == "click" and bool(e.get("trusted"))]
                verify = {"pass": bool(trusted_clicks), "events": len(events), "trusted_clicks": len(trusted_clicks)}
                if not verify["pass"]:
                    raise RuntimeError("PyAutoGUI click did not produce a trusted browser click event on the resolved HIP surface.")
            safe_write_json(self.run_dir / "pyautogui" / f"click_verify_{len(self.action_events):05d}.json", {"result": result, "verification": verify})
            return {"pass": True, "result": result, "verification": verify, "dispatch_attempted": dispatched}
        except Exception as exc:
            return {"pass": False, "reason": mask_sensitive_string(str(exc)), "dispatch_attempted": dispatched, "result": result}

    async def _try_pyautogui_fill(self, *, locator: Locator, value: str, selector: str) -> Dict[str, Any]:
        tool = getattr(self, "pyautogui_tool", None)
        if tool is None or not tool.enabled() or not self.page:
            return {"pass": False, "reason": "pyautogui_unavailable"}
        try:
            result = await tool.fill_locator(self.page, locator, value, selector=selector)
            verify = await self._verify_exact_fill_commit(locator, value, selector=selector)
            safe_write_json(self.run_dir / "pyautogui" / f"fill_verify_{len(self.action_events):05d}.json", {"result": result, "verification": verify})
            if not verify.get("pass"):
                raise RuntimeError("PyAutoGUI fill did not remain exactly committed in the resolved HIP field.")
            return {"pass": True, "result": result, "verification": verify}
        except Exception as exc:
            return {"pass": False, "reason": mask_sensitive_string(str(exc))}

    async def _try_pyautogui_press(self, *, key: str, selector: str) -> Dict[str, Any]:
        tool = getattr(self, "pyautogui_tool", None)
        if tool is None or not tool.enabled() or not self.page:
            return {"pass": False, "reason": "pyautogui_unavailable"}
        try:
            result = await tool.press_key(self.page, key, selector=selector)
            return {"pass": True, "result": result}
        except Exception as exc:
            return {"pass": False, "reason": mask_sensitive_string(str(exc))}

    async def click_visual_structural_target(
        self, *, label: str, phase: str = "", context: str = ""
    ) -> Dict[str, Any]:
        """High-confidence visual recovery for structural controls such as + Add.

        This path exists specifically for cases where the HIP SPA renders a visible
        control that cannot be resolved reliably through the accessibility/DOM
        surface. Vision only LOCATES the target; PyAutoGUI MCP performs the physical
        click; the browser DOM event stream and the caller verify the effect.
        Mutating Save/Create/Delete/Deploy actions are never accepted here.
        """
        cfg = getattr(self.config, "pyautogui", None)
        tool = getattr(self, "pyautogui_tool", None)
        vision = getattr(self, "vision_runtime", None)
        if (
            cfg is None
            or not bool(getattr(cfg, "visual_structural_recovery_enabled", True))
            or tool is None
            or not tool.enabled()
            or vision is None
            or self.page is None
        ):
            return {"pass": False, "reason": "visual_structural_recovery_unavailable"}

        evidence_context: Dict[str, Any] = {}
        try:
            evidence_context = await self.browser_use_recovery_context(max_elements=80)
        except Exception as exc:
            evidence_context = {"available": False, "error": mask_sensitive_string(str(exc))[:600]}

        try:
            visual = await vision.locate_visual_target(
                page=self.page,
                target_label=label,
                target_kind="structural button",
                context=(
                    f"phase={phase}; {context}; Browser-Use recovery context: "
                    f"{json.dumps(mask_sensitive_data(evidence_context), ensure_ascii=False)[:3000]}"
                ),
            )
        except Exception as exc:
            return {"pass": False, "reason": "vision_locator_failed", "error": mask_sensitive_string(str(exc))[:1000]}

        threshold = float(getattr(cfg, "visual_target_confidence_threshold", 0.94))
        confidence = float(visual.get("confidence") or 0.0)
        if not visual.get("target_visible") or confidence < threshold:
            return {
                "pass": False,
                "reason": "visual_target_not_proven",
                "threshold": threshold,
                "visual": mask_sensitive_data(visual),
                "browser_use": mask_sensitive_data(evidence_context),
            }

        cursor = await self.mark_dom_event_cursor()
        try:
            result = await tool.click_viewport_ratio(
                self.page,
                x_ratio=float(visual.get("x_ratio")),
                y_ratio=float(visual.get("y_ratio")),
                action=f"structural_opener visual_recovery {phase} {label}",
                evidence=visual,
            )
            await self.wait_ready()
            window = await self.collect_dom_event_window(cursor)
            events = window.get("events") or []
            trusted = [
                e for e in events
                if str(e.get("type") or "").lower() == "click" and bool(e.get("trusted"))
            ]
            verify_required = bool(getattr(cfg, "verify_after_click_event", True))
            passed = bool(result.get("pass")) and (bool(trusted) if verify_required else True)
            payload = {
                "pass": passed,
                "executor": str(result.get("executor") or "pyautogui-mcp"),
                "visual": mask_sensitive_data(visual),
                "browser_use": mask_sensitive_data(evidence_context),
                "verification": {"trusted_clicks": len(trusted), "events": len(events)},
                "result": mask_sensitive_data(result),
            }
            safe_write_json(
                self.run_dir / "pyautogui" / f"visual_structural_{len(self.action_events):05d}.json",
                payload,
            )
            return payload
        except Exception as exc:
            return {
                "pass": False,
                "reason": "visual_structural_click_failed",
                "error": mask_sensitive_string(str(exc))[:1000],
                "visual": mask_sensitive_data(visual),
            }

    async def _active_semantic_surface_lease(self) -> Dict[str, Any]:
        """Return the deepest still-proven nested surface, pruning closed children.

        A child surface may disappear after Save/Back/Close.  We pop only surfaces
        whose continuity lease can no longer be proven, allowing the caller to
        safely return to the still-visible proven parent when one exists.
        """
        chain = getattr(self, "_semantic_surface_chain", None)
        if chain is None:
            self._semantic_surface_chain = []
            chain = self._semantic_surface_chain
        history: List[Dict[str, Any]] = []
        while chain:
            proof = dict(chain[-1] or {})
            if not self.page:
                return {"active": False, "reason": "page unavailable", "pruned": history}
            proof_url = str(proof.get("page_url") or "")
            current_url = str(getattr(self.page, "url", "") or "")
            if proof_url and current_url and not self._surface_url_matches(proof_url, current_url):
                history.append({
                    "depth": int(proof.get("chain_depth") or len(chain)),
                    "rebound": False,
                    "reason": "surface provenance invalidated by SPA/page route change",
                    "selector": proof.get("selector"),
                    "score": None,
                })
                chain.clear()
                return {
                    "active": False,
                    "reason": "nested surface provenance belongs to a different page route",
                    "route_invalidated": True,
                    "pruned": history,
                }
            lease = await refresh_affordance_surface_lease(self.page, proof)
            history.append({
                "depth": int(proof.get("chain_depth") or len(chain)),
                "rebound": bool(lease.get("rebound")),
                "reason": lease.get("reason"),
                "selector": lease.get("selector"),
                "score": lease.get("score"),
            })
            if lease.get("rebound"):
                updated = dict(lease.get("surface_proof") or proof)
                chain[-1] = updated
                return {
                    "active": True,
                    "selector": str(lease.get("selector") or updated.get("selector") or ""),
                    "surface_proof": updated,
                    "depth": int(updated.get("chain_depth") or len(chain)),
                    "chain_length": len(chain),
                    "rebind": {k: v for k, v in lease.items() if k != "snapshot"},
                    "pruned": history[:-1],
                }
            chain.pop()
        return {"active": False, "reason": "no proven nested surface remains", "pruned": history}

    def _push_semantic_surface_proof(self, proof: Dict[str, Any]) -> Dict[str, Any]:
        chain = getattr(self, "_semantic_surface_chain", None)
        if chain is None:
            self._semantic_surface_chain = []
            chain = self._semantic_surface_chain
        p = dict(proof or {})
        if self.page and not p.get("page_url"):
            p["page_url"] = self._evidence_url(str(getattr(self.page, "url", "") or ""))
        if not p.get("selector"):
            return {"pushed": False, "reason": "child surface proof has no selector", "chain_length": len(chain)}
        depth = int(p.get("chain_depth") or (len(chain) + 1))
        if depth > MAX_SURFACE_CHAIN_DEPTH or len(chain) >= MAX_SURFACE_CHAIN_DEPTH:
            return {"pushed": False, "reason": "surface ancestry depth limit reached", "chain_length": len(chain)}
        # Do not duplicate the same proven surface if a rerender changed only its
        # selector. Stable id + kind is preferred; selector + kind is the fallback.
        if chain:
            top = dict(chain[-1] or {})
            same_id = bool(p.get("id") and top.get("id") and str(p.get("id")) == str(top.get("id")))
            same_selector = str(p.get("selector") or "") == str(top.get("selector") or "")
            same_kind = str(p.get("kind") or "") == str(top.get("kind") or "")
            if (same_id and same_kind) or (same_selector and same_kind):
                chain[-1] = p
                return {"pushed": False, "updated_top": True, "chain_length": len(chain), "depth": depth}
        chain.append(p)
        return {"pushed": True, "chain_length": len(chain), "depth": depth}

    async def _prune_semantic_surface_chain(self) -> Dict[str, Any]:
        active = await self._active_semantic_surface_lease()
        return {
            "active": bool(active.get("active")),
            "depth": active.get("depth"),
            "chain_length": len(getattr(self, "_semantic_surface_chain", []) or []),
            "pruned": active.get("pruned") or [],
            "reason": active.get("reason"),
        }

    async def resolve_semantic_affordance(
        self, *, intent: str, aliases: Optional[List[str]] = None, allow_mutation: bool = False
    ) -> Dict[str, Any]:
        """Resolve icon/text/ARIA affordances on the current HIP surface.

        This is the generalized portal-option binder used when a control may be
        represented only by ``+``, a chevron, ellipsis/kebab, pencil, copy, migrate
        or deploy icon. Mutation affordances remain subject to the normal governance
        gate at execution time.
        """
        if not self.page:
            return {"resolved": False, "reason": "page unavailable", "intent": intent}
        return await resolve_semantic_affordance(
            self.page, intent=intent, aliases=list(aliases or []), allow_mutation=allow_mutation
        )

    async def click_semantic_affordance(
        self, *, intent: str, aliases: Optional[List[str]] = None, allow_mutation: bool = False,
        action_label: str = "", require_effect: bool = True, allow_compound_menu: bool = True
    ) -> Dict[str, Any]:
        """Resolve, click and structurally verify one generalized HIP affordance.

        The control is re-resolved immediately before execution so Angular/DDS
        rerenders cannot invalidate an earlier nth-of-type/dynamic selector. For
        row actions commonly hidden behind an overflow control, a scoped
        ``More Actions -> target action`` traversal is attempted only when the
        overflow candidate is semantically tied to the supplied entity/section
        aliases. Mutation governance still applies to the target action.
        """
        if not self.page:
            raise RuntimeError("HIP semantic affordance click requires an active page")
        aliases = list(aliases or [])
        canonical = canonical_intent(intent)
        compound_evidence: Dict[str, Any] = {}
        active_chain_evidence: Dict[str, Any] = {}
        target_surface_selector = ""
        target_surface_proof: Dict[str, Any] = {}

        # If a prior proven action opened a dialog/drawer/menu, continuation
        # actions must remain inside the deepest still-proven child surface. This
        # prevents a global Save/Create/Next/Deploy button from stealing focus while
        # the intended nested HIP surface is active.
        if prefers_active_surface_chain(canonical):
            active = await self._active_semantic_surface_lease()
            active_chain_evidence = active
            if active.get("active"):
                target_surface_selector = str(active.get("selector") or "")
                target_surface_proof = dict(active.get("surface_proof") or {})
                resolution = await resolve_affordance_in_proven_surface(
                    self.page, intent=canonical, surface_selector=target_surface_selector,
                    surface_proof=target_surface_proof, allow_mutation=allow_mutation,
                    max_scrolls=8, stable_reads=2,
                )
                if not resolution.get("resolved"):
                    raise RuntimeError(
                        f"HIP_ACTIVE_SURFACE_CONTINUATION_UNRESOLVED: {canonical}: "
                        f"{resolution.get('reason') or 'target not found inside active proven surface'}"
                    )
            else:
                resolution = await self.resolve_semantic_affordance(
                    intent=canonical, aliases=aliases, allow_mutation=allow_mutation
                )
        else:
            resolution = await self.resolve_semantic_affordance(
                intent=canonical, aliases=aliases, allow_mutation=allow_mutation
            )

        if (
            not resolution.get("resolved")
            and allow_compound_menu
            and requires_compound_menu_fallback(canonical)
        ):
            menu_resolution = await self.resolve_semantic_affordance(
                intent="more_actions", aliases=aliases, allow_mutation=False
            )
            # A global kebab/ellipsis is unsafe. For compound traversal require
            # positive entity/section alias evidence before opening the menu.
            if not menu_resolution.get("resolved") or not menu_resolution.get("alias_hits"):
                raise RuntimeError(
                    f"HIP_COMPOUND_AFFORDANCE_MENU_UNRESOLVED: {canonical}: "
                    f"{menu_resolution.get('reason') or 'overflow menu not locally scoped'}"
                )
            menu_selector = str(menu_resolution.get("selector") or "")
            # Re-resolve once immediately before clicking to survive a generation
            # change between inventory and execution.
            menu_fresh = await self.resolve_semantic_affordance(
                intent="more_actions", aliases=aliases, allow_mutation=False
            )
            if not menu_fresh.get("resolved") or not menu_fresh.get("alias_hits"):
                raise RuntimeError("HIP_COMPOUND_AFFORDANCE_MENU_STALE: scoped menu disappeared before click")
            menu_selector = str(menu_fresh.get("selector") or menu_selector)
            before_menu = await snapshot_affordance_surface(self.page, selector=menu_selector)
            await self.click_and_wait(
                action=str(menu_fresh.get("label") or "More Actions"),
                locator=self.page.locator(menu_selector).first, selector=menu_selector
            )
            await asyncio.sleep(0.18)
            after_menu = await snapshot_affordance_surface(self.page, selector=menu_selector)
            menu_effect = verify_affordance_effect(
                intent="more_actions", expected_effect="menu_or_popover_opened",
                before=before_menu, after=after_menu
            )
            if not menu_effect.get("pass"):
                raise RuntimeError(f"HIP_COMPOUND_AFFORDANCE_MENU_EFFECT_NOT_PROVEN: {menu_effect}")
            surface_binding = bind_new_affordance_surface(
                before=before_menu, after=after_menu,
                opener_candidate=dict(menu_fresh.get("candidate") or {}),
            )
            compound_evidence = {
                "menu_resolution": menu_fresh, "menu_before": before_menu,
                "menu_after": after_menu, "menu_effect": menu_effect,
                "surface_binding": surface_binding,
            }
            # A detached CDK/DDS overlay often has no row/entity alias in its own
            # DOM ancestry. Transfer trust from the already-scoped opener only to
            # the *new surface proven to have appeared because of that click*.
            target_surface_selector = str(surface_binding.get("selector") or "") if surface_binding.get("bound") else ""
            if target_surface_selector:
                resolution = await resolve_affordance_in_proven_surface(
                    self.page, intent=canonical, surface_selector=target_surface_selector,
                    surface_proof=dict(surface_binding.get("surface_proof") or {}),
                    allow_mutation=allow_mutation, max_scrolls=8, stable_reads=2,
                )
                compound_evidence["surface_target_resolution"] = resolution
            else:
                # Compatibility fallback: retain the v1.9.3 behavior, but only if
                # the target itself still carries positive alias evidence.
                resolution = await self.resolve_semantic_affordance(
                    intent=canonical, aliases=aliases, allow_mutation=allow_mutation
                )
                if resolution.get("resolved") and aliases and not resolution.get("alias_hits"):
                    resolution = {
                        "resolved": False, "intent": canonical,
                        "reason": "detached overlay provenance unavailable and target lacks local alias evidence",
                    }

        if not resolution.get("resolved"):
            raise RuntimeError(f"HIP_SEMANTIC_AFFORDANCE_UNRESOLVED: {resolution.get('reason') or canonical}")

        # Generation-safe just-in-time semantic rebinding. If the target lives in
        # a proven detached surface, keep the re-resolution inside that surface;
        # never fall back to a similarly named global action.
        target_surface_proof = dict(resolution.get("surface_provenance") or target_surface_proof or {})
        target_surface_selector = str(
            target_surface_proof.get("selector")
            or target_surface_selector
            or ""
        )
        if target_surface_selector:
            fresh = await resolve_affordance_in_proven_surface(
                self.page, intent=canonical, surface_selector=target_surface_selector,
                surface_proof=target_surface_proof, allow_mutation=allow_mutation,
                max_scrolls=0, stable_reads=1,
            )
        else:
            fresh = await self.resolve_semantic_affordance(
                intent=canonical, aliases=aliases, allow_mutation=allow_mutation
            )
        if not fresh.get("resolved"):
            raise RuntimeError(f"HIP_SEMANTIC_AFFORDANCE_STALE_BEFORE_CLICK: {canonical}")
        if (not target_surface_selector) and aliases and resolution.get("alias_hits") and not fresh.get("alias_hits"):
            raise RuntimeError(f"HIP_SEMANTIC_AFFORDANCE_SCOPE_LOST_BEFORE_CLICK: {canonical}")
        resolution = fresh
        selector = str(resolution.get("selector") or "")
        if not selector:
            raise RuntimeError("HIP_SEMANTIC_AFFORDANCE_UNRESOLVED: candidate has no selector")
        target_membership: Dict[str, Any] = {}
        surface_lease_active = bool((resolution.get("surface_provenance") or {}).get("lease_revalidated"))
        if target_surface_selector and surface_lease_active:
            target_membership = await verify_affordance_target_membership(
                self.page, target_selector=selector,
                surface_proof=dict(resolution.get("surface_provenance") or target_surface_proof),
            )
            if not target_membership.get("pass"):
                raise RuntimeError(
                    f"HIP_SEMANTIC_AFFORDANCE_SURFACE_MEMBERSHIP_LOST: {canonical}: "
                    f"{target_membership.get('reason') or 'target left proven surface'}"
                )
        locator = self.page.locator(selector).first
        before = await snapshot_affordance_surface(self.page, selector=selector)
        await self.click_and_wait(
            action=action_label or str(resolution.get("label") or canonical), locator=locator, selector=selector
        )
        await asyncio.sleep(0.18)
        after = await snapshot_affordance_surface(self.page, selector=selector)
        effect = verify_affordance_effect(
            intent=canonical, expected_effect=str(resolution.get("expected_effect") or "structural_change"),
            before=before, after=after
        )

        # Carry provenance across nested surfaces.  If this exact action opened a
        # new dialog/drawer/menu, bind only that structural delta and push it as the
        # active child.  If no child opened, prune any surface that closed as a
        # result of Save/Back/Close so the proven parent becomes active again.
        parent_proof_for_child = dict(
            resolution.get("surface_provenance")
            or target_surface_proof
            or (active_chain_evidence.get("surface_proof") if isinstance(active_chain_evidence, dict) else {})
            or {}
        )
        child_surface_binding = bind_child_affordance_surface(
            parent_proof=parent_proof_for_child,
            before=before,
            after=after,
            opener_candidate=dict(resolution.get("candidate") or {}),
        )
        surface_chain_update: Dict[str, Any]
        if child_surface_binding.get("bound"):
            surface_chain_update = self._push_semantic_surface_proof(
                dict(child_surface_binding.get("surface_proof") or {})
            )
        else:
            surface_chain_update = await self._prune_semantic_surface_chain()

        evidence = {
            "resolution": resolution, "before": before, "after": after, "effect": effect,
            "compound_menu": compound_evidence or None,
            "generation_safe_reresolution": True,
            "detached_overlay_provenance": bool(target_surface_selector),
            "surface_continuity_lease": bool(target_surface_selector and surface_lease_active),
            "target_surface_membership": target_membership or None,
            "active_surface_chain": active_chain_evidence or None,
            "child_surface_binding": child_surface_binding if child_surface_binding.get("bound") else None,
            "surface_chain_update": surface_chain_update,
            "nested_surface_ancestry_chain": True,
        }
        try:
            safe_write_json(
                self.run_dir / "browser_intelligence" / "semantic_affordances" / f"{len(self.action_events):05d}_{canonical}.json",
                mask_sensitive_data(evidence),
            )
        except Exception:
            pass
        if require_effect and canonical_effect_required(canonical) and not effect.get("pass"):
            raise RuntimeError(f"HIP_SEMANTIC_AFFORDANCE_EFFECT_NOT_PROVEN: {canonical}: {effect}")
        return mask_sensitive_data(evidence)

    def last_click_dispatch_evidence(self) -> Dict[str, Any]:
        return mask_sensitive_data(dict(self._last_click_dispatch or {}))

    def mutation_dispatch_guard_status(self) -> Dict[str, Any]:
        row = dict(getattr(self, "_mutation_quarantine", {}) or {})
        return mask_sensitive_data({
            "active": bool(row.get("active")),
            "classification": str(row.get("classification") or ""),
            "action_hash": str(row.get("action_hash") or ""),
            "armed_at": str(row.get("armed_at") or ""),
            "dispatch_count": int(row.get("dispatch_count") or 0),
            "values_stored": False,
        })

    def resolve_mutation_dispatch_guard(self, outcome: Dict[str, Any] | None) -> Dict[str, Any]:
        outcome = dict(outcome or {})
        classification = str(outcome.get("classification") or "")
        resolved = classification in {
            "not_dispatched", "rejected_verified", "committed_verified",
            "committed_verified_after_transport_or_ui_error",
        }
        current = dict(getattr(self, "_mutation_quarantine", {}) or {})
        if resolved:
            self._mutation_quarantine = {}
        elif current.get("active"):
            current["classification"] = classification or "indeterminate_backend_outcome"
            current["last_reconciled_at"] = utc_now()
            self._mutation_quarantine = current
        return mask_sensitive_data({
            "cleared": bool(resolved),
            "retained": bool((self._mutation_quarantine or {}).get("active")),
            "classification": classification,
            "values_stored": False,
        })

    def _assert_mutation_dispatch_guard_clear(self) -> None:
        guard = dict(getattr(self, "_mutation_quarantine", {}) or {})
        if guard.get("active"):
            raise RuntimeError(
                "HIP_MUTATION_QUARANTINE_ACTIVE: a prior mutation dispatch has not been authoritatively reconciled; "
                "no additional portal mutation may be dispatched in this browser session"
            )

    def _mark_click_dispatch(self, *, action: str, selector: str, executor: str, mutation_risk: bool) -> None:
        row = dict(self._last_click_dispatch or {})
        row.update({
            "action": str(action or ""),
            "selector": str(selector or ""),
            "mutation_risk": bool(mutation_risk),
            "dispatch_attempted": True,
            "dispatch_count": int(row.get("dispatch_count") or 0) + 1,
            "executor": str(executor or ""),
            "dispatch_returned": False,
            "phase": "dispatching",
            "dispatch_timestamp": utc_now(),
            "dispatch_page_url": self._evidence_url(str(self.page.url if self.page else "")),
            "dispatch_stage": str(self._current_stage or ""),
            "network_event_index_at_dispatch": len(self.network_tab_events),
            "network_request_ids_at_dispatch": list(self._cdp_requests.keys()),
        })
        self._last_click_dispatch = row
        if mutation_risk:
            self._mutation_quarantine = {
                "active": True,
                "classification": "awaiting_reconciliation",
                "action_hash": hashlib.sha256(str(action or "").encode("utf-8", errors="ignore")).hexdigest()[:20],
                "armed_at": utc_now(),
                "dispatch_count": int(row.get("dispatch_count") or 0),
                "values_stored": False,
            }

    def _mark_click_dispatch_returned(self) -> None:
        row = dict(self._last_click_dispatch or {})
        row.update({"dispatch_returned": True, "phase": "dispatch_returned"})
        self._last_click_dispatch = row

    async def mutation_ui_signal(self, *, action: str = "", entity: str = "") -> Dict[str, Any]:
        '''Return value-free visible mutation outcome hints.

        Raw toast/dialog text is deliberately not persisted. Only keyword classes
        and hashes are returned so reconciliation cannot leak customer-entered
        values into long-term evidence.
        '''
        if not self.page:
            return {"success": False, "error": False, "signal_count": 0, "signal_hashes": []}
        try:
            rows = await self.page.evaluate(
                r'''() => {
                  const visible = el => {
                    if (!el || !el.isConnected) return false;
                    const s=getComputedStyle(el), r=el.getBoundingClientRect();
                    return s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||1)!==0 && r.width>0 && r.height>0;
                  };
                  const selectors='[role="alert"],[role="status"],[aria-live],.dds__toast,[class*="toast"],[class*="notification"],[class*="snackbar"],[class*="success"],[class*="error"]';
                  return [...document.querySelectorAll(selectors)].filter(visible).slice(0,80).map(el => ({
                    text:String(el.innerText||el.textContent||el.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim().slice(0,1000),
                    role:String(el.getAttribute('role')||''),
                    live:String(el.getAttribute('aria-live')||''),
                    classes:String(el.className||'').slice(0,400)
                  }));
                }'''
            )
        except Exception as exc:
            return {"success": False, "error": False, "signal_count": 0, "signal_hashes": [], "error_observing": mask_sensitive_string(str(exc))}
        success_words = {"success", "successful", "successfully", "created", "saved", "deployed", "updated", "published", "completed", "complete"}
        error_words = {"error", "failed", "failure", "invalid", "unable", "denied", "rejected"}
        action_tokens = {x for x in re.findall(r"[a-z0-9]+", str(action or "").lower()) if len(x) >= 3}
        entity_tokens = {x for x in re.findall(r"[a-z0-9]+", str(entity or "").lower()) if len(x) >= 4}
        success_hits = set(); error_hits = set(); hashes = []
        for row in rows or []:
            text = " ".join(str(row.get(k) or "") for k in ("text", "role", "live", "classes")).lower()
            words = set(re.findall(r"[a-z0-9]+", text))
            sh = success_words & words
            eh = error_words & words
            correlated = bool((action_tokens | entity_tokens) & words) or bool(sh) or bool(eh)
            if not correlated:
                continue
            success_hits.update(sh); error_hits.update(eh)
            hashes.append(hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:20])
        return {
            "success": bool(success_hits and not error_hits),
            "error": bool(error_hits),
            "success_keywords": sorted(success_hits),
            "error_keywords": sorted(error_hits),
            "signal_count": len(hashes),
            "signal_hashes": hashes[:20],
            "raw_text_stored": False,
        }

    def _infer_click_mutation_risk(self, action: str, mutation_risk: Optional[bool]) -> bool:
        if mutation_risk is not None:
            return bool(mutation_risk)
        action_text = str(action or "").strip().lower()
        inferred = (
            canonical_intent(action) in {"save", "create", "deploy", "delete", "migrate", "clone", "edit"}
            or bool(re.search(r"\b(save|create|submit|delete|remove|deploy|publish|update|enable|disable|confirm|migrate|clone)\b", action_text))
        )
        return bool(inferred and (self._portal_mutation_authorization or {}).get("enabled"))

    async def click_and_wait(self, *, action: str, locator: Locator, selector: str = "", screenshot_name: Optional[str] = None, mutation_risk: Optional[bool] = None) -> None:
        selector_hint = str(selector or "")
        dispatch_selector_meta: Dict[str, Any] = {"selector_hint": selector_hint, "selector": "", "strategy": "pending"}
        mutation_risk = self._infer_click_mutation_risk(action, mutation_risk)
        mutation_lock = None
        if mutation_risk:
            mutation_lock = getattr(self, "_mutation_dispatch_lock", None)
            if mutation_lock is None:
                self._mutation_dispatch_lock = asyncio.Lock()
                mutation_lock = self._mutation_dispatch_lock
            await mutation_lock.acquire()
            try:
                self._assert_mutation_dispatch_guard_clear()
            except Exception:
                mutation_lock.release()
                raise
        before_count = len(self.click_events)
        self._last_click_dispatch = {
            "action": str(action or ""), "selector": str(selector or ""),
            "mutation_risk": bool(mutation_risk), "dispatch_attempted": False,
            "dispatch_count": 0, "dispatch_returned": False, "executor": "",
            "phase": "pre_dispatch",
        }
        ev = await self._begin_action("click", selector or action, screenshot_before=False)
        dom_cursor = await self.mark_dom_event_cursor()
        autowebglm_decision: Dict[str, Any] = {}
        autowebglm_reward_recorded = False
        try:
            safe_click_details = await self._assert_safe_click(action=action, locator=locator, selector=selector)
            await self._sync_page_click_safety_context(action=action, selector=selector, details=safe_click_details)
            universal_audit = await universal_locator_preflight(
                self.page, locator, action=action or "click", selector=selector, timeout_ms=5000
            )
            safe_write_json(self.run_dir / "form_policy" / f"{ev.action_id}_preflight.json", universal_audit)
            if not universal_audit.get("pass"):
                raise RuntimeError(f"HIP_UNIVERSAL_FORM_POLICY_BLOCKED: {universal_audit.get('reason')}")
            dispatch_selector_meta = await self._canonical_selector_for_locator(locator, selector_hint)
            if dispatch_selector_meta.get("selector"):
                selector = str(dispatch_selector_meta["selector"])
            await self.ensure_interactable(action=action, selector=selector, timeout_ms=20000)
            semantic_resolution = await self._semantic_action_preflight(
                action="click", locator=locator, selector=selector, label=action or selector_hint or selector,
                action_id=ev.action_id, expected_value="",
            )
            autowebglm_decision = await self._autowebglm_primary_decision(
                action="click", selector=selector,
                label=str((semantic_resolution.get("candidate") or {}).get("label") or action or selector),
            )
            try:
                self.agent_live_view.record_planner(action_id=ev.action_id, decision=autowebglm_decision)
            except Exception:
                pass
            locator, selector, semantic_revalidation = await self._semantic_dispatch_target(
                resolution=semantic_resolution, locator=locator, selector=selector
            )
            dispatch_selector_meta = await self._canonical_selector_for_locator(locator, selector or selector_hint)
            if dispatch_selector_meta.get("selector"):
                selector = str(dispatch_selector_meta["selector"])
            if mutation_risk:
                # Revalidate the exact route/auth/locator at the last possible
                # pre-dispatch point. Angular can rerender or SSO can redirect
                # after the earlier preflight while AutoWebGLM is deciding.
                current_url = str(self.page.url if self.page else "")
                if self._is_sso_transition_url(current_url):
                    raise RuntimeError("HIP_MUTATION_PREDISPATCH_AUTH_DRIFT: Dell SSO became active before physical mutation dispatch")
                if self._active_target_url and not self._same_target_path(self._active_target_url, current_url):
                    raise RuntimeError("HIP_MUTATION_PREDISPATCH_ROUTE_DRIFT: active HIP route changed before physical mutation dispatch")
                final_preflight = await universal_locator_preflight(
                    self.page, locator, action=action or "mutation click", selector=selector, timeout_ms=2500
                )
                safe_write_json(self.run_dir / "form_policy" / f"{ev.action_id}_mutation_final_preflight.json", final_preflight)
                if not final_preflight.get("pass"):
                    raise RuntimeError(
                        f"HIP_MUTATION_PREDISPATCH_LOCATOR_DRIFT: {final_preflight.get('reason') or 'target no longer exact'}"
                    )
            try:
                await self.agent_live_view.record_acting(page=self.page, action_id=ev.action_id)
            except Exception:
                pass
            before_url = self.page.url if self.page else ""
            await self.log_automation_click(action=action, locator=locator, selector=selector, screenshot_name=screenshot_name, extra={"before_url": before_url})
            executed_by_mcp = False
            mcp_error: Optional[str] = None
            executed_by_pyautogui = False
            pyautogui_executor = ""
            pyautogui_error: Optional[str] = None
            py_cfg = getattr(self.config, "pyautogui", None)
            py_primary = bool(
                getattr(self, "pyautogui_tool", None)
                and self.pyautogui_tool.should_primary("click", mutation=bool(mutation_risk))
            )

            # V228: the first physical interaction is PyAutoGUI MCP. The DOM,
            # semantic-control gate, policy preflight and AutoWebGLM plan above are
            # evidence/targeting layers only; they do not mutate the browser.
            if py_primary:
                desktop = await self._try_pyautogui_click(
                    action=action, locator=locator, selector=selector, dom_cursor=dom_cursor
                )
                if desktop.get("pass"):
                    pyautogui_executor = str(
                        ((desktop.get("result") or {}).get("executor")) or "pyautogui-mcp-primary"
                    )
                    self._mark_click_dispatch(
                        action=action, selector=selector, executor=pyautogui_executor,
                        mutation_risk=mutation_risk,
                    )
                    self._mark_click_dispatch_returned()
                    executed_by_pyautogui = True
                else:
                    pyautogui_error = str(desktop.get("reason") or "PyAutoGUI primary click failed")
                    # Never replay a tenant mutation when the desktop channel may
                    # already have physically dispatched it but effect verification
                    # was inconclusive.
                    if mutation_risk and bool(desktop.get("dispatch_attempted")):
                        self._last_click_dispatch.update({
                            "action": str(action or ""),
                            "selector": str(selector or ""),
                            "mutation_risk": True,
                            "dispatch_attempted": True,
                            "dispatch_count": 1,
                            "dispatch_returned": False,
                            "executor": "pyautogui-mcp-primary",
                            "phase": "dispatch_outcome_unknown",
                            "error": pyautogui_error,
                        })
                        raise RuntimeError(f"HIP_MUTATION_DISPATCH_OUTCOME_UNKNOWN: {pyautogui_error}")

            # Playwright MCP is the deterministic browser fallback. It also remains
            # an independent DOM/effect witness even when PyAutoGUI executes.
            if (
                not executed_by_pyautogui
                and bool(getattr(py_cfg, "fallback_to_playwright_mcp", True))
                and self.playwright_mcp_backend is not None
                and bool(self._playwright_mcp_surface_healthy)
                and getattr(self.config.mcp, "playwright_mcp_primary_for_safe_actions", True)
                and self._mcp_safe_selector(selector)
            ):
                try:
                    await self.wait_ready()
                    await self.ensure_interactable(action=action, selector=selector, timeout_ms=20000)
                    self._mark_click_dispatch(
                        action=action, selector=selector, executor="playwright-mcp-fallback",
                        mutation_risk=mutation_risk,
                    )
                    await self.playwright_mcp_backend.click(selector, element=action or "HIP Portal control")
                    self._mark_click_dispatch_returned()
                    executed_by_mcp = True
                except Exception as exc:
                    mcp_error = mask_sensitive_string(str(exc))
                    if mutation_risk and bool((self._last_click_dispatch or {}).get("dispatch_attempted")):
                        self._last_click_dispatch.update({"phase": "dispatch_exception", "error": mcp_error})
                        raise RuntimeError(f"HIP_MUTATION_DISPATCH_OUTCOME_UNKNOWN: {mcp_error}") from exc
                    if (
                        "intercepts pointer events" in str(exc).lower()
                        or "loading-indicator" in str(exc).lower()
                        or "overlay" in str(exc).lower()
                    ):
                        try:
                            await self.ensure_interactable(action=action, selector=selector, timeout_ms=30000)
                            self._mark_click_dispatch(
                                action=action, selector=selector,
                                executor="playwright-mcp-fallback-retry",
                                mutation_risk=mutation_risk,
                            )
                            await self.playwright_mcp_backend.click(
                                selector, element=action or "HIP Portal control"
                            )
                            self._mark_click_dispatch_returned()
                            executed_by_mcp = True
                        except Exception as retry_exc:
                            mcp_error = mask_sensitive_string(
                                f"{exc}; overlay recovery retry failed: {retry_exc}"
                            )

            # Compatibility mode preserves the historical desktop-before-local
            # recovery path when interaction_mode=fallback.
            if not executed_by_pyautogui and not executed_by_mcp and not py_primary:
                py_recovery = bool(
                    py_cfg is not None
                    and getattr(py_cfg, "prefer_mcp_before_local_web_fallback", True)
                    and getattr(py_cfg, "use_for_structural_web_recovery", True)
                    and not mutation_risk
                )
                if py_recovery:
                    desktop = await self._try_pyautogui_click(
                        action=action, locator=locator, selector=selector, dom_cursor=dom_cursor
                    )
                    if desktop.get("pass"):
                        pyautogui_executor = str(
                            ((desktop.get("result") or {}).get("executor")) or "pyautogui-mcp-recovery"
                        )
                        self._mark_click_dispatch(
                            action=action, selector=selector, executor=pyautogui_executor,
                            mutation_risk=mutation_risk,
                        )
                        self._mark_click_dispatch_returned()
                        executed_by_pyautogui = True
                    else:
                        pyautogui_error = str(desktop.get("reason") or "desktop recovery failed")

            if not executed_by_pyautogui and not executed_by_mcp:
                if not bool(getattr(py_cfg, "fallback_to_python_playwright", True)):
                    raise RuntimeError(
                        "HIP_INTERACTION_EXECUTORS_EXHAUSTED: PyAutoGUI primary and Playwright MCP "
                        "fallback did not execute and Python Playwright fallback is disabled."
                    )
                last_error: Optional[Exception] = None
                for attempt in range(2):
                    await self.wait_ready()
                    await self.wait_for_blocking_overlays_gone(
                        timeout_ms=15000 if attempt == 0 else 30000,
                        target_selector=selector,
                    )
                    try:
                        self._mark_click_dispatch(
                            action=action, selector=selector,
                            executor="python-playwright-fallback",
                            mutation_risk=mutation_risk,
                        )
                        await locator.first.click(timeout=self.config.portal.timeout_ms)
                        self._mark_click_dispatch_returned()
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        self._last_click_dispatch.update({
                            "phase": "dispatch_exception",
                            "error": mask_sensitive_string(str(exc)),
                        })
                        if mutation_risk:
                            break
                        msg = str(exc).lower()
                        if attempt == 0 and (
                            "intercepts pointer events" in msg
                            or "loading-indicator" in msg
                            or "overlay" in msg
                        ):
                            await asyncio.sleep(1.0)
                            continue
                        break
                if last_error is not None:
                    raise last_error

            await self.wait_ready()
            await self.collect_dom_click_log()
            await self._mcp_snapshot_after_action(ev.action_id)
            after_url = self.page.url if self.page else ""
            if len(self.click_events) > before_count:
                self.click_events[before_count].extra = {
                    **(self.click_events[before_count].extra or {}),
                    "after_url": after_url,
                    "framework": "autowebglm_primary",
                    "autowebglm_decision_status": autowebglm_decision.get("status"),
                    "executor": pyautogui_executor if executed_by_pyautogui else ("playwright-mcp-fallback" if executed_by_mcp else "python-playwright-fallback"),
                    "playwright_mcp_error": mcp_error,
                    "pyautogui_error": pyautogui_error,
                    "click_dispatch_evidence": self.last_click_dispatch_evidence(),
                }
            await self.record_dom_transition(action_id=ev.action_id, action_type="click", target=selector or action, cursor=dom_cursor)
            semantic_effect = await self._semantic_post_action_verify(
                resolution=semantic_resolution, action="click", exact_value_verified=False
            )
            ev.execution_provenance = mask_sensitive_data({
                "planner": "autowebglm-primary",
                "planner_status": autowebglm_decision.get("status"),
                "planner_aligned": autowebglm_decision.get("aligned"),
                "primary_executor": "pyautogui-mcp",
                "actual_executor": pyautogui_executor if executed_by_pyautogui else ("playwright-mcp-fallback" if executed_by_mcp else "python-playwright-fallback"),
                "pyautogui_primary_requested": bool(py_primary),
                "pyautogui_mcp_available": bool(getattr(getattr(self, "pyautogui_tool", None), "available", lambda: False)()),
                "playwright_mcp_available": bool(self.playwright_mcp_backend is not None),
                "playwright_mcp_attempted": bool(self.playwright_mcp_backend is not None and self._playwright_mcp_surface_healthy and self._mcp_safe_selector(selector)),
                "playwright_mcp_succeeded": bool(executed_by_mcp),
                "playwright_mcp_post_action_snapshot": bool(self.playwright_mcp_backend is not None and getattr(self.config.mcp, "playwright_mcp_snapshot_after_action", True)),
                "selector_hint": selector_hint,
                "dispatch_selector": selector,
                "dispatch_selector_strategy": dispatch_selector_meta.get("strategy") or "",
                "fallback_reason": mcp_error or pyautogui_error or "",
                "semantic_control_id": semantic_resolution.get("semantic_control_id") or "",
                "semantic_confidence": semantic_resolution.get("confidence"),
                "semantic_margin": semantic_resolution.get("margin"),
                "semantic_gate_status": semantic_resolution.get("status") or "",
                "semantic_revalidation": semantic_revalidation.get("status") or "",
                "semantic_effect_pass": bool(semantic_effect.get("pass")),
                "semantic_effect_type": semantic_effect.get("effect_type") or "",
                "semantic_effect_confidence": semantic_effect.get("confidence"),
            })
            await self._observe_form_memory_action(
                action_type="click", selector=selector, audit=universal_audit, success=True
            )
            model_reward = self._record_autowebglm_primary_outcome(
                decision=autowebglm_decision, success=bool(semantic_effect.get("pass")),
                reward=1.0 if semantic_effect.get("pass") else 0.0,
                drift=not bool(semantic_effect.get("pass")),
            )
            autowebglm_reward_recorded = bool(model_reward.get("updated"))
            ev.execution_provenance["model_downstream_reward"] = mask_sensitive_data(model_reward)
            await self._finish_action(ev, True, screenshot_after=False)
            if mutation_lock is not None and mutation_lock.locked():
                mutation_lock.release()
        except Exception as exc:
            if not autowebglm_reward_recorded:
                self._record_autowebglm_primary_outcome(
                    decision=autowebglm_decision, success=False, reward=0.0, drift=True
                )
            await self._finish_action(ev, False, str(exc), screenshot_after=True)
            if mutation_lock is not None and mutation_lock.locked():
                mutation_lock.release()
            raise

    async def fill_and_log(self, *, locator: Locator, value: str, selector: str = "", action_type: str = "fill") -> None:
        selector_hint = str(selector or "")
        dispatch_selector_meta: Dict[str, Any] = {"selector_hint": selector_hint, "selector": "", "strategy": "pending"}
        action_name = "search" if action_type == "search" else "fill"
        ev = await self._begin_action(action_name, selector or action_type, value=value)
        dom_cursor = await self.mark_dom_event_cursor()
        semantic_resolution: Dict[str, Any] = {}
        semantic_revalidation: Dict[str, Any] = {"pass": True, "status": "not_required"}
        # Initialised here so an early preflight failure reports its real reason
        # instead of an UnboundLocalError from the exception handler.
        autowebglm_decision: Dict[str, Any] = {}
        autowebglm_reward_recorded = False
        try:
            universal_audit = await universal_locator_preflight(
                self.page, locator, action=action_type or "fill", selector=selector, timeout_ms=5000
            )
            safe_write_json(self.run_dir / "form_policy" / f"{ev.action_id}_preflight.json", universal_audit)
            if not universal_audit.get("pass"):
                raise RuntimeError(f"HIP_UNIVERSAL_FORM_POLICY_BLOCKED: {universal_audit.get('reason')}")

            dispatch_selector_meta = await self._canonical_selector_for_locator(locator, selector_hint)
            if dispatch_selector_meta.get("selector"):
                selector = str(dispatch_selector_meta["selector"])
            semantic_resolution = await self._semantic_action_preflight(
                action=action_name, locator=locator, selector=selector, label=selector_hint or selector or action_type,
                action_id=ev.action_id, expected_value=value,
            )

            # Idempotency is allowed only after the live semantic target is proven.
            if action_type != "search":
                existing = await self._read_locator_committed_value(locator)
                if self._exact_fill_value_matches(existing, value):
                    ev.target = f"{ev.target} [executor=already-committed]"
                    ev.execution_provenance = mask_sensitive_data({
                        "planner": "autowebglm-primary",
                        "planner_status": "idempotent_exact_commit",
                        "planner_aligned": True,
                        "primary_executor": "pyautogui-mcp",
                        "actual_executor": "already-committed",
                        "semantic_control_id": semantic_resolution.get("semantic_control_id") or "",
                        "semantic_confidence": semantic_resolution.get("confidence"),
                        "semantic_margin": semantic_resolution.get("margin"),
                        "semantic_gate_status": semantic_resolution.get("status") or "",
                        "semantic_reobserved": bool(semantic_resolution.get("reobserved")),
                    })
                    await self._observe_form_memory_action(action_type="fill", selector=selector, audit=universal_audit, success=True)
                    await self._finish_action(ev, True)
                    return

            locator, selector, semantic_revalidation = await self._semantic_dispatch_target(
                resolution=semantic_resolution, locator=locator, selector=selector
            )
            dispatch_selector_meta = await self._canonical_selector_for_locator(locator, selector or selector_hint)
            if dispatch_selector_meta.get("selector"):
                selector = str(dispatch_selector_meta["selector"])
            await self.ensure_interactable(action=action_type, selector=selector, timeout_ms=20000)
            autowebglm_decision = await self._autowebglm_primary_decision(
                action=action_name, selector=selector, label=selector or action_type, value=value,
            )
            try:
                self.agent_live_view.record_planner(action_id=ev.action_id, decision=autowebglm_decision)
            except Exception:
                pass
            try:
                await self.agent_live_view.record_acting(page=self.page, action_id=ev.action_id)
            except Exception:
                pass
            executed_by_mcp = False
            mcp_error: Optional[str] = None
            executed_by_pyautogui = False
            pyautogui_executor = ""
            pyautogui_error: Optional[str] = None
            py_cfg = getattr(self.config, "pyautogui", None)
            py_primary = bool(
                getattr(self, "pyautogui_tool", None)
                and self.pyautogui_tool.should_primary(action_name, mutation=False)
            )

            # V228: fill/search typing is physically performed by PyAutoGUI MCP
            # first, after semantic target proof. Exact DOM value verification is
            # still mandatory after the desktop action.
            if py_primary:
                desktop = await self._try_pyautogui_fill(
                    locator=locator, value=value, selector=selector
                )
                if desktop.get("pass"):
                    executed_by_pyautogui = True
                    pyautogui_executor = str(
                        ((desktop.get("result") or {}).get("executor")) or "pyautogui-mcp-primary"
                    )
                else:
                    pyautogui_error = str(desktop.get("reason") or "PyAutoGUI primary fill failed")

            if (
                not executed_by_pyautogui
                and bool(getattr(py_cfg, "fallback_to_playwright_mcp", True))
                and self.playwright_mcp_backend is not None
                and bool(self._playwright_mcp_surface_healthy)
                and getattr(self.config.mcp, "playwright_mcp_primary_for_safe_actions", True)
                and self._mcp_safe_selector(selector)
            ):
                try:
                    await self.playwright_mcp_backend.fill(
                        selector, value, element=selector or action_type, slowly=False
                    )
                    try:
                        actual = await locator.first.input_value(timeout=3000)
                    except Exception:
                        actual = ""
                    local_exact = actual.strip() == value.strip()
                    mcp_verify = await self.playwright_mcp_backend.verify_value(
                        selector, value, element=selector or action_type
                    )
                    executed_by_mcp = bool(local_exact and mcp_verify.get("pass"))
                    if not executed_by_mcp:
                        mcp_error = (
                            f"MCP exact-value verification failed: local={actual!r}, verify={mcp_verify}"
                        )
                except Exception as exc:
                    mcp_error = mask_sensitive_string(str(exc))

            # Historical recovery mode remains available when explicitly selected.
            if not executed_by_pyautogui and not executed_by_mcp and not py_primary:
                py_recovery = bool(
                    py_cfg is not None
                    and getattr(py_cfg, "prefer_mcp_before_local_web_fallback", True)
                    and getattr(py_cfg, "use_for_form_fill_recovery", True)
                )
                if py_recovery:
                    desktop = await self._try_pyautogui_fill(
                        locator=locator, value=value, selector=selector
                    )
                    if desktop.get("pass"):
                        executed_by_pyautogui = True
                        pyautogui_executor = str(
                            ((desktop.get("result") or {}).get("executor")) or "pyautogui-mcp-recovery"
                        )
                    else:
                        pyautogui_error = str(desktop.get("reason") or "desktop recovery failed")

            if not executed_by_pyautogui and not executed_by_mcp:
                if not bool(getattr(py_cfg, "fallback_to_python_playwright", True)):
                    raise RuntimeError(
                        "HIP_INTERACTION_EXECUTORS_EXHAUSTED: PyAutoGUI primary and Playwright MCP "
                        "fallback did not fill and Python Playwright fallback is disabled."
                    )
                await locator.first.fill(value)
                local_verify = await self._verify_exact_fill_commit(locator, value, selector=selector)
                if (
                    bool(getattr(py_cfg, "verify_after_fill", True))
                    and not local_verify.get("pass")
                    and action_type != "search"
                ):
                    raise RuntimeError(f"HIP_EXACT_FILL_NOT_COMMITTED: {local_verify}")

            exact_commit = await self._verify_exact_fill_commit(locator, value, selector=selector)
            if action_type != "search" and not exact_commit.get("pass"):
                raise RuntimeError(f"HIP_EXACT_FILL_NOT_COMMITTED: {exact_commit}")
            if getattr(self.config.mcp, "playwright_mcp_verify_every_action", True):
                await self._mcp_snapshot_after_action(ev.action_id)
            await self.record_dom_transition(action_id=ev.action_id, action_type=action_name, target=selector or action_type, cursor=dom_cursor)
            semantic_effect = await self._semantic_post_action_verify(
                resolution=semantic_resolution, action=action_name, exact_value_verified=bool(exact_commit.get("pass"))
            )

            executor = pyautogui_executor if executed_by_pyautogui else ("playwright-mcp-fallback" if executed_by_mcp else "python-playwright-fallback")
            ev.target = f"{ev.target} [framework=autowebglm-primary executor={executor} decision={autowebglm_decision.get('status')}]"
            fallback_notes = []
            if mcp_error:
                fallback_notes.append(f"MCP fallback reason: {mcp_error}")
            if pyautogui_error:
                fallback_notes.append(f"PyAutoGUI fallback reason: {pyautogui_error}")
            if fallback_notes:
                ev.error = "; ".join(fallback_notes)
            ev.execution_provenance = mask_sensitive_data({
                "planner": "autowebglm-primary",
                "planner_status": autowebglm_decision.get("status"),
                "planner_aligned": autowebglm_decision.get("aligned"),
                "primary_executor": "pyautogui-mcp",
                "actual_executor": executor,
                "pyautogui_primary_requested": bool(py_primary),
                "pyautogui_mcp_available": bool(getattr(getattr(self, "pyautogui_tool", None), "available", lambda: False)()),
                "playwright_mcp_available": bool(self.playwright_mcp_backend is not None),
                "playwright_mcp_attempted": bool(self.playwright_mcp_backend is not None and self._playwright_mcp_surface_healthy and self._mcp_safe_selector(selector)),
                "playwright_mcp_succeeded": bool(executed_by_mcp),
                "playwright_mcp_exact_value_verified": bool(executed_by_mcp),
                "selector_hint": selector_hint,
                "dispatch_selector": selector,
                "dispatch_selector_strategy": dispatch_selector_meta.get("strategy") or "",
                "fallback_reason": mcp_error or pyautogui_error or "",
                "semantic_control_id": semantic_resolution.get("semantic_control_id") or "",
                "semantic_confidence": semantic_resolution.get("confidence"),
                "semantic_margin": semantic_resolution.get("margin"),
                "semantic_gate_status": semantic_resolution.get("status") or "",
                "semantic_reobserved": bool(semantic_resolution.get("reobserved")),
                "semantic_revalidation": semantic_revalidation.get("status") or "",
                "semantic_effect_pass": bool(semantic_effect.get("pass")),
                "semantic_effect_type": semantic_effect.get("effect_type") or "",
                "semantic_effect_confidence": semantic_effect.get("confidence"),
                "exact_value_commit_verified": bool(exact_commit.get("pass")),
                "observed_value": exact_commit.get("second") if not ev.was_secret else "***MASKED***",
            })
            await self._observe_form_memory_action(
                action_type=action_name, selector=selector, audit=universal_audit, success=True
            )
            verified_outcome = bool(semantic_effect.get("pass")) and (action_type == "search" or bool(exact_commit.get("pass")))
            model_reward = self._record_autowebglm_primary_outcome(
                decision=autowebglm_decision, success=verified_outcome,
                reward=1.0 if verified_outcome else 0.0, drift=not verified_outcome,
            )
            autowebglm_reward_recorded = bool(model_reward.get("updated"))
            ev.execution_provenance["model_downstream_reward"] = mask_sensitive_data(model_reward)
            await self._finish_action(ev, True)
        except Exception as exc:
            if not autowebglm_reward_recorded:
                self._record_autowebglm_primary_outcome(
                    decision=autowebglm_decision, success=False, reward=0.0, drift=True
                )
            await self._finish_action(ev, False, str(exc), screenshot_after=True)
            raise

    async def press_and_log(self, *, locator: Locator, key: str, selector: str = "") -> None:
        selector_hint = str(selector or "")
        dispatch_selector_meta: Dict[str, Any] = {"selector_hint": selector_hint, "selector": "", "strategy": "pending"}
        ev = await self._begin_action("press", selector or key, value=key)
        dom_cursor = await self.mark_dom_event_cursor()
        autowebglm_decision: Dict[str, Any] = {}
        autowebglm_reward_recorded = False
        try:
            dispatch_selector_meta = await self._canonical_selector_for_locator(locator, selector_hint)
            if dispatch_selector_meta.get("selector"):
                selector = str(dispatch_selector_meta["selector"])
            await self.ensure_interactable(action=f"press {key}", selector=selector, timeout_ms=20000)
            semantic_resolution = await self._semantic_action_preflight(
                action="press", locator=locator, selector=selector, label=f"press {key} on {selector_hint or selector or 'HIP Portal field'}",
                action_id=ev.action_id, expected_value=key,
            )
            locator, selector, semantic_revalidation = await self._semantic_dispatch_target(
                resolution=semantic_resolution, locator=locator, selector=selector
            )
            dispatch_selector_meta = await self._canonical_selector_for_locator(locator, selector or selector_hint)
            if dispatch_selector_meta.get("selector"):
                selector = str(dispatch_selector_meta["selector"])
            autowebglm_decision = await self._autowebglm_primary_decision(
                action="press_key", selector=selector, label=f"press {key} on {selector or 'HIP Portal field'}", value=key
            )
            try:
                self.agent_live_view.record_planner(action_id=ev.action_id, decision=autowebglm_decision)
            except Exception:
                pass
            try:
                await self.agent_live_view.record_acting(page=self.page, action_id=ev.action_id)
            except Exception:
                pass
            executed_by_mcp = False
            mcp_error: Optional[str] = None
            executed_by_pyautogui = False
            pyautogui_executor = ""
            pyautogui_error: Optional[str] = None
            py_cfg = getattr(self.config, "pyautogui", None)
            py_primary = bool(
                getattr(self, "pyautogui_tool", None)
                and self.pyautogui_tool.should_primary("press", mutation=False)
            )

            if py_primary:
                desktop = await self._try_pyautogui_press(key=key, selector=selector)
                if desktop.get("pass"):
                    executed_by_pyautogui = True
                    pyautogui_executor = str(
                        ((desktop.get("result") or {}).get("executor")) or "pyautogui-mcp-primary"
                    )
                else:
                    pyautogui_error = str(desktop.get("reason") or "PyAutoGUI primary key press failed")

            if (
                not executed_by_pyautogui
                and bool(getattr(py_cfg, "fallback_to_playwright_mcp", True))
                and self.playwright_mcp_backend is not None
                and bool(self._playwright_mcp_surface_healthy)
                and getattr(self.config.mcp, "playwright_mcp_primary_for_safe_actions", True)
                and self._mcp_safe_selector(selector)
            ):
                try:
                    await self.playwright_mcp_backend.press(
                        selector, key, element=selector or "HIP Portal field"
                    )
                    executed_by_mcp = "browser_press_key" in self.playwright_mcp_backend.client.tools
                except Exception as exc:
                    mcp_error = mask_sensitive_string(str(exc))

            if not executed_by_pyautogui and not executed_by_mcp and not py_primary:
                py_recovery = bool(
                    py_cfg is not None
                    and getattr(py_cfg, "prefer_mcp_before_local_web_fallback", True)
                    and getattr(py_cfg, "use_for_key_recovery", True)
                )
                if py_recovery:
                    desktop = await self._try_pyautogui_press(key=key, selector=selector)
                    if desktop.get("pass"):
                        executed_by_pyautogui = True
                        pyautogui_executor = str(
                            ((desktop.get("result") or {}).get("executor")) or "pyautogui-mcp-recovery"
                        )
                    else:
                        pyautogui_error = str(desktop.get("reason") or "desktop recovery failed")

            if not executed_by_pyautogui and not executed_by_mcp:
                if not bool(getattr(py_cfg, "fallback_to_python_playwright", True)):
                    raise RuntimeError(
                        "HIP_INTERACTION_EXECUTORS_EXHAUSTED: PyAutoGUI primary and Playwright MCP "
                        "fallback did not press the key and Python Playwright fallback is disabled."
                    )
                await locator.first.press(key)
            await self.wait_ready()
            await self._mcp_snapshot_after_action(ev.action_id)
            executor = pyautogui_executor if executed_by_pyautogui else ("playwright-mcp-fallback" if executed_by_mcp else "python-playwright-fallback")
            ev.target = f"{ev.target} [executor={executor}]"
            fallback_notes = []
            if mcp_error:
                fallback_notes.append(f"MCP fallback reason: {mcp_error}")
            if pyautogui_error:
                fallback_notes.append(f"PyAutoGUI fallback reason: {pyautogui_error}")
            if fallback_notes:
                ev.error = "; ".join(fallback_notes)
            semantic_effect = await self._semantic_post_action_verify(
                resolution=semantic_resolution, action="press", exact_value_verified=False
            )
            ev.execution_provenance = mask_sensitive_data({
                "planner": "autowebglm-primary",
                "planner_status": autowebglm_decision.get("status"),
                "planner_aligned": autowebglm_decision.get("aligned"),
                "primary_executor": "pyautogui-mcp",
                "actual_executor": executor,
                "pyautogui_primary_requested": bool(py_primary),
                "pyautogui_mcp_available": bool(getattr(getattr(self, "pyautogui_tool", None), "available", lambda: False)()),
                "playwright_mcp_available": bool(self.playwright_mcp_backend is not None),
                "playwright_mcp_attempted": bool(self.playwright_mcp_backend is not None and self._playwright_mcp_surface_healthy and self._mcp_safe_selector(selector)),
                "playwright_mcp_succeeded": bool(executed_by_mcp),
                "selector_hint": selector_hint,
                "dispatch_selector": selector,
                "dispatch_selector_strategy": dispatch_selector_meta.get("strategy") or "",
                "fallback_reason": mcp_error or pyautogui_error or "",
                "semantic_control_id": semantic_resolution.get("semantic_control_id") or "",
                "semantic_confidence": semantic_resolution.get("confidence"),
                "semantic_margin": semantic_resolution.get("margin"),
                "semantic_gate_status": semantic_resolution.get("status") or "",
                "semantic_reobserved": bool(semantic_resolution.get("reobserved")),
                "semantic_revalidation": semantic_revalidation.get("status") or "",
                "semantic_effect_pass": bool(semantic_effect.get("pass")),
                "semantic_effect_type": semantic_effect.get("effect_type") or "",
                "semantic_effect_confidence": semantic_effect.get("confidence"),
            })
            await self.record_dom_transition(action_id=ev.action_id, action_type="press", target=selector or key, cursor=dom_cursor)
            verified_outcome = bool(semantic_effect.get("pass"))
            model_reward = self._record_autowebglm_primary_outcome(
                decision=autowebglm_decision, success=verified_outcome,
                reward=1.0 if verified_outcome else 0.0, drift=not verified_outcome,
            )
            autowebglm_reward_recorded = bool(model_reward.get("updated"))
            ev.execution_provenance["model_downstream_reward"] = mask_sensitive_data(model_reward)
            await self._finish_action(ev, True)
        except Exception as exc:
            if not autowebglm_reward_recorded:
                self._record_autowebglm_primary_outcome(
                    decision=autowebglm_decision, success=False, reward=0.0, drift=True
                )
            await self._finish_action(ev, False, str(exc), screenshot_after=True)
            raise

    async def mark_dom_event_cursor(self) -> Dict[str, int]:
        if not self.page or not getattr(self.config.extraction, "collect_dom_events", True):
            return {"event_seq": 0, "mutation_seq": 0}
        try:
            return await self.page.evaluate("""
() => ({event_seq:Number(window.__HIP_DOM_EVENT_SEQ||0), mutation_seq:Number(window.__HIP_DOM_MUTATION_SEQ||0)})
""")
        except Exception:
            return {"event_seq": 0, "mutation_seq": 0}

    async def collect_dom_event_window(self, cursor: Optional[Dict[str, int]] = None, *, clear: bool = False) -> Dict[str, Any]:
        if not self.page or not getattr(self.config.extraction, "collect_dom_events", True):
            return {"events": [], "mutations": [], "summary": {"event_count": 0, "mutation_count": 0}}
        cursor = cursor or {"event_seq": 0, "mutation_seq": 0}
        try:
            payload = await self.page.evaluate("""
({eventSeq, mutationSeq, clear}) => {
  const events=(window.__HIP_DOM_EVENT_LOG||[]).filter(x => Number(x.seq||0) > Number(eventSeq||0));
  const mutations=(window.__HIP_DOM_MUTATION_LOG||[]).filter(x => Number(x.seq||0) > Number(mutationSeq||0));
  if (clear) { window.__HIP_DOM_EVENT_LOG=[]; window.__HIP_DOM_MUTATION_LOG=[]; }
  return {events, mutations, cursor:{event_seq:Number(window.__HIP_DOM_EVENT_SEQ||0), mutation_seq:Number(window.__HIP_DOM_MUTATION_SEQ||0)}};
}
""", {"eventSeq": int(cursor.get("event_seq", 0)), "mutationSeq": int(cursor.get("mutation_seq", 0)), "clear": bool(clear)})
        except Exception as exc:
            return {"events": [], "mutations": [], "summary": {"event_count": 0, "mutation_count": 0, "error": mask_sensitive_string(str(exc))}}
        events = mask_sensitive_data(payload.get("events") or [])
        mutations = mask_sensitive_data(payload.get("mutations") or [])
        added = []
        removed = []
        attributes = []
        for m in mutations:
            if not isinstance(m, dict):
                continue
            added.extend(m.get("added_controls") or [])
            removed.extend(m.get("removed_controls") or [])
            if m.get("attribute"):
                attributes.append({
                    "target": m.get("target"), "attribute": m.get("attribute"),
                    "old_value": m.get("old_value"), "new_value": m.get("new_value"),
                })
        summary = {
            "event_count": len(events), "mutation_count": len(mutations),
            "event_types": sorted({str(e.get("type")) for e in events if isinstance(e, dict) and e.get("type")}),
            "added_control_count": len(added), "removed_control_count": len(removed),
            "attribute_change_count": len(attributes),
            "added_controls": added[:50], "removed_controls": removed[:50], "attribute_changes": attributes[:80],
        }
        return {"events": events, "mutations": mutations, "summary": summary, "cursor": payload.get("cursor") or {}}

    async def collect_dom_event_log(self) -> None:
        if not self.page or not getattr(self.config.extraction, "collect_dom_events", True):
            return
        window = await self.collect_dom_event_window({"event_seq": 0, "mutation_seq": 0}, clear=True)
        self.dom_event_records.extend(window.get("events") or [])
        if getattr(self.config.extraction, "collect_dom_mutations", True):
            self.dom_mutation_records.extend(window.get("mutations") or [])

    async def record_dom_transition(self, *, action_id: str, action_type: str, target: str, cursor: Dict[str, int]) -> Dict[str, Any]:
        window = await self.collect_dom_event_window(cursor, clear=False)
        record = mask_sensitive_data({
            "action_id": action_id, "action_type": action_type, "target": target,
            "stage": self._current_stage, "url": self.page.url if self.page else "",
            "summary": window.get("summary") or {},
            "events": (window.get("events") or [])[-120:],
            "mutations": (window.get("mutations") or [])[-120:],
        })
        self.dom_transition_records.append(record)
        safe_write_json(self.dom_events_dir / f"{action_id}_transition.json", record)
        return record

    async def collect_dom_click_log(self) -> None:
        if not self.page or not self.config.extraction.collect_dom_clicks:
            return
        try:
            rows = await self.page.evaluate("""
() => { const rows = window.__HIP_CLICK_LOG || []; window.__HIP_CLICK_LOG = []; return rows; }
""")
        except Exception:
            return
        for row in rows or []:
            self.click_events.append(ClickEvent(
                timestamp=row.get("timestamp") or utc_now(), source="dom", url=mask_sensitive_string(row.get("url", "")),
                action="user_or_dom_click", selector=row.get("selector"), text=mask_sensitive_string(row.get("text", "")),
                tag=row.get("tag"), element_id=row.get("id"), classes=row.get("classes"), href=mask_sensitive_string(row.get("href", "")),
                role=row.get("role"), aria_label=row.get("ariaLabel"), bounding_box=row.get("boundingBox", {}),
                extra={
                    "stage": self._current_stage,
                    "safety_blocked": bool(row.get("safety_blocked")),
                    "safety_authorized": bool(row.get("safety_authorized")),
                    "safety_structural_opener": bool(row.get("safety_structural_opener")),
                    "safety_authorization_task_id": mask_sensitive_string(str(row.get("safety_authorization_task_id") or ""))[:240],
                    "safety_structural_action_label": mask_sensitive_string(str(row.get("safety_structural_action_label") or ""))[:240],
                    "safety_action_text": mask_sensitive_string(str(row.get("safety_action_text") or ""))[:240],
                    "safety_action_selector": mask_sensitive_string(str(row.get("safety_action_selector") or ""))[:500],
                },
            ))

    async def save_dom_snapshot(self, name: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not self.page:
            return out
        self.dom_dir.mkdir(parents=True, exist_ok=True)
        try:
            html = await self.page.content()
            p = self.dom_dir / f"{name}.html"
            p.write_text(mask_sensitive_string(html), encoding="utf-8")
            out["html"] = str(p)
        except Exception:
            pass
        try:
            text = await self.page.locator("body").inner_text(timeout=4000)
            p = self.dom_dir / f"{name}.txt"
            p.write_text(mask_sensitive_string(text), encoding="utf-8")
            out["text"] = str(p)
        except Exception:
            pass
        return out

    async def write_failure_bundle(self, exc: BaseException | None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        bundle = self.run_dir / "failure_bundle"
        bundle.mkdir(parents=True, exist_ok=True)
        paths: Dict[str, str] = {}

        def write_text_file(name: str, content: str) -> None:
            path = bundle / name
            path.write_text(mask_sensitive_string(content), encoding="utf-8")
            paths[name] = str(path)

        def write_json_file(name: str, payload: Any) -> None:
            path = bundle / name
            path.write_text(json.dumps(mask_sensitive_data(payload), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            paths[name] = str(path)

        unavailable = {"available": False, "reason": "Not captured before failure"}
        write_text_file("exception.txt", repr(exc) if exc else str((extra or {}).get("failure_reason") or "Logical failure without exception"))
        write_text_file("current_url.txt", self.page.url if self.page else "")

        if self.page:
            try:
                paths["last_screenshot.png"] = await self.screenshot(bundle / "last_screenshot.png")
            except Exception as e:
                (bundle / "last_screenshot.png").write_text(json.dumps({**unavailable, "error": str(e)}), encoding="utf-8")
                paths["last_screenshot.png"] = str(bundle / "last_screenshot.png")
            dom = await self.save_dom_snapshot("failure_last_dom_snapshot")
            copied = set()
            for _, src in dom.items():
                srcp = Path(src)
                dst = bundle / ("last_dom_snapshot.html" if srcp.suffix == ".html" else "last_dom_text.txt")
                dst.write_text(srcp.read_text(encoding="utf-8"), encoding="utf-8")
                paths[dst.name] = str(dst)
                copied.add(dst.name)
            for name in ["last_dom_snapshot.html", "last_dom_text.txt"]:
                if name not in copied:
                    write_json_file(name, unavailable)
        else:
            write_json_file("last_screenshot.png", unavailable)
            write_json_file("last_dom_snapshot.html", unavailable)
            write_json_file("last_dom_text.txt", unavailable)

        write_json_file("last_20_actions.json", [asdict(a) for a in self.action_events[-20:]] if self.action_events else unavailable)
        write_json_file("last_50_network_events.json", [asdict(n) for n in self.network_tab_events[-50:]] if self.network_tab_events else unavailable)
        (bundle / "console_logs.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in mask_sensitive_data(self.console_messages)) or json.dumps(unavailable), encoding="utf-8")
        paths["console_logs.jsonl"] = str(bundle / "console_logs.jsonl")

        suggestions = [
            "Search box not found. Try role/name based locator, placeholder matching, or wait for grid render.",
            "Partner/System row found but ID not verified. Open details page and extract typed partnerId/systemId/domainId field.",
            "Generic ID detected from unrelated endpoint. Ignore unless endpoint context matches target type.",
            "SSO page still active. User login may be required in persistent browser profile.",
            "No network response body captured. Enable CDP response body capture or increase wait after search."
        ]
        if extra and extra.get("recovery_suggestions"):
            suggestions.extend(extra["recovery_suggestions"])
        write_json_file("recovery_suggestions.json", sorted(set(str(s) for s in suggestions)))

        for name in ["id_candidates.json", "rejected_candidates.json"]:
            src_name = "rejected_ids.json" if name == "rejected_candidates.json" else name
            src = self.run_dir / src_name
            if src.exists():
                try:
                    write_json_file(name, json.loads(src.read_text(encoding="utf-8")))
                except Exception:
                    write_json_file(name, unavailable)
            else:
                write_json_file(name, unavailable)

        partial_graph = {
            "available": True,
            "partial": True,
            "created_at": utc_now(),
            "failure": mask_sensitive_string(repr(exc) if exc else str((extra or {}).get("failure_reason") or "Logical failure")),
            "actions": [asdict(a) for a in self.action_events[-20:]],
            "network_events": [asdict(n) for n in self.network_tab_events[-50:]],
            "console_messages": self.console_messages[-50:],
            "recovery_suggestions": sorted(set(str(s) for s in suggestions)),
        }
        write_json_file("partial_knowledge_graph.json", partial_graph)
        html = "<html><body><h1>Partial HIP Failure Knowledge Graph</h1><pre>" + json.dumps(mask_sensitive_data(partial_graph), indent=2, ensure_ascii=False, default=str) + "</pre></body></html>"
        (bundle / "partial_knowledge_graph.html").write_text(html, encoding="utf-8")
        paths["partial_knowledge_graph.html"] = str(bundle / "partial_knowledge_graph.html")
        return paths

    async def flush_logs(self, *, force: bool = False) -> None:
        """Write evidence logs once, quickly and safely.

        The previous implementation could appear stuck in the finalizing phase for
        very large Network captures because it built several large indented JSON
        strings in memory and re-flushed again during BrowserSession.close().  This
        implementation is idempotent, bounds DOM collection, and streams large JSONL
        output so users can stop/restart without losing inventory files.
        """
        if self._logs_flushed and not force:
            return
        try:
            await asyncio.wait_for(self._capture_browser_session_history("evidence_flush"), timeout=12)
        except Exception:
            pass
        try:
            await asyncio.wait_for(self.collect_dom_click_log(), timeout=8)
        except Exception as exc:
            self.console_messages.append({"timestamp": utc_now(), "type": "log_flush_warning", "text": mask_sensitive_string(f"DOM click log collection skipped: {exc}"), "stage": self._current_stage, "url": self.page.url if self.page else ""})
        try:
            await asyncio.wait_for(self.collect_dom_event_log(), timeout=8)
        except Exception as exc:
            self.console_messages.append({"timestamp": utc_now(), "type": "log_flush_warning", "text": mask_sensitive_string(f"DOM event log collection skipped: {exc}"), "stage": self._current_stage, "url": self.page.url if self.page else ""})

        def _compact(obj: Any) -> str:
            return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)

        # Convert small/medium logs normally. Network tab events can be very large,
        # especially when full response bodies are captured. By default we now write
        # a compact sampled evidence file only; set HIP_WRITE_FULL_NETWORK_LOGS=1
        # when you explicitly need the old full raw Network dump.
        cons = mask_sensitive_data(self.console_messages)
        clicks = [mask_sensitive_data(asdict(c)) for c in self.click_events]
        actions = [mask_sensitive_data(asdict(a)) for a in self.action_events]

        def _trim_event(e: Dict[str, Any], *, body_limit: int = 1800) -> Dict[str, Any]:
            out = dict(e)
            for key in ["response_body_redacted", "response_body_text_redacted", "request_post_data_redacted"]:
                value = out.get(key)
                if isinstance(value, str) and len(value) > body_limit:
                    out[key] = value[:body_limit] + f"... <truncated {len(value) - body_limit} chars>"
                elif isinstance(value, (dict, list)):
                    text = json.dumps(value, ensure_ascii=False, default=str)
                    if len(text) > body_limit:
                        out[key] = text[:body_limit] + f"... <truncated {len(text) - body_limit} chars>"
            return out

        failed: List[Dict[str, Any]] = []
        relevant: List[Dict[str, Any]] = []
        sample: List[Dict[str, Any]] = []
        by_status: Dict[str, int] = {}
        by_resource_type: Dict[str, int] = {}
        write_full_network = os.getenv("HIP_WRITE_FULL_NETWORK_LOGS", "").strip().lower() in {"1", "true", "yes"}
        for idx, r in enumerate(self.network_tab_events):
            e_full = mask_sensitive_data(asdict(r))
            status = e_full.get("status")
            by_status[str(status)] = by_status.get(str(status), 0) + 1
            by_resource_type[str(e_full.get("resource_type"))] = by_resource_type.get(str(e_full.get("resource_type")), 0) + 1
            try:
                is_failed = bool(e_full.get("error") or (status is not None and int(status) >= 400))
            except Exception:
                is_failed = bool(e_full.get("error"))
            url_l = str(e_full.get("url", "")).lower()
            is_relevant = any(h in url_l for h in ["partner", "system", "domain", "account", "deployment-group"])
            e = _trim_event(e_full)
            if is_failed and len(failed) < 120:
                failed.append(e)
            if is_relevant and len(relevant) < 250:
                relevant.append(e)
            if len(sample) < 500 and (is_failed or is_relevant or idx < 50):
                sample.append(e)

        tab_json_path = self.network_dir / "network_tab_events.json"
        tab_jsonl_path = self.run_dir / "network_events.jsonl"
        events_to_write = [mask_sensitive_data(asdict(r)) for r in self.network_tab_events] if write_full_network else sample
        with tab_json_path.open("w", encoding="utf-8") as jf, tab_jsonl_path.open("w", encoding="utf-8") as lf:
            jf.write("[\n")
            first = True
            for e in events_to_write:
                if not write_full_network:
                    e = _trim_event(e)
                line = _compact(e)
                if not first:
                    jf.write(",\n")
                jf.write(line)
                lf.write(line + "\n")
                first = False
            jf.write("\n]")

        # Legacy low-level network records are not useful for upload/review and can
        # become large. Keep only a compact sample unless explicitly requested.
        net = [mask_sensitive_data(asdict(r)) for r in self.network_records[:1000]]
        (self.network_dir / "network_records_legacy.json").write_text(json.dumps(net, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        summary = {
            "total_events": len(self.network_tab_events),
            "sampled_events_written": len(events_to_write),
            "full_network_logs_written": write_full_network,
            "full_network_logs_hint": "Set HIP_WRITE_FULL_NETWORK_LOGS=1 before running if raw full Network evidence is needed.",
            "by_status": by_status,
            "by_resource_type": by_resource_type,
            "failed": failed,
            "relevant_endpoints": relevant,
            "flush_mode": "compact_summary_default",
        }
        (self.run_dir / "network_summary.json").write_text(json.dumps(mask_sensitive_data(summary), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (self.console_dir / "console_messages.json").write_text(json.dumps(cons, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (self.run_dir / "console_logs.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False, default=str) for x in cons), encoding="utf-8")
        (self.clicks_dir / "click_events.json").write_text(json.dumps(clicks, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (self.run_dir / "click_sequence.json").write_text(json.dumps(clicks, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (self.actions_dir / "action_events.json").write_text(json.dumps(actions, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (self.run_dir / "action_sequence.json").write_text(json.dumps(actions, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        dom_events = mask_sensitive_data(self.dom_event_records[-int(getattr(self.config.extraction, "max_dom_event_records", 6000) or 6000):])
        dom_mutations = mask_sensitive_data(self.dom_mutation_records[-int(getattr(self.config.extraction, "max_dom_mutation_records", 6000) or 6000):])
        dom_transitions = mask_sensitive_data(self.dom_transition_records)
        (self.dom_events_dir / "dom_events.json").write_text(json.dumps(dom_events, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (self.dom_events_dir / "dom_mutations.json").write_text(json.dumps(dom_mutations, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (self.dom_events_dir / "action_dom_transitions.json").write_text(json.dumps(dom_transitions, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        event_type_counts: Dict[str, int] = {}
        for row in dom_events:
            event_type_counts[str(row.get("type", "unknown"))] = event_type_counts.get(str(row.get("type", "unknown")), 0) + 1
        mutation_type_counts: Dict[str, int] = {}
        for row in dom_mutations:
            key = str(row.get("attribute") or row.get("type") or "unknown")
            mutation_type_counts[key] = mutation_type_counts.get(key, 0) + 1
        safe_write_json(self.dom_events_dir / "dom_event_summary.json", {
            "schema_version": "hip.dom-event-evidence.v1",
            "event_count": len(dom_events), "mutation_count": len(dom_mutations),
            "action_transition_count": len(dom_transitions),
            "event_type_counts": event_type_counts, "mutation_type_counts": mutation_type_counts,
            "captures": ["pointerdown", "click", "focusin", "focusout", "input", "change", "keydown", "childList", "attributes"],
        })
        self._logs_flushed = True


@asynccontextmanager
async def browser_session_scope(
    config: AppConfig,
    run_dir: Path,
    *,
    existing: Optional[BrowserSession] = None,
    phase_name: str = "",
):
    """Yield a browser session without closing a borrowed full-run session.

    Standalone KB commands still own and close their browser. Full dummy-fill
    orchestration passes one already-started BrowserSession to every phase so Dell
    SSO cookies, local storage, tabs, MCP attachments and Angular session state stay
    in the same persistent Chrome context.
    """
    if existing is not None:
        if existing._closed:
            raise RuntimeError("Cannot reuse a closed HIP browser session")
        if existing.page is None:
            await existing.start()
        await existing.prepare_borrowed_phase(run_dir, phase_name)
        yield existing
        return
    async with BrowserSession(config, run_dir) as owned:
        await owned.prepare_borrowed_phase(run_dir, phase_name or "standalone")
        yield owned
