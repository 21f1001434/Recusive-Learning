from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .security import mask_sensitive_data, mask_sensitive_string




def resolve_mcp_command(command: str) -> str:
    """Resolve an MCP runner without forcing Bun or npm.

    ``auto`` prefers npm/npx because enterprise Windows environments commonly
    trust the corporate CA in Node/npm even when Bun's registry client does not.
    Bun remains the fallback and can still be selected explicitly in config.
    """
    requested = str(command or "").strip() or "auto"
    if requested.lower() != "auto":
        return requested

    candidates = ["npx.cmd", "npx", "bunx.exe", "bunx"] if os.name == "nt" else ["npx", "bunx"]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    # Preserve a useful command name for the eventual FileNotFoundError message.
    return "npx.cmd" if os.name == "nt" else "npx"

class MCPClientError(RuntimeError):
    """Raised when an MCP stdio server cannot be started or called safely."""


@dataclass
class MCPTool:
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)


class MCPStdioClient:
    """Small stdio JSON-RPC client for pre-existing MCP servers.

    This client intentionally does not implement a custom MCP server. It starts an already existing
    MCP server command (for example `npx chrome-devtools-mcp@1.6.0`) and calls its published
    tools over stdio. It is lightweight so the project can run without adding an MCP SDK dependency.
    """

    def __init__(
        self,
        *,
        command: str,
        args: Optional[List[str]] = None,
        cwd: str | Path | None = None,
        env: Optional[Dict[str, str]] = None,
        startup_timeout_seconds: int = 30,
        request_timeout_seconds: float = 30.0,
        shutdown_timeout_seconds: float = 3.0,
        message_framing: str = "json-lines",
        stream_limit_bytes: int = 32 * 1024 * 1024,
    ):
        self.command = command
        self.args = args or []
        self.cwd = Path(cwd) if cwd else None
        self.env = env
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = max(0.1, float(request_timeout_seconds))
        self.shutdown_timeout_seconds = max(0.1, float(shutdown_timeout_seconds))
        # asyncio's default subprocess StreamReader limit is ~64 KiB. MCP tools
        # such as Playwright/PyAutoGUI screenshots legitimately return base64
        # image content in a single JSON-RPC line, which otherwise raises
        # LimitOverrunError ("Separator is not found, and chunk exceed the limit").
        self.stream_limit_bytes = max(256 * 1024, int(stream_limit_bytes or 32 * 1024 * 1024))
        # MCP stdio servers created with the official @modelcontextprotocol SDK use
        # newline-delimited JSON-RPC messages, not LSP-style Content-Length frames.
        # Older internal test doubles can still use "content-length" by passing it explicitly.
        self.message_framing = (message_framing or "json-lines").strip().lower().replace("_", "-")
        self.process: Optional[asyncio.subprocess.Process] = None
        self._next_id = 1
        self._stderr_task: Optional[asyncio.Task] = None
        self.stderr_lines: List[str] = []
        self.tools: Dict[str, MCPTool] = {}

    @property
    def is_started(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        if self.is_started:
            return
        try:
            resolved_command = resolve_mcp_command(self.command)
            self.process = await asyncio.create_subprocess_exec(
                resolved_command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cwd) if self.cwd else None,
                env=self.env,
                limit=self.stream_limit_bytes,
            )
        except FileNotFoundError as exc:
            raise MCPClientError(
                f"MCP server command not found: {self.command}. Install npm/npx or Bun/bunx, or configure the relevant MCP command path explicitly."
            ) from exc
        except Exception as exc:
            raise MCPClientError(f"Could not start MCP server {self.command}: {exc}") from exc
        self._stderr_task = asyncio.create_task(self._read_stderr())
        try:
            await asyncio.wait_for(self.initialize(), timeout=self.startup_timeout_seconds)
            await asyncio.wait_for(self.list_tools(), timeout=self.startup_timeout_seconds)
        except asyncio.TimeoutError as exc:
            stderr_tail = self.stderr_lines[-10:]
            await self.close()
            raise MCPClientError(
                "MCP server started but did not answer initialize/tools/list before timeout. "
                f"framing={self.message_framing}; stderr_tail={stderr_tail}"
            ) from exc
        except Exception as exc:
            stderr_tail = self.stderr_lines[-10:]
            await self.close()
            if isinstance(exc, MCPClientError):
                raise
            raise MCPClientError(
                f"MCP server started but initialization/list_tools failed: {exc}; stderr_tail={stderr_tail}"
            ) from exc

    async def initialize(self) -> Dict[str, Any]:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hip-portal-id-agent", "version": "1.0.0"},
            },
        )
        await self.notify("notifications/initialized", {})
        return result

    async def list_tools(self) -> List[MCPTool]:
        result = await self.request("tools/list", {})
        raw_tools = result.get("tools", []) if isinstance(result, dict) else []
        parsed: List[MCPTool] = []
        for item in raw_tools:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            tool = MCPTool(
                name=str(item.get("name")),
                description=str(item.get("description") or ""),
                input_schema=item.get("inputSchema") or item.get("input_schema") or {},
            )
            parsed.append(tool)
            self.tools[tool.name] = tool
        return parsed

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        if name not in self.tools:
            # Re-read in case the server lazily exposes tools after startup.
            await self.list_tools()
        if name not in self.tools:
            raise MCPClientError(f"MCP tool not available: {name}. Available tools: {sorted(self.tools)}")
        return await self.request("tools/call", {"name": name, "arguments": arguments or {}})

    async def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        if not self.process or not self.process.stdin:
            raise MCPClientError("MCP server process is not started")
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        await self._write_message(message)

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise MCPClientError("MCP server process is not started")
        try:
            return await asyncio.wait_for(
                self._request_unbounded(method, params),
                timeout=self.request_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise MCPClientError(
                f"MCP request timed out after {self.request_timeout_seconds:g}s: {method}"
            ) from exc

    async def _request_unbounded(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        req_id = self._next_id
        self._next_id += 1
        await self._write_message({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        while True:
            msg = await self._read_message()
            if not isinstance(msg, dict):
                continue
            if msg.get("id") != req_id:
                # Ignore notifications or out-of-order messages for this simple client.
                continue
            if "error" in msg:
                err = msg.get("error") or {}
                raise MCPClientError(mask_sensitive_string(str(err)))
            return msg.get("result")

    async def _write_message(self, message: Dict[str, Any]) -> None:
        assert self.process and self.process.stdin
        payload = json.dumps(mask_sensitive_data(message), ensure_ascii=False)
        raw = payload.encode("utf-8")
        if self.message_framing in {"content-length", "lsp", "headers"}:
            header = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii")
            self.process.stdin.write(header + raw)
        else:
            # Official MCP stdio framing: one JSON-RPC message per line.
            self.process.stdin.write(raw + b"\n")
        await self.process.stdin.drain()

    async def _read_message(self) -> Dict[str, Any]:
        assert self.process and self.process.stdout
        if self.message_framing in {"content-length", "lsp", "headers"}:
            return await self._read_content_length_message()
        return await self._read_json_line_message()

    async def _read_json_line_message(self) -> Dict[str, Any]:
        assert self.process and self.process.stdout
        while True:
            line = await self.process.stdout.readline()
            if not line:
                code = self.process.returncode
                raise MCPClientError(f"MCP server stdout closed unexpectedly; returncode={code}; stderr={self.stderr_lines[-5:]}")
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Some tools can print startup noise on stdout. Keep scanning for real JSON-RPC.
                self.stderr_lines.append(mask_sensitive_string(f"non-json stdout: {text[:500]}"))
                if len(self.stderr_lines) > 200:
                    self.stderr_lines = self.stderr_lines[-200:]
                continue

    async def _read_content_length_message(self) -> Dict[str, Any]:
        assert self.process and self.process.stdout
        headers: Dict[str, str] = {}
        while True:
            line = await self.process.stdout.readline()
            if not line:
                code = self.process.returncode
                raise MCPClientError(f"MCP server stdout closed unexpectedly; returncode={code}; stderr={self.stderr_lines[-5:]}")
            stripped = line.decode("ascii", errors="ignore").strip()
            if not stripped:
                break
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                headers[k.lower()] = v.strip()
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return {}
        payload = await self.process.stdout.readexactly(length)
        return json.loads(payload.decode("utf-8"))

    async def _read_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    return
                text = mask_sensitive_string(line.decode("utf-8", errors="replace").rstrip())
                if text:
                    self.stderr_lines.append(text)
                    if len(self.stderr_lines) > 200:
                        self.stderr_lines = self.stderr_lines[-200:]
        except Exception:
            return

    async def close(self) -> None:
        if self.process:
            process = self.process
            try:
                if process.stdin:
                    process.stdin.close()
                    try:
                        await asyncio.wait_for(process.stdin.wait_closed(), timeout=self.shutdown_timeout_seconds)
                    except Exception:
                        pass
            except Exception:
                pass
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=self.shutdown_timeout_seconds)
                except Exception:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=min(1.0, self.shutdown_timeout_seconds))
                    except Exception:
                        pass
            self.process = None
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._stderr_task), timeout=min(0.5, self.shutdown_timeout_seconds))
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            self._stderr_task = None
