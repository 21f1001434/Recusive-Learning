"""Bounded, state-based no-progress watchdog for live HIP phases.

The watchdog intentionally observes only structural browser-state fingerprints. It
never stores DOM text or form values. A new, previously unseen structural state is
progress; toggling forever among already-seen states is not. This lets the mission
controller interrupt loops such as repeatedly expanding/collapsing Data Map rows
while still allowing legitimate multi-step form filling to proceed.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from .safe_io import safe_write_json
from .security import mask_sensitive_string


class PhaseNoProgressError(RuntimeError):
    """Raised when a live phase stops making structural browser progress."""

    def __init__(self, code: str, payload: Dict[str, Any]):
        self.code = str(code)
        self.payload = dict(payload)
        super().__init__(f"{self.code}: {payload.get('message') or 'phase made no structural progress'}")


async def run_with_progress_watchdog(
    operation: Awaitable[Any],
    *,
    phase: str,
    marker_provider: Callable[[], Awaitable[Dict[str, Any]]],
    checkpoint_provider: Callable[[], Any],
    evidence_path: Optional[str | Path] = None,
    no_progress_seconds: float = 90.0,
    poll_seconds: float = 5.0,
    recent_signature_limit: int = 12,
    blocking_wait_seconds: Optional[float] = None,
) -> Any:
    """Run ``operation`` while requiring new structural state within a time bound.

    A signature only counts as progress when it has not been seen in the recent
    structural-state window. Therefore A->B->A->B UI cycling does not reset the
    watchdog. The marker itself is expected to contain hashes/counters only.

    While the marker reports a *blocking portal loader* the portal, not the
    agent, is busy: the watchdog then allows ``blocking_wait_seconds`` (the
    loading budget) before it stops the attempt, and reports
    ``HIP_PORTAL_LOADING_STUCK`` so recovery refreshes the page and, if that is
    not enough, restarts the browser instead of treating it as an agent stall.
    """
    task = asyncio.ensure_future(operation)
    started = time.monotonic()
    last_novel = started
    recent: list[str] = []
    samples: list[Dict[str, Any]] = []
    marker_errors: list[str] = []
    limit = max(2, int(recent_signature_limit or 12))
    threshold = max(0.05, float(no_progress_seconds or 90.0))
    poll = max(0.01, float(poll_seconds or 5.0))

    async def sample() -> Dict[str, Any]:
        try:
            row = dict(await marker_provider() or {})
            clean = {
                "signature": str(row.get("signature") or ""),
                "route": str(row.get("route") or ""),
                "action_count": int(row.get("action_count") or 0),
                "successful_fill_count": int(row.get("successful_fill_count") or 0),
                "successful_click_count": int(row.get("successful_click_count") or 0),
                "dom_transition_count": int(row.get("dom_transition_count") or 0),
                "executor_progress": str(row.get("executor_progress") or ""),
                "blocking_loader": bool(row.get("blocking_loader")),
            }
            samples.append(clean)
            if len(samples) > 20:
                del samples[:-20]
            return clean
        except Exception as exc:  # observation failure must not crash the operation
            marker_errors.append(mask_sensitive_string(str(exc))[:500])
            if len(marker_errors) > 10:
                del marker_errors[:-10]
            return {}

    # Executor heartbeats: each new field/retry/completion the form executor
    # starts is progress even when the page only revisits known states (a
    # dropdown retried, or model decisions that change nothing on screen).
    seen_executor_tokens: set[str] = set()
    max_fills = -1

    def executor_progressed(row: Dict[str, Any]) -> bool:
        nonlocal max_fills
        novel = False
        token = str(row.get("executor_progress") or "")
        if token and token not in seen_executor_tokens:
            seen_executor_tokens.add(token)
            novel = True
        fills = int(row.get("successful_fill_count") or 0)
        if fills > max_fills:
            novel = novel or max_fills >= 0
            max_fills = fills
        return novel

    loader_wait = max(threshold, float(blocking_wait_seconds or 0.0))
    loader_since: Optional[float] = None

    first = await sample()
    if first.get("signature"):
        recent.append(first["signature"])
    executor_progressed(first)
    if first.get("blocking_loader"):
        loader_since = started

    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll)
            if task in done:
                return await task

            row = await sample()
            sig = str(row.get("signature") or "")
            now = time.monotonic()
            loader_blocking = bool(row.get("blocking_loader"))
            fills_before = max_fills
            heartbeat = executor_progressed(row)
            if loader_blocking:
                # Behind a blocking portal loader nothing the agent does can
                # change the form: retry heartbeats and loader re-renders are not
                # progress; only a newly committed field is.
                if int(row.get("successful_fill_count") or 0) > fills_before >= 0:
                    last_novel = now
                if sig and sig not in recent:
                    recent.append(sig)
                    if len(recent) > limit:
                        del recent[:-limit]
            else:
                if sig and sig not in recent:
                    last_novel = now
                    recent.append(sig)
                    if len(recent) > limit:
                        del recent[:-limit]
                if heartbeat:
                    last_novel = now
            if loader_blocking:
                loader_since = loader_since if loader_since is not None else now
            else:
                loader_since = None

            no_progress_for = now - last_novel
            if no_progress_for < (loader_wait if loader_blocking else threshold):
                continue

            checkpoint: Dict[str, Any]
            try:
                maybe_checkpoint = checkpoint_provider()
                if inspect.isawaitable(maybe_checkpoint):
                    maybe_checkpoint = await maybe_checkpoint
                checkpoint = dict(maybe_checkpoint or {})
            except Exception as exc:
                checkpoint = {"pass": False, "error": mask_sensitive_string(str(exc))[:500]}
            exact = checkpoint.get("pass") is True
            loader_stuck = bool(loader_blocking and not exact)
            code = (
                "HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL" if exact
                else "HIP_PORTAL_LOADING_STUCK" if loader_stuck
                else "HIP_PHASE_NO_PROGRESS_WATCHDOG"
            )
            payload = {
                "schema_version": "hip.phase-no-progress-watchdog.v1",
                "phase": str(phase),
                "code": code,
                "exact_completion_checkpoint_pass": bool(exact),
                "elapsed_seconds": round(now - started, 3),
                "no_progress_seconds": round(no_progress_for, 3),
                "configured_no_progress_seconds": threshold,
                "poll_seconds": poll,
                "recent_unique_signature_count": len(set(recent)),
                "executor_progress_units": len(seen_executor_tokens),
                "last_executor_progress": (samples[-1].get("executor_progress") if samples else ""),
                "blocking_loader": loader_blocking,
                "blocking_loader_seconds": round(now - loader_since, 3) if loader_since is not None else 0.0,
                "configured_blocking_wait_seconds": loader_wait,
                "recent_samples": samples[-8:],
                "marker_errors": marker_errors[-5:],
                "operation_cancelled": True,
                "message": (
                    "Exact phase state was already proven, but post-completion work stopped making progress; "
                    "cancel reporting/exploration and continue to read-only verification."
                    if exact else
                    f"The Dell portal's loading indicator stayed blocking for {round(now - (loader_since or now))}s; "
                    "refresh the page, and restart the browser if it is still loading, then resume the phase from input.json."
                    if loader_stuck else
                    "The live HIP phase produced no new structural browser state within the bounded interval; "
                    "cancel this attempt and enter deterministic recovery instead of continuing model reasoning."
                ),
            }
            if evidence_path:
                safe_write_json(Path(evidence_path), payload)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise PhaseNoProgressError(code, payload)
    except BaseException:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        raise


def progress_units(marker: Optional[Dict[str, Any]]) -> int:
    """Loop-proof progress count from a phase progress marker (V243R22)."""
    row = dict(marker or {})
    if "progress_units" in row:
        return int(row.get("progress_units") or 0)
    return int(row.get("verified_node_count") or 0) + int(row.get("successful_fill_count") or 0)


async def run_with_progress_budget(
    operation: Awaitable[Any],
    *,
    phase: str,
    budget_seconds: float,
    marker_provider: Callable[[], Awaitable[Dict[str, Any]]],
    extend: Callable[[int], Dict[str, Any]],
    evidence_path: Optional[str | Path] = None,
) -> Any:
    """``asyncio.wait_for`` for a phase attempt, except progress earns time.

    The phase wall budget exists so a stuck phase cannot hold the browser for
    hours.  A slow but advancing form is not stuck: when the deadline arrives
    and the attempt has verified new fields since the last deadline, ``extend``
    is asked for more time (it grants a bounded number of extensions).  With no
    new progress, or no extension left, the attempt is cancelled and
    ``asyncio.TimeoutError`` is raised exactly as before.
    """
    task = asyncio.ensure_future(operation)

    async def units() -> int:
        try:
            return progress_units(await marker_provider())
        except Exception:
            return -1

    deadline = time.monotonic() + max(1.0, float(budget_seconds or 0.0))
    last_units = await units()
    extensions: list[Dict[str, Any]] = []
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=max(0.01, deadline - time.monotonic()))
            if task in done:
                return await task
            now_units = await units()
            granted: Dict[str, Any] = {}
            if now_units > last_units >= 0:
                granted = dict(extend(now_units - last_units) or {})
            record = {
                "schema_version": "hip.phase-progress-budget.v1",
                "phase": str(phase),
                "progress_units_since_last_deadline": (now_units - last_units) if last_units >= 0 else None,
                "progress_units": now_units,
                "extension": granted,
                "extensions": extensions + ([granted] if granted.get("granted") else []),
            }
            if granted.get("granted"):
                extensions.append(granted)
                last_units = now_units
                deadline = time.monotonic() + float(granted.get("seconds") or 0.0)
                record["decision"] = "extended_for_verified_progress"
                if evidence_path:
                    safe_write_json(Path(evidence_path), record)
                continue
            record["decision"] = "stopped_no_new_progress" if now_units <= last_units else "stopped_extensions_exhausted"
            if evidence_path:
                safe_write_json(Path(evidence_path), record)
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            if record["decision"] == "stopped_no_new_progress":
                # Same recovery class as the structural watchdog: reopen from
                # input.json, refresh, restart the browser -- then a human.
                raise asyncio.TimeoutError(
                    "HIP_PHASE_NO_PROGRESS_WATCHDOG: no new verified field before the phase wall budget "
                    f"(HIP_PHASE_WALL_BUDGET, {len(extensions)} progress extension(s) used)"
                )
            raise asyncio.TimeoutError(
                f"HIP_PHASE_WALL_BUDGET timed out after {len(extensions)} progress extension(s)"
            )
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
