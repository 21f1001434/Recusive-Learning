from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string

SCHEMA = "hip.interactive-teaching.v1"


def _stable(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(x or "") for x in parts).encode("utf-8", errors="ignore")).hexdigest()[:24]


def _read(path: Path) -> Dict[str, Any]:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return row if isinstance(row, dict) else {}
    except Exception:
        return {}


def _safe_url(url: str) -> str:
    try:
        p = urlsplit(str(url or ""))
        # Persist only route structure, never query/fragment values.
        return f"{p.scheme}://{p.netloc}{p.path}" if p.scheme and p.netloc else p.path
    except Exception:
        return ""


def _clean_label(row: Mapping[str, Any]) -> str:
    for key in ("ariaLabel", "aria_label", "text", "label", "name"):
        value = str(row.get(key) or "").strip()
        if value:
            return mask_sensitive_string(value)[:240]
    return ""


class InteractiveTeachingStore:
    """Human demonstration memory for HIP navigation and form interaction.

    A teaching session records semantic click/navigation/control structure only.
    Customer-entered values, selectors and screen coordinates are never promoted
    into long-term teaching memory.  A captured demonstration becomes trusted only
    after the live phase is exact-verified and the human accepts the result.
    """

    def __init__(self, root: str | Path, config: Any = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.trajectories_path = self.root / "interactive_trajectories.jsonl"
        self.config = config

    def start(self, *, run_id: str, phase: str, task: str = "", note: str = "") -> Dict[str, Any]:
        sid = _stable("interactive-teaching", run_id, phase, utc_now())
        row = {
            "schema_version": SCHEMA,
            "session_id": sid,
            "run_id": str(run_id or ""),
            "phase": str(phase or ""),
            "task": mask_sensitive_string(str(task or ""))[:500],
            "note": mask_sensitive_string(str(note or ""))[:500],
            "status": "recording",
            "started_at": utc_now(),
            "finished_at": "",
            "captured_at": "",
            "validated": False,
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
            "instruction": "Navigate and click through the live HIP page normally. Finish teaching when the demonstrated path is complete.",
        }
        safe_write_json(self.sessions_dir / f"{sid}.json", row)
        return row

    def get(self, session_id: str) -> Dict[str, Any]:
        return _read(self.sessions_dir / f"{session_id}.json")

    def active(self, *, run_id: str = "", phase: str = "") -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for p in sorted(self.sessions_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            row = _read(p)
            if row.get("status") not in {"recording", "capture_requested"}:
                continue
            if run_id and str(row.get("run_id") or "") != str(run_id):
                continue
            if phase and str(row.get("phase") or "") != str(phase):
                continue
            rows.append(row)
        return rows

    def finish(self, *, session_id: str, note: str = "") -> Dict[str, Any]:
        path = self.sessions_dir / f"{session_id}.json"
        row = _read(path)
        if not row:
            raise ValueError(f"Unknown interactive teaching session: {session_id}")
        if row.get("status") == "validated":
            return row
        row["status"] = "capture_requested"
        row["finished_at"] = utc_now()
        if note:
            row["finish_note"] = mask_sensitive_string(str(note))[:500]
        safe_write_json(path, row)
        return row

    def _compile_steps(self, *, session: Mapping[str, Any], clicks: Sequence[Mapping[str, Any]], current_url: str = "") -> List[Dict[str, Any]]:
        started = str(session.get("started_at") or "")
        finished = str(session.get("finished_at") or "")
        filtered: List[Mapping[str, Any]] = []
        for c in clicks:
            if not isinstance(c, Mapping):
                continue
            ts = str(c.get("timestamp") or "")
            if started and ts and ts < started:
                continue
            if finished and ts and ts > finished:
                continue
            if bool(c.get("safety_blocked")):
                continue
            filtered.append(c)

        out: List[Dict[str, Any]] = []
        previous_url = ""
        for c in filtered:
            url = _safe_url(str(c.get("url") or ""))
            if url and url != previous_url:
                out.append({
                    "type": "navigate_observed",
                    "target_route": url,
                    "value_source": "live_portal_route",
                    "human_demonstrated": True,
                })
                previous_url = url
            label = _clean_label(c)
            role = str(c.get("role") or "").strip()
            tag = str(c.get("tag") or "").strip().lower()
            if not (label or role or tag):
                continue
            out.append(mask_sensitive_data({
                "type": "semantic_click",
                "label": label,
                "role": role,
                "tag": tag,
                "action": "click",
                "human_demonstrated": True,
                "value_source": "none",
            }))
        final_route = _safe_url(current_url)
        if final_route and final_route != previous_url:
            out.append({"type": "navigate_observed", "target_route": final_route, "value_source": "live_portal_route", "human_demonstrated": True})
        # Compact adjacent duplicate actions caused by event bubbling/re-rendering.
        compact: List[Dict[str, Any]] = []
        for step in out:
            if compact and step == compact[-1]:
                continue
            compact.append(step)
        return compact[:300]

    def capture(
        self,
        *,
        session_id: str,
        clicks: Sequence[Mapping[str, Any]],
        current_url: str = "",
        dom_events: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        path = self.sessions_dir / f"{session_id}.json"
        row = _read(path)
        if not row:
            raise ValueError(f"Unknown interactive teaching session: {session_id}")
        steps = self._compile_steps(session=row, clicks=clicks, current_url=current_url)
        row["status"] = "captured"
        row["captured_at"] = utc_now()
        row["step_count"] = len(steps)
        row["semantic_steps"] = steps
        row["dom_event_count"] = len(list(dom_events or []))
        row["values_stored"] = False
        row["selectors_stored"] = False
        row["coordinates_stored"] = False
        safe_write_json(path, row)
        return row

    def validate(self, *, session_id: str, exact_pass: bool, human_pass: bool, judge_pass: bool, run_id: str = "") -> Dict[str, Any]:
        path = self.sessions_dir / f"{session_id}.json"
        row = _read(path)
        if not row:
            return {}
        trusted = bool(exact_pass and human_pass and judge_pass and row.get("semantic_steps"))
        row["validated"] = trusted
        row["status"] = "validated" if trusted else "captured_unvalidated"
        row["validated_at"] = utc_now() if trusted else ""
        row["exact_pass"] = bool(exact_pass)
        row["human_pass"] = bool(human_pass)
        row["judge_pass"] = bool(judge_pass)
        safe_write_json(path, row)
        if trusted:
            with self.trajectories_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps(mask_sensitive_data({
                    "schema_version": "hip.interactive-teaching-trajectory.v1",
                    "session_id": session_id,
                    "run_id": str(run_id or row.get("run_id") or ""),
                    "phase": row.get("phase"),
                    "validated_at": row.get("validated_at"),
                    "semantic_steps": row.get("semantic_steps") or [],
                    "values_stored": False,
                    "selectors_stored": False,
                    "coordinates_stored": False,
                }), ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return row

    def manifest(self) -> Dict[str, Any]:
        sessions = [_read(p) for p in self.sessions_dir.glob("*.json")]
        return {
            "schema_version": SCHEMA,
            "enabled": True,
            "session_count": len(sessions),
            "active_count": sum(1 for x in sessions if x.get("status") in {"recording", "capture_requested"}),
            "validated_count": sum(1 for x in sessions if x.get("validated")),
            "root": str(self.root),
            "values_stored": False,
            "selectors_stored": False,
            "coordinates_stored": False,
        }


async def capture_pending_interactive_teaching(store: InteractiveTeachingStore, browser: Any, *, run_id: str, phase: str) -> List[Dict[str, Any]]:
    """Capture any finished demonstration from the live borrowed BrowserSession."""
    if browser is None or getattr(browser, "page", None) is None:
        return []
    captured: List[Dict[str, Any]] = []
    for session in store.active(run_id=run_id, phase=phase):
        if session.get("status") != "capture_requested":
            continue
        try:
            clicks = await browser.page.evaluate("() => Array.from(window.__HIP_CLICK_LOG || [])")
        except Exception:
            clicks = []
        try:
            dom = await browser.collect_dom_event_window({"event_seq": 0, "mutation_seq": 0}, clear=False)
            dom_events = dom.get("events") or []
        except Exception:
            dom_events = []
        try:
            current_url = str(browser.page.url or "")
        except Exception:
            current_url = ""
        captured.append(store.capture(session_id=str(session.get("session_id") or ""), clicks=clicks or [], current_url=current_url, dom_events=dom_events))
    return captured


def interactive_teaching_from_config(app_config: Any) -> InteractiveTeachingStore:
    base = Path(app_config.reporting.memory_dir) / str(getattr(app_config.human_in_the_loop, "memory_subdir", "human_teaching") or "human_teaching")
    return InteractiveTeachingStore(base / "interactive_demonstrations", app_config.human_in_the_loop)
