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


_S = _State()


def bind(run_dir: str | Path, *, poll_seconds: float = 0.5) -> None:
    """Attach this process (one mission) to its run folder."""
    global _S
    _S = _State()
    _S.run_dir = Path(run_dir)
    _S.poll_seconds = max(0.05, float(poll_seconds or 0.5))
    # Hints and pauses from before this mission started belong to an earlier run.
    _S.hints_seen = len(agent_chat.iter_operator_messages(_S.run_dir))
    _S.baseline = _file_paused_seconds(read_control(_S.run_dir))


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


def write_control(run_dir: str | Path, state: str, *, by: str = "chat") -> Dict[str, Any]:
    """Backend side: set ``running`` / ``paused`` for the mission of ``run_dir``.

    The file also keeps the pause account: when the current pause began and the
    total of the finished ones.
    """
    import os

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
    row = {"schema_version": "hip.operator-control.v1", "state": str(state), "at": utc_now(), "by": str(by),
           "paused_at_epoch": paused_at, "paused_total_seconds": round(total, 3)}
    target = Path(run_dir) / CONTROL_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    text = json.dumps(row, indent=1)
    temp.write_text(text, encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(temp, target)  # the agent never reads half a file
            return row
        except PermissionError:
            # Windows: the mission has the file open for a moment.
            time.sleep(0.05 * (attempt + 1))
    target.write_text(text, encoding="utf-8")
    try:
        temp.unlink()
    except OSError:
        pass
    return row


def read_control(run_dir: str | Path) -> Dict[str, Any]:
    try:
        data = json.loads((Path(run_dir) / CONTROL_FILENAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
