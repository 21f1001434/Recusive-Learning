"""The deterministic script of a phase, as a person can read it (V243R38).

What the agent learns from a completed phase is a *skill* (``portal_skills``):
for every input.json path, how the form field is bound and filled -- value-free.
The next run replays it without models and checks every value; a replay that
reproduces the fill exactly *certifies* it.  That is the deterministic script.
It always existed, but only as internal bindings, so after a successful phase
nobody could tell whether a script had been generated, where, or whether it
would be used.

This module renders it after every completed phase:

* ``<run>/<phase>/deterministic_script.json`` and ``.md`` -- the script of this run;
* ``<memory>/deterministic_scripts/<phase>.json`` and ``.md`` -- the current
  script of the phase, and ``index.json`` for the Control Center and the API.

Each step names the action, the field (and row) and the input.json path its
value comes from.  No values, selectors or coordinates are written.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .safe_io import safe_write_json, safe_write_text
from .security import mask_sensitive_string

SCHEMA = "hip.deterministic-script.v1"
INDEX_SCHEMA = "hip.deterministic-script-index.v1"

PHASE_DISPLAY = {
    "data_map": "Data Map", "source_document_type": "Source Document Type", "target_document_type": "Target Document Type",
    "rule": "Rule", "source_transport_profile": "Source Transport Profile",
    "target_transport_profile": "Target Transport Profile", "biz_flow": "BizFlow",
}
VERBS = {
    "fill_text": "Type", "select_single": "Select", "select_multi": "Tick each of", "select_radio": "Choose",
    "toggle": "Set the switch", "select_checkbox_group": "Tick", "upload_file": "Upload the file from",
    "verify_only": "Check (portal-owned)",
}
STATUS_TEXT = {
    "certified": "certified — a later run replayed it and every value matched; runs replay it without models",
    "candidate": "learned in this run — the next run replays it without models; it is certified when that replay "
                 "reproduces every value",
    "stale": "stale — the form changed since it was learned; the next run learns it again",
    "none": "not generated — the phase did not record a learned skill (it ran without the skill layer)",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _humanize(key: str) -> str:
    text = re.sub(r"[_\-]+", " ", str(key or "")).strip()
    return text[:1].upper() + text[1:] if text else ""


def _rel(input_path: str, phase: str) -> str:
    raw = str(input_path or "")
    for prefix in (f"$.objects.{phase}.", f"objects.{phase}."):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return raw[2:] if raw.startswith("$.") else raw


def _best_of(skills: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rank = {"certified": 0, "candidate": 1, "stale": 2}
    best = min(rank.get(str(s.get("status")), 3) for s in skills)
    tier = [s for s in skills if rank.get(str(s.get("status")), 3) == best]
    tier.sort(key=lambda s: str(s.get("certified_at") or s.get("learned_at") or ""), reverse=True)
    return dict(tier[0])


def _pick_skill(data: Mapping[str, Any], operation: str = "create") -> Dict[str, Any]:
    """The phase's current skill: the operation's certified one, else its newest candidate.

    V243R39: a wizard phase (BizFlow) learns one skill per tab (its ``scope``);
    the phase's skill is all of them together -- certified only when every tab's is.
    """
    skills = [s for s in (data.get("skills") or {}).values() if isinstance(s, dict)]
    same_op = [s for s in skills if s.get("operation") == operation] or skills
    if not same_op:
        return {}
    by_scope: Dict[str, List[Mapping[str, Any]]] = {}
    for skill in same_op:
        by_scope.setdefault(str(skill.get("scope") or "form"), []).append(skill)
    picks = [_best_of(group) for group in by_scope.values()]
    if len(picks) == 1:
        return picks[0]
    rank = {"certified": 0, "candidate": 1, "stale": 2}
    weakest = max(picks, key=lambda s: rank.get(str(s.get("status")), 3))
    merged = dict(max(picks, key=lambda s: str(s.get("certified_at") or s.get("learned_at") or "")))
    merged["bindings"] = {k: v for pick in picks for k, v in (pick.get("bindings") or {}).items()}
    rows: Dict[str, Any] = {}
    for pick in picks:
        rows.update(((pick.get("structure") or {}).get("rows") or {}) if isinstance(pick.get("structure"), dict) else {})
    merged["structure"] = {"rows": rows}
    merged["status"] = str(weakest.get("status") or "candidate")
    merged["scopes"] = sorted(by_scope)
    merged["skill_id"] = "+".join(sorted(str(p.get("skill_id") or "") for p in picks))[:120]
    merged["learned_at"] = max(str(p.get("learned_at") or "") for p in picks) or None
    merged["certified_at"] = (max(str(p.get("certified_at") or "") for p in picks) or None) if merged["status"] == "certified" else None
    stats = [p.get("stats") or {} for p in picks]
    merged["stats"] = {"replays": min(int(x.get("replays") or 0) for x in stats),
                       "replay_failures": sum(int(x.get("replay_failures") or 0) for x in stats),
                       "learn_seconds": round(sum(float(x.get("learn_seconds") or 0) for x in stats), 2) or None,
                       "last_replay_seconds": round(sum(float(x.get("last_replay_seconds") or 0) for x in stats), 2) or None}
    return merged


def _list_name(list_path: str) -> str:
    """V243R39: "configure_routing.conditions.rows" -> "Configure routing · Conditions"."""
    parts = [p for p in str(list_path or "").split(".") if p]
    if parts and parts[-1].lower() in {"rows", "items", "list", "entries"}:
        parts = parts[:-1]
    if len(parts) >= 2:
        return f"{_humanize(parts[0])} · {_humanize(parts[-1])}"
    return _humanize(parts[-1]) if parts else "Rows"


def _add_label_for(list_path: str, rows: Mapping[str, Any]) -> str:
    """The "+" learned for this list's row kind (e.g. "flow identifier" -> flow_identifiers.conditions)."""
    tokens = {t for t in re.split(r"[^a-z]+", str(list_path or "").lower()) if t}
    tokens |= {t.rstrip("s") for t in tokens}
    for key, value in (rows or {}).items():
        if not isinstance(value, Mapping) or not value.get("add_label"):
            continue
        words = [w.rstrip("s") for w in re.split(r"[^a-z]+", str(key).split(":", 1)[-1].lower()) if w]
        if words and all(w in tokens for w in words):
            return str(value.get("add_label"))
    return "+"


def _norm_section(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _options_text(tab: str, options: Mapping[str, Any]) -> str:
    parts: List[str] = []
    menus = options.get("menus") if isinstance(options.get("menus"), Mapping) else {}
    for name, menu in menus.items():
        items = [str(i) for i in (menu or {}).get("items") or []] if isinstance(menu, Mapping) else []
        if items:
            parts.append(f"{name} → {', '.join(items)}")
    labels = []
    for b in options.get("buttons") or []:
        if isinstance(b, Mapping) and b.get("label") and not b.get("pager") and str(b.get("label")) not in labels:
            labels.append(str(b.get("label")))
    commits = [str(c) for c in options.get("commit_buttons") or []]
    text = f"Learned what “{tab}” offers (read-only): buttons {', '.join(labels[:14]) or '—'}"
    if parts:
        text += "; menus " + "; ".join(parts[:8])
    if commits:
        text += f"; never clicked: {', '.join(commits)}"
    return text


def build_script(
    *, phase: str, skill_data: Mapping[str, Any], graph: Optional[Mapping[str, Any]] = None, url: str = "",
    run_id: str = "", save_after_fill: bool = False, navigation: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """The phase's deterministic script from its learned skill (and the phase state graph for names).

    V243R39: with the phase's navigation knowledge (``phase_navigation``) the
    script also says how the form is reached (BizFlow: + Add, then the
    template's link) and moved through (fill a tab, then Next), and what each
    tab offers.
    """
    skill = _pick_skill(skill_data)
    status = str(skill.get("status") or "none") if skill else "none"
    display = PHASE_DISPLAY.get(phase, _humanize(phase))
    nodes = [n for n in ((graph or {}).get("nodes") or []) if isinstance(n, dict)]
    by_path = {_rel(str(n.get("input_path") or ""), phase): n for n in nodes}
    bindings = skill.get("bindings") if isinstance(skill.get("bindings"), dict) else {}
    order = [p for p in by_path if p in bindings] + [p for p in bindings if p not in by_path]
    if not order:
        order = list(by_path)

    nav = dict(navigation or {})
    entry = [str(x) for x in nav.get("entry") or [] if str(x).strip()]
    steps: List[Dict[str, Any]] = [
        {"do": "open", "text": f"Open {url or 'the ' + display + ' listing'}", "target": url},
        {"do": "click", "text": (f"Click “+ Add” to open the Create {display.replace('Source ', '').replace('Target ', '')} form"
                                 if len(entry) <= 1 else "Click “+ Add”"), "target": "+ Add"},
    ]
    for item in entry[1:]:
        steps.append({"do": "click", "text": f"Click the {item} to open the form", "target": item})
    rows = ((skill.get("structure") or {}).get("rows") or {}) if isinstance(skill.get("structure"), dict) else {}
    row_counts: Dict[str, int] = {}
    for path in order:
        m = re.match(r"^(.*?)\[(\d+)\]", path)
        if m:
            row_counts[m.group(1)] = max(row_counts.get(m.group(1), 0), int(m.group(2)) + 1)
    for list_path, count in row_counts.items():
        if count <= 1:
            continue
        add_label = _add_label_for(list_path, rows)
        section = _list_name(list_path)
        steps.append({"do": "add_rows", "text": f"Add rows in “{section}” until there is one per input.json item "
                                                f"({count} in this run) with “{add_label}”",
                      "section": section, "rows_from": f"input.json → {list_path}", "add_label": add_label})
    tabs = [t for t in nav.get("tabs") or [] if isinstance(t, Mapping) and t.get("tab")]
    if tabs:
        # Fields grouped by the tab they are on, in the portal's tab order.
        def tab_of(path: str) -> int:
            section = _norm_section((by_path.get(path) or {}).get("section"))
            for i, t in enumerate(tabs):
                if section and (section == _norm_section(t.get("tab")) or section == _norm_section(t.get("portal_tab"))):
                    return i
            return len(tabs)
        order = sorted(order, key=lambda p: (tab_of(p), order.index(p)))
    current_tab = -1
    options = nav.get("options") if isinstance(nav.get("options"), Mapping) else {}

    def close_tab(index: int) -> None:
        if index < 0 or index >= len(tabs):
            return
        tab = tabs[index]
        name = str(tab.get("tab"))
        for key in (name, f"{name} + Add"):
            if isinstance(options.get(key), Mapping) and not options[key].get("error"):
                steps.append({"do": "learn_options", "text": _options_text(key, options[key]), "tab": key, "read_only": True})
        if tab.get("advance"):
            nxt = str(tabs[index + 1].get("tab")) if index + 1 < len(tabs) else "the next tab"
            steps.append({"do": "next", "text": f"Click “{tab.get('advance')}” at the bottom — the wizard moves to “{nxt}” "
                                                f"(it refuses until this tab's required fields are filled)", "target": tab.get("advance")})

    for path in order:
        node = by_path.get(path) or {}
        if tabs:
            index = tab_of(path)
            if index != current_tab:
                close_tab(current_tab)
                current_tab = index
                if index < len(tabs):
                    steps.append({"do": "tab", "text": f"On the “{tabs[index].get('tab')}” tab:", "tab": tabs[index].get("tab")})
        binding = bindings.get(path) if isinstance(bindings.get(path), dict) else {}
        action = str(binding.get("action") or node.get("action") or "fill_text")
        field = _humanize(str(node.get("field_key") or path.split(".")[-1]).replace("[*]", ""))
        row = node.get("row_index")
        if row is None:
            m = re.search(r"\[(\d+)\]", path)
            row = int(m.group(1)) if m else None
        where = f" (row {int(row) + 1})" if row is not None else ""
        section = str(node.get("section") or "")
        verb = VERBS.get(action, _humanize(action))
        text = (f"{verb} {field}{where}: must equal input.json → {path}" if action == "verify_only"
                else f"{verb} input.json → {path} in “{field}”{where}")
        steps.append({"do": action, "text": text, "field": field, "section": section, "row": row,
                      "value_from": f"input.json → objects.{phase}.{path}", "learned_binding": bool(binding)})
    if tabs:
        close_tab(current_tab)
        for index in range(max(current_tab + 1, 0), len(tabs)):
            close_tab(index)
    steps.append({"do": "verify_all", "text": "Read every field back; each must equal its input.json value exactly"})
    steps.append({"do": "save" if save_after_fill else "stop",
                  "text": ("Click Save once (governed save after the exact proof), then check the listing"
                           if save_after_fill else "Stop — nothing is saved (Submit/Save/Create are never clicked)")})
    for n, step in enumerate(steps, 1):
        step["n"] = n
    stats = skill.get("stats") if isinstance(skill.get("stats"), dict) else {}
    return {
        "schema_version": SCHEMA, "phase": phase, "phase_display": display, "url": url, "run_id": run_id,
        "generated_at": _now(), "operation": str(skill.get("operation") or "create"),
        "status": status, "status_text": STATUS_TEXT.get(status, status),
        "skill_id": str(skill.get("skill_id") or ""), "learned_at": skill.get("learned_at"),
        "certified_at": skill.get("certified_at"), "replays": int(stats.get("replays") or 0),
        "replay_failures": int(stats.get("replay_failures") or 0),
        "learn_seconds": stats.get("learn_seconds"), "last_replay_seconds": stats.get("last_replay_seconds"),
        "field_steps": len(order), "step_count": len(steps), "steps": steps,
        "navigation": {"entry": entry, "tabs": [t.get("tab") for t in tabs], "wizard": bool(nav.get("wizard"))} if nav else {},
        "values_stored": False, "selectors_stored": False, "coordinates_stored": False,
    }


def render_markdown(script: Mapping[str, Any]) -> str:
    lines = [
        f"# Deterministic script — {script.get('phase_display')}",
        "",
        f"- **Status:** {script.get('status_text')}",
        f"- **Steps:** {script.get('step_count')} ({script.get('field_steps')} fields)",
        f"- **Learned:** {script.get('learned_at') or '—'}" + (f" · **Certified:** {script.get('certified_at')}" if script.get("certified_at") else ""),
        f"- **Replays:** {script.get('replays', 0)} (failed: {script.get('replay_failures', 0)})",
        f"- **Run:** {script.get('run_id') or '—'} · generated {script.get('generated_at')}",
        "",
        "Values always come from the current input.json; no value, selector or screen position is stored. "
        "Before each step the field is found again on the live form, and after it the value is read back.",
        "",
    ]
    for step in script.get("steps") or []:
        lines.append(f"{step.get('n')}. {step.get('text')}")
    return "\n".join(lines) + "\n"


def _skill_data(memory_dir: Path, phase: str) -> Dict[str, Any]:
    path = Path(memory_dir) / "portal_skills" / f"{re.sub(r'[^a-z0-9_]+', '_', phase.lower())}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_phase_script(
    *, phase: str, phase_dir: Path, memory_dir: Path, graph: Optional[Mapping[str, Any]] = None, url: str = "",
    run_id: str = "", save_after_fill: bool = False,
) -> Dict[str, Any]:
    """Render and store the phase's script; returns a summary for the chat and the report."""
    try:
        from .phase_navigation import load as _load_navigation

        navigation = _load_navigation(memory_dir, phase)
    except Exception:
        navigation = {}
    script = build_script(phase=phase, skill_data=_skill_data(memory_dir, phase), graph=graph, url=url,
                          run_id=run_id, save_after_fill=save_after_fill, navigation=navigation)
    markdown = render_markdown(script)
    phase_dir = Path(phase_dir)
    safe_write_json(phase_dir / "deterministic_script.json", script, mask=False)
    safe_write_text(phase_dir / "deterministic_script.md", markdown)
    store = Path(memory_dir) / "deterministic_scripts"
    if script["status"] != "none":
        safe_write_json(store / f"{phase}.json", script, mask=False)
        safe_write_text(store / f"{phase}.md", markdown)
    index = read_index(memory_dir)
    if script["status"] != "none":
        index["phases"][phase] = {
            "phase_display": script["phase_display"], "status": script["status"], "status_text": script["status_text"],
            "steps": script["step_count"], "fields": script["field_steps"], "replays": script["replays"],
            "learned_at": script["learned_at"], "certified_at": script["certified_at"], "run_id": run_id,
            "updated_at": script["generated_at"], "file": str(store / f"{phase}.md"),
            "run_file": str(phase_dir / "deterministic_script.md"),
        }
        index["updated_at"] = _now()
        safe_write_json(store / "index.json", index, mask=False)
    return {
        "phase": phase, "status": script["status"], "status_text": script["status_text"], "steps": script["step_count"],
        "fields": script["field_steps"], "file": str(phase_dir / "deterministic_script.md"),
        "memory_file": str(store / f"{phase}.md") if script["status"] != "none" else "",
    }


def read_index(memory_dir: Path) -> Dict[str, Any]:
    try:
        data = json.loads((Path(memory_dir) / "deterministic_scripts" / "index.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("phases"), dict):
            return data
    except Exception:
        pass
    return {"schema_version": INDEX_SCHEMA, "phases": {}}


def list_scripts(memory_dir: Path) -> Dict[str, Any]:
    """Every phase's current script (status, steps, markdown) for the API and the Control Center."""
    index = read_index(memory_dir)
    store = Path(memory_dir) / "deterministic_scripts"
    rows: List[Dict[str, Any]] = []
    for phase, row in index["phases"].items():
        # The skill may have been certified (or gone stale) since the script was written.
        skill = _pick_skill(_skill_data(memory_dir, phase))
        status = str(skill.get("status") or row.get("status") or "none") if skill else str(row.get("status") or "none")
        try:
            markdown = (store / f"{phase}.md").read_text(encoding="utf-8")
        except Exception:
            markdown = ""
        if skill and status != row.get("status"):
            markdown = re.sub(r"^- \*\*Status:\*\* .*$", f"- **Status:** {STATUS_TEXT.get(status, status)}", markdown, count=1, flags=re.M)
        rows.append({**row, "phase": phase, "status": status, "status_text": STATUS_TEXT.get(status, status),
                     "replays": int(((skill.get("stats") or {}) if skill else {}).get("replays") or row.get("replays") or 0),
                     "markdown": markdown})
    order = list(PHASE_DISPLAY)
    rows.sort(key=lambda r: order.index(r["phase"]) if r["phase"] in order else 99)
    return {"schema_version": INDEX_SCHEMA, "scripts": rows, "count": len(rows),
            "certified": sum(1 for r in rows if r["status"] == "certified"),
            "candidates": sum(1 for r in rows if r["status"] == "candidate"),
            "store": str(store)}


def chat_line(summary: Mapping[str, Any], display: str) -> str:
    status = str(summary.get("status") or "none")
    if status == "none":
        return f"📜 No deterministic script was recorded for {display} (the skill layer was off for this phase)."
    head = {"certified": "certified ✔", "candidate": "saved (candidate)", "stale": "marked stale"}.get(status, status)
    tail = {
        "certified": "it replays without models",
        "candidate": "the next run replays it without models and certifies it when every value matches",
        "stale": "the next run learns it again",
    }.get(status, "")
    return (f"📜 Deterministic script for {display} {head}: {summary.get('steps')} steps "
            f"({summary.get('fields')} fields) — {tail}. File: {mask_sensitive_string(str(summary.get('file') or ''))}")


__all__ = ["build_script", "render_markdown", "write_phase_script", "list_scripts", "read_index", "chat_line",
           "SCHEMA", "STATUS_TEXT"]
