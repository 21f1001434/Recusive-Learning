"""Portal/browser failures that no per-field retry can fix (V243R18).

A refreshed page (the unsaved form is gone), a loading indicator that never
clears, or an expired login blocks every field alike.  Such errors must end
the phase attempt so the mission's recovery ladder (refresh, then browser
restart) can act, instead of being turned into "this field failed" and retried
field by field against a blocked page.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

WHITELABEL_CODE = "HIP_WHITELABEL_ERROR_PAGE"

ENVIRONMENT_FATAL_CODES = (
    "HIP_VISION_LOADING_REFRESH_REPLAY_REQUIRED",
    "HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH",
    "HIP_PORTAL_STALE_OVERLAY_UNRECOVERED_FORM_PRESERVED",
    "HIP_PORTAL_LOADING_STUCK",
    "HIP_AUTH_SESSION_EXPIRED",
    # V243R28: a dropdown that keeps listing no values although the fields above
    # it hold theirs -- the portal's lists failed in this browser session; only
    # closing and reopening the browser helps.
    "HIP_DROPDOWN_OPTIONS_EMPTY",
    # V243R32: the portal answered with Spring Boot's "Whitelabel Error Page"
    # instead of the page -- the form is gone; close and reopen the browser, open
    # the same phase link and the form again, and fill it from input.json.
    WHITELABEL_CODE,
)


def is_environment_fatal(exc: BaseException) -> bool:
    text = str(exc)
    return any(code in text for code in ENVIRONMENT_FATAL_CODES)


def raise_if_environment_fatal(exc: BaseException) -> None:
    if is_environment_fatal(exc):
        raise exc


# Spring Boot's default error page ("Whitelabel Error Page ... This application
# has no explicit mapping for /error ... There was an unexpected error
# (type=Internal Server Error, status=500)").  Only its own wording counts.
WHITELABEL_JS = r"""() => {
  const title = String(document.title || '');
  const body = document.body ? String(document.body.innerText || document.body.textContent || '').slice(0, 6000) : '';
  const hit = /whitelabel\s+error\s+page/i.test(body) || /whitelabel\s+error/i.test(title)
    || /this application has no explicit mapping for \/error/i.test(body);
  if (!hit) return null;
  const status = (body.match(/status\s*=\s*(\d{3})/i) || [])[1] || '';
  const type = ((body.match(/type\s*=\s*([^,)]+)/i) || [])[1] || '').trim().slice(0, 60);
  return {whitelabel: true, status, type, title: title.slice(0, 80), url_path: String(location.pathname || '').slice(0, 160)};
}"""


async def whitelabel_error_on(page: Any) -> Optional[Dict[str, Any]]:
    """The Whitelabel Error Page shown in the page (or one of its frames), else None."""
    if page is None:
        return None
    frames = []
    try:
        frames = list(getattr(page, "frames", []) or [])
    except Exception:
        frames = []
    for target in [page, *[f for f in frames if f is not getattr(page, "main_frame", None)]]:
        try:
            found = await target.evaluate(WHITELABEL_JS)
        except Exception:
            continue
        # Only the detector's own answer counts (a page stub may answer anything).
        if isinstance(found, dict) and found.get("whitelabel") is True:
            return {k: v for k, v in found.items() if k != "whitelabel"}
    return None


def whitelabel_message(found: Dict[str, Any], where: str = "") -> str:
    status = str((found or {}).get("status") or "")
    kind = str((found or {}).get("type") or "")
    detail = ", ".join(x for x in (f"status {status}" if status else "", kind) if x)
    return (
        f"{WHITELABEL_CODE}: the portal showed a Whitelabel Error Page"
        + (f" ({detail})" if detail else "")
        + (f" at {where}" if where else "")
        + "; close and reopen the browser, open the same phase link, reopen the form and fill it from input.json"
    )


async def raise_if_whitelabel(page: Any, where: str = "") -> None:
    found = await whitelabel_error_on(page)
    if found:
        raise RuntimeError(whitelabel_message(found, where))
