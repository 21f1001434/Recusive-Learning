from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

from .mcp_stdio import MCPStdioClient


def _structured(result: Any) -> Any:
    if isinstance(result, dict):
        if isinstance(result.get("structuredContent"), (dict, list)):
            return result.get("structuredContent")
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        return json.loads(str(item.get("text") or ""))
                    except Exception:
                        continue
    return result


class HIPIntelligenceMCPBackend:
    def __init__(self, *, command: str, args: list[str], run_dir: Path, startup_timeout_seconds: int = 20, cwd: Path | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.client = MCPStdioClient(
            command=command,
            args=args,
            startup_timeout_seconds=startup_timeout_seconds,
            message_framing="json-lines",
            cwd=cwd or Path(__file__).resolve().parent.parent,
        )

    @classmethod
    def from_config(cls, config: Any, *, run_dir: Path, memory_dir: Path) -> "HIPIntelligenceMCPBackend":
        mcp = getattr(config, "mcp", None)
        command = str(getattr(mcp, "hip_intelligence_mcp_command", "") or sys.executable)
        configured_args = list(getattr(mcp, "hip_intelligence_mcp_args", []) or [])
        args = configured_args or [
            "-m", "hip_id_agent.hip_intelligence_mcp_server",
            "--memory-dir", str(memory_dir),
        ]
        return cls(
            command=command,
            args=args,
            run_dir=run_dir,
            startup_timeout_seconds=int(getattr(mcp, "hip_intelligence_mcp_startup_timeout_seconds", 20) or 20),
            cwd=Path(__file__).resolve().parent.parent,
        )

    async def start(self) -> None:
        await self.client.start()

    async def close(self) -> None:
        await self.client.close()

    async def _call(self, name: str, payload: Dict[str, Any]) -> Any:
        return _structured(await self.client.call_tool(name, payload))

    async def build_representation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("build_web_representation", payload)
        return result if isinstance(result, dict) else {}

    async def plan_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("plan_form_action", payload)
        return result if isinstance(result, dict) else {}

    async def critique(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("critique_action_result", payload)
        return result if isinstance(result, dict) else {}

    async def retrieve(self, payload: Dict[str, Any]) -> list[Dict[str, Any]]:
        result = await self._call("retrieve_similar_trajectories", payload)
        return result if isinstance(result, list) else []

    async def record_outcome(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("record_trajectory_outcome", payload)
        return result if isinstance(result, dict) else {}
    async def resolve_semantic_control(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("resolve_semantic_control", payload)
        return result if isinstance(result, dict) else {}

    async def rank_semantic_candidates(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("rank_semantic_candidates", payload)
        return result if isinstance(result, dict) else {}

    async def verify_semantic_action_effect(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("verify_semantic_action_effect", payload)
        return result if isinstance(result, dict) else {}

    async def semantic_capabilities(self) -> Dict[str, Any]:
        result = await self._call("get_semantic_control_capabilities", {})
        return result if isinstance(result, dict) else {}

    async def get_current_surface(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_get_current_surface", payload)
        return result if isinstance(result, dict) else {}

    async def get_form_schema(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_get_form_schema", payload)
        return result if isinstance(result, dict) else {}

    async def find_control(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_find_control", payload)
        return result if isinstance(result, dict) else {}

    async def find_owned_popup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_find_owned_popup", payload)
        return result if isinstance(result, dict) else {}

    async def get_repeatable_rows(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_get_repeatable_rows", payload)
        return result if isinstance(result, dict) else {}

    async def get_required_fields(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_get_required_fields", payload)
        return result if isinstance(result, dict) else {}

    async def get_current_values(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_get_current_values", payload)
        return result if isinstance(result, dict) else {}

    async def compare_expected_actual(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_compare_expected_actual", payload)
        return result if isinstance(result, dict) else {}

    async def get_safe_actions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_get_safe_actions", payload)
        return result if isinstance(result, dict) else {}

    async def verify_action_effect(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_verify_action_effect", payload)
        return result if isinstance(result, dict) else {}

    async def get_route_identity(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_get_route_identity", payload)
        return result if isinstance(result, dict) else {}

    async def get_form_generation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._call("hip_get_form_generation", payload)
        return result if isinstance(result, dict) else {}

