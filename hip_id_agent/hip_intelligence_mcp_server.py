from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

from .action_model import HIPActionModel, course_architecture_manifest
from .trajectory_memory import TrajectoryMemory
from .web_representation import build_web_representation, representation_drift
from .semantic_control import (
    SemanticControlMemory, control_fingerprint, semantic_control_id,
    rank_semantic_candidates, verify_semantic_effect, semantic_control_mcp_status,
)
from .hip_semantic_tools import (
    HIP_SEMANTIC_TOOL_NAMES, hip_get_current_surface, hip_get_form_schema,
    hip_find_control, hip_find_owned_popup, hip_get_repeatable_rows,
    hip_get_required_fields, hip_get_current_values, hip_compare_expected_actual,
    hip_get_safe_actions, hip_verify_action_effect, hip_get_route_identity,
    hip_get_form_generation,
)


TOOLS = [
    {
        "name": "build_web_representation",
        "description": "Build a value-free semantic embedding of a HIP portal surface from DOM/accessibility controls.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "plan_form_action",
        "description": "Rank one safe field action and bounded recovery candidates using the action model and trajectory priors.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "critique_action_result",
        "description": "Critique a completed field transaction using exact stability, event and mutation evidence.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "retrieve_similar_trajectories",
        "description": "Retrieve value-free successful and failed trajectories for a structurally similar HIP field action.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "record_trajectory_outcome",
        "description": "Record a value-free successful or failed HIP action transition for future planning.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "detect_web_representation_drift",
        "description": "Compare two value-free HIP web representations and detect portal structure drift.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "resolve_semantic_control",
        "description": "Resolve one reviewed HIP action intent to a ranked value-free semantic control candidate with stable fingerprint identity.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "rank_semantic_candidates",
        "description": "Rank value-free HIP control candidates for an expected label/section/action without executing browser actions.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "verify_semantic_action_effect",
        "description": "Verify a value-free before/after semantic action effect using route, DOM generation and control-state evidence.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "get_semantic_control_fingerprint",
        "description": "Return the stable value-free semantic control id/fingerprint for one HIP control candidate.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "get_semantic_control_capabilities",
        "description": "Return Layer 11 semantic website-understanding capabilities exposed by HIP Intelligence MCP.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {"name": "hip_get_current_surface", "description": "Summarize the current HIP surface, route and DOM generation without opening a browser.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_get_form_schema", "description": "Return the value-bounded semantic schema for the active HIP form.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_find_control", "description": "Find and rank one HIP control using label, section, role, accessibility, DOM and historical semantic evidence.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_find_owned_popup", "description": "Resolve a popup/listbox owned by a specific HIP control using aria-controls/aria-owns provenance.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_get_repeatable_rows", "description": "Return generation-scoped repeatable rows and their semantic controls.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_get_required_fields", "description": "Return required fields from the active HIP form schema.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_get_current_values", "description": "Return ephemeral security-masked current field values/presence; nothing is persisted.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_compare_expected_actual", "description": "Compare expected and actual HIP field maps without persisting raw values.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_get_safe_actions", "description": "Classify visible HIP actions as SAFE, CONDITIONAL or DANGEROUS for the mutation authorization layer.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_verify_action_effect", "description": "Verify the expected semantic before/after effect of one HIP action.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_get_route_identity", "description": "Infer HIP module route identity from URL path and heading evidence.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {"name": "hip_get_form_generation", "description": "Return the current DOM/form generation and state fingerprint.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}},
    {
        "name": "get_course_architecture_manifest",
        "description": "Return the implemented mapping of course web-agent concepts to the HIP runtime.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
]


class Server:
    def __init__(self, memory_dir: Path) -> None:
        self.memory = TrajectoryMemory(memory_dir)
        self.action_model = HIPActionModel()
        self.semantic_memory = SemanticControlMemory(memory_dir.parent / "semantic_website_understanding")

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        if name == "build_web_representation":
            return build_web_representation(
                phase=str(args.get("phase") or ""),
                url=str(args.get("url") or ""),
                controls=args.get("controls") or [],
                surface_gate=args.get("surface_gate") or {},
                dom_transition=args.get("dom_transition") or {},
                console_signatures=args.get("console_signatures") or [],
                network_signatures=args.get("network_signatures") or [],
            )
        if name == "plan_form_action":
            return self.action_model.plan(
                node=args.get("node") or {},
                representation=args.get("representation") or {},
                binding=args.get("binding") or {},
                surface_gate=args.get("surface_gate") or {},
                memory_matches=args.get("memory_matches") or [],
                interaction_state=args.get("interaction_state") or {},
            )
        if name == "critique_action_result":
            return self.action_model.critique(
                node=args.get("node") or {},
                action_plan=args.get("action_plan") or {},
                outcome=args.get("outcome") or {},
            )
        if name == "retrieve_similar_trajectories":
            return self.memory.retrieve(
                phase=str(args.get("phase") or ""),
                family=str(args.get("family") or ""),
                node=args.get("node") or {},
                representation=args.get("representation") or {},
                limit=int(args.get("limit") or 8),
            )
        if name == "record_trajectory_outcome":
            return self.memory.record(
                phase=str(args.get("phase") or ""),
                family=str(args.get("family") or ""),
                node=args.get("node") or {},
                representation_before=args.get("representation_before") or {},
                action_plan=args.get("action_plan") or {},
                outcome=args.get("outcome") or {},
                representation_after=args.get("representation_after") or {},
            )
        if name == "detect_web_representation_drift":
            return representation_drift(args.get("current") or {}, args.get("previous") or {})
        if name in {"resolve_semantic_control", "rank_semantic_candidates"}:
            rows = rank_semantic_candidates(
                candidates=args.get("candidates") or [],
                action=str(args.get("action") or "click"),
                expected_label=str(args.get("expected_label") or ""),
                expected_section=str(args.get("expected_section") or ""),
                accessibility_text=str(args.get("accessibility_text") or ""),
                devtools_text=str(args.get("devtools_text") or ""),
                memory=self.semantic_memory,
                phase=str(args.get("phase") or ""),
            )
            compact = []
            for row in rows[:8]:
                cand = dict(row.get("candidate") or {})
                compact.append({
                    "score": row.get("score"),
                    "anchored": bool(row.get("anchored")),
                    "reasons": row.get("reasons") or [],
                    "semantic_control_id": semantic_control_id(cand) if cand else "",
                    "control_fingerprint": control_fingerprint(cand) if cand else "",
                    "candidate": cand,
                })
            top = compact[0] if compact else {}
            second = float(compact[1].get("score") or 0.0) if len(compact) > 1 else 0.0
            return {
                "schema_version": "hip.semantic-control-resolution.v1",
                "pass": bool(top),
                "candidate": top.get("candidate") or {},
                "semantic_control_id": top.get("semantic_control_id") or "",
                "control_fingerprint": top.get("control_fingerprint") or "",
                "confidence": float(top.get("score") or 0.0),
                "margin": float(top.get("score") or 0.0) - second,
                "ranked": compact,
                "values_stored": False,
            }
        if name == "verify_semantic_action_effect":
            return verify_semantic_effect(
                before=args.get("before") or {},
                after=args.get("after") or {},
                action=str(args.get("action") or "click"),
                target_before=args.get("target_before") or {},
                target_after=args.get("target_after") or {},
                exact_value_verified=bool(args.get("exact_value_verified")),
            )
        if name == "get_semantic_control_fingerprint":
            cand = args.get("candidate") or {}
            return {
                "semantic_control_id": semantic_control_id(cand),
                "control_fingerprint": control_fingerprint(cand),
                "values_stored": False,
            }
        if name == "get_semantic_control_capabilities":
            return semantic_control_mcp_status()
        if name == "hip_get_current_surface":
            return hip_get_current_surface(args)
        if name == "hip_get_form_schema":
            return hip_get_form_schema(args)
        if name == "hip_find_control":
            return hip_find_control(args, memory=self.semantic_memory)
        if name == "hip_find_owned_popup":
            return hip_find_owned_popup(args)
        if name == "hip_get_repeatable_rows":
            return hip_get_repeatable_rows(args)
        if name == "hip_get_required_fields":
            return hip_get_required_fields(args)
        if name == "hip_get_current_values":
            return hip_get_current_values(args)
        if name == "hip_compare_expected_actual":
            return hip_compare_expected_actual(args)
        if name == "hip_get_safe_actions":
            return hip_get_safe_actions(args)
        if name == "hip_verify_action_effect":
            return hip_verify_action_effect(args)
        if name == "hip_get_route_identity":
            return hip_get_route_identity(args)
        if name == "hip_get_form_generation":
            return hip_get_form_generation(args)
        if name == "get_course_architecture_manifest":
            return course_architecture_manifest()
        raise ValueError(f"Unknown MCP tool: {name}")


def _result(value: Any) -> Dict[str, Any]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return {"content": [{"type": "text", "text": text}], "structuredContent": value, "isError": False}


async def serve(memory_dir: Path) -> None:
    server = Server(memory_dir)
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
        if not line:
            break
        try:
            request = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        if not isinstance(request, dict):
            continue
        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params") or {}
        if method == "notifications/initialized":
            continue
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "hip-intelligence-mcp", "version": "1.1.0"},
                }
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = _result(server.call(str(params.get("name") or ""), params.get("arguments") or {}))
            else:
                raise ValueError(f"Unsupported method: {method}")
            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory-dir", required=True)
    args = parser.parse_args()
    asyncio.run(serve(Path(args.memory_dir)))


if __name__ == "__main__":
    main()
