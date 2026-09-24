from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv


def _candidate_env_files(root: str | Path | None = None) -> List[Path]:
    """Return deterministic .env candidates, preserving explicit operator override first."""
    candidates: List[Path] = []
    explicit = str(os.getenv("HIP_ENV_FILE") or "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())

    if root is not None:
        candidates.append(Path(root).expanduser() / ".env")

    candidates.append(Path.cwd() / ".env")

    # The package root is a stable fallback for direct Python/uvicorn launches.
    package_root = Path(__file__).resolve().parents[1]
    candidates.append(package_root / ".env")

    deduped: List[Path] = []
    seen: set[str] = set()
    for item in candidates:
        try:
            key = str(item.resolve())
        except Exception:
            key = str(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def load_runtime_env(root: str | Path | None = None, *, override: bool = False) -> Dict[str, Any]:
    """Load the HIP runtime .env deterministically.

    Order:
      1. HIP_ENV_FILE (explicit external file)
      2. <root>/.env when a root is supplied
      3. current-working-directory/.env
      4. package-root/.env

    Existing process environment wins by default. This is important when the
    operator intentionally injects credentials through PowerShell/CI instead of
    a file.
    """
    loaded: List[str] = []
    checked: List[str] = []
    for path in _candidate_env_files(root):
        checked.append(str(path))
        try:
            if path.is_file():
                load_dotenv(dotenv_path=path, override=override)
                loaded.append(str(path))
        except Exception:
            # Environment loading must never crash the application. The normal
            # model/readiness checks will report missing values precisely.
            continue
    return {
        "loaded": bool(loaded),
        "loaded_files": loaded,
        "checked_files": checked,
        "explicit_env_file": str(os.getenv("HIP_ENV_FILE") or "").strip(),
    }


def configure_utf8_stdio() -> Dict[str, Any]:
    """Force Unicode-safe Python stdio on Windows/legacy code pages.

    HIP labels and model diagnostics contain Unicode punctuation.  A Windows
    cp1252 console must never crash a mission merely because a label contains a
    non-breaking hyphen or other valid Unicode character.
    """
    # Force the mission process to Unicode-safe stdio even when VS Code or a
    # parent shell exported a legacy Windows code page.  A HIP label containing
    # e.g. U+2011 (non-breaking hyphen) must never terminate the agent.
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "0"
    reconfigured: List[str] = []
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        fn = getattr(stream, "reconfigure", None)
        if callable(fn):
            try:
                fn(encoding="utf-8", errors="replace")
                reconfigured.append(name)
            except Exception:
                pass
    return {
        "PYTHONUTF8": os.environ.get("PYTHONUTF8"),
        "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING"),
        "PYTHONLEGACYWINDOWSSTDIO": os.environ.get("PYTHONLEGACYWINDOWSSTDIO"),
        "reconfigured": reconfigured,
    }
