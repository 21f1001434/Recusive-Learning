"""Learn every phase's Edit section and read its values (V243R30).

For each HIP object (Transport Profile, Business Flow, Data Map, Rule,
Document Type) the agent can now:

1. open the phase's listing link, search the object (or take the first row),
   click the row's expander and the Edit in its expanded details -- the same
   semantic path Edit operations use (R24);
2. capture every value of the Edit form: every tab of a wizard, every
   collapsed section, every repeatable row; each field with its label,
   section, tab, row, kind (text / dropdown / radio / switch ...), whether it
   is required or read-only in Edit, the options a dropdown offers, and the
   input.json key it maps to (bound with the fill engine's own binder);
3. close the form with Cancel / Back -- nothing is typed, chosen or saved, and
   no write request may leave the browser;
4. remember the Edit section: ``<memory_dir>/edit_sections/<phase>.json``
   keeps how to open it (listing -> search -> expand -> Edit), the surface
   (drawer or page, title, tabs, sections, Save / Cancel labels) and every
   field's structure.  The memory is value-free; the values read from an
   object are written to the run folder (``edit_values.json`` and an
   input.json-shaped ``edit_input.json`` that can be changed and run as an
   Edit operation).

Edit operations use the knowledge: a requested change to a field that is
read-only in Edit becomes NEEDS_INPUT before anything is touched, the learned
Save label is tried first, and a committed edit is read back from the
reopened Edit form.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.edit-section-knowledge.v1"
CAPTURE_SCHEMA = "hip.edit-section-capture.v1"
EXAMPLE_INPUT = Path(__file__).resolve().parents[1] / "examples" / "uhaul_poasn_full_dummy_input.json"
ALL_PHASES = (
    "source_document_type", "target_document_type", "data_map", "rule",
    "source_transport_profile", "target_transport_profile", "biz_flow",
)
NAME_PATHS = {
    "source_document_type": "name", "target_document_type": "name", "data_map": "map_identifier", "rule": "name",
    "source_transport_profile": "profile_name", "target_transport_profile": "profile_name",
    "biz_flow": "flow_details.business_flow_name",
}
CLOSE_LABELS = ("Cancel", "Close", "Back", "Discard", "Exit")
LEAVE_LABELS = ("Discard", "Discard Changes", "Leave", "Leave Page", "Yes", "OK")
# A listing's own search box inside a tab (BizFlow Configure Routing) is not a field.
_SEARCH_BOX = re.compile(r"^(table\s+)?(search|filter)\b", re.I)
_SENSITIVE = re.compile(r"pass(word|phrase)?|secret|token|api[\s_-]*key|private[\s_-]*key|credential", re.I)
_COMMIT_WORDS = re.compile(r"^(save|save changes|update|submit|create|finish|apply|deploy)$", re.I)
_CLOSE_WORDS = re.compile(r"^(‹\s*)?(cancel|close|back|discard|exit)$", re.I)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").split())


def _clean_label(value: Any) -> str:
    return re.sub(r"\s*[*:]+\s*$", "", " ".join(str(value or "").split())).strip()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------- page scripts
# The open Edit surface: the dialog / drawer holding the form, or the page
# section around it.  Marks it so the close can prove it went away.
_SURFACE_JS = r"""
({token}) => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const controlsIn = el => el.querySelectorAll('input:not([type=hidden]),textarea,select,[role=combobox]').length;
  const dialogs = Array.from(document.querySelectorAll('[role=dialog],dds-drawer,.dds__drawer,.dds__modal')).filter(visible).filter(d => controlsIn(d) > 0);
  let scope = dialogs.length ? dialogs[dialogs.length - 1] : null;
  let kind = scope ? (/modal/i.test(String(scope.className || '')) ? 'modal' : 'drawer') : 'page';
  if (!scope) {
    const forms = Array.from(document.querySelectorAll('form')).filter(visible).filter(f => controlsIn(f) > 0);
    const form = forms[forms.length - 1] || null;
    scope = form;
    let cur = form;
    while (cur && cur !== document.body) {
      if (Array.from(cur.children || []).some(c => /^H[1-3]$/.test(c.tagName) || c.getAttribute('role') === 'heading')) { scope = cur; break; }
      cur = cur.parentElement;
    }
  }
  if (!scope) return {found: false};
  scope.setAttribute('data-hip-edit-surface', token);
  const heading = scope.getAttribute('aria-label')
    || norm((scope.querySelector('.dds__drawer__title,h1,h2,h3,[role=heading]') || {}).innerText || '');
  const tabs = Array.from(scope.querySelectorAll('[role=tab]')).filter(visible).map((t, i) => {
    t.setAttribute('data-hip-op', `${token}-tab-${i}`);
    return {text: norm(t.innerText || t.textContent), selected: t.getAttribute('aria-selected') === 'true',
            disabled: !!t.disabled || t.getAttribute('aria-disabled') === 'true', token: `${token}-tab-${i}`};
  });
  const legends = Array.from(scope.querySelectorAll('legend,.dds__accordion__button,h3,h4')).filter(visible)
    .map(e => norm(e.innerText || e.textContent).replace(/\s*[*:]+\s*$/, '')).filter(Boolean);
  const buttons = Array.from(scope.querySelectorAll('button,a[href],[role=button],input[type=submit]')).filter(visible)
    .filter(b => !b.closest('dds-dropdown,[role=listbox],[role=tablist],[role=menu]') && !/accordion/.test(String(b.className || '')))
    .map(b => norm(b.innerText || b.textContent || b.getAttribute('aria-label') || b.value)).filter(Boolean);
  const loading = Array.from(scope.querySelectorAll('[role=progressbar],.dds__loading-indicator,.loading,.spinner')).some(visible)
    || Array.from(scope.querySelectorAll('div,span,p')).some(e => !e.children.length && visible(e) && /^loading\b/i.test(norm(e.innerText)));
  return {found: true, kind, title: norm(heading), tabs, sections: Array.from(new Set(legends)).slice(0, 60),
          buttons: Array.from(new Set(buttons)).slice(0, 40), url: location.href, loading,
          controls: Array.from(scope.querySelectorAll('input:not([type=hidden]),textarea,select,[role=combobox]')).filter(visible).length};
}
"""

# Collapsed sections of the surface (DDS accordions): opened to read them.
_COLLAPSED_JS = r"""
({token}) => {
  const scope = document.querySelector(`[data-hip-edit-surface="${token}"]`) || document;
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  return Array.from(scope.querySelectorAll('button[aria-expanded=false][aria-controls]')).filter(visible)
    .filter(b => !b.closest('dds-dropdown,[role=row],tr,[role=tablist]') && b.getAttribute('role') !== 'combobox')
    .filter(b => { const target = document.getElementById(b.getAttribute('aria-controls')); return !!target && !visible(target); })
    .filter(b => !/\b(add|save|submit|delete|remove|deploy|migrate)\b/i.test(b.innerText || b.getAttribute('aria-label') || ''))
    .map((b, i) => { const t = `${token}-sec-${i}-${Math.floor(Math.random() * 1e6)}`; b.setAttribute('data-hip-op', t);
      return {title: String(b.innerText || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(), token: t}; });
}
"""

# The options each dropdown offers, read from its own listbox (DDS renders it
# even while the popup is closed) -- never by opening it.
_OPTIONS_JS = r"""
({token}) => {
  const scope = document.querySelector(`[data-hip-edit-surface="${token}"]`) || document;
  const out = {};
  scope.querySelectorAll('[role=combobox],select').forEach(el => {
    if (!el.id) return;
    let opts = [];
    if (el.tagName === 'SELECT') opts = Array.from(el.options).map(o => o.text);
    else {
      const lb = el.getAttribute('aria-controls') && document.getElementById(el.getAttribute('aria-controls'));
      const host = lb || (el.closest('dds-dropdown') && el.closest('dds-dropdown').querySelector('[role=listbox]'));
      if (host) opts = Array.from(host.querySelectorAll('[role=option]')).map(o => (o.innerText || o.textContent || '').trim());
    }
    opts = opts.map(o => String(o).replace(/\s+/g, ' ').trim()).filter(o => o && !/^(select all|no data found|no results|loading)/i.test(o));
    if (opts.length) out[el.id] = Array.from(new Set(opts)).slice(0, 60);
  });
  return out;
}
"""

# A file input shows the uploaded file by name next to "Browse Files", not in its value.
_FILE_NAMES_JS = r"""
({token}) => {
  const scope = document.querySelector(`[data-hip-edit-surface="${token}"]`) || document;
  const out = {};
  scope.querySelectorAll('input[type=file]').forEach(el => {
    if (!el.id) return;
    if (el.files && el.files.length) { out[el.id] = el.files[0].name; return; }
    const host = el.closest('.dds__form-group,.dds__file-input,[class*=file-input],[class*=upload]') || el.parentElement;
    const shown = host && Array.from(host.querySelectorAll('[class*=file-name],[class*=file-input__name],[class*=filename],a[download]'))
      .map(e => (e.innerText || e.textContent || '').trim()).find(Boolean);
    if (shown) out[el.id] = shown;
  });
  return out;
}
"""

# Close the surface without saving: its own Cancel / Close / Back.
_CLOSE_JS = r"""
({token, labels, pick}) => {
  const norm = s => String(s || '').replace(/[‹›×]/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.disabled; };
  const scope = document.querySelector(`[data-hip-edit-surface="${token}"]`);
  if (!scope || !visible(scope)) return {found: false, gone: true};
  const els = Array.from(scope.querySelectorAll('button,a,[role=button]')).filter(visible)
    .filter(e => !e.closest('dds-dropdown,[role=listbox],[role=tablist],[role=menu]'));
  for (const w of labels.map(norm)) {
    const hit = els.find(e => norm(e.innerText || e.textContent || e.getAttribute('aria-label')) === w);
    if (hit) { hit.setAttribute('data-hip-op', pick); return {found: true, label: norm(hit.innerText || hit.getAttribute('aria-label'))}; }
  }
  return {found: false, gone: false};
}
"""

# Which captured controls belong to the Edit surface (not the listing behind a drawer).
_INSIDE_JS = r"""
({token, selectors}) => {
  const scope = document.querySelector(`[data-hip-edit-surface="${token}"]`);
  return selectors.map(sel => { if (!scope) return true; try { const el = document.querySelector(sel); return !!el && scope.contains(el); } catch (e) { return true; } });
}
"""

# V243R31: a menu the page already holds (hidden until its button is clicked):
# read without clicking anything.
_HIDDEN_MENU_JS = r"""
({controls}) => {
  const el = controls ? document.getElementById(controls) : null;
  if (!el || !(el.getAttribute('role') === 'menu' || /action-menu|dropdown-menu/.test(String(el.className || '')))) return [];
  return Array.from(el.querySelectorAll('[role=menuitem],[role=option],button,li'))
    .map(e => String(e.textContent || '').replace(/\s+/g, ' ').trim()).filter((t, i, a) => t && a.indexOf(t) === i);
}
"""

_SURFACE_GONE_JS = r"""
({token}) => {
  const el = document.querySelector(`[data-hip-edit-surface="${token}"]`);
  if (!el) return true;
  const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
  return !(r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none');
}
"""

# The first data row's name, when no object was named ("learn the Edit section").
_CLEAR_SEARCH_JS = r"""
() => {
  for (const box of Array.from(document.querySelectorAll('input[type=text],input[type=search],input:not([type])'))) {
    const hint = `${box.getAttribute('placeholder') || ''} ${box.getAttribute('aria-label') || ''}`;
    if (!/search|filter/i.test(hint) || !box.value) continue;
    box.value = '';
    box.dispatchEvent(new Event('input', { bubbles: true }));
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    box.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }));
  }
}
"""

_FIRST_ROW_JS = r"""
() => {
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const isPanel = r => /--expanded\b/.test(String(r.className || '')) || !!r.querySelector('[class*=expandable-content]');
  const rows = Array.from(document.querySelectorAll('tr,[role=row],.dds__table__row')).filter(visible)
    .filter(r => !isPanel(r) && !r.querySelector('[role=columnheader],th'));
  for (const row of rows) {
    const cells = Array.from(row.querySelectorAll('td,[role=cell],[role=gridcell]'))
      .map(c => (c.innerText || '').replace(/\s+/g, ' ').trim()).filter(t => t && !/^[⌄˅▾▸›>+]$/.test(t));
    // V243R39: an empty table's placeholder row is not an object.
    if (cells.length && /^(no (data|records?|results?|items?)( to display| found| available)?|nothing to (display|show))\.?$/i.test(cells[0])) continue;
    if (cells.length) return cells[0];
  }
  return '';
}
"""


# ---------------------------------------------------------------- fields
def _kind(control: Mapping[str, Any]) -> str:
    role = str(control.get("role") or "").lower()
    ctype = str(control.get("type") or "").lower()
    tag = str(control.get("tag") or "").lower()
    if ctype == "file":
        return "file"
    if ctype == "radio" or role == "radio":
        return "radio"
    if role == "switch":
        return "switch"
    if ctype == "checkbox" or role == "checkbox":
        return "checkbox_group" if control.get("group_label") else "checkbox"
    if role == "combobox" or str(control.get("component_tag") or "").replace("-", "_") in {"dds_dropdown", "app_generic_dropdown"}:
        return "multi_select" if str(control.get("selection_mode") or "") == "multiple" else "dropdown"
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "textarea"
    if ctype == "password":
        return "password"
    return "text"


def _group_key(control: Mapping[str, Any]) -> Tuple[str, ...]:
    return (
        str(control.get("tab") or ""), _norm(control.get("section")), str(control.get("row_signature") or ""),
        str(control.get("group_name") or "") or _norm(control.get("group_label")),
    )


def _basename(value: Any) -> str:
    text = str(value or "").strip()
    return re.split(r"[\\/]", text)[-1] if text else ""


def build_fields(controls: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One field per question: a radio / checkbox group is one field with its options."""
    fields: List[Dict[str, Any]] = []
    groups: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for c in controls:
        if not isinstance(c, Mapping):
            continue
        kind = _kind(c)
        raw_label = str(c.get("label") or c.get("placeholder") or c.get("name") or "")
        if kind in {"radio", "checkbox_group"}:
            key = _group_key(c)
            field = groups.get(key)
            option = _clean_label(c.get("label") or c.get("value"))
            if field is None:
                label = str(c.get("group_label") or c.get("name") or "")
                field = {
                    "label": _clean_label(label), "required": "*" in label or bool(c.get("required")),
                    "kind": kind, "options": [], "value": [] if kind == "checkbox_group" else "",
                    "controls": [],
                }
                groups[key] = field
                fields.append(field)
            if option and option not in field["options"]:
                field["options"].append(option)
            if c.get("checked"):
                if kind == "checkbox_group":
                    field["value"].append(option)
                else:
                    field["value"] = option
            field["controls"].append(dict(c))
            field["read_only"] = bool(field.get("read_only", True) and (c.get("disabled") or c.get("readonly")))
        else:
            value: Any
            if kind in {"switch", "checkbox"}:
                value = bool(c.get("checked"))
            elif kind == "multi_select":
                value = [str(x) for x in (c.get("selected_values") or []) if str(x).strip()]
            elif kind == "file":
                value = _basename(c.get("value"))
            else:
                value = str(c.get("value") or "")
                placeholder = str(c.get("placeholder") or "").strip()
                if not value and (c.get("disabled") or c.get("readonly")) and placeholder and _norm(placeholder) != _norm(_clean_label(raw_label)):
                    # A portal-owned read-only value shown as the placeholder (Version "1").
                    value = placeholder
            field = {
                "label": _clean_label(raw_label), "required": "*" in raw_label or bool(c.get("required")),
                "kind": kind, "value": value, "controls": [dict(c)],
                "read_only": bool(c.get("disabled") or c.get("readonly")),
            }
            if c.get("options"):
                field["options"] = list(c.get("options") or [])
            fields.append(field)
    for field in fields:
        first = field["controls"][0]
        field.update({
            "section": str(first.get("section") or ""), "tab": str(first.get("tab") or ""),
            "row_kind": str(first.get("row_kind") or ""), "row_index": first.get("row_index"),
            "framework_key": str(first.get("framework_key") or first.get("form_control_name") or first.get("name") or ""),
        })
        sensitive = field["kind"] == "password" or bool(_SENSITIVE.search(f"{field['label']} {field['framework_key']}"))
        field["sensitive"] = sensitive
        if sensitive:
            field["value"] = "********" if field["value"] not in ("", None, [], False) else ""
            field.pop("options", None)
        elif isinstance(field["value"], str):
            field["value"] = mask_sensitive_string(field["value"])
    return fields


def _example_input() -> Dict[str, Any]:
    try:
        return json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _phase_input(input_data: Optional[Mapping[str, Any]], phase: str) -> Dict[str, Any]:
    """The object whose keys the fields are mapped to: input.json's, else the shipped example's."""
    for source in (input_data or {}, _example_input()):
        objects = source.get("objects") if isinstance(source, Mapping) and isinstance(source.get("objects"), Mapping) else source
        if isinstance(objects, Mapping) and isinstance(objects.get(phase), Mapping) and objects.get(phase):
            return {"objects": {phase: copy.deepcopy(dict(objects[phase]))}}
    return {"objects": {phase: {}}}


def _rel_path(input_path: str) -> str:
    parts = str(input_path or "").split(".")
    return ".".join(parts[3:]) if len(parts) > 3 and parts[0] == "$" else str(input_path or "")


def _control_identity(control: Mapping[str, Any]) -> str:
    return str(control.get("id") or control.get("selector") or "")


def map_fields_to_input(phase: str, fields: List[Dict[str, Any]], controls: Sequence[Mapping[str, Any]],
                        input_data: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Bind input.json keys to the captured fields with the fill engine's binder.

    A field of a repeatable row the input does not have (a third condition)
    takes its path from the same field of a bound row.
    """
    from .stateful_form_runtime import build_phase_form_state_model, compile_phase_state_graph

    data = _phase_input(input_data, phase)
    # Structural binding: a field the portal keeps read-only in Edit (the Rule's
    # Name) is still that field -- the fill binder's "not interactable" penalty
    # would hand its key to a neighbour ("Document Type Name (Version)").
    neutral = [dict(c, disabled=False, readonly=False, interactable=True) for c in controls if isinstance(c, Mapping)]
    try:
        graph = compile_phase_state_graph(data, phase)
        model = build_phase_form_state_model(graph, neutral, phase=phase)
    except Exception as exc:
        return {"bound": 0, "error": mask_sensitive_string(str(exc))[:300]}
    owner: Dict[str, str] = {}
    for binding in model.get("bindings") or []:
        control = ((binding.get("binding") or {}).get("control")) or {}
        ident = _control_identity(control)
        if binding.get("status") == "resolved" and ident and ident not in owner:
            owner[ident] = _rel_path(str(binding.get("input_path") or ""))
    for field in fields:
        for control in field["controls"]:
            path = owner.get(_control_identity(control))
            if path:
                field["input_key"] = path
                break
    # Rows beyond the input's: the same field of another row gives the pattern.
    patterns: Dict[Tuple[str, str, str], str] = {}
    for field in fields:
        if field.get("input_key") and field.get("row_index") is not None and "[" in field["input_key"]:
            patterns.setdefault((field["row_kind"], _norm(field["section"]), _norm(field["label"])), field["input_key"])
    for field in fields:
        if field.get("input_key") or field.get("row_index") is None:
            continue
        pattern = patterns.get((field["row_kind"], _norm(field["section"]), _norm(field["label"])))
        if pattern:
            field["input_key"] = re.sub(r"\[\d+\](?!.*\[\d+\])", f"[{int(field['row_index'])}]", pattern)
            field["input_key_inferred_from_row"] = True
    return {"bound": sum(1 for f in fields if f.get("input_key")), "one_to_one": bool(model.get("one_to_one_pass")),
            "graph_nodes": len(graph.get("nodes") or [])}


def _set_path(root: Dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in re.split(r"\.(?![^\[]*\])", path) if p]
    cur: Any = root
    for i, part in enumerate(parts):
        m = re.fullmatch(r"([^\[]+)((?:\[\d+\])*)", part)
        if not m:
            return
        key, indexes = m.group(1), [int(x) for x in re.findall(r"\[(\d+)\]", m.group(2))]
        last = i == len(parts) - 1
        if not indexes:
            if last:
                cur[key] = value
                return
            cur = cur.setdefault(key, {})
            if not isinstance(cur, dict):
                return
            continue
        seq = cur.setdefault(key, [])
        for depth, index in enumerate(indexes):
            while len(seq) <= index:
                seq.append({} if (not last or depth < len(indexes) - 1) else None)
            if depth == len(indexes) - 1:
                if last:
                    seq[index] = value
                    return
                if not isinstance(seq[index], dict):
                    seq[index] = {}
                cur = seq[index]
            else:
                if not isinstance(seq[index], list):
                    seq[index] = []
                seq = seq[index]


def _input_style(value: Any, example: Any) -> Any:
    """A switch / checkbox in the vocabulary input.json uses for it ("Enable", "yes" ...)."""
    if not isinstance(value, bool):
        return value
    pairs = (("enable", "disable"), ("enabled", "disabled"), ("enabled", "not enabled"), ("yes", "no"), ("true", "false"), ("on", "off"))
    ex = _norm(example)
    for on, off in pairs:
        if ex in {on, off}:
            word = on if value else off
            return word.upper() if str(example).isupper() else (word.title() if str(example)[:1].isupper() else word)
    return value


def input_json_from_fields(phase: str, fields: Sequence[Mapping[str, Any]], input_data: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The Edit form's current values, shaped as input.json ``objects.<phase>``."""
    example = _phase_input(input_data, phase)["objects"][phase]
    obj: Dict[str, Any] = {}
    for field in fields:
        key = str(field.get("input_key") or "")
        if not key or field.get("sensitive"):
            continue
        value = field.get("value")
        if isinstance(value, bool):
            value = _input_style(value, _lookup(example, key))
        _set_path(obj, key, value)
    return {"objects": {phase: obj}}


def _lookup(obj: Any, path: str) -> Any:
    cur = obj
    for part in [p for p in re.split(r"\.(?![^\[]*\])", path) if p]:
        m = re.fullmatch(r"([^\[]+)((?:\[\d+\])*)", part)
        if not m or not isinstance(cur, Mapping):
            return None
        cur = cur.get(m.group(1))
        for index in re.findall(r"\[(\d+)\]", m.group(2)):
            if not isinstance(cur, list) or int(index) >= len(cur):
                return None
            cur = cur[int(index)]
    return cur


def compare_requested(fields: Sequence[Mapping[str, Any]], requested: Mapping[str, Any]) -> Dict[str, Any]:
    """Which requested input.json values the Edit form shows (read back after a save)."""
    from .section_judge import _values_equal

    flat: Dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for k, v in value.items():
                walk(f"{prefix}.{k}" if prefix else str(k), v)
        elif isinstance(value, list) and value and all(isinstance(x, Mapping) for x in value):
            for i, v in enumerate(value):
                walk(f"{prefix}[{i}]", v)
        else:
            flat[prefix] = value

    walk("", dict(requested or {}))
    by_key = {str(f.get("input_key")): f for f in fields if f.get("input_key")}
    seen: Dict[str, bool] = {}
    for key, want in flat.items():
        if want in (None, "", []) or key.startswith("_"):
            continue
        field = by_key.get(key)
        if field is None:
            continue
        got = field.get("value")
        if isinstance(got, bool):
            seen[key] = _norm(want) in ({"true", "yes", "enable", "enabled", "on"} if got else {"false", "no", "disable", "disabled", "not enabled", "off"})
        else:
            seen[key] = bool(_values_equal(want, got))
    return {"requested_values_seen": seen, "all_seen": all(seen.values()) if seen else None,
            "not_in_form": [k for k, v in flat.items() if v not in (None, "", []) and k not in by_key and not k.startswith("_")]}


# ---------------------------------------------------------------- memory
class SectionMemory:
    """Value-free knowledge of an action's section per phase.

    Edit: ``<memory_dir>/edit_sections/<phase>.json`` (V243R30).  V243R31: Clone,
    Deploy, Migrate ...: ``<memory_dir>/action_sections/<action>/<phase>.json`` --
    the form or dialog (fields, options), the menu of target environments per
    source environment, or the confirmation the action asks.
    """

    def __init__(self, memory_dir: Path, action: str = "edit"):
        self.action = str(action or "edit")
        self.root = Path(memory_dir) / "edit_sections" if self.action == "edit" else Path(memory_dir) / "action_sections" / self.action

    @classmethod
    def for_run(cls, config: Any = None, page: Any = None, *, action: str = "edit") -> Optional["SectionMemory"]:
        from .form_structure_memory import resolve_memory_dir

        memory_dir = resolve_memory_dir(config, page)
        if memory_dir is None:
            return None
        return EditSectionMemory(memory_dir) if action == "edit" else cls(memory_dir, action)

    def file(self, phase: str) -> Path:
        return self.root / f"{re.sub(r'[^a-z0-9_]+', '_', str(phase or 'unknown').lower())}.json"

    def load(self, phase: str) -> Dict[str, Any]:
        try:
            data = json.loads(self.file(phase).read_text(encoding="utf-8")) if self.file(phase).exists() else {}
        except Exception:
            data = {}
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
            return {}
        return data

    def known(self, phase: str) -> bool:
        data = self.load(phase)
        return bool(data.get("fields") or data.get("menus") or data.get("confirm"))

    def record(self, phase: str, capture: Mapping[str, Any], *, opener: Optional[Mapping[str, Any]] = None,
               source: str = "learn_edit") -> Dict[str, Any]:
        previous = self.load(phase)
        fields = []
        for f in capture.get("fields") or []:
            # First row of a repeatable list stands for the row; values are never stored.
            if f.get("row_index") not in (None, 0):
                continue
            row = {k: f.get(k) for k in ("label", "section", "tab", "row_kind", "kind", "required", "read_only", "input_key", "sensitive", "framework_key")}
            if f.get("options") and not f.get("sensitive"):
                row["options"] = [mask_sensitive_string(str(o)) for o in list(f["options"])[:40]]
            fields.append(row)
        rows: Dict[str, int] = {}
        for f in capture.get("fields") or []:
            if f.get("row_kind") and f.get("row_index") is not None:
                rows[str(f["row_kind"])] = max(rows.get(str(f["row_kind"]), 0), int(f["row_index"]) + 1)
        surface = dict(capture.get("surface") or {})
        menus = dict(previous.get("menus") or {})
        for env, items in dict(capture.get("menus") or {}).items():
            menus[str(env)] = [str(x) for x in items][:20]
        confirm = dict(capture.get("confirm") or previous.get("confirm") or {})
        structure = sorted(f"{f.get('tab')}|{_norm(f.get('section'))}|{_norm(f.get('label'))}|{f.get('kind')}" for f in fields)
        structure += sorted(f"menu|{env}|{'/'.join(items)}" for env, items in menus.items())
        fingerprint = hashlib.sha256(json.dumps(structure).encode("utf-8")).hexdigest()[:16]
        changed = bool(previous) and previous.get("structure_fingerprint") != fingerprint
        data = {
            "schema_version": SCHEMA,
            "phase": phase,
            "action": self.action,
            "section_kind": str(capture.get("section_kind") or previous.get("section_kind") or "form"),
            "menus": menus,
            "confirm": confirm,
            "listing_url": str(capture.get("listing_url") or previous.get("listing_url") or ""),
            "opener": {k: (opener or {}).get(k) for k in ("path", "selectors", "learned_opener_labels")} if opener else previous.get("opener") or {},
            "surface": {
                "kind": surface.get("kind"), "title": surface.get("title"), "tabs": [t.get("text") for t in surface.get("tabs") or []],
                "sections": list(surface.get("sections") or [])[:60], "collapsed_sections_opened": list(capture.get("sections_opened") or []),
                "commit_labels": [b for b in surface.get("buttons") or [] if _COMMIT_WORDS.match(b)],
                "close_labels": [b for b in surface.get("buttons") or [] if _CLOSE_WORDS.match(b)],
                "url_changes": bool(capture.get("url_changed")),
            },
            "fields": fields,
            "field_count": len(fields),
            "read_only_fields": sorted({f["label"] for f in fields if f.get("read_only")}),
            "required_fields": sorted({f["label"] for f in fields if f.get("required")}),
            "input_keys": sorted({re.sub(r"\[\d+\]", "[*]", str(f["input_key"])) for f in fields if f.get("input_key")}),
            "portal_only_fields": sorted({f["label"] for f in fields if not f.get("input_key")}),
            "row_groups": rows,
            "structure_fingerprint": fingerprint,
            "learned_at": previous.get("learned_at") or _now(),
            "updated_at": _now(),
            "verified": int(previous.get("verified") or 0) + (1 if capture.get("record_verified") else 0),
            "captures": int(previous.get("captures") or 0) + 1,
            "structure_changes": int(previous.get("structure_changes") or 0) + (1 if changed else 0),
            "last_source": source,
            "last_capture_file": str(capture.get("capture_file") or ""),
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }
        safe_write_json(self.file(phase), mask_sensitive_data(data), mask=False)
        return data

    def summary(self, phase: str) -> Dict[str, Any]:
        data = self.load(phase)
        if not data:
            return {"phase": phase, "known": False}
        surface = data.get("surface") or {}
        return {
            "phase": phase, "action": self.action, "known": True, "title": surface.get("title") or (data.get("confirm") or {}).get("title"),
            "kind": surface.get("kind"), "section_kind": data.get("section_kind"), "menus": data.get("menus") or {},
            "tabs": surface.get("tabs") or [], "fields": data.get("field_count"), "mapped": len(data.get("input_keys") or []),
            "read_only": data.get("read_only_fields") or [], "portal_only": len(data.get("portal_only_fields") or []),
            "commit_labels": surface.get("commit_labels") or [], "path": (data.get("opener") or {}).get("path") or [],
            "verified": data.get("verified"), "captures": data.get("captures"), "updated_at": data.get("updated_at"),
        }

    def summaries(self, phases: Sequence[str] = ALL_PHASES) -> Dict[str, Any]:
        rows = [self.summary(p) for p in phases]
        return {"known": sum(1 for r in rows if r.get("known")), "total": len(rows), "phases": rows}

    def next_environments(self, phase: str, source: str) -> List[str]:
        """Where this action goes from ``source`` (learned from its menus)."""
        menus = self.load(phase).get("menus") or {}
        return [str(x) for x in menus.get(str(source or "").upper()) or []]

    def route(self, phase: str, source: str, target: str) -> List[str]:
        """Environments to go through from ``source`` to ``target`` (DEV > TEST2 > PROD), learned."""
        menus = {k.upper(): [str(x).upper() for x in v] for k, v in (self.load(phase).get("menus") or {}).items()}
        start, goal = str(source or "").upper(), str(target or "").upper()
        paths, seen = [[start]], {start}
        while paths:
            path = paths.pop(0)
            for nxt in menus.get(path[-1], []):
                if nxt == goal:
                    return path + [nxt]
                if nxt not in seen:
                    seen.add(nxt)
                    paths.append(path + [nxt])
        return []


class EditSectionMemory(SectionMemory):
    """``<memory_dir>/edit_sections/<phase>.json`` -- value-free Edit section knowledge (V243R30)."""

    def __init__(self, memory_dir: Path):
        super().__init__(memory_dir, "edit")

    @classmethod
    def for_run(cls, config: Any = None, page: Any = None, *, action: str = "edit") -> Optional["SectionMemory"]:
        return SectionMemory.for_run(config, page, action=action)


def action_summaries(memory_dir: Path, actions: Sequence[str] = ("edit", "clone", "deploy", "migrate"),
                     phases: Sequence[str] = ALL_PHASES) -> Dict[str, Any]:
    """Which phases' action sections are known, per action."""
    out: Dict[str, Any] = {}
    for action in actions:
        memory = EditSectionMemory(memory_dir) if action == "edit" else SectionMemory(memory_dir, action)
        out[action] = memory.summaries(phases)
    return out


# ---------------------------------------------------------------- capture
async def wait_for_edit_surface(page: Any, *, timeout_s: float = 30.0) -> Dict[str, Any]:
    """The Edit form itself -- not the listing behind it -- rendered, loaded and stable.

    The portal opens the drawer (or page) first and fills it from the record a
    moment later; a form read before that would be empty.
    """
    deadline = time.monotonic() + max(1.0, float(timeout_s))
    last: Dict[str, Any] = {}
    stable = 0
    while True:
        token = uuid.uuid4().hex[:10]
        try:
            surface = await page.evaluate(_SURFACE_JS, {"token": token})
        except Exception:
            surface = {"found": False}
        ready = bool(surface.get("found") and int(surface.get("controls") or 0) > 0 and not surface.get("loading"))
        if ready and last.get("found") and last.get("controls") == surface.get("controls") and last.get("title") == surface.get("title"):
            stable += 1
        else:
            stable = 0
        last = surface
        if ready and stable >= 1:
            return {**surface, "surface_token": token, "ready": True}
        if time.monotonic() >= deadline:
            return {**surface, "surface_token": token, "ready": False}
        await page.wait_for_timeout(300)


class EditSectionLearner:
    """Opens a phase object's Edit form, reads it completely and closes it unsaved."""

    def __init__(self, config: Any, browser: Any, run_dir: Path, *, listing_urls: Optional[Mapping[str, str]] = None):
        from .portal_operations import PortalOperationRunner

        self.config = config
        self.browser = browser
        self.run_dir = Path(run_dir)
        self.runner = PortalOperationRunner(config, browser, self.run_dir, listing_urls=listing_urls)

    async def _page(self) -> Any:
        return await self.runner._page()

    async def _click(self, token: str, label: str) -> None:
        await self.runner._click_token(token, label, mutation=False)

    async def capture(self, phase: str, *, input_data: Optional[Mapping[str, Any]] = None, restore_tab: bool = True) -> Dict[str, Any]:
        """Read the open Edit form: every tab, every collapsed section.  Read-only."""
        from .stateful_form_runtime import capture_stateful_controls

        page = await self._page()
        wait = float(getattr(getattr(self.config, "edit_sections", None), "form_wait_seconds", 30.0) or 30.0)
        surface = await wait_for_edit_surface(page, timeout_s=wait)
        token = str(surface.pop("surface_token", "") or "")
        if not surface.get("found") or not surface.get("ready"):
            return {"pass": False, "status": "edit_form_not_found" if not surface.get("found") else "edit_form_not_ready", "surface": surface}
        tabs = [t for t in surface.get("tabs") or [] if not t.get("disabled")]
        start_tab = next((t for t in tabs if t.get("selected")), tabs[0] if tabs else None)
        controls: List[Dict[str, Any]] = []
        seen: set = set()
        opened: List[str] = []
        per_tab: List[Dict[str, Any]] = []
        for tab in (tabs or [None]):
            if tab is not None and not tab.get("selected"):
                await self._click(str(tab["token"]), str(tab["text"]))
                await page.wait_for_timeout(350)
            for _ in range(4):
                collapsed = await page.evaluate(_COLLAPSED_JS, {"token": token})
                if not collapsed:
                    break
                for section in collapsed:
                    await self._click(str(section["token"]), str(section["title"] or "section"))
                    opened.append(str(section["title"]))
                    await page.wait_for_timeout(250)
            rows = [dict(r) for r in (await capture_stateful_controls(page, phase) or []) if isinstance(r, Mapping)]
            rows = [r for r in rows if not (str(r.get("type") or "").lower() == "search"
                                            or _SEARCH_BOX.match(_clean_label(r.get("label") or r.get("placeholder"))))]
            try:
                inside = await page.evaluate(_INSIDE_JS, {"token": token, "selectors": [str(r.get("selector") or "") for r in rows]})
                rows = [r for r, keep in zip(rows, inside) if keep]
            except Exception:
                pass
            options = await page.evaluate(_OPTIONS_JS, {"token": token})
            try:
                shown_files = await page.evaluate(_FILE_NAMES_JS, {"token": token})
            except Exception:
                shown_files = {}
            added = 0
            for c in rows or []:
                ident = (_control_identity(c), str(tab["text"] if tab else ""))
                if not _control_identity(c) or ident in seen:
                    continue
                seen.add(ident)
                c = dict(c)
                c["tab"] = str(tab["text"]) if tab else ""
                if c.get("id") and options.get(str(c["id"])):
                    c["options"] = options[str(c["id"])]
                if c.get("id") and shown_files.get(str(c["id"])) and not str(c.get("value") or "").strip():
                    c["value"] = shown_files[str(c["id"])]
                controls.append(c)
                added += 1
            per_tab.append({"tab": tab["text"] if tab else "", "controls": added})
        if restore_tab and start_tab is not None and len(tabs) > 1:
            await self._click(str(start_tab["token"]), str(start_tab["text"]))
            await page.wait_for_timeout(200)
        fields = build_fields(controls)
        mapping = map_fields_to_input(phase, fields, controls, input_data)
        surface["tabs"] = [{k: t.get(k) for k in ("text", "selected", "disabled")} for t in surface.get("tabs") or []]
        values = [
            {k: f.get(k) for k in ("label", "section", "tab", "row_kind", "row_index", "kind", "required", "read_only", "value", "options", "input_key", "sensitive")}
            for f in fields
        ]
        return {
            "schema_version": CAPTURE_SCHEMA, "phase": phase, "pass": bool(fields), "status": "captured" if fields else "no_fields",
            "surface": surface, "surface_token": token, "tabs_read": per_tab, "sections_opened": opened,
            "fields": values, "field_count": len(values), "mapping": mapping,
            "input_json": input_json_from_fields(phase, values, input_data), "read_only": True,
        }

    async def close(self, token: str, listing_url: str) -> Dict[str, Any]:
        """Leave the Edit form without saving: Cancel / Close / Back, else Escape, else the listing."""
        result = await self._close(token, listing_url)
        try:
            # An Edit page returned to its listing: that is the expected route now.
            page = await self._page()
            if result.get("closed") and page.url and hasattr(self.browser, "_active_target_url"):
                self.browser._active_target_url = str(page.url)
        except Exception:
            pass
        return result

    async def _close(self, token: str, listing_url: str) -> Dict[str, Any]:
        page = await self._page()
        pick = uuid.uuid4().hex[:10]
        hit = await page.evaluate(_CLOSE_JS, {"token": token, "labels": list(CLOSE_LABELS), "pick": pick})
        via = ""
        if hit.get("gone"):
            return {"closed": True, "via": "already_closed"}
        if hit.get("found"):
            via = str(hit.get("label") or "cancel")
            await self._click(pick, via.title())
        else:
            try:
                await page.keyboard.press("Escape")
                via = "escape"
            except Exception:
                via = ""
        from .portal_operations import _CONFIRM_JS

        for _ in range(24):
            await page.wait_for_timeout(250)
            try:
                if await page.evaluate(_SURFACE_GONE_JS, {"token": token}):
                    return {"closed": True, "via": via}
            except Exception:
                # The page navigated (an Edit page returning to its listing).
                return {"closed": True, "via": via}
            leave = uuid.uuid4().hex[:10]
            try:
                confirm = await page.evaluate(_CONFIRM_JS, {"labels": list(LEAVE_LABELS), "token": leave})
            except Exception:
                confirm = {}
            if confirm.get("found"):
                # "Discard changes?" -- nothing was changed, leaving discards nothing.
                await self._click(leave, str(confirm.get("label") or "discard").title())
                via = f"{via}+{confirm.get('label')}"
        if listing_url:
            await self.runner._navigate(listing_url)
            return {"closed": True, "via": f"{via}+listing" if via else "listing"}
        return {"closed": False, "via": via}

    def _writes_since(self, start: int) -> List[Dict[str, Any]]:
        try:
            from .certified_future_task_agent import CertifiedHIPFutureTaskExecutor

            rows = CertifiedHIPFutureTaskExecutor._network_rows_since(self.browser, start, dispatch={})
        except Exception:
            rows = []
        return [{"method": r.get("method"), "url": mask_sensitive_string(str(r.get("url") or ""))[:200], "status": r.get("status")}
                for r in rows if str(r.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}]

    async def first_row_name(self, phase: str) -> str:
        await self.runner._navigate(self.runner._listing_url(phase))
        page = await self._page()
        try:
            # V243R39: a search left from looking for another object filters the table.
            await page.evaluate(_CLEAR_SEARCH_JS)
            await page.wait_for_timeout(400)
        except Exception:
            pass
        wait = float(getattr(getattr(self.config, "portal", None), "navigation_render_wait_seconds", 20) or 20)
        deadline = time.monotonic() + min(wait, 60.0)
        while True:
            name = str(await page.evaluate(_FIRST_ROW_JS) or "")
            if name or time.monotonic() >= deadline:
                return name
            await page.wait_for_timeout(400)

    async def learn(self, phase: str, *, target: str = "", input_data: Optional[Mapping[str, Any]] = None,
                    options: Optional[Mapping[str, Any]] = None, out_dir: Optional[Path] = None, action: str = "edit",
                    gate: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Learn one action's section of a phase object (read-only).

        ``edit`` / ``clone``: the form, read completely, closed unsaved.
        V243R31 ``migrate`` / ``deploy``: per environment tab, where the action goes
        (its menu, its confirmation, or its dialog) -- never confirmed.
        """
        from .portal_operations import OperationNeedsInput, _name_in
        from .portal_skills import PortalSkillStore

        started = time.monotonic()
        action = str(action or "edit")
        out = Path(out_dir or self.run_dir / ("edit_sections" if action == "edit" else f"action_sections/{action}") / phase)
        start_net = len(getattr(self.browser, "network_tab_events", []) or [])
        listing = self.runner._listing_url(phase)
        result: Dict[str, Any] = {"schema_version": CAPTURE_SCHEMA, "phase": phase, "listing_url": listing, "operation": f"learn_{action}",
                                  "action": action}
        if not target:
            objects = (input_data or {}).get("objects") if isinstance((input_data or {}).get("objects"), Mapping) else {}
            target = _name_in(objects.get(phase) or {}) if isinstance(objects, Mapping) else ""
            result["target_source"] = "input.json" if target else "first_listing_row"
        if not target:
            target = await self.first_row_name(phase)
        result["target"] = target
        if not target:
            result.update({"pass": False, "status": "no_row_on_listing", "result": "NEEDS_INPUT",
                           "reason": "the listing shows no object to open; name one"})
            return self._finish(result, started, out)
        if action not in {"edit", "clone"}:
            return await self._learn_promotion(phase, target=target, action=action, gate=gate or {}, result=result,
                                               started=started, out=out, start_net=start_net, listing=listing)
        try:
            opened = await self.runner.open_surface(phase=phase, operation=action, target=target, form_phase=phase, options=dict(options or {}))
        except OperationNeedsInput as exc:
            result.update({"pass": False, "status": "needs_input", "result": "NEEDS_INPUT", "needs_input": exc.detail, "reason": exc.detail.get("reason")})
            return self._finish(result, started, out)
        result["open"] = {k: opened.get(k) for k in ("path", "selectors", "panel", "learned_opener_labels", "learned_mode", "surface_url", "form_visible")}
        if not opened.get("form_visible"):
            result.update({"pass": False, "status": "edit_form_not_visible", "result": "FAILED"})
            return self._finish(result, started, out)
        page = await self._page()
        before_url = str(page.url or "")
        capture = await self.capture(phase, input_data=input_data)
        capture["listing_url"] = listing
        capture["url_changed"] = bool(listing and before_url and before_url.split("#")[0].rstrip("/") != listing.split("#")[0].rstrip("/"))
        name_key = NAME_PATHS.get(phase, "")
        name_field = next((f for f in capture.get("fields") or [] if f.get("input_key") == name_key), None)
        capture["record_verified"] = bool(name_field and _norm(name_field.get("value")) == _norm(target))
        result["close"] = await self.close(str(capture.get("surface_token") or ""), listing) if capture.get("surface_token") else {"closed": False}
        writes = self._writes_since(start_net)
        result.update({
            "capture": {k: capture.get(k) for k in ("status", "surface", "tabs_read", "sections_opened", "field_count", "mapping", "record_verified")},
            "write_requests": writes, "no_write_requests": not writes,
        })
        if not capture.get("pass"):
            result.update({"pass": False, "status": str(capture.get("status") or "capture_failed"), "result": "FAILED"})
            return self._finish(result, started, out)
        out.mkdir(parents=True, exist_ok=True)
        values_file = out / "edit_values.json"
        safe_write_json(values_file, mask_sensitive_data({
            "schema_version": CAPTURE_SCHEMA, "phase": phase, "object": target, "captured_at": _now(), "surface": capture.get("surface"),
            "fields": capture.get("fields"), "record_verified": capture.get("record_verified"),
        }), mask=False)
        safe_write_json(out / "edit_input.json", capture.get("input_json") or {}, mask=False)
        capture["capture_file"] = str(values_file)
        knowledge: Dict[str, Any] = {}
        memory = SectionMemory.for_run(self.config, page, action=action)
        if memory is not None and not writes:
            knowledge = memory.record(phase, capture, opener=result["open"], source=f"learn_{action}")
        store = PortalSkillStore.for_run(self.config, page)
        if store is not None and capture.get("record_verified") and result["close"].get("closed") and not writes:
            # The opened form was that object's: a verified outcome of the action's path.
            result["learned_mode"] = store.record_opener_outcome(phase, action, success=True, reason=f"{action}_section_learned")
        passed = bool(result["close"].get("closed")) and not writes
        result.update({
            "pass": passed, "status": f"{action}_section_learned" if passed else ("write_request_seen" if writes else f"{action}_form_not_closed"),
            "result": "SUCCESS" if passed else "FAILED",
            "fields": capture.get("fields"), "input_json": capture.get("input_json"),
            "values_file": str(values_file), "edit_input_file": str(out / "edit_input.json"),
            "knowledge": {k: knowledge.get(k) for k in ("field_count", "read_only_fields", "input_keys", "portal_only_fields", "row_groups", "verified", "captures")} if knowledge else {},
            "knowledge_file": str(memory.file(phase)) if memory is not None else "",
        })
        return self._finish(result, started, out)

    async def _open_details(self, phase: str, target: str) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        """Listing -> search -> the exact row -> its expanded details (read-only)."""
        from .portal_operations import OperationNeedsInput

        runner = self.runner
        await runner._navigate(runner._listing_url(phase))
        await runner._search(target)
        page = await self._page()
        token = uuid.uuid4().hex[:10]
        hit = await runner._find_row(page, target, [], token)
        if hit.get("found") == "ambiguous":
            raise OperationNeedsInput("target", f"{len(hit.get('candidates') or [])} rows match {target!r}", offered=hit.get("candidates") or [])
        if hit.get("found") != "expander":
            return token, hit, {}
        if not hit.get("expanded"):
            await runner._click_token(token, "Expand the row")
        return token, hit, await runner._panel(token, target)

    async def _learn_promotion(self, phase: str, *, target: str, action: str, gate: Mapping[str, Any], result: Dict[str, Any],
                               started: float, out: Path, start_net: int, listing: str) -> Dict[str, Any]:
        """V243R31: Migrate / Deploy: where the action goes from every environment of the object.

        A menu the page already holds is read without a click.  An action that is
        not a guarded word (Migrate) is opened, read and closed.  A guarded one
        (Deploy) may act at once, so it is opened only with the mutation gate open
        -- and even then nothing is confirmed; otherwise it is learned by the first
        authorized deploy, which records what it saw.
        """
        from .portal_operations import OPENER_LABELS, OperationNeedsInput, _GUARDED, _PANEL_PICK_JS

        runner = self.runner
        labels = list(OPENER_LABELS.get(action) or (action.title(),))
        menus: Dict[str, List[str]] = {}
        kinds: List[str] = []
        confirm: Dict[str, Any] = {}
        dialog: Dict[str, Any] = {}
        blocked: List[str] = []
        label_seen = ""
        try:
            token, hit, panel = await self._open_details(phase, target)
        except OperationNeedsInput as exc:
            result.update({"pass": False, "status": "needs_input", "result": "NEEDS_INPUT", "needs_input": exc.detail, "reason": exc.detail.get("reason")})
            return self._finish(result, started, out)
        if not panel.get("found"):
            result.update({"pass": False, "status": "no_expanded_details", "result": "FAILED",
                           "reason": f"{target!r} has no expander on this listing; its {action} is learned by the first {action}"})
            return self._finish(result, started, out)
        envs = [str(t.get("text")) for t in panel.get("tabs") or [] if not t.get("disabled")] or [""]
        result["environments"] = envs
        for env in envs:
            page = await self._page()
            if env:
                panel = await runner._select_tab(token, target, env, panel)
            pick = uuid.uuid4().hex[:10]
            act = await page.evaluate(_PANEL_PICK_JS, {"token": token, "kind": "action", "value": labels, "pickToken": pick})
            if not act.get("found"):
                menus[env] = []
                continue
            label = str(act.get("label") or labels[0])
            label_seen = label_seen or label
            hidden = await page.evaluate(_HIDDEN_MENU_JS, {"controls": str(act.get("controls") or "")})
            if hidden:
                menus[env] = hidden
                kinds.append("menu")
                continue
            guarded = bool(_GUARDED.search(label))
            if guarded and not gate.get("pass"):
                blocked.append(env)
                continue
            task_id = f"learn-{uuid.uuid4().hex[:8]}"
            click_net = len(getattr(self.browser, "network_tab_events", []) or [])
            if guarded:
                # Gate open: the opener only; its menu choice / confirmation is never clicked.
                self.browser.set_portal_mutation_authorization(enabled=True, allowed_labels=[label], task_id=task_id)
            try:
                await runner._click_token(pick, label.title(), mutation=guarded)
                audit: Dict[str, Any] = {"path": [label]}
                opened = await runner._note_what_opened(audit, controls=str(act.get("controls") or ""))
                kind = str(opened.get("kind") or "none")
                kinds.append(kind)
                if kind == "menu":
                    menus[env] = list(opened.get("items") or [])
                    await self._close_menu(pick, label)
                elif kind == "confirm":
                    text = str(opened.get("text") or "").upper()
                    to = re.search(r"\bTO\s+(DEV|TEST\d*|PROD|UAT|QA|SIT|STAGE)\b", text)
                    menus[env] = [to.group(1)] if to else []
                    confirm = {"title": opened.get("title") or "", "buttons": list(opened.get("buttons") or []),
                               "text": runner._confirm_template(str(opened.get("text") or ""), target), "asks_before_acting": True}
                    await runner._cancel_dialog()
                elif kind == "form":
                    cap = await self.capture(f"universal_{phase}_{action}", input_data={})
                    env_field = next((f for f in cap.get("fields") or [] if f.get("options") and "environment" in _norm(f.get("label"))), None)
                    menus[env] = list((env_field or {}).get("options") or [])
                    dialog = cap
                    await self.close(str(cap.get("surface_token") or ""), listing)
                if guarded:
                    await runner._reconcile_opener(click_net, form_visible=kind in {"menu", "confirm", "form"})
            finally:
                if guarded:
                    self.browser.clear_portal_mutation_authorization()
            # Start the next environment from a fresh listing: the dialog / confirmation is gone.
            token, hit, panel = await self._open_details(phase, target)
        writes = self._writes_since(start_net)
        learned_envs = {env: items for env, items in menus.items() if env}
        section_kind = next((k for k in ("dialog" if dialog else "", "confirm" if confirm else "", "menu" if "menu" in kinds else "") if k), "")
        memory = SectionMemory.for_run(self.config, await self._page(), action=action)
        knowledge: Dict[str, Any] = {}
        if memory is not None and not writes and (learned_envs or confirm or dialog):
            knowledge = memory.record(phase, {
                **({k: dialog.get(k) for k in ("fields", "surface")} if dialog else {}),
                "section_kind": section_kind or "menu", "menus": learned_envs, "confirm": confirm, "listing_url": listing,
                "action_label": label_seen,
            }, opener={"path": ["expand row", label_seen.lower()] if label_seen else ["expand row"]}, source=f"learn_{action}")
        known_before = bool(memory is not None and memory.known(phase))
        passed = not writes and bool(learned_envs or known_before)
        result.update({
            "pass": passed,
            "status": ("write_request_seen" if writes else f"{action}_section_learned" if learned_envs and not blocked
                       else f"{action}_section_learned_partly" if learned_envs else f"{action}_needs_authorized_run"),
            "result": "SUCCESS" if passed else ("FAILED" if writes else "BLOCKED"),
            "action_label": label_seen, "section_kind": section_kind or ("menu" if learned_envs else ""),
            "menus": learned_envs, "confirm": confirm or None,
            "dialog_fields": [{k: f.get(k) for k in ("label", "kind", "required", "options")} for f in dialog.get("fields") or []] if dialog else None,
            "blocked_environments": blocked, "write_requests": writes, "no_write_requests": not writes,
            "knowledge_file": str(memory.file(phase)) if memory is not None else "",
            "knowledge": {"menus": (knowledge or {}).get("menus"), "section_kind": (knowledge or {}).get("section_kind")} if knowledge else {},
        })
        if blocked:
            result["reason"] = (f"{label_seen or labels[0]!r} is a guarded action that may act at once, so it was not opened in "
                                f"{', '.join(blocked)}: open the mutation gate to learn it (nothing is confirmed), or it is learned "
                                f"by the first authorized {action}")
        return self._finish(result, started, out)

    async def _close_menu(self, pick: str, label: str) -> None:
        page = await self._page()
        try:
            still = await page.evaluate("(t) => { const b = document.querySelector(`[data-hip-op=\"${t}\"]`); return !!b && b.getAttribute('aria-expanded') === 'true'; }", pick)
            if still:
                await self.runner._click_token(pick, label.title())
            else:
                await page.keyboard.press("Escape")
        except Exception:
            pass

    def _finish(self, result: Dict[str, Any], started: float, out: Path) -> Dict[str, Any]:
        result["seconds"] = round(time.monotonic() - started, 1)
        result.setdefault("result", "SUCCESS" if result.get("pass") else "FAILED")
        try:
            safe_write_json(out / "edit_section_learning.json", mask_sensitive_data(result))
        except Exception:
            pass
        return mask_sensitive_data(result)


def phase_targets(input_data: Optional[Mapping[str, Any]], phases: Sequence[str]) -> Dict[str, str]:
    from .portal_operations import _name_in

    objects = (input_data or {}).get("objects") if isinstance((input_data or {}).get("objects"), Mapping) else {}
    return {p: (_name_in(objects.get(p) or {}) if isinstance(objects, Mapping) else "") for p in phases}


def resolve_phases(value: Any) -> List[str]:
    text = str(value or "all").strip().lower()
    if text in {"", "all", "every", "*"}:
        return list(ALL_PHASES)
    wanted: List[str] = []
    aliases = {"transport_profile": ["source_transport_profile", "target_transport_profile"],
               "document_type": ["source_document_type", "target_document_type"], "bizflow": ["biz_flow"], "datamap": ["data_map"]}
    for part in re.split(r"[,\s]+", text):
        part = part.strip().replace("-", "_")
        if not part:
            continue
        for phase in aliases.get(part, [part]):
            if phase in ALL_PHASES and phase not in wanted:
                wanted.append(phase)
    return wanted


async def learn_edit_sections(
    config: Any, input_data: Optional[Mapping[str, Any]], *, run_dir: Path, phases: Sequence[str] = ALL_PHASES,
    browser: Any = None, listing_urls: Optional[Mapping[str, str]] = None, targets: Optional[Mapping[str, str]] = None,
    actions: Sequence[str] = ("edit",), gate: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Learn each phase's action sections (Edit; V243R31: Clone, Deploy, Migrate) in one browser session."""
    if browser is None:
        from .browser_session import BrowserSession

        async with BrowserSession(config, run_dir) as session:
            return await learn_edit_sections(config, input_data, run_dir=run_dir, phases=phases, browser=session,
                                             listing_urls=listing_urls, targets=targets, actions=actions, gate=gate)
    from .environment_faults import is_environment_fatal

    learner = EditSectionLearner(config, browser, run_dir, listing_urls=listing_urls)
    names = {**phase_targets(input_data, phases), **dict(targets or {})}
    report: Dict[str, Any] = {"schema_version": "hip.edit-section-learning-report.v1", "phases": [], "started_at": _now(),
                              "actions": list(actions)}
    stop = False
    for phase in phases:
        for action in actions:
            try:
                row = await learner.learn(phase, target=str(names.get(phase) or ""), input_data=input_data, action=action, gate=gate)
            except Exception as exc:
                row = {"phase": phase, "action": action, "pass": False, "status": "error", "result": "FAILED",
                       "error": mask_sensitive_string(str(exc))[:600], "environment_fault": is_environment_fatal(exc)}
            report["phases"].append(row)
            if row.get("environment_fault"):
                stop = True
                break
        if stop:
            break
    report["pass"] = bool(report["phases"]) and all(r.get("pass") for r in report["phases"])
    report["summary"] = [{"phase": r.get("phase"), "action": r.get("action") or "edit", "target": r.get("target"), "result": r.get("result"),
                          "status": r.get("status"), "menus": r.get("menus"), "reason": r.get("reason"),
                          "fields": (r.get("capture") or {}).get("field_count"), "tabs": [t.get("tab") for t in (r.get("capture") or {}).get("tabs_read") or [] if t.get("tab")],
                          "read_only": (r.get("knowledge") or {}).get("read_only_fields"), "values_file": r.get("values_file")} for r in report["phases"]]
    report["finished_at"] = _now()
    safe_write_json(Path(run_dir) / "edit_section_learning_report.json", mask_sensitive_data(report))
    return mask_sensitive_data(report)


__all__ = [
    "ALL_PHASES", "CAPTURE_SCHEMA", "EditSectionLearner", "EditSectionMemory", "SCHEMA", "SectionMemory", "action_summaries", "build_fields", "compare_requested",
    "input_json_from_fields", "learn_edit_sections", "map_fields_to_input", "phase_targets", "resolve_phases",
]
