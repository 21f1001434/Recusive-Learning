"""Read-only terminal reproof for a stable HIP phase.

This module is intentionally value-minimal and never clicks/fills/saves.  It exists
for the case where a phase has already reached the requested live state but the
long-running KB/learning coroutine has not yet emitted its normal exact-execution
artifact.  The no-progress watchdog and human-review path can use this probe to
prove the current browser state and hand off without destructively replaying the
form.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .section_judge import build_phase_expectation, DualModelSectionJudge
from .stateful_form_runtime import capture_stateful_controls, compile_phase_state_graph
from .security import mask_sensitive_string


def _basename_any(value: Any) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    # Path on Linux does not split a Windows path, so normalize by syntax.
    if "\\" in text or (len(text) > 1 and text[1:2] == ":"):
        return PureWindowsPath(text).name
    return Path(text).name


def _row_counts(controls: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    seen: Dict[str, set[int]] = {}
    for row in controls:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("row_kind") or "").strip()
        index = row.get("row_index")
        if not kind or index is None:
            continue
        try:
            idx = int(index)
        except Exception:
            continue
        seen.setdefault(kind, set()).add(idx)
    counts = {f"{kind}_rows": len(indexes) for kind, indexes in seen.items()}
    # Historical Document Type exact-judge names.
    if "document_identifier" in seen:
        counts["document_identifier_rows"] = len(seen["document_identifier"])
    if "attribute" in seen:
        counts["attribute_rows"] = len(seen["attribute"])
    return counts


def _upload_nodes(phase_input: Dict[str, Any], phase: str) -> List[Dict[str, Any]]:
    try:
        graph = compile_phase_state_graph(phase_input if isinstance(phase_input, dict) else {}, phase)
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for node in (graph.get("nodes") or []) if isinstance(graph, dict) else []:
        if not isinstance(node, dict) or str(node.get("action") or "") != "upload_file":
            continue
        expected = node.get("expected_value")
        if expected in (None, "", []):
            continue
        rows.append({
            "field": str(node.get("field_key") or node.get("node_id") or "upload_file"),
            "input_path": str(node.get("input_path") or ""),
            "expected_basename": _basename_any(expected),
            "section": str(node.get("section") or ""),
        })
    return rows


def _without_upload_facts(expected: Dict[str, Any], uploads: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not uploads:
        return expected
    upload_fields = {str(x.get("field") or "").strip().lower() for x in uploads}
    upload_paths = {str(x.get("input_path") or "").strip().lower() for x in uploads if x.get("input_path")}
    out = deepcopy(expected)
    facts = []
    for fact in out.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        field = str(fact.get("field") or "").strip().lower()
        path = str(fact.get("input_path") or "").strip().lower()
        if field in upload_fields or (path and path in upload_paths):
            continue
        facts.append(fact)
    out["facts"] = facts
    return out


async def _live_file_proof(page: Any, uploads: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not uploads:
        return {"required": False, "pass": True, "expected_upload_count": 0, "matched_upload_count": 0}
    try:
        rows = await page.evaluate(
            """
() => Array.from(document.querySelectorAll('input[type=file]')).map(el => ({
  files: Array.from(el.files || []).map(f => String(f.name || '').trim()).filter(Boolean),
  value: String(el.value || '').trim()
}))
"""
        )
    except Exception as exc:
        return {
            "required": True,
            "pass": False,
            "expected_upload_count": len(uploads),
            "matched_upload_count": 0,
            "capture_error": mask_sensitive_string(str(exc))[:500],
        }
    live_names: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for name in row.get("files") or []:
            if str(name or "").strip():
                live_names.add(str(name).strip().lower())
        value_name = _basename_any(row.get("value"))
        if value_name:
            live_names.add(value_name.lower())
    matched_fields: List[str] = []
    missing_fields: List[str] = []
    for upload in uploads:
        expected_name = str(upload.get("expected_basename") or "").lower()
        field = str(upload.get("field") or "upload_file")
        if expected_name and expected_name in live_names:
            matched_fields.append(field)
        else:
            missing_fields.append(field)
    return {
        "required": True,
        "pass": len(missing_fields) == 0,
        "expected_upload_count": len(uploads),
        "matched_upload_count": len(matched_fields),
        "matched_fields": matched_fields,
        "missing_fields": missing_fields,
        "values_stored": False,
    }


def _chip_values(controls: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """V243R32: a chip multi-select with an empty text value reads as its chosen chips."""
    out: List[Dict[str, Any]] = []
    for c in controls:
        if (isinstance(c, dict) and str(c.get("value") or "").strip() == ""
                and [str(v).strip() for v in (c.get("selected_values") or []) if str(v).strip()]):
            chips = [str(v).strip() for v in c.get("selected_values") if str(v).strip()]
            out.append(dict(c, value=", ".join(chips), value_source="selected_chips"))
        else:
            out.append(c)
    return out


def _radio_group_answers(controls: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One control per radio group: its label and the checked option's label (V243R32)."""
    groups: Dict[tuple, Dict[str, Any]] = {}
    for c in controls:
        if not isinstance(c, dict) or str(c.get("type") or "").lower() != "radio" or not isinstance(c.get("checked"), bool):
            continue
        label = str(c.get("group_label") or "").strip()
        if not label:
            continue
        key = (label, str(c.get("group_name") or c.get("name") or ""), str(c.get("section") or ""),
               str(c.get("row_kind") or ""), c.get("row_index"))
        row = groups.setdefault(key, {
            "label": label, "type": "radio_group", "role": "radiogroup", "value": "", "selected_values": [],
            "section": c.get("section"), "row_kind": c.get("row_kind"), "row_index": c.get("row_index"),
            "framework_key": c.get("framework_key"), "group_name": c.get("group_name"), "disabled": False,
            "source": "radio_group_checked_option",
        })
        if c.get("checked"):
            row["value"] = str(c.get("label") or "").strip()
            row["selected_values"] = [row["value"]]
    return [g for g in groups.values() if g["value"]]


def _section_matches(expected: Optional[str], actual: Any) -> bool:
    e, a = _norm_section(expected), _norm_section(actual)
    return bool(e and a and (e == a or e in a or a in e))


def _norm_section(value: Any) -> str:
    return " ".join(str(value or "").replace(":", " ").split()).strip().lower()


def _form_level_facts_relaxed(
    expected: Dict[str, Any], controls: Sequence[Dict[str, Any]], deterministic: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Missing scalar facts whose section is the form's own title, matched on the whole form (V243R32).

    The form title is the section most scalar facts share; controls directly
    under it carry it, controls in its fieldsets carry the fieldset's name --
    both are on that form.  Only facts the strict match missed are relaxed, the
    committed value must still be exactly equal, and row facts (row kind / row
    index) always keep their strict section match.
    """
    facts = [f for f in (expected.get("facts") or []) if isinstance(f, dict)]
    scalar = [f for f in facts if not f.get("row_kind") and f.get("row_index") is None and _norm_section(f.get("section"))]
    if not scalar:
        return None
    counts: Dict[str, int] = {}
    for f in scalar:
        counts[_norm_section(f.get("section"))] = counts.get(_norm_section(f.get("section")), 0) + 1
    title = max(counts, key=lambda k: counts[k])
    missing = {str(x.get("field")) for x in deterministic.get("missing_values") or [] if isinstance(x, dict)}
    changed = False
    relaxed: List[Dict[str, Any]] = []
    for f in facts:
        if (f in scalar and _norm_section(f.get("section")) == title and str(f.get("field")) in missing):
            relaxed.append(dict(f, section="", section_aliases=[], form_level_section=f.get("section")))
            changed = True
        else:
            relaxed.append(f)
    return dict(expected, facts=relaxed) if changed else None


async def _prove_surface(
    *,
    page: Any,
    phase: str,
    phase_input: Dict[str, Any],
    judge: Optional[DualModelSectionJudge] = None,
    section: Optional[str] = None,
) -> Dict[str, Any]:
    """Prove the *current* live form without taking any physical action.

    V243R32: ``section`` proves one section of a wizard (a Business Flow tab):
    only that section's input.json facts and uploads are compared, because a
    wizard shows one tab at a time.

    This is a terminal/checkpoint probe only. It never authorizes a mutation and
    never persists customer values, selectors, or coordinates.  Exact values are
    compared in-memory and only structural field names/counts are returned.
    """
    deterministic_judge = judge or DualModelSectionJudge()
    try:
        controls = await capture_stateful_controls(page, phase)
    except Exception as exc:
        return {
            "schema_version": "hip.live-read-only-phase-reproof.v1",
            "phase": phase,
            "pass": False,
            "status": "capture_failed",
            "read_only": True,
            "source": "current_live_browser",
            "error": mask_sensitive_string(str(exc))[:500],
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }

    expected = build_phase_expectation(phase_input if isinstance(phase_input, dict) else {}, phase)
    uploads = _upload_nodes(phase_input if isinstance(phase_input, dict) else {}, phase)
    if section:
        uploads = [u for u in uploads if _section_matches(section, u.get("section"))]
    expected_for_fields = _without_upload_facts(expected, uploads)
    if section:
        scoped = [f for f in (expected_for_fields.get("facts") or []) if isinstance(f, dict)
                  and _section_matches(section, f.get("section"))]
        if not scoped:
            return {
                "schema_version": "hip.live-read-only-phase-reproof.v1", "phase": phase, "section": section,
                "pass": False, "status": "no_input_json_facts_for_section", "read_only": True,
                "source": "current_live_browser", "values_stored": False, "selectors_stored": False,
                "coordinates_stored": False,
            }
        expected_for_fields = dict(expected_for_fields, facts=scoped, row_counts={
            k: v for k, v in (expected_for_fields.get("row_counts") or {}).items()
            if any(str(f.get("row_kind") or "") and str(k).startswith(str(f.get("row_kind"))) for f in scoped)})
    actual_state = {
        # V243R32: a radio group answers with its checked option ("Existing
        # Account" -> "Yes"); a checked "No" radio was dropped as a false value.
        # A chip multi-select holds its choice in selected_values, not in value.
        "controls": [*_chip_values(controls), *_radio_group_answers(controls)],
        "row_counts": _row_counts(controls),
        "visible_text": "",
        "source": "current_live_browser_stateful_controls",
    }
    deterministic = deterministic_judge.deterministic_judge(
        expected=expected_for_fields,
        actual_state=actual_state,
        attempts=[],
    )
    relaxed_fields: List[str] = []
    if deterministic.get("missing_values"):
        # V243R32: form-level facts carry the form title as their section
        # ("Create Transport Profile") while the live controls carry their
        # fieldset ("Basic Details :"), so every field looked missing on Transport
        # Profile, BizFlow, Rule and Data Map.  Such facts are matched on the
        # whole active form -- still by label and exact committed value.
        relaxed_expected = _form_level_facts_relaxed(expected_for_fields, controls, deterministic)
        if relaxed_expected is not None:
            again = deterministic_judge.deterministic_judge(expected=relaxed_expected, actual_state=actual_state, attempts=[])
            before = {str(x.get("field")) for x in deterministic.get("missing_values") or [] if isinstance(x, dict)}
            after = {str(x.get("field")) for x in again.get("missing_values") or [] if isinstance(x, dict)}
            relaxed_fields = sorted(before - after)
            deterministic = again
    upload_proof = await _live_file_proof(page, uploads)
    invalid_fields = sorted({
        str(c.get("label") or c.get("framework_key") or c.get("semantic_key") or "control")
        for c in controls
        if isinstance(c, dict) and str(c.get("aria_invalid") or "").strip().lower() == "true"
    })
    field_pass = bool(deterministic.get("pass"))
    passed = bool(field_pass and upload_proof.get("pass") and not invalid_fields)
    return {
        "schema_version": "hip.live-read-only-phase-reproof.v1",
        "phase": phase,
        "section": section or "",
        "pass": passed,
        "status": "exact_live_state_reproved" if passed else "live_state_not_exact",
        "read_only": True,
        "source": "current_live_browser",
        "deterministic_pass": field_pass,
        "matched_fields": [str(x.get("field") or "") for x in (deterministic.get("matched_values") or []) if isinstance(x, dict)],
        "missing_fields": [str(x.get("field") or "") for x in (deterministic.get("missing_values") or []) if isinstance(x, dict)],
        "row_issue_fields": [str(x.get("field") or "") for x in (deterministic.get("row_issues") or []) if isinstance(x, dict)],
        "invalid_fields": invalid_fields,
        "form_level_section_matched_fields": relaxed_fields,
        "actual_control_count": int(deterministic.get("actual_control_count") or len(controls)),
        "row_counts": actual_state["row_counts"],
        "required_upload_proof": upload_proof,
        "browser_replay_performed": False,
        "form_mutated": False,
        "values_stored": False,
        "selectors_stored": False,
        "coordinates_stored": False,
    }
_TABS_JS = r"""() => {
  const shown = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  return Array.from(document.querySelectorAll('[role=tab]')).map((t, index) => ({
    index, shown: shown(t), text: String(t.innerText || t.textContent || t.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(),
    selected: t.getAttribute('aria-selected') === 'true' || /\b(active|dds__tabs__tab--active)\b/.test(String(t.className || '')),
  })).filter((t) => t.shown && t.text);
}"""

# The live portal names some wizard tabs differently from the canonical sections.
_TAB_SECTION_ALIASES = {
    "flow details": ("basic details", "general details", "flow information", "create biz flow"),
    "configure source": ("source details", "source configuration", "source information"),
    "configure target(s)": ("configure target", "target details", "target configuration", "target(s)"),
    "configure routing": ("routing", "routing configuration"),
}


def _tab_for_section(section: str, tabs: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    names = [_norm_section(section), *[_norm_section(a) for a in _TAB_SECTION_ALIASES.get(_norm_section(section), ())]]
    for tab in tabs:
        text = _norm_section(tab.get("text"))
        if any(n and (n == text or n in text or text in n) for n in names):
            return tab
    return None


async def _click_tab(page: Any, index: int) -> None:
    await page.locator("[role=tab]").nth(int(index)).click(timeout=3000)
    await page.wait_for_timeout(450)


async def live_read_only_phase_reproof(
    *,
    page: Any,
    phase: str,
    phase_input: Dict[str, Any],
    judge: Optional[DualModelSectionJudge] = None,
    section: Optional[str] = None,
    walk_tabs: bool = True,
) -> Dict[str, Any]:
    """Prove the live form (see ``_prove_surface``); a tabbed wizard tab by tab (V243R32).

    A wizard (Business Flow) shows one tab at a time, so a whole-phase proof
    could never pass.  When input.json's facts span several sections and each
    has a tab on the page, every tab is shown in turn (tab navigation only: no
    value is typed, selected or saved), that tab's facts are proved, and the tab
    that was open is shown again.  ``walk_tabs=False`` (a probe while the agent
    is still working) never switches tabs.
    """
    if section or not walk_tabs:
        return await _prove_surface(page=page, phase=phase, phase_input=phase_input, judge=judge, section=section)
    expected = build_phase_expectation(phase_input if isinstance(phase_input, dict) else {}, phase)
    sections = list(dict.fromkeys(
        str(f.get("section") or "") for f in (expected.get("facts") or []) if isinstance(f, dict) and f.get("section")))
    tabs: List[Dict[str, Any]] = []
    if len(sections) > 1:
        try:
            tabs = list(await page.evaluate(_TABS_JS) or [])
        except Exception:
            tabs = []
    plan = [(sec, _tab_for_section(sec, tabs)) for sec in sections] if len(tabs) > 1 else []
    if not plan or any(tab is None for _, tab in plan) or len({tab["index"] for _, tab in plan}) < 2:
        return await _prove_surface(page=page, phase=phase, phase_input=phase_input, judge=judge)
    original = next((t for t in tabs if t.get("selected")), None)
    parts: List[Dict[str, Any]] = []
    try:
        for sec, tab in plan:
            try:
                await _click_tab(page, tab["index"])
            except Exception as exc:
                parts.append({"section": sec, "pass": False, "status": "tab_not_shown",
                              "error": mask_sensitive_string(str(exc))[:200], "missing_fields": [sec]})
                continue
            parts.append(await _prove_surface(page=page, phase=phase, phase_input=phase_input, judge=judge, section=sec))
    finally:
        if original is not None:
            try:
                await _click_tab(page, original["index"])
            except Exception:
                pass
    def joined(key: str) -> List[str]:
        return [str(x) for part in parts for x in (part.get(key) or [])]
    uploads = [part.get("required_upload_proof") or {} for part in parts]
    passed = bool(parts) and all(part.get("pass") is True for part in parts)
    return {
        "schema_version": "hip.live-read-only-phase-reproof.v1",
        "phase": phase,
        "section": "",
        "pass": passed,
        "status": "exact_live_state_reproved" if passed else "live_state_not_exact",
        "read_only": True,
        "source": "current_live_browser",
        "deterministic_pass": bool(parts) and all(part.get("deterministic_pass") is True for part in parts),
        "matched_fields": joined("matched_fields"),
        "missing_fields": joined("missing_fields"),
        "row_issue_fields": joined("row_issue_fields"),
        "invalid_fields": joined("invalid_fields"),
        "form_level_section_matched_fields": joined("form_level_section_matched_fields"),
        "actual_control_count": sum(int(part.get("actual_control_count") or 0) for part in parts),
        "row_counts": {k: v for part in parts for k, v in (part.get("row_counts") or {}).items()},
        "required_upload_proof": {"required": any(u.get("required") for u in uploads),
                                  "pass": all(bool(u.get("pass", True)) for u in uploads), "values_stored": False},
        "wizard_tabs_proved": [{"section": part.get("section"), "pass": bool(part.get("pass")),
                                "matched": len(part.get("matched_fields") or []),
                                "missing": list(part.get("missing_fields") or [])[:10]} for part in parts],
        "tab_navigation_only": True,
        "browser_replay_performed": False,
        "form_mutated": False,
        "values_stored": False,
        "selectors_stored": False,
        "coordinates_stored": False,
    }


