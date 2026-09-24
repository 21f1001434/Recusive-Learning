from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.pyautogui_tool import PyAutoGUIFallbackTool


def test_pyautogui_is_primary_and_governed_mutation_enabled(tmp_path: Path):
    cfg = AppConfig()
    assert cfg.pyautogui.enabled is True
    assert cfg.pyautogui.interaction_mode == "primary"
    assert cfg.pyautogui.allow_mutation_clicks is True
    tool = PyAutoGUIFallbackTool(cfg, tmp_path)
    assert tool._is_mutating_action("Save") is True
    assert tool._is_mutating_action("Deploy") is True
    assert tool._is_mutating_action("+ Add") is False
    assert tool._is_mutating_action("Next") is False


def test_windows_only_dependency_is_packaged():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8").lower()
    assert "pyautogui==0.9.54" in requirements
    assert "platform_system" in requirements
    assert "pyautogui==0.9.54" in pyproject


def test_browser_session_exact_value_matching_is_strict():
    assert BrowserSession._exact_fill_value_matches("Alpha  Beta", "Alpha Beta") is True
    assert BrowserSession._exact_fill_value_matches("1.0", "1") is False
    assert BrowserSession._exact_fill_value_matches("UAT", "DEV") is False
    assert BrowserSession._exact_fill_value_matches(None, "DEV") is False


@pytest.mark.asyncio
async def test_pyautogui_fill_requires_exact_post_verification(tmp_path: Path):
    class FakeTool:
        def enabled(self):
            return True

        async def fill_locator(self, page, locator, value, *, selector=""):
            return {"pass": True, "selector": selector}

    session = object.__new__(BrowserSession)
    session.pyautogui_tool = FakeTool()
    session.page = object()
    session.run_dir = tmp_path
    session.action_events = []

    async def verify(self, locator, expected, *, selector=""):
        return {"pass": True, "selector": selector}

    session._verify_exact_fill_commit = MethodType(verify, session)
    out = await BrowserSession._try_pyautogui_fill(session, locator=object(), value="ABC", selector="#field")
    assert out["pass"] is True
    assert (tmp_path / "pyautogui").exists()


def test_browser_session_action_ladder_contains_pyautogui_primary():
    import inspect

    click = inspect.getsource(BrowserSession.click_and_wait)
    fill = inspect.getsource(BrowserSession.fill_and_log)
    press = inspect.getsource(BrowserSession.press_and_log)
    assert "_try_pyautogui_click" in click
    assert "pyautogui-mcp-primary" in click
    assert click.index("_try_pyautogui_click") < click.index("playwright_mcp_backend.click")
    assert "_try_pyautogui_fill" in fill
    assert fill.index("_try_pyautogui_fill") < fill.index("playwright_mcp_backend.fill")
    assert "already-committed" in fill
    assert "HIP_EXACT_FILL_NOT_COMMITTED" in fill
    assert "_try_pyautogui_press" in press
    assert press.index("_try_pyautogui_press") < press.index("playwright_mcp_backend.press")
