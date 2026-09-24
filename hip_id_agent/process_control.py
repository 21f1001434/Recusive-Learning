from __future__ import annotations

import os
import signal
from typing import Any, Dict


def _psutil():
    try:
        import psutil  # type: ignore
        return psutil
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Pause/resume requires psutil. Install project requirements before using this control."
        ) from exc


def process_exists(pid: int | str | None) -> bool:
    try:
        numeric = int(pid or 0)
    except Exception:
        return False
    if numeric <= 0:
        return False
    try:
        psutil = _psutil()
        return bool(psutil.pid_exists(numeric) and psutil.Process(numeric).is_running())
    except Exception:
        try:
            if os.name != "nt":
                os.kill(numeric, 0)
                return True
        except OSError:
            return False
        return False


def pause_process(pid: int | str) -> Dict[str, Any]:
    """Suspend only the HIP Python controller, leaving Chrome interactive.

    This mirrors Browser-Use WebUI's human-in-the-loop pause semantics: the agent
    stops issuing actions, while the browser remains available for an operator to
    inspect or complete Dell SSO/manual recovery. Child Chrome/MCP processes are not
    suspended intentionally.
    """
    numeric = int(pid)
    psutil = _psutil()
    proc = psutil.Process(numeric)
    if not proc.is_running():
        return {"status": "not_running", "pid": numeric}
    try:
        status = str(proc.status()).lower()
        if "stopped" in status:
            return {"status": "already_paused", "pid": numeric}
    except Exception:
        pass
    proc.suspend()
    return {"status": "paused", "pid": numeric}


def resume_process(pid: int | str) -> Dict[str, Any]:
    numeric = int(pid)
    psutil = _psutil()
    proc = psutil.Process(numeric)
    if not proc.is_running():
        return {"status": "not_running", "pid": numeric}
    proc.resume()
    return {"status": "resumed", "pid": numeric}
