"""Every phase learns Create, Edit, Clone, Migrate and Deploy (V243R39).

A mission creates each phase's object (the form filled from input.json; its
deterministic script is the *Create* knowledge).  Until now the other
operations were learned only when someone ran ``learn-action-sections``.  Now,
at the end of every mission, each mission phase also learns -- read-only --
where its Edit, Clone, Migrate and Deploy are and what they open:

* the listing, the object's row expander and the action button;
* Edit / Clone: the form, read completely (every tab, collapsed section and
  row) and closed with Cancel -- nothing is saved;
* Migrate / Deploy: per environment tab, the menu of target environments, the
  confirmation or the dialog -- nothing is ever confirmed.  A guarded Deploy
  button (one that may act at once) is opened only with the three-part
  mutation gate; otherwise the matrix says so, and the first authorized deploy
  learns it.

The object opened is input.json's when it is on the listing, else the
listing's first row (a new object exists only after an authorized save).

Each learned operation gets its own deterministic script
(``<memory>/deterministic_scripts/<phase>__<operation>.md``), and
``operation_matrix`` gives the phase x operation table the Control Center and
``GET /api/operation-matrix`` show.  Known operations are not learned again
unless their knowledge is older than ``refresh_days``.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .safe_io import safe_write_json, safe_write_text
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.operation-matrix.v1"
OPERATIONS = ("create", "edit", "clone", "migrate", "deploy")
LEARNED_OPERATIONS = ("edit", "clone", "migrate", "deploy")
PHASE_DISPLAY = {
    "data_map": "Data Map", "source_document_type": "Source Document Type", "target_document_type": "Target Document Type",
    "rule": "Rule", "source_transport_profile": "Source Transport Profile",
    "target_transport_profile": "Target Transport Profile", "biz_flow": "BizFlow",
}
_FALLBACK_STATUSES = {"needs_input", "no_expanded_details", "edit_form_not_visible", "no_row_on_listing", "error"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _age_days(stamp: Any) -> float:
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - then).total_seconds() / 86400.0
    except Exception:
        return 1e9


def _memory(memory_dir: Path, action: str) -> Any:
    from .edit_section_learning import EditSectionMemory, SectionMemory

    return EditSectionMemory(memory_dir) if action == "edit" else SectionMemory(memory_dir, action)


def _cell(memory_dir: Path, phase: str, action: str) -> Dict[str, Any]:
    """One phase x operation cell: known?, what it opens, how it is reached."""
    if action == "create":
        from .deterministic_script import _pick_skill, _skill_data
        from .phase_navigation import load as load_navigation

        skill = _pick_skill(_skill_data(memory_dir, phase))
        nav = load_navigation(memory_dir, phase)
        status = str(skill.get("status") or "") if skill else ""
        known = status in {"certified", "candidate"}
        path = list(nav.get("entry") or ["+ Add"])
        if nav.get("wizard"):
            path.append("fill each tab, then Next")
        return {"known": known, "status": status or "unknown", "path": path,
                "detail": (f"{len((skill or {}).get('bindings') or {})} fields" if skill else "")
                          + (f"; tabs {', '.join(t.get('tab') for t in nav.get('tabs') or [])}" if nav.get("tabs") else ""),
                "updated_at": (skill or {}).get("certified_at") or (skill or {}).get("learned_at") or nav.get("updated_at")}
    summary = _memory(memory_dir, action).summary(phase)
    if not summary.get("known"):
        return {"known": False, "status": "unknown"}
    if action in {"edit", "clone"}:
        detail = f"{summary.get('fields') or 0} fields"
        if summary.get("tabs"):
            detail += f"; tabs {', '.join(summary['tabs'])}"
        if summary.get("read_only"):
            detail += f"; read-only {', '.join(summary['read_only'][:4])}"
        if summary.get("commit_labels"):
            detail += f"; saves with {', '.join(summary['commit_labels'])} (never clicked while learning)"
    else:
        menus = summary.get("menus") or {}
        detail = "; ".join(f"{env} → {', '.join(items) or '—'}" for env, items in menus.items()) or str(summary.get("section_kind") or "")
        if summary.get("section_kind"):
            detail = f"{summary['section_kind']}: {detail}"
        opener = str((summary.get("path") or [""])[-1] or "")
        if opener and action not in opener.lower():
            # No Deploy button on this object: an environment is reached with its Migrate.
            detail = f"through “{opener.title()}” (no {action.title()} button) — {detail}"
    return {"known": True, "status": "learned", "path": summary.get("path") or ["expand row", action],
            "detail": detail, "updated_at": summary.get("updated_at"), "kind": summary.get("section_kind") or summary.get("kind")}


def operation_matrix(memory_dir: Path, phases: Optional[Sequence[str]] = None,
                     last_report: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The phase x operation table (Create / Edit / Clone / Migrate / Deploy)."""
    from .edit_section_learning import ALL_PHASES

    memory_dir = Path(memory_dir)
    order = list(PHASE_DISPLAY)
    wanted = list(phases) if phases else sorted(ALL_PHASES, key=lambda p: order.index(p) if p in order else 99)
    report = dict(last_report or read_last_report(memory_dir))
    outcomes = {(r.get("phase"), r.get("action")): r for r in report.get("rows") or [] if isinstance(r, Mapping)}
    rows: List[Dict[str, Any]] = []
    for phase in wanted:
        row: Dict[str, Any] = {"phase": phase, "phase_display": PHASE_DISPLAY.get(phase, phase)}
        for action in OPERATIONS:
            cell = _cell(memory_dir, phase, action)
            last = outcomes.get((phase, action))
            if last and not cell.get("known"):
                cell.update({"status": last.get("status") or "not learned", "reason": last.get("reason") or ""})
            row[action] = cell
        row["known"] = sum(1 for a in OPERATIONS if row[a].get("known"))
        rows.append(row)
    total = len(rows) * len(OPERATIONS)
    known = sum(r["known"] for r in rows)
    return {"schema_version": SCHEMA, "operations": list(OPERATIONS), "rows": rows, "known": known, "total": total,
            "generated_at": _now(), "last_run": {k: report.get(k) for k in ("run_id", "finished_at", "learned", "failed", "skipped")}}


def read_last_report(memory_dir: Path) -> Dict[str, Any]:
    import json

    try:
        data = json.loads((Path(memory_dir) / "operation_learning" / "last_report.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _script_text(phase: str, action: str, row: Mapping[str, Any], knowledge: Mapping[str, Any]) -> str:
    display = PHASE_DISPLAY.get(phase, phase)
    lines = [f"# Deterministic script — {display} · {action.title()}", "",
             f"- **Learned:** {knowledge.get('updated_at') or _now()} (read-only; nothing saved or confirmed)",
             f"- **Object opened while learning:** {row.get('target') or '—'} ({row.get('target_source') or 'input.json'})", "",
             "Values come from input.json (the object's name and, for Edit/Clone, the values to change).", ""]
    steps = [f"Open the {display} listing", "Search the object's name in “Table search”",
             "Click the row's expander (“Expand the row”)", "Pick the environment tab the object is in"]
    if action in {"edit", "clone"}:
        fields = knowledge.get("fields") or knowledge.get("field_count")
        steps.append(f"Click “{action.title()}” — the {action.title()} form opens"
                     + (f" ({fields} fields" + (f", tabs {', '.join(knowledge.get('tabs') or [])}" if knowledge.get("tabs") else "") + ")" if fields else ""))
        if knowledge.get("read_only"):
            steps.append(f"Read-only in {action.title()}: {', '.join(knowledge['read_only'])}")
        if action == "clone":
            steps.append("Type the new name from input.json (the source name is rejected as a duplicate)")
        steps.append("Change the fields input.json names; read each back")
        commits = knowledge.get("commit_labels") or []
        steps.append(f"Click “{commits[0] if commits else 'Save'}” only in an authorized run (three-part mutation gate); "
                     "then check the listing")
    else:
        menus = knowledge.get("menus") or {}
        kind = knowledge.get("section_kind") or knowledge.get("kind") or "menu"
        steps.append(f"Click “{action.title()}” — it opens a {kind}")
        for env, items in menus.items():
            steps.append(f"From {env}: choose one of {', '.join(items) or '—'}")
        steps.append(f"Confirm only in an authorized run (three-part mutation gate); then check the environment badges")
    lines += [f"{n}. {text}" for n, text in enumerate(steps, 1)]
    return "\n".join(lines) + "\n"


def _write_operation_script(memory_dir: Path, phase: str, action: str, row: Mapping[str, Any]) -> str:
    knowledge = _memory(memory_dir, action).summary(phase)
    if not knowledge.get("known"):
        return ""
    path = Path(memory_dir) / "deterministic_scripts" / f"{phase}__{action}.md"
    safe_write_text(path, _script_text(phase, action, row, knowledge))
    return str(path)


async def _fresh_listing(browser: Any, url: str) -> None:
    """A new document of the listing: whatever form the mission left open is discarded unsaved."""
    if not url:
        return
    page = getattr(browser, "page", None)
    try:
        if page is not None:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await browser.goto_base_and_complete_sso(url)
    except Exception:
        pass


def _say(chat: Any, text: str, *, kind: str = "learn", phase: str = "") -> None:
    try:
        if chat is not None:
            chat.post(text, kind=kind, phase=phase)
    except Exception:
        pass


async def learn_mission_operations(
    config: Any, browser: Any, input_data: Optional[Mapping[str, Any]], *, run_dir: Path, phases: Sequence[str],
    actions: Sequence[str] = LEARNED_OPERATIONS, refresh_days: float = 7.0, gate: Optional[Mapping[str, Any]] = None,
    chat: Any = None, run_id: str = "", deadline_seconds: float = 1800.0,
) -> Dict[str, Any]:
    """Learn each mission phase's Edit / Clone / Migrate / Deploy that is not known yet (read-only)."""
    from .edit_section_learning import EditSectionLearner
    from .environment_faults import is_environment_fatal

    memory_dir = Path(getattr(getattr(config, "reporting", None), "memory_dir", "data/hip_memory"))
    out_dir = Path(run_dir) / "operation_learning"
    learner = EditSectionLearner(config, browser, out_dir)
    started = time.monotonic()
    rows: List[Dict[str, Any]] = []
    stop_reason = ""
    _say(chat, "🧭 Now learning, for every phase of this mission, where Edit, Clone, Migrate and Deploy are and what "
               "they open — read-only: forms are read and closed with Cancel, menus are read, nothing is saved or confirmed.")
    for phase in phases:
        display = PHASE_DISPLAY.get(phase, phase)
        fresh = False
        for action in actions:
            memory = _memory(memory_dir, action)
            known = memory.summary(phase)
            if known.get("known") and _age_days(known.get("updated_at")) < float(refresh_days):
                rows.append({"phase": phase, "action": action, "pass": True, "status": "already_known",
                             "result": "SKIPPED", "updated_at": known.get("updated_at")})
                continue
            if time.monotonic() - started > float(deadline_seconds):
                stop_reason = stop_reason or f"time budget of {int(deadline_seconds)} s used; the rest is learned next mission"
                rows.append({"phase": phase, "action": action, "pass": False, "status": "deferred", "result": "SKIPPED",
                             "reason": stop_reason})
                continue
            if not fresh:
                # The phase's Create form (never saved) may still be open on the listing's
                # own URL (a BizFlow wizard page, a drawer): load the listing afresh.
                await _fresh_listing(browser, learner.runner._listing_url(phase))
                fresh = True
            _say(chat, f"🧭 {display} · {action.title()}: opening the listing, the object's row and “{action.title()}” (read-only)",
                 phase=phase)
            try:
                try:
                    row = await learner.learn(phase, target="", input_data=input_data, action=action, gate=gate or {})
                except Exception as exc:
                    if is_environment_fatal(exc):
                        raise
                    # The object was not found / not opened: try an object the listing shows.
                    row = {"pass": False, "status": "error", "target_source": "input.json",
                           "reason": mask_sensitive_string(str(exc))[:300]}
                if (not row.get("pass") and row.get("target_source") == "input.json"
                        and str(row.get("status") or "") in _FALLBACK_STATUSES):
                    # input.json's object is new (it exists only after an authorized save):
                    # learn the section on an object the listing already shows.
                    first = await learner.first_row_name(phase)
                    if first and first != row.get("target"):
                        retry = await learner.learn(phase, target=first, input_data=input_data, action=action, gate=gate or {})
                        retry["target_source"] = "first listing row (input.json's object is not on the listing yet)"
                        retry["first_try"] = {k: row.get(k) for k in ("target", "status", "reason")}
                        row = retry
            except Exception as exc:
                row = {"phase": phase, "action": action, "pass": False, "status": "error", "result": "FAILED",
                       "error": mask_sensitive_string(str(exc))[:400], "environment_fault": is_environment_fatal(exc)}
            row = {**row, "phase": phase, "action": action}
            if row.get("pass"):
                row["script_file"] = _write_operation_script(memory_dir, phase, action, row)
                cell = _cell(memory_dir, phase, action)
                _say(chat, f"✓ {display} · {action.title()} learned: {cell.get('detail') or row.get('status')}"
                           + (f" — script {Path(row['script_file']).name}" if row.get("script_file") else ""),
                     kind="verified", phase=phase)
            elif str(row.get("status") or "").endswith("needs_authorized_run"):
                _say(chat, f"⏸ {display} · {action.title()}: {row.get('reason') or 'a guarded button; learned in an authorized run'}",
                     kind="warn", phase=phase)
            else:
                _say(chat, f"✗ {display} · {action.title()} not learned: "
                           f"{row.get('reason') or row.get('error') or row.get('status')}", kind="warn", phase=phase)
            rows.append(mask_sensitive_data({k: row.get(k) for k in (
                "phase", "action", "pass", "status", "result", "target", "target_source", "reason", "error", "menus",
                "section_kind", "seconds", "script_file", "first_try", "environment_fault", "blocked_environments")}))
            if row.get("environment_fault"):
                stop_reason = "the portal session is not usable (environment fault); learning stops here"
                break
        if stop_reason.startswith("the portal session"):
            break
    learned = sum(1 for r in rows if r.get("pass") and r.get("status") != "already_known")
    report = {
        "schema_version": "hip.operation-learning-report.v1", "run_id": run_id, "finished_at": _now(),
        "seconds": round(time.monotonic() - started, 1), "phases": list(phases), "actions": list(actions),
        "rows": rows, "learned": learned, "skipped": sum(1 for r in rows if r.get("result") == "SKIPPED"),
        "failed": sum(1 for r in rows if not r.get("pass") and r.get("result") != "SKIPPED"),
        "stop_reason": stop_reason, "read_only": True,
    }
    matrix = operation_matrix(memory_dir, phases, report)
    report["matrix"] = matrix
    safe_write_json(out_dir / "operation_learning_report.json", report, mask=False)
    safe_write_json(memory_dir / "operation_learning" / "last_report.json", report, mask=False)
    safe_write_json(memory_dir / "operation_learning" / "matrix.json", matrix, mask=False)
    _say(chat, f"🧭 Operations known: {matrix['known']}/{matrix['total']} (phase × Create/Edit/Clone/Migrate/Deploy); "
               f"learned now {learned}, already known {report['skipped']}, not learned {report['failed']}.",
         kind="learn")
    return report


__all__ = ["OPERATIONS", "LEARNED_OPERATIONS", "SCHEMA", "operation_matrix", "learn_mission_operations", "read_last_report"]
