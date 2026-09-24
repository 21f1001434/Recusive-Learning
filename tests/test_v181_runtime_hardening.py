import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.browser_backend import MCPBackend
from hip_id_agent.config import MCPConfig
from hip_id_agent.mcp_stdio import MCPClientError, MCPStdioClient


def test_direct_chrome_mcp_library_default_is_fail_closed(tmp_path: Path):
    cfg = SimpleNamespace(mcp=MCPConfig())
    assert cfg.mcp.chrome_devtools_mcp_direct_backend_enabled is False
    backend = MCPBackend(config=cfg, run_dir=tmp_path)
    assert backend.tool_client is None
    with pytest.raises(RuntimeError, match="no Chrome DevTools MCP adapter/client is bound"):
        asyncio.run(backend._require())


def test_mcp_request_is_bounded_by_request_timeout():
    client = MCPStdioClient(command="unused", request_timeout_seconds=0.02)
    client.process = SimpleNamespace(stdin=object(), stdout=object())

    async def slow_request(method, params=None):
        await asyncio.sleep(1)

    client._request_unbounded = slow_request
    with pytest.raises(MCPClientError, match="MCP request timed out"):
        asyncio.run(client.request("tools/call", {}))


def test_mcp_shutdown_timeout_is_configurable_and_bounded():
    client = MCPStdioClient(command="unused", shutdown_timeout_seconds=0.05)
    assert client.shutdown_timeout_seconds == pytest.approx(0.1)
    assert client.request_timeout_seconds == pytest.approx(30.0)


def test_platform_launcher_waits_for_verified_backend_health():
    source = (Path(__file__).resolve().parents[1] / "webui" / "platform.js").read_text(encoding="utf-8")
    assert 'const HEALTH_URL = `${API_BASE}/health`' in source
    assert "await backendHealthy()" in source
    assert "STARTUP_TIMEOUT_MS" in source
    assert 'await import("./server.js")' in source
    assert "Bun.sleep(700)" not in source
