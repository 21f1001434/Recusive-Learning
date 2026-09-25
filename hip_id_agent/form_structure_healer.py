"""Make the live form structurally able to hold every input.json value (V243R17).

Two things stop a correct value from ever reaching the portal even when the
agent knows exactly what to type:

* the field sits in a collapsed section (DDS accordion, ``<details>``) and is
  not rendered visible until its header is clicked;
* the field belongs to row N of a repeatable list and the portal shows fewer
  rows, so "+ Add ..." must be clicked first.

Both are repaired here, generically, from the goal itself: unresolved input
leaves and graph nodes say what is missing, the live DOM says where.  Every
click is effect-verified (the section is now expanded; the row count grew) and
nothing that could save, submit or delete is ever a candidate.  The steps taken
are returned as value-free evidence so they can be learned and replayed.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .security import mask_sensitive_data, mask_sensitive_string

_BLOCKED_WORDS = re.compile(r"\b(save|create|submit|delete|remove|deploy|publish|update|cancel|close|reset|next|previous|back)\b", re.I)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").replace("*", " ").replace(":", " ").split())


def _words(value: Any) -> set:
    return {w for w in _norm(value).split() if len(w) >= 3}


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _alias_words(values: Sequence[Any]) -> set:
    out: set = set()
    for value in values:
        for word in _words(value):
            out.add(word)
            out.add(_singular(word))
    return out


_COLLAPSED_JS = r"""
(root, arg) => {
  const clean = v => String(v || '').replace(/\s+/g, ' ').trim();
  function css(n){if(n.id)return `${n.tagName.toLowerCase()}#${CSS.escape(n.id)}`;const p=[];let x=n;while(x&&x.nodeType===1&&p.length<10){let t=x.tagName.toLowerCase();const par=x.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===x.tagName);if(same.length>1)t+=`:nth-of-type(${same.indexOf(x)+1})`;}p.unshift(t);x=par;}return p.join(' > ');}
  function visible(el){const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden');}
  const scope = root || document.body;
  const heads = Array.from(scope.querySelectorAll('button[aria-expanded="false"],[role=button][aria-expanded="false"],details:not([open]) > summary'));
  const out = [];
  for (const h of heads) {
    if (!visible(h)) continue;
    // Dropdowns, menus and tabs also carry aria-expanded; they are not sections.
    if (h.closest('dds-dropdown,[role=combobox],[role=listbox],[role=menu],[role=tablist],nav,header')) continue;
    const role = (h.getAttribute('role') || '').toLowerCase();
    if (role === 'combobox' || role === 'tab' || role === 'menuitem') continue;
    const popup = (h.getAttribute('aria-haspopup') || '').toLowerCase();
    if (popup && popup !== 'false') continue;
    const title = clean(h.innerText || h.textContent || h.getAttribute('aria-label'));
    if (!title || title.length > 120 || /\b(save|submit|delete|remove|deploy|publish)\b/i.test(title)) continue;
    let panel = null;
    const controls = h.getAttribute('aria-controls');
    if (controls) panel = document.getElementById(controls);
    if (!panel && h.tagName === 'SUMMARY') panel = h.parentElement;
    if (panel && !scope.contains(panel)) continue;
    out.push({selector: css(h), title, has_panel: !!panel});
  }
  return out.slice(0, 20);
}
"""

_EXPANDED_JS = r"""
(el) => {
  if (el.tagName === 'SUMMARY') return !!(el.parentElement && el.parentElement.open);
  if (el.getAttribute('aria-expanded') === 'true') return true;
  const id = el.getAttribute('aria-controls');
  const panel = id && document.getElementById(id);
  if (!panel) return false;
  const r = panel.getBoundingClientRect();
  return !panel.hidden && r.width > 0 && r.height > 0;
}
"""


async def _active_root(page: Any, phase: str) -> Any:
    try:
        from .dds_control_driver import get_active_form_root
        return await get_active_form_root(page, phase)
    except Exception:
        return None


async def _click(page: Any, selector: str, *, label: str) -> Dict[str, Any]:
    session = getattr(page, "_hip_browser_session", None)
    loc = page.locator(selector).first
    try:
        await loc.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    try:
        if session is not None and hasattr(session, "click_and_wait"):
            await session.click_and_wait(action=label, locator=loc, selector=selector, mutation_risk=False)
            return {"clicked": True, "executor": "browser-session-broker"}
        await loc.click(timeout=3000)
        return {"clicked": True, "executor": "python-playwright-offline"}
    except Exception as exc:
        return {"clicked": False, "error": mask_sensitive_string(str(exc))[:300]}


async def reveal_collapsed_sections(
    page: Any, phase: str, *, wanted: Sequence[str] = (), expand_unmatched: bool = True, max_clicks: int = 6,
) -> Dict[str, Any]:
    """Expand collapsed sections whose title matches ``wanted`` (else all, bounded).

    Expanding a section changes what is rendered, never a value, so when the
    missing fields' section cannot be named every collapsed section of the
    active form is opened once.
    """
    audit: Dict[str, Any] = {"schema_version": "hip.collapsed-section-reveal.v1", "phase": phase, "expanded": [], "candidates": []}
    root = await _active_root(page, phase)
    try:
        if root is not None:
            heads = await root.evaluate(_COLLAPSED_JS, {})
        else:
            heads = await page.evaluate("(arg) => (" + _COLLAPSED_JS + ")(document.body, arg)", {})
    except Exception as exc:
        audit["error"] = mask_sensitive_string(str(exc))[:300]
        return audit
    heads = [h for h in (heads or []) if isinstance(h, dict) and h.get("selector")]
    wanted_words = _alias_words(wanted)
    ranked: List[Tuple[int, Dict[str, Any]]] = []
    for head in heads:
        overlap = len(_alias_words([head.get("title")]) & wanted_words)
        ranked.append((overlap, head))
        audit["candidates"].append({"title": head.get("title"), "match_words": overlap})
    matching = [h for score, h in sorted(ranked, key=lambda x: -x[0]) if score > 0]
    targets = matching or ([h for _, h in ranked] if expand_unmatched else [])
    for head in targets[:max_clicks]:
        result = await _click(page, str(head["selector"]), label=f"Expand section {head.get('title')}")
        try:
            await page.wait_for_timeout(250)
            expanded = bool(await page.locator(str(head["selector"])).first.evaluate(_EXPANDED_JS))
        except Exception:
            expanded = False
        audit["expanded"].append({"title": head.get("title"), "matched_input": head in matching, "expanded": expanded, **result})
    audit["expanded_count"] = sum(1 for e in audit["expanded"] if e.get("expanded"))
    return mask_sensitive_data(audit)


_ADD_BUTTON_JS = r"""
(root, arg) => {
  const clean = v => String(v || '').replace(/\s+/g, ' ').trim();
  function css(n){if(n.id)return `${n.tagName.toLowerCase()}#${CSS.escape(n.id)}`;const p=[];let x=n;while(x&&x.nodeType===1&&p.length<10){let t=x.tagName.toLowerCase();const par=x.parentElement;if(par){const same=Array.from(par.children).filter(y=>y.tagName===x.tagName);if(same.length>1)t+=`:nth-of-type(${same.indexOf(x)+1})`;}p.unshift(t);x=par;}return p.join(' > ');}
  function visible(el){const r=el.getBoundingClientRect();const s=getComputedStyle(el);return !!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden');}
  const bad = new RegExp(arg.bad, 'i');
  function addLike(el){
    if (!visible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') return null;
    const text = clean(el.innerText || el.textContent);
    const aria = clean(el.getAttribute('aria-label') || el.getAttribute('title'));
    const label = clean(`${text} ${aria}`);
    if (bad.test(label)) return null;
    const icon = !!el.querySelector('[class*=plus],[name*=plus],use[href*=plus],[class*=add-circle]');
    const isAdd = /^\+?\s*add\b/i.test(text) || /^\+$/.test(text) || /^\+?\s*add\b/i.test(aria) || /\bplus\b/i.test(aria) || (icon && text.length <= 1);
    if (!isAdd) return null;
    const r = el.getBoundingClientRect();
    if (r.width > 320 || r.height > 90) return null;
    return {label: label || '+'};
  }
  const scope = root || document.body;
  const buttons = Array.from(scope.querySelectorAll('button,a,[role=button]'));
  const anchors = (arg.anchors || []).map(s => { try { return document.querySelector(s); } catch (e) { return null; } }).filter(Boolean);
  const words = (arg.words || []).map(w => String(w).toLowerCase());
  const contextWords = (arg.context_words || []).map(w => String(w).toLowerCase());
  const cands = [];
  for (const b of buttons) {
    const info = addLike(b);
    if (!info) continue;
    let level = 99;
    if (anchors.length) {
      // Smallest ancestor of the rows that also holds this button.
      let n = anchors[anchors.length - 1];
      for (let d = 0; n && d < 12; d++, n = n.parentElement) {
        if (n.contains(b)) { level = d; break; }
      }
      if (level === 99) continue;
    }
    let ctx = b.parentElement;
    for (let i = 0; i < 3 && ctx && clean(ctx.innerText).length < 20; i++) ctx = ctx.parentElement;
    const ctxText = clean(((ctx && ctx.innerText) || '')).toLowerCase().slice(0, 1500);
    const lab = info.label.toLowerCase();
    const labelHits = words.filter(w => lab.includes(w)).length;
    const wordHits = labelHits * 3 + words.concat(contextWords).filter(w => ctxText.includes(w)).length;
    // Without rows on screen to anchor to, only a button that names this
    // list ("+ Add Tag") may be clicked, never any "+ Add" that happens to be near.
    if (!anchors.length && !labelHits) continue;
    // Prefer the button right after the last row in document order.
    const after = anchors.length ? !!(anchors[anchors.length - 1].compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) : true;
    cands.push({selector: css(b), label: info.label, level, word_hits: wordHits, after});
  }
  cands.sort((a, b) => (a.level - b.level) || (b.word_hits - a.word_hits) || (Number(b.after) - Number(a.after)));
  return cands.slice(0, 5);
}
"""


def _control_row_ordinal(control: Dict[str, Any]) -> Optional[int]:
    for key in ("expected_row_index", "row_kind_ordinal", "row_index", "label_occurrence"):
        value = control.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _control_matches_names(control: Dict[str, Any], names: set) -> bool:
    keys = {
        _norm(control.get(k)) for k in ("label", "placeholder", "name", "form_control_name", "framework_key", "semantic_key", "group_label")
    }
    keys = {k for k in keys if k}
    compact_keys = {k.replace(" ", "") for k in keys}
    return bool((keys & names) or (compact_keys & {n.replace(" ", "") for n in names}))


def plan_row_groups(
    graph: Dict[str, Any], unresolved_leaves: Sequence[Dict[str, Any]], input_data: Dict[str, Any], *, section: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Row groups whose input needs more rows than may be on screen."""
    groups: Dict[str, Dict[str, Any]] = {}
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or node.get("row_index") is None or not node.get("row_kind"):
            continue
        if section and _norm(section) not in _norm(node.get("section")) and _norm(node.get("section")) not in _norm(section):
            continue
        key = f"kind:{_norm(node.get('row_kind'))}"
        g = groups.setdefault(key, {"group": key, "row_kind": node.get("row_kind"), "needed": 0, "names": set(), "aliases": set(), "context_aliases": set(), "section": node.get("section")})
        g["needed"] = max(g["needed"], int(node["row_index"]) + 1)
        loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), dict) else {}
        for value in list(loc.get("labels") or []) + list(loc.get("placeholders") or []) + list(loc.get("names") or []):
            if _norm(value):
                g["names"].add(_norm(value))
        g["aliases"].update(_alias_words([node.get("row_kind")]))
        g["context_aliases"].update(_alias_words([node.get("section")]))
    for leaf in unresolved_leaves or []:
        path = str(leaf.get("input_path") or "")
        match = re.match(r"^(.*)\[(\d+)\]\.([^.\[\]]+)$", path)
        if not match:
            continue
        list_path, index, key = match.group(1), int(match.group(2)), match.group(3)
        gkey = f"list:{list_path}"
        g = groups.setdefault(gkey, {"group": gkey, "row_kind": "", "needed": 0, "names": set(), "aliases": set(), "context_aliases": set(), "section": ""})
        g["needed"] = max(g["needed"], index + 1)
        g["names"].add(_norm(key))
        g["aliases"].update(_alias_words([list_path.rsplit(".", 1)[-1]]))
    out = []
    for g in groups.values():
        g["names"] = sorted(g["names"])
        g["aliases"] = sorted(g["aliases"])
        g["context_aliases"] = sorted(g["context_aliases"])
        out.append(g)
    return out


def _live_rows(controls: Sequence[Dict[str, Any]], group: Dict[str, Any]) -> Tuple[int, List[Dict[str, Any]]]:
    names = set(group.get("names") or [])
    kind = _norm(group.get("row_kind"))
    members = [
        c for c in controls if isinstance(c, dict)
        and (not kind or not _norm(c.get("row_kind")) or _norm(c.get("row_kind")) == kind)
        and _control_matches_names(c, names)
    ]
    ordinals = {o for o in (_control_row_ordinal(c) for c in members) if o is not None}
    count = (max(ordinals) + 1) if ordinals else (1 if members else 0)
    return count, members


async def ensure_repeatable_rows(
    page: Any, phase: str, groups: Sequence[Dict[str, Any]], *, capture: Any, max_clicks_per_group: int = 12,
) -> Dict[str, Any]:
    """Click the rows' own "+ Add" until each group has the rows input.json needs."""
    audit: Dict[str, Any] = {"schema_version": "hip.repeatable-row-heal.v1", "phase": phase, "groups": []}
    root = await _active_root(page, phase)
    for group in groups:
        controls = await capture()
        live, members = _live_rows(controls, group)
        entry: Dict[str, Any] = {
            "group": group.get("group"), "row_kind": group.get("row_kind"), "needed_rows": group.get("needed"),
            "live_rows_before": live, "clicks": [],
        }
        if live >= int(group.get("needed") or 0):
            entry["status"] = "enough_rows"
            audit["groups"].append(entry)
            continue
        for _ in range(max_clicks_per_group):
            anchors = [str(c.get("selector") or "") for c in members if c.get("selector")]
            try:
                arg = {
                    "anchors": anchors[-3:], "words": list(group.get("aliases") or []),
                    "context_words": list(group.get("context_aliases") or []), "bad": _BLOCKED_WORDS.pattern,
                }
                if root is not None:
                    cands = await root.evaluate(_ADD_BUTTON_JS, arg)
                else:
                    cands = await page.evaluate("(arg) => (" + _ADD_BUTTON_JS + ")(document.body, arg)", arg)
            except Exception as exc:
                entry["error"] = mask_sensitive_string(str(exc))[:300]
                break
            cands = [c for c in (cands or []) if isinstance(c, dict) and c.get("selector")]
            if not cands:
                entry["status"] = "no_add_control_found"
                break
            best = cands[0]
            click = await _click(page, str(best["selector"]), label=f"Add row ({best.get('label')})")
            await page.wait_for_timeout(450)
            controls = await capture()
            after, members_after = _live_rows(controls, group)
            entry["clicks"].append({"label": best.get("label"), "rows_before": live, "rows_after": after, **click})
            if after <= live:
                # The wait is generous; a click that added nothing is not repeated.
                await page.wait_for_timeout(600)
                controls = await capture()
                after, members_after = _live_rows(controls, group)
                entry["clicks"][-1]["rows_after_wait"] = after
                if after <= live:
                    entry["status"] = "add_had_no_row_effect"
                    break
            live, members = after, members_after
            if live >= int(group.get("needed") or 0):
                entry["status"] = "rows_created"
                break
        entry["live_rows_after"] = live
        entry.setdefault("status", "incomplete")
        audit["groups"].append(entry)
    audit["rows_added"] = sum(
        max(0, int(g.get("live_rows_after") or 0) - int(g.get("live_rows_before") or 0)) for g in audit["groups"]
    )
    return mask_sensitive_data(audit)
