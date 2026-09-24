import asyncio
import json
from pathlib import Path

from hip_id_agent.chrome_devtools_mcp import ChromeDevToolsMCPBackend
from hip_id_agent.browser_backend import MCPBackend
from hip_id_agent.config import load_config


class FakeMCPClient:
    def __init__(self):
        self.tools = {
            "new_page": object(),
            "navigate_page": object(),
            "take_snapshot": object(),
            "click": object(),
            "fill": object(),
            "press_key": object(),
            "take_screenshot": object(),
            "list_network_requests": object(),
            "list_console_messages": object(),
            "list_pages": object(),
        }
        self.calls = []
        self.started = False

    async def start(self):
        self.started = True

    async def close(self):
        pass

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments or {}))
        if name == "take_snapshot":
            return {"content": [{"type": "text", "text": 'input "Search" uid=search-1\ninput "password" uid=password-1\nbutton "Submit" uid=submit-1'}]}
        if name == "list_pages":
            return {"content": [{"type": "text", "text": "0 https://developer.dell.com/hybrid-integrations/bizlink/partner"}]}
        if name == "list_network_requests":
            return {"content": [{"type": "text", "text": "GET /api/partners/search 200"}]}
        return {"content": [{"type": "text", "text": "ok"}]}


def test_chrome_devtools_mcp_backend_maps_snapshot_uid_and_calls_tools(tmp_path: Path):
    backend = ChromeDevToolsMCPBackend(FakeMCPClient(), tmp_path)
    asyncio.run(backend.start())
    asyncio.run(backend.navigate("https://developer.dell.com/hybrid-integrations/bizlink/partner"))
    asyncio.run(backend.fill("Search", "UHAL"))
    asyncio.run(backend.press("Search", "Enter"))
    events = asyncio.run(backend.get_network_events())
    call_names = [c[0] for c in backend.client.calls]
    assert "new_page" in call_names
    assert ("fill", {"uid": "search-1", "value": "UHAL", "includeSnapshot": False}) in backend.client.calls
    assert "press_key" in call_names
    assert events and events[0]["source"] == "chrome_devtools_mcp"
    assert all(a.backend == "chrome-devtools-mcp" for a in backend.action_events)


def test_mcp_backend_delegates_to_chrome_devtools_mcp_client(tmp_path: Path):
    fake = FakeMCPClient()
    chrome = ChromeDevToolsMCPBackend(fake, tmp_path)
    backend = MCPBackend(tool_client=chrome)
    asyncio.run(backend.navigate("https://example.com"))
    assert fake.started is True
    assert fake.calls[0][0] == "new_page"


def test_config_exposes_chrome_devtools_mcp_adapter_fields(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
mcp:
  browser_backend: "mcp"
  use_chrome_devtools_mcp: true
  chrome_devtools_command: "npx"
  chrome_devtools_args: ["-y", "chrome-devtools-mcp@latest", "--no-usage-statistics"]
  chrome_devtools_startup_timeout_seconds: 30
  chrome_devtools_mcp_direct_backend_enabled: true
portal:
  headless: true
""",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.mcp.browser_backend == "mcp"
    assert cfg.mcp.use_chrome_devtools_mcp is True
    assert cfg.mcp.chrome_devtools_command == "npx"
    assert "chrome-devtools-mcp@latest" in cfg.mcp.chrome_devtools_args
    assert cfg.mcp.chrome_devtools_mcp_direct_backend_enabled is True


def test_chrome_devtools_mcp_actions_mask_secret_values(tmp_path: Path):
    backend = ChromeDevToolsMCPBackend(FakeMCPClient(), tmp_path)
    asyncio.run(backend.start())
    asyncio.run(backend.fill("password", "real-secret"))
    payload = json.dumps([a.__dict__ for a in backend.action_events])
    assert "real-secret" not in payload
    assert "***MASKED***" in payload
