from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .mcp_stdio import MCPClientError, MCPStdioClient
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


def _content_text(result: Any) -> str:
    """Flatten MCP content blocks into masked text."""
    if result is None:
        return ""
    if isinstance(result, str):
        return mask_sensitive_string(result)
    if isinstance(result, list):
        return "\n".join(_content_text(x) for x in result if x is not None)
    if isinstance(result, dict):
        if "content" in result:
            return _content_text(result.get("content"))
        if result.get("type") == "text":
            return mask_sensitive_string(str(result.get("text") or ""))
        if "text" in result and isinstance(result.get("text"), str):
            return mask_sensitive_string(str(result.get("text") or ""))
        return mask_sensitive_string(json.dumps(mask_sensitive_data(result), ensure_ascii=False, default=str))
    return mask_sensitive_string(str(result))


def _tool_props(client: MCPStdioClient, name: str) -> Dict[str, Any]:
    tool = client.tools.get(name)
    schema = getattr(tool, "input_schema", {}) if tool else {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    return props if isinstance(props, dict) else {}


def _tool_schema(client: MCPStdioClient, name: str) -> Dict[str, Any]:
    tool = client.tools.get(name)
    schema = getattr(tool, "input_schema", {}) if tool else {}
    return schema if isinstance(schema, dict) else {}


class PlaywrightMCPBackend:
    """Adapter for Microsoft's official ``@playwright/mcp`` server.

    The server is started over stdio and attaches to the same Chrome/Edge instance
    as the Python Playwright session through CDP. This gives the HIP agent two
    independent structured views of the same page:

    * Python Playwright locators for the existing deterministic executors.
    * Playwright MCP accessibility snapshots and browser tools for deterministic fallback/verified
      actions, effect validation, recovery and section judging.

    It does not create a custom MCP server.
    """

    name = "playwright-mcp"

    def __init__(self, client: MCPStdioClient, run_dir: Path, *, config_path: Optional[Path] = None):
        self.client = client
        self.run_dir = Path(run_dir)
        self.config_path = config_path
        self.started = False
        self._seq = 0
        self.events: List[Dict[str, Any]] = []
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(
        cls,
        config: Any,
        run_dir: Path,
        *,
        cdp_endpoint: Optional[str] = None,
        standalone_preflight: bool = False,
    ) -> "PlaywrightMCPBackend":
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        mcp_cfg = getattr(config, "mcp", config)
        command = getattr(mcp_cfg, "playwright_mcp_command", "auto")
        base_args = list(getattr(mcp_cfg, "playwright_mcp_args", ["@playwright/mcp@0.0.79"]))
        timeout = int(getattr(mcp_cfg, "playwright_mcp_startup_timeout_seconds", 45) or 45)

        generated: Dict[str, Any] = {
            "capabilities": [
                "core",
                "core-navigation",
                "core-tabs",
                "core-input",
                "network",
                "storage",
                "testing",
                "devtools",
            ],
            "saveSession": True,
            "sharedBrowserContext": True,
            "outputDir": str((run_dir / "artifacts").resolve()),
            "imageResponses": "allow",
            "snapshot": {"mode": "full"},
            "codegen": "none",
            "timeouts": {
                "action": int(getattr(mcp_cfg, "playwright_mcp_action_timeout_ms", 15000) or 15000),
                "navigation": int(getattr(mcp_cfg, "playwright_mcp_navigation_timeout_ms", 60000) or 60000),
                "expect": int(getattr(mcp_cfg, "playwright_mcp_expect_timeout_ms", 10000) or 10000),
            },
            "console": {"level": "debug"},
        }
        if cdp_endpoint and not standalone_preflight:
            generated["browser"] = {
                "browserName": "chromium",
                "cdpEndpoint": cdp_endpoint,
                "cdpTimeout": int(getattr(mcp_cfg, "playwright_mcp_cdp_timeout_ms", 30000) or 30000),
            }
        else:
            # Preflight lists tools without depending on the HIP browser. The first
            # navigation/action would launch the configured headed browser.
            generated["browser"] = {
                "browserName": "chromium",
                "isolated": True,
                "launchOptions": {"headless": bool(getattr(mcp_cfg, "playwright_mcp_preflight_headless", True))},
            }

        config_path = run_dir / "playwright_mcp.generated.json"
        safe_write_json(config_path, generated)
        args = base_args + ["--config", str(config_path)]
        client = MCPStdioClient(
            command=command,
            args=args,
            startup_timeout_seconds=timeout,
        )
        return cls(client, run_dir, config_path=config_path)

    async def start(self) -> None:
        await self.client.start()
        self.started = True
        safe_write_json(
            self.run_dir / "playwright_mcp_capabilities.json",
            {
                "available": True,
                "backend": self.name,
                "tool_count": len(self.client.tools),
                "tools": sorted(self.client.tools),
                "config_file": str(self.config_path) if self.config_path else None,
            },
        )

    async def close(self) -> None:
        await self.client.close()
        self.started = False
        safe_write_json(self.run_dir / "playwright_mcp_action_log.json", self.events)

    def _target_args(self, tool_name: str, target: str, element: str = "HIP Portal control") -> Dict[str, Any]:
        props = _tool_props(self.client, tool_name)
        args: Dict[str, Any] = {}
        # Current server uses target; older releases used ref.
        if "target" in props or "ref" not in props:
            args["target"] = target
        else:
            args["ref"] = target
        if "element" in props:
            args["element"] = element
        return args

    async def _call(self, name: str, arguments: Optional[Dict[str, Any]] = None, *, action: str = "") -> Any:
        self._seq += 1
        event: Dict[str, Any] = {
            "sequence": self._seq,
            "tool": name,
            "action": action or name,
            "arguments": mask_sensitive_data(arguments or {}),
        }
        try:
            result = await self.client.call_tool(name, arguments or {})
            text = _content_text(result)
            is_error = bool(isinstance(result, dict) and (result.get("isError") or result.get("is_error")))
            if is_error or text.lstrip().lower().startswith("### error"):
                raise MCPClientError(text[:3000] or f"Playwright MCP tool {name} returned an error")
            event["success"] = True
            event["result_excerpt"] = text[:5000]
            self.events.append(event)
            return result
        except Exception as exc:
            event["success"] = False
            event["error"] = mask_sensitive_string(str(exc))
            self.events.append(event)
            raise

    async def navigate(self, url: str) -> None:
        await self._call("browser_navigate", {"url": url}, action="navigate")

    async def snapshot(
        self,
        *,
        target: Optional[str] = None,
        filename: Optional[str] = None,
        boxes: bool = True,
        depth: Optional[int] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {}
        props = _tool_props(self.client, "browser_snapshot")
        if target and "target" in props:
            args["target"] = target
        if filename and "filename" in props:
            args["filename"] = filename
        if "boxes" in props:
            args["boxes"] = boxes
        if depth is not None and "depth" in props:
            args["depth"] = depth
        result = await self._call("browser_snapshot", args, action="accessibility_snapshot")
        return {"text": _content_text(result), "raw": mask_sensitive_data(result)}

    async def find(self, *, text: Optional[str] = None, regex: Optional[str] = None) -> Dict[str, Any]:
        if "browser_find" not in self.client.tools:
            snap = await self.snapshot()
            needle = text or regex or ""
            lines = [line for line in snap.get("text", "").splitlines() if needle.lower() in line.lower()]
            return {"text": "\n".join(lines), "fallback": True}
        args = {"text": text} if text else {"regex": regex}
        result = await self._call("browser_find", args, action="find_in_accessibility_snapshot")
        return {"text": _content_text(result), "raw": mask_sensitive_data(result)}

    async def find_ref(self, text: str, *, expected_role: str = "", section: str = "") -> Dict[str, Any]:
        """Return a unique accessibility ref from browser_find evidence.

        Refs are intentionally ephemeral; they are used only for the current MCP
        snapshot generation and are never promoted into long-term fingerprints.
        """
        result = await self.find(text=text)
        raw = str(result.get("text") or "")
        label_norm = re.sub(r"\s+", " ", str(text or "").strip().lower())
        role_norm = str(expected_role or "").strip().lower()
        section_norm = re.sub(r"\s+", " ", str(section or "").strip().lower())
        candidates: List[Dict[str, str]] = []
        raw_norm = re.sub(r"\s+", " ", raw.strip().lower())
        section_match = bool(not section_norm or section_norm in raw_norm)
        if section_norm and not section_match:
            return {
                "status": "section_mismatch", "ref": "", "candidates": [],
                "browser_find_used": not bool(result.get("fallback")),
                "section_hint": section_norm, "section_match": False,
            }
        for line in raw.splitlines():
            refs = re.findall(r"\[ref=([^\]]+)\]", line)
            if not refs:
                continue
            line_norm = re.sub(r"\s+", " ", line.strip().lower())
            label_match = bool(label_norm and label_norm in line_norm)
            role_match = bool(not role_norm or role_norm in line_norm)
            if label_match and role_match:
                for ref in refs:
                    candidates.append({"ref": ref, "line": line.strip()[:1000]})
        # browser_find may return the matched node on a neighboring line.  If the
        # exact-label pass found nothing and there is only one ref overall, accept it.
        if not candidates:
            all_refs = re.findall(r"\[ref=([^\]]+)\]", raw)
            if len(set(all_refs)) == 1:
                candidates = [{"ref": all_refs[0], "line": "unique ref in browser_find result"}]
        unique = []
        seen = set()
        for row in candidates:
            if row["ref"] not in seen:
                seen.add(row["ref"]); unique.append(row)
        return {
            "status": "unique_match" if len(unique) == 1 else ("ambiguous" if len(unique) > 1 else "not_found"),
            "ref": unique[0]["ref"] if len(unique) == 1 else "",
            "candidates": unique[:12],
            "browser_find_used": not bool(result.get("fallback")),
            "section_hint": section_norm,
            "section_match": section_match,
        }

    async def click(self, target: str, *, element: str = "HIP Portal control") -> None:
        await self._call("browser_click", self._target_args("browser_click", target, element), action="click")

    async def fill(self, target: str, value: str, *, element: str = "HIP Portal field", slowly: bool = False) -> None:
        args = self._target_args("browser_type", target, element)
        args.update({"text": value})
        props = _tool_props(self.client, "browser_type")
        if "submit" in props:
            args["submit"] = False
        if "slowly" in props:
            args["slowly"] = slowly
        await self._call("browser_type", args, action="type_or_fill")

    def _adapt_fill_form_field(self, field: Dict[str, Any]) -> Dict[str, Any]:
        """Adapt one canonical HIP field to the live MCP server schema.

        Microsoft Playwright MCP has evolved from ``ref`` to ``target`` in some
        tools.  The server-provided JSON schema is authoritative, so this adapter
        keeps v2.1.7 compatible with both shapes while retaining exact refs/selectors.
        """
        schema = _tool_schema(self.client, "browser_fill_form")
        fields_schema = ((_tool_props(self.client, "browser_fill_form").get("fields") or {}) if isinstance(_tool_props(self.client, "browser_fill_form").get("fields"), dict) else {})
        item_schema = fields_schema.get("items") if isinstance(fields_schema, dict) else {}
        item_props = item_schema.get("properties", {}) if isinstance(item_schema, dict) else {}
        item_props = item_props if isinstance(item_props, dict) else {}
        target = str(field.get("target") or field.get("ref") or field.get("selector") or "")
        element = str(field.get("element") or field.get("name") or field.get("label") or "HIP Portal field")
        value = field.get("value")
        role = str(field.get("type") or field.get("role") or "textbox")
        out: Dict[str, Any] = {}
        # Current official releases accept a field reference plus human-readable name/type/value.
        if "target" in item_props:
            out["target"] = target
        elif "ref" in item_props or not item_props:
            out["ref"] = target
        if "element" in item_props:
            out["element"] = element
        if "name" in item_props or not item_props:
            out["name"] = element
        if "type" in item_props or not item_props:
            out["type"] = role
        if "value" in item_props or not item_props:
            out["value"] = value
        if "checked" in item_props and "checked" in field:
            out["checked"] = bool(field.get("checked"))
            out.pop("value", None)
        if "options" in item_props and field.get("options") is not None:
            out["options"] = list(field.get("options") or [])
            out.pop("value", None)
        # If the live schema contains only a generic object shape, preserve the canonical
        # keys rather than guessing unsupported extras.
        if item_props:
            out = {k: v for k, v in out.items() if k in item_props}
        if not target:
            raise MCPClientError("browser_fill_form field is missing an exact MCP ref/target")
        return out

    async def fill_form(self, fields: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        if "browser_fill_form" not in self.client.tools:
            raise MCPClientError("Playwright MCP browser_fill_form tool unavailable")
        rows = [self._adapt_fill_form_field(dict(field)) for field in fields if isinstance(field, dict)]
        if not rows:
            raise MCPClientError("browser_fill_form requires at least one verified field")
        result = await self._call("browser_fill_form", {"fields": rows}, action="fill_verified_form")
        return {"pass": True, "field_count": len(rows), "text": _content_text(result), "raw": mask_sensitive_data(result)}

    async def select_option(self, target: str, values: str | Sequence[str], *, element: str = "HIP Portal dropdown") -> None:
        vals = [values] if isinstance(values, str) else list(values)
        args = self._target_args("browser_select_option", target, element)
        args["values"] = vals
        await self._call("browser_select_option", args, action="select_option")

    async def press(self, target: str, key: str, *, element: str = "HIP Portal field") -> None:
        if "browser_press_key" in self.client.tools:
            props = _tool_props(self.client, "browser_press_key")
            args: Dict[str, Any] = {"key": key}
            if "target" in props or "ref" in props:
                args.update(self._target_args("browser_press_key", target, element))
            await self._call("browser_press_key", args, action="press_key")
            return
        # Safe fallback through browser_evaluate, scoped to the exact target.
        if "browser_evaluate" not in self.client.tools:
            raise MCPClientError("Playwright MCP has neither browser_press_key nor browser_evaluate")
        fn = "(el) => { el.focus(); return true; }"
        args = self._target_args("browser_evaluate", target, element)
        args["function"] = fn
        await self._call("browser_evaluate", args, action=f"focus_before_key:{key}")

    async def verify_value(self, target: str, expected: str, *, element: str = "HIP Portal field", type_name: str = "textbox") -> Dict[str, Any]:
        if "browser_verify_value" in self.client.tools:
            args = self._target_args("browser_verify_value", target, element)
            args.update({"type": type_name, "value": expected})
            result = await self._call("browser_verify_value", args, action="verify_value")
            return {"pass": True, "text": _content_text(result)}
        if "browser_evaluate" not in self.client.tools:
            return {"pass": False, "reason": "no verify/evaluate tool"}
        args = self._target_args("browser_evaluate", target, element)
        args["function"] = "(el) => String(el.value ?? el.getAttribute('aria-valuetext') ?? el.textContent ?? '').trim()"
        result = await self._call("browser_evaluate", args, action="read_value")
        actual = _content_text(result).strip()
        norm = lambda x: re.sub(r"\s+", " ", str(x or "").strip().lower())
        return {"pass": norm(expected) == norm(actual), "actual": actual, "expected": expected}

    async def screenshot(self, name: str | Path, *, full_page: bool = True) -> str:
        name = Path(name)
        filename = name.name
        args: Dict[str, Any] = {"filename": filename}
        props = _tool_props(self.client, "browser_take_screenshot")
        if "type" in props:
            args["type"] = "png"
        if "fullPage" in props:
            args["fullPage"] = full_page
        if "scale" in props:
            args["scale"] = "css"
        result = await self._call("browser_take_screenshot", args, action="take_screenshot")
        # MCP writes inside outputDir. Return the expected generated path when present;
        # retain response text for versions that return a different path.
        expected = self.run_dir / "artifacts" / filename
        if expected.exists():
            return str(expected)
        text = _content_text(result)
        match = re.search(r"(?:saved|written|screenshot).*?([A-Za-z]:\\[^\n]+\.png|/[^\n]+\.png)", text, flags=re.I)
        return match.group(1).strip() if match else str(expected)

    async def get_dom_snapshot(self) -> Dict[str, Any]:
        snap = await self.snapshot(boxes=True)
        return {"url": await self.get_current_url(), "accessibility_snapshot": snap.get("text", ""), "raw": snap.get("raw")}

    async def get_network_events(self) -> List[Dict[str, Any]]:
        name = "browser_network_requests"
        if name not in self.client.tools:
            return []
        result = await self._call(name, {}, action="network_requests")
        return [{"source": self.name, "text": _content_text(result)}]

    async def get_console_messages(self) -> List[Dict[str, Any]]:
        if "browser_console_messages" not in self.client.tools:
            return []
        props = _tool_props(self.client, "browser_console_messages")
        args: Dict[str, Any] = {}
        if "level" in props:
            args["level"] = "debug"
        if "all" in props:
            args["all"] = True
        result = await self._call("browser_console_messages", args, action="console_messages")
        return [{"source": self.name, "text": _content_text(result)}]

    async def get_current_url(self) -> str:
        if "browser_evaluate" not in self.client.tools:
            snap = await self.snapshot(depth=2)
            match = re.search(r"https?://[^\s\]\)]+", snap.get("text", ""))
            return match.group(0) if match else ""
        result = await self._call("browser_evaluate", {"function": "() => location.href"}, action="current_url")
        text = _content_text(result).strip().strip('"')
        urls = re.findall(r"https?://[^\s\]\)\"']+", text)
        return urls[-1].rstrip(".,") if urls else text

    async def evaluate(self, function: str, *, action: str = "learning_evaluate") -> Dict[str, Any]:
        """Evaluate a read-only JavaScript function through Playwright MCP.

        Callers are responsible for supplying a non-mutating function. The HIP
        learning runtime uses this only for independent semantic/storage-key
        observations; values such as cookies and tokens are never requested.
        """
        if "browser_evaluate" not in self.client.tools:
            return {"available": False, "reason": "browser_evaluate tool unavailable"}
        result = await self._call("browser_evaluate", {"function": function}, action=action)
        return {"available": True, "text": _content_text(result), "raw": mask_sensitive_data(result)}

    async def storage_key_names(self) -> Dict[str, Any]:
        """Return storage key names only, never values."""
        result = await self.evaluate(
            "() => ({localStorage:Object.keys(localStorage).sort(), sessionStorage:Object.keys(sessionStorage).sort(), valuesCaptured:false})",
            action="storage_key_names",
        )
        if not result.get("available"):
            return result
        text = str(result.get("text") or "")
        try:
            payload = json.loads(text)
        except Exception:
            payload = {"raw_text": text[:5000], "valuesCaptured": False}
        return mask_sensitive_data(payload)

    def learning_capabilities(self) -> Dict[str, Any]:
        tools = sorted(self.client.tools)
        return {
            "backend": self.name,
            "tool_count": len(tools),
            "accessibility_snapshot": "browser_snapshot" in tools,
            "browser_find": "browser_find" in tools,
            "browser_fill_form": "browser_fill_form" in tools,
            "network": "browser_network_requests" in tools,
            "console": "browser_console_messages" in tools,
            "evaluate": "browser_evaluate" in tools,
            "storage_tools": [name for name in tools if "storage" in name],
            "tab_tools": [name for name in tools if "tab" in name],
            "tools": tools,
        }


async def validate_playwright_mcp(config: Any, run_dir: str | Path) -> Dict[str, Any]:
    """Start the official Playwright MCP server and return capability evidence."""
    backend = PlaywrightMCPBackend.from_config(config, Path(run_dir), standalone_preflight=True)
    try:
        await backend.start()
        tools = sorted(backend.client.tools)
        required = [
            "browser_navigate",
            "browser_snapshot",
            "browser_find",
            "browser_click",
            "browser_type",
            "browser_fill_form",
            "browser_select_option",
            "browser_take_screenshot",
            "browser_network_requests",
            "browser_console_messages",
        ]
        status = {name: name in backend.client.tools for name in required}
        return {
            "available": all(status.get(name, False) for name in ["browser_navigate", "browser_snapshot", "browser_find", "browser_click", "browser_type", "browser_fill_form", "browser_take_screenshot"]),
            "backend": backend.name,
            "tool_count": len(tools),
            "tools": tools,
            "required_tool_status": status,
            "official_package": "@playwright/mcp@0.0.79",
        }
    except Exception as exc:
        return {
            "available": False,
            "backend": "playwright-mcp",
            "error": mask_sensitive_string(str(exc)),
            "recovery": "Install Bun and run `bun install`; shipped defaults use `npx @playwright/mcp@0.0.79` (or Bun equivalent).",
        }
    finally:
        try:
            await backend.close()
        except Exception:
            pass
