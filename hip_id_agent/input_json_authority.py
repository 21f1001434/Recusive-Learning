"""The live form holds exactly what input.json asks for -> the phase is complete (V243R29).

Before R29 a phase whose form was already filled and committed exactly as
input.json asks could still stay open:

* a text or vision model judge answered "not complete" (a model false
  negative) and the champion/challenger panel -- since R24 only the one locked
  model -- agreed, so the phase was held for a human;
* a newly learned phase always waited for a human confirmation, also when
  every value was exact;
* an attempt that failed after the form was complete (a late executor error)
  reopened the form and filled it again.

This module gives the mission one model-independent authority: a fresh,
read-only proof of the *current* live form.  Open dropdowns are closed first
(a value only typed into a dropdown's search box reverts, so only committed
values count), then every input.json-owned value, repeatable row and required
upload is compared with the form.  When it is exact the phase is complete; a
model judge that disagrees is recorded as that model's mistake (it feeds the
champion selection) instead of blocking.  Nothing is clicked, filled or saved.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_string

SCHEMA = "hip.input-json-completion-authority.v1"

# Failures where the live form cannot be the answer (not authenticated, a
# mutation question, a dead browser, lists that never loaded, another page).
NOT_ELIGIBLE_CLASSES = frozenset({
    "authentication_expired", "unsafe_or_mutating", "dependency_contract_invalid", "browser_disconnected",
    "dropdown_options_empty", "route_not_committed", "mcp_surface_drift",
    # V243R32: the page is a Whitelabel Error Page -- the form is gone.
    "whitelabel_error_page",
})


def is_authoritative(live: Mapping[str, Any]) -> bool:
    """Every input.json value exact on the live form, nothing missing, invalid or unproven."""
    return bool(
        isinstance(live, Mapping)
        and live.get("pass") is True
        and live.get("deterministic_pass") is True
        and live.get("read_only") is True
        and str(live.get("source") or "") == "current_live_browser"
        and live.get("matched_fields")
        and not live.get("missing_fields")
        and not live.get("row_issue_fields")
        and not live.get("invalid_fields")
        and bool((live.get("required_upload_proof") or {}).get("pass", True))
    )


async def prove_input_json_completion(
    *, page: Any, phase: str, phase_input: Dict[str, Any], judge: Any = None, blur: bool = True,
    section: Optional[str] = None, walk_tabs: bool = True,
) -> Dict[str, Any]:
    """Fresh read-only proof of the current live form against input.json.

    ``section``: one wizard tab.  ``walk_tabs``: a wizard's tabs may be shown in
    turn (navigation only) -- never while the agent is still working.
    """
    from .dds_control_driver import close_open_dropdown
    from .phase_live_reproof import live_read_only_phase_reproof

    if page is None:
        return {"schema_version": SCHEMA, "phase": phase, "pass": False, "status": "no_page", "read_only": True}
    if blur:
        try:
            await close_open_dropdown(page, phase)  # non-clicking blur: only committed values stay
            # DDS restores the committed value when the popup closes (asynchronously):
            # read the form only once no dropdown is open any more.
            for _ in range(15):
                still_open = await page.evaluate(
                    "() => document.querySelectorAll('[role=combobox][aria-expanded=true]').length")
                if not still_open:
                    break
                await page.wait_for_timeout(100)
            await page.wait_for_timeout(150)
        except Exception:
            pass
    live = await live_read_only_phase_reproof(
        page=page, phase=phase, phase_input=phase_input, judge=judge, section=section, walk_tabs=walk_tabs)
    authoritative = is_authoritative(live)
    return {
        **live,
        "schema_version": SCHEMA,
        "live_reproof_schema": live.get("schema_version"),
        "authoritative": authoritative,
        "pass": authoritative,
        "matched_count": len(live.get("matched_fields") or []),
        "proved_at": utc_now(),
        "rule": "every input.json value, row and required upload is exact and committed on the live form",
    }


_BUSY_JS = r"""() => {
  // V243R36: only a popup that is open makes the form "busy" -- a combobox (or a
  // popup trigger) expanded right now, a list it owns, or a visible loader.  The
  // portal's own navigation menu, a chip list or an expanded accordion are always
  // on screen and kept the live map frozen at 0/N on the live portal.
  const shown = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const shell = 'nav,header,aside,[role=navigation],[role=menubar],[role=banner]';
  const expanded = Array.from(document.querySelectorAll('[aria-expanded=true]')).filter(shown)
    .filter((t) => !t.closest(shell))
    .filter((t) => t.getAttribute('role') === 'combobox' || /^(listbox|menu|tree|grid|dialog|true)$/i.test(t.getAttribute('aria-haspopup') || ''));
  const owned = new Set();
  for (const t of expanded) for (const a of ['aria-controls', 'aria-owns'])
    for (const id of String(t.getAttribute(a) || '').split(/\s+/)) if (id) owned.add(id);
  const lists = Array.from(document.querySelectorAll('[role=listbox],[role=menu]')).filter(shown)
    .filter((l) => owned.has(l.id) || !!(l.closest('dds-dropdown,.dds__dropdown') && l.closest('dds-dropdown,.dds__dropdown').querySelector('[aria-expanded=true]'))).length;
  const busy = Array.from(document.querySelectorAll('[aria-busy=true],.dds__loading-indicator,.dds__progress-indicator,.spinner,.loading'))
    .filter(shown).filter((el) => !el.closest(shell)).length;
  return {open: expanded.length, lists, busy};
}"""


_PERSISTENT_BUSY_SECONDS = 20.0


async def quiet_completion_probe(*, page: Any, phase: str, phase_input: Dict[str, Any]) -> Dict[str, Any]:
    """V243R32: is the live form complete right now?  Read-only and non-intrusive.

    Used while the agent is still working, so it never blurs, opens or closes
    anything: when a dropdown or list is open, or the portal is loading, the
    answer is "busy" (not complete) -- a value only typed into a search box is
    not committed.
    """
    if page is None:
        return {"pass": False, "status": "no_page"}
    try:
        state = await page.evaluate(_BUSY_JS)
    except Exception as exc:
        return {"pass": False, "status": "probe_error", "error": mask_sensitive_string(str(exc))[:200]}
    counts = {k: int((state or {}).get(k) or 0) for k in ("open", "lists", "busy")}
    busy_ignored = False
    if any(counts.values()):
        # V243R36: something that never closes is not "busy" -- after 20 s of an
        # unbroken busy signal the map is read anyway (reading touches nothing).
        import time as _time

        since = getattr(page, "_hip_quiet_busy_since", None)
        now = _time.monotonic()
        if since is None:
            try:
                setattr(page, "_hip_quiet_busy_since", now)
            except Exception:
                pass
            since = now
        if now - float(since) < _PERSISTENT_BUSY_SECONDS:
            return {"pass": False, "status": "busy", **counts}
        busy_ignored = True
    else:
        try:
            setattr(page, "_hip_quiet_busy_since", None)
        except Exception:
            pass
    # V243R34: the live input.json map -- every value, the form field it maps to,
    # what the form holds now -- read without touching the page.
    from .phase_live_reproof import live_input_field_map

    live_map = await live_input_field_map(page=page, phase=phase, phase_input=phase_input)
    if busy_ignored:
        live_map["busy_ignored"] = counts
    return {"pass": bool(live_map.get("complete")), "status": "complete" if live_map.get("complete") else "filling",
            "matched_count": live_map.get("exact"), "missing_count": int(live_map.get("total") or 0) - int(live_map.get("exact") or 0),
            "map": live_map, **({"busy_ignored": counts} if busy_ignored else {})}


def model_judge_verdicts(judge_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The model judges' own verdicts (text and vision), for scoring against the live truth."""
    out: List[Dict[str, Any]] = []
    for kind in ("text_model_judge", "vision_model_judge"):
        row = judge_result.get(kind) if isinstance(judge_result, Mapping) else None
        if not isinstance(row, Mapping) or str(row.get("status") or "") != "ok" or not isinstance(row.get("pass"), bool):
            continue
        out.append({
            "judge": kind.replace("_model_judge", ""),
            "model": str(row.get("model") or row.get("selected_model") or row.get("model_used") or ""),
            "pass": bool(row.get("pass")),
        })
    return out


def write_authority(phase_dir: Path, proof: Mapping[str, Any], *, reason: str, judge_result: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Persist the proof; an authoritative one also becomes the phase's live reproof artifact."""
    verdicts = model_judge_verdicts(judge_result or {})
    truth = bool(proof.get("pass"))
    record = {
        **dict(proof),
        "reason": str(reason or "")[:300],
        "model_judges": [dict(v, agrees_with_live_form=(v["pass"] == truth)) for v in verdicts],
        "model_judges_overruled": [v for v in verdicts if v["pass"] != truth and truth],
        "values_stored": False,
        "selectors_stored": False,
        "coordinates_stored": False,
    }
    try:
        safe_write_json(Path(phase_dir) / "input_json_completion_authority.json", record)
        if truth:
            # The existing exact-completion checkpoint accepts this read-only reproof.
            reproof = {k: v for k, v in record.items() if k not in {"model_judges", "model_judges_overruled"}}
            reproof["schema_version"] = str(proof.get("live_reproof_schema") or "hip.live-read-only-phase-reproof.v1")
            safe_write_json(Path(phase_dir) / "phase_live_read_only_reproof.json", reproof)
    except Exception as exc:
        record["write_error"] = mask_sensitive_string(str(exc))[:300]
    return record


def accept_exact_phase(
    judge_result: Mapping[str, Any], diagnosis: Mapping[str, Any], authority: Mapping[str, Any],
) -> tuple:
    """(judge_result, diagnosis, overruled): an exact live form completes the phase.

    Only a passing authority changes anything; a judge block on a form that is
    exact becomes a pass (status ``pass_input_json_exact``) and the block is kept
    as ``overruled_diagnosis`` for the audit.
    """
    judge = dict(judge_result or {})
    blocked = bool(diagnosis) or not bool(judge.get("pass", True))
    if not (isinstance(authority, Mapping) and authority.get("pass")) or not blocked:
        return judge, dict(diagnosis or {}), False
    judge["pre_authority_pass"] = bool(judge.get("pass"))
    judge["pass"] = True
    judge["status"] = "pass_input_json_exact"
    judge["input_json_authority"] = {k: authority.get(k) for k in (
        "matched_count", "matched_fields", "model_judges", "model_judges_overruled", "rule")}
    from .security import mask_sensitive_data

    judge["overruled_diagnosis"] = mask_sensitive_data(dict(diagnosis or {}))
    return judge, {}, True


def learning_review_needed(*, learning_phase: bool, authority: Mapping[str, Any], review_even_when_exact: bool = False) -> bool:
    """A newly learned phase asks a human only when its live form is not already exact."""
    if not learning_phase:
        return False
    return bool(review_even_when_exact or not (isinstance(authority, Mapping) and authority.get("pass")))


__all__ = [
    "accept_exact_phase", "learning_review_needed", "quiet_completion_probe",
    "NOT_ELIGIBLE_CLASSES", "SCHEMA", "is_authoritative", "model_judge_verdicts", "prove_input_json_completion", "write_authority",
]
