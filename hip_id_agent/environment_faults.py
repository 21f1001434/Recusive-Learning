"""Portal/browser failures that no per-field retry can fix (V243R18).

A refreshed page (the unsaved form is gone), a loading indicator that never
clears, or an expired login blocks every field alike.  Such errors must end
the phase attempt so the mission's recovery ladder (refresh, then browser
restart) can act, instead of being turned into "this field failed" and retried
field by field against a blocked page.
"""
from __future__ import annotations

ENVIRONMENT_FATAL_CODES = (
    "HIP_VISION_LOADING_REFRESH_REPLAY_REQUIRED",
    "HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH",
    "HIP_PORTAL_STALE_OVERLAY_UNRECOVERED_FORM_PRESERVED",
    "HIP_PORTAL_LOADING_STUCK",
    "HIP_AUTH_SESSION_EXPIRED",
)


def is_environment_fatal(exc: BaseException) -> bool:
    text = str(exc)
    return any(code in text for code in ENVIRONMENT_FATAL_CODES)


def raise_if_environment_fatal(exc: BaseException) -> None:
    if is_environment_fatal(exc):
        raise exc
