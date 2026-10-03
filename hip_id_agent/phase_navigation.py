"""How a phase's form is reached and moved through, and what it offers (V243R39).

The field skill (``portal_skills``) says how each input.json value is filled.
It does not say how the form is *reached* or *moved through*: BizFlow's
"+ Add" opens a flow-template picker, the template's link opens the Create
Biz Flow wizard, and the wizard only moves forward with its own Next once a
tab's required fields are filled; its last tab offers a routing table with
"+ Add", column menus, row actions, Previous and Submit.

This module keeps that knowledge per phase:

* ``entry`` -- the clicks from the listing to the form (e.g. "+ Add",
  "template link B2B-Flow-PubSub-Template");
* ``tabs`` -- the tab order, how each tab is left (``Next``), the buttons of
  its bottom bar and the required fields the portal named when Next refused;
* ``options`` -- per tab, every button, menu and the items of each menu, read
  without acting (menus are opened, read and closed); commit buttons (Submit,
  Save, ...) are recorded as such and never clicked.

It is written to ``<memory>/phase_navigation/<phase>.json`` after a phase
whose form was completed, read by the deterministic script (so the script
says "click the template link", "click Next") and listed by the API.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .safe_io import safe_write_json

SCHEMA = "hip.phase-navigation.v1"
COMMIT_WORDS = re.compile(r"^(‹\s*)?(submit|save|save changes|create|update|deploy|delete|publish|confirm|finish)\b", re.I)
# A row's own "+" ("Create Condition", "Create Attribute", ...) only adds a row on the form.
ROW_TOOLS = re.compile(r"^create\s+(condition|attribute|process step|file name part|row|identifier)s?\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _file(memory_dir: Path, phase: str) -> Path:
    return Path(memory_dir) / "phase_navigation" / f"{re.sub(r'[^a-z0-9_]+', '_', str(phase).lower())}.json"


def load(memory_dir: Path, phase: str) -> Dict[str, Any]:
    try:
        data = json.loads(_file(memory_dir, phase).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_commit(label: str) -> bool:
    """A button that commits to the portal (never clicked while learning); a row's own +/- is not one."""
    text = str(label or "").strip()
    return bool(COMMIT_WORDS.search(text)) and not ROW_TOOLS.search(text)


def record(memory_dir: Path, phase: str, navigation: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge one run's navigation into the phase's knowledge (newest wins per tab)."""
    known = load(memory_dir, phase)
    tabs_by_name: Dict[str, Dict[str, Any]] = {str(t.get("tab")): dict(t) for t in known.get("tabs") or [] if isinstance(t, dict)}
    order: List[str] = [str(t.get("tab")) for t in known.get("tabs") or [] if isinstance(t, dict)]
    for tab in navigation.get("tabs") or []:
        if not isinstance(tab, Mapping) or not tab.get("tab"):
            continue
        name = str(tab["tab"])
        merged = {**tabs_by_name.get(name, {}), **{k: v for k, v in tab.items() if v not in (None, "", [], {})}}
        tabs_by_name[name] = merged
        if name not in order:
            order.append(name)
    if navigation.get("tabs"):
        # The run's own order is the portal's order.
        run_order = [str(t.get("tab")) for t in navigation.get("tabs") or [] if isinstance(t, Mapping) and t.get("tab")]
        order = run_order + [n for n in order if n not in run_order]
    options = dict(known.get("options") or {})
    for tab, opts in (navigation.get("options") or {}).items():
        if isinstance(opts, Mapping):
            options[str(tab)] = dict(opts)
    data = {
        "schema_version": SCHEMA, "phase": phase,
        "entry": list(navigation.get("entry") or known.get("entry") or []),
        "tabs": [tabs_by_name[n] for n in order if n in tabs_by_name],
        "options": options,
        "wizard": bool(navigation.get("wizard", known.get("wizard", False))),
        "commit_buttons_never_clicked": sorted({*known.get("commit_buttons_never_clicked", []),
                                                *navigation.get("commit_buttons_never_clicked", [])}),
        "learned_at": known.get("learned_at") or _now(), "updated_at": _now(),
        "runs": int(known.get("runs") or 0) + 1, "last_run_id": str(navigation.get("run_id") or ""),
    }
    safe_write_json(_file(memory_dir, phase), data, mask=False)
    return data


def script_steps(navigation: Mapping[str, Any]) -> Dict[str, Any]:
    """The navigation part of a deterministic script: entry clicks and per-tab moves."""
    entry = [str(x) for x in navigation.get("entry") or [] if str(x).strip()]
    tabs = [t for t in navigation.get("tabs") or [] if isinstance(t, Mapping) and t.get("tab")]
    return {"entry": entry, "tabs": tabs, "wizard": bool(navigation.get("wizard")),
            "options": dict(navigation.get("options") or {})}


def summaries(memory_dir: Path, phases: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    root = Path(memory_dir) / "phase_navigation"
    rows: List[Dict[str, Any]] = []
    names = list(phases) if phases else sorted(p.stem for p in root.glob("*.json")) if root.exists() else []
    for phase in names:
        data = load(memory_dir, phase)
        if not data:
            rows.append({"phase": phase, "known": False})
            continue
        menus = sum(len((o or {}).get("menus") or {}) for o in (data.get("options") or {}).values() if isinstance(o, Mapping))
        rows.append({"phase": phase, "known": True, "entry": data.get("entry"), "wizard": data.get("wizard"),
                     "tabs": [t.get("tab") for t in data.get("tabs") or []],
                     "advance": {t.get("tab"): t.get("advance") for t in data.get("tabs") or []},
                     "menus_learned": menus, "commit_buttons_never_clicked": data.get("commit_buttons_never_clicked"),
                     "runs": data.get("runs"), "updated_at": data.get("updated_at")})
    return {"schema_version": SCHEMA, "phases": rows}


__all__ = ["SCHEMA", "load", "record", "summaries", "script_steps", "is_commit"]
