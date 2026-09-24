from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from .security import mask_sensitive_data



# Original pathlib methods are retained so the global safe path patch can call
# the real implementation without recursion.
_ORIGINAL_PATH_OPEN = Path.open
_ORIGINAL_PATH_MKDIR = Path.mkdir
_SAFE_PATH_IO_INSTALLED = False


def install_safe_path_io() -> None:
    """Install process-wide safe ``Path.open`` *and* ``Path.mkdir`` guards.

    HIP produces evidence under deeply nested run directories.  On Windows +
    OneDrive the failure can occur while creating the directory itself (WinError
    206) *before* a protected writer is reached.  Patching both primitives keeps
    legacy call-sites safe without requiring every flow to be rewritten.
    """
    global _SAFE_PATH_IO_INSTALLED
    if _SAFE_PATH_IO_INSTALLED:
        return

    def _safe_open(self: Path, mode: str = "r", buffering: int = -1, encoding: str | None = None, errors: str | None = None, newline: str | None = None):
        write_mode = any(flag in mode for flag in ("w", "a", "x", "+"))
        if write_mode:
            try:
                ensure_parent(self)
            except Exception:
                # Preserve original behavior if parent creation fails for a real reason.
                pass
        try:
            return _ORIGINAL_PATH_OPEN(self, mode=mode, buffering=buffering, encoding=encoding, errors=errors, newline=newline)
        except (FileNotFoundError, OSError):
            if os.name != "nt":
                raise
            # Retry using Windows long-path prefix.  Binary mode cannot receive
            # encoding/errors/newline arguments.
            long_path = _nt_long_path(self)
            if "b" in mode:
                return open(long_path, mode=mode, buffering=buffering)
            return open(long_path, mode=mode, buffering=buffering, encoding=encoding, errors=errors, newline=newline)


    def _safe_mkdir(self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False):
        try:
            return _ORIGINAL_PATH_MKDIR(self, mode=mode, parents=parents, exist_ok=exist_ok)
        except OSError:
            if os.name != "nt":
                raise
            target = _nt_long_path(self)
            if parents:
                os.makedirs(target, mode=mode, exist_ok=exist_ok)
                return None
            try:
                os.mkdir(target, mode=mode)
            except FileExistsError:
                if not exist_ok:
                    raise
            return None

    Path.open = _safe_open  # type: ignore[assignment]
    Path.mkdir = _safe_mkdir  # type: ignore[assignment]
    _SAFE_PATH_IO_INSTALLED = True

def _nt_long_path(path: Path) -> str:
    """Return a Windows long-path string for paths that may exceed MAX_PATH.

    This is intentionally a no-op on non-Windows platforms.  On corporate Windows
    images, Python can raise FileNotFoundError for deep OneDrive workspaces even
    when the parent directory exists because the fully qualified path is >260 chars.
    """
    p = Path(path)
    if os.name != "nt":
        return str(p)
    try:
        resolved = str(p.resolve())
    except Exception:
        resolved = str(p.absolute())
    if resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def safe_mkdir(path: str | Path, *, parents: bool = True, exist_ok: bool = True, mode: int = 0o777) -> str:
    """Create a directory safely on deep Windows/OneDrive paths.

    This helper is intentionally usable before ``install_safe_path_io`` is called
    and is therefore also used by the global ``Path.mkdir`` patch itself.
    """
    target = Path(path)
    try:
        _ORIGINAL_PATH_MKDIR(target, mode=mode, parents=parents, exist_ok=exist_ok)
    except OSError:
        if os.name != "nt":
            raise
        long_target = _nt_long_path(target)
        if parents:
            os.makedirs(long_target, mode=mode, exist_ok=exist_ok)
        else:
            try:
                os.mkdir(long_target, mode=mode)
            except FileExistsError:
                if not exist_ok:
                    raise
    return str(target)


def ensure_parent(path: str | Path) -> None:
    parent = Path(path).parent
    safe_mkdir(parent, parents=True, exist_ok=True)


def compact_path_component(value: Any, *, max_len: int = 32, fallback: str = "item") -> str:
    """Return a filesystem-safe, deterministic compact component.

    Long human labels remain available in JSON evidence; only the physical path
    component is shortened.  A hash suffix prevents collisions after truncation.
    """
    import hashlib
    import re

    raw = str(value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(" .-_") or fallback
    if len(safe) <= max_len:
        return safe
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:8]
    head = safe[: max(1, max_len - 9)].rstrip(" .-_") or fallback[: max(1, max_len - 9)]
    return f"{head}-{digest}"[:max_len]


def safe_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> str:
    p = Path(path)
    ensure_parent(p)
    try:
        p.write_text(text, encoding=encoding)
    except (FileNotFoundError, OSError):
        if os.name != "nt":
            raise
        with open(_nt_long_path(p), "w", encoding=encoding, newline="") as f:
            f.write(text)
    return str(p)


def safe_write_bytes(path: str | Path, data: bytes) -> str:
    p = Path(path)
    ensure_parent(p)
    try:
        p.write_bytes(data)
    except (FileNotFoundError, OSError):
        if os.name != "nt":
            raise
        with open(_nt_long_path(p), "wb") as f:
            f.write(data)
    return str(p)


def safe_write_json(path: str | Path, data: Any, *, mask: bool = True) -> str:
    payload = mask_sensitive_data(data) if mask else data
    return safe_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

def safe_write_csv(path: str | Path, fieldnames: list[str], rows: list[dict[str, Any]], *, extrasaction: str = "ignore") -> str:
    """Safely write CSV files in deep Windows/OneDrive workspaces.

    Some corporate Windows environments throw FileNotFoundError for deep paths
    even when the parent exists. This helper mirrors safe_write_json/text and
    falls back to the Windows long-path prefix.
    """
    p = Path(path)
    ensure_parent(p)

    def _write(handle) -> None:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction=extrasaction)
        writer.writeheader()
        for row in rows or []:
            writer.writerow({k: (row.get(k, "") if isinstance(row, dict) else "") for k in fieldnames})

    try:
        with p.open("w", newline="", encoding="utf-8") as f:
            _write(f)
    except (FileNotFoundError, OSError):
        if os.name != "nt":
            raise
        with open(_nt_long_path(p), "w", newline="", encoding="utf-8") as f:
            _write(f)
    return str(p)

