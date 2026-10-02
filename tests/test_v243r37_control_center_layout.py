"""V243R37: the Control Center fits every screen -- nothing overflows, wraps badly or is cut off.

"Improve the UI and fix overflowing UI elements too."

Measured on the real Control Center (served by the backend) in a real browser:

* no element spills out of its box, leaves the viewport or widens the page, and no
  button, badge or tab is cut off, at 1920 / 1440 / 1366 / 1024 / 390 px, on every tab;
* very long values (paths, run ids) wrap where they land instead of widening the page;
* the sidebar collapses (remembered) and is a drawer below 1100 px; tabs work with
  the keyboard, are remembered and linkable; the docked chat can be resized; sidebar
  sections fold; the "Open the Teach panel" link really opens the Teach section;
* the browser tab title says what the agent is doing.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

AUDIT_JS = r"""() => {
  const vw = innerWidth;
  const shown = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const name = (el) => (el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.classList.length ? '.' + [...el.classList].join('.') : ''));
  const scrollerOf = (el) => { let n = el.parentElement;
    while (n && n !== document.body) { if (/(auto|scroll)/.test(getComputedStyle(n).overflowX)) return n; n = n.parentElement; } return null; };
  const issues = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!shown(el) || ['SCRIPT','STYLE','OPTION','BR'].includes(el.tagName) || el.closest('.hidden')) continue;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    const fixed = cs.position === 'fixed' || (el.closest('.chat-dock') && getComputedStyle(el.closest('.chat-dock')).position === 'fixed');
    if (!scrollerOf(el) && !fixed && r.right > vw + 1) issues.push(['beyond_viewport', name(el)]);
    if (['TD','TH','TR','TBODY','THEAD','TABLE','IMG','INPUT','TEXTAREA','SELECT'].includes(el.tagName)) continue;
    const spill = el.scrollWidth - el.clientWidth;
    if (el.clientWidth > 0 && spill > 1) {
      if (cs.overflowX === 'visible') issues.push(['content_spills', name(el)]);
      if (cs.textOverflow === 'ellipsis' && el.matches('button,.badge,.tabs button')) issues.push(['cut_off', name(el)]);
    }
    if (el.matches('button,.badge,.tabs button') && r.height > 2 * (parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.25) + parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + 2)
      issues.push(['wraps', name(el)]);
  }
  return {page_overflow: document.documentElement.scrollWidth - vw, issues: [...new Set(issues.map(i => i.join(' ')))]};
}"""

LONG_VALUES_JS = r"""() => {
  const long = 'C:\\Users\\operator\\OneDrive - Dell\\HIP\\runs\\FULL_DUMMY_FILL_20261002T101010Z_' + 'X'.repeat(60);
  const hi = setTimeout(() => {}, 0); for (let i = 0; i <= hi; i++) { clearInterval(i); clearTimeout(i); }
  let n = 0;
  for (const el of document.querySelectorAll('.table td, .kv > span, .run strong, .metrics strong, .metrics small, .panel-head h2, .chat-bubble p, .badge, .mission-step-grid span, .help, .muted, .empty'))
    if (!el.children.length || el.matches('.kv > span')) { el.textContent += ' ' + long; n++; }
  return n;
}"""


def _need_browser():
    from phase_replica_support import chromium_path

    if not chromium_path():
        pytest.skip("Chromium is not available")


@pytest.fixture(scope="module")
def control_center():
    """The backend on a free port; it serves the Control Center at /."""
    _need_browser()
    import uvicorn

    from backend.app import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.1)
    assert server.started, "the backend did not start"
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True
    thread.join(timeout=10)


async def _open(pw, url, width, height):
    from phase_replica_support import chromium_path

    browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
    page = await browser.new_page(viewport={"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_function("() => document.getElementById('preflightMetric').textContent !== 'Not run'", timeout=30000)
    await page.wait_for_timeout(1200)
    return browser, page, errors


def test_nothing_overflows_wraps_badly_or_is_cut_off_at_any_screen_size(control_center):
    from playwright.async_api import async_playwright

    async def run():
        found = {}
        async with async_playwright() as pw:
            for width, height in ((1920, 1080), (1440, 900), (1366, 768), (1024, 768), (390, 844)):
                browser, page, errors = await _open(pw, control_center, width, height)
                for tab in ("preflight", "mission", "tasks"):
                    await page.evaluate(f"() => window.hipControlCenter.showTab('{tab}')")
                    await page.wait_for_timeout(500)
                    result = await page.evaluate(AUDIT_JS)
                    found[f"{width}/{tab}"] = result
                found[f"{width}/errors"] = errors
                await browser.close()
        return found

    found = asyncio.run(run())
    for key, result in found.items():
        if key.endswith("/errors"):
            assert result == [], (key, result)
            continue
        assert result["page_overflow"] <= 0, (key, result)
        assert result["issues"] == [], (key, result["issues"][:10])


def test_very_long_values_wrap_instead_of_widening_the_page(control_center):
    from playwright.async_api import async_playwright

    async def run():
        found = {}
        async with async_playwright() as pw:
            for width, height in ((1366, 768), (390, 844)):
                browser, page, _ = await _open(pw, control_center, width, height)
                for tab in ("preflight", "mission"):
                    await page.evaluate(f"() => window.hipControlCenter.showTab('{tab}')")
                    await page.wait_for_timeout(300)
                    injected = await page.evaluate(LONG_VALUES_JS)
                    result = await page.evaluate(AUDIT_JS)
                    found[f"{width}/{tab}"] = (injected, result)
                await browser.close()
        return found

    for key, (injected, result) in asyncio.run(run()).items():
        assert injected > 20, key
        assert result["page_overflow"] <= 0, (key, result)
        spills = [i for i in result["issues"] if i.startswith(("content_spills", "beyond_viewport"))]
        assert spills == [], (key, spills[:10])


def test_sidebar_tabs_chat_and_sections_behave(control_center):
    from playwright.async_api import async_playwright

    async def run():
        out = {}
        async with async_playwright() as pw:
            browser, page, errors = await _open(pw, control_center, 1600, 900)
            main_width = "() => document.querySelector('main').getBoundingClientRect().width"
            before = await page.evaluate(main_width)
            await page.click("#sideToggleBtn")
            await page.wait_for_timeout(300)
            out["main_grows"] = (before, await page.evaluate(main_width))
            out["aria_expanded"] = await page.get_attribute("#sideToggleBtn", "aria-expanded")
            # keyboard tabs, remembered tab and the address
            await page.focus("#tab-preflight")
            await page.keyboard.press("ArrowRight")
            out["arrow"] = await page.evaluate("() => [document.activeElement.id, document.querySelector('.tab-panel.active').id, location.hash]")
            await page.keyboard.press("End")
            # a sidebar section folds on its heading
            await page.click("#sideToggleBtn")
            await page.click("#humanAssistancePanel > h3")
            out["folded"] = await page.evaluate("() => document.getElementById('humanAssistancePanel').classList.contains('collapsed')")
            # the chat resizes when docked
            handle = await page.evaluate("() => { const r = document.getElementById('chatResize').getBoundingClientRect(); return [r.x + r.width / 2, r.y + 300]; }")
            dock = "() => Math.round(document.getElementById('chatDock').getBoundingClientRect().width)"
            width0 = await page.evaluate(dock)
            await page.mouse.move(*handle)
            await page.mouse.down()
            await page.mouse.move(handle[0] - 100, handle[1], steps=5)
            await page.mouse.up()
            out["chat_width"] = (width0, await page.evaluate(dock))
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            out["after_reload"] = await page.evaluate("""() => ({
                side_collapsed: document.querySelector('.shell').classList.contains('side-collapsed'),
                tab: document.querySelector('.tab-panel.active').id,
                folded: document.getElementById('humanAssistancePanel').classList.contains('collapsed'),
                chat: Math.round(document.getElementById('chatDock').getBoundingClientRect().width)})""")
            # "Open the Teach panel" opens the folded Teach section and brings it into view
            await page.evaluate("() => window.hipControlCenter.revealHumanAssistance()")
            await page.wait_for_timeout(800)
            out["teach"] = await page.evaluate("""() => { const s = document.getElementById('humanAssistancePanel'), r = s.getBoundingClientRect();
                return {open: !s.classList.contains('collapsed'), in_view: r.top < innerHeight && r.bottom > 0}; }""")
            # a toast keeps its time when an older one's timer runs out
            await page.evaluate("() => window.hipControlCenter.toast('first')")
            await page.wait_for_timeout(3000)
            await page.evaluate("() => window.hipControlCenter.toast('second')")
            await page.wait_for_timeout(1200)
            out["toast"] = await page.evaluate("() => !document.getElementById('toast').classList.contains('hidden') && document.getElementById('toast').textContent")
            out["errors"] = errors
            await browser.close()
            # a deep link opens its tab
            browser, page, errors = await _open(pw, control_center + "#tasks", 1600, 900)
            out["deep_link"] = await page.evaluate("() => [document.querySelector('.tab-panel.active').id, document.getElementById('tab-tasks').getAttribute('aria-selected')]")
            await browser.close()
            # below 1100 px the sidebar is a drawer: hidden, opened by Controls, closed by Escape
            browser, page, errors = await _open(pw, control_center, 1024, 768)
            hidden = "() => document.querySelector('.sidebar').getBoundingClientRect().right <= 0"
            out["drawer"] = [await page.evaluate(hidden)]
            await page.click("#sideToggleBtn")
            await page.wait_for_timeout(400)
            out["drawer"].append(await page.evaluate(hidden))
            out["drawer_audit"] = await page.evaluate(AUDIT_JS)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)
            out["drawer"].append(await page.evaluate(hidden))
            out["title"] = await page.title()
            await browser.close()
        return out

    out = asyncio.run(run())
    assert out["main_grows"][1] >= out["main_grows"][0] + 250 and out["aria_expanded"] == "false"
    assert out["arrow"] == ["tab-mission", "mission", "#mission"]
    assert out["folded"] is True
    assert out["chat_width"][1] >= out["chat_width"][0] + 80
    assert out["after_reload"]["side_collapsed"] is False and out["after_reload"]["tab"] == "governance"
    assert out["after_reload"]["folded"] is True and abs(out["after_reload"]["chat"] - out["chat_width"][1]) <= 2
    assert out["teach"] == {"open": True, "in_view": True}
    assert out["toast"] == "second"
    assert out["errors"] == []
    assert out["deep_link"] == ["tasks", "true"]
    assert out["drawer"] == [True, False, True]
    assert out["drawer_audit"]["page_overflow"] <= 0 and out["drawer_audit"]["issues"] == []
    assert out["title"].endswith("HIP Agent Control Center")


def test_both_copies_are_identical_and_carry_the_layout_rules():
    for name in ("index.html", "app.js", "styles.css"):
        assert (ROOT / "webui" / name).read_bytes() == (ROOT / "backend" / "webui" / name).read_bytes(), name
    css = (ROOT / "webui" / "styles.css").read_text(encoding="utf-8")
    for needle in ("container-type:inline-size", "@container main", "repeat(auto-fill,minmax(172px,1fr))",
                   "body{overflow-wrap:break-word}", ".side-section.collapsed", ".chat-resize", ":focus-visible"):
        assert needle in css, needle
    html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    for needle in ('id="sideToggleBtn"', 'role="tablist"', 'role="tab"', 'role="tabpanel"', 'id="chatResize"', 'id="sideBackdrop"',
                   'aria-live="polite"', 'rel="icon"'):
        assert needle in html, needle
    js = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
    for needle in ("function initLayout", "function initSideSections", "function revealHumanAssistance", "function initChatResize",
                   "clearTimeout(state.toastTimer)", "initLayout();"):
        assert needle in js, needle
    # R35's Teach link looked for a main-area panel the Teach controls are not in.
    assert '$("humanAssistState")?.closest(".panel")' not in js
