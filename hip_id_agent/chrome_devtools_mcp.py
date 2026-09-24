from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .mcp_stdio import MCPClientError, MCPStdioClient
from .models import ActionEvent, NetworkTabEvent, utc_now
from .safe_io import safe_write_json
from .security import compact_text, is_secret_target, mask_sensitive_data, mask_sensitive_string


def _result_to_text(result: Any) -> str:
    """Flatten MCP tool result content into text for UID search/reporting."""
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if not isinstance(item, dict):
                    parts.append(str(item))
                elif item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") in {"image", "resource"}:
                    parts.append(json.dumps(mask_sensitive_data(item), ensure_ascii=False, default=str))
                else:
                    parts.append(json.dumps(mask_sensitive_data(item), ensure_ascii=False, default=str))
            return "\n".join(parts)
        return json.dumps(mask_sensitive_data(result), ensure_ascii=False, default=str)
    return str(result)



def _tool_props(client: MCPStdioClient, name: str) -> Dict[str, Any]:
    tool = client.tools.get(name)
    schema = getattr(tool, "input_schema", {}) if tool else {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    return props if isinstance(props, dict) else {}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


class ChromeDevToolsMCPBackend:
    """Adapter for the pre-existing `chrome-devtools-mcp` server.

    This class calls Chrome DevTools MCP tools over stdio. It does not create a custom MCP server.
    It can be used directly in host environments that allow launching `bunx --bun chrome-devtools-mcp`.
    """

    name = "chrome-devtools-mcp"

    def __init__(self, client: MCPStdioClient, run_dir: Path):
        self.client = client
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.action_events: List[ActionEvent] = []
        self.network_events: List[NetworkTabEvent] = []
        self.current_url: str = ""
        self.last_snapshot_text: str = ""
        self._action_seq = 0
        self.started = False

    @classmethod
    def from_config(cls, config: Any, run_dir: Path, *, cdp_endpoint: Optional[str] = None) -> "ChromeDevToolsMCPBackend":
        command = getattr(config.mcp, "chrome_devtools_command", "auto")
        args = list(getattr(config.mcp, "chrome_devtools_args", ["chrome-devtools-mcp@1.6.0", "--no-usage-statistics"]))
        if cdp_endpoint and getattr(config.mcp, "chrome_devtools_attach_same_browser", True):
            joined = " ".join(str(x) for x in args).lower()
            if not any(flag in joined for flag in ("--browserurl", "--browser-url", "--wsendpoint", "--autoconnect")):
                args.append(f"--browser-url={cdp_endpoint}")
        if getattr(config.mcp, "chrome_devtools_experimental_page_id_routing", True):
            if not any(str(x).lower().startswith("--experimentalpageidrouting".lower()) for x in args):
                args.append("--experimentalPageIdRouting")
        timeout = getattr(config.mcp, "chrome_devtools_startup_timeout_seconds", 30)
        request_timeout = getattr(config.mcp, "chrome_devtools_request_timeout_seconds", 30.0)
        shutdown_timeout = getattr(config.mcp, "chrome_devtools_shutdown_timeout_seconds", 3.0)
        backend = cls(
            MCPStdioClient(
                command=command,
                args=args,
                startup_timeout_seconds=int(timeout),
                request_timeout_seconds=float(request_timeout),
                shutdown_timeout_seconds=float(shutdown_timeout),
            ),
            run_dir,
        )
        backend.cdp_endpoint = cdp_endpoint or ""
        backend.launch_args = args
        return backend

    async def start(self) -> None:
        await self.client.start()
        self.started = True

    async def close(self) -> None:
        await self.client.close()

    async def navigate(self, url: str) -> None:
        ev = self._begin_action("navigate", url)
        try:
            tool = "new_page" if "new_page" in self.client.tools else "navigate_page"
            args = {"url": url, "timeout": 0} if tool == "new_page" else {"type": "url", "url": url, "timeout": 0}
            await self.client.call_tool(tool, args)
            self.current_url = url
            await self._finish_action(ev, success=True)
        except Exception as exc:
            await self._finish_action(ev, success=False, error=str(exc))
            raise

    async def click(self, selector_or_description: str) -> None:
        ev = self._begin_action("click", selector_or_description)
        try:
            uid = await self._uid_for(selector_or_description)
            await self.client.call_tool("click", {"uid": uid, "includeSnapshot": False})
            await self._finish_action(ev, success=True)
        except Exception as exc:
            await self._finish_action(ev, success=False, error=str(exc))
            raise

    async def fill(self, selector_or_description: str, value: str) -> None:
        ev = self._begin_action("fill", selector_or_description, value=value)
        try:
            uid = await self._uid_for(selector_or_description)
            await self.client.call_tool("fill", {"uid": uid, "value": value, "includeSnapshot": False})
            await self._finish_action(ev, success=True)
        except Exception as exc:
            await self._finish_action(ev, success=False, error=str(exc))
            raise

    async def press(self, selector_or_description: str, key: str) -> None:
        ev = self._begin_action("press", selector_or_description, value=key)
        try:
            if selector_or_description:
                try:
                    await self.click(selector_or_description)
                except Exception:
                    pass
            await self.client.call_tool("press_key", {"key": key, "includeSnapshot": False})
            await self._finish_action(ev, success=True)
        except Exception as exc:
            await self._finish_action(ev, success=False, error=str(exc))
            raise

    async def screenshot(self, name: str | Path) -> str:
        path = Path(name)
        if not path.is_absolute():
            path = self.run_dir / path
        path.parent.mkdir(parents=True, exist_ok=True)
        ev = self._begin_action("screenshot", str(path))
        try:
            result = await self.client.call_tool("take_screenshot", {})
            saved = self._save_screenshot_result(result, path)
            await self._finish_action(ev, success=True, screenshot_after=saved)
            return saved
        except Exception as exc:
            await self._finish_action(ev, success=False, error=str(exc))
            raise

    async def get_dom_snapshot(self) -> Dict[str, Any]:
        result = await self.client.call_tool("take_snapshot", {})
        text = _result_to_text(result)
        self.last_snapshot_text = text
        return {"url": self.current_url, "snapshot_text": text, "raw": mask_sensitive_data(result)}

    async def get_network_events(self) -> List[Dict[str, Any]]:
        if "list_network_requests" not in self.client.tools:
            return []
        result = await self.client.call_tool("list_network_requests", {})
        redacted = mask_sensitive_data(result)
        text = _result_to_text(redacted)
        event = NetworkTabEvent(
            request_id=f"chrome-devtools-mcp-{len(self.network_events)+1}",
            url="chrome-devtools-mcp:list_network_requests",
            method="MCP",
            status=None,
            resource_type="mcp_network_list",
            response_body_redacted=redacted,
            response_body_text_redacted=compact_text(text, 20000),
            page_context=self.current_url,
            stage="chrome_devtools_mcp",
            source="chrome_devtools_mcp",
        )
        self.network_events.append(event)
        return [asdict(e) for e in self.network_events]

    async def get_console_messages(self) -> Any:
        if "list_console_messages" not in self.client.tools:
            return []
        return mask_sensitive_data(await self.client.call_tool("list_console_messages", {}))

    def learning_capabilities(self) -> Dict[str, Any]:
        tools = sorted(self.client.tools)
        return {
            "backend": self.name,
            "tool_count": len(tools),
            "dom_snapshot": "take_snapshot" in tools,
            "network": "list_network_requests" in tools,
            "console": "list_console_messages" in tools,
            "evaluate": "evaluate_script" in tools,
            "performance_trace": "performance_start_trace" in tools and "performance_stop_trace" in tools,
            "performance_insight": "performance_analyze_insight" in tools,
            "tools": tools,
        }

    async def evaluate_script(self, function: str, *, args: Optional[List[Any]] = None) -> Dict[str, Any]:
        if "evaluate_script" not in self.client.tools:
            return {"available": False, "reason": "evaluate_script tool unavailable"}
        props = _tool_props(self.client, "evaluate_script")
        payload: Dict[str, Any] = {}
        if "function" in props or "script" not in props:
            payload["function"] = function
        else:
            payload["script"] = function
        if args and "args" in props:
            payload["args"] = args
        result = await self.client.call_tool("evaluate_script", payload)
        return {"available": True, "text": _result_to_text(result), "raw": mask_sensitive_data(result)}

    async def start_performance_trace(self, *, reload: bool = False, auto_stop: bool = False) -> Dict[str, Any]:
        if "performance_start_trace" not in self.client.tools:
            return {"started": False, "reason": "performance_start_trace tool unavailable"}
        props = _tool_props(self.client, "performance_start_trace")
        args: Dict[str, Any] = {}
        if "reload" in props:
            args["reload"] = bool(reload)
        if "autoStop" in props:
            args["autoStop"] = bool(auto_stop)
        elif "auto_stop" in props:
            args["auto_stop"] = bool(auto_stop)
        try:
            result = await self.client.call_tool("performance_start_trace", args)
            payload = {"started": True, "captured_at": utc_now(), "result": mask_sensitive_data(result)}
            safe_write_json(self.run_dir / "performance_trace_start.json", payload)
            return payload
        except Exception as exc:
            return {"started": False, "error": mask_sensitive_string(str(exc))}

    async def stop_performance_trace(self) -> Dict[str, Any]:
        if "performance_stop_trace" not in self.client.tools:
            return {"captured": False, "reason": "performance_stop_trace tool unavailable"}
        try:
            result = await self.client.call_tool("performance_stop_trace", {})
            payload = {"captured": True, "captured_at": utc_now(), "result": mask_sensitive_data(result), "text": compact_text(_result_to_text(result), 60000)}
            safe_write_json(self.run_dir / "performance_trace_stop.json", payload)
            return payload
        except Exception as exc:
            return {"captured": False, "error": mask_sensitive_string(str(exc))}

    async def list_pages(self) -> Dict[str, Any]:
        """Return pages across current and legacy Chrome DevTools MCP formats."""
        if "list_pages" not in self.client.tools:
            return {"pages": [], "raw": None}
        result = await self.client.call_tool("list_pages", {})
        text = _result_to_text(result)
        pages: List[Dict[str, Any]] = []

        def add(page_id: Any, url: Any) -> None:
            value = str(url or "").strip().strip('"\'').rstrip(")],.,")
            if not value or not re.match(r"^(?:https?://|about:|chrome://|edge://|devtools://)", value, re.I):
                return
            try:
                pid = int(str(page_id).strip())
            except Exception:
                return
            candidate = {"page_id": pid, "url": value}
            if candidate not in pages:
                pages.append(candidate)

        # Some releases return structured JSON/resources instead of only markdown.
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                url_value = next((value.get(k) for k in ("url", "href", "pageUrl", "page_url") if value.get(k)), None)
                id_value = next((value.get(k) for k in ("pageId", "page_id", "id", "targetId", "target_id") if value.get(k) is not None), None)
                if url_value is not None and id_value is not None:
                    add(id_value, url_value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(result)

        # Current output is usually markdown such as ``1: URL [selected]``;
        # older/newer builds have also used ``1. URL`` and pageId/url labels.
        for line in text.splitlines():
            url_match = re.search(r"((?:https?://|chrome://|edge://|devtools://)[^\s\]\)\"']+|about:[^\s\]\)\"']+)", line, re.I)
            if not url_match:
                continue
            url_value = url_match.group(1)
            prefix = line[: url_match.start()]
            id_match = re.search(r"(?:page(?:Id)?\s*[:=]?\s*)?(\d+)\s*(?:[:.\)-]|$)", prefix, re.I)
            if not id_match:
                id_match = re.search(r"\b(?:pageId|page_id|id|targetId)\s*[:=]\s*[\"']?(\d+)", line, re.I)
            if id_match:
                add(id_match.group(1), url_value)

        # JSON text embedded in a content block is another observed format.
        if not pages:
            for match in re.finditer(r"\{[^{}]{0,1000}\}", text):
                try:
                    walk(json.loads(match.group(0)))
                except Exception:
                    continue

        return {"pages": pages, "raw": mask_sensitive_data(result), "text": text}

    async def select_page_for_url(self, expected_url: str) -> Dict[str, Any]:
        listing = await self.list_pages()
        expected = str(expected_url or "")

        def surface(value: str) -> tuple[str, str]:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(str(value or ""))
                return (parsed.hostname or "").lower(), ((parsed.path or "/").rstrip("/").lower() or "/")
            except Exception:
                return "", str(value or "").split("?", 1)[0].rstrip("/").lower()

        expected_surface = surface(expected)
        matches = [p for p in listing.get("pages", []) if surface(str(p.get("url") or "")) == expected_surface]
        if not matches:
            return {
                "pass": False,
                "expected_url": expected.split("?", 1)[0],
                "pages": [
                    {"page_id": p.get("page_id"), "url": str(p.get("url") or "").split("?", 1)[0]}
                    for p in listing.get("pages", [])
                ],
                "raw_text_excerpt": str(listing.get("text") or "")[:2000],
                "reason": "expected page not visible to Chrome DevTools MCP",
            }
        selected = matches[-1]
        if "select_page" in self.client.tools:
            await self.client.call_tool("select_page", {"pageId": selected["page_id"], "bringToFront": True})
        self.current_url = str(selected["url"] or "")
        return {
            "pass": True,
            "selected": {"page_id": selected["page_id"], "url": self.current_url.split("?", 1)[0]},
            "pages": [
                {"page_id": p.get("page_id"), "url": str(p.get("url") or "").split("?", 1)[0]}
                for p in listing.get("pages", [])
            ],
        }

    async def get_current_url(self) -> str:
        if "list_pages" not in self.client.tools:
            return self.current_url
        result = await self.client.call_tool("list_pages", {})
        text = _result_to_text(result)
        urls = re.findall(r"https?://[^\s)'\"]+", text)
        if urls:
            self.current_url = urls[-1]
        return self.current_url

    async def _uid_for(self, selector_or_description: str) -> str:
        snapshot = await self.get_dom_snapshot()
        text = snapshot.get("snapshot_text", "")
        uid = self._find_uid(text, selector_or_description)
        if not uid:
            raise MCPClientError(f"Could not find Chrome DevTools MCP uid for: {selector_or_description}")
        return uid

    def _find_uid(self, snapshot_text: str, description: str) -> Optional[str]:
        wanted = _normalize(description)
        lines = snapshot_text.splitlines()
        candidates = []
        for line in lines:
            norm = _normalize(line)
            if wanted and wanted in norm:
                candidates.append(line)
        if not candidates and description:
            tokens = [t for t in wanted.split() if len(t) > 2]
            for line in lines:
                norm = _normalize(line)
                if tokens and all(t in norm for t in tokens[:3]):
                    candidates.append(line)
        patterns = [
            r"uid\s*[:=]\s*['\"]?([A-Za-z0-9_.:-]+)",
            r"\buid\s+([A-Za-z0-9_.:-]+)",
            r"\[([A-Za-z0-9_.:-]+)\]",
        ]
        for line in candidates:
            for pat in patterns:
                m = re.search(pat, line, flags=re.IGNORECASE)
                if m:
                    return m.group(1)
        # Chrome snapshots often use a line prefix like "button \"Search\" [uid=12]"; try all lines as a final pass.
        for line in lines:
            if description.lower() in line.lower():
                for pat in patterns:
                    m = re.search(pat, line, flags=re.IGNORECASE)
                    if m:
                        return m.group(1)
        return None

    def _save_screenshot_result(self, result: Any, path: Path) -> str:
        text = _result_to_text(result)
        # If the MCP server returns an image content block with base64 data, write PNG. Otherwise write JSON sidecar.
        if isinstance(result, dict):
            for item in result.get("content", []) or []:
                if isinstance(item, dict) and item.get("type") == "image" and item.get("data"):
                    data = base64.b64decode(item["data"])
                    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                        path = path.with_suffix(".png")
                    path.write_bytes(data)
                    return str(path)
        fallback = path.with_suffix(path.suffix + ".json" if path.suffix else ".json")
        fallback.write_text(json.dumps(mask_sensitive_data(result), indent=2, ensure_ascii=False, default=str) if isinstance(result, (dict, list)) else text, encoding="utf-8")
        return str(fallback)

    def _begin_action(self, action_type: str, target: str, value: Optional[str] = None) -> ActionEvent:
        self._action_seq += 1
        was_secret = is_secret_target(target) or is_secret_target(value)
        ev = ActionEvent(
            action_id=f"chrome-devtools-mcp-{self._action_seq:04d}",
            type=action_type,  # type: ignore[arg-type]
            target=mask_sensitive_string(target),
            value_redacted="***MASKED***" if was_secret and value is not None else mask_sensitive_string(value) if value is not None else None,
            value_hash=None if value is None else __import__("hashlib").sha256(value.encode("utf-8", errors="ignore")).hexdigest(),
            page_url_before=self.current_url,
            timestamp_start=utc_now(),
            backend=self.name,
            was_secret=was_secret,
        )
        self.action_events.append(ev)
        return ev

    async def _finish_action(self, ev: ActionEvent, *, success: bool, error: Optional[str] = None, screenshot_after: Optional[str] = None) -> None:
        ev.timestamp_end = utc_now()
        ev.success = success
        ev.error = mask_sensitive_string(error) if error else None
        ev.page_url_after = await self.get_current_url()
        if screenshot_after:
            ev.screenshot_after = screenshot_after
