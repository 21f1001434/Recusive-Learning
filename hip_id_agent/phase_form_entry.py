from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


AsyncLocatorFinder = Callable[[Any], Awaitable[Any]]
AsyncBoolProbe = Callable[[Any], Awaitable[bool]]
AsyncAddClicker = Callable[[Any, int], Awaitable[bool]]
AsyncAfterAdd = Callable[[Any], Awaitable[Dict[str, Any]]]


# HIP create experiences are drawers/wizards mounted on the listing route.
# Query/hash changes are allowed because the SPA may encode drawer state there,
# but a pathname/host change means the wrong Add/link was clicked.
IN_PAGE_CREATE_PHASES = {
    "data_map", "datamap",
    "document_type", "source_document_type", "target_document_type", "doctype",
    "rule", "rules",
    "transport_profile", "source_transport_profile", "target_transport_profile",
    "biz_flow", "bizflow",
}


async def same_page_add_candidate(page: Any, locator: Any) -> bool:
    """Return True only for a page-level Add that cannot navigate to another route.

    This is intentionally phase-agnostic so every HIP section uses the same
    structural contract: listing -> top-right + Add -> in-page form. Nested row,
    menu, dialog and form-local Add controls are rejected. Anchors are accepted
    only when their href preserves scheme/host/path (query/hash may change).
    """
    if locator is None:
        return False
    try:
        meta = await locator.evaluate(r"""
el => {
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim();
  const tag=(el.tagName||'').toLowerCase();
  const a=tag==='a' ? el : el.closest('a');
  const r=el.getBoundingClientRect();
  return {
    text: clean(el.innerText||el.textContent||''),
    aria: clean(el.getAttribute('aria-label')||''),
    title: clean(el.getAttribute('title')||''),
    tag,
    href: a ? (a.href||a.getAttribute('href')||'') : '',
    nested: !!el.closest('tbody tr,[role=row],[role=menu],[role=menuitem],.dds__menu,.dds__dropdown__list,form,[role=dialog],dds-drawer,.dds__drawer,.dds__modal,.modal-dialog'),
    chrome: !!el.closest('nav,header,footer,dds-pagination,.dds__pagination,#onetrust-consent-sdk,#onetrust-pc-sdk,#onetrust-banner-sdk'),
    x:r.x, y:r.y, w:r.width, h:r.height
  };
}
""")
        if not isinstance(meta, dict) or meta.get("nested") or meta.get("chrome"):
            return False
        labels = [str(meta.get(k) or "").strip() for k in ("text", "aria", "title")]
        if not any(re.fullmatch(r"\+?\s*Add", value, flags=re.I) for value in labels if value):
            return False
        href = str(meta.get("href") or "").strip()
        if href:
            try:
                current = str(getattr(page, "url", "") or "")
                target = urljoin(current, href)
                if not _same_route(current, target):
                    return False
            except Exception:
                return False
        return True
    except Exception:
        return False


async def find_same_page_top_right_add(page: Any) -> Any:
    """Find the page-level top-right + Add shared by all HIP listing sections."""
    try:
        candidates = page.locator("button, [role=button], a, dds-button, .dds__button")
        count = min(int(await candidates.count()), 300)
    except Exception:
        return None

    best: tuple[float, Any] | None = None
    for idx in range(count):
        loc = candidates.nth(idx)
        try:
            if not await loc.is_visible(timeout=300) or not await loc.is_enabled(timeout=300):
                continue
            if not await same_page_add_candidate(page, loc):
                continue
            meta = await loc.evaluate(r"""
el => {
  const r=el.getBoundingClientRect();
  const tag=(el.tagName||'').toLowerCase();
  const text=String(el.innerText||el.textContent||el.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();
  const vw=Math.max(document.documentElement.clientWidth||0,window.innerWidth||0,1);
  const vh=Math.max(document.documentElement.clientHeight||0,window.innerHeight||0,1);
  return {x:r.x,y:r.y,tag,text,vw,vh,inMain:!!el.closest('main,.app__content,.dds__container')};
}
""")
            x=float(meta.get("x") or 0); y=float(meta.get("y") or 0)
            vw=max(float(meta.get("vw") or 1),1.0); vh=max(float(meta.get("vh") or 1),1.0)
            score=0.0
            if str(meta.get("tag") or "") == "button": score += 60.0
            if str(meta.get("text") or "").lstrip().startswith("+"): score += 25.0
            if meta.get("inMain"): score += 20.0
            score += 50.0 * max(0.0,min(1.0,x/vw))
            score += 35.0 * max(0.0,min(1.0,1.0-y/vh))
            if best is None or score > best[0]:
                best=(score,loc)
        except Exception:
            continue
    return best[1] if best is not None else None


def _same_route(left: str, right: str) -> bool:
    """Compare only scheme/host/path; query/hash changes are allowed for in-page state."""
    try:
        a=urlsplit(str(left or "")); b=urlsplit(str(right or ""))
        ap=(a.path or "/").rstrip("/") or "/"
        bp=(b.path or "/").rstrip("/") or "/"
        return (a.scheme.lower(), a.netloc.lower(), ap.lower()) == (b.scheme.lower(), b.netloc.lower(), bp.lower())
    except Exception:
        return str(left or "").split("?",1)[0].split("#",1)[0].rstrip("/").lower() == str(right or "").split("?",1)[0].split("#",1)[0].rstrip("/").lower()

async def _safe_bool(probe: Optional[AsyncBoolProbe], page: Any) -> bool:
    if probe is None:
        return False
    try:
        return bool(await probe(page))
    except Exception:
        return False


async def _dom_generation(page: Any) -> int:
    try:
        return int(await page.evaluate("Number(window.__HIP_DOM_GENERATION || window.__HIP_DOM_EVENT_SEQ || 0)"))
    except Exception:
        return 0


async def _mcp_find_evidence(browser: Any, terms: List[str]) -> List[Dict[str, Any]]:
    """Collect browser_find evidence without using it as an executor.

    The physical action still goes through BrowserSession.click_and_wait, which
    performs semantic fusion, AutoWebGLM approval, AgentQ/MCP safety and effect
    verification. browser_find is used here as a ReAct observation so the loop
    can shrink the candidate space after a failed DOM lookup.
    """
    backend = getattr(browser, "playwright_mcp_backend", None)
    rows: List[Dict[str, Any]] = []
    if backend is None:
        return rows
    for term in terms:
        try:
            result = await backend.find(text=term)
            rows.append({
                "term": term,
                "ok": True,
                "text": str((result or {}).get("text") or "")[:3500],
                "fallback": bool((result or {}).get("fallback")),
            })
        except Exception as exc:
            rows.append({"term": term, "ok": False, "error": mask_sensitive_string(str(exc))[:1000]})
    return rows


async def _recovery_intelligence(browser: Any, *, phase: str, task: str, label: str = "+ Add") -> Dict[str, Any]:
    """Read-only multi-modal ReAct observation for a failed form-entry step.

    AutoWebGLM/Browser-Use/LangChain/Gemma are allowed to inspect and propose,
    but the deterministic expected intent remains authoritative.  No proposed
    selector/action is executed directly from this payload.
    """
    fn = getattr(browser, "browser_intelligence_recovery_context", None)
    if not callable(fn):
        return {"available": False, "reason": "browser intelligence recovery context unavailable"}
    try:
        result = await fn(
            task=task, include_autowebglm_proposal=True,
            expected_intent={
                "action": "click", "label": label, "phase": phase,
                "mutation_risk": False, "structural_opener": True,
                "goal": "open the active HIP create form and verify the new surface",
            },
        )
        return mask_sensitive_data(result if isinstance(result, dict) else {"result": result})
    except Exception as exc:
        return {"available": False, "error": mask_sensitive_string(str(exc))[:1200]}


async def _react_route_back(browser: Any, listing_url: str, *, phase: str) -> Dict[str, Any]:
    if not listing_url:
        return {"pass": False, "status": "no_listing_url"}
    try:
        fn = getattr(browser, "_react_ensure_target_surface", None)
        if callable(fn):
            result = await fn(listing_url, max_steps=3)
            return mask_sensitive_data(result if isinstance(result, dict) else {"pass": bool(result)})
    except Exception as exc:
        return {"pass": False, "status": "react_route_failed", "error": mask_sensitive_string(str(exc))[:1200]}
    try:
        await browser.navigate(listing_url)
        return {"pass": True, "status": "navigate_fallback"}
    except Exception as exc:
        return {"pass": False, "status": "navigate_failed", "error": mask_sensitive_string(str(exc))[:1200]}


async def ensure_phase_form_entry(
    *,
    page: Any,
    browser: Any,
    phase: str,
    listing_url: str,
    find_add: AsyncLocatorFinder,
    is_form_open: AsyncBoolProbe,
    evidence_dir: Optional[Path] = None,
    click_add: Optional[AsyncAddClicker] = None,
    after_add: Optional[AsyncAfterAdd] = None,
    is_intermediate_surface: Optional[AsyncBoolProbe] = None,
    max_steps: int = 4,
    settle_ms: int = 900,
    require_same_route: bool = False,
) -> Dict[str, Any]:
    """Bounded ReAct controller for opening the correct HIP create form.

    Standard HIP phases use, without changing pathname:
      listing -> top-right + Add -> active in-page create form

    BizFlow also remains on the same pathname:
      listing -> top-right + Add -> in-page template/card surface -> card link/action -> multi-tab form

    The loop is intentionally deterministic and safe.  It *observes* through the
    accessibility snapshot/browser_find plus DOM state, *plans* one structural
    action, dispatches through the normal BrowserSession semantic executor, then
    proves the expected surface before proceeding.  A failure is raised instead
    of silently returning an empty form, so the mission-level ReAct self-healer
    can reopen the phase and retry autonomously.
    """
    phase_n = str(phase or "").strip().lower()
    effective_same_route = bool(require_same_route or phase_n in IN_PAGE_CREATE_PHASES)
    audit: Dict[str, Any] = {
        "schema_version": "hip.phase-form-entry-react.v1",
        "phase": phase_n,
        "listing_url": str(listing_url or ""),
        "max_steps": max(1, int(max_steps)),
        "steps": [],
        "pass": False,
        "status": "starting",
        "require_same_route": effective_same_route,
    }

    async def persist() -> None:
        if evidence_dir is None:
            return
        try:
            Path(evidence_dir).mkdir(parents=True, exist_ok=True)
            safe_write_json(Path(evidence_dir) / "phase_form_entry_react.json", mask_sensitive_data(audit))
        except Exception:
            pass

    if await _safe_bool(is_form_open, page):
        if not effective_same_route or _same_route(str(getattr(page, "url", "") or ""), listing_url):
            audit.update({"pass": True, "status": "form_already_open", "attempts": 0})
            await persist()
            return audit
        audit["initial_route_drift"] = {
            "url": str(getattr(page, "url", "") or ""),
            "expected_listing_route": str(listing_url or ""),
        }
        audit["initial_route_recovery"] = await _react_route_back(browser, listing_url, phase=phase_n)
        await page.wait_for_timeout(settle_ms)

    for step_no in range(1, max(1, int(max_steps)) + 1):
        before_url = str(getattr(page, "url", "") or "")
        before_gen = await _dom_generation(page)
        form_before = await _safe_bool(is_form_open, page)
        intermediate_before = await _safe_bool(is_intermediate_surface, page)
        mcp_terms = ["+ Add", "Add"]
        if phase_n in {"biz_flow", "bizflow"}:
            mcp_terms.extend(["Create Biz Flow", "Flow Template"])
        mcp_find = await _mcp_find_evidence(browser, mcp_terms)
        step: Dict[str, Any] = {
            "step": step_no,
            "observe": {
                "url": before_url,
                "dom_generation": before_gen,
                "form_open": form_before,
                "intermediate_surface": intermediate_before,
                "browser_find": mcp_find,
            },
            "plan": "",
            "action": {},
            "effect": {},
        }
        audit["steps"].append(step)

        if effective_same_route and not _same_route(before_url, listing_url):
            step["plan"] = "recover_wrong_route_before_form_entry"
            step["action"] = {
                "kind": "wrong_route_detected",
                "url": before_url,
                "expected_listing_route": str(listing_url or ""),
            }
            step["recovery"] = await _react_route_back(browser, listing_url, phase=phase_n)
            await page.wait_for_timeout(settle_ms)
            step["effect"] = {
                "url": str(getattr(page, "url", "") or ""),
                "same_route": _same_route(str(getattr(page, "url", "") or ""), listing_url),
                "form_open": await _safe_bool(is_form_open, page),
                "dom_generation": await _dom_generation(page),
            }
            await persist()
            continue

        if form_before:
            audit.update({"pass": True, "status": "form_open", "attempts": step_no - 1})
            await persist()
            return audit

        # BizFlow may already be sitting on the template picker after a prior
        # partial attempt.  Continue from that state instead of clicking + Add a
        # second time.
        if intermediate_before and after_add is not None:
            step["plan"] = "launch_intermediate_form"
            try:
                launch = await after_add(page)
                step["action"] = {"kind": "after_add", "result": mask_sensitive_data(launch)}
            except Exception as exc:
                step["action"] = {"kind": "after_add", "error": mask_sensitive_string(str(exc))[:1200]}
            await page.wait_for_timeout(settle_ms)
            launched_url = str(getattr(page, "url", "") or "")
            route_drift = bool(effective_same_route and not _same_route(launched_url, listing_url))
            form_after = False if route_drift else await _safe_bool(is_form_open, page)
            step["effect"] = {
                "form_open": form_after,
                "intermediate_surface": False if route_drift else await _safe_bool(is_intermediate_surface, page),
                "dom_generation": await _dom_generation(page),
                "url": launched_url,
                "same_route": (not effective_same_route) or _same_route(launched_url, listing_url),
                "wrong_route_after_intermediate": route_drift,
            }
            await persist()
            if form_after:
                audit.update({"pass": True, "status": "form_open_after_intermediate_launch", "attempts": step_no})
                await persist()
                return audit
            if route_drift:
                step["wrong_intermediate_target"] = {
                    "reason": "in-page BizFlow/template launcher navigated away from the listing route",
                    "url": launched_url,
                    "expected_listing_route": str(listing_url or ""),
                }
            # A stale/wrong template surface gets one route reset before the next loop.
            step["recovery"] = await _react_route_back(browser, listing_url, phase=phase_n)
            await page.wait_for_timeout(settle_ms)
            continue

        # Standard state: locate the page-level + Add structural opener.
        add = None
        try:
            add = await find_add(page)
        except Exception as exc:
            step["find_add_error"] = mask_sensitive_string(str(exc))[:1200]

        if add is None:
            step["plan"] = "visual_reobserve_then_route_listing"
            step["action"] = {"kind": "add_not_found"}
            step["recovery_intelligence"] = await _recovery_intelligence(
                browser, phase=phase_n,
                task=f"HIP {phase_n}: the page-level + Add structural opener was not found. Re-observe the current page and explain where the top-right Add control or correct listing surface is, without clicking anything.",
            )

            # V228: when DOM/accessibility discovery cannot resolve a visibly
            # rendered page-level Add, use Gemma/browser-use perception to locate
            # it and PyAutoGUI MCP to click it physically. This is structural-only;
            # Save/Create/Delete/Deploy are never vision-coordinate actions.
            visual_result: Dict[str, Any] = {"pass": False, "reason": "visual_recovery_not_supported"}
            visual_click = getattr(browser, "click_visual_structural_target", None)
            if callable(visual_click):
                try:
                    visual_result = await visual_click(
                        label="+ Add",
                        phase=phase_n,
                        context=(
                            "Use the top-right page-level HIP section opener only. "
                            "Reject row-level Add, nested form Add, menus, pagination and route-changing links."
                        ),
                    )
                except Exception as exc:
                    visual_result = {"pass": False, "error": mask_sensitive_string(str(exc))[:1200]}
            step["visual_structural_recovery"] = mask_sensitive_data(visual_result)

            if visual_result.get("pass"):
                await page.wait_for_timeout(settle_ms)
                visual_url = str(getattr(page, "url", "") or "")
                visual_route_drift = bool(effective_same_route and not _same_route(visual_url, listing_url))
                visual_form = False if visual_route_drift else await _safe_bool(is_form_open, page)
                visual_intermediate = False if visual_route_drift else await _safe_bool(is_intermediate_surface, page)
                step["effect"] = {
                    "url": visual_url,
                    "form_open": visual_form,
                    "intermediate_surface": visual_intermediate,
                    "dom_generation": await _dom_generation(page),
                    "same_route": (not effective_same_route) or _same_route(visual_url, listing_url),
                    "wrong_route_after_visual_add": visual_route_drift,
                }
                await persist()
                if visual_route_drift:
                    step["recovery"] = await _react_route_back(browser, listing_url, phase=phase_n)
                    await page.wait_for_timeout(settle_ms)
                    continue
                if visual_form:
                    audit.update({"pass": True, "status": "form_open_after_visual_add", "attempts": step_no})
                    await persist()
                    return audit
                # BizFlow can land on the same-page template picker. Re-enter the
                # loop and the existing intermediate-surface path will launch it.
                if visual_intermediate:
                    continue

            step["recovery"] = await _react_route_back(browser, listing_url, phase=phase_n)
            await page.wait_for_timeout(settle_ms)
            step["effect"] = {
                "url": str(getattr(page, "url", "") or ""),
                "form_open": await _safe_bool(is_form_open, page),
                "intermediate_surface": await _safe_bool(is_intermediate_surface, page),
                "dom_generation": await _dom_generation(page),
            }
            await persist()
            continue

        step["plan"] = "click_page_add_structural_opener"
        clicked = False
        try:
            if click_add is not None:
                clicked = bool(await click_add(add, step_no))
            else:
                await browser.click_and_wait(
                    action=f"structural_opener phase_entry {phase_n} + Add",
                    locator=add,
                    selector=f"+ Add {phase_n}",
                    mutation_risk=False,
                )
                clicked = True
            step["action"] = {"kind": "click_add", "clicked": clicked}
        except Exception as exc:
            step["action"] = {
                "kind": "click_add",
                "clicked": False,
                "error": mask_sensitive_string(str(exc))[:1800],
            }
        await page.wait_for_timeout(settle_ms)

        current_url = str(getattr(page, "url", "") or "")
        route_drift_after_add = bool(effective_same_route and not _same_route(current_url, listing_url))
        form_after_add = False if route_drift_after_add else await _safe_bool(is_form_open, page)
        intermediate_after_add = False if route_drift_after_add else await _safe_bool(is_intermediate_surface, page)
        after_gen = await _dom_generation(page)
        step["effect"] = {
            "form_open": form_after_add,
            "intermediate_surface": intermediate_after_add,
            "dom_generation": after_gen,
            "dom_generation_changed": bool(after_gen != before_gen),
            "url": current_url,
            "same_route": (not effective_same_route) or _same_route(current_url, listing_url),
            "wrong_route_after_add": route_drift_after_add,
        }
        await persist()

        if route_drift_after_add:
            step["plan"] = "reject_navigating_add_and_restore_listing"
            step["wrong_add_target"] = {
                "reason": "in-page form phase navigated away after + Add",
                "url": current_url,
                "expected_listing_route": str(listing_url or ""),
            }
            step["recovery"] = await _react_route_back(browser, listing_url, phase=phase_n)
            await page.wait_for_timeout(settle_ms)
            await persist()
            continue

        if form_after_add:
            audit.update({"pass": True, "status": "form_open_after_add", "attempts": step_no})
            await persist()
            return audit

        if intermediate_after_add and after_add is not None:
            step["plan"] = "click_add_then_launch_intermediate_form"
            try:
                launch = await after_add(page)
                step["after_add"] = mask_sensitive_data(launch)
            except Exception as exc:
                step["after_add"] = {"error": mask_sensitive_string(str(exc))[:1800]}
            await page.wait_for_timeout(max(settle_ms, 1200))
            final_url = str(getattr(page, "url", "") or "")
            final_route_drift = bool(effective_same_route and not _same_route(final_url, listing_url))
            final_form = False if final_route_drift else await _safe_bool(is_form_open, page)
            step["effect_after_intermediate"] = {
                "form_open": final_form,
                "intermediate_surface": False if final_route_drift else await _safe_bool(is_intermediate_surface, page),
                "dom_generation": await _dom_generation(page),
                "url": final_url,
                "same_route": (not effective_same_route) or _same_route(final_url, listing_url),
                "wrong_route_after_intermediate": final_route_drift,
            }
            await persist()
            if final_form:
                audit.update({"pass": True, "status": "form_open_after_add_and_intermediate", "attempts": step_no})
                await persist()
                return audit
            if final_route_drift:
                step["wrong_intermediate_target"] = {
                    "reason": "in-page BizFlow/template launcher navigated away from the listing route",
                    "url": final_url,
                    "expected_listing_route": str(listing_url or ""),
                }

        # No verified effect: ask the existing multimodal browser-intelligence
        # stack to re-observe the failure, but keep the deterministic expected
        # intent fixed. Then return to the canonical listing and retry.
        step["recovery_intelligence"] = await _recovery_intelligence(
            browser, phase=phase_n,
            task=(
                f"HIP {phase_n}: + Add was attempted but the expected create form did not open. "
                "Inspect DOM/accessibility/vision evidence and identify whether a drawer, template picker, overlay, wrong Add control, or route drift explains the missing effect. Do not execute a portal action."
            ),
        )
        step["recovery"] = await _react_route_back(browser, listing_url, phase=phase_n)
        await page.wait_for_timeout(settle_ms)
        await persist()

    audit.update({
        "pass": False,
        "status": "blocked",
        "error_code": "HIP_FORM_ENTRY_NOT_OPENED",
        "reason": "active create form surface lost after bounded ReAct +Add/form-launch recovery",
    })
    await persist()
    raise RuntimeError(
        "HIP_FORM_ENTRY_NOT_OPENED: active form surface lost; bounded ReAct opener could not complete "
        f"listing -> + Add -> form for phase={phase_n}. "
        "This is recoverable by the mission self-heal loop and must not be downgraded to an empty-form warning."
    )
