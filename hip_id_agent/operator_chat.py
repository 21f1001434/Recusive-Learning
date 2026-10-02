"""V243R35: what the operator types in the live agent chat, and the answers.

The Control Center backend calls :func:`interpret` for each message, then:

* ``status`` / ``left`` / ``help`` -- answered at once from the run's live state
  (mission trace, live input.json map, the agent's last lines);
* ``pause`` / ``resume`` -- written to ``operator_control.json``; the agent pauses
  at its next safe point (between fields / before an attempt) and says so;
* ``stop`` -- stops the mission process (as the Stop button does);
* ``accept`` / ``reject`` -- answer the phase review the agent is waiting on;
* anything else -- a hint: the agent acknowledges it and hands it to the form
  planner as advisory context.  A hint never changes an input.json value and
  never authorizes Save / Submit / Deploy.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent_chat import CHAT_FILENAME, display_value, humanize, last_agent_messages

_INTENTS = [
    ("help", re.compile(r"^\s*(help|\?|commands|what can i (say|do))\s*\??\s*$", re.I)),
    ("stop", re.compile(r"^\s*(stop|abort|cancel|kill)( the)?( mission| run| agent)?[\s.!]*$", re.I)),
    ("pause", re.compile(r"^\s*(pause|hold( on)?|wait( a (moment|minute|sec(ond)?))?|hang on)\b[\s.!]*$", re.I)),
    ("left", re.compile(r"(what'?s|what is) (left|remaining|missing)|which (fields|values) (are )?(left|remaining|missing|not)|\bremaining\b|\bleft to (fill|do)\b", re.I)),
    # "status" alone asks for the status; "Status is enabled" talks about the Status field.
    ("status", re.compile(r"^\s*(status|progress|update)( please| now| update)?[\s?.!]*$|^\s*(where are you|what are you doing|what'?s happening|what is happening|how (is it|far|are you)( going)?)\b", re.I)),
]

# V243R36: "everything is filled correctly", "all fields are correct", "the form is
# complete", "looks good, move on" -- the operator confirms the phase.
_NEGATION = re.compile(r"\b(not|n't|no longer|missing|wrong|incorrect|isnt|arent|wasnt|fail(ed|s)?|error)\b", re.I)
_CONFIRM = [
    re.compile(r"^\s*(accept(ed)?|approve(d)?|confirm(ed)?|lgtm|correct|perfect|all good|done|complete(d)?)\b", re.I),
    re.compile(r"\blooks? (good|correct|right|fine|ok|okay|perfect)\b", re.I),
    re.compile(r"\b(every ?thing|all( the)?( fields| values| of it)?|the form|this form|the phase|this phase|it|that|form)\b"
               r"[^.?!]{0,40}?\b(is |are |was |were |got |has been |have been )?(all )?"
               r"(filled( in| out)?( correctly| properly| right| fine)?|correct(ly)?|complete(d)?|done|fine|good|ok(ay)?|right|perfect)\b", re.I),
    re.compile(r"\b(filled|entered|done) (correctly|properly|right)\b", re.I),
    re.compile(r"\bmark (it|this|the phase|the form)? ?(as )?(complete|completed|done)\b", re.I),
    re.compile(r"\b(move|go) (on )?to the next( phase)?\b|\bnext phase\b|\bproceed to (the )?next\b", re.I),
]
_REJECT = re.compile(r"^\s*(reject|needs? (a )?(fix|correction)|wrong|incorrect|not correct|that'?s wrong)\b", re.I)
_SHORT_YES = re.compile(r"^\s*(yes|yep|yeah|y|ok|okay|sure|fine|go ahead|go on|continue|proceed|carry on|keep going|resume|unpause)\b[\s.!]*$", re.I)
_SHORT_NO = re.compile(r"^\s*(no|nope|n)\b[\s.!]*$", re.I)
_RESUME_WORDS = re.compile(r"^\s*(resume|continue|go( on| ahead)?|carry on|unpause|proceed|keep going)\b[\s.!]*$", re.I)

HELP_TEXT = (
    "You can type: “status” (what I'm doing now), “what's left” (values not on the form yet), "
    "“pause” / “resume” (I finish the field in hand, then wait), “stop” (end the mission), "
    "“everything is filled correctly” / “accept” (you confirm the phase: I stop filling, prove the form and finish it "
    "without reopening it), “reject” (answer a phase review). Anything else is a hint for me "
    "— e.g. “Interface Type is on the Connection tab” — I use it to find controls; input.json stays "
    "the source of every value and nothing is saved without your Save confirmation."
)


_ACTION_KINDS = {"navigate", "click", "type", "select", "key", "field", "verified", "failed", "retry", "heal",
                 "complete", "blocked", "phase", "stop"}

INTENTS = ("help", "stop", "pause", "resume", "left", "status", "accept", "reject", "ack", "hint")


def interpret(text: str, *, review_pending: bool = False, paused: bool = False) -> str:
    """The operator's intent.  Short replies depend on what the agent is waiting for."""
    clean = str(text or "").strip()
    if not clean:
        return ""
    for name, pattern in _INTENTS:
        if pattern.search(clean):
            return name
    if _SHORT_YES.search(clean):
        # "yes" / "go ahead": answers the review the agent waits on, else resumes a pause.
        if review_pending:
            return "accept"
        if paused or _RESUME_WORDS.search(clean):
            return "resume"
        return "ack"
    if _SHORT_NO.search(clean):
        return "reject" if review_pending else "ack"
    if _REJECT.search(clean) or (review_pending and _NEGATION.search(clean)):
        return "reject"
    if not clean.rstrip().endswith("?") and not _NEGATION.search(clean) and any(p.search(clean) for p in _CONFIRM):
        return "accept"
    return "hint"


_MODEL_SYSTEM = (
    "You classify one message an operator typed to a web-form automation agent that fills Dell HIP portal forms. "
    "Return ONLY JSON: {\"intent\": one of [\"status\", \"left\", \"pause\", \"resume\", \"accept\", \"reject\", \"help\", \"hint\"], "
    "\"confidence\": 0..1}. accept = the operator says the current form/phase is filled correctly or should be completed. "
    "reject = the operator says something on the form is wrong. hint = advice about where a field is or what to watch for. "
    "status/left = asks what the agent is doing / what is still missing. Never output stop."
)


def interpret_with_model(text: str, *, review_pending: bool = False, paused: bool = False, client: Any = None) -> Dict[str, Any]:
    """Patterns first; for a message they read as a plain hint, the configured model may classify it.

    The model is advisory and bounded: "stop" is never taken from it, only a
    confident answer (>= 0.8) among the known intents is used.
    """
    intent = interpret(text, review_pending=review_pending, paused=paused)
    result = {"intent": intent, "source": "patterns"}
    if intent != "hint" or client is None or len(str(text or "")) > 400:
        return result
    try:
        decision = client.json_decision(_MODEL_SYSTEM, json.dumps({
            "message": str(text)[:400], "agent_waits_for_review": bool(review_pending), "agent_paused": bool(paused)}))
    except Exception as exc:
        return dict(result, model_error=str(exc)[:200])
    if not isinstance(decision, dict):
        return result
    model_intent = str(decision.get("intent") or "").strip().lower()
    try:
        confidence = float(decision.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if model_intent in {"status", "left", "pause", "resume", "accept", "reject", "help"} and confidence >= 0.8:
        return {"intent": model_intent, "source": "model", "confidence": confidence}
    return dict(result, model_intent=model_intent, model_confidence=confidence)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _ago(stamp: str) -> str:
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        seconds = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
    except Exception:
        return ""
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def live_state(run_dir: str | Path) -> Dict[str, Any]:
    """The run's current state for the chat header and the answers."""
    run_dir = Path(run_dir)
    trace = _read_json(run_dir / "mission_trace.json")
    live_map = _read_json(run_dir / "input_json_live_map.json")
    step = {}
    current_id = str(trace.get("current_step_id") or "")
    for row in trace.get("steps") or []:
        if isinstance(row, dict) and (row.get("step_id") == current_id or (not current_id and row.get("status") in {"running", "in_progress"})):
            step = row
            break
    control = _read_json(run_dir / "operator_control.json")
    # "The last thing I did" is the last action on the page, not a live count or a note.
    recent = last_agent_messages(run_dir, limit=40)
    actions = [r for r in recent if r.get("kind") in _ACTION_KINDS]
    last = actions or recent
    try:
        chat_age = time.time() - (run_dir / CHAT_FILENAME).stat().st_mtime
    except OSError:
        chat_age = None
    return {
        "run_id": str(trace.get("run_id") or run_dir.name),
        "mission_status": str(trace.get("status") or ""),
        "phase": str(step.get("phase") or live_map.get("phase") or ""),
        "phase_display": str(step.get("label") or live_map.get("phase_display") or humanize(step.get("phase") or "")),
        "attempt": int(step.get("attempt") or 0),
        "activity": str(step.get("current_activity") or ""),
        "paused": str(control.get("state") or "") == "paused",
        "live_map": {k: live_map.get(k) for k in ("phase", "phase_display", "exact", "total", "different", "not_on_screen",
                                                   "invalid", "complete", "complete_at", "at") if k in live_map},
        "last_action": last[-1] if last else {},
        "chat_age_seconds": round(chat_age, 1) if chat_age is not None else None,
        "completed_steps": len(trace.get("completed_step_ids") or []),
        "total_steps": len(trace.get("steps") or []),
    }


def status_reply(state: Dict[str, Any], *, running: bool) -> str:
    parts: List[str] = []
    if not running:
        parts.append(f"No mission is running right now. Last run {state.get('run_id') or '—'}: "
                     f"{state.get('mission_status') or 'unknown'} ({state.get('completed_steps', 0)}/{state.get('total_steps', 0)} phases done).")
    elif state.get("phase"):
        tries = f" (attempt {state['attempt']})" if int(state.get("attempt") or 0) > 1 else ""
        parts.append(f"{'⏸ Paused on' if state.get('paused') else 'Working on'} {state.get('phase_display')}{tries}.")
    else:
        parts.append("The mission is starting (signing in / opening the portal).")
    lm = state.get("live_map") or {}
    if lm.get("total"):
        done = "complete — filling stopped" if lm.get("complete") else f"{lm.get('exact')}/{lm.get('total')} values on the form"
        parts.append(f"Live check for {lm.get('phase_display') or humanize(lm.get('phase'))}: {done}.")
    last = state.get("last_action") or {}
    if last.get("text"):
        ago = _ago(str(last.get("at") or ""))
        parts.append(f"Last thing I did: {last['text']}" + (f" ({ago})." if ago else "."))
    return " ".join(parts)


def left_reply(run_dir: str | Path) -> str:
    live_map = _read_json(Path(run_dir) / "input_json_live_map.json")
    rows = [r for r in (live_map.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        return "I have not read the form yet in this run — ask again once a phase is being filled."
    name = live_map.get("phase_display") or humanize(live_map.get("phase"))
    if live_map.get("complete"):
        return f"Nothing is left on {name}: all {live_map.get('total')}/{live_map.get('total')} input.json values are on the form."
    pending = [r for r in rows if r.get("status") not in {"exact", "not_checked"}]
    shown = []
    for r in pending[:8]:
        label = r.get("label") or humanize(r.get("field"))
        state = {"different": f"shows {display_value(label, r.get('live'))}", "not_on_screen": "not on screen yet",
                 "invalid": "flagged by the portal"}.get(str(r.get("status")), str(r.get("status")))
        shown.append(f"{label} → {display_value(label, r.get('expected'))} ({state})")
    more = f" …and {len(pending) - 8} more" if len(pending) > 8 else ""
    return (f"{len(pending)} of {live_map.get('total')} values still to finish on {name}: "
            + "; ".join(shown) + more + ".")


def hint_reply(state: Dict[str, Any]) -> str:
    where = state.get("phase_display") or "the next phase"
    return (f"📝 Noted. The agent will pick this up at its next step on {where} and use it as a hint "
            "to find controls — input.json stays the source of every value, and nothing is saved without your Save confirmation.")


def pending_review(store: Any, run_id: str) -> Optional[Dict[str, Any]]:
    try:
        rows = store.pending(run_id=run_id) or store.pending()
    except Exception:
        return None
    return rows[0] if rows else None
