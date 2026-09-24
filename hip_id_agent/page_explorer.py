from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urljoin

from playwright.async_api import Locator, Page

from .browser_session import BrowserSession
from .config import AppConfig
from .security import mask_sensitive_string


class PageExplorer:
    def __init__(self, config: AppConfig):
        self.config = config

    async def navigate_to_area(self, session: BrowserSession, area: str) -> Dict[str, Any]:
        """Navigate to Partners or Systems using supplied BizLink URLs first, then menu discovery.

        Every actual click is logged by BrowserSession. Direct URL attempts are also returned as
        evidence so you can see exactly where the automation went.
        """
        page = session.page
        if page is None:
            raise RuntimeError("Browser page not started")
        assert area in {"partner", "system"}
        session.set_stage(f"navigate_{area}")
        candidate_paths = self.config.navigation.partner_candidate_paths if area == "partner" else self.config.navigation.system_candidate_paths
        nav_texts = self.config.navigation.partner_nav_texts if area == "partner" else self.config.navigation.system_nav_texts
        tried: List[Dict[str, Any]] = []

        for path in self._unique_direct_paths(area, candidate_paths):
            target = self._resolve_candidate_url(path)
            rec = {"method": "direct_url", "target": target}
            tried.append(rec)
            try:
                await session.navigate(target)
                await self.dismiss_blockers(session)
                rec["landed_url"] = page.url
                rec["url_match"] = self._url_matches_area(page.url, area)
                rec["page_match"] = await self._page_mentions_area(page, area)
                # Dell BizLink System can render shared text such as "Manage Account" even on the
                # canonical /bizlink/system route. The canonical route itself is authoritative.
                # Do not reject it and then try relative fallback URLs that can become 404s like
                # /bizlink/partner/hybrid-integrations/bizlink/system.
                if rec["url_match"] or rec["page_match"]:
                    return {"status": "success", "method": "direct_url", "target": target, "tried": tried, "url": page.url, "url_match": rec["url_match"], "page_match": rec["page_match"]}
            except Exception as exc:
                rec["error"] = str(exc)
                continue

        # Try menu buttons first to reveal nav, then links/buttons/text.
        await self._open_possible_menus(session)
        for text in nav_texts:
            locators = [
                (f"role=link[name*={text}]", page.get_by_role("link", name=text, exact=False)),
                (f"role=button[name*={text}]", page.get_by_role("button", name=text, exact=False)),
                (f"text={text}", page.get_by_text(text, exact=False)),
            ]
            for selector, loc in locators:
                rec = {"method": "nav_text", "target": text, "selector": selector}
                tried.append(rec)
                try:
                    first = loc.first
                    if await first.is_visible(timeout=2500):
                        await session.click_and_wait(action=f"navigate_to_{area}", locator=first, selector=selector)
                        rec["landed_url"] = page.url
                        rec["page_match"] = await self._page_mentions_area(page, area)
                        if rec["page_match"]:
                            return {"status": "success", "method": "nav_text", "target": text, "selector": selector, "tried": tried, "url": page.url}
                except Exception as exc:
                    rec["error"] = str(exc)
                    continue
        return {"status": "failed", "message": f"Could not navigate to {area}", "tried": tried, "url": page.url}


    def _canonical_path_for_area(self, area: str) -> str:
        return "/hybrid-integrations/bizlink/partner" if area == "partner" else "/hybrid-integrations/bizlink/system"

    def _canonical_url_for_area(self, area: str) -> str:
        return "https://developer.dell.com" + self._canonical_path_for_area(area)

    def _url_matches_area(self, url: str, area: str) -> bool:
        try:
            parsed = urlparse(url or "")
            return parsed.netloc.lower().endswith("developer.dell.com") and parsed.path.rstrip("/").lower() == self._canonical_path_for_area(area)
        except Exception:
            return False

    def _resolve_candidate_url(self, path: str) -> str:
        if (path or "").lower().startswith("http"):
            return path
        # Resolve relative navigation from the Dell Developer origin, not from the current
        # /bizlink/partner page. This prevents wrong URLs such as:
        # https://developer.dell.com/hybrid-integrations/bizlink/partner/system
        return urljoin("https://developer.dell.com/", (path or "").lstrip("/"))

    def _unique_direct_paths(self, area: str, candidate_paths: List[str]) -> List[str]:
        canonical = self._canonical_url_for_area(area)
        out: List[str] = [canonical]
        for path in candidate_paths:
            resolved = self._resolve_candidate_url(path)
            if resolved not in out:
                out.append(resolved)
        return out

    async def _open_possible_menus(self, session: BrowserSession) -> None:
        page = session.page
        if not page:
            return
        for text in self.config.navigation.menu_button_texts:
            for selector, loc in [
                (f"role=button[name*={text}]", page.get_by_role("button", name=text, exact=False)),
                (f"role=link[name*={text}]", page.get_by_role("link", name=text, exact=False)),
            ]:
                try:
                    first = loc.first
                    if await first.is_visible(timeout=800):
                        await session.click_and_wait(action="open_menu", locator=first, selector=selector)
                        await asyncio.sleep(0.15)
                        return
                except Exception:
                    continue

    async def search_query(self, session: BrowserSession, query: str, area: str) -> Dict[str, Any]:
        page = session.page
        if page is None:
            raise RuntimeError("Browser page not started")
        session.set_stage(f"search_{area}")
        await self.dismiss_blockers(session)
        search = await self._find_search_box(page, area)
        if not search:
            await self._open_filter_or_search_panel(session)
            search = await self._find_search_box(page, area)
        if not search:
            return {"status": "warning", "message": "Search box not found; extraction will run on current page", "recovery_suggestion": "Use role/name/placeholder matching or wait for grid render."}
        try:
            before_network_count = len(session.network_tab_events)
            await session.fill_and_log(locator=search, value=query, selector="detected_search_box", action_type="search")
            await session.press_and_log(locator=search, key="Enter", selector="detected_search_box")
            wait = await self.wait_after_search(session, query=query, area=area, before_network_count=before_network_count)
            return {"status": "success" if wait.get("satisfied") else "warning", "message": f"Searched {query}", "url": page.url, "wait_result": wait}
        except Exception as exc:
            return {"status": "failed", "message": f"Search failed: {exc}", "url": page.url}

    async def maybe_open_first_matching_row(self, session: BrowserSession, query: str) -> Dict[str, Any]:
        page = session.page
        if page is None:
            raise RuntimeError("Browser page not started")
        if not self.config.extraction.open_first_matching_row:
            return {"status": "skipped"}
        session.set_stage("open_matching_row")
        q = query.strip()
        clicked: List[Dict[str, Any]] = []

        # Prefer rows containing query; click explicit detail actions first.
        row = page.locator("tr", has_text=q).first
        try:
            if await row.is_visible(timeout=4000):
                for text in self.config.navigation.detail_link_texts:
                    for selector, loc in [
                        (f"row role=link[name*={text}]", row.get_by_role("link", name=text, exact=False)),
                        (f"row role=button[name*={text}]", row.get_by_role("button", name=text, exact=False)),
                    ]:
                        try:
                            first = loc.first
                            if await first.is_visible(timeout=1000):
                                before_url = page.url
                                before_network_count = len(session.network_tab_events)
                                await session.click_and_wait(action="open_row_detail", locator=first, selector=selector)
                                wait = await self.wait_after_row_click(session, query=query, area=self._infer_area_from_url(page.url), before_url=before_url, before_network_count=before_network_count)
                                return {"status": "success", "method": "row_detail", "text": text, "clicked": clicked, "url": page.url, "wait_result": wait}
                        except Exception as exc:
                            clicked.append({"selector": selector, "error": str(exc)})
                anchor = row.locator("a").first
                if await anchor.is_visible(timeout=1200):
                    before_url = page.url
                    before_network_count = len(session.network_tab_events)
                    await session.click_and_wait(action="open_row_anchor", locator=anchor, selector="row a:first")
                    wait = await self.wait_after_row_click(session, query=query, area=self._infer_area_from_url(page.url), before_url=before_url, before_network_count=before_network_count)
                    return {"status": "success", "method": "row_anchor", "url": page.url, "wait_result": wait}
                # If no anchor, clicking row can still open side panel in some HIP pages.
                before_url = page.url
                before_network_count = len(session.network_tab_events)
                await session.click_and_wait(action="open_row_click", locator=row, selector=f"tr:has-text({q})")
                wait = await self.wait_after_row_click(session, query=query, area=self._infer_area_from_url(page.url), before_url=before_url, before_network_count=before_network_count)
                return {"status": "success", "method": "row_click", "url": page.url, "wait_result": wait}
        except Exception as exc:
            clicked.append({"selector": "matching_row", "error": str(exc)})

        # Try any visible link/button containing query.
        for selector, loc in [
            (f"role=link[name*={q}]", page.get_by_role("link", name=q, exact=False)),
            (f"role=button[name*={q}]", page.get_by_role("button", name=q, exact=False)),
            (f"text={q}", page.get_by_text(q, exact=False)),
        ]:
            try:
                first = loc.first
                if await first.is_visible(timeout=2500):
                    before_url = page.url
                    before_network_count = len(session.network_tab_events)
                    await session.click_and_wait(action="open_query_match", locator=first, selector=selector)
                    wait = await self.wait_after_row_click(session, query=query, area=self._infer_area_from_url(page.url), before_url=before_url, before_network_count=before_network_count)
                    return {"status": "success", "method": "query_match", "selector": selector, "url": page.url, "wait_result": wait}
            except Exception as exc:
                clicked.append({"selector": selector, "error": str(exc)})
        return {"status": "not_opened", "message": "No matching details row/link opened", "clicked_attempts": clicked, "url": page.url}


    async def wait_after_search(self, session: BrowserSession, *, query: str, area: str, before_network_count: int = 0) -> Dict[str, Any]:
        """Portal-aware wait after a search/fill action. Logs a wait action for KG/action evidence."""
        page = session.page
        if page is None:
            return {"satisfied": False, "condition": "no_page", "recovery_suggestion": "Browser page is unavailable."}
        ev = await session._begin_action("wait", f"wait_after_search_{area}", value=query)
        deadline = asyncio.get_event_loop().time() + (self.config.browser.search_timeout_ms / 1000.0)
        result: Dict[str, Any] = {"satisfied": False, "condition": "timeout", "area": area, "query": query, "timeout_ms": self.config.browser.search_timeout_ms}
        try:
            while asyncio.get_event_loop().time() < deadline:
                new_events = session.network_tab_events[before_network_count:]
                relevant = [e for e in new_events if self._is_relevant_endpoint(e.url, area)]
                if relevant:
                    result = {"satisfied": True, "condition": "relevant_network_response", "event_count": len(relevant), "last_url": relevant[-1].url}
                    break
                try:
                    if await page.locator("tr", has_text=query).first.is_visible(timeout=700):
                        result = {"satisfied": True, "condition": "visible_row_text", "query": query}
                        break
                except Exception:
                    pass
                try:
                    body = (await page.locator("body").inner_text(timeout=900)).lower()
                    if query.lower() in body:
                        result = {"satisfied": True, "condition": "dom_contains_query", "query": query}
                        break
                except Exception:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=self.config.browser.network_idle_timeout_ms)
                    result = {"satisfied": True, "condition": "network_idle_after_search"}
                    break
                except Exception:
                    pass
                await asyncio.sleep(0.25)
            if not result.get("satisfied"):
                result["recovery_suggestion"] = "Search did not produce a visible row or relevant network response. Increase search_timeout_ms or verify grid/search selector."
            await session._finish_action(ev, result.get("satisfied", False), None if result.get("satisfied") else result.get("recovery_suggestion"))
            return result
        except Exception as exc:
            await session._finish_action(ev, False, str(exc), screenshot_after=True)
            return {"satisfied": False, "condition": "exception", "error": str(exc), "recovery_suggestion": "Check search result wait logic and portal grid render."}

    async def wait_after_row_click(self, session: BrowserSession, *, query: str, area: str, before_url: str, before_network_count: int = 0) -> Dict[str, Any]:
        page = session.page
        if page is None:
            return {"satisfied": False, "condition": "no_page", "recovery_suggestion": "Browser page is unavailable."}
        ev = await session._begin_action("wait", f"wait_after_row_click_{area}", value=query)
        deadline = asyncio.get_event_loop().time() + (self.config.browser.detail_timeout_ms / 1000.0)
        result: Dict[str, Any] = {"satisfied": False, "condition": "timeout", "area": area, "query": query, "timeout_ms": self.config.browser.detail_timeout_ms}
        try:
            while asyncio.get_event_loop().time() < deadline:
                if page.url != before_url:
                    result = {"satisfied": True, "condition": "details_url_changed", "before_url": before_url, "after_url": page.url}
                    break
                new_events = session.network_tab_events[before_network_count:]
                relevant = [e for e in new_events if self._is_relevant_endpoint(e.url, area)]
                if relevant:
                    result = {"satisfied": True, "condition": "detail_network_payload", "event_count": len(relevant), "last_url": relevant[-1].url}
                    break
                try:
                    body = (await page.locator("body").inner_text(timeout=900)).lower()
                    typed_terms = ["partner id", "partnerid"] if area == "partner" else ["system id", "systemid", "domain id", "domainid"]
                    if any(t in body for t in typed_terms):
                        result = {"satisfied": True, "condition": "dom_contains_typed_id_field"}
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.25)
            if not result.get("satisfied"):
                result["recovery_suggestion"] = "Row/detail click did not produce details URL, typed ID field, or relevant network payload. Try explicit details button or longer detail_timeout_ms."
            await session._finish_action(ev, result.get("satisfied", False), None if result.get("satisfied") else result.get("recovery_suggestion"))
            return result
        except Exception as exc:
            await session._finish_action(ev, False, str(exc), screenshot_after=True)
            return {"satisfied": False, "condition": "exception", "error": str(exc), "recovery_suggestion": "Check detail wait logic and row click selector."}

    def _is_relevant_endpoint(self, url: str, area: str) -> bool:
        u = (url or "").lower()
        if area == "partner":
            # Partner screen is backed by both account and partner APIs. Waiting may be
            # satisfied by either, but extraction keeps account IDs separate from partner IDs.
            return any(x in u for x in ["/partner", "/partners", "/accounts", "/authz/accounts", "/authz/partners", "partnerid", "partner-name", "partnername", "accountname"])
        return any(x in u for x in ["/system", "/systems", "/domains", "systemid", "domain", "system-name", "systemname"])

    def _infer_area_from_url(self, url: str) -> str:
        u = (url or "").lower()
        return "system" if "system" in u or "domain" in u else "partner"

    async def collect_snapshot(self, page: Page) -> Dict[str, Any]:
        text = ""
        html = ""
        title = ""
        try:
            text = await page.locator("body").inner_text(timeout=6000)
        except Exception:
            pass
        try:
            html = await page.content()
        except Exception:
            pass
        try:
            title = await page.title()
        except Exception:
            pass
        tables = await self._extract_tables_js(page)
        controls = await self._extract_controls_js(page)
        headings = await self._extract_headings_js(page)
        return {
            "url": mask_sensitive_string(page.url),
            "title": title,
            "body_text": text,
            "html": html,
            "tables": tables,
            "controls": controls,
            "headings": headings,
        }

    async def _extract_tables_js(self, page: Page) -> List[Dict[str, Any]]:
        try:
            return await page.evaluate("""
() => Array.from(document.querySelectorAll('table,[role="table"],[role="grid"]')).map((table, tableIndex) => {
  const headers = Array.from(table.querySelectorAll('th,[role="columnheader"]')).map(th => th.innerText.trim());
  const rows = Array.from(table.querySelectorAll('tr,[role="row"]')).map((tr, rowIndex) => ({
    rowIndex,
    cells: Array.from(tr.querySelectorAll('td,th,[role="cell"],[role="gridcell"]')).map(td => td.innerText.trim()),
    links: Array.from(tr.querySelectorAll('a')).map(a => ({text: a.innerText.trim(), href: a.href}))
  })).filter(r => r.cells.length);
  return { tableIndex, headers, rows };
})
""")
        except Exception:
            return []

    async def _extract_controls_js(self, page: Page) -> List[Dict[str, Any]]:
        try:
            return await page.evaluate("""
() => Array.from(document.querySelectorAll('input,select,textarea,button,a,[role="button"],[role="link"],[role="textbox"],[role="searchbox"]')).slice(0, 800).map((el, i) => {
  const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {x:0,y:0,width:0,height:0};
  return {
    index: i,
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role'),
    type: el.getAttribute('type'),
    text: (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || '').trim(),
    name: el.getAttribute('name'),
    id: el.id,
    href: el.href || null,
    disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
    bbox: {x:r.x,y:r.y,width:r.width,height:r.height}
  }
})
""")
        except Exception:
            return []

    async def _extract_headings_js(self, page: Page) -> List[str]:
        try:
            return await page.evaluate("""
() => Array.from(document.querySelectorAll('h1,h2,h3,h4,[role="heading"]')).map(x => x.innerText.trim()).filter(Boolean).slice(0, 100)
""")
        except Exception:
            return []

    async def dismiss_blockers(self, session: BrowserSession) -> Dict[str, Any]:
        """Dismiss common Dell/Qualtrics/OneTrust overlays that block the grid/search box.

        The live Dell Developer site sometimes opens the OneTrust preference center
        instead of the small cookie banner. In that state the page heading becomes
        ``Your Cookie Preferences`` and the underlying BizLink cards are not
        interactable. This routine handles both normal buttons and OneTrust id-based
        controls, then waits briefly for the real BizLink page to render.
        """
        page = session.page
        if not page:
            return {"status": "no_page"}
        clicked: List[Dict[str, Any]] = []
        labels = [
            "No, thanks", "No thanks", "Decline All", "Reject All", "Accept All",
            "Confirm My Choices", "Save Choices", "Close", "Got it",
        ]
        id_selectors = [
            "#onetrust-accept-btn-handler",
            "#onetrust-reject-all-handler",
            "#accept-recommended-btn-handler",
            "#onetrust-pc-btn-handler",
            "#close-pc-btn-handler",
            "button[aria-label='Close preference center']",
            "button[aria-label*='Close' i]",
        ]
        old_stage = session._current_stage
        session.set_stage("dismiss_blockers")

        # First try stable OneTrust ids. These buttons often have no friendly text.
        for selector in id_selectors:
            try:
                loc = page.locator(selector).first
                if await loc.is_visible(timeout=700) and await loc.is_enabled(timeout=700):
                    await session.click_and_wait(action="dismiss_blocker", locator=loc, selector=selector)
                    clicked.append({"label": selector, "selector": selector})
                    await asyncio.sleep(0.35)
            except Exception:
                continue

        for label in labels:
            for selector, loc in [
                (f"role=button[name={label}]", page.get_by_role("button", name=label, exact=True)),
                (f"button:has-text({label})", page.locator("button", has_text=label)),
                (f"text={label}", page.get_by_text(label, exact=True)),
            ]:
                try:
                    first = loc.first
                    if await first.is_visible(timeout=500) and await first.is_enabled(timeout=500):
                        await session.click_and_wait(action="dismiss_blocker", locator=first, selector=selector)
                        clicked.append({"label": label, "selector": selector})
                        await asyncio.sleep(0.25)
                except Exception:
                    continue

        # If OneTrust still owns the page, hide only its overlay containers as a last
        # resort. This does not alter portal data; it simply removes a cookie UI that
        # blocks card detection in automation.
        try:
            forced = await page.evaluate("""
() => {
  const ids = ['onetrust-pc-sdk','onetrust-banner-sdk','onetrust-consent-sdk'];
  let changed = 0;
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el && el.getBoundingClientRect && (el.getBoundingClientRect().width || el.getBoundingClientRect().height)) {
      el.style.display = 'none';
      el.setAttribute('aria-hidden', 'true');
      changed++;
    }
  }
  for (const el of document.querySelectorAll('.onetrust-pc-dark-filter, .ot-fade-in')) {
    el.style.display = 'none';
    changed++;
  }
  return changed;
}
""")
            if forced:
                clicked.append({"label": "forced_hide_onetrust_overlay", "selector": "#onetrust-*", "count": forced})
                await asyncio.sleep(0.4)
        except Exception:
            pass

        session.set_stage(old_stage)
        return {"status": "clicked" if clicked else "none", "clicked": clicked}

    async def _open_filter_or_search_panel(self, session: BrowserSession) -> Dict[str, Any]:
        page = session.page
        if not page:
            return {"status": "no_page"}
        old_stage = session._current_stage
        session.set_stage("open_filter_panel")
        # Avoid clicking the visible Dell DDS "Filter" button during long full
        # inventory runs. It can open a modal/panel that is unnecessary for the
        # validated parent-card flow and can make the run look stuck. Set
        # HIP_ALLOW_FILTER_BUTTON_CLICK=1 only when a portal variant requires it.
        labels = ["Search"] if os.getenv("HIP_ALLOW_FILTER_BUTTON_CLICK", "").strip().lower() not in {"1", "true", "yes"} else ["Filter", "Search", "Filters", "Open filters"]
        for label in labels:
            for selector, loc in [
                (f"role=button[name*={label}]", page.get_by_role("button", name=label, exact=False)),
                (f"button:has-text({label})", page.locator("button", has_text=label)),
            ]:
                try:
                    first = loc.first
                    if await first.is_visible(timeout=800) and await first.is_enabled(timeout=800):
                        await session.click_and_wait(action="open_filter_panel", locator=first, selector=selector)
                        session.set_stage(old_stage)
                        return {"status": "clicked", "selector": selector}
                except Exception:
                    continue
        session.set_stage(old_stage)
        return {"status": "not_found"}

    async def _is_fillable_control(self, item: Locator, *, timeout: int = 250) -> bool:
        """Return True only for elements that Playwright can fill.

        Dell BizLink/DDS search controls often label the submit button as
        "Search" too. A label-based locator can therefore resolve to the button
        instead of the input, causing ``Locator.fill`` to fail with "Element is
        not an <input>...". This live-run guard filters button/search-icon
        elements out and keeps only editable inputs/textareas/contenteditable
        controls.
        """
        # Unit-test fakes and some wrapper locators may not expose evaluate; the
        # real Playwright path still applies the DOM fillability filter below.
        if not hasattr(item, "evaluate"):
            return True
        try:
            return bool(await item.evaluate("""
(el) => {
  const tag = (el.tagName || '').toLowerCase();
  const role = (el.getAttribute('role') || '').toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  if (el.closest && el.closest('button,a,[role="button"]')) return false;
  if (tag === 'textarea') return !el.disabled && !el.readOnly;
  if (tag === 'input') {
    const blocked = new Set(['button','submit','reset','checkbox','radio','file','image','hidden']);
    return !blocked.has(type) && !el.disabled && !el.readOnly;
  }
  if (el.isContentEditable) return true;
  if (role === 'textbox' || role === 'searchbox' || role === 'combobox') return !el.getAttribute('aria-readonly');
  return false;
}
""", timeout=timeout))
        except Exception:
            return False

    async def _first_visible_enabled(self, loc: Locator, *, timeout: int = 250) -> Optional[Locator]:
        """Return the best visible/enabled fillable search element.

        The BizLink pages have at least two search boxes: a global header search and
        a page/grid filter search. The older implementation checked only `.first`,
        and label matching could resolve to the DDS Search button. This scans all
        matches, filters to fillable controls, and prefers the lower/page-local
        search box over the header search.
        """
        try:
            count = await loc.count()
        except Exception:
            count = 0
        if count <= 0:
            return None
        visible: List[tuple[float, Locator]] = []
        for i in range(min(count, 60)):
            item = loc.nth(i)
            try:
                if await item.is_visible(timeout=timeout) and await item.is_enabled(timeout=timeout) and await self._is_fillable_control(item, timeout=timeout):
                    box = await item.bounding_box()
                    y = float((box or {}).get("y", 0) or 0)
                    # Prefer page-level search controls below the global header.
                    visible.append((y, item))
            except Exception:
                continue
        if not visible:
            return None
        visible.sort(key=lambda x: x[0], reverse=True)
        return visible[0][1]

    async def _find_search_box(self, page: Page, area: str) -> Optional[Locator]:
        labels = list(self.config.navigation.search_labels)
        if area == "partner":
            labels.extend(["Partner Name", "Partner", "Trading Partner", "Account", "Account Name"])
        else:
            labels.extend(["System Name", "System", "Domain", "Domain Name", "Product ID"])

        candidates: List[Locator] = []
        for label in labels:
            candidates.extend([
                page.get_by_role("searchbox", name=label, exact=False),
                page.get_by_role("textbox", name=label, exact=False),
                page.get_by_label(label, exact=False),
                page.get_by_placeholder(label, exact=False),
            ])

        # Page/grid search first. The nth/last behavior is important because the
        # header global search appears before the BizLink grid filter search.
        candidates.extend([
            page.locator("main input[type='search']"),
            page.locator("app-root input[type='search']"),
            page.locator("input.dds__search__control"),
            page.locator("input[type='search']"),
            page.locator("input[placeholder*='Search' i]"),
            page.locator("input[aria-label*='Search' i]"),
            page.locator("input[type='text']"),
        ])
        for loc in candidates:
            found = await self._first_visible_enabled(loc)
            if found:
                return found
        return None

    def _is_safe_exploration_action(self, label: str) -> bool:
        text = (label or "").strip().lower()
        if not text:
            return False
        if self.config.exploration.allow_unsafe_clicks:
            return True
        if any(bad.lower() in text for bad in self.config.exploration.unsafe_action_keywords):
            # Edit is read-only safe in this tool only when the agent does not click Update/Save.
            if "edit" in text and self.config.exploration.open_edit_pages_for_readonly_capture:
                return True
            return False
        return any(good.lower() in text for good in self.config.exploration.safe_action_keywords)

    def _is_nested_discovery_action(self, label: str, target_type: str) -> bool:
        """Return True when a card-menu action should expose nested child rows.

        BizLink Partner/System pages are account-centric. A direct search for a
        child item can return no grid rows because the child only appears after
        opening a parent card action:

        * Partner: Account card -> kebab -> Show Partner(s) -> /accounts/<id>/partners
        * System:  Account card -> kebab -> View Domain(s)   -> /accounts/<id>/domains

        This matcher is intentionally narrow so broad exploration never clicks
        destructive actions and never treats unrelated menu labels as nested
        discovery actions.
        """
        text = (label or "").strip().lower()
        if not text:
            return False
        if target_type == "partner":
            return (
                ("show" in text or "view" in text or "open" in text or "manage" in text)
                and ("partner" in text or "partners" in text or "trading" in text)
            )
        if target_type == "system":
            return (
                ("show" in text or "view" in text or "open" in text or "manage" in text)
                and ("domain" in text or "domains" in text or "system" in text or "systems" in text)
            )
        return False

    async def find_nested_entity_via_parent_actions(self, session: BrowserSession, *, area: str, target_type: str, query: str) -> Dict[str, Any]:
        """Search parent account cards across pages and open child-list actions.

        This is the live-portal fallback for the case the user reported: searching
        AS2TEST on the Partner grid returns no result because AS2TEST is nested
        under a parent account such as Gmail Account. The same pattern applies to
        Systems/Domains under Customer Experience (CX). The method resets to the
        canonical page, iterates visible parent cards over pagination, clicks only
        safe child-list actions, and extracts the requested child ID from the
        network/DOM events caused by that specific click.
        """
        page = session.page
        if page is None:
            return {"status": "failed", "message": "Browser page not started"}
        if target_type not in {"partner", "system"}:
            return {"status": "skipped", "message": f"Nested discovery is not supported for {target_type}"}
        if not hasattr(self, "configured_extractor"):
            return {"status": "failed", "message": "Configured extractor is not attached to PageExplorer"}

        session.set_stage(f"nested_{target_type}_discovery")
        summary: Dict[str, Any] = {
            "status": "not_found",
            "area": area,
            "target_type": target_type,
            "query": query,
            "pattern": "parent_account_card_menu_to_child_list",
            "pages_seen": [],
            "parents_checked": [],
            "actions_checked": [],
            "recovery_suggestions": [],
        }

        # Reset the page so a failed direct search filter does not hide parent accounts.
        nav = await self.navigate_to_area(session, area)
        summary["navigation_reset"] = nav
        await self.dismiss_blockers(session)

        total_actions = 0
        for page_no in range(1, max(1, self.config.exploration.max_pages) + 1):
            session.set_stage(f"nested_{target_type}_page_{page_no}")
            await self.dismiss_blockers(session)
            snapshot = await self.collect_snapshot(page)
            page_rec = {
                "page_no": page_no,
                "url": page.url,
                "title": snapshot.get("title"),
                "heading": (snapshot.get("headings") or [""])[0] if snapshot.get("headings") else "",
            }
            try:
                page_rec["dom_snapshot"] = await session.save_dom_snapshot(f"nested_{target_type}_page_{page_no:03d}")
            except Exception:
                pass
            cards = await self._discover_entity_cards(page)
            page_rec["card_count"] = len(cards)
            summary["pages_seen"].append(page_rec)

            max_cards = min(len(cards), self.config.exploration.max_cards_per_page)
            for idx in range(max_cards):
                if total_actions >= self.config.exploration.max_total_actions:
                    summary["stopped_reason"] = "max_total_actions reached"
                    break
                card = cards[idx]
                card_title = card.get("title") or card.get("name") or card.get("text", "")[:80]
                parent_rec: Dict[str, Any] = {"page_no": page_no, "card_index": idx, "card_title": card_title}
                summary["parents_checked"].append(parent_rec)

                actions = await self._open_card_menu_and_collect_actions(session, idx, card_title)
                parent_rec["menu_actions"] = actions
                nested_actions = [a for a in actions if self._is_nested_discovery_action(a.get("label") or "", target_type)]
                parent_rec["nested_actions"] = nested_actions
                if not nested_actions:
                    # Close the menu so the next parent can be inspected cleanly.
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
                    continue

                for action in nested_actions:
                    label = action.get("label") or ""
                    before_url = page.url
                    before_network = len(session.network_tab_events)
                    click_result = await self._click_menu_action_by_label(session, label, area=target_type, card_title=card_title, card_index=idx)
                    total_actions += 1
                    wait = await self.wait_after_row_click(session, query=query, area=target_type, before_url=before_url, before_network_count=before_network)
                    # Give slow grids a short extra chance to render child rows/respond.
                    await asyncio.sleep(0.4)
                    child_snapshot = await self.collect_snapshot(page)
                    network_after_click = session.network_tab_events[before_network:]
                    extracted, debug = self.configured_extractor.extract(  # type: ignore[attr-defined]
                        object_type=target_type,
                        query=query,
                        url=child_snapshot.get("url", page.url),
                        html=child_snapshot.get("html", ""),
                        body_text=child_snapshot.get("body_text", ""),
                        network_records=[],
                        network_tab_events=network_after_click,
                    )
                    attempt: Dict[str, Any] = {
                        "parent_card_title": card_title,
                        "action_label": label,
                        "click_result": click_result,
                        "wait_result": wait,
                        "network_events_after_click": len(network_after_click),
                        "child_url": page.url,
                        "child_heading": (child_snapshot.get("headings") or [""])[0] if child_snapshot.get("headings") else "",
                        "debug": {k: v for k, v in (debug or {}).items() if k in {"verified_result", "winner", "candidate_count", "rejected_count"}},
                    }
                    summary["actions_checked"].append(attempt)
                    if extracted:
                        # Preserve the parent context in the evidence so the KG/report can explain
                        # that AS2TEST/AIC-DCE was found inside a parent account, not by direct search.
                        extracted.evidence = dict(extracted.evidence or {})
                        extracted.evidence.update({
                            "nested_discovery": True,
                            "parent_card_title": card_title,
                            "parent_action_label": label,
                            "child_list_url": page.url,
                            "network_events_after_parent_action": len(network_after_click),
                        })
                        summary.update({
                            "status": "found",
                            "message": f"Found {target_type} {query} by opening parent card action {label!r} on {card_title!r}",
                            "found_parent_card_title": card_title,
                            "found_action_label": label,
                            "_extracted": extracted,
                            "found_debug": debug,
                        })
                        return summary
                    await self._return_to_area_after_exploration_action(session, area=area, previous_url=before_url)
                    await asyncio.sleep(0.2)
                if total_actions >= self.config.exploration.max_total_actions:
                    break
            if total_actions >= self.config.exploration.max_total_actions:
                break
            if not self.config.exploration.collect_all_pages:
                break
            next_result = await self._go_to_next_page(session, area=area)
            summary.setdefault("pagination", []).append(next_result)
            if next_result.get("status") != "clicked":
                break
            await asyncio.sleep(0.8)

        if target_type == "partner":
            summary["recovery_suggestions"].append("Direct Partner search returned no verified row. Confirm the parent Account card has a Show Partner(s) action and increase max_pages/max_total_actions if the parent account is on a later page.")
        else:
            summary["recovery_suggestions"].append("Direct System search returned no verified row. Confirm the parent Account/Customer Experience card has a View Domain(s) action and increase max_pages/max_total_actions if the parent account is on a later page.")
        return summary

    async def explore_area(self, session: BrowserSession, area: str, *, initial_query: str | None = None) -> Dict[str, Any]:
        """Controlled discovery crawl for BizLink Partner/System pages.

        It searches the supplied example first, then safely explores visible cards/rows,
        kebab menus, View/Show/Edit detail actions, and pagination. It records what every
        click does and relies on network/DOM evidence for ID extraction. Destructive actions
        such as Delete/Update/Save/Add are skipped unless allow_unsafe_clicks=true.
        """
        page = session.page
        if page is None:
            return {"status": "failed", "message": "Browser page not started"}
        session.set_stage(f"explore_{area}")
        summary: Dict[str, Any] = {
            "area": area,
            "initial_query": initial_query,
            "pages_seen": [],
            "actions": [],
            "entities_saved": 0,
            "skipped_unsafe_actions": [],
            "errors": [],
        }
        if initial_query and self.config.exploration.search_first:
            summary["search"] = await self.search_query(session, initial_query, area)
            await asyncio.sleep(0.5)
        total_actions = 0
        for page_no in range(1, max(1, self.config.exploration.max_pages) + 1):
            session.set_stage(f"explore_{area}_page_{page_no}")
            await self.dismiss_blockers(session)
            snapshot = await self.collect_snapshot(page)
            page_rec = {
                "page_no": page_no,
                "url": page.url,
                "title": snapshot.get("title"),
                "heading": (snapshot.get("headings") or [""])[0] if snapshot.get("headings") else "",
            }
            dom_path = await session.save_dom_snapshot(f"explore_{area}_page_{page_no:03d}")
            page_rec["dom_snapshot"] = dom_path
            cards = await self._discover_entity_cards(page)
            page_rec["card_count"] = len(cards)
            summary["pages_seen"].append(page_rec)

            # Save all strongly evidenced IDs visible in network so far.
            for object_type in (["partner"] if area == "partner" else ["system"]):
                bulk = self.configured_extractor.extract_all_from_network(object_type, session.network_tab_events) if hasattr(self, "configured_extractor") else []
                # The configured_extractor attribute is injected by the flow. If not present, skip.
                for item in bulk:
                    try:
                        self.configured_memory.save_entity(object_type, item)  # type: ignore[attr-defined]
                        summary["entities_saved"] += 1
                    except Exception:
                        pass

            max_cards = min(len(cards), self.config.exploration.max_cards_per_page)
            for idx in range(max_cards):
                if total_actions >= self.config.exploration.max_total_actions:
                    summary["stopped_reason"] = "max_total_actions reached"
                    break
                card = cards[idx]
                card_title = card.get("title") or card.get("name") or card.get("text", "")[:80]
                actions = await self._open_card_menu_and_collect_actions(session, idx, card_title)
                summary["actions"].append({"card_index": idx, "card_title": card_title, "menu_actions": actions})
                clicked_for_card = 0
                for action in actions:
                    label = action.get("label") or ""
                    if not self._is_safe_exploration_action(label):
                        summary["skipped_unsafe_actions"].append({"card_title": card_title, "label": label, "reason": "unsafe or not useful for ID discovery"})
                        continue
                    if clicked_for_card >= self.config.exploration.max_actions_per_entity:
                        break
                    if total_actions >= self.config.exploration.max_total_actions:
                        break
                    before_url = page.url
                    before_network = len(session.network_tab_events)
                    result = await self._click_menu_action_by_label(session, label, area=area, card_title=card_title, card_index=idx)
                    result.update({"card_title": card_title, "label": label})
                    summary["actions"].append(result)
                    total_actions += 1
                    clicked_for_card += 1
                    await self.wait_after_row_click(session, query=card_title or initial_query or "", area=area, before_url=before_url, before_network_count=before_network)
                    await self._snapshot_and_extract_after_exploration_action(session, area=area, query=card_title or initial_query or "", summary=summary)
                    await self._return_to_area_after_exploration_action(session, area=area, previous_url=before_url)
                    await asyncio.sleep(0.2)
                if total_actions >= self.config.exploration.max_total_actions:
                    break
            if total_actions >= self.config.exploration.max_total_actions:
                break
            if not self.config.exploration.collect_all_pages:
                break
            next_result = await self._go_to_next_page(session, area=area)
            summary["next_page"] = next_result
            if next_result.get("status") != "clicked":
                break
            await asyncio.sleep(1.0)
        summary["status"] = "success"
        summary["total_actions"] = total_actions
        return summary

    async def _snapshot_and_extract_after_exploration_action(self, session: BrowserSession, *, area: str, query: str, summary: Dict[str, Any]) -> None:
        page = session.page
        if not page:
            return
        snapshot = await self.collect_snapshot(page)
        shot = None
        try:
            shot = await session.screenshot(session.run_dir / "screenshots" / f"explore_{area}_{len(summary.get('actions', [])):04d}.png")
        except Exception:
            pass
        rec = {"url": page.url, "query": query, "screenshot": shot, "body_excerpt": (snapshot.get("body_text") or "")[:1200]}
        summary.setdefault("snapshots", []).append(rec)

    async def _return_to_area_after_exploration_action(self, session: BrowserSession, *, area: str, previous_url: str) -> None:
        page = session.page
        if not page:
            return
        # Close menus/dialogs/drawers first.
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.1)
        except Exception:
            pass
        if page.url != previous_url:
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=8000)
                await session.wait_ready()
                return
            except Exception:
                pass
            try:
                await self.navigate_to_area(session, area)
            except Exception:
                pass

    def _fallback_cards_from_body_text(self, body_text: str) -> List[Dict[str, Any]]:
        """Extract parent cards from plain visible body text.

        The live Dell DDS cards sometimes have usable body text while the card
        wrapper reports a zero bounding box to Playwright/JS. This fallback keeps
        discovery deterministic: build virtual parent cards from the visible text,
        then use the card title to re-scope the actual click target in the DOM.
        """
        lines = [ln.strip() for ln in (body_text or "").splitlines()]
        lines = [ln for ln in lines if ln]
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        stop_tokens = {"items per page", "previous", "next", "copyright", "privacy", "terms of use", "accessibility"}
        skip = {
            "developer", "mcp", "blogs", "open source", "community", "home", "bizlink",
            "securelink", "bizexchange", "transtrack", "bizmon", "navigation expanded",
            "manage account", "manage domain", "filter", "learn more", "description:", "page", "of"
        }

        def add_card(title: str, block: list[str], kind: str) -> None:
            key = title.lower().strip()
            if not title or key in seen or key in skip:
                return
            if any(tok in key for tok in stop_tokens):
                return
            seen.add(key)
            text = " ".join(block).strip() or title
            actions = []
            low = text.lower()
            if kind == "system" or re.search(r"\bPT\d{3,}", text, flags=re.I):
                actions = [{"label": "View Domain", "source": "text_fallback"}]
            else:
                actions = [{"label": "Show Partner(s)", "source": "text_fallback"}, {"label": "Domain(s)", "source": "text_fallback"}]
            out.append({
                "index": len(out),
                "selector": "body_text_fallback",
                "title": title,
                "text": text[:1600],
                "buttons": actions,
                "bbox": {"x": 0, "y": 0, "width": 0, "height": 0},
                "fallback": True,
            })

        # System/domain page pattern: title followed by PT/product id.
        for i, line in enumerate(lines[:-1]):
            nxt = lines[i + 1]
            low = line.lower()
            if low in skip or any(tok in low for tok in stop_tokens):
                continue
            if nxt.upper().startswith("PT") or re.match(r"^PT\d{3,}$", nxt, flags=re.I):
                block = lines[i:i + 5]
                add_card(line, block, "system")

        # Partner/account page pattern: title followed shortly by Description:.
        for i, line in enumerate(lines):
            low = line.lower()
            if low in skip or any(tok in low for tok in stop_tokens):
                continue
            window = lines[i + 1:i + 5]
            if any(w.lower() == "description:" for w in window):
                j = i + 1
                block = [line]
                while j < len(lines) and len(block) < 12:
                    if j > i + 1 and lines[j].lower() in skip:
                        break
                    if any(tok in lines[j].lower() for tok in stop_tokens):
                        break
                    # Stop at the next card title if it is followed by Description.
                    if j > i + 1 and (j + 1) < len(lines) and lines[j + 1].lower() == "description:":
                        break
                    block.append(lines[j])
                    j += 1
                add_card(line, block, "partner")
        return out[:300]

    async def _discover_entity_cards(self, page: Page) -> List[Dict[str, Any]]:
        """Return visible BizLink parent cards/rows.

        Real BizLink cards are Angular/DDS custom elements. On some builds,
        wrapper ``innerText``/bounding boxes can be unreliable even though the
        page body visibly contains the cards. The function therefore tries DOM
        candidates first, then falls back to visible body-text card extraction.
        """
        cards: List[Dict[str, Any]] = []
        try:
            cards = await page.evaluate("""
() => {
  const selectors = [
    '.customCard',
    'dds-card.existing_domain',
    'dds-card.dds__card',
    'dds-card',
    '.existing_domain',
    '.dds__card',
    'mat-card',
    '.mat-card',
    '[role="listitem"]',
    'tr[role="row"]',
    'tr'
  ];
  const seen = new Set();
  const out = [];
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const getText = (el) => norm(el.innerText || el.textContent || '');
  const rectOf = (el) => {
    let r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    if (r && (r.width > 2 || r.height > 2)) return r;
    const child = el && el.querySelector ? el.querySelector('a, button, dds-link, dds-button, [role="button"], [role="link"], .dds__card__header__title, .dds__card__content') : null;
    r = child && child.getBoundingClientRect ? child.getBoundingClientRect() : r;
    return r || {x:0,y:0,width:0,height:0};
  };
  const looksLikeEntity = (text) => {
    const t = (text || '').toLowerCase();
    if (!t) return false;
    if (t.includes('copyright') || t.includes('privacy') || t.includes('terms of use')) return false;
    if (t.includes('your cookie preferences')) return false;
    return (
      t.includes('description') ||
      t.includes('domain') ||
      t.includes('partner') ||
      t.includes('account') ||
      t.includes('pt281') ||
      /pt\\d{3,}/i.test(text) ||
      /[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}/i.test(text) ||
      /[a-z0-9_-]+_pc/i.test(text) ||
      /customer experience/i.test(text) ||
      /supply chain/i.test(text) ||
      /enterprise finance/i.test(text)
    );
  };
  for (const sel of selectors) {
    let nodes = [];
    try { nodes = Array.from(document.querySelectorAll(sel)); } catch (err) { continue; }
    for (const el of nodes) {
      if (seen.has(el)) continue;
      const rect = rectOf(el);
      const text = getText(el);
      if (!text) continue;
      if (rect && rect.y && rect.y < 45) continue;
      // Do not require card wrapper dimensions. DDS/custom elements sometimes
      // report zero rects while child title/action controls are visible.
      if (!looksLikeEntity(text)) continue;

      let childCards = [];
      try {
        childCards = Array.from(el.querySelectorAll('.customCard, dds-card, .existing_domain, tr[role="row"], tr'))
          .filter(c => c !== el && getText(c));
      } catch (err) { childCards = []; }
      if (childCards.length > 4 && !el.matches('tr, tr[role="row"]')) continue;

      seen.add(el);
      const titleEl = el.querySelector('a.dds__link--standalone, .dds__card__header__title, dds-card-title, a, h1, h2, h3, h4, [class*="title"], [class*="name"], .dds__card__header, td, [role="cell"]');
      const title = norm((titleEl && (titleEl.innerText || titleEl.textContent)) || text.split(' Description')[0] || text.split('\n')[0] || text.slice(0, 80));
      const buttons = Array.from(el.querySelectorAll('button, a, [role="button"], [role="link"], dds-button, dds-link')).map((b, i) => {
        const br = b.getBoundingClientRect ? b.getBoundingClientRect() : {x:0,y:0,width:0,height:0};
        return {
          index: i,
          text: norm(b.innerText || b.textContent || b.getAttribute('aria-label') || b.getAttribute('title') || ''),
          aria: b.getAttribute('aria-label') || '',
          title: b.getAttribute('title') || '',
          href: b.href || '',
          bbox: {x: br.x, y: br.y, width: br.width, height: br.height}
        };
      }).filter(b => b.text || b.aria || b.title || b.href);
      out.push({
        index: out.length, selector: sel, title, text: text.slice(0, 1600), buttons,
        bbox: {x: rect.x || 0, y: rect.y || 0, width: rect.width || 0, height: rect.height || 0}
      });
    }
  }
  return out.slice(0, 300);
}
""")
        except Exception:
            cards = []
        if cards:
            return cards
        try:
            body_text = await page.locator("body").inner_text(timeout=1500)
        except Exception:
            try:
                body_text = await page.evaluate("() => document.body ? (document.body.innerText || document.body.textContent || '') : ''")
            except Exception:
                body_text = ""
        return self._fallback_cards_from_body_text(body_text)

    def _xpath_literal(self, value: str) -> str:
        if '"' not in value:
            return f'"{value}"'
        if "'" not in value:
            return f"'{value}'"
        parts = value.split('"')
        return "concat(" + ', "\\\"", '.join(f'"{p}"' for p in parts) + ")"

    async def _card_locator_by_title(self, page: Page, title: str) -> Optional[Locator]:
        """Find the actual card container by its visible title.

        This is the live BizLink-safe fallback used when generic card selectors
        return zero due to DDS/custom-element bounding-box behavior.
        """
        title = (title or "").strip()
        if not title:
            return None
        lit = self._xpath_literal(title)
        xpaths = [
            f"xpath=//*[normalize-space(.)={lit}]/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' customCard ')][1]",
            f"xpath=//*[normalize-space(.)={lit}]/ancestor::*[local-name()='dds-card'][1]",
            f"xpath=//*[contains(normalize-space(.), {lit}) and (contains(concat(' ', normalize-space(@class), ' '), ' customCard ') or local-name()='dds-card' or contains(concat(' ', normalize-space(@class), ' '), ' existing_domain '))][1]",
        ]
        for xp in xpaths:
            try:
                loc = page.locator(xp).first
                if await loc.count() and await loc.is_visible(timeout=700):
                    return loc
            except Exception:
                continue
        # Last resort: visible title/link itself. Scoped action lookup will climb from it poorly,
        # but direct role/text click can still use page-level fallback if needed.
        for loc in [page.get_by_role("link", name=title, exact=True), page.get_by_text(title, exact=True)]:
            try:
                first = loc.first
                if await first.count() and await first.is_visible(timeout=700):
                    return first
            except Exception:
                continue
        return None

    async def _card_locator(self, page: Page, card_index: int) -> Optional[Locator]:
        selectors = [
            ".customCard", "dds-card.existing_domain", "dds-card.dds__card", "dds-card", ".existing_domain",
            ".dds__card", "mat-card", ".mat-card",
            '[role="listitem"]', 'tr[role="row"]', "tr"
        ]
        matches: List[Locator] = []
        seen_texts: set[str] = set()
        for sel in selectors:
            try:
                loc = page.locator(sel)
                count = await loc.count()
            except Exception:
                continue
            for i in range(min(count, 300)):
                item = loc.nth(i)
                try:
                    if not await item.is_visible(timeout=100):
                        continue
                    box = await item.bounding_box()
                    if not box or box.get("width", 0) < 40 or box.get("height", 0) < 10 or box.get("y", 0) < 55:
                        continue
                    text = (await item.evaluate("el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim()", timeout=500) or "").strip()
                    low = text.lower()
                    if not text or any(x in low for x in ["copyright", "privacy", "terms of use", "your cookie preferences"]):
                        continue
                    if not any(x in low for x in ["description", "domain", "partner", "edit account", "show partner", "view domain", "pt281", ".com", "_pc", "account", "customer experience", "supply chain", "enterprise finance"]):
                        continue
                    key = f"{round(box.get('x',0))}:{round(box.get('y',0))}:{text[:80]}"
                    if key in seen_texts:
                        continue
                    seen_texts.add(key)
                    matches.append(item)
                except Exception:
                    pass
        return matches[card_index] if card_index < len(matches) else None

    async def _open_card_menu_and_collect_actions(self, session: BrowserSession, card_index: int, card_title: str) -> List[Dict[str, Any]]:
        page = session.page
        if not page:
            return []
        card = await self._card_locator(page, card_index)
        if not card:
            card = await self._card_locator_by_title(page, card_title)
        if not card:
            return []

        # Many Dell DDS cards already render read-only action links such as
        # "Domain(s)", "Show Partner(s)", and "Edit Account" inside the card
        # without requiring the kebab menu to be opened. Collect those first.
        direct_actions = await self._collect_card_actions(card)
        if any(self._is_nested_discovery_action(a.get("label") or "", "partner") or self._is_nested_discovery_action(a.get("label") or "", "system") for a in direct_actions):
            return direct_actions

        candidates = [
            ("card dds overflow", card.locator("dds-button[icon-name='overflow'] button, button.dds__button__icon, button[aria-haspopup='true']")),
            ("card kebab aria", card.locator("button[aria-label*='more' i],button[aria-label*='action' i],[role=button][aria-label*='more' i]")),
            ("card icon buttons", card.locator("button,[role=button]")),
        ]
        for selector, loc in candidates:
            try:
                count = await loc.count()
            except Exception:
                count = 0
            # Kebab/action is usually the last icon button on the card.
            for i in reversed(range(min(count, 8))):
                btn = loc.nth(i)
                try:
                    if await btn.is_visible(timeout=400) and await btn.is_enabled(timeout=400):
                        await session.click_and_wait(action="open_entity_action_menu", locator=btn, selector=f"{selector}[{i}]")
                        await asyncio.sleep(0.2)
                        actions = await self._collect_open_menu_actions(page)
                        if actions:
                            return actions
                except Exception:
                    continue
        return direct_actions

    async def _collect_card_actions(self, card: Locator) -> List[Dict[str, Any]]:
        try:
            return await card.evaluate("""
(el) => Array.from(el.querySelectorAll('button, a, [role="button"], [role="link"], dds-button, dds-link'))
  .map((node, i) => {
    const r = node.getBoundingClientRect ? node.getBoundingClientRect() : {x:0,y:0,width:0,height:0};
    const label = (node.innerText || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
    return {
      index: i,
      label,
      tag: (node.tagName || '').toLowerCase(),
      href: node.href || '',
      visible: !!(r.width || r.height || node.getClientRects().length),
      bbox: {x:r.x,y:r.y,width:r.width,height:r.height},
      source: 'card'
    };
  })
  .filter(x => x.visible && x.label && x.bbox.width > 2 && x.bbox.height > 2)
  .slice(0, 40)
""")
        except Exception:
            return []

    async def _collect_open_menu_actions(self, page: Page) -> List[Dict[str, Any]]:
        try:
            return await page.evaluate("""
() => Array.from(document.querySelectorAll('[role=menuitem], .dds__dropdown__item, .mat-menu-item, button, a, li'))
  .map((el, i) => {
    const r = el.getBoundingClientRect();
    const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
    return {index: i, label: text, tag: (el.tagName || '').toLowerCase(), href: el.href || '', visible: !!(r.width || r.height || el.getClientRects().length), bbox: {x:r.x,y:r.y,width:r.width,height:r.height}};
  })
  .filter(x => x.visible && x.label && x.bbox.y > 80 && x.bbox.width > 5 && x.bbox.height > 5)
  .slice(0, 80)
""")
        except Exception:
            return []

    async def _click_menu_action_by_label(self, session: BrowserSession, label: str, *, area: str, card_title: str, card_index: int | None = None) -> Dict[str, Any]:
        page = session.page
        if not page:
            return {"status": "failed", "label": label, "error": "no page"}
        session.set_stage(f"explore_{area}_click_{label}"[:80])

        # Prefer the action inside the currently inspected parent card. This prevents
        # clicking the first global "Show Partner(s)"/"View Domain" on the page
        # when we are iterating through paginated parent cards.
        if card_index is not None:
            try:
                card = await self._card_locator(page, card_index)
                if card is None:
                    card = await self._card_locator_by_title(page, card_title)
            except Exception:
                card = None
            if card is not None:
                scoped_locators = [
                    (f"card[{card_index}] role=button[name*={label}]", card.get_by_role("button", name=label, exact=False)),
                    (f"card[{card_index}] role=link[name*={label}]", card.get_by_role("link", name=label, exact=False)),
                    (f"card[{card_index}] text={label}", card.get_by_text(label, exact=False)),
                    (f"card[{card_index}] button/a has text {label}", card.locator("button, a, [role=button], [role=link], dds-button, dds-link").filter(has_text=label)),
                ]
                for selector, loc in scoped_locators:
                    try:
                        first = loc.first
                        if await first.is_visible(timeout=900) and await first.is_enabled(timeout=900):
                            await session.click_and_wait(action="explore_menu_action", locator=first, selector=selector)
                            return {"status": "clicked", "selector": selector, "area": area, "card_title": card_title, "scoped_to_card": True}
                    except Exception as exc:
                        last = str(exc)

        for selector, loc in [
            (f"role=menuitem[name*={label}]", page.get_by_role("menuitem", name=label, exact=False)),
            (f"role=button[name*={label}]", page.get_by_role("button", name=label, exact=False)),
            (f"role=link[name*={label}]", page.get_by_role("link", name=label, exact=False)),
            (f"text={label}", page.get_by_text(label, exact=False)),
        ]:
            try:
                first = loc.first
                if await first.is_visible(timeout=900) and await first.is_enabled(timeout=900):
                    await session.click_and_wait(action="explore_menu_action", locator=first, selector=selector)
                    return {"status": "clicked", "selector": selector, "area": area, "card_title": card_title, "scoped_to_card": False}
            except Exception as exc:
                last = str(exc)
        return {"status": "failed", "label": label, "error": locals().get("last", "action not found")}

    async def _go_to_next_page(self, session: BrowserSession, *, area: str) -> Dict[str, Any]:
        page = session.page
        if not page:
            return {"status": "no_page"}
        session.set_stage(f"explore_{area}_next_page")
        candidates = [
            ("role=button[name=Next]", page.get_by_role("button", name="Next", exact=False)),
            ("role=link[name=Next]", page.get_by_role("link", name="Next", exact=False)),
            ("button[aria-label*=next]", page.locator("button[aria-label*='next' i],[role=button][aria-label*='next' i]")),
            ("text=Next", page.get_by_text("Next", exact=False)),
        ]
        for selector, loc in candidates:
            try:
                found = await self._first_visible_enabled(loc, timeout=500)
                if found:
                    before_url = page.url
                    before_network = len(session.network_tab_events)
                    await session.click_and_wait(action="next_page", locator=found, selector=selector)
                    wait = await self.wait_after_row_click(session, query="next page", area=area, before_url=before_url, before_network_count=before_network)
                    return {"status": "clicked", "selector": selector, "wait_result": wait, "url": page.url}
            except Exception:
                continue
        return {"status": "not_found_or_disabled"}

    async def _page_mentions_area(self, page: Page, area: str) -> bool:
        try:
            text = (await page.locator("body").inner_text(timeout=3500)).lower()
        except Exception:
            return False
        if area == "partner":
            return any(x in text for x in ["partner", "trading partner", "partner id", "partner name"])
        return any(x in text for x in ["system", "system id", "system name", "domain"])
