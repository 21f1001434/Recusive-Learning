"""WebMCP: tools a web page offers an agent, used to complete HIP tasks (V243R33).

WebMCP (W3C Web Machine Learning CG proposal) lets a page expose *tools* to an
AI agent through ``navigator.modelContext``:

* imperative -- ``navigator.modelContext.registerTool({name, description,
  inputSchema, annotations, execute})`` / ``provideContext({tools})``;
* declarative -- ``<form toolname="…" tooldescription="…" [toolautosubmit]>``
  whose fields (``toolparamtitle`` / ``toolparamdescription``) become the
  tool's parameters;
* agent side -- Chrome's early preview exposes ``navigator.modelContextTesting``
  (``listTools()`` / ``executeTool(name, argsJson)``).

This module makes all of that usable by the HIP agent in every browser:

1. ``WEBMCP_INIT_JS`` is added to every page before the page's own scripts.
   With a native ``navigator.modelContext`` it mirrors what the page registers;
   without one it installs a standards-shaped polyfill, so a page that feature-
   detects WebMCP registers its tools anyway.  Nothing on the page changes.
2. The agent adds its own *in-page* tools (private, never registered with the
   page's context): ``hip_page_state``, ``hip_read_form``, ``hip_form_matches``,
   ``hip_open_tab``, ``hip_fill_text``.
3. Every tool is classified: ``read_only``, ``navigation``, ``form_edit`` (puts
   values in a form, saves nothing) or ``mutating`` (save / submit / deploy /
   delete …, a declarative form with ``toolautosubmit``, or anything unknown --
   fail closed).  Mutating tools run only with the three-part portal mutation
   gate; the mission never calls them (Save stays the governed UI commit).
4. ``fill_with_page_tools``: when the page offers a form-edit tool whose
   parameters cover the phase's input.json values, the agent calls it once
   with exactly those values.  The live form is then proved against input.json
   (R32); only an exact form counts, otherwise the normal fill continues.

Artifacts are value-free: tool names, parameter names and counts only.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.webmcp.v1"

# ---------------------------------------------------------------------------
# In-page layer
# ---------------------------------------------------------------------------
WEBMCP_INIT_JS = r"""
(() => {
  if (window.__hipWebMCP) return;
  const nav = navigator;
  const pageTools = new Map();
  const agentTools = new Map();
  const clean = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const parse = (v) => { if (typeof v !== 'string') return v || {}; try { return JSON.parse(v); } catch (e) { return {}; } };
  const record = (tool, source) => { if (tool && tool.name) pageTools.set(String(tool.name), { tool, source }); };
  const nativeMC = nav.modelContext || null;
  const orig = {};
  if (nativeMC) {
    for (const k of ['provideContext', 'registerTool', 'unregisterTool', 'clearContext']) {
      if (typeof nativeMC[k] === 'function') orig[k] = nativeMC[k].bind(nativeMC);
    }
  }
  const ctx = {
    provideContext(c) {
      for (const [n, r] of Array.from(pageTools)) if (r.source === 'provideContext') pageTools.delete(n);
      for (const t of ((c && c.tools) || [])) record(t, 'provideContext');
      if (orig.provideContext) return orig.provideContext(c);
    },
    registerTool(t) {
      record(t, 'registerTool');
      let reg = null;
      if (orig.registerTool) reg = orig.registerTool(t);
      return { unregister() { pageTools.delete(String(t && t.name)); try { if (reg && reg.unregister) reg.unregister(); } catch (e) {} } };
    },
    unregisterTool(name) { pageTools.delete(String(name)); if (orig.unregisterTool) return orig.unregisterTool(name); },
    clearContext() { pageTools.clear(); if (orig.clearContext) return orig.clearContext(); },
  };
  let polyfilled = false;
  if (nativeMC) {
    for (const k of Object.keys(ctx)) { try { nativeMC[k] = ctx[k]; } catch (e) {} }
  } else {
    try { Object.defineProperty(nav, 'modelContext', { value: ctx, configurable: true, enumerable: true }); polyfilled = true; }
    catch (e) { try { nav.modelContext = ctx; polyfilled = true; } catch (e2) {} }
  }

  // Declarative WebMCP: <form toolname=…> fields become the tool's parameters.
  const fieldLabel = (el) => clean(el.getAttribute('toolparamtitle')
    || (el.labels && el.labels[0] && el.labels[0].innerText)
    || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name).replace(/\s*\*$/, '');
  const declarative = () => Array.from(document.querySelectorAll('form[toolname]')).map((form) => {
    const props = {}; const required = [];
    for (const el of form.querySelectorAll('input[name],select[name],textarea[name]')) {
      const type = (el.type || '').toLowerCase();
      if (['hidden', 'submit', 'button', 'reset', 'file', 'image'].includes(type)) continue;
      const name = el.name;
      let schema;
      if (type === 'radio') {
        const prev = props[name];
        const legend = el.closest('fieldset') && el.closest('fieldset').querySelector('legend');
        schema = prev || { type: 'string', title: clean(el.getAttribute('toolparamtitle') || (legend && legend.innerText) || name), enum: [] };
        if (!schema.enum.includes(el.value)) schema.enum.push(el.value);
      } else if (!props[name]) {
        schema = { type: type === 'number' ? 'number' : type === 'checkbox' ? 'boolean' : 'string', title: fieldLabel(el) };
        if (el.tagName === 'SELECT') schema.enum = Array.from(el.options).map((o) => o.value).filter(Boolean);  // a placeholder (value="") is no choice
      } else continue;
      const desc = el.getAttribute('toolparamdescription');
      if (desc) schema.description = clean(desc);
      props[name] = schema;
      if (el.required && !required.includes(name)) required.push(name);
    }
    return { name: form.getAttribute('toolname'), description: clean(form.getAttribute('tooldescription')),
             inputSchema: { type: 'object', properties: props, required }, annotations: {},
             source: 'declarative', autosubmit: form.hasAttribute('toolautosubmit') };
  });
  const setValue = (el, value) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value');
    if (setter && setter.set) setter.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const fillForm = (form, args) => {
    const filled = []; const missing = [];
    for (const [name, raw] of Object.entries(args || {})) {
      if (name.startsWith('__')) continue;
      const els = Array.from(form.querySelectorAll(`[name="${CSS.escape(name)}"]`));
      if (!els.length) { missing.push(name); continue; }
      const el = els[0]; const type = (el.type || '').toLowerCase();
      if (type === 'radio') {
        const hit = els.find((r) => String(r.value).toLowerCase() === String(raw).toLowerCase());
        if (hit) { hit.checked = true; hit.dispatchEvent(new Event('change', { bubbles: true })); filled.push(name); } else missing.push(name);
      } else if (type === 'checkbox') {
        el.checked = raw === true || /^(true|yes|1|on)$/i.test(String(raw)); el.dispatchEvent(new Event('change', { bubbles: true })); filled.push(name);
      } else if (el.tagName === 'SELECT') {
        const opt = Array.from(el.options).find((o) => o.value === String(raw) || clean(o.text) === String(raw));
        if (opt) { setValue(el, opt.value); filled.push(name); } else missing.push(name);
      } else { setValue(el, String(raw)); filled.push(name); }
    }
    return { filled, missing };
  };
  const normalize = (res) => {
    if (res == null) return { content: [], text: '' };
    if (typeof res === 'string') return { content: [{ type: 'text', text: res }], text: res };
    const content = Array.isArray(res.content) ? res.content : [{ type: 'text', text: JSON.stringify(res) }];
    return { content, text: content.filter((c) => c && c.type === 'text').map((c) => c.text).join('\n'), isError: !!res.isError };
  };
  const agentHandle = { async requestUserInteraction() { throw new Error('HIP agent: this tool asks for a person; not run autonomously'); } };

  async function list() {
    const out = [];
    const add = (t) => { if (t && t.name && !out.some((o) => o.name === t.name)) out.push(t); };
    const testing = nav.modelContextTesting;
    if (testing && typeof testing.listTools === 'function') {
      try {
        for (const t of (await testing.listTools()) || []) {
          add({ name: t.name, description: clean(t.description), inputSchema: parse(t.inputSchema), annotations: t.annotations || {}, source: 'native' });
        }
      } catch (e) {}
    }
    for (const [name, r] of pageTools) {
      add({ name, description: clean(r.tool.description), inputSchema: parse(r.tool.inputSchema), annotations: r.tool.annotations || {}, source: r.source });
    }
    for (const d of declarative()) add(d);
    for (const [name, t] of agentTools) add({ name, description: t.description, inputSchema: t.inputSchema, annotations: t.annotations || {}, source: 'hip-agent', kind: t.kind });
    return out;
  }
  async function call(name, args) {
    args = args || {};
    if (agentTools.has(name)) return normalize(await agentTools.get(name).execute(args));
    const form = Array.from(document.querySelectorAll('form[toolname]')).find((f) => f.getAttribute('toolname') === name);
    if (form) {
      const res = fillForm(form, args);
      if (args.__submit === true) { if (form.requestSubmit) form.requestSubmit(); else form.submit(); res.submitted = true; }
      return normalize({ content: [{ type: 'text', text: JSON.stringify(res) }] });
    }
    if (pageTools.has(name)) return normalize(await pageTools.get(name).tool.execute(args, agentHandle));
    const testing = nav.modelContextTesting;
    if (testing && typeof testing.executeTool === 'function') return normalize(await testing.executeTool(name, JSON.stringify(args)));
    throw new Error('WebMCP tool not found: ' + name);
  }
  window.__hipWebMCP = {
    version: 1, polyfilled, native: !!nativeMC, nativeTesting: !!nav.modelContextTesting,
    list, call, fillForm, clean,
    addAgentTool(tool) { agentTools.set(tool.name, tool); },
  };
})();
"""

# The agent's own in-page tools (private to the agent; never given to the page's context).
HIP_AGENT_TOOLS_JS = r"""
(() => {
  const W = window.__hipWebMCP;
  if (!W || W.__hipAgentTools) return;
  W.__hipAgentTools = true;
  const clean = W.clean;
  const norm = (s) => clean(s).toLowerCase().replace(/\s*\*\s*$/, '').replace(/[^a-z0-9]+/g, ' ').trim();
  const shown = (el) => { if (!el || !el.getBoundingClientRect) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const root = () => {
    const layers = Array.from(document.querySelectorAll('[role=dialog],dds-drawer,.dds__drawer,.cdk-overlay-pane,[aria-modal=true],form'))
      .filter(shown).filter((el) => el.querySelector('input,select,textarea,[role=combobox]'));
    return layers.length ? layers[layers.length - 1] : document.body;
  };
  const labelOf = (el) => {
    const group = el.closest('.dds__form-group,.form-group,[class*=form-field],fieldset');
    const lab = (el.labels && el.labels[0]) || (group && group.querySelector('label,legend'));
    return clean((lab && lab.innerText) || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '');
  };
  const controls = () => Array.from(root().querySelectorAll('input,select,textarea')).filter((el) => {
    const type = (el.type || '').toLowerCase();
    return !['hidden', 'submit', 'button', 'reset', 'image'].includes(type) && (shown(el) || type === 'radio' || type === 'checkbox');
  }).map((el) => {
    const type = (el.type || el.tagName).toLowerCase();
    const host = el.closest('dds-dropdown,.dds__dropdown,[class*=multiselect]');
    const chips = host ? Array.from(host.querySelectorAll('.dds__tag,[class*=chip],[class*=tag]')).map((t) => clean(t.innerText)).filter(Boolean) : [];
    return { label: labelOf(el), name: el.name || '', type, value: type === 'file' ? Array.from(el.files || []).map((f) => f.name).join(', ') : String(el.value || ''),
             chips, checked: !!el.checked, required: !!el.required || el.getAttribute('aria-required') === 'true',
             invalid: el.getAttribute('aria-invalid') === 'true', disabled: !!el.disabled };
  });
  W.addAgentTool({
    name: 'hip_page_state', kind: 'read_only', annotations: { readOnlyHint: true },
    description: 'State of the current HIP page: route, title, Whitelabel Error Page, blocking loader, open dropdowns, wizard tabs, active form.',
    inputSchema: { type: 'object', properties: {} },
    async execute() {
      const body = document.body ? String(document.body.innerText || '').slice(0, 6000) : '';
      const tabs = Array.from(document.querySelectorAll('[role=tab]')).filter(shown).map((t) => ({ text: clean(t.innerText), selected: t.getAttribute('aria-selected') === 'true' }));
      const r = root();
      return { content: [{ type: 'text', text: JSON.stringify({
        path: location.pathname, title: document.title,
        whitelabel_error: /whitelabel\s+error\s+page/i.test(body) || /no explicit mapping for \/error/i.test(body),
        blocking_loader: Array.from(document.querySelectorAll('[aria-busy=true],.dds__loading-indicator')).some(shown),
        open_dropdowns: document.querySelectorAll('[role=combobox][aria-expanded=true]').length,
        tabs, form_title: clean((r.querySelector('h1,h2,h3,[class*=title]') || {}).innerText || ''),
        control_count: controls().length,
      }) }] };
    },
  });
  W.addAgentTool({
    name: 'hip_read_form', kind: 'read_only', annotations: { readOnlyHint: true },
    description: 'Every field of the active form: label, name, type, committed value (or chips), checked, required, invalid.',
    inputSchema: { type: 'object', properties: {} },
    async execute() { return { content: [{ type: 'text', text: JSON.stringify(controls()) }] }; },
  });
  W.addAgentTool({
    name: 'hip_form_matches', kind: 'read_only', annotations: { readOnlyHint: true },
    description: 'Compare expected values with the active form: which labels hold exactly the expected value.',
    inputSchema: { type: 'object', properties: { expected: { type: 'array', items: { type: 'object', properties: {
      field: { type: 'string' }, labels: { type: 'array', items: { type: 'string' } }, value: { type: 'string' } } } } }, required: ['expected'] },
    async execute({ expected }) {
      const cs = controls(); const matched = []; const missing = [];
      for (const want of expected || []) {
        const names = (want.labels || [want.field]).map(norm).filter(Boolean);
        const hits = cs.filter((c) => names.some((n) => { const l = norm(c.label); return l && (l === n || l.includes(n) || n.includes(l)); }));
        const val = norm(want.value);
        const ok = hits.some((c) => norm(c.value) === val || c.chips.map(norm).includes(val)
          || ((c.type === 'radio' || c.type === 'checkbox') && c.checked && norm(c.label) === val));
        (ok ? matched : missing).push(want.field);
      }
      return { content: [{ type: 'text', text: JSON.stringify({ matched, missing, complete: missing.length === 0 }) }] };
    },
  });
  W.addAgentTool({
    name: 'hip_open_tab', kind: 'navigation', annotations: {},
    description: 'Show a wizard tab by its label (navigation only; no value changes).',
    inputSchema: { type: 'object', properties: { label: { type: 'string' } }, required: ['label'] },
    async execute({ label }) {
      const want = norm(label);
      const tab = Array.from(document.querySelectorAll('[role=tab]')).filter(shown).find((t) => { const n = norm(t.innerText); return n === want || n.includes(want); });
      if (!tab) return { content: [{ type: 'text', text: JSON.stringify({ opened: false }) }], isError: true };
      tab.click();
      return { content: [{ type: 'text', text: JSON.stringify({ opened: true, tab: clean(tab.innerText) }) }] };
    },
  });
  W.addAgentTool({
    name: 'hip_fill_text', kind: 'form_edit', annotations: {},
    description: 'Type a value into a plain text field of the active form, found by its label (dropdowns are left to the DDS driver). Saves nothing.',
    inputSchema: { type: 'object', properties: { label: { type: 'string' }, value: { type: 'string' } }, required: ['label', 'value'] },
    async execute({ label, value }) {
      const want = norm(label);
      const el = Array.from(root().querySelectorAll('input[type=text],input:not([type]),textarea')).filter(shown)
        .find((e) => !e.disabled && !e.readOnly && e.getAttribute('role') !== 'combobox' && norm(labelOf(e)) === want);
      if (!el) return { content: [{ type: 'text', text: JSON.stringify({ filled: false }) }], isError: true };
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, String(value));
      el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('blur', { bubbles: true }));
      return { content: [{ type: 'text', text: JSON.stringify({ filled: el.value === String(value) }) }] };
    },
  });
})();
"""

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
# Verbs that always change the portal (fail closed).
_MUTATING = {
    "save", "submit", "delete", "remove", "deploy", "publish", "migrate", "approve", "confirm", "send", "commit",
    "promote", "activate", "deactivate", "enable", "disable", "archive", "cancel", "reject", "post", "upload",
    "import", "execute", "run", "trigger", "purchase", "pay", "book", "order", "validate",
}
# Verbs that change an object unless the tool only fills a form ("fill_create_profile_form").
_OBJECT_CHANGE = {"create", "update", "edit", "clone", "merge", "add", "rename", "copy", "modify"}
_FORM_EDIT = {"fill", "prefill", "populate", "set", "enter", "type", "select", "choose", "input", "complete", "draft"}
_NAVIGATION = {"open", "show", "goto", "navigate", "expand", "collapse", "tab", "switch", "scroll"}
_READ = {"get", "read", "list", "search", "find", "view", "describe", "status", "state", "check", "lookup",
         "query", "fetch", "count", "inspect", "matches", "compare", "preview"}
_SAVE_PHRASES = re.compile(r"\b(and|then)\s+(save|submit|create|deploy|publish|send)s?\b|\b(saves|submits|deploys|publishes|persists)\b", re.I)
_NO_SAVE = re.compile(r"\b(does not|doesn't|without|never|no)\s+(save|submit|saving|submitting|persist)", re.I)


class WebMCPPolicy:
    def __init__(self, **kw: Any) -> None:
        self.enabled = bool(kw.get("enabled", True))
        self.inject_polyfill = bool(kw.get("inject_polyfill", True))
        self.agent_tools = bool(kw.get("agent_tools", True))
        self.use_page_tools_for_fill = bool(kw.get("use_page_tools_for_fill", True))
        self.min_input_coverage = float(kw.get("min_input_coverage", 0.6) or 0.6)
        self.tool_timeout_seconds = float(kw.get("tool_timeout_seconds", 20.0) or 20.0)
        self.max_calls_per_phase = int(kw.get("max_calls_per_phase", 20) or 20)
        self.settle_ms = int(kw.get("settle_ms", 400) or 400)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def webmcp_policy(config: Any = None) -> WebMCPPolicy:
    cfg = getattr(config, "webmcp", None)
    values = {k: getattr(cfg, k) for k in ("enabled", "inject_polyfill", "agent_tools", "use_page_tools_for_fill",
                                           "min_input_coverage", "tool_timeout_seconds", "max_calls_per_phase", "settle_ms")
              if cfg is not None and hasattr(cfg, k)}
    env = os.getenv("HIP_WEBMCP")
    if env not in (None, ""):
        values["enabled"] = str(env).strip().lower() in {"1", "true", "yes", "on"}
    return WebMCPPolicy(**values)


def init_script(policy: Optional[WebMCPPolicy] = None) -> str:
    policy = policy or WebMCPPolicy()
    return WEBMCP_INIT_JS + (HIP_AGENT_TOOLS_JS if policy.agent_tools else "")


async def install_on_context(context: Any, policy: Optional[WebMCPPolicy] = None) -> bool:
    """Add the WebMCP layer to every future page of the browser context."""
    policy = policy or WebMCPPolicy()
    if not (policy.enabled and policy.inject_polyfill) or context is None:
        return False
    await context.add_init_script(init_script(policy))
    return True


async def ensure_installed(page: Any, policy: Optional[WebMCPPolicy] = None) -> Dict[str, Any]:
    """The layer on this page now (a page opened before the init script existed gets it too)."""
    policy = policy or WebMCPPolicy()
    if not policy.enabled or page is None:
        return {"installed": False, "reason": "disabled"}
    try:
        state = await page.evaluate("() => window.__hipWebMCP ? {polyfilled: window.__hipWebMCP.polyfilled, native: window.__hipWebMCP.native, nativeTesting: window.__hipWebMCP.nativeTesting} : null")
        if not state:
            await page.evaluate(init_script(policy))
            state = await page.evaluate("() => ({polyfilled: window.__hipWebMCP.polyfilled, native: window.__hipWebMCP.native, nativeTesting: window.__hipWebMCP.nativeTesting, late: true})")
        return {"installed": True, **(state or {})}
    except Exception as exc:
        return {"installed": False, "error": mask_sensitive_string(str(exc))[:200]}


# ---------------------------------------------------------------------------
# Discovery and classification
# ---------------------------------------------------------------------------
def _tokens(text: str) -> List[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text or ""))
    return [t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t]


def classify_tool(tool: Mapping[str, Any]) -> str:
    """read_only | navigation | form_edit | mutating (fail closed)."""
    if str(tool.get("source") or "") == "hip-agent":
        return str(tool.get("kind") or "read_only")
    if tool.get("source") == "declarative":
        return "mutating" if tool.get("autosubmit") else "form_edit"
    ordered = _tokens(str(tool.get("name") or ""))
    name_tokens = set(ordered)
    description = str(tool.get("description") or "")
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), Mapping) else {}
    if name_tokens & _MUTATING or annotations.get("destructiveHint") is True:
        return "mutating"
    if _SAVE_PHRASES.search(description) and not _NO_SAVE.search(description):
        return "mutating"
    fills_form = bool(ordered) and ordered[0] in _FORM_EDIT
    if name_tokens & _OBJECT_CHANGE and not fills_form:
        return "mutating"
    if annotations.get("readOnlyHint") is True or (ordered and ordered[0] in _READ):
        return "read_only"
    if fills_form or name_tokens & _FORM_EDIT:
        return "form_edit"
    if name_tokens & _NAVIGATION:
        return "navigation"
    if name_tokens & _READ:
        return "read_only"
    return "mutating"


async def list_tools(page: Any, policy: Optional[WebMCPPolicy] = None) -> List[Dict[str, Any]]:
    policy = policy or WebMCPPolicy()
    if not policy.enabled or page is None:
        return []
    await ensure_installed(page, policy)
    try:
        tools = await asyncio.wait_for(page.evaluate("() => window.__hipWebMCP.list()"), timeout=policy.tool_timeout_seconds)
    except Exception:
        return []
    out = []
    for tool in tools or []:
        if isinstance(tool, dict) and tool.get("name"):
            row = dict(tool)
            row["classification"] = classify_tool(row)
            out.append(row)
    return out


def catalog(tools: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Value-free summary of the tools (for artifacts, the planner and the Control Center)."""
    rows = []
    for t in tools:
        props = ((t.get("inputSchema") or {}).get("properties") or {}) if isinstance(t.get("inputSchema"), Mapping) else {}
        rows.append({"name": str(t.get("name")), "source": t.get("source"), "classification": t.get("classification"),
                     "description": mask_sensitive_string(str(t.get("description") or ""))[:240],
                     "parameters": sorted(str(k) for k in props)[:60]})
    return rows


class WebMCPToolRefused(RuntimeError):
    pass


async def call_tool(
    page: Any, name: str, args: Optional[Mapping[str, Any]] = None, *, tools: Optional[Sequence[Mapping[str, Any]]] = None,
    gate: Optional[Mapping[str, Any]] = None, policy: Optional[WebMCPPolicy] = None,
) -> Dict[str, Any]:
    """Call one WebMCP tool.  A mutating tool needs the three-part mutation gate."""
    policy = policy or WebMCPPolicy()
    tools = list(tools) if tools is not None else await list_tools(page, policy)
    tool = next((t for t in tools if t.get("name") == name), None)
    if tool is None:
        raise WebMCPToolRefused(f"HIP_WEBMCP_TOOL_NOT_FOUND: {name}")
    kind = str(tool.get("classification") or classify_tool(tool))
    args = dict(args or {})
    if kind == "mutating" and not (isinstance(gate, Mapping) and gate.get("pass") is True):
        raise WebMCPToolRefused(
            f"HIP_WEBMCP_MUTATING_TOOL_BLOCKED: {name} would change the portal; it needs --allow-portal-mutation, "
            "HIP_ALLOW_PORTAL_MUTATION=YES and the confirmation phrase")
    if tool.get("source") == "declarative" and args.get("__submit") and kind != "mutating":
        args.pop("__submit", None)  # a form-edit call never submits
    if kind == "mutating" and tool.get("source") == "declarative":
        args["__submit"] = True
    result = await asyncio.wait_for(page.evaluate("([n, a]) => window.__hipWebMCP.call(n, a)", [name, args]),
                                    timeout=policy.tool_timeout_seconds)
    result = dict(result or {})
    text = str(result.get("text") or "")
    parsed: Any = None
    try:
        parsed = json.loads(text) if text.strip().startswith(("{", "[")) else None
    except Exception:
        parsed = None
    return {"tool": name, "classification": kind, "source": tool.get("source"), "is_error": bool(result.get("isError")),
            "text": text, "json": parsed}


# ---------------------------------------------------------------------------
# Completing a phase with a page tool
# ---------------------------------------------------------------------------
def _key(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower().replace("*", ""))


def _leaf_row(path: str) -> Tuple[str, Optional[int], str]:
    """``$.objects.rule.conditions[1].operator`` -> ("conditions", 1, "operator")."""
    m = re.search(r"\.([A-Za-z0-9_]+)\[(\d+)\]\.([A-Za-z0-9_]+)$", path)
    if m:
        return m.group(1), int(m.group(2)), m.group(3)
    return "", None, path.rsplit(".", 1)[-1]


def _coerce(value: Any, schema: Mapping[str, Any]) -> Tuple[bool, Any]:
    kind = str(schema.get("type") or "string")
    enum = schema.get("enum") if isinstance(schema.get("enum"), list) else None
    if isinstance(value, list):
        if kind == "array":
            return True, list(value)
        if len(value) == 1:
            value = value[0]
        else:
            return False, None
    if kind == "boolean":
        text = str(value).strip().lower()
        if text in {"true", "yes", "y", "1", "on", "enabled"}:
            return True, True
        if text in {"false", "no", "n", "0", "off", "disabled"}:
            return True, False
        return False, None
    if kind in {"number", "integer"}:
        try:
            return True, (int(value) if kind == "integer" else float(value))
        except Exception:
            return False, None
    text = str(value)
    if enum:
        # The page's own option wins: "Move to Archive" -> "Move To Archive", "IB(1.0)" -> "IB (1.0)".
        hit = (next((e for e in enum if str(e) == text), None)
               or next((e for e in enum if str(e).lower() == text.lower()), None)
               or next((e for e in enum if _key(e) and _key(e) == _key(text)), None))
        return (True, hit) if hit is not None else (False, None)
    return True, text


def map_input_to_tool(tool: Mapping[str, Any], leaves: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Map input.json leaves onto a tool's parameters by name or title (and rows onto array parameters)."""
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), Mapping) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    by_key: Dict[str, Tuple[str, Mapping[str, Any]]] = {}
    for pname, pschema in props.items():
        pschema = pschema if isinstance(pschema, Mapping) else {}
        for alias in (pname, pschema.get("title")):
            if _key(alias):
                by_key.setdefault(_key(alias), (str(pname), pschema))
    args: Dict[str, Any] = {}
    mapped: List[str] = []
    unmapped: List[str] = []
    rows: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for leaf in leaves:
        path = str(leaf.get("input_path") or "")
        container, index, field = _leaf_row(path)
        if container and index is not None:
            hit = by_key.get(_key(container))
            item = ((hit[1].get("items") or {}) if hit else {}) if hit and isinstance(hit[1].get("items"), Mapping) else {}
            item_props = item.get("properties") if isinstance(item.get("properties"), Mapping) else {}
            sub = next((n for n in item_props if _key(n) == _key(field) or _key((item_props[n] or {}).get("title")) == _key(field)), None)
            if hit and sub is not None:
                ok, value = _coerce(leaf.get("value"), item_props[sub] or {})
                if ok:
                    rows.setdefault(hit[0], {}).setdefault(index, {})[sub] = value
                    mapped.append(path)
                    continue
            unmapped.append(path)
            continue
        hit = by_key.get(_key(leaf.get("field_key") or field))
        if hit is None:
            unmapped.append(path)
            continue
        ok, value = _coerce(leaf.get("value"), hit[1])
        if not ok:
            unmapped.append(path)
            continue
        args[hit[0]] = value
        mapped.append(path)
    for pname, indexed in rows.items():
        args[pname] = [indexed[i] for i in sorted(indexed)]
    required = [str(r) for r in (schema.get("required") or []) if isinstance(r, str)]
    total = len(list(leaves))
    return {
        "tool": tool.get("name"), "args": args, "mapped_inputs": mapped, "unmapped_inputs": unmapped,
        "coverage": round(len(mapped) / total, 3) if total else 0.0,
        "missing_required": [r for r in required if r not in args],
    }


async def fill_with_page_tools(
    page: Any, *, phase: str, leaves: Sequence[Mapping[str, Any]], config: Any = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Fill the form with the page's own WebMCP tool, when one covers input.json (V243R33).

    Only a ``form_edit`` tool (never a mutating one) whose required parameters
    are all given and that covers ``min_input_coverage`` of the values is used.
    Whether the form is then right is decided by the live input.json proof.
    """
    policy = webmcp_policy(config)
    audit: Dict[str, Any] = {"schema_version": SCHEMA, "phase": phase, "enabled": policy.enabled, "filled": False,
                             "values_stored": False}
    if not (policy.enabled and policy.use_page_tools_for_fill) or not leaves:
        audit["reason"] = "disabled" if not policy.enabled else "no input values"
        return audit
    tools = await list_tools(page, policy)
    audit["tools"] = catalog(tools)
    candidates = []
    for tool in tools:
        if tool.get("source") == "hip-agent" or tool.get("classification") != "form_edit":
            continue
        plan = map_input_to_tool(tool, leaves)
        candidates.append(plan)
    audit["candidates"] = [{k: v for k, v in c.items() if k != "args"} for c in candidates]
    usable = [c for c in candidates if c["mapped_inputs"] and not c["missing_required"]
              and c["coverage"] >= policy.min_input_coverage]
    if not usable:
        audit["reason"] = "no page tool covers this phase's input.json" if tools else "the page offers no WebMCP tools"
        _write(output_dir, audit)
        return audit
    best = max(usable, key=lambda c: (c["coverage"], len(c["mapped_inputs"])))
    try:
        result = await call_tool(page, str(best["tool"]), best["args"], tools=tools, policy=policy)
        await page.wait_for_timeout(policy.settle_ms)
        audit.update({
            "filled": not result.get("is_error"), "tool": best["tool"], "coverage": best["coverage"],
            "mapped_inputs": [p.rsplit(".", 1)[-1] for p in best["mapped_inputs"]],
            "unmapped_inputs": [p.rsplit(".", 1)[-1] for p in best["unmapped_inputs"]],
            "tool_reported_error": bool(result.get("is_error")),
            "tool_result": mask_sensitive_string(str(result.get("text") or ""))[:300],
        })
    except Exception as exc:
        from .environment_faults import raise_if_environment_fatal

        raise_if_environment_fatal(exc)
        audit.update({"filled": False, "tool": best["tool"], "error": mask_sensitive_string(str(exc))[:300]})
    _write(output_dir, audit)
    return audit


async def probe_pages(
    config: Any, urls: Mapping[str, str], *, run_dir: Path, call: str = "", args: Optional[Mapping[str, Any]] = None,
    gate: Optional[Mapping[str, Any]] = None, browser: Any = None,
) -> Dict[str, Any]:
    """Open each phase link and list the WebMCP tools its page offers (optionally call one)."""
    from .browser_session import BrowserSession

    policy = webmcp_policy(config)
    report: Dict[str, Any] = {"schema_version": SCHEMA, "policy": policy.to_dict(), "pages": [], "values_stored": False}
    owned = browser is None
    session = browser or BrowserSession(config, Path(run_dir))
    try:
        if owned:
            await session.start()
        for phase, url in urls.items():
            row: Dict[str, Any] = {"phase": phase, "url": session._evidence_url(url) if hasattr(session, "_evidence_url") else url}
            try:
                await session.goto_base_and_complete_sso(url)
                page = await session._ensure_active_page(url)
                row["layer"] = await ensure_installed(page, policy)
                tools = await list_tools(page, policy)
                row["tools"] = catalog(tools)
                row["page_tool_count"] = sum(1 for t in tools if t.get("source") != "hip-agent")
                if call and len(urls) == 1:
                    try:
                        result = await call_tool(page, call, args or {}, tools=tools, gate=gate, policy=policy)
                        row["call"] = {k: (mask_sensitive_string(str(v))[:2000] if k == "text" else v)
                                       for k, v in result.items() if k != "json"}
                    except WebMCPToolRefused as exc:
                        row["call"] = {"tool": call, "refused": mask_sensitive_string(str(exc))[:400]}
            except Exception as exc:
                row["error"] = mask_sensitive_string(str(exc))[:400]
            report["pages"].append(row)
    finally:
        if owned:
            try:
                await session.close()
            except Exception:
                pass
    report["pass"] = bool(report["pages"]) and not any(p.get("error") for p in report["pages"])
    safe_write_json(Path(run_dir) / "webmcp_tools.json", mask_sensitive_data(report))
    return report


def _write(output_dir: Optional[Path], audit: Mapping[str, Any]) -> None:
    if output_dir is None:
        return
    try:
        safe_write_json(Path(output_dir) / "webmcp_page_tools.json", mask_sensitive_data(dict(audit)))
    except Exception:
        pass


__all__ = [
    "HIP_AGENT_TOOLS_JS", "SCHEMA", "WEBMCP_INIT_JS", "WebMCPPolicy", "WebMCPToolRefused", "call_tool", "catalog",
    "classify_tool", "ensure_installed", "fill_with_page_tools", "init_script", "install_on_context", "list_tools",
    "map_input_to_tool", "probe_pages", "webmcp_policy",
]
