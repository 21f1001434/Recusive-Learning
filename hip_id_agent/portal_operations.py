"""Run portal operations from input.json: create, edit, clone, merge, deploy ... (V243R19).

input.json names what to do in an ``operations`` list::

    "operations": [
      {"phase": "source_transport_profile", "operation": "edit",
       "target": "SFTP_U-HAUL_ASN_PC_SRC_IB", "values": {...}, "commit": true},
      {"phase": "source_transport_profile", "operation": "deploy",
       "target": "SFTP_U-HAUL_ASN_PC_SRC_IB", "values": {"target_environment": "UAT"}, "commit": true}
    ]

``values`` defaults to ``objects.<phase>`` of the same file.  For each operation
the runner:

1. opens the surface: the listing, a search for the target, then the row's
   action (directly or from its "More actions" menu); ``create`` opens + Add;
2. fills it with the certified-skill goal engine: a known skill for this
   phase + operation + branch is replayed deterministically; anything new is
   learned adaptively and proved by a replay on a freshly reopened surface
   before it is saved (``portal_skills``);
3. commits (Save / Deploy / Merge ...) only when ``commit`` is true, the fill
   was exact, the skill is certified and the three-part mutation gate is open
   (``--allow-portal-mutation``, ``HIP_ALLOW_PORTAL_MUTATION=YES`` and the phrase
   ``ALLOW HIP MUTATION``).  A commit is clicked at most once, never retried
   blindly;
4. verifies the effect: the surface closes or reports success, and the listing
   shows the object (and any ``expect`` text such as a new status).

Create / edit / clone fill the phase form itself.  Merge, deploy and other
row actions open their own dialog, which is learned as its own form
(``universal_<phase>_<operation>``).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .portal_skills import COMMIT_LABELS, PortalSkillStore, canonical_operation, operation_specs
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.portal-operation.v1"
MUTATION_CONFIRMATION = "ALLOW HIP MUTATION"
PHASE_FORM_OPERATIONS = {"create", "edit", "clone"}
OPENER_LABELS: Dict[str, Sequence[str]] = {
    "edit": ("Edit", "Update", "Modify"),
    "clone": ("Clone", "Copy", "Duplicate"),
    "merge": ("Merge",),
    # V243R24: objects without a Deploy (Document Types) make a version available
    # in an environment with Migrate.
    "deploy": ("Deploy", "Migrate", "Promote"),
    "migrate": ("Migrate", "Promote"),
    "validate": ("Validate",),
    "delete": ("Delete", "Remove"),
}
ADD_LABELS = ("+ Add", "Add", "+ Create", "New", "+ New")
NAME_KEYS = (
    "profile_name", "name", "map_name", "map_identifier", "business_flow_name", "rule_name",
    "document_type_name", "flow_name",
)
# The browser safety guard blocks these words unless explicitly authorized.
_GUARDED = re.compile(r"\b(save|create|submit|delete|remove|deploy|publish|update|enable|disable|confirm)\b", re.I)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").split())


class OperationNeedsInput(Exception):
    """V243R24: a value the operation needs is missing, ambiguous or not offered.

    Never guessed: the operation stops before any change and says which value
    it needs, what the portal offers and where the value can come from.
    """

    def __init__(self, field: str, reason: str, *, offered: Sequence[str] = (), observed: Any = None, source: str = ""):
        super().__init__(f"HIP_OPERATION_NEEDS_INPUT: {field}: {reason}")
        self.detail = {"status": "NEEDS_INPUT", "field": field, "reason": reason, "offered": [str(x) for x in offered][:12],
                       "observed_existing_value": observed, "suggested_source": source or "the task text or input.json"}


# V243R24: what an operation's status means for the requester.
RESULTS = {
    "committed_and_verified": "SUCCESS", "committed": "SUCCESS", "filled_not_committed": "SUCCESS",
    "validated_not_committed": "SUCCESS", "already_in_target": "EXISTING", "no_change_needed": "EXISTING",
    "edit_section_learned": "SUCCESS", "clone_section_learned": "SUCCESS", "deploy_section_learned": "SUCCESS",
    "migrate_section_learned": "SUCCESS", "deploy_section_learned_partly": "SUCCESS", "migrate_section_learned_partly": "SUCCESS",
    "deploy_needs_authorized_run": "BLOCKED", "migrate_needs_authorized_run": "BLOCKED",
    "needs_input": "NEEDS_INPUT", "blocked_mutation_authorization": "BLOCKED", "commit_not_authorized": "BLOCKED",
    "commit_waiting_for_certified_skill": "BLOCKED",
}


def operation_result(status: str) -> str:
    return RESULTS.get(str(status or ""), "FAILED")


def _same_value(a: Any, b: Any) -> bool:
    x, y = _norm(a), _norm(b)
    if x == y:
        return True
    truthy = {"true", "enable", "enabled", "yes", "on", "active"}
    falsy = {"false", "disable", "disabled", "no", "off", "inactive"}
    if (x in truthy and y in truthy) or (x in falsy and y in falsy):
        return True
    try:
        return bool(re.fullmatch(r"[\d.]+", x) and re.fullmatch(r"[\d.]+", y) and float(x) == float(y))
    except ValueError:
        return False


def _label_key(value: Any) -> str:
    return re.sub(r"\s*[*:]\s*$", "", " ".join(str(value or "").lower().split()))


def operation_gate(allow_portal_mutation: bool, confirmation: str) -> Dict[str, Any]:
    env_ok = str(os.getenv("HIP_ALLOW_PORTAL_MUTATION", "")).strip().upper() == "YES"
    phrase_ok = str(confirmation or "").strip() == MUTATION_CONFIRMATION
    return {
        "pass": bool(allow_portal_mutation and env_ok and phrase_ok), "explicit_flag": bool(allow_portal_mutation),
        "environment_gate": env_ok, "confirmation_gate": phrase_ok, "required_confirmation": MUTATION_CONFIRMATION,
    }


def _name_in(values: Mapping[str, Any]) -> str:
    for key in NAME_KEYS:
        if isinstance(values.get(key), str) and values.get(key):
            return str(values[key])
    for child in values.values():
        if isinstance(child, Mapping):
            found = _name_in(child)
            if found:
                return found
    return ""


def _verify_name(spec: Mapping[str, Any], operation: str, values: Mapping[str, Any], target: str) -> str:
    """Which listing row proves the commit: the saved object, or a merge's target."""
    if spec.get("verify_name"):
        return str(spec["verify_name"])
    if operation in {"create", "clone", "edit"} and _name_in(values):
        return _name_in(values)
    if operation == "merge":
        # The source may be merged away; the object merged into must show it.
        for key, value in values.items():
            if isinstance(value, str) and value and re.search(r"into|target|destination", str(key), re.I):
                return value
    return target


def form_phase_for(phase: str, operation: str, spec: Mapping[str, Any]) -> str:
    """The form an operation fills: the phase form, or the action's own dialog."""
    form = str(spec.get("form") or "").lower()
    if form == "phase" or (not form and operation in PHASE_FORM_OPERATIONS):
        return phase
    return f"universal_{phase}_{operation}"


_ROW_ACTION_JS = r"""
({target, labels, token}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const textOf = el => norm(el.getAttribute('aria-label') || el.innerText || el.textContent || el.getAttribute('title') || el.value);
  const wanted = labels.map(norm).filter(Boolean);
  const t = norm(target);
  // A row whose own cell is exactly the target beats one that only contains it
  // ("TP_BETA" must not open "TP_BETA_COPY").
  const exact = r => !!t && Array.from(r.querySelectorAll('td,th,[role=cell],[role=gridcell],a,span')).some(c => norm(c.innerText) === t);
  // An expanded row's detail panel is not a data row.
  const isPanel = r => /--expanded\b/.test(String(r.className || '')) || !!r.querySelector('[class*=expandable-content]');
  let rows = Array.from(document.querySelectorAll('tr,[role=row],.dds__table__row')).filter(visible)
    .filter(r => !isPanel(r) && !r.querySelector('[role=columnheader],th'));
  if (!rows.length) rows = Array.from(document.querySelectorAll('[class*=card],li')).filter(visible);
  const matching = rows.filter(r => !t || norm(r.innerText).includes(t));
  const exacts = matching.filter(exact);
  if (t && (exacts.length > 1 || (!exacts.length && matching.length > 1))) {
    // V243R24: never pick one of several candidates.
    return {found: 'ambiguous', rows: rows.length,
            candidates: (exacts.length ? exacts : matching).slice(0, 8).map(r => (r.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 120))};
  }
  const chosen = (exacts.length ? exacts : matching)
    .sort((a, b) => (exact(b) - exact(a)) || ((a.innerText || '').length - (b.innerText || '').length));
  const pick = root => {
    const els = Array.from(root.querySelectorAll('button,a,[role=button],[role=menuitem],[role=link]')).filter(visible);
    for (const w of wanted) {
      const hit = els.find(e => textOf(e) === w) || els.find(e => textOf(e).split(/[^a-z0-9+]+/).includes(w));
      if (hit) return hit;
    }
    return null;
  };
  for (const row of chosen) {
    const hit = pick(row);
    if (hit) { hit.setAttribute('data-hip-op', token); return {found: 'row_action', label: textOf(hit), rows: rows.length}; }
  }
  for (const row of chosen) {
    // V243R24: the row's expander (chevron): its actions live in the expanded panel.
    const exp = Array.from(row.querySelectorAll('button,[role=button]')).filter(visible).find(e =>
      /expand|collapse/i.test((e.getAttribute('aria-label') || '') + ' ' + String(e.className || ''))
      || (e.hasAttribute('aria-expanded') && !e.getAttribute('aria-haspopup') && !norm(e.innerText)));
    if (exp) {
      exp.setAttribute('data-hip-op', token); row.setAttribute('data-hip-row', token);
      return {found: 'expander', expanded: exp.getAttribute('aria-expanded') === 'true', label: textOf(exp) || 'expand the row', rows: rows.length,
              badges: Array.from(row.querySelectorAll('[class*=badge]')).map(e => norm(e.innerText).toUpperCase()).filter(Boolean)};
    }
  }
  for (const row of chosen) {
    const els = Array.from(row.querySelectorAll('button,a,[role=button]')).filter(visible);
    const menu = els.find(e => e.getAttribute('aria-haspopup') || /\b(more|actions|options|menu)\b|⋮|…|\.\.\./.test(textOf(e)));
    if (menu) { menu.setAttribute('data-hip-op', token); return {found: 'menu', label: textOf(menu), rows: rows.length}; }
  }
  return {found: '', rows: rows.length, matching: matching.length};
}
"""

# V243R24: the detail panel a row's expander opened (Document Types: description,
# environment tabs, Version, Edit / Clone / Migrate), proven to belong to that row.
_PANEL_JS = r"""
({token, target}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const labelledHost = (root, want) => {
    // A dropdown / select named `want` by its own label, placeholder or aria-label,
    // else the first one after a caption such as "Version :".
    const hosts = Array.from(root.querySelectorAll('dds-dropdown,select'));
    const own = h => { const l = h.querySelector && h.querySelector('label'); const i = h.querySelector && h.querySelector('input');
      return norm((l && l.innerText) || h.getAttribute('aria-label') || (i && (i.getAttribute('aria-label') || i.getAttribute('placeholder'))) || ''); };
    let host = hosts.find(h => own(h).replace(/\s*[*:]\s*$/, '') === want) || hosts.find(h => own(h).includes(want));
    if (!host) {
      const cap = Array.from(root.querySelectorAll('label,span,div,b,strong,p')).find(e => !e.children.length && new RegExp('^' + want + '\\s*:?$').test(norm(e.innerText)));
      if (cap) host = hosts.find(h => cap.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_FOLLOWING);
    }
    return host || null;
  };
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const textOf = el => norm(el.innerText || el.textContent || el.getAttribute('aria-label') || el.value);
  const row = document.querySelector(`[data-hip-row="${token}"]`);
  if (!row) return {found: false, reason: 'row_not_rendered'};
  const exp = row.querySelector(`[data-hip-op="${token}"]`);
  const expanded = exp ? exp.getAttribute('aria-expanded') : null;
  const isPanel = el => !!el && (/expand/i.test(String(el.className || '')) || !!el.querySelector('[class*=expandable-content]'));
  let panel = null;
  const owned = exp && exp.getAttribute('aria-controls');
  if (owned) panel = document.getElementById(owned);
  if (!panel) { let s = row.nextElementSibling; while (s && !visible(s)) s = s.nextElementSibling; if (isPanel(s)) panel = s; }
  if (!panel && row.parentElement) { const s = row.parentElement.nextElementSibling; if (isPanel(s)) panel = s; }
  if (!panel || !visible(panel)) return {found: false, expanded};
  panel.setAttribute('data-hip-panel', token);
  const t = norm(target);
  const values = Array.from(panel.querySelectorAll('input,textarea')).map(i => norm(i.value));
  const text = norm(panel.innerText);
  const verified_by = t && values.includes(t) ? 'name_field' : (t && text.includes(t) ? 'panel_text' : (expanded === 'true' ? 'expanded_adjacent_row' : 'adjacent_row'));
  const tabs = Array.from(panel.querySelectorAll('[role=tab]')).filter(visible).map(e => ({
    text: textOf(e).toUpperCase(), selected: e.getAttribute('aria-selected') === 'true',
    disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true'}));
  const actions = Array.from(panel.querySelectorAll('button,a,[role=button]')).filter(visible)
    .filter(e => !e.closest('[role=tablist],[role=menu],[role=listbox],dds-dropdown,.dds__dropdown')).map(textOf).filter(Boolean);
  const labelled = {};
  panel.querySelectorAll('input,textarea,select').forEach(el => {
    let label = '';
    if (el.id) { const l = panel.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) label = l.innerText; }
    if (!label) { const g = el.closest('.dds__form-group,dds-dropdown'); const l = g && g.querySelector('label,.dds__label'); if (l) label = l.innerText; }
    label = norm(label || el.getAttribute('aria-label') || el.getAttribute('placeholder')).replace(/\s*[*:]\s*$/, '');
    if (label && !(label in labelled)) labelled[label] = (el.value || '').trim();
  });
  const versionHost = labelledHost(panel, 'version');
  const versions = versionHost ? Array.from(versionHost.querySelectorAll('option,[role=option]')).map(o => (o.innerText || o.textContent || '').trim()).filter(Boolean) : [];
  const versionInput = versionHost ? (versionHost.tagName === 'SELECT' ? versionHost : versionHost.querySelector('input')) : null;
  const desc = (panel.innerText.match(/description\s*:\s*([^\n]*)/i) || [])[1] || '';
  // V243R31: a Version shown as text ("Version : 1.0") rather than as a dropdown.
  const shownVersion = versionInput ? String(versionInput.value || '').trim() : ((panel.innerText.match(/\bversion\s*:\s*([0-9][0-9.]*)/i) || [])[1] || '');
  return {found: true, verified_by, expanded, tabs, actions, details: labelled, version: shownVersion,
          versions: versions.length ? versions : (shownVersion ? [shownVersion] : []), description: desc.trim().slice(0, 200)};
}
"""

_PANEL_PICK_JS = r"""
({token, kind, value, label, pickToken}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const labelledHost = (root, want) => {
    // A dropdown / select named `want` by its own label, placeholder or aria-label,
    // else the first one after a caption such as "Version :".
    const hosts = Array.from(root.querySelectorAll('dds-dropdown,select'));
    const own = h => { const l = h.querySelector && h.querySelector('label'); const i = h.querySelector && h.querySelector('input');
      return norm((l && l.innerText) || h.getAttribute('aria-label') || (i && (i.getAttribute('aria-label') || i.getAttribute('placeholder'))) || ''); };
    let host = hosts.find(h => own(h).replace(/\s*[*:]\s*$/, '') === want) || hosts.find(h => own(h).includes(want));
    if (!host) {
      const cap = Array.from(root.querySelectorAll('label,span,div,b,strong,p')).find(e => !e.children.length && new RegExp('^' + want + '\\s*:?$').test(norm(e.innerText)));
      if (cap) host = hosts.find(h => cap.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_FOLLOWING);
    }
    return host || null;
  };
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const panel = document.querySelector(`[data-hip-panel="${token}"]`);
  if (!panel) return {found: false, reason: 'panel_gone'};
  const same = (a, b) => { const x = norm(a), y = norm(b); if (x === y) return true;
    const fx = parseFloat(x), fy = parseFloat(y); return !isNaN(fx) && !isNaN(fy) && /^[\d.]+$/.test(x) && /^[\d.]+$/.test(y) && fx === fy; };
  if (kind === 'tab') {
    const tab = Array.from(panel.querySelectorAll('[role=tab]')).filter(visible).find(e => same(e.innerText || e.textContent, value));
    if (!tab) return {found: false};
    tab.setAttribute('data-hip-op', pickToken);
    return {found: true, selected: tab.getAttribute('aria-selected') === 'true', disabled: !!tab.disabled || tab.getAttribute('aria-disabled') === 'true'};
  }
  if (kind === 'action') {
    const els = Array.from(panel.querySelectorAll('button,a,[role=button]')).filter(visible)
      .filter(e => !e.closest('[role=tablist],[role=menu],[role=listbox],dds-dropdown,.dds__dropdown'));
    for (const w of value.map(norm)) {
      const hit = els.find(e => norm(e.innerText || e.getAttribute('aria-label')) === w);
      if (hit) { hit.setAttribute('data-hip-op', pickToken);
        return {found: true, label: norm(hit.innerText || hit.getAttribute('aria-label')), menu: !!(hit.getAttribute('aria-controls') || hit.getAttribute('aria-haspopup')),
                controls: hit.getAttribute('aria-controls') || ''}; }
    }
    return {found: false};
  }
  // kind === 'dropdown': the control labelled `label` (Version ...)
  const host = labelledHost(panel, norm(label));
  if (!host) return {found: false};
  if (host.tagName === 'SELECT') {
    const opt = Array.from(host.options).find(o => same(o.text, value));
    if (!opt) return {found: false, options: Array.from(host.options).map(o => o.text)};
    host.setAttribute('data-hip-op', pickToken); return {found: true, native: true, option: opt.value};
  }
  const input = host.querySelector('input,[role=combobox]');
  if (!input) return {found: false};
  if (same(input.value, value)) return {found: true, already: true};
  input.setAttribute('data-hip-op', pickToken);
  return {found: true, native: false, listbox: input.getAttribute('aria-controls') || ''};
}
"""

_OPTION_JS = r"""
({listbox, value, token}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const same = (a, b) => { const x = norm(a), y = norm(b); if (x === y) return true;
    const fx = parseFloat(x), fy = parseFloat(y); return !isNaN(fx) && !isNaN(fy) && /^[\d.]+$/.test(x) && /^[\d.]+$/.test(y) && fx === fy; };
  const root = (listbox && document.getElementById(listbox)) || document;
  const opts = Array.from(root.querySelectorAll('[role=option]')).filter(o => o.getBoundingClientRect().height > 0);
  const hit = opts.find(o => same(o.innerText || o.textContent, value));
  if (!hit) return {found: false, options: opts.map(o => (o.innerText || '').trim()).slice(0, 20)};
  hit.setAttribute('data-hip-op', token); return {found: true};
}
"""

_MENU_ITEMS_JS = r"""
({controls}) => {
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const root = (controls && document.getElementById(controls)) || null;
  const scopes = root ? [root] : Array.from(document.querySelectorAll('[role=menu],.dds__action-menu,[role=listbox]')).filter(visible);
  const items = [];
  scopes.forEach(s => s.querySelectorAll('[role=menuitem],[role=option],button,li').forEach(e => {
    if (!visible(e)) return; const t = (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
    if (t && !items.includes(t)) items.push(t);
  }));
  return items;
}
"""

# V243R31: what a row action opened -- a menu of choices (Migrate > TEST1), a
# dialog or drawer with fields (Deploy with a Target Environment), or a
# confirmation without fields ("Deploy X from DEV to TEST1?").  A popup
# attribute on the button does not say which: the page after the click does.
_OPENED_JS = r"""
({controls}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const own = controls ? document.getElementById(controls) : null;
  const menus = own ? [own] : Array.from(document.querySelectorAll('[role=menu],.dds__action-menu'));
  const items = [];
  menus.filter(visible).forEach(m => m.querySelectorAll('[role=menuitem],[role=option],button,li').forEach(e => {
    const t = norm(e.innerText || e.textContent); if (visible(e) && t && !items.includes(t)) items.push(t); }));
  if (items.length) return {kind: 'menu', items};
  const dialogs = Array.from(document.querySelectorAll('[role=dialog],[role=alertdialog],.dds__modal,dds-drawer,.dds__drawer')).filter(visible);
  const top = dialogs[dialogs.length - 1];
  if (!top) return {kind: 'none'};
  const text = norm(top.innerText);
  if (/^loading\b|\bloading\.\.\./i.test(text)) return {kind: 'loading'};
  if (top.querySelector('input:not([type=hidden]),textarea,select,[role=combobox]')) return {kind: 'form', title: norm(top.getAttribute('aria-label') || '')};
  const buttons = Array.from(top.querySelectorAll('button,[role=button]')).filter(visible).map(b => norm(b.innerText || b.getAttribute('aria-label'))).filter(Boolean);
  if (!buttons.length) return {kind: 'loading'};
  // The question itself, without the dialog's title and button captions.
  const body = top.querySelector('p,.dds__modal__body,[class*=modal-body],[class*=message]');
  const question = norm(body ? body.innerText : text);
  return {kind: 'confirm', text: question.slice(0, 400), buttons, title: norm(top.getAttribute('aria-label') || '')};
}
"""

_CONFIRM_JS = r"""
({labels, token}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const dialogs = Array.from(document.querySelectorAll('[role=dialog],[role=alertdialog],.dds__modal')).filter(visible)
    .filter(d => !d.querySelector('form input:not([type=hidden]),textarea'));
  for (const d of dialogs.reverse()) {
    const els = Array.from(d.querySelectorAll('button,[role=button]')).filter(visible);
    for (const w of labels.map(norm)) {
      const hit = els.find(e => norm(e.innerText || e.getAttribute('aria-label')) === w);
      if (hit) { hit.setAttribute('data-hip-op', token); return {found: true, label: norm(hit.innerText), dialog: norm(d.innerText).slice(0, 200)}; }
    }
  }
  return {found: false};
}
"""

# V243R24: the open form's labelled values, before and after the fill -- only
# the requested fields may change.
_FORM_SNAPSHOT_JS = r"""
() => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const surfaces = Array.from(document.querySelectorAll('[role=dialog],dds-drawer,.dds__drawer,.dds__modal,form')).filter(visible);
  const scope = surfaces.length ? surfaces[surfaces.length - 1] : document;
  const labelOf = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    const by = el.getAttribute('aria-labelledby'); if (by) { const l = document.getElementById(by.split(' ')[0]); if (l) return l.innerText; }
    const g = el.closest('.dds__form-group,dds-dropdown'); const l = g && g.querySelector('label,.dds__label');
    return (l && l.innerText) || el.getAttribute('placeholder') || el.getAttribute('name') || '';
  };
  const out = {}; const counts = {};
  scope.querySelectorAll('input,textarea,select').forEach(el => {
    const type = String(el.type || '').toLowerCase();
    if (['hidden', 'button', 'submit', 'file'].includes(type)) return;
    const dd = el.closest('dds-dropdown');
    if (!dd && !visible(el)) return;
    if (dd && el !== dd.querySelector('input')) return;
    let value;
    if (dd) { const tags = Array.from(dd.querySelectorAll('.dds__tag')).map(t => norm(t.innerText)).filter(Boolean); value = tags.length ? tags.join(', ') : norm(el.value); }
    else if (type === 'checkbox') value = el.checked ? 'true' : 'false';
    else if (type === 'radio') { if (!el.checked) return; const l = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`); value = norm((l && l.innerText) || el.value); }
    else value = norm(el.value);
    const label = norm(type === 'radio' ? (el.getAttribute('name') || '') : labelOf(el)).toLowerCase().replace(/\s*[*:]\s*$/, '');
    if (!label) return;
    const n = counts[label] = (counts[label] === undefined ? 0 : counts[label] + 1);
    out[`${label}#${n}`] = String(value).slice(0, 300);
  });
  return out;
}
"""

_MENU_ITEM_JS = r"""
({labels, token}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const textOf = el => norm(el.getAttribute('aria-label') || el.innerText || el.textContent);
  const items = Array.from(document.querySelectorAll('[role=menuitem],[role=menu] button,[role=menu] a,[role=menu] li,.dds__action-menu__option,.dds__dropdown__item-option')).filter(visible);
  for (const w of labels.map(norm)) {
    const hit = items.find(e => textOf(e) === w);
    if (hit) { hit.setAttribute('data-hip-op', token); return {found: true, label: textOf(hit)}; }
  }
  return {found: false};
}
"""

_PAGE_BUTTON_JS = r"""
({labels, token, surfaceOnly}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.disabled; };
  const textOf = el => norm(el.innerText || el.textContent || el.getAttribute('aria-label') || el.value);
  let scope = document;
  if (surfaceOnly) {
    const surfaces = Array.from(document.querySelectorAll('[role=dialog],dds-drawer,.dds__drawer,.dds__modal,form')).filter(visible);
    if (surfaces.length) scope = surfaces[surfaces.length - 1];
  }
  const els = Array.from(scope.querySelectorAll('button,a,[role=button],input[type=submit]')).filter(visible)
    .filter(e => !e.closest('tr,[role=row],[role=menu]'));
  for (const w of labels.map(norm)) {
    const hit = els.find(e => textOf(e) === w);
    if (hit) { hit.setAttribute('data-hip-op', token); return {found: true, label: textOf(hit)}; }
  }
  return {found: false};
}
"""

_EFFECT_JS = r"""
({token}) => {
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const texts = sel => Array.from(document.querySelectorAll(sel)).filter(visible).map(e => (e.innerText || '').trim()).filter(Boolean);
  const errors = texts('[role=alert],.toast--error,.dds__notification--error,.dds__invalid-feedback,.error-message');
  const notes = texts('[role=status],.toast,.dds__notification,.dds__toast');
  const button = document.querySelector(`[data-hip-op="${token}"]`);
  return {url: location.href, errors, notes, button_visible: visible(button)};
}
"""


class PortalOperationRunner:
    """Opens, fills, commits and verifies input.json operations in one browser."""

    def __init__(self, config: Any, browser: Any, run_dir: Path, *, listing_urls: Optional[Mapping[str, str]] = None):
        self.config = config
        self.browser = browser
        self.run_dir = Path(run_dir)
        self.listing_urls = dict(listing_urls or {})
        ops_cfg = getattr(config, "portal_operations", None)
        self.require_certified = bool(getattr(ops_cfg, "commit_requires_certified_skill", True))
        self.verify_after_commit = bool(getattr(ops_cfg, "verify_after_commit", True))
        self.effect_timeout = float(getattr(ops_cfg, "commit_effect_timeout_seconds", 20.0) or 20.0)
        # V243R24: an Edit / Clone that changed fields nobody asked for is not saved.
        self.block_unrelated_changes = bool(getattr(ops_cfg, "block_unrelated_changes", True))
        # V243R30: the Edit section is read first, read-only fields guarded, the save read back.
        edit_cfg = getattr(config, "edit_sections", None)
        self.edit_capture = bool(getattr(edit_cfg, "enabled", True)) and bool(getattr(edit_cfg, "capture_on_edit", True))
        self.block_read_only = bool(getattr(edit_cfg, "block_read_only_changes", True))
        self.verify_by_edit = bool(getattr(edit_cfg, "verify_by_reopening_edit", True))
        # V243R32: a Whitelabel Error Page -> close/reopen the browser and perform the
        # operation again from its listing (only when nothing was saved yet).
        heal_cfg = getattr(config, "runtime_self_heal", None)
        self.whitelabel_restarts = max(0, int(getattr(heal_cfg, "whitelabel_browser_restarts", 3) or 0))
        self._commit_clicked = False

    # ------------------------------------------------------------------ helpers
    def _listing_url(self, phase: str) -> str:
        if phase in self.listing_urls:
            return self.listing_urls[phase]
        from .dummy_fill_e2e import PHASE_URLS

        return str(PHASE_URLS.get(phase) or "")

    async def _page(self, url: str = "") -> Any:
        return await self.browser._ensure_active_page(url or getattr(self.browser, "_active_target_url", "") or "")

    async def _navigate(self, url: str) -> None:
        await self.browser.goto_base_and_complete_sso(url)
        page = await self._page(url)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await self.browser.wait_ready()
        from .environment_faults import raise_if_whitelabel

        await raise_if_whitelabel(page, url)

    async def _click_token(self, token: str, label: str, *, mutation: bool = False) -> None:
        page = await self._page()
        selector = f'[data-hip-op="{token}"]'
        await self.browser.click_and_wait(action=label, locator=page.locator(selector), selector=selector, mutation_risk=mutation)

    async def _search(self, target: str) -> Dict[str, Any]:
        page = await self._page()
        token = uuid.uuid4().hex[:10]
        found = await page.evaluate(
            r"""(token) => {
              const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
              const box = Array.from(document.querySelectorAll('input[type=search],input[placeholder],input[aria-label]'))
                .filter(visible).find(e => /search|filter/i.test(e.type + ' ' + (e.placeholder || '') + ' ' + (e.getAttribute('aria-label') || '')));
              if (!box) return false;
              box.setAttribute('data-hip-op', token); return true;
            }""", token)
        if not found:
            return {"searched": False}
        selector = f'[data-hip-op="{token}"]'
        locator = page.locator(selector)
        await self.browser.fill_and_log(locator=locator, value=target, selector=selector, action_type="search")
        try:
            await self.browser.press_and_log(locator=locator, key="Enter", selector=selector)
        except Exception:
            pass
        await page.wait_for_timeout(400)
        return {"searched": True}

    async def _wait_for_form(self, form_phase: str, timeout_s: float = 12.0) -> bool:
        from .stateful_form_runtime import capture_stateful_controls

        page = await self._page()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if await capture_stateful_controls(page, form_phase):
                    return True
            except Exception:
                pass
            await page.wait_for_timeout(250)
        return False

    async def _find_row(self, page: Any, target: str, labels: Sequence[str], token: str, timeout_s: float = 6.0) -> Dict[str, Any]:
        """The requested row, once the listing (re-)rendered after the search."""
        deadline = time.monotonic() + timeout_s
        while True:
            hit = await page.evaluate(_ROW_ACTION_JS, {"target": target, "labels": list(labels), "token": token})
            if hit.get("found") or time.monotonic() >= deadline:
                return hit
            await page.wait_for_timeout(300)

    async def _panel(self, token: str, target: str, timeout_s: float = 8.0) -> Dict[str, Any]:
        page = await self._page()
        deadline = time.monotonic() + timeout_s
        while True:
            panel = await page.evaluate(_PANEL_JS, {"token": token, "target": target})
            if panel.get("found") or time.monotonic() >= deadline:
                return panel
            await page.wait_for_timeout(250)

    async def _pick_panel_version(self, token: str, target: str, version: str, label: str = "version") -> Dict[str, Any]:
        page = await self._page()
        pick = uuid.uuid4().hex[:10]
        found = await page.evaluate(_PANEL_PICK_JS, {"token": token, "kind": "dropdown", "label": label, "value": version, "pickToken": pick})
        if not found.get("found"):
            return {"picked": False, "options": found.get("options") or []}
        if found.get("already"):
            return {"picked": True, "already": True}
        selector = f'[data-hip-op="{pick}"]'
        if found.get("native"):
            await page.locator(selector).select_option(value=str(found.get("option")))
            return {"picked": True}
        await self._click_token(pick, label.title())
        option = uuid.uuid4().hex[:10]
        hit: Dict[str, Any] = {}
        for _ in range(12):
            hit = await page.evaluate(_OPTION_JS, {"listbox": found.get("listbox") or "", "value": version, "token": option})
            if hit.get("found"):
                break
            await page.wait_for_timeout(150)
        if not hit.get("found"):
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return {"picked": False, "options": hit.get("options") or []}
        await self._click_token(option, str(version))
        return {"picked": True}

    async def _select_tab(self, token: str, target: str, env: str, panel: Dict[str, Any]) -> Dict[str, Any]:
        page = await self._page()
        pick = uuid.uuid4().hex[:10]
        tab = await page.evaluate(_PANEL_PICK_JS, {"token": token, "kind": "tab", "value": env, "pickToken": pick})
        enabled = [t["text"] for t in panel.get("tabs") or [] if not t.get("disabled")]
        if not tab.get("found"):
            raise OperationNeedsInput("environment", f"{env} is not an environment of this object", offered=enabled, observed=panel.get("tabs"))
        if tab.get("disabled"):
            raise OperationNeedsInput("environment", f"{target!r} is not available in {env}", offered=enabled)
        if not tab.get("selected"):
            await self._click_token(pick, env)
            await page.wait_for_timeout(250)
        return await self._panel(token, target)

    def _learned_targets(self, phase: str, operation: str, source_env: str) -> Optional[List[str]]:
        """V243R31: where this action went from ``source_env`` when it was learned (None: not learned)."""
        from .edit_section_learning import SectionMemory

        try:
            memory = SectionMemory.for_run(self.config, getattr(self.browser, "page", None), action=operation)
            menus = (memory.load(phase).get("menus") or {}) if memory is not None else {}
        except Exception:
            return None
        items = menus.get(str(source_env or "").upper())
        return [str(x) for x in items] if items is not None else None

    def _route_hint(self, phase: str, source_env: str, target_env: str) -> str:
        """"PROD is reached through DEV > TEST2 > PROD" from the learned Migrate / Deploy menus."""
        from .edit_section_learning import SectionMemory

        if not source_env or not target_env:
            return ""
        for action in ("migrate", "deploy"):
            try:
                memory = SectionMemory.for_run(self.config, getattr(self.browser, "page", None), action=action)
                route = memory.route(phase, source_env, target_env) if memory is not None else []
            except Exception:
                route = []
            if len(route) > 2:
                return f"; {target_env} is reached through {' > '.join(route)} ({action} to {route[1]} first)"
        return ""

    async def _open_panel_action(
        self, hit: Mapping[str, Any], *, token: str, target: str, labels: Sequence[str], options: Mapping[str, Any],
        audit: Dict[str, Any], operation: str, phase: str = "",
    ) -> None:
        """V243R24: expand the row, prove the panel is that row's, choose the
        environment tab and version the task names, then click the action."""
        page = await self._page()
        if not hit.get("expanded"):
            await self._click_token(token, "Expand the row")
        audit["path"].append("expand row")
        audit["selectors"].append({"role": "button", "name": "Expand the row", "context": "row whose name cell is exactly the requested object"})
        panel = await self._panel(token, target)
        if not panel.get("found"):
            raise RuntimeError(f"HIP_OPERATION_ROW_PANEL_NOT_FOUND: the expanded details of {target!r} did not appear")
        audit["row_badges"] = list(hit.get("badges") or [])
        env = str(options.get("environment") or "").strip().upper()
        if env:
            panel = await self._select_tab(token, target, env, panel)
            audit["path"].append(f"tab:{env}")
        version = str(options.get("version") or "").strip()
        if version:
            picked = await self._pick_panel_version(token, target, version)
            if not picked.get("picked"):
                raise OperationNeedsInput("version", f"version {version} is not offered for {target!r}", offered=picked.get("options") or panel.get("versions") or [],
                                          observed=panel.get("version"))
            panel = await self._panel(token, target)
            audit["path"].append(f"version:{version}")
        for key, value in options.items():
            # Any other choice on the expanded details (a tab or a labelled
            # dropdown), e.g. {"Sharing": "..."} from input.json "panel".
            if key in {"environment", "version", "target_environment"} or value in (None, ""):
                continue
            pick = uuid.uuid4().hex[:10]
            tab = await page.evaluate(_PANEL_PICK_JS, {"token": token, "kind": "tab", "value": str(value), "pickToken": pick})
            if tab.get("found") and not tab.get("disabled"):
                if not tab.get("selected"):
                    await self._click_token(pick, str(value))
                    await page.wait_for_timeout(250)
            else:
                picked = await self._pick_panel_version(token, target, str(value), label=str(key).lower())
                if not picked.get("picked"):
                    raise OperationNeedsInput(str(key), f"{value!r} is not offered for {key!r} on {target!r}", offered=picked.get("options") or [])
            panel = await self._panel(token, target)
            audit["path"].append(f"{str(key).lower()}:{value}")
        audit["panel"] = {k: panel.get(k) for k in ("verified_by", "tabs", "version", "versions", "description", "actions")}
        audit["before"] = {"environment": next((t["text"] for t in panel.get("tabs") or [] if t.get("selected")), ""),
                           "version": panel.get("version"), "available_environments": audit["row_badges"], "details": panel.get("details") or {}}
        wanted = str(options.get("target_environment") or "").strip().upper()
        if operation in {"migrate", "deploy"} and wanted and wanted in audit["row_badges"]:
            existing = await self._target_holds_version(token, target, wanted, panel)
            audit["target_check"] = existing
            if existing.get("holds_version"):
                audit["existing"] = True
                return
        source_env = audit["before"]["environment"]
        if operation in {"migrate", "deploy"} and wanted and _GUARDED.search(str(labels[0])):
            # V243R31: a guarded action (Deploy) is not even opened for a target it was
            # learned not to offer from this environment.
            learned = self._learned_targets(phase, operation, source_env)
            if learned is not None and not any(_same_value(x, wanted) for x in learned):
                raise OperationNeedsInput(
                    "target_environment", f"{wanted} is not offered by {labels[0]} from {source_env} (learned: "
                    f"{', '.join(learned) or 'nothing'}){self._route_hint(phase, source_env, wanted)}; nothing was clicked",
                    offered=learned, source="an offered environment, or learn-action-sections again if the portal changed")
        pick = uuid.uuid4().hex[:10]
        action = await page.evaluate(_PANEL_PICK_JS, {"token": token, "kind": "action", "value": list(labels), "pickToken": pick})
        if not action.get("found"):
            raise OperationNeedsInput("operation", f"{labels[0]!r} is not an action of {target!r} in {env or 'this environment'}",
                                      offered=panel.get("actions") or [])
        label = str(action.get("label") or labels[0])
        await self._click_token(pick, label, mutation=bool(_GUARDED.search(label)))
        audit["path"].append(label)
        audit["selectors"].append({"role": "button", "name": label.title(), "context": "expanded details of the requested row"})
        if operation not in PHASE_FORM_OPERATIONS:
            await self._note_what_opened(audit, controls=str(action.get("controls") or ""))

    async def _note_what_opened(self, audit: Dict[str, Any], *, controls: str = "", timeout_s: float = 4.0) -> Dict[str, Any]:
        """V243R31: a menu (its choices), a dialog with fields, or a confirmation (its text)."""
        page = await self._page()
        deadline = time.monotonic() + timeout_s
        opened: Dict[str, Any] = {"kind": "none"}
        while True:
            try:
                opened = await page.evaluate(_OPENED_JS, {"controls": controls})
            except Exception:
                opened = {"kind": "none"}  # the action navigated to its own page
            if opened.get("kind") in {"menu", "form", "confirm"} or time.monotonic() >= deadline:
                break
            await page.wait_for_timeout(200)
        audit["opened"] = opened.get("kind")
        if opened.get("kind") == "menu":
            audit["menu_opened"] = True
            audit["menu_items"] = list(opened.get("items") or [])
            audit["menu_controls"] = controls
        elif opened.get("kind") == "confirm":
            audit["confirm_opened"] = True
            audit["confirm"] = {"text": mask_sensitive_string(str(opened.get("text") or "")), "buttons": list(opened.get("buttons") or []),
                                "title": opened.get("title") or ""}
        return opened

    async def _target_holds_version(self, token: str, target: str, env: str, panel: Dict[str, Any]) -> Dict[str, Any]:
        """Does the target environment already hold the selected version?  (Read-only: tab clicks.)"""
        source_env = next((t["text"] for t in panel.get("tabs") or [] if t.get("selected")), "")
        source_version = str(panel.get("version") or "")
        result: Dict[str, Any] = {"target_environment": env, "source_environment": source_env, "source_version": source_version}
        try:
            there = await self._select_tab(token, target, env, panel)
            versions = list(there.get("versions") or ([there.get("version")] if there.get("version") else []))
            result["target_versions"] = versions
            result["holds_version"] = bool(source_version) and any(_same_value(v, source_version) for v in versions)
            if source_env and not result["holds_version"]:
                back = await self._select_tab(token, target, source_env, there)
                if source_version:
                    await self._pick_panel_version(token, target, source_version)
                result["restored"] = bool(back.get("found"))
        except OperationNeedsInput as exc:
            result.update(holds_version=False, error=exc.detail.get("reason"))
        return result

    async def _form_snapshot(self) -> Dict[str, str]:
        page = await self._page()
        try:
            return dict(await page.evaluate(_FORM_SNAPSHOT_JS))
        except Exception:
            return {}

    @staticmethod
    def _requested_fields(form_phase: str, values: Mapping[str, Any]) -> List[Dict[str, Any]]:
        from .stateful_form_runtime import compile_phase_state_graph

        graph = compile_phase_state_graph({"objects": {form_phase: dict(values)}}, form_phase)
        rows: List[Dict[str, Any]] = []
        for node in graph.get("nodes") or []:
            if not isinstance(node, Mapping):
                continue
            loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), Mapping) else {}
            names = [_label_key(x) for x in [*(loc.get("labels") or []), *(loc.get("placeholders") or []), *(loc.get("names") or [])] if str(x).strip()]
            path = str(node.get("input_path") or "")
            top = path.split(".")[3] if path.count(".") == 3 else ""
            rel = ".".join(path.split(".")[3:]) if path.startswith("$.") and path.count(".") >= 3 else path
            rows.append({"field": str(node.get("field_key") or ""), "labels": list(dict.fromkeys(names)), "row_index": node.get("row_index"),
                         "requested": node.get("expected_value"), "top_key": top, "input_path": rel})
        return rows

    @staticmethod
    def _plan_changes(fields: Sequence[Mapping[str, Any]], before: Mapping[str, str]) -> List[Dict[str, Any]]:
        """CURRENT / REQUESTED / CHANGE for every requested field (EDIT SAFETY)."""
        plan: List[Dict[str, Any]] = []
        for f in fields:
            index = int(f.get("row_index") or 0)
            current = next((before[f"{label}#{index}"] for label in f.get("labels") or [] if f"{label}#{index}" in before), None)
            requested = f.get("requested")
            plan.append({"field": f.get("field"), "current": current, "requested": requested,
                         "change": current is None or not _same_value(current, requested), "top_key": f.get("top_key"),
                         "input_path": f.get("input_path")})
        return plan

    # ------------------------------------------------------------------ open
    async def open_surface(
        self, *, phase: str, operation: str, target: str, form_phase: str, options: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = self._listing_url(phase)
        options = dict(options or {})
        audit: Dict[str, Any] = {"listing_url": url, "operation": operation, "path": [], "selectors": []}
        await self._navigate(url)
        page = await self._page(url)
        if operation == "create":
            token = uuid.uuid4().hex[:10]
            hit = await page.evaluate(_PAGE_BUTTON_JS, {"labels": list(ADD_LABELS), "token": token, "surfaceOnly": False})
            if not hit.get("found"):
                # Live portal: the semantic affordance resolver knows the Add drawer.
                await self.browser.click_semantic_affordance(
                    intent="open_add_form", aliases=["Add", "Create"], allow_mutation=False,
                    action_label="Open create form", allow_compound_menu=False,
                )
                audit["path"].append("semantic:open_add_form")
            else:
                await self._click_token(token, str(hit.get("label") or "Add"))
                audit["path"].append(str(hit.get("label")))
            if phase == "biz_flow":
                from .bizflow_kb import _click_bizflow_template_link_after_add, _is_bizflow_form_surface

                if not await _is_bizflow_form_surface(page):
                    audit["bizflow_template"] = mask_sensitive_data(await _click_bizflow_template_link_after_add(page))
        else:
            if target:
                audit["search"] = await self._search(target)
            labels = list(OPENER_LABELS.get(operation) or (operation.replace("_", " ").title(),))
            store = PortalSkillStore.for_run(self.config, page)
            learned = store.opener_labels(phase, operation) if store is not None else []
            if learned:
                # V243R23: the label that opened this action last time comes first.
                labels = list(dict.fromkeys([*learned, *labels]))
                audit["learned_opener_labels"] = learned
            plan = store.opener_plan(phase, operation) if store is not None else {}
            if plan.get("mode"):
                audit["learned_mode"] = plan.get("mode")
            start_net = len(getattr(self.browser, "network_tab_events", []) or [])
            token = uuid.uuid4().hex[:10]
            hit = await self._find_row(page, target, labels, token)
            if hit.get("found") == "ambiguous":
                raise OperationNeedsInput(
                    "target", f"{len(hit.get('candidates') or [])} rows match {target!r} and none (or more than one) is exactly it",
                    offered=hit.get("candidates") or [], source="the exact object name in the task or input.json")
            if not hit.get("found") and target and int(hit.get("rows") or 0) > 0 and not hit.get("matching"):
                raise OperationNeedsInput("target", f"no row named {target!r} on the listing", source="the exact object name")
            if hit.get("found") == "expander":
                await self._open_panel_action(hit, token=token, target=target, labels=labels, options=options, audit=audit, operation=operation,
                                              phase=phase)
                if audit.get("menu_opened") and audit.get("menu_items") and store is not None:
                    # The action's menu (Migrate > TEST1 / TEST2) opened: that is where it lives.
                    store.record_opener(phase, operation, path=list(audit["path"]), label=str(audit["path"][-1]),
                                        selectors=audit.get("selectors") or (), expander=True)
                if audit.get("existing") or audit.get("menu_opened") or audit.get("confirm_opened"):
                    await self._settle_guarded_opener(audit, start_net)
                    return audit
            elif hit.get("found") == "row_action":
                await self._click_token(token, str(hit.get("label") or labels[0]), mutation=bool(_GUARDED.search(labels[0])))
                audit["path"].append(str(hit.get("label")))
                audit["selectors"].append({"role": "button", "name": str(hit.get("label") or ""), "context": "row of the requested object"})
                if operation not in PHASE_FORM_OPERATIONS:
                    await self._note_what_opened(audit, timeout_s=2.0)
                    if audit.get("menu_opened") or audit.get("confirm_opened"):
                        await self._settle_guarded_opener(audit, start_net)
                        return audit
            elif hit.get("found") == "menu":
                await self._click_token(token, str(hit.get("label") or "More actions"))
                audit["path"].append(str(hit.get("label") or "more actions"))
                item_token = uuid.uuid4().hex[:10]
                item = {}
                for _ in range(10):
                    item = await page.evaluate(_MENU_ITEM_JS, {"labels": labels, "token": item_token})
                    if item.get("found"):
                        break
                    await page.wait_for_timeout(150)
                if not item.get("found"):
                    raise RuntimeError(f"HIP_OPERATION_ACTION_NOT_IN_MENU: {operation} for {target!r}")
                await self._click_token(item_token, str(item.get("label") or labels[0]), mutation=bool(_GUARDED.search(labels[0])))
                audit["path"].append(str(item.get("label")))
                if operation not in PHASE_FORM_OPERATIONS:
                    await self._note_what_opened(audit, timeout_s=2.0)
                    if audit.get("confirm_opened"):
                        await self._settle_guarded_opener(audit, start_net)
                        return audit
            else:
                # Icon-only or unusual markup: the live semantic resolver (compound menus).
                await self.browser.click_semantic_affordance(
                    intent=operation, aliases=[labels[0], target], allow_mutation=bool(_GUARDED.search(labels[0])),
                    action_label=labels[0], allow_compound_menu=True,
                )
                audit["path"].append(f"semantic:{operation}")
        if operation in {"edit", "clone"} and form_phase == phase:
            # V243R30: the drawer / page the action opened -- filled by the portal
            # from the record a moment later -- not the listing behind it.
            from .edit_section_learning import wait_for_edit_surface

            wait = float(getattr(getattr(self.config, "edit_sections", None), "form_wait_seconds", 30.0) or 30.0)
            surface = await wait_for_edit_surface(await self._page(), timeout_s=wait)
            audit["edit_surface"] = {k: surface.get(k) for k in ("found", "ready", "kind", "title", "controls")}
        audit["form_visible"] = await self._wait_for_form(form_phase)
        if operation != "create":
            opener = (OPENER_LABELS.get(operation) or (operation.title(),))[0]
            if _GUARDED.search(opener):
                audit["opener_outcome"] = await self._reconcile_opener(start_net, form_visible=bool(audit["form_visible"]))
            if audit["form_visible"]:
                try:
                    store = PortalSkillStore.for_run(self.config, await self._page())
                    clicked = [str(x) for x in audit["path"] if str(x) and ":" not in str(x) and str(x) != "expand row"]
                    if store is not None and audit["path"]:
                        store.record_opener(phase, operation, path=[str(x) for x in audit["path"]], label=clicked[-1] if clicked else "",
                                            selectors=audit.get("selectors") or (), expander="expand row" in audit["path"])
                except Exception:
                    pass
        if audit["form_visible"]:
            # The proven surface's route is now the expected route: the commit
            # guard rejects any drift away from it before Save/Deploy.
            page = await self._page()
            self.browser._active_target_url = str(page.url or url)
            audit["surface_url"] = self.browser._evidence_url(str(page.url or "")) if hasattr(self.browser, "_evidence_url") else ""
        return audit

    # ------------------------------------------------------------------ fill
    async def fill(
        self, *, phase: str, form_phase: str, payload: Dict[str, Any], out_dir: Path, reopen: Any,
    ) -> Dict[str, Any]:
        from .autonomous_form_runtime import execute_autonomous_phase_goal
        from .stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph

        page = await self._page()
        graph = compile_phase_state_graph(payload, form_phase)
        kwargs: Dict[str, Any] = {"executor": execute_document_type_state_graph} if "document_type" in form_phase and not form_phase.startswith("universal_") else {}
        max_cycles = int(getattr(getattr(self.config, "autonomous_form", None), "max_cycles", 4) or 4)
        if form_phase == "biz_flow":
            result = await self._fill_bizflow(page, graph, payload, out_dir, max_cycles)
            learned = (result.get("skill") or {}).get("outcome") or {}
            if (payload.get("_operation") in {"edit", "clone"} and reopen is not None and result.get("pass")
                    and learned.get("status") != "certified"):
                # V243R30: an Edit / Clone page can be opened again as a whole, so the
                # tabs' new skills are proved by a replay now instead of on the next
                # run: reopen the object's form and replay the fill deterministically.
                await reopen()
                proof = await self._fill_bizflow(await self._page(), graph, payload, out_dir / "replay_proof", max_cycles)
                proof["learned_then_replayed"] = True
                proof["execution_mode"] = "learned_then_certified_by_replay" if (proof.get("skill") or {}).get("outcome", {}).get("status") == "certified" else proof.get("execution_mode")
                return proof
            return result
        return await execute_autonomous_phase_goal(
            page=page, graph=graph, phase=form_phase, input_data=payload, config=self.config,
            output_dir=out_dir, max_cycles=max_cycles, repair=True, strict_live_execution=True,
            reopen=reopen, **kwargs,
        )

    async def _fill_bizflow(self, page: Any, graph: Dict[str, Any], payload: Dict[str, Any], out_dir: Path, max_cycles: int) -> Dict[str, Any]:
        """BizFlow tabs: one skill per tab; proved on the next run (a tab cannot be reopened alone)."""
        from .autonomous_form_runtime import execute_autonomous_phase_goal
        from .bizflow_kb import _click_configure_routing_add
        from .dds_control_driver import click_visible_tab

        wizard = [
            ("Flow Details", ["Flow Details", "Basic Details"]),
            ("Configure Source", ["Configure Source", "Source Details"]),
            ("Configure Target(s)", ["Configure Target(s)", "Configure Target", "Target Details"]),
            ("Configure Routing", ["Configure Routing"]),
        ]
        present = {str(n.get("section") or "") for n in graph.get("nodes") or [] if isinstance(n, Mapping)}
        sections: List[Dict[str, Any]] = []
        for name, aliases in wizard:
            if name not in present:
                continue
            await click_visible_tab(page, None, aliases, phase="biz_flow")
            if name == "Configure Routing":
                await _click_configure_routing_add(page)
            result = await execute_autonomous_phase_goal(
                page=page, graph=graph, phase="biz_flow", input_data=payload, config=self.config,
                output_dir=out_dir / re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"), max_cycles=max_cycles,
                repair=True, strict_live_execution=True, section=name,
            )
            sections.append(result)
            if not result.get("pass"):
                break
        passed = bool(sections) and all(r.get("pass") for r in sections)
        statuses = [str(((r.get("skill") or {}).get("outcome") or {}).get("status") or "") for r in sections]
        return {
            "pass": passed, "status": "pass" if passed else "failed_closed", "sections": sections,
            "execution_mode": "+".join(sorted({str(r.get("execution_mode")) for r in sections})),
            "skill": {"outcome": {"status": "certified" if passed and all(s == "certified" for s in statuses) else "candidate"},
                      "sections": [r.get("skill") for r in sections]},
        }

    # ------------------------------------------------------------------ commit
    async def commit(self, *, labels: Sequence[str], gate: Mapping[str, Any], task_id: str, entity: str = "") -> Dict[str, Any]:
        page = await self._page()
        token = uuid.uuid4().hex[:10]
        hit = await page.evaluate(_PAGE_BUTTON_JS, {"labels": list(labels), "token": token, "surfaceOnly": True})
        if not hit.get("found"):
            hit = await page.evaluate(_PAGE_BUTTON_JS, {"labels": list(labels), "token": token, "surfaceOnly": False})
        if not hit.get("found"):
            return {"pass": False, "status": "commit_control_not_found", "labels": list(labels)}
        label = str(hit.get("label") or labels[0])
        return await self._commit_click(token, label, allowed=list(labels), task_id=task_id, entity=entity)

    async def commit_menu_choice(self, *, label: str, gate: Mapping[str, Any], task_id: str, entity: str = "",
                                 confirm_labels: Sequence[str] = ()) -> Dict[str, Any]:
        """V243R24: the mutation is the menu item itself (Migrate > TEST2), plus the
        portal's confirmation dialog when it shows one -- each clicked once."""
        page = await self._page()
        token = uuid.uuid4().hex[:10]
        hit = await page.evaluate(_MENU_ITEM_JS, {"labels": [label], "token": token})
        if not hit.get("found"):
            return {"pass": False, "status": "commit_control_not_found", "labels": [label]}
        return await self._commit_click(token, str(hit.get("label") or label), allowed=[label], task_id=task_id, entity=entity,
                                        confirm_labels=confirm_labels)

    async def _commit_click(self, token: str, label: str, *, allowed: Sequence[str], task_id: str, entity: str = "",
                            confirm_labels: Sequence[str] = ()) -> Dict[str, Any]:
        page = await self._page()
        before_url = page.url
        start_net = len(getattr(self.browser, "network_tab_events", []) or [])
        self._commit_clicked = True  # V243R32: from here on the operation is never repeated
        self.browser.set_portal_mutation_authorization(enabled=True, allowed_labels=[*allowed, *confirm_labels], task_id=task_id)
        click_error = ""
        confirm: Dict[str, Any] = {}
        effect_token = token
        try:
            # Clicked once.  An unclear outcome is reported, never retried blindly.
            await self._click_token(token, label, mutation=True)
            if confirm_labels:
                confirm_token = uuid.uuid4().hex[:10]
                for _ in range(10):
                    confirm = await page.evaluate(_CONFIRM_JS, {"labels": list(confirm_labels), "token": confirm_token})
                    if confirm.get("found"):
                        # The choice only opened the portal's confirmation (no write
                        # was sent): that click is reconciled, and the confirmation
                        # is the one mutation click.
                        opened = await self._reconcile_opener(start_net, form_visible=True)
                        confirm["choice_outcome"] = opened.get("classification")
                        if opened.get("classification") != "opened_surface_no_write":
                            break
                        await self._click_token(confirm_token, str(confirm.get("label") or confirm_labels[0]), mutation=True)
                        effect_token = confirm_token
                        break
                    await page.wait_for_timeout(200)
        except Exception as exc:
            click_error = mask_sensitive_string(str(exc))[:500]
        finally:
            self.browser.clear_portal_mutation_authorization()
        effect = await self._await_effect(effect_token, before_url) if not click_error else {"pass": False, "status": "commit_click_error"}
        if confirm.get("found"):
            effect["confirmation"] = {"label": confirm.get("label"), "dialog": mask_sensitive_string(str(confirm.get("dialog") or ""))[:200]}
        effect["reconciliation"] = await self._reconcile(start_net, label=label, entity=entity, effect=effect, click_error=click_error)
        if click_error:
            effect["error"] = click_error
        if effect["reconciliation"].get("classification") == "rejected_verified":
            effect.update({"pass": False, "status": "portal_rejected_commit"})
        elif effect["reconciliation"].get("classification") in {"committed_verified", "committed_verified_after_transport_or_ui_error"}:
            effect.update({"pass": True, "status": "committed"})
        if effect.get("pass"):
            effect["settled"] = await self._settle_after_commit()
        return dict(effect, label=label)

    async def _settle_after_commit(self, timeout_s: float = 6.0) -> Dict[str, Any]:
        """V243R23: let the portal finish its own post-save navigation (a redirect
        to the listing, a route change with a success toast) before the next
        navigation -- otherwise that late redirect interrupts it."""
        deadline = time.monotonic() + timeout_s
        stable, last = 0, ""
        while time.monotonic() < deadline:
            try:
                page = await self._page()
                state = await page.evaluate("() => location.href + '|' + document.readyState")
            except Exception:
                state = ""
            if state and state == last and state.endswith("|complete"):
                stable += 1
                if stable >= 2:
                    return {"settled": True, "url": self.browser._evidence_url(state.rsplit("|", 1)[0]) if hasattr(self.browser, "_evidence_url") else ""}
            else:
                stable = 0
            last = state
            await asyncio.sleep(0.3)
        return {"settled": False}

    async def _settle_guarded_opener(self, audit: Dict[str, Any], start_net: int) -> None:
        """V243R31: a guarded action (Deploy) that only opened a menu or a confirmation
        wrote nothing: reconcile its click so the real choice can be dispatched."""
        clicked = [str(x) for x in audit.get("path") or [] if str(x) and ":" not in str(x) and str(x) != "expand row"]
        if (audit.get("menu_opened") or audit.get("confirm_opened")) and clicked and _GUARDED.search(clicked[-1]):
            audit["opener_outcome"] = await self._reconcile_opener(start_net, form_visible=True)

    async def _reconcile_opener(self, start_net: int, *, form_visible: bool) -> Dict[str, Any]:
        """A guarded row action (Deploy ...) either opened its dialog or acted at once."""
        from .certified_future_task_agent import CertifiedHIPFutureTaskExecutor

        dispatch = self.browser.last_click_dispatch_evidence() if hasattr(self.browser, "last_click_dispatch_evidence") else {}
        try:
            rows = CertifiedHIPFutureTaskExecutor._network_rows_since(self.browser, start_net, dispatch=dispatch)
        except Exception:
            rows = []
        writes = [r for r in rows if str(r.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}]
        if form_visible and not writes and hasattr(self.browser, "resolve_mutation_dispatch_guard"):
            return {"classification": "opened_surface_no_write",
                    "mutation_dispatch_guard": self.browser.resolve_mutation_dispatch_guard({"classification": "opened_surface_no_write"})}
        return await self._reconcile(start_net, label="opener", entity="", effect={"pass": False}, click_error="")

    async def _reconcile(self, start_net: int, *, label: str, entity: str, effect: Mapping[str, Any], click_error: str) -> Dict[str, Any]:
        """Classify the commit from write responses and UI signals; release the
        session's mutation guard only for an authoritative outcome."""
        from .certified_future_task_agent import CertifiedHIPFutureTaskExecutor
        from .mutation_outcome import classify_mutation_outcome

        dispatch = self.browser.last_click_dispatch_evidence() if hasattr(self.browser, "last_click_dispatch_evidence") else {}
        rows: List[Dict[str, Any]] = []
        deadline = time.monotonic() + 3.0
        while True:
            try:
                rows = CertifiedHIPFutureTaskExecutor._network_rows_since(self.browser, start_net, dispatch=dispatch)
            except Exception:
                rows = []
            if any(str(r.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"} and r.get("status") is not None for r in rows):
                break
            if time.monotonic() >= deadline or not dispatch.get("dispatch_attempted"):
                break
            await asyncio.sleep(0.25)
        ui = {"success": bool(effect.get("pass")) and bool(effect.get("notes")), "error": effect.get("status") == "portal_reported_error"}
        outcome = classify_mutation_outcome(
            dispatch=dispatch, network_rows=rows, ui_signal=ui,
            structural_change=bool(effect.get("surface_closed")), action_error=click_error,
        )
        if hasattr(self.browser, "resolve_mutation_dispatch_guard"):
            outcome["mutation_dispatch_guard"] = self.browser.resolve_mutation_dispatch_guard(outcome)
        return mask_sensitive_data({k: outcome.get(k) for k in (
            "classification", "pass", "reason", "successful_2xx_write_response_count", "failed_write_response_count",
            "write_request_count", "mutation_dispatch_guard", "automatic_mutation_retry_allowed")})

    async def _await_effect(self, token: str, before_url: str) -> Dict[str, Any]:
        deadline = time.monotonic() + self.effect_timeout
        last: Dict[str, Any] = {}
        success = re.compile(r"success|saved|created|updated|deployed|merged|cloned|completed|submitted", re.I)
        while time.monotonic() < deadline:
            page = await self._page()
            try:
                last = await page.evaluate(_EFFECT_JS, {"token": token})
            except Exception:
                # The page navigated away from the form: the surface closed.
                await asyncio.sleep(0.3)
                continue
            if last.get("errors"):
                return {"pass": False, "status": "portal_reported_error", "errors": [mask_sensitive_string(x)[:300] for x in last["errors"][:3]]}
            if any(success.search(x) for x in last.get("notes") or []) or last.get("url") != before_url or not last.get("button_visible"):
                return {"pass": True, "status": "committed", "notes": [mask_sensitive_string(x)[:200] for x in (last.get("notes") or [])[:3]],
                        "surface_closed": not last.get("button_visible") or last.get("url") != before_url}
            await asyncio.sleep(0.25)
        return {"pass": False, "status": "commit_outcome_unknown", "detail": "no success, error or closed surface within the timeout; not retried"}

    async def verify_listing(self, *, phase: str, name: str, expect: Sequence[str]) -> Dict[str, Any]:
        url = self._listing_url(phase)
        await self._navigate(url)
        if name:
            await self._search(name)
        page = await self._page(url)
        rows = await page.evaluate(
            r"""(name) => {
              const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const want = norm(name);
              const exact = r => Array.from(r.querySelectorAll('td,th,[role=cell],[role=gridcell],a,span')).some(c => norm(c.innerText) === want);
              return Array.from(document.querySelectorAll('tr,[role=row],.dds__table__row,[class*=card]'))
                .filter(r => r.getBoundingClientRect().height > 0)
                .filter(r => !want || norm(r.innerText).includes(want))
                .sort((a, b) => exact(b) - exact(a))
                .map(r => (r.innerText || '').replace(/\s+/g, ' ').trim());
            }""", name)
        row = rows[0] if rows else ""
        missing = [x for x in expect if _norm(x) not in _norm(row)]
        return {"pass": bool(row) and not missing, "row_found": bool(row), "missing": missing, "row_text": mask_sensitive_string(row)[:300]}

    # ------------------------------------------------------------------ run
    async def run(self, input_data: Mapping[str, Any], *, allow_portal_mutation: bool = False, confirmation: str = "") -> Dict[str, Any]:
        specs = operation_specs(input_data)
        gate = operation_gate(allow_portal_mutation, confirmation)
        started = time.monotonic()
        report: Dict[str, Any] = {"schema_version": SCHEMA, "operations": [], "mutation_gate": gate}
        restarts_left = self.whitelabel_restarts
        for index, spec in enumerate(specs, start=1):
            recoveries: List[Dict[str, Any]] = []
            while True:
                self._commit_clicked = False
                error: Optional[BaseException] = None
                try:
                    row = await self.run_one(spec, input_data, gate, index=index)
                except Exception as exc:
                    from .environment_faults import is_environment_fatal

                    error = exc
                    row = {"phase": spec.get("phase"), "operation": spec.get("operation"), "pass": False,
                           "status": "error", "result": "FAILED", "error": mask_sensitive_string(str(exc))[:800],
                           "environment_fault": is_environment_fatal(exc)}
                if not row.get("pass") and await self._whitelabel_behind(error):
                    # The portal answered with a Whitelabel Error Page (the error itself
                    # may only say a field vanished or the page context was destroyed).
                    row.update(status="whitelabel_error_page", environment_fault=True)
                    if self._commit_clicked:
                        # Save / Submit / Deploy was already clicked: its outcome is
                        # unknown, so the operation is not repeated (check the listing).
                        row["whitelabel_after_commit"] = True
                    elif restarts_left > 0:
                        restarts_left -= 1
                        recoveries.append(await self._restart_after_whitelabel(spec, error or RuntimeError(row.get("error") or "")))
                        if recoveries[-1].get("restarted"):
                            continue
                if recoveries:
                    row["whitelabel_recoveries"] = recoveries
                break
            report["operations"].append(row)
            if row.get("environment_fault"):
                break
        report["pass"] = bool(specs) and all(r.get("pass") for r in report["operations"])
        report["results"] = [{"phase": r.get("phase"), "operation": r.get("operation"), "target": r.get("target"),
                              "result": r.get("result"), "status": r.get("status")} for r in report["operations"]]
        report["seconds"] = round(time.monotonic() - started, 1)
        safe_write_json(self.run_dir / "portal_operations.json", mask_sensitive_data(report))
        return mask_sensitive_data(report)

    async def _whitelabel_behind(self, error: Optional[BaseException]) -> bool:
        from .environment_faults import WHITELABEL_CODE, whitelabel_error_on

        if error is not None and WHITELABEL_CODE in str(error):
            return True
        try:
            return bool(await whitelabel_error_on(getattr(self.browser, "page", None)))
        except Exception:
            return False

    async def _restart_after_whitelabel(self, spec: Mapping[str, Any], exc: BaseException) -> Dict[str, Any]:
        """V243R32: close and reopen the browser; the operation then starts again from its listing."""
        record: Dict[str, Any] = {"phase": spec.get("phase"), "operation": spec.get("operation"),
                                  "error": mask_sensitive_string(str(exc))[:300], "restarted": False}
        restart = getattr(self.browser, "restart", None)
        if not callable(restart):
            record["reason"] = "browser session cannot restart"
            return record
        try:
            info = await restart(reason=f"Whitelabel Error Page during {spec.get('operation')} {spec.get('phase')}: close and reopen the browser")
            record.update(restarted=True, browser_restart=mask_sensitive_data(info if isinstance(info, dict) else {}))
        except Exception as restart_exc:
            record["reason"] = mask_sensitive_string(str(restart_exc))[:300]
        return record

    async def run_one(self, spec: Mapping[str, Any], input_data: Mapping[str, Any], gate: Mapping[str, Any], *, index: int = 1) -> Dict[str, Any]:
        started = time.monotonic()
        phase = str(spec.get("phase") or "")
        operation = canonical_operation(spec.get("operation"))
        objects = input_data.get("objects") if isinstance(input_data.get("objects"), Mapping) else {}
        form_phase = form_phase_for(phase, operation, spec)
        if form_phase == phase:
            values = dict(spec.get("values") or objects.get(phase) or {})
        else:
            # V243R23: an action's own dialog (Deploy, Merge ...) is not the phase
            # form: its values come from objects.<phase>_<operation> (or
            # objects.<operation>) plus the operation's own values.
            values = {**dict(objects.get(f"{phase}_{operation}") or objects.get(operation) or {}), **dict(spec.get("values") or {})}
        target = str(spec.get("target") or (_name_in(objects.get(phase) or {}) if operation != "create" else "") or _name_in(values))
        commit_wanted = bool(spec.get("commit", False))
        out_dir = self.run_dir / "operations" / f"{index:02d}_{phase}_{operation}"
        row: Dict[str, Any] = {"phase": phase, "operation": operation, "form": form_phase, "target": target,
                               "commit_requested": commit_wanted}
        opener = (OPENER_LABELS.get(operation) or (operation.title(),))[0]
        # V243R24: which environment / version of the object (the expanded row's
        # tabs and Version), and a Migrate / Deploy target checked before any click.
        options: Dict[str, Any] = dict(spec.get("panel") or {}) if isinstance(spec.get("panel"), Mapping) else {}
        if values.get("target_environment"):
            options["target_environment"] = values.get("target_environment")
        if operation == "clone":
            new_name = _name_in(values)
            if not new_name or (target and _norm(new_name) == _norm(target)):
                return self._needs_input(row, OperationNeedsInput(
                    "name", "a clone needs a new, unique name; the source object's name is taken", observed=target,
                    source=f"objects.{phase}.name (a new name) or the task text"), started, out_dir)
        if operation in {"learn_edit", "learn_clone", "learn_deploy", "learn_migrate"}:
            # V243R30: open the Edit form, read every value, close it unsaved and
            # remember the Edit section.  V243R31: the Clone form, and where Deploy /
            # Migrate go from every environment.  Read-only: nothing is confirmed.
            from .edit_section_learning import EditSectionLearner

            learned = await EditSectionLearner(self.config, self.browser, self.run_dir, listing_urls=self.listing_urls).learn(
                phase, target=target, input_data=input_data, options=options, out_dir=out_dir,
                action=operation[len("learn_"):], gate=gate)
            row.update({k: learned.get(k) for k in (
                "pass", "status", "target", "target_source", "open", "close", "capture", "fields", "input_json", "values_file",
                "edit_input_file", "knowledge", "knowledge_file", "no_write_requests", "write_requests", "needs_input", "reason", "learned_mode",
                "environments", "menus", "section_kind", "confirm", "dialog_fields", "blocked_environments", "action_label")
                if k in learned})
            return self._finish(row, started, out_dir)
        if operation != "create" and _GUARDED.search(opener) and not gate.get("pass"):
            # The row action itself (Deploy, Delete ...) is a portal mutation.
            row.update({"pass": False, "status": "blocked_mutation_authorization",
                        "reason": f"opening {opener!r} is itself a portal mutation; the three-part gate is closed"})
            return self._finish(row, started, out_dir)
        task_id = f"op-{uuid.uuid4().hex[:10]}"
        fields: List[Dict[str, Any]] = []
        edit_capture: Dict[str, Any] = {}
        if gate.get("pass") and _GUARDED.search(opener):
            self.browser.set_portal_mutation_authorization(enabled=True, allowed_labels=[opener], task_id=task_id)
        try:
            try:
                row["open"] = await self.open_surface(phase=phase, operation=operation, target=target, form_phase=form_phase, options=options)
            except OperationNeedsInput as exc:
                return self._needs_input(row, exc, started, out_dir)
            if row["open"].get("existing"):
                row.update({"pass": True, "status": "already_in_target",
                            "reason": f"{target!r} {row['open'].get('target_check', {}).get('source_version') or ''} is already in {options.get('target_environment')}; nothing was clicked"})
                return self._finish(row, started, out_dir)
            if row["open"].get("menu_opened"):
                return await self._menu_choice(row, values=values, gate=gate, task_id=task_id, target=target, phase=phase,
                                               opener=opener, commit_wanted=commit_wanted, started=started, out_dir=out_dir)
            if row["open"].get("confirm_opened"):
                return await self._confirm_choice(row, values=values, gate=gate, task_id=task_id, target=target, phase=phase,
                                                  opener=opener, commit_wanted=commit_wanted, started=started, out_dir=out_dir)
            if operation in {"edit", "clone"} and form_phase == phase and row["open"].get("form_visible"):
                if self.edit_capture:
                    # V243R30: read the whole Edit form first (every tab, read-only)
                    # and refresh the learned Edit section with it.  V243R31: the
                    # Clone form too -- its values are the source's, kept by the clone.
                    edit_capture = await self._capture_edit_section(phase, input_data, row, action=operation)
                # V243R24 EDIT SAFETY: capture the form as it is, compare every
                # requested value with it, and only change what differs.
                row["before"] = await self._form_snapshot()
                fields = self._requested_fields(form_phase, values)
                row["changes"] = self._plan_changes(fields, row["before"])
                locked = self._read_only_changes(row["changes"], edit_capture) if self.block_read_only and row["changes"] else []
                if locked and operation == "clone":
                    await self._leave_edit_form(phase, edit_capture)
                    return self._needs_input(row, OperationNeedsInput(
                        locked[0]["label"], f"read-only in {edit_capture.get('title') or 'the Clone form'}: a clone cannot change "
                        + ", ".join(f"{x['label']!r}" for x in locked) + "; nothing was created",
                        observed={x["label"]: x["current"] for x in locked}, source="leave it as the source has it"), started, out_dir)
                if operation == "edit" and row["changes"]:
                    if locked:
                        await self._leave_edit_form(phase, edit_capture)
                        return self._needs_input(row, OperationNeedsInput(
                            locked[0]["label"],
                            f"read-only in {edit_capture.get('title') or 'the Edit form'}: the portal does not let Edit change "
                            + ", ".join(f"{x['label']!r}" for x in locked) + "; nothing was changed",
                            observed={x["label"]: x["current"] for x in locked},
                            source="leave it as it is, or Clone the object to give it a new name"), started, out_dir)
                    if not any(c["change"] for c in row["changes"]):
                        row.update({"pass": True, "status": "no_change_needed",
                                    "reason": "every requested value already equals the current one; nothing was changed"})
                        return self._finish(row, started, out_dir)
                    same = {c["top_key"] for c in row["changes"] if not c["change"] and c["top_key"]}
                    differ = {c["top_key"] for c in row["changes"] if c["change"] and c["top_key"]}
                    values = {k: v for k, v in values.items() if k not in (same - differ)}
                    fields = [f for f in fields if f.get("top_key") not in (same - differ)]
            if operation not in PHASE_FORM_OPERATIONS and row["open"].get("opened") == "form" and values:
                # V243R31: an action's dialog (Deploy > Target Environment): read it first;
                # a requested value it does not offer is NEEDS_INPUT, never guessed or typed.
                dialog = await self._capture_action_dialog(phase, operation, form_phase, row)
                missing = self._not_offered(values, dialog.get("fields") or [])
                if missing:
                    await self._leave_edit_form(phase, dialog)
                    first = missing[0]
                    return self._needs_input(row, OperationNeedsInput(
                        first["field"], f"{first['requested']} is not offered by {opener} from "
                        f"{(row['open'].get('before') or {}).get('environment') or 'this environment'} (the dialog offers {', '.join(first['offered'])})"
                        + self._route_hint(phase, str((row['open'].get('before') or {}).get('environment') or ''), first["requested"]),
                        offered=first["offered"], source="one of the offered values"), started, out_dir)
            payload: Dict[str, Any] = {"objects": {form_phase: values}, "_operation": operation}
            for key in ("_upload_assets_dir", "_input_json_path"):
                if key in input_data:
                    payload[key] = input_data[key]
            reopen_count = {"n": 0}

            async def reopen() -> None:
                reopen_count["n"] += 1
                await self.open_surface(phase=phase, operation=operation, target=target, form_phase=form_phase, options=options)

            fill = await self.fill(phase=phase, form_phase=form_phase, payload=payload, out_dir=out_dir, reopen=reopen)
        finally:
            if gate.get("pass") and _GUARDED.search(opener):
                self.browser.clear_portal_mutation_authorization()
        skill = fill.get("skill") if isinstance(fill.get("skill"), dict) else {}
        outcome = skill.get("outcome") if isinstance(skill.get("outcome"), dict) else {}
        certified = outcome.get("status") == "certified"
        row["fill"] = {
            "pass": bool(fill.get("pass")), "status": fill.get("status"), "execution_mode": fill.get("execution_mode"),
            "skill_plan": skill.get("plan"), "skill_reason": skill.get("reason"), "skill_status": outcome.get("status") or skill.get("skill_status"),
            "novelty": skill.get("novelty") or [], "learn_seconds": skill.get("learn_seconds"), "replay_seconds": skill.get("replay_seconds"),
            "reopened": reopen_count["n"],
        }
        if not fill.get("pass"):
            row.update({"pass": False, "status": "fill_not_proven", "failure_summary": fill.get("failure_summary") or {}})
            return self._finish(row, started, out_dir)
        if fields and isinstance(row.get("before"), Mapping):
            # Let the form react to the last edit (Angular validates / resets
            # dependent fields on blur) before comparing it with the snapshot.
            page = await self._page()
            try:
                await page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
                await page.wait_for_timeout(400)
            except Exception:
                pass
            after_fill = await self._form_snapshot()
            unrelated = self._unrelated_changes(fields, row["before"], after_fill)
            row["unrelated_changes"] = unrelated
            if unrelated and self.block_unrelated_changes:
                row.update({"pass": False, "status": "unrelated_field_changed",
                            "reason": "fields that were not requested changed while filling; nothing was saved"})
                return self._finish(row, started, out_dir)
        if not commit_wanted:
            row.update({"pass": True, "status": "filled_not_committed"})
            return self._finish(row, started, out_dir)
        if not gate.get("pass"):
            row.update({"pass": False, "status": "commit_not_authorized",
                        "reason": "commit requested but the three-part mutation gate is closed; the form was filled and left unsaved"})
            return self._finish(row, started, out_dir)
        if self.require_certified and not certified:
            row.update({"pass": False, "status": "commit_waiting_for_certified_skill",
                        "reason": "the skill is not certified by a deterministic replay yet; nothing was saved"})
            return self._finish(row, started, out_dir)
        skill_id = str(outcome.get("skill_id") or skill.get("skill_id") or "")
        store = PortalSkillStore.for_run(self.config, await self._page())
        learned = store.commit_label(form_phase, skill_id) if store is not None else ""
        # V243R30: the Save the Edit form shows (learned: "Update", "Submit" ...) comes first.
        seen_commit = list(edit_capture.get("commit_labels") or []) if operation in {"edit", "clone"} else []
        labels = list(dict.fromkeys([x for x in [learned, *(spec.get("commit_labels") or []), *seen_commit, *COMMIT_LABELS.get(operation, ("Save",))] if x]))
        committed = await self.commit(labels=labels, gate=gate, task_id=task_id, entity=target)
        row["commit"] = committed
        if committed.get("pass") and store is not None and skill_id:
            store.record_commit(form_phase, skill_id, str(committed.get("label") or ""))
        if not committed.get("pass"):
            row.update({"pass": False, "status": committed.get("status") or "commit_failed"})
            return self._finish(row, started, out_dir)
        expect = [str(x) for x in (spec.get("expect") or [])] if isinstance(spec.get("expect"), list) else (
            [str(v) for v in (spec.get("expect") or {}).values()] if isinstance(spec.get("expect"), Mapping) else [])
        name = _verify_name(spec, operation, values, target)
        if operation in {"deploy", "migrate"} and str(values.get("target_environment") or "").strip():
            # V243R31: a Deploy / Migrate dialog: the object's row must now list the environment.
            expect = list(dict.fromkeys([*expect, str(values.get("target_environment")).strip().upper()]))
        if self.verify_after_commit:
            row["verification"] = await self.verify_listing(phase=phase, name=name, expect=expect)
            row["pass"] = bool(row["verification"].get("pass"))
            row["status"] = "committed_and_verified" if row["pass"] else "committed_verification_failed"
            guard = (committed.get("reconciliation") or {}).get("mutation_dispatch_guard") or {}
            if row["pass"] and guard.get("retained") and hasattr(self.browser, "resolve_mutation_dispatch_guard"):
                # No write response was captured, but the listing now shows the
                # committed state: that is the authoritative outcome.
                row["reconciliation_by_listing"] = self.browser.resolve_mutation_dispatch_guard(
                    {"classification": "committed_verified", "pass": True})
            if row["pass"] and "expand row" in (row["open"].get("path") or []) and fields:
                # V243R24: re-open the object's details and read the requested values back.
                row["after"] = await self.read_details(phase=phase, target=name, options=options, fields=fields)
            if row["pass"] and operation == "clone" and self.verify_by_edit:
                # V243R31: open the new object's Edit form: the requested values, and
                # everything else as the source had it.
                reread = await self.read_back_edit_form(phase=phase, target=name, options={}, values=values, input_data=input_data,
                                                        source_fields=edit_capture.get("fields") or [])
                row["after_edit_form"] = reread
                if reread.get("read"):
                    row["after"] = {**(row.get("after") or {}), "source": "clone_edit_form",
                                    "requested_values_seen": reread.get("requested_values_seen"), "all_seen": reread.get("all_seen"),
                                    "kept_from_source": reread.get("kept_from_source"), "differs_from_source": reread.get("differs_from_source")}
                    if reread.get("all_seen") is False:
                        row["pass"] = False
                        row["status"] = "committed_values_not_seen"
                        row["reason"] = "created, but the new object's Edit form does not show every requested value"
            if row["pass"] and operation == "edit" and fields and self.verify_by_edit and (row.get("after") or {}).get("all_seen") is not True:
                # V243R30: the details do not show every edited field -- reopen the
                # Edit form (read-only) and read them there.
                reread = await self.read_back_edit_form(phase=phase, target=name, options=options, values=values, input_data=input_data)
                row["after_edit_form"] = reread
                if reread.get("read"):
                    row["after"] = {**(row.get("after") or {}), "source": "edit_form_reopened",
                                    "requested_values_seen": reread.get("requested_values_seen"), "all_seen": reread.get("all_seen")}
                    if reread.get("all_seen") is False:
                        row["pass"] = False
                        row["status"] = "committed_values_not_seen"
                        row["reason"] = "saved, but the reopened Edit form does not show every requested value"
        else:
            row.update({"pass": True, "status": "committed"})
        return self._finish(row, started, out_dir)

    async def _menu_choice(
        self, row: Dict[str, Any], *, values: Mapping[str, Any], gate: Mapping[str, Any], task_id: str, target: str, phase: str,
        opener: str, commit_wanted: bool, started: float, out_dir: Path,
    ) -> Dict[str, Any]:
        """Migrate > TEST2: the target is a menu choice; checked, then clicked once."""
        page = await self._page()
        items = [str(x) for x in row["open"].get("menu_items") or []]
        want = str(values.get("target_environment") or values.get("environment") or values.get("to") or "").strip()

        async def close_menu() -> None:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

        source_env = str((row["open"].get("before") or {}).get("environment") or "")
        if source_env and items:
            self._remember_action(phase, str(row.get("operation") or ""), row, kind="menu", menus={source_env.upper(): items})
        if not want:
            await close_menu()
            return self._needs_input(row, OperationNeedsInput(
                "target_environment", f"{opener} needs a target environment", offered=items, source="'to <ENV>' in the task"), started, out_dir)
        match = next((i for i in items if _same_value(i, want)), "")
        if not match:
            await close_menu()
            return self._needs_input(row, OperationNeedsInput(
                "target_environment", f"{want} is not offered by {opener} from {source_env or 'this environment'}"
                + self._route_hint(phase, source_env, want), offered=items), started, out_dir)
        row["choice"] = match
        if not commit_wanted:
            await close_menu()
            row.update({"pass": True, "status": "validated_not_committed"})
            return self._finish(row, started, out_dir)
        if not gate.get("pass"):
            await close_menu()
            row.update({"pass": False, "status": "commit_not_authorized",
                        "reason": f"{opener} to {match} is a portal mutation; the three-part gate is closed and nothing was clicked"})
            return self._finish(row, started, out_dir)
        clicked = str((row["open"].get("path") or [opener])[-1] or opener)
        confirm_labels = list(dict.fromkeys([clicked.title(), opener, "Confirm", "Yes", "OK", "Continue", "Submit"]))
        committed = await self.commit_menu_choice(label=match, gate=gate, task_id=task_id, entity=target, confirm_labels=confirm_labels)
        row["commit"] = committed
        if not committed.get("pass"):
            row.update({"pass": False, "status": committed.get("status") or "commit_failed"})
            return self._finish(row, started, out_dir)
        row["verification"] = await self.verify_listing(phase=phase, name=target, expect=[match])
        row["pass"] = bool(row["verification"].get("pass"))
        row["status"] = "committed_and_verified" if row["pass"] else "committed_verification_failed"
        row["after"] = {"available_environments_row": row["verification"].get("row_text")}
        guard = (committed.get("reconciliation") or {}).get("mutation_dispatch_guard") or {}
        if row["pass"] and guard.get("retained") and hasattr(self.browser, "resolve_mutation_dispatch_guard"):
            row["reconciliation_by_listing"] = self.browser.resolve_mutation_dispatch_guard({"classification": "committed_verified", "pass": True})
        return self._finish(row, started, out_dir)

    def _remember_action(self, phase: str, operation: str, row: Mapping[str, Any], *, kind: str,
                         menus: Optional[Mapping[str, Sequence[str]]] = None, confirm: Optional[Mapping[str, Any]] = None) -> None:
        """V243R31: what this action opened on this phase's listing -- remembered, value-free."""
        from .edit_section_learning import SectionMemory

        try:
            memory = SectionMemory.for_run(self.config, getattr(self.browser, "page", None), action=operation)
            if memory is not None:
                memory.record(phase, {"section_kind": kind, "menus": dict(menus or {}), "confirm": dict(confirm or {}),
                                      "listing_url": self._listing_url(phase)}, opener=row.get("open"), source=f"{operation}_operation")
        except Exception:
            pass

    @staticmethod
    def _confirm_template(text: str, target: str) -> str:
        """The confirmation's wording without this object's name or version."""
        out = str(text or "")
        if target:
            out = re.sub(re.escape(target), "<object>", out, flags=re.I)
        return re.sub(r"\b\d+(?:\.\d+)+\b", "<version>", out)[:240]

    async def _cancel_dialog(self) -> bool:
        page = await self._page()
        token = uuid.uuid4().hex[:10]
        hit = await page.evaluate(_CONFIRM_JS, {"labels": ["Cancel", "No", "Close"], "token": token})
        if hit.get("found"):
            await self._click_token(token, "Cancel")
            await page.wait_for_timeout(250)
            return True
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    async def _confirm_choice(
        self, row: Dict[str, Any], *, values: Mapping[str, Any], gate: Mapping[str, Any], task_id: str, target: str, phase: str,
        opener: str, commit_wanted: bool, started: float, out_dir: Path,
    ) -> Dict[str, Any]:
        """V243R31: Deploy that asks "Deploy X from DEV to TEST1?" -- the portal names the
        target; it must be the requested one.  Confirmed once, or cancelled."""
        confirm = row["open"].get("confirm") or {}
        text = str(confirm.get("text") or "")
        envs = re.findall(r"\b(DEV|TEST\d*|PROD|UAT|QA|SIT|STAGE)\b", text.upper())
        to = (re.search(r"\bTO\s+(DEV|TEST\d*|PROD|UAT|QA|SIT|STAGE)\b", text.upper()) or [None, envs[-1] if envs else ""])[1]
        source = (re.search(r"\bFROM\s+(DEV|TEST\d*|PROD|UAT|QA|SIT|STAGE)\b", text.upper()) or [None, ""])[1]
        want = str(values.get("target_environment") or values.get("environment") or values.get("to") or "").strip()
        row["confirmation"] = {"text": text, "source_environment": source, "target_environment": to}
        self._remember_action(phase, str(row.get("operation") or ""), row, kind="confirm",
                              menus={source: [to]} if source and to else {},
                              confirm={"title": confirm.get("title") or "", "buttons": list(confirm.get("buttons") or []),
                                       "text": self._confirm_template(text, target), "asks_before_acting": True})
        if not want or (to and not _same_value(want, to)):
            await self._cancel_dialog()
            reason = (f"{opener} from {source or 'this environment'} goes to {to} (the portal asks: {text[:160]!r}); "
                      + (f"{want} is not where it goes" if want else "name the target to confirm it")
                      + (self._route_hint(phase, source, want) if want else ""))
            return self._needs_input(row, OperationNeedsInput("target_environment", reason, offered=[to] if to else [],
                                                              source="'to <ENV>' in the task"), started, out_dir)
        row["choice"] = to or want
        if not commit_wanted:
            await self._cancel_dialog()
            row.update({"pass": True, "status": "validated_not_committed"})
            return self._finish(row, started, out_dir)
        if not gate.get("pass"):
            await self._cancel_dialog()
            row.update({"pass": False, "status": "commit_not_authorized",
                        "reason": f"{opener} to {row['choice']} is a portal mutation; the three-part gate is closed and nothing was confirmed"})
            return self._finish(row, started, out_dir)
        page = await self._page()
        labels = [x for x in dict.fromkeys([opener, str(opener).title(), "Confirm", "Yes", "OK", "Continue", "Proceed", "Submit"]) if x]
        token = uuid.uuid4().hex[:10]
        hit = await page.evaluate(_CONFIRM_JS, {"labels": labels, "token": token})
        if not hit.get("found"):
            await self._cancel_dialog()
            row.update({"pass": False, "status": "commit_control_not_found", "reason": f"no {'/'.join(labels[:3])} in the confirmation"})
            return self._finish(row, started, out_dir)
        committed = await self._commit_click(token, str(hit.get("label") or opener), allowed=labels, task_id=task_id, entity=target)
        row["commit"] = committed
        if not committed.get("pass"):
            row.update({"pass": False, "status": committed.get("status") or "commit_failed"})
            return self._finish(row, started, out_dir)
        row["verification"] = await self.verify_listing(phase=phase, name=target, expect=[row["choice"]])
        row["pass"] = bool(row["verification"].get("pass"))
        row["status"] = "committed_and_verified" if row["pass"] else "committed_verification_failed"
        row["after"] = {"available_environments_row": row["verification"].get("row_text")}
        guard = (committed.get("reconciliation") or {}).get("mutation_dispatch_guard") or {}
        if row["pass"] and guard.get("retained") and hasattr(self.browser, "resolve_mutation_dispatch_guard"):
            row["reconciliation_by_listing"] = self.browser.resolve_mutation_dispatch_guard({"classification": "committed_verified", "pass": True})
        return self._finish(row, started, out_dir)

    async def _capture_action_dialog(self, phase: str, operation: str, form_phase: str, row: Dict[str, Any]) -> Dict[str, Any]:
        """V243R31: the dialog an action opened, read (read-only) and remembered as that action's section."""
        from .edit_section_learning import EditSectionLearner, SectionMemory

        try:
            capture = await EditSectionLearner(self.config, self.browser, self.run_dir, listing_urls=self.listing_urls).capture(
                form_phase, input_data={})
        except Exception as exc:
            row["action_section"] = {"captured": False, "error": mask_sensitive_string(str(exc))[:300]}
            return {}
        if not capture.get("pass"):
            row["action_section"] = {"captured": False, "status": capture.get("status")}
            return {}
        memory = SectionMemory.for_run(self.config, await self._page(), action=operation)
        source_env = str((row.get("open") or {}).get("before", {}).get("environment") or "").upper()
        env_field = next((f for f in capture.get("fields") or [] if f.get("options") and "environment" in _norm(f.get("label"))), None)
        menus = {source_env: list(env_field.get("options") or [])} if source_env and env_field else {}
        if memory is not None:
            memory.record(phase, {**capture, "listing_url": self._listing_url(phase), "section_kind": "dialog", "menus": menus},
                          opener=row.get("open"), source=f"{operation}_operation")
        surface = capture.get("surface") or {}
        row["action_section"] = {"captured": True, "kind": "dialog", "title": surface.get("title"),
                                 "fields": [{k: f.get(k) for k in ("label", "kind", "required", "options")} for f in capture.get("fields") or []]}
        return {"fields": capture.get("fields") or [], "token": capture.get("surface_token"), "title": surface.get("title")}

    @staticmethod
    def _not_offered(values: Mapping[str, Any], fields: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        """Requested values a dialog's dropdown / radio group does not offer."""
        out: List[Dict[str, Any]] = []
        for key, want in values.items():
            if not isinstance(want, (str, int, float)) or str(want).strip() == "" or str(key).startswith("_"):
                continue
            words = _norm(key)
            field = next((f for f in fields if f.get("options") and (_norm(f.get("label")) == words or _norm(f.get("input_key")) == words
                                                                    or (words and words in _norm(f.get("label"))))), None)
            if field is None:
                continue
            offered = [str(o) for o in field.get("options") or []]
            if not any(_same_value(o, want) for o in offered):
                out.append({"field": str(key), "label": field.get("label"), "requested": str(want), "offered": offered})
        return out

    async def _capture_edit_section(self, phase: str, input_data: Mapping[str, Any], row: Dict[str, Any], *, action: str = "edit") -> Dict[str, Any]:
        """V243R30: the open Edit (V243R31: or Clone) form, read completely (read-only), remembered."""
        from .edit_section_learning import EditSectionLearner, SectionMemory

        out: Dict[str, Any] = {}
        key = "edit_section" if action == "edit" else f"{action}_section"
        try:
            memory = SectionMemory.for_run(self.config, await self._page(), action=action)
            known = memory.summary(phase) if memory is not None else {"known": False}
            capture = await EditSectionLearner(self.config, self.browser, self.run_dir, listing_urls=self.listing_urls).capture(
                phase, input_data=input_data)
            if not capture.get("pass"):
                row[key] = {"captured": False, "status": capture.get("status"), "known_before": bool(known.get("known"))}
                return out
            surface = capture.get("surface") or {}
            if memory is not None:
                memory.record(phase, {**capture, "listing_url": self._listing_url(phase)}, opener=row.get("open"), source=f"{action}_operation")
            commit_re = r"(?i)save|save changes|update|submit" + (r"|create|clone" if action == "clone" else "")
            out = {
                "fields": capture.get("fields") or [], "title": surface.get("title"), "token": capture.get("surface_token"),
                "commit_labels": [b for b in surface.get("buttons") or [] if re.fullmatch(commit_re, b)],
            }
            row[key] = {
                "captured": True, "known_before": bool(known.get("known")), "title": surface.get("title"), "kind": surface.get("kind"),
                "tabs": [t.get("text") for t in surface.get("tabs") or []], "fields": capture.get("field_count"),
                "read_only": sorted({f["label"] for f in out["fields"] if f.get("read_only")}),
                "commit_labels": out["commit_labels"],
            }
        except Exception as exc:
            row[key] = {"captured": False, "error": mask_sensitive_string(str(exc))[:300]}
        return out

    @staticmethod
    def _read_only_changes(changes: Sequence[Mapping[str, Any]], capture: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Requested changes to fields the Edit form keeps read-only (Profile Name ...)."""
        by_key = {str(f.get("input_key")): f for f in capture.get("fields") or [] if f.get("input_key")}
        locked: List[Dict[str, Any]] = []
        for change in changes:
            field = by_key.get(str(change.get("input_path") or ""))
            if change.get("change") and field is not None and field.get("read_only"):
                locked.append({"label": str(field.get("label") or change.get("field")), "current": field.get("value"),
                               "requested": change.get("requested")})
        return locked

    async def _leave_edit_form(self, phase: str, capture: Mapping[str, Any]) -> None:
        from .edit_section_learning import EditSectionLearner

        try:
            await EditSectionLearner(self.config, self.browser, self.run_dir, listing_urls=self.listing_urls).close(
                str(capture.get("token") or ""), self._listing_url(phase))
        except Exception:
            pass

    async def read_back_edit_form(self, *, phase: str, target: str, options: Mapping[str, Any], values: Mapping[str, Any],
                                  input_data: Mapping[str, Any], source_fields: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
        """Reopen the object's Edit form, read the requested values, close it unsaved.

        V243R31: with ``source_fields`` (a clone's source form), also which of the
        source's other values the new object kept.
        """
        from .edit_section_learning import EditSectionLearner, compare_requested

        learner = EditSectionLearner(self.config, self.browser, self.run_dir, listing_urls=self.listing_urls)
        # V243R31: compare with what the form was filled with -- the values after the
        # phase's own policies (a legacy SFTP-HAFT Deployment Group becomes the
        # portal's current one), not the raw request.
        values = self._effective_requested(phase, values)
        try:
            opened = await self.open_surface(phase=phase, operation="edit", target=target, form_phase=phase, options=options)
            if not opened.get("form_visible"):
                return {"read": False, "reason": "the Edit form did not open again"}
            capture = await learner.capture(phase, input_data=input_data)
            closed = await learner.close(str(capture.get("surface_token") or ""), self._listing_url(phase))
            if not capture.get("pass"):
                return {"read": False, "reason": str(capture.get("status") or "capture_failed"), "closed": closed}
            result = {"read": True, "closed": closed.get("closed"), **compare_requested(capture.get("fields") or [], values)}
            if source_fields:
                result.update(self._kept_from_source(source_fields, capture.get("fields") or [], values))
            return result
        except OperationNeedsInput as exc:
            return {"read": False, "reason": exc.detail.get("reason")}
        except Exception as exc:
            return {"read": False, "reason": mask_sensitive_string(str(exc))[:300]}

    def _effective_requested(self, phase: str, values: Mapping[str, Any]) -> Dict[str, Any]:
        """``{input path: value}`` as the phase compiler resolved them for the fill."""
        try:
            rows = self._requested_fields(phase, values)
        except Exception:
            rows = []
        resolved = {str(r["input_path"]): r.get("requested") for r in rows
                    if r.get("input_path") and r.get("requested") not in (None, "", [])}
        return resolved or dict(values)

    @staticmethod
    def _kept_from_source(source: Sequence[Mapping[str, Any]], cloned: Sequence[Mapping[str, Any]], requested: Mapping[str, Any]) -> Dict[str, Any]:
        """A clone keeps the source's values except the requested ones (and what the portal owns)."""
        from .edit_section_learning import compare_requested

        asked = set((compare_requested(source, requested).get("requested_values_seen") or {}).keys())
        mine = {str(f.get("input_key")): f for f in cloned if f.get("input_key")}
        same, differs = 0, []
        for field in source:
            key = str(field.get("input_key") or "")
            if not key or key in asked or field.get("read_only") or field.get("sensitive") or key not in mine:
                continue
            if _same_value(json.dumps(field.get("value"), sort_keys=True, default=str), json.dumps(mine[key].get("value"), sort_keys=True, default=str)):
                same += 1
            else:
                differs.append({"field": key, "source": field.get("value"), "clone": mine[key].get("value")})
        return {"kept_from_source": same, "differs_from_source": differs[:20]}

    async def read_details(self, *, phase: str, target: str, options: Mapping[str, Any], fields: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        """Re-open the object's expanded details and check the requested values."""
        try:
            await self._navigate(self._listing_url(phase))
            await self._search(target)
            page = await self._page()
            token = uuid.uuid4().hex[:10]
            hit = await self._find_row(page, target, [], token)
            if hit.get("found") != "expander":
                return {"read": False, "reason": f"row {hit.get('found') or 'not found'}"}
            if not hit.get("expanded"):
                await self._click_token(token, "Expand the row")
            panel = await self._panel(token, target)
            if options.get("environment"):
                panel = await self._select_tab(token, target, str(options["environment"]).upper(), panel)
            details = panel.get("details") or {}
            seen = {str(f.get("field")): any(_same_value(v, f.get("requested")) for v in details.values())
                    for f in fields if isinstance(f.get("requested"), (str, int, float)) and str(f.get("requested"))}
            return {"read": True, "details": details, "requested_values_seen": seen, "all_seen": all(seen.values()) if seen else None}
        except Exception as exc:
            return {"read": False, "reason": mask_sensitive_string(str(exc))[:300]}

    @staticmethod
    def _unrelated_changes(fields: Sequence[Mapping[str, Any]], before: Mapping[str, str], after: Mapping[str, str]) -> List[Dict[str, Any]]:
        allowed = {label for f in fields for label in f.get("labels") or []}
        out: List[Dict[str, Any]] = []
        for key, old in before.items():
            if key not in after or key.rsplit("#", 1)[0] in allowed:
                continue
            if not _same_value(old, after[key]):
                out.append({"field": key.rsplit("#", 1)[0], "before": old, "after": after[key]})
        return out

    def _needs_input(self, row: Dict[str, Any], exc: OperationNeedsInput, started: float, out_dir: Path) -> Dict[str, Any]:
        row.update({"pass": False, "status": "needs_input", "needs_input": exc.detail, "reason": exc.detail.get("reason")})
        return self._finish(row, started, out_dir)

    def _finish(self, row: Dict[str, Any], started: float, out_dir: Path) -> Dict[str, Any]:
        row["seconds"] = round(time.monotonic() - started, 1)
        row["result"] = operation_result(str(row.get("status") or ""))
        opened = row.get("open") if isinstance(row.get("open"), Mapping) else {}
        if opened.get("path") and row.get("status") in {"committed_and_verified", "already_in_target", "committed_verification_failed", "error"}:
            # V243R24: a verified outcome promotes the learned action path
            # (EXPLORATION -> DETERMINISTIC); a failed one demotes it.
            try:
                store = PortalSkillStore.for_run(self.config, getattr(self.browser, "page", None))
                if store is not None:
                    row["learned_mode"] = store.record_opener_outcome(
                        str(row.get("phase") or ""), str(row.get("operation") or ""),
                        success=row.get("status") in {"committed_and_verified", "already_in_target"}, reason=str(row.get("status")))
            except Exception:
                pass
        safe_write_json(out_dir / "operation.json", mask_sensitive_data(row))
        return mask_sensitive_data(row)


PHASE_SAVE_LABELS = ("Submit", "Save", "Create", "Save Changes", "Finish")


async def save_verified_phase(
    config: Any, browser: Any, *, phase: str, run_dir: Path, values: Mapping[str, Any], gate: Mapping[str, Any],
    listing_url: str = "",
) -> Dict[str, Any]:
    """V243R23: save a phase form that is filled and exactly verified.

    Clicks the form's Save / Submit once through the governed commit (the
    three-part gate authorizes only these labels), reconciles the outcome from
    the write response and the page, then checks the listing shows the object.
    A rejected or unclear save is reported, never retried; the label that
    worked is learned for the next run.
    """
    name = _name_in(values)
    result: Dict[str, Any] = {"schema_version": "hip.phase-save.v1", "phase": phase, "object": name, "saved": False}
    if not gate.get("pass"):
        return {**result, "pass": False, "status": "save_not_authorized",
                "reason": "save after fill requested but the three-part mutation gate is closed; the form was filled and left unsaved",
                "gate": dict(gate)}
    runner = PortalOperationRunner(config, browser, Path(run_dir), listing_urls={phase: listing_url} if listing_url else None)
    store = PortalSkillStore.for_run(config, await runner._page())
    learned = store.phase_commit_labels(phase) if store is not None else []
    labels = list(dict.fromkeys([*learned, *PHASE_SAVE_LABELS]))
    committed = await runner.commit(labels=labels, gate=gate, task_id=f"save-{uuid.uuid4().hex[:10]}", entity=name)
    result["commit"] = committed
    if not committed.get("pass"):
        return {**result, "pass": False, "status": str(committed.get("status") or "save_failed")}
    if store is not None:
        store.record_phase_commit(phase, str(committed.get("label") or ""))
    if runner.verify_after_commit and name:
        result["verification"] = await runner.verify_listing(phase=phase, name=name, expect=[])
        ok = bool(result["verification"].get("pass"))
        return {**result, "pass": ok, "saved": True, "status": "saved_and_verified" if ok else "saved_listing_not_confirmed"}
    return {**result, "pass": True, "saved": True, "status": "saved"}


async def run_portal_operations(
    config: Any, input_data: Mapping[str, Any], *, run_dir: Path, allow_portal_mutation: bool = False,
    confirmation: str = "", browser: Any = None, listing_urls: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Run every operation of ``input_data`` in one browser session."""
    if browser is not None:
        return await PortalOperationRunner(config, browser, run_dir, listing_urls=listing_urls).run(
            input_data, allow_portal_mutation=allow_portal_mutation, confirmation=confirmation)
    from .browser_session import BrowserSession

    async with BrowserSession(config, run_dir) as session:
        return await PortalOperationRunner(config, session, run_dir, listing_urls=listing_urls).run(
            input_data, allow_portal_mutation=allow_portal_mutation, confirmation=confirmation)
