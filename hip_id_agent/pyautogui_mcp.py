from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .mcp_stdio import MCPClientError, MCPStdioClient
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


class PyAutoGUIMCPError(RuntimeError):
    """Raised when the optional desktop MCP channel cannot be used safely."""


def _content_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result)
    chunks: List[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text") or ""))
    return "\n".join(x for x in chunks if x)


def _structured_payload(result: Any) -> Any:
    if isinstance(result, dict):
        for key in ("structuredContent", "structured_content", "data", "result"):
            value = result.get(key)
            if value not in (None, ""):
                return value
    text = _content_text(result).strip()
    if not text:
        return result
    try:
        return json.loads(text)
    except Exception:
        return text


def _tool_props(client: MCPStdioClient, name: str) -> Dict[str, Any]:
    tool = client.tools.get(name)
    schema = getattr(tool, "input_schema", {}) if tool else {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    return props if isinstance(props, dict) else {}


class PyAutoGUIMCPBackend:
    """MCP wrapper around the community ``pyautogui-mcp`` package.

    This is the audited visible-desktop executor for a local Windows session after
    HIP semantic target proof has already happened. The higher-level
    ``PyAutoGUIFallbackTool`` owns target/risk gating and exact effect verification.
    Playwright stays attached for discovery/verification and deterministic fallback.
    """

    name = "pyautogui-mcp"
    DEFAULT_PREFIX = "pyautogui_"
    REQUIRED_TOOLS = (
        "pyautogui_size",
        "pyautogui_position",
        "pyautogui_click",
        "pyautogui_write",
        "pyautogui_press",
        "pyautogui_hotkey",
        "pyautogui_screenshot",
    )

    def __init__(self, client: MCPStdioClient, run_dir: Path, *, prefix: str = DEFAULT_PREFIX):
        self.client = client
        self.run_dir = Path(run_dir)
        self.prefix = str(prefix or self.DEFAULT_PREFIX)
        self.started = False
        self.events: List[Dict[str, Any]] = []
        self._seq = 0
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(cls, config: Any, run_dir: str | Path) -> "PyAutoGUIMCPBackend":
        py_cfg = getattr(config, "pyautogui", config)
        command = str(getattr(py_cfg, "mcp_command", "") or "").strip()
        args = list(getattr(py_cfg, "mcp_args", []) or [])
        prefix = str(getattr(py_cfg, "mcp_prefix", cls.DEFAULT_PREFIX) or cls.DEFAULT_PREFIX)
        if not command:
            command = sys.executable
            if not args:
                args = ["-m", "pyautogui_mcp", "--transport", "stdio", "--prefix", prefix]
        elif not args:
            args = ["--transport", "stdio", "--prefix", prefix]
        env = os.environ.copy()
        env["PYAUTOGUI_PAUSE"] = str(max(0.0, float(getattr(py_cfg, "pause_seconds", 0.05))))
        client = MCPStdioClient(
            command=command,
            args=args,
            env=env,
            startup_timeout_seconds=int(getattr(py_cfg, "mcp_startup_timeout_seconds", 20) or 20),
            request_timeout_seconds=float(getattr(py_cfg, "mcp_request_timeout_seconds", 15.0) or 15.0),
            shutdown_timeout_seconds=float(getattr(py_cfg, "mcp_shutdown_timeout_seconds", 3.0) or 3.0),
            stream_limit_bytes=int(getattr(py_cfg, "mcp_stream_limit_bytes", 32 * 1024 * 1024) or 32 * 1024 * 1024),
        )
        return cls(client, Path(run_dir), prefix=prefix)

    def _name(self, suffix: str) -> str:
        return f"{self.prefix}{suffix}"

    async def start(self) -> None:
        await self.client.start()
        self.started = True
        status = self.capability_status()
        safe_write_json(self.run_dir / "pyautogui_mcp_capabilities.json", status)

    async def close(self) -> None:
        try:
            await self.client.close()
        finally:
            self.started = False
            safe_write_json(self.run_dir / "pyautogui_mcp_action_log.json", self.events)

    def capability_status(self) -> Dict[str, Any]:
        names = sorted(self.client.tools)
        required = [self._name(x.removeprefix("pyautogui_")) for x in self.REQUIRED_TOOLS]
        status = {name: name in self.client.tools for name in required}
        return {
            "available": bool(self.started and all(status.values())),
            "backend": self.name,
            "tool_count": len(names),
            "tools": names,
            "required_tool_status": status,
            "desktop_session_required": True,
            "platform": platform.system(),
            "role": "primary visible-desktop executor for semantically proven HIP web controls",
        }

    async def _call(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None, *, action: str = "") -> Any:
        self._seq += 1
        event = {
            "sequence": self._seq,
            "tool": tool_name,
            "action": action or tool_name,
            "arguments": mask_sensitive_data(arguments or {}),
            "success": False,
        }
        try:
            result = await self.client.call_tool(tool_name, arguments or {})
            if isinstance(result, dict) and (result.get("isError") or result.get("is_error")):
                raise MCPClientError(_content_text(result) or f"{tool_name} returned an MCP error")
            event["success"] = True
            event["result_excerpt"] = _content_text(result)[:1500]
            return result
        except Exception as exc:
            event["error"] = mask_sensitive_string(str(exc))[:2000]
            raise
        finally:
            self.events.append(event)

    async def diagnose(self) -> Dict[str, Any]:
        name = self._name("diagnose")
        if name not in self.client.tools:
            return {"available": False, "reason": "diagnose_tool_not_published"}
        result = await self._call(name, {}, action="diagnose")
        return {"available": True, "payload": mask_sensitive_data(_structured_payload(result))}

    async def size(self) -> Tuple[int, int]:
        name = self._name("size")
        result = await self._call(name, {}, action="screen_size")
        payload = _structured_payload(result)
        if isinstance(payload, dict):
            width = payload.get("width", payload.get("x"))
            height = payload.get("height", payload.get("y"))
            if width is not None and height is not None:
                return int(width), int(height)
        if isinstance(payload, (list, tuple)) and len(payload) >= 2:
            return int(payload[0]), int(payload[1])
        text = str(payload)
        import re
        nums = [int(x) for x in re.findall(r"\d+", text)]
        if len(nums) >= 2:
            return nums[0], nums[1]
        raise PyAutoGUIMCPError(f"Could not parse desktop size from {name}: {text[:500]}")

    async def position(self) -> Tuple[int, int]:
        name = self._name("position")
        result = await self._call(name, {}, action="cursor_position")
        payload = _structured_payload(result)
        if isinstance(payload, dict) and payload.get("x") is not None and payload.get("y") is not None:
            return int(payload["x"]), int(payload["y"])
        if isinstance(payload, (list, tuple)) and len(payload) >= 2:
            return int(payload[0]), int(payload[1])
        raise PyAutoGUIMCPError(f"Could not parse cursor position from {name}")

    async def click(self, x: int, y: int, *, duration: float = 0.12, button: str = "left") -> None:
        name = self._name("click")
        props = _tool_props(self.client, name)
        args: Dict[str, Any] = {"x": int(x), "y": int(y)}
        optional = {
            "clicks": 1,
            "button": str(button),
            "interval": 0.0,
            "duration": max(0.0, float(duration)),
        }
        for key, value in optional.items():
            if key in props:
                args[key] = value
        await self._call(name, args, action="desktop_click")

    async def write(self, message: str, *, interval: float = 0.01) -> None:
        name = self._name("write")
        props = _tool_props(self.client, name)
        args: Dict[str, Any] = {}
        if "message" in props or "text" not in props:
            args["message"] = str(message)
        else:
            args["text"] = str(message)
        if "interval" in props:
            args["interval"] = max(0.0, float(interval))
        await self._call(name, args, action="desktop_write")

    async def press(self, key: str) -> None:
        name = self._name("press")
        props = _tool_props(self.client, name)
        normalized = str(key or "").strip().lower()
        args: Dict[str, Any] = {}
        if "keys" in props or "key" not in props:
            args["keys"] = normalized
        else:
            args["key"] = normalized
        if "presses" in props:
            args["presses"] = 1
        if "interval" in props:
            args["interval"] = 0.0
        await self._call(name, args, action="desktop_press")

    async def hotkey(self, keys: Iterable[str]) -> None:
        name = self._name("hotkey")
        props = _tool_props(self.client, name)
        key_list = [str(x).strip().lower() for x in keys if str(x).strip()]
        args: Dict[str, Any] = {}
        if "keys" in props or not props:
            args["keys"] = key_list
        elif "hotkeys" in props:
            args["hotkeys"] = key_list
        else:
            # Dynamic wrappers sometimes expose a single variadic-style key field.
            args[next(iter(props))] = key_list
        if "interval" in props:
            args["interval"] = 0.0
        await self._call(name, args, action="desktop_hotkey")

    async def screenshot(self, *, region: Optional[List[int]] = None) -> Any:
        name = self._name("screenshot")
        props = _tool_props(self.client, name)
        args: Dict[str, Any] = {}
        if region is not None and "region" in props:
            args["region"] = [int(x) for x in region]
        return await self._call(name, args, action="desktop_screenshot")
