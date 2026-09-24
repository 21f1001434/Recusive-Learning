import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.config import AppConfig
from hip_id_agent.pyautogui_tool import PyAutoGUIFallbackTool


class BrowserBackedPyAutoGUIMCP:
    """Test double for the MCP desktop server.

    It consumes desktop coordinates exactly like pyautogui-mcp, then maps them
    back into the headless Chromium viewport. This validates the HIP coordinate
    conversion and PRIMARY MCP call path without pretending this Linux runner is
    a real Windows desktop certification.
    """
    started = True

    def __init__(self, page):
        self.page = page
        self.calls = []

    async def _metrics(self):
        return await self.page.evaluate("""() => ({
          screenX:Number(window.screenX||0), screenY:Number(window.screenY||0),
          outerWidth:Number(window.outerWidth||window.innerWidth||0), outerHeight:Number(window.outerHeight||window.innerHeight||0),
          innerWidth:Number(window.innerWidth||0), innerHeight:Number(window.innerHeight||0),
          screenWidth:Number(window.screen?.width||0), screenHeight:Number(window.screen?.height||0)
        })""")

    async def size(self):
        m = await self._metrics()
        return int(m["screenWidth"]), int(m["screenHeight"])

    async def click(self, x, y, duration=0.0):
        self.calls.append(("click", int(x), int(y)))
        m = await self._metrics()
        border_x = max(0.0, (m["outerWidth"] - m["innerWidth"]) / 2.0)
        chrome_y = max(0.0, m["outerHeight"] - m["innerHeight"] - border_x)
        vx = float(x) - m["screenX"] - border_x
        vy = float(y) - m["screenY"] - chrome_y
        await self.page.mouse.click(vx, vy)

    async def hotkey(self, keys):
        self.calls.append(("hotkey", tuple(keys)))
        mapping = {"ctrl": "Control", "alt": "Alt", "shift": "Shift", "win": "Meta"}
        await self.page.keyboard.press("+".join(mapping.get(k.lower(), k) for k in keys))

    async def write(self, text, interval=0.0):
        self.calls.append(("write", text))
        await self.page.keyboard.type(text, delay=max(0, int(interval * 1000)))

    async def press(self, keys, presses=1, interval=0.0):
        key = keys[0] if isinstance(keys, list) else keys
        self.calls.append(("press", key))
        for _ in range(int(presses or 1)):
            await self.page.keyboard.press(str(key))

    def capability_status(self):
        return {"available": True}


@pytest.mark.asyncio
async def test_v228_all_sections_same_page_with_pyautogui_mcp_primary(tmp_path: Path):
    cfg = AppConfig()
    cfg.portal.headless = False
    cfg.pyautogui.windows_only = False
    cfg.pyautogui.interaction_mode = "primary"
    cfg.pyautogui.allow_mutation_clicks = True
    cfg.pyautogui.verify_after_click_event = False
    html = Path(__file__).resolve().parents[1] / "local_mock_hip" / "mock_hip.html"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.set_content(html.read_text(encoding="utf-8"))
        mcp = BrowserBackedPyAutoGUIMCP(page)
        tool = PyAutoGUIFallbackTool(cfg, tmp_path)
        tool.set_mcp_backend(mcp)

        assert tool.should_primary("click") is True
        assert tool.should_primary("fill") is True
        assert tool.should_primary("search") is True
        assert tool.should_primary("press") is True

        for section in ["Data Maps", "Document Types", "Rules", "Transport Profiles", "BizFlow"]:
            nav = page.get_by_role("button", name=section, exact=True)
            await tool.click_locator(page, nav, action=f"open {section}", selector=f"nav:{section}")
            assert await page.locator("#title").inner_text() == section
            before = page.url

            add = page.locator("#page-add")
            result = await tool.click_locator(page, add, action=f"structural_opener {section} + Add", selector="#page-add")
            assert result["executor"] == "pyautogui-mcp"
            assert page.url == before, f"{section} + Add must be in-page"

            if section == "BizFlow":
                assert await page.locator("#picker").is_visible()
                await tool.click_locator(page, page.locator("#biz-template"), action="BizFlow template", selector="#biz-template")
                assert await page.locator("#tabs").is_visible()
            assert await page.locator("#form").is_visible()

            value = section.replace(" ", "_") + "_V228"
            fill = await tool.fill_locator(page, page.locator("#name"), value, selector="#name")
            assert fill["executor"] == "pyautogui-mcp"
            assert await page.locator("#name").input_value() == value

        assert any(c[0] == "click" for c in mcp.calls)
        assert any(c[0] == "write" for c in mcp.calls)
        await browser.close()
