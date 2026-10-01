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
from contextlib import suppress
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from . import agent_chat, operator_control
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
    completion_probe: Optional[Callable[[], Any]] = None,
    refill_probe_seconds: float = 120.0,
    refill_loop_seconds: float = 600.0,
    live_map_seconds: float = 0.0,
    post_complete_fill_seconds: float = 30.0,
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

    V243R32 refill guard: filling fields that are already filled produces new
    screens and executor heartbeats, so it looked like progress for ever.  The
    loop-proof ``progress_units`` (distinct fields verified / filled / clicked)
    decide instead: once they stop growing for ``refill_probe_seconds`` the
    live form is probed read-only (``completion_probe``, never while a dropdown
    is open); two exact probes in a row stop the attempt as complete.  When they
    stop growing for ``refill_loop_seconds`` the attempt is a refill loop and is
    stopped (exact -> complete, otherwise the stall recovery ladder).

    V243R34 live map: with ``live_map_seconds`` the probe (the live input.json
    map) also runs on that cadence, so the Control Center sees the form fill in
    real time.  A form that stays complete while fields keep being filled again
    for ``post_complete_fill_seconds`` is stopped as complete; a complete form
    that is only finishing its checks (no more fills) is left to finish.
    """
    task = asyncio.ensure_future(operation)
    started = operator_control.work_clock()
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
                "progress_units": (int(row.get("progress_units") or 0) if "progress_units" in row else None),
                "fill_complete": bool(row.get("fill_complete")),
                "whitelabel_error": dict(row.get("whitelabel_error") or {}) if row.get("whitelabel_error") else None,
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
    # V243R32 refill guard state.
    probe_after = max(poll, float(refill_probe_seconds or 0.0)) if refill_probe_seconds else None
    refill_limit = max(threshold, float(refill_loop_seconds or 0.0)) if refill_loop_seconds else None
    best_units = -1
    units_since = started
    exact_probes = 0
    probes: list[Dict[str, Any]] = []
    live_every = max(poll, float(live_map_seconds or 0.0)) if live_map_seconds else None
    last_probe_at = started
    complete_since: Optional[float] = None
    fills_at_complete = 0

    def units_progressed(row: Dict[str, Any], now: float) -> None:
        nonlocal best_units, units_since, exact_probes
        units = row.get("progress_units")
        if units is None:
            return
        if int(units) > best_units:
            best_units = int(units)
            units_since = now
            exact_probes = 0

    first = await sample()
    if first.get("signature"):
        recent.append(first["signature"])
    executor_progressed(first)
    units_progressed(first, started)
    if first.get("blocking_loader"):
        loader_since = started

    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll)
            if task in done:
                return await task

            row = await sample()
            sig = str(row.get("signature") or "")
            now = operator_control.work_clock()
            if row.get("whitelabel_error"):
                # V243R32: the portal replaced the page with a Whitelabel Error Page --
                # nothing on it can be filled; the stage restarts in a fresh browser.
                from .environment_faults import WHITELABEL_CODE, whitelabel_message

                payload = {
                    "schema_version": "hip.phase-no-progress-watchdog.v1", "phase": str(phase),
                    "code": WHITELABEL_CODE, "stop_reason": "whitelabel_error_page",
                    "whitelabel_error": row.get("whitelabel_error"),
                    "elapsed_seconds": round(now - started, 3), "operation_cancelled": True,
                    "message": whitelabel_message(row.get("whitelabel_error") or {}, f"{phase} (watchdog)").split(": ", 1)[1],
                }
                if evidence_path:
                    safe_write_json(Path(evidence_path), payload)
                agent_chat.say("⚠ The portal replaced the page with a Whitelabel Error Page — stopping this attempt",
                               kind="warn", phase=str(phase))
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise PhaseNoProgressError(WHITELABEL_CODE, payload)
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

            units_progressed(row, now)
            stop_reason = ""
            refill_idle = now - units_since
            if not loader_blocking and best_units >= 0:
                idle_probe_due = probe_after is not None and refill_idle >= probe_after
                live_probe_due = live_every is not None and now - last_probe_at >= live_every
                if refill_limit is not None and refill_idle >= refill_limit:
                    stop_reason = "refill_loop"
                elif completion_probe is not None and (idle_probe_due or live_probe_due):
                    last_probe_at = now
                    try:
                        probe = completion_probe()
                        if inspect.isawaitable(probe):
                            probe = await probe
                        probe = dict(probe or {})
                    except Exception as exc:
                        probe = {"pass": False, "status": "probe_error", "error": mask_sensitive_string(str(exc))[:200]}
                    exact_probes = exact_probes + 1 if probe.get("pass") is True else 0
                    fills_now = int(row.get("successful_fill_count") or 0)
                    if probe.get("pass") is True:
                        if complete_since is None:
                            complete_since, fills_at_complete = now, fills_now
                    elif str(probe.get("status") or "") != "busy":
                        complete_since = None
                    probes.append({"at_seconds": round(now - started, 3), "pass": probe.get("pass") is True,
                                   "status": str(probe.get("status") or "")[:80]})
                    if len(probes) > 10:
                        del probes[:-10]
                    if exact_probes >= 2 and idle_probe_due:
                        stop_reason = "input_json_complete"
                    elif (exact_probes >= 2 and complete_since is not None and fills_now > fills_at_complete
                          and now - complete_since >= max(0.0, float(post_complete_fill_seconds or 0.0))):
                        stop_reason = "filled_again_after_complete"

            no_progress_for = now - last_novel
            if not stop_reason and no_progress_for < (loader_wait if loader_blocking else threshold):
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
                "stop_reason": stop_reason or ("loader" if loader_stuck else "no_new_state"),
                "seconds_without_new_field": round(refill_idle, 3),
                "progress_units": best_units,
                "completion_probes": probes[-5:],
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
                    "Every input.json value is filled and committed on the live form; the agent was still filling "
                    "fields that were already filled -- stopped filling and continuing to read-only verification."
                    if exact and stop_reason else
                    "Exact phase state was already proven, but post-completion work stopped making progress; "
                    "cancel reporting/exploration and continue to read-only verification."
                    if exact else
                    f"Fields were filled again for {round(refill_idle)}s without a new field being verified "
                    "(a refill loop); stop this attempt and enter deterministic recovery."
                    if stop_reason == "refill_loop" else
                    f"The Dell portal's loading indicator stayed blocking for {round(now - (loader_since or now))}s; "
                    "refresh the page, and restart the browser if it is still loading, then resume the phase from input.json."
                    if loader_stuck else
                    "The live HIP phase produced no new structural browser state within the bounded interval; "
                    "cancel this attempt and enter deterministic recovery instead of continuing model reasoning."
                ),
            }
            if evidence_path:
                safe_write_json(Path(evidence_path), payload)
            agent_chat.say(
                ("🛑 Every input.json value is on the form — I stop filling and go on to verification"
                 if exact else "⚠ " + str(payload["message"])[:300]),
                kind="complete" if exact else "stop", phase=str(phase))
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
    checkpoint_provider: Optional[Callable[[], Any]] = None,
    finalize_seconds: float = 600.0,
    max_finalize_extensions: int = 2,
) -> Any:
    """``asyncio.wait_for`` for a phase attempt, except progress earns time.

    The phase wall budget exists so a stuck phase cannot hold the browser for
    hours.  A slow but advancing form is not stuck: when the deadline arrives
    and the attempt has verified new fields since the last deadline, ``extend``
    is asked for more time (it grants a bounded number of extensions).  With no
    new progress, or no extension left, the attempt is cancelled and
    ``asyncio.TimeoutError`` is raised exactly as before.

    V243R25: an attempt whose form is already completely filled and verified
    (``fill_complete`` in the marker) is finishing its read-back / evidence work:
    it gets ``finalize_seconds`` more (at most ``max_finalize_extensions`` times)
    instead of being cancelled as "no new verified field".  Before any stop, the
    live form is re-proved read-only (``checkpoint_provider``); when it is exact
    the stop is reported as ``HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL`` so the
    mission continues to the judges instead of reopening a blank form.
    """
    task = asyncio.ensure_future(operation)
    last_marker: Dict[str, Any] = {}

    async def units() -> int:
        nonlocal last_marker
        try:
            last_marker = dict(await marker_provider() or {})
            return progress_units(last_marker)
        except Exception:
            return -1

    finalize_used: list[Dict[str, Any]] = []

    deadline = operator_control.work_clock() + max(1.0, float(budget_seconds or 0.0))
    last_units = await units()
    extensions: list[Dict[str, Any]] = []
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=max(0.01, deadline - operator_control.work_clock()))
            if task in done:
                return await task
            if operator_control.work_clock() < deadline:
                # V243R35: the operator paused the agent; paused time does not count.
                continue
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
                deadline = operator_control.work_clock() + float(granted.get("seconds") or 0.0)
                record["decision"] = "extended_for_verified_progress"
                agent_chat.say("⏱ Still verifying new fields — this attempt gets more time", kind="info", phase=str(phase))
                if evidence_path:
                    safe_write_json(Path(evidence_path), record)
                continue
            if bool(last_marker.get("fill_complete")) and len(finalize_used) < max(0, int(max_finalize_extensions)):
                # Every field is filled and verified: the attempt is finishing
                # (read-back, judges' evidence) -- let it finish.
                grant = {"granted": True, "seconds": float(finalize_seconds), "reason": "form complete; finishing verification"}
                finalize_used.append(grant)
                last_units = now_units
                deadline = operator_control.work_clock() + float(finalize_seconds)
                record.update(decision="extended_to_finish_verification", finalize_extensions=list(finalize_used))
                if evidence_path:
                    safe_write_json(Path(evidence_path), record)
                continue
            record["decision"] = "stopped_no_new_progress" if now_units <= last_units else "stopped_extensions_exhausted"
            checkpoint: Dict[str, Any] = {}
            if checkpoint_provider is not None:
                # Re-prove the live form read-only before giving up on it.
                try:
                    maybe = checkpoint_provider()
                    if inspect.isawaitable(maybe):
                        maybe = await maybe
                    checkpoint = dict(maybe or {})
                except Exception as exc:
                    checkpoint = {"pass": False, "error": mask_sensitive_string(str(exc))[:300]}
            record["exact_completion_checkpoint_pass"] = checkpoint.get("pass") is True
            if evidence_path:
                safe_write_json(Path(evidence_path), record)
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            if checkpoint.get("pass") is True:
                raise asyncio.TimeoutError(
                    "HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL: the form is exact (read-only proof) but the attempt "
                    "did not finish within the phase wall budget; continue to verification without reopening the form"
                )
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
