"""V243R35: the live agent chat -- what the agent is doing, as it does it.

Every concrete step of a mission becomes one line of ``runs/<run>/agent_chat.jsonl``:
the page it opens, each click, the value it types or picks in which field (masked
when sensitive), every field it verifies or fails, the live input.json count,
self-heal steps (refresh, browser restart), phase start and finish.  The file is
append-only and has one writer (the mission process), so a line costs one small
append -- nothing is re-read or rewritten.

The operator's side of the conversation (questions, pause / resume / stop, hints,
review answers) is written by the backend to ``operator_chat.jsonl``; the agent
reads it at safe points (see :mod:`hip_id_agent.operator_control`).

This is observability, not reasoning: it records only what was done on the page
and what was verified.  Selectors, coordinates and secrets are never written.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from .models import utc_now
from .security import is_secret_target, mask_sensitive_data, mask_sensitive_string

CHAT_FILENAME = "agent_chat.jsonl"
OPERATOR_FILENAME = "operator_chat.jsonl"
CONTROL_FILENAME = "operator_control.json"
FRAME_DIRNAME = "agent_chat"
FRAME_FILENAME = "live_frame.jpg"
SCHEMA_VERSION = "hip.agent-chat.v1"
MASK = "***MASKED***"


def _clean(value: Any, limit: int = 300) -> str:
    return mask_sensitive_string(re.sub(r"\s+", " ", str(value or "")).strip())[:limit]


def humanize(name: Any) -> str:
    """``source_transport_profile`` / ``profileName`` -> ``Source Transport Profile`` / ``Profile Name``."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name or ""))
    text = re.sub(r"[_\-.]+", " ", text).strip()
    return " ".join(w if w.isupper() and len(w) > 1 else w.capitalize() for w in text.split())


def display_value(target: Any, value: Any, limit: int = 90) -> str:
    """A value as the chat shows it: quoted, short, masked when it is a secret."""
    if value is None or value == "" or value == []:
        return "“”"
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    if is_secret_target(str(target or "")) or is_secret_target(str(value)):
        return MASK
    text = _clean(value, 400)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return f"“{text}”"


def field_name(node: Mapping[str, Any]) -> str:
    name = humanize(node.get("field_key") or str(node.get("input_path") or "").split(".")[-1] or "field")
    row = node.get("row_index")
    if row is not None and row != "":
        try:
            name += f" (row {int(row) + 1})"
        except (TypeError, ValueError):
            pass
    return name


def _control_label(control: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(control, Mapping):
        return ""
    kind = str(control.get("type") or control.get("role") or "").lower()
    # A radio / checkbox is named by its question ("Existing Account"), not its option ("Yes").
    group = control.get("group_label") if kind in {"radio", "checkbox", "switch"} else ""
    label = _clean(group or control.get("label") or control.get("aria_label") or control.get("name") or "", 120)
    return label.rstrip(" *").strip()


class AgentChatFeed:
    """Append-only chat log of one run (``runs/<run>/agent_chat.jsonl``)."""

    def __init__(self, run_dir: str | Path, *, run_id: str = "", enabled: bool = True) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / CHAT_FILENAME
        self.run_id = str(run_id or self.run_dir.name)
        self.enabled = bool(enabled)
        self._lock = threading.Lock()
        self._last_key: Tuple[str, ...] = ()
        self._last_at = 0.0
        self._seq = 0
        try:
            with self.path.open("rb") as handle:
                self._seq = sum(1 for _ in handle)
        except OSError:
            self._seq = 0

    def post(self, text: Any, *, kind: str = "info", role: str = "agent", phase: str = "",
             group: str = "", detail: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        clean = _clean(text, 700)
        if not clean:
            return {}
        key = (role, kind, phase, clean)
        now = time.monotonic()
        with self._lock:
            # The same line again within a few seconds (a dropdown re-opened, a
            # repeated heartbeat) adds nothing to the conversation.
            if key == self._last_key and now - self._last_at < 3.0:
                return {}
            self._last_key, self._last_at = key, now
            self._seq += 1
            row: Dict[str, Any] = {"seq": self._seq, "at": utc_now(), "role": str(role), "kind": str(kind),
                                   "phase": str(phase or ""), "text": clean}
            if group:
                row["group"] = str(group)
            if detail:
                row["detail"] = mask_sensitive_data(dict(detail))
            try:
                self.run_dir.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            except OSError:
                return {}
        return row


# ---------------------------------------------------------------- active feed
# One mission runs per process; the mission activates its run's feed so that the
# form executor and the action broker (which only see the page) can narrate.
_ACTIVE: Optional[AgentChatFeed] = None


def activate(feed: Optional[AgentChatFeed]) -> Optional[AgentChatFeed]:
    global _ACTIVE
    _ACTIVE = feed
    return feed


def deactivate(feed: Optional[AgentChatFeed] = None) -> None:
    global _ACTIVE
    if feed is None or _ACTIVE is feed:
        _ACTIVE = None


def active_feed() -> Optional[AgentChatFeed]:
    return _ACTIVE


def say(text: Any, *, kind: str = "info", role: str = "agent", phase: str = "", group: str = "",
        detail: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Post to the active run's chat; a no-op outside a mission.  Never raises."""
    feed = _ACTIVE
    if feed is None:
        return {}
    try:
        return feed.post(text, kind=kind, role=role, phase=phase, group=group, detail=detail)
    except Exception:
        return {}


# ------------------------------------------------------- form executor hooks
def _field_context(page: Any) -> Dict[str, Any]:
    ctx = getattr(page, "_hip_chat_field", None)
    return ctx if isinstance(ctx, dict) else {}


def executor_step(page: Any, *, phase: str, node: Mapping[str, Any], stage: str, retry: int = 0) -> None:
    """Field lifecycle from the form executor: start / attempt / done / failed."""
    if _ACTIVE is None:
        return
    try:
        node_id = str(node.get("node_id") or "")
        ctx = _field_context(page)
        if stage == "start" or ctx.get("node_id") != node_id:
            ctx = {"node_id": node_id, "phase": phase, "label": field_name(node),
                   "field_key": str(node.get("field_key") or ""), "value": node.get("expected_value")}
            setattr(page, "_hip_chat_field", ctx)
        if stage == "start":
            return
        label = ctx.get("label") or field_name(node)
        value = display_value(ctx.get("field_key") or label, ctx.get("value"))
        if stage == "attempt" and int(retry or 0) > 0:
            say(f"Retrying {label} (try {int(retry) + 1})", kind="retry", phase=phase)
        elif stage == "done":
            say(f"✓ {label} = {value} — verified on the form", kind="verified", phase=phase)
        elif stage == "failed":
            last = getattr(page, "_hip_last_broker_error", None)
            why = _clean((last or {}).get("error"), 160) if isinstance(last, Mapping) else ""
            say(f"✗ {label} did not take {value}" + (f" ({why})" if why else ""), kind="failed", phase=phase)
        if stage in {"done", "failed"}:
            # V243R38: later clicks (an opener, the next phase) are not this field's.
            setattr(page, "_hip_chat_field", None)
    except Exception:
        pass


def field_ready(page: Any, *, phase: str, node: Mapping[str, Any], control: Optional[Mapping[str, Any]],
                already: bool = False) -> None:
    """The executor found (or did not find) the live control for an input.json value."""
    if _ACTIVE is None:
        return
    try:
        ctx = _field_context(page)
        if ctx.get("node_id") != str(node.get("node_id") or ""):
            executor_step(page, phase=phase, node=node, stage="start")
            ctx = _field_context(page)
        label = _control_label(control) or ctx.get("label") or field_name(node)
        row = node.get("row_index")
        if _control_label(control) and row not in (None, ""):
            try:
                label += f" (row {int(row) + 1})"
            except (TypeError, ValueError):
                pass
        ctx["label"] = label
        value = display_value(node.get("field_key") or label, node.get("expected_value"))
        if control is None:
            if node.get("required"):
                say(f"Looking for {label} — it is not on the screen yet", kind="warn", phase=phase)
            return
        if already:
            say(f"✓ {label} already shows {value}", kind="verified", phase=phase, group="already")
        else:
            say(f"Filling {label} with {value}", kind="field", phase=phase)
    except Exception:
        pass


# ------------------------------------------------------------- broker hooks
_PRESS_TEXT = {
    "native select home": "Moved to the first {field} option",
    "native select next option": "Moving through the {field} options",
    "native select commit": "Pressed Enter to choose the {field} option",
}


def broker_action(page: Any, *, action: str, label: str, success: bool, value: Any = None, error: Any = None) -> None:
    """One physical click / type / key press by the governed action broker."""
    if _ACTIVE is None:
        return
    try:
        ctx = _field_context(page)
        field = ctx.get("label") or "the form"
        # V243R38: without a field context (a listing-era helper clicked) the phase
        # is still the session's current one.
        phase = str(ctx.get("phase") or getattr(getattr(page, "_hip_browser_session", None), "_active_phase_name", "") or "")
        lab = _clean(label, 200)
        low = lab.lower()
        shown = display_value(ctx.get("field_key") or field, value) if value not in (None, "") else ""
        kind, group = "click", ""
        if " option " in low and (low.startswith("hip portal dds option") or low.startswith("hip portal multi-select option")):
            option = lab.split(" option ", 1)[1].strip()
            multi = "multi-select" in low
            text = (f"Ticked “{option}” in {field}" if multi else f"Selected “{option}” in {field}")
            kind = "select"
        elif low.startswith("hip portal radio "):
            text = f"Chose “{lab[17:].strip()}” for {field}"
            kind = "select"
        elif low.startswith("hip portal checkbox "):
            text = f"Ticked “{lab[20:].strip()}”" + (f" in {field}" if field != "the form" else "")
            kind = "select"
        elif low.startswith("hip portal boolean control"):
            desired = lab.split("->", 1)[-1].strip().lower()
            text = f"Switched {field} {'on' if desired in {'true', 'yes', 'on', '1'} else 'off'}"
            kind = "select"
        elif low.startswith("hip portal tab "):
            text = f"Switched to the “{lab[15:].strip()}” tab"
        elif "search" in low and action in {"search", "fill", "type"}:
            text = f"Typed {shown} to search {field}"
            kind = "type"
        elif low in {"hip portal dds combobox", "hip portal native dropdown", "hip portal dds multi-select"}:
            text = f"Opened the {field} dropdown" if field != "the form" else "Opened a dropdown on the form"
        elif low.startswith("hip portal text field") or (action in {"fill", "type"} and not lab):
            text = f"Typed {shown} into {field}"
            kind = "type"
        elif action == "press":
            text = _PRESS_TEXT.get(low, "Pressed a key in {field}").format(field=field)
            kind, group = "key", "keys"
        elif action in {"fill", "type"}:
            text = f"Typed {shown} into {lab}"
            kind = "type"
        else:
            target = lab if lab and not low.startswith("hip portal") else field
            text = f"Clicked {target}" if target == field else f"Clicked “{target}”"
            if target != field and field != "the form":
                text += f" for {field}"
        if not success:
            why = _clean(error, 160)
            text = "✗ Could not do it: " + text[0].lower() + text[1:] + (f" ({why})" if why else "")
            kind = "failed"
        say(text, kind=kind, phase=phase, group=group)
    except Exception:
        pass


# ------------------------------------------------------- browser session hooks
_SELECTOR_NAME = re.compile(
    r"""(?:name|formcontrolname|aria-label|placeholder|data-testid|id)\s*[=*^$~|]?=\s*["']?([^"'\]]+)""", re.I)


def _target_text(target: str) -> str:
    """A readable name for a session action target (often a selector)."""
    t = str(target or "").strip()
    if not t:
        return ""
    for prefix in ("text=", "role=", "label=", "placeholder="):
        if t.lower().startswith(prefix):
            t = t[len(prefix):]
            m = re.search(r"""name\s*=\s*["']?([^"'\]]+)""", t)
            return _clean(m.group(1) if m else t.strip("\"'"), 120)
    m = _SELECTOR_NAME.search(t)
    if m:
        return humanize(m.group(1)) if re.fullmatch(r"[A-Za-z0-9_\-]+", m.group(1)) else _clean(m.group(1), 120)
    if t.startswith("#") and re.fullmatch(r"#[A-Za-z0-9_\-]+", t):
        return humanize(t[1:])
    if any(ch in t for ch in "[]>#=:/()") or t.startswith((".", "//", "xpath")):
        return ""
    return _clean(t, 120)


def _short_url(url: str) -> str:
    try:
        parts = urlsplit(str(url or ""))
        if not parts.netloc:
            return _clean(url, 120)
        path = parts.path.rstrip("/") or "/"
        return _clean(f"{parts.netloc}{path}", 160)
    except Exception:
        return _clean(url, 120)


def session_action(event: Any, *, phase: str = "", label: str = "", in_broker: bool = False) -> None:
    """A BrowserSession action that the broker did not already narrate."""
    if _ACTIVE is None or in_broker:
        return
    try:
        raw = asdict(event) if is_dataclass(event) else dict(event) if isinstance(event, Mapping) else dict(getattr(event, "__dict__", {}) or {})
        typ = str(raw.get("type") or "")
        ok = bool(raw.get("success", True))
        target = _clean(label, 120) or _target_text(str(raw.get("target") or ""))
        value = raw.get("value_redacted")
        if typ in {"screenshot", "extract"}:
            return
        if typ == "navigate":
            text, kind = f"Opened {_short_url(raw.get('page_url_after') or raw.get('target'))}", "navigate"
        elif typ == "refresh":
            text, kind = "Refreshed the page", "heal"
        elif typ == "wait":
            if str(raw.get("target") or "") != "portal_ready":
                return
            text, kind = "Waiting for the portal to finish loading", "wait"
        elif typ == "click":
            text, kind = (f"Clicked “{target}”" if target else "Clicked a control"), "click"
        elif typ in {"fill", "type"}:
            shown = MASK if raw.get("was_secret") else display_value(target, value)
            text, kind = (f"Typed {shown} into “{target}”" if target else f"Typed {shown}"), "type"
        elif typ == "search":
            text, kind = f"Searched for {display_value(target, value)}" + (f" in “{target}”" if target else ""), "type"
        elif typ == "press":
            text, kind = f"Pressed {_clean(value, 30) or 'a key'}" + (f" in “{target}”" if target else ""), "key"
        elif typ == "verify":
            text, kind = f"Checked {target or 'the page'}", "observe"
        else:
            text, kind = f"{humanize(typ)} {target}".strip(), "info"
        if not ok:
            why = _clean(raw.get("error"), 160)
            text = "✗ " + text + (f" — failed ({why})" if why else " — failed")
            kind = "failed"
        say(text, kind=kind, phase=phase)
    except Exception:
        pass


# --------------------------------------------------------------- reading
def _read_lines(path: Path, offset: int, *, tail: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    rows: List[Dict[str, Any]] = []
    try:
        size = path.stat().st_size
    except OSError:
        return rows, 0
    if offset > size:
        offset = 0
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return rows, offset
    end = data.rfind(b"\n")
    if end < 0:
        return rows, offset
    for line in data[: end + 1].splitlines():
        try:
            row = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    if tail and len(rows) > tail:
        rows = rows[-tail:]
    return rows, offset + end + 1


def read_chat(run_dir: str | Path, *, cursor: str = "", tail: int = 300) -> Dict[str, Any]:
    """Messages after ``cursor`` (``"<agent offset>.<operator offset>"``) from both sides, in time order."""
    run_dir = Path(run_dir)
    agent_offset = operator_offset = 0
    first = not cursor
    if cursor:
        try:
            a, o = str(cursor).split(".", 1)
            agent_offset, operator_offset = max(0, int(a)), max(0, int(o))
        except ValueError:
            first = True
    agent_rows, agent_end = _read_lines(run_dir / CHAT_FILENAME, agent_offset, tail=tail if first else 0)
    operator_rows, operator_end = _read_lines(run_dir / OPERATOR_FILENAME, operator_offset, tail=tail if first else 0)
    for row in agent_rows:
        row.setdefault("source", "agent")
        row["id"] = f"a{row.get('seq')}"
    for row in operator_rows:
        row.setdefault("source", "operator")
        row["id"] = f"o{row.get('seq')}"
    messages = sorted(agent_rows + operator_rows, key=lambda r: (str(r.get("at") or ""), r["id"]))
    if first and tail and len(messages) > tail:
        messages = messages[-tail:]
    return {"messages": messages, "cursor": f"{agent_end}.{operator_end}"}


def append_operator(run_dir: str | Path, text: Any, *, role: str = "user", kind: str = "message",
                    detail: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """One operator-side line (the user's message or the Control Center's reply)."""
    path = Path(run_dir) / OPERATOR_FILENAME
    clean = _clean(text, 1200)
    seq = 0
    try:
        with path.open("rb") as handle:
            seq = sum(1 for _ in handle)
    except OSError:
        seq = 0
    row: Dict[str, Any] = {"seq": seq + 1, "at": utc_now(), "role": role, "kind": kind, "text": clean}
    if detail:
        row["detail"] = mask_sensitive_data(dict(detail))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row


def last_agent_messages(run_dir: str | Path, limit: int = 5) -> List[Dict[str, Any]]:
    path = Path(run_dir) / CHAT_FILENAME
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - 64_000))
            data = handle.read()
    except OSError:
        return []
    rows = []
    for line in data.splitlines()[-max(1, limit) * 3:]:
        try:
            row = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        if isinstance(row, dict) and row.get("role") == "agent":
            rows.append(row)
    return rows[-limit:]


def iter_operator_messages(run_dir: str | Path, *, kinds: Iterable[str] = ("hint",)) -> List[Dict[str, Any]]:
    wanted = set(kinds)
    rows, _ = _read_lines(Path(run_dir) / OPERATOR_FILENAME, 0)
    return [r for r in rows if r.get("role") == "user" and str(r.get("kind") or "") in wanted]


async def live_frame_loop(get_page: Any, run_dir: str | Path, *, seconds: float = 3.0, quality: int = 55) -> None:
    """A JPEG of the browser every few seconds for the chat panel (``agent_chat/live_frame.jpg``).

    Read-only: a CDP screenshot, no input, no injected style (``caret="initial"``),
    so the page and the agent's DOM observer see nothing.  Written to a temp file
    and swapped in, so the Control Center never reads half a frame.
    """
    import asyncio
    import os

    folder = Path(run_dir) / FRAME_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / FRAME_FILENAME
    temp = folder / "live_frame.tmp.jpg"
    while True:
        try:
            page = get_page()
        except Exception:
            page = None
        closed = True
        try:
            closed = page is None or bool(page.is_closed())
        except Exception:
            closed = page is None
        if not closed:
            try:
                await page.screenshot(path=str(temp), type="jpeg", quality=int(quality), caret="initial",
                                      animations="allow", scale="css", timeout=4000)
                os.replace(temp, target)
            except Exception:
                pass
        await asyncio.sleep(max(0.5, float(seconds or 3.0)))
