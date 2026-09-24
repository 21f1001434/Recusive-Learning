from pathlib import Path
from types import MethodType
import inspect

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.phase_form_entry import ensure_phase_form_entry


class FakeVision:
    async def locate_visual_target(self, **kwargs):
        return {
            "available": True,
            "target_visible": True,
            "x_ratio": 0.91,
            "y_ratio": 0.12,
            "confidence": 0.98,
            "source": "vision",
            "reason": "unique top-right + Add",
        }


class FakeDesktopTool:
    def __init__(self):
        self.calls = []
    def enabled(self):
        return True
    async def click_viewport_ratio(self, page, **kwargs):
        self.calls.append(kwargs)
        return {"pass": True, "executor": "pyautogui-mcp", "point": {"x": 1000, "y": 120}}


@pytest.mark.asyncio
async def test_visual_structural_target_is_located_by_vision_but_clicked_by_pyautogui(tmp_path: Path):
    session = object.__new__(BrowserSession)
    session.config = AppConfig()
    session.config.pyautogui.visual_structural_recovery_enabled = True
    session.config.pyautogui.verify_after_click_event = True
    session.pyautogui_tool = FakeDesktopTool()
    session.vision_runtime = FakeVision()
    session.page = object()
    session.run_dir = tmp_path
    session.action_events = []

    async def bu(self, *, max_elements=120):
        return {"available": True, "elements": [{"label": "+ Add"}]}
    async def cursor(self):
        return {"cursor": 1}
    async def window(self, _cursor):
        return {"events": [{"type": "click", "trusted": True}]}
    async def ready(self):
        return None

    session.browser_use_recovery_context = MethodType(bu, session)
    session.mark_dom_event_cursor = MethodType(cursor, session)
    session.collect_dom_event_window = MethodType(window, session)
    session.wait_ready = MethodType(ready, session)

    out = await BrowserSession.click_visual_structural_target(
        session, label="+ Add", phase="Data Maps", context="top-right page opener"
    )
    assert out["pass"] is True
    assert out["executor"] == "pyautogui-mcp"
    assert session.pyautogui_tool.calls[0]["x_ratio"] == pytest.approx(0.91)
    assert session.pyautogui_tool.calls[0]["action"].startswith("structural_opener")


def test_in_page_form_controller_has_visual_add_recovery_before_route_reset():
    src = inspect.getsource(ensure_phase_form_entry)
    visual = src.index('click_visual_structural_target')
    reset = src.index('_react_route_back(browser, listing_url', visual)
    assert visual < reset
    assert 'label="+ Add"' in src
    assert 'wrong_route_after_visual_add' in src
    assert 'form_open_after_visual_add' in src
