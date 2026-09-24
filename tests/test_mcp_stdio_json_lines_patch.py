import asyncio
import json
import sys
from pathlib import Path

from hip_id_agent.mcp_stdio import MCPStdioClient


def test_mcp_stdio_client_uses_json_lines_for_official_mcp_servers(tmp_path: Path):
    server = tmp_path / "fake_mcp_json_lines_server.py"
    server.write_text(
        r'''
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"fake"}}}), flush=True)
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        resp = {"jsonrpc":"2.0","id":msg["id"],"result":{"tools":[{"name":"take_snapshot","description":"snapshot","inputSchema":{}},{"name":"click","description":"click","inputSchema":{}}]}}
        print(json.dumps(resp), flush=True)
    elif method == "tools/call":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"content":[{"type":"text","text":"ok"}]}}), flush=True)
''',
        encoding="utf-8",
    )
    client = MCPStdioClient(command=sys.executable, args=[str(server)], startup_timeout_seconds=5)
    async def run():
        await client.start()
        assert "take_snapshot" in client.tools
        result = await client.call_tool("take_snapshot", {})
        await client.close()
        return result
    result = asyncio.run(run())
    assert result["content"][0]["text"] == "ok"
