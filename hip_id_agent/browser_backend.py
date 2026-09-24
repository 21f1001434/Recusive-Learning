from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from .mcp_stdio import MCPClientError


class BrowserActionBackend(ABC):
    """Execution abstraction for local Playwright or pre-existing MCP-backed browser actions.

    No custom MCP server is built here. Implementations either wrap the local Playwright session or
    call an already installed MCP server such as `chrome-devtools-mcp` over stdio.
    """

    name: str = "base"

    @abstractmethod
    async def navigate(self, url: str) -> None: ...

    @abstractmethod
    async def click(self, selector_or_description: str) -> None: ...

    @abstractmethod
    async def fill(self, selector_or_description: str, value: str) -> None: ...

    @abstractmethod
    async def press(self, selector_or_description: str, key: str) -> None: ...

    @abstractmethod
    async def screenshot(self, name: str | Path) -> str: ...

    @abstractmethod
    async def get_dom_snapshot(self) -> Dict[str, Any]: ...

    @abstractmethod
    async def get_network_events(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_current_url(self) -> str: ...


class PlaywrightBackend(BrowserActionBackend):
    name = "playwright"

    def __init__(self, session: Any):
        self.session = session

    async def navigate(self, url: str) -> None:
        if not self.session.page:
            raise RuntimeError("Browser session not started")
        await self.session.navigate(url)

    async def click(self, selector_or_description: str) -> None:
        if not self.session.page:
            raise RuntimeError("Browser session not started")
        loc = self.session.page.locator(selector_or_description).first
        await self.session.click_and_wait(action="backend_click", locator=loc, selector=selector_or_description)

    async def fill(self, selector_or_description: str, value: str) -> None:
        if not self.session.page:
            raise RuntimeError("Browser session not started")
        loc = self.session.page.locator(selector_or_description).first
        await self.session.fill_and_log(locator=loc, value=value, selector=selector_or_description, action_type="fill")

    async def press(self, selector_or_description: str, key: str) -> None:
        if not self.session.page:
            raise RuntimeError("Browser session not started")
        loc = self.session.page.locator(selector_or_description).first
        await self.session.press_and_log(locator=loc, key=key, selector=selector_or_description)

    async def screenshot(self, name: str | Path) -> str:
        return await self.session.screenshot(Path(name))

    async def get_dom_snapshot(self) -> Dict[str, Any]:
        page = self.session.page
        if not page:
            return {}
        html = await page.content()
        text = await page.locator("body").inner_text(timeout=5000)
        return {"url": page.url, "html": html, "text": text[:20000]}

    async def get_network_events(self) -> List[Dict[str, Any]]:
        from dataclasses import asdict
        return [asdict(e) for e in self.session.network_tab_events]

    async def get_current_url(self) -> str:
        return self.session.page.url if self.session.page else ""


class MCPBackend(BrowserActionBackend):
    """Generic MCP backend wrapper.

    By default this binds to Chrome DevTools MCP. The BrowserSession also attaches
    Microsoft's official Playwright MCP to the same browser for accessibility snapshots,
    safe actions and verification. Tests can inject a compatible tool_client.
    """

    name = "mcp"

    def __init__(self, tool_client: Optional[Any] = None, *, run_dir: str | Path | None = None, config: Any = None):
        if tool_client is not None:
            self.tool_client = tool_client
        elif config is not None:
            direct_enabled = bool(getattr(getattr(config, "mcp", None), "chrome_devtools_mcp_direct_backend_enabled", False))
            chrome_enabled = bool(getattr(getattr(config, "mcp", None), "use_chrome_devtools_mcp", False))
            if direct_enabled and chrome_enabled:
                from .chrome_devtools_mcp import ChromeDevToolsMCPBackend
                self.tool_client = ChromeDevToolsMCPBackend.from_config(config, Path(run_dir or "."))
            else:
                # Fail closed: merely selecting/constructing MCPBackend must never
                # spawn an external process unless the project explicitly enables
                # direct Chrome DevTools MCP execution.
                self.tool_client = None
        else:
            self.tool_client = None

    async def _require(self) -> Any:
        if self.tool_client is None:
            raise RuntimeError(
                "MCP backend selected, but no Chrome DevTools MCP adapter/client is bound. "
                "Install/use the pre-existing chrome-devtools-mcp server or use browser_backend=playwright/auto."
            )
        if hasattr(self.tool_client, "start") and not getattr(self.tool_client, "started", True):
            await self.tool_client.start()
        return self.tool_client

    async def navigate(self, url: str) -> None:
        return await (await self._require()).navigate(url)

    async def click(self, selector_or_description: str) -> None:
        return await (await self._require()).click(selector_or_description)

    async def fill(self, selector_or_description: str, value: str) -> None:
        return await (await self._require()).fill(selector_or_description, value)

    async def press(self, selector_or_description: str, key: str) -> None:
        return await (await self._require()).press(selector_or_description, key)

    async def screenshot(self, name: str | Path) -> str:
        return await (await self._require()).screenshot(name)

    async def get_dom_snapshot(self) -> Dict[str, Any]:
        return await (await self._require()).get_dom_snapshot()

    async def get_network_events(self) -> List[Dict[str, Any]]:
        return await (await self._require()).get_network_events()

    async def get_current_url(self) -> str:
        return await (await self._require()).get_current_url()


async def validate_chrome_devtools_mcp(config: Any, run_dir: str | Path) -> Dict[str, Any]:
    """Start Chrome DevTools MCP, list tools, and return a redacted capability summary."""
    from .chrome_devtools_mcp import ChromeDevToolsMCPBackend

    backend = ChromeDevToolsMCPBackend.from_config(config, Path(run_dir))
    try:
        await backend.start()
        tool_names = sorted(backend.client.tools)
        required = ["new_page", "navigate_page", "take_snapshot", "click", "fill", "press_key", "take_screenshot", "list_network_requests", "list_console_messages"]
        return {
            "available": True,
            "backend": backend.name,
            "tool_count": len(tool_names),
            "tools": tool_names,
            "required_tool_status": {name: name in backend.client.tools for name in required},
        }
    except Exception as exc:
        return {
            "available": False,
            "backend": "chrome-devtools-mcp",
            "error": str(exc),
            "recovery": "Install Bun and run `bun install`, or set mcp.browser_backend to playwright/auto.",
        }
    finally:
        try:
            await backend.close()
        except Exception:
            pass


async def validate_playwright_mcp(config: Any, run_dir: str | Path) -> Dict[str, Any]:
    """Start Microsoft's official Playwright MCP and list its published tools."""
    from .playwright_mcp import validate_playwright_mcp as _validate
    return await _validate(config, run_dir)


async def validate_hip_intelligence_mcp(config: Any, run_dir: str | Path) -> Dict[str, Any]:
    """Start the existing HIP Intelligence MCP and prove Layer-11 semantic tools."""
    from .hip_intelligence_mcp import HIPIntelligenceMCPBackend

    base = Path(run_dir)
    backend = HIPIntelligenceMCPBackend.from_config(
        config, run_dir=base, memory_dir=base / "memory" / "action_trajectories"
    )
    required = [
        "build_web_representation", "plan_form_action",
        "resolve_semantic_control", "rank_semantic_candidates",
        "verify_semantic_action_effect", "get_semantic_control_fingerprint",
        "get_semantic_control_capabilities",
        "hip_get_current_surface", "hip_get_form_schema", "hip_find_control",
        "hip_find_owned_popup", "hip_get_repeatable_rows", "hip_get_required_fields",
        "hip_get_current_values", "hip_compare_expected_actual", "hip_get_safe_actions",
        "hip_verify_action_effect", "hip_get_route_identity", "hip_get_form_generation",
    ]
    try:
        await backend.start()
        tools = sorted(backend.client.tools)
        capabilities = await backend.semantic_capabilities() if "get_semantic_control_capabilities" in backend.client.tools else {}
        status = {name: name in backend.client.tools for name in required}
        return {
            "available": all(status.values()),
            "backend": "hip-intelligence-mcp",
            "tool_count": len(tools),
            "tools": tools,
            "required_tool_status": status,
            "semantic_capabilities": capabilities,
            "semantic_tools_ready": all(status.values()),
        }
    except Exception as exc:
        return {
            "available": False,
            "backend": "hip-intelligence-mcp",
            "error": str(exc),
            "required_tool_status": {name: False for name in required},
            "semantic_tools_ready": False,
        }
    finally:
        try:
            await backend.close()
        except Exception:
            pass


async def validate_pyautogui_mcp(config: Any, run_dir: str | Path) -> Dict[str, Any]:
    """Validate the optional PyAutoGUI MCP desktop fallback without making it primary."""
    py_cfg = getattr(config, "pyautogui", None)
    required_names = [
        "pyautogui_size", "pyautogui_position", "pyautogui_click",
        "pyautogui_write", "pyautogui_press", "pyautogui_hotkey", "pyautogui_screenshot",
    ]
    if py_cfg is None or not bool(getattr(py_cfg, "enabled", True)) or not bool(getattr(py_cfg, "mcp_enabled", True)):
        return {
            "available": False, "enabled": False, "backend": "pyautogui-mcp",
            "required": bool(getattr(py_cfg, "mcp_required", False)) if py_cfg is not None else False,
            "required_tool_status": {name: False for name in required_names},
            "reason": "disabled",
        }
    if bool(getattr(py_cfg, "windows_only", True)) and __import__("platform").system().lower() != "windows":
        return {
            "available": False, "enabled": True, "backend": "pyautogui-mcp",
            "required": bool(getattr(py_cfg, "mcp_required", False)),
            "required_tool_status": {name: False for name in required_names},
            "reason": "windows_desktop_required",
        }
    from .pyautogui_mcp import PyAutoGUIMCPBackend
    backend = PyAutoGUIMCPBackend.from_config(config, Path(run_dir))
    try:
        await backend.start()
        status = backend.capability_status()
        status["required"] = bool(getattr(py_cfg, "mcp_required", False))
        return status
    except Exception as exc:
        return {
            "available": False, "enabled": True, "backend": "pyautogui-mcp",
            "required": bool(getattr(py_cfg, "mcp_required", False)),
            "required_tool_status": {name: False for name in required_names},
            "error": str(exc),
        }
    finally:
        try:
            await backend.close()
        except Exception:
            pass


async def validate_dual_browser_mcps(config: Any, run_dir: str | Path) -> Dict[str, Any]:
    """Validate both browser MCPs plus the HIP semantic-intelligence MCP."""
    base = Path(run_dir)
    playwright_result = await validate_playwright_mcp(config, base / "playwright")
    chrome_result = await validate_chrome_devtools_mcp(config, base / "chrome_devtools")
    hip_result = await validate_hip_intelligence_mcp(config, base / "hip_intelligence")
    pyautogui_result = await validate_pyautogui_mcp(config, base / "pyautogui")
    browser_available = bool(playwright_result.get("available") and chrome_result.get("available"))
    py_required = bool(getattr(getattr(config, "pyautogui", None), "mcp_required", False))
    py_ok = bool(pyautogui_result.get("available")) if py_required else True
    all_available = bool(browser_available and hip_result.get("available") and hip_result.get("semantic_tools_ready") and py_ok)
    return {
        "available": browser_available,
        "all_required_available": all_available,
        "mode": "layered-mcp",
        "playwright_mcp": playwright_result,
        "chrome_devtools_mcp": chrome_result,
        "hip_intelligence_mcp": hip_result,
        "pyautogui_mcp": pyautogui_result,
        "execution_contract": {
            "plan_source": "AutoWebGLM + HIP Portal form knowledge + input.json",
            "semantic_brain": "HIP Intelligence MCP Layer-11 value-free semantic consensus",
            "primary_safe_action_executor": "PyAutoGUI MCP",
            "governed_executor": "PyAutoGUI MCP physical interaction only after Layer-11 semantic proof; Playwright MCP deterministic fallback",
            "legacy_direct_fallback": "disabled while the Layer-11 semantic runtime is active; available only in explicitly standalone/offline compatibility paths",
            "network_console_cdp_judge": "Chrome DevTools MCP independent witness; not an operational executor",
            "desktop_primary": "PyAutoGUI MCP primary physical click/type/key executor for semantically proven targets; visual-coordinate recovery is structural-only",
            "section_gate": "semantic target proof + DOM + text model + vision model + exact effect verification",
        },
    }
