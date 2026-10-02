"""V243R35: the operator's controls from the live chat -- pause, resume and hints.

The backend writes ``runs/<run>/operator_control.json`` (``{"state": "paused" | "running"}``)
when the user types *pause* / *resume* in the chat, and appends hints to
``operator_chat.jsonl``.  The mission binds to its run when it starts; the form
executor (before each field) and the phase loop (before each attempt) call
:func:`checkpoint`, where a pause takes effect -- the field in hand is finished
first, nothing is left half typed, no click is interrupted.

Paused time is excluded from the no-progress watchdog and the phase wall budget
(:func:`work_clock`), so a pause is never mistaken for a stall.

Hints are advisory: the agent acknowledges each one in the chat and hands the
recent ones to the form planner as context.  They never change an input.json
value and never authorize Save / Submit / Deploy.

V243R36: the operator can confirm a phase ("everything is filled correctly",
Accept).  The agent then stops filling, proves the live form read-only and
completes the phase without reopening it; values it could not read back
itself are recorded as confirmed by the operator (never a field the portal
flags invalid).  This never authorizes Save / Submit / Deploy either.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import agent_chat
from .agent_chat import CONTROL_FILENAME


class _State:
    def __init__(self) -> None:
        self.run_dir: Optional[Path] = None
        self.baseline = 0.0
        self.last_read = 0.0
        self.last_mtime: Any = None
        self.control: Dict[str, Any] = {}
        self.hints_seen = 0
        self.last_hint_check = 0.0
        self.poll_seconds = 0.5
        self.bound_at = 0.0
        self.confirm_said: set = set()
        self.max_confirmed_unread = 3
        self.refusal_said: Dict[str, str] = {}


_S = _State()


def bind(run_dir: str | Path, *, poll_seconds: float = 0.5, max_confirmed_unread: int = 3) -> None:
    """Attach this process (one mission) to its run folder."""
    global _S
    _S = _State()
    _S.run_dir = Path(run_dir)
    _S.poll_seconds = max(0.05, float(poll_seconds or 0.5))
    _S.max_confirmed_unread = max(0, int(max_confirmed_unread))
    # Hints and pauses from before this mission started belong to an earlier run.
    _S.hints_seen = len(agent_chat.iter_operator_messages(_S.run_dir))
    _S.baseline = _file_paused_seconds(read_control(_S.run_dir))
    _S.bound_at = time.time()


def unbind() -> None:
    global _S
    _S = _State()


def bound_run_dir() -> Optional[Path]:
    return _S.run_dir


def _read_control() -> Dict[str, Any]:
    if _S.run_dir is None:
        return {}
    now = time.monotonic()
    if now - _S.last_read < 0.2:
        return _S.control
    _S.last_read = now
    path = _S.run_dir / CONTROL_FILENAME
    try:
        st = path.stat()
    except OSError:
        _S.control = {}
        return _S.control
    # Each write replaces the file, so (time, size, inode) changes even within
    # the file system's timestamp resolution.
    mtime = (st.st_mtime_ns, st.st_size, st.st_ino)
    if mtime != _S.last_mtime:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return _S.control  # caught mid-write: read it again next time
        _S.control = data if isinstance(data, dict) else {}
        _S.last_mtime = mtime
    return _S.control


def _file_paused_seconds(control: Dict[str, Any]) -> float:
    """Paused seconds recorded by the control file, including a pause still running.

    The file keeps its own account (the backend adds each finished pause and
    stamps when the current one began), so a pause counts in full however late
    the agent first looks -- even one that ended before it looked.
    """
    total = float(control.get("paused_total_seconds") or 0.0)
    if str(control.get("state") or "") == "paused" and control.get("paused_at_epoch"):
        total += max(0.0, time.time() - float(control["paused_at_epoch"]))
    return total


def is_paused() -> bool:
    return str(_read_control().get("state") or "") == "paused"


def paused_seconds() -> float:
    """Total time this mission has been paused by the operator (including now)."""
    if _S.run_dir is None:
        return 0.0
    return max(0.0, _file_paused_seconds(_read_control()) - _S.baseline)


def work_clock() -> float:
    """``time.monotonic()`` that stands still while the operator has the agent paused."""
    return time.monotonic() - paused_seconds()


def notes(limit: int = 8) -> List[str]:
    """The operator's recent hints (advisory context for the planner)."""
    if _S.run_dir is None:
        return []
    rows = agent_chat.iter_operator_messages(_S.run_dir)
    return [str(r.get("text") or "") for r in rows[-max(1, int(limit)):] if r.get("text")]


def _acknowledge_hints(phase: str) -> None:
    now = time.monotonic()
    if _S.run_dir is None or now - _S.last_hint_check < 1.0:
        return
    _S.last_hint_check = now
    rows = agent_chat.iter_operator_messages(_S.run_dir)
    for row in rows[_S.hints_seen:]:
        agent_chat.say(
            f"📝 Got your note: “{str(row.get('text') or '')[:200]}”. I'll give it to the planner as a hint "
            "(input.json stays the source of every value).", kind="ack", phase=phase)
    _S.hints_seen = len(rows)


async def checkpoint(*, phase: str = "", where: str = "") -> Dict[str, Any]:
    """A safe point: pick up new hints; wait here while the operator has paused the agent."""
    if _S.run_dir is None:
        return {"paused": False}
    try:
        _acknowledge_hints(phase)
    except Exception:
        pass
    if not is_paused():
        return {"paused": False}
    where_text = f" before {where}" if where else ""
    agent_chat.say(f"⏸ Paused{where_text}. Say “resume” to continue.", kind="paused", phase=phase)
    started = time.monotonic()
    while is_paused():
        await asyncio.sleep(_S.poll_seconds)
    seconds = round(time.monotonic() - started, 1)
    agent_chat.say(f"▶ Resuming{where_text} (paused {seconds}s)", kind="resumed", phase=phase)
    return {"paused": True, "seconds": seconds}


def _write_row(run_dir: str | Path, row: Dict[str, Any]) -> None:
    import os

    target = Path(run_dir) / CONTROL_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    text = json.dumps(row, indent=1)
    temp.write_text(text, encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(temp, target)  # the agent never reads half a file
            return
        except PermissionError:
            # Windows: the mission has the file open for a moment.
            time.sleep(0.05 * (attempt + 1))
    target.write_text(text, encoding="utf-8")
    try:
        temp.unlink()
    except OSError:
        pass


def write_control(run_dir: str | Path, state: str, *, by: str = "chat") -> Dict[str, Any]:
    """Backend side: set ``running`` / ``paused`` for the mission of ``run_dir``.

    The file also keeps the pause account: when the current pause began and the
    total of the finished ones.
    """
    from .models import utc_now

    previous = read_control(run_dir)
    was_paused = str(previous.get("state") or "") == "paused"
    now = time.time()
    total = float(previous.get("paused_total_seconds") or 0.0)
    paused_at = previous.get("paused_at_epoch") if was_paused else None
    if state == "paused" and not was_paused:
        paused_at = now
    elif state != "paused" and was_paused:
        total += max(0.0, now - float(previous.get("paused_at_epoch") or now))
        paused_at = None
    row = {**previous, "schema_version": "hip.operator-control.v1", "state": str(state), "at": utc_now(), "by": str(by),
           "paused_at_epoch": paused_at, "paused_total_seconds": round(total, 3)}
    _write_row(run_dir, row)
    return row


def confirm_complete(run_dir: str | Path, phase: str, *, text: str = "", by: str = "chat") -> Dict[str, Any]:
    """Backend side: the operator confirms ``phase`` is filled correctly (V243R36)."""
    from .models import utc_now
    from .security import mask_sensitive_string

    previous = read_control(run_dir)
    confirmations = dict(previous.get("confirmations") or {})
    confirmations[str(phase)] = {"at": utc_now(), "epoch": time.time(), "by": str(by),
                                 "text": mask_sensitive_string(str(text or ""))[:300]}
    row = {"schema_version": "hip.operator-control.v1", "state": "running", **previous, "confirmations": confirmations}
    _write_row(run_dir, row)
    return confirmations[str(phase)]


def confirmed_complete(phase: str) -> Optional[Dict[str, Any]]:
    """Agent side: the operator confirmed ``phase`` during this mission."""
    if _S.run_dir is None or not phase:
        return None
    row = (_read_control().get("confirmations") or {}).get(str(phase))
    if not isinstance(row, dict) or float(row.get("epoch") or 0.0) < _S.bound_at - 1.0:
        return None
    return row


def confirmed_proof(phase: str, proof: Dict[str, Any]) -> Dict[str, Any]:
    """A read-only proof, completed by the operator's confirmation where it falls short (V243R36).

    Only a proof of a form that is on screen (controls were read) is completed;
    a field the portal itself flags invalid is never overridden.  What the agent
    could not read back is listed as ``operator_confirmed_fields``.
    """
    if not isinstance(proof, dict) or proof.get("pass") is True:
        return proof
    confirmation = confirmed_complete(phase)
    if not confirmation:
        return proof
    unread = [*(proof.get("missing_fields") or []), *(proof.get("row_issue_fields") or [])]
    upload = proof.get("required_upload_proof") or {}
    if upload and upload.get("pass") is False:
        unread.extend(upload.get("missing_fields") or ["upload"])
    unread = list(dict.fromkeys(str(x) for x in unread if str(x)))
    refused = ""
    if proof.get("invalid_fields"):
        refused = (f"the portal flags {', '.join(str(x) for x in proof['invalid_fields'])}. Fix it on screen "
                   "(or tell me what is wrong); I keep working on it")
    elif int(proof.get("actual_control_count") or 0) <= 0:
        refused = "the form of this phase is not on screen. I keep working on it"
    elif len(unread) > _S.max_confirmed_unread:
        # Many values are still not on the form: the confirmation is most likely
        # early.  Keep filling; the confirmation stays valid.
        names = ", ".join(unread[:8]) + (f" and {len(unread) - 8} more" if len(unread) > 8 else "")
        refused = (f"{len(unread)} input.json values are not on the form yet ({names}). I keep filling them and "
                   "finish on your confirmation once only values I can't read back remain")
    if refused:
        key = str(phase)
        if _S.refusal_said.get(key) != refused:
            _S.refusal_said[key] = refused
            agent_chat.say(f"I can't finish this phase on your confirmation yet: {refused}.", kind="warn", phase=key)
        return dict(proof, operator_confirmation_refused=refused)
    out = dict(proof)
    out.update({
        "pass": True, "deterministic_pass": True, "status": "exact_live_state_operator_confirmed",
        "missing_fields": [], "row_issue_fields": [],
        "required_upload_proof": dict(upload, **{"pass": True, "operator_confirmed": upload.get("pass") is False}) if upload else upload,
        "operator_confirmed_fields": sorted(set(str(x) for x in unread)),
        "operator_confirmation": confirmation,
    })
    key = str(phase)
    if key not in _S.confirm_said:
        _S.confirm_said.add(key)
        names = ", ".join(out["operator_confirmed_fields"]) or "nothing"
        agent_chat.say(
            f"✅ You confirmed this phase is correct. I proved every other value myself; "
            f"recorded as confirmed by you: {names}.", kind="complete", phase=key)
    return out


def confirmation_refusal(phase: str) -> str:
    """Why the operator's confirmation of ``phase`` could not finish it (said in the chat), or ""."""
    return _S.refusal_said.get(str(phase), "")


def read_control(run_dir: str | Path) -> Dict[str, Any]:
    try:
        data = json.loads((Path(run_dir) / CONTROL_FILENAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
