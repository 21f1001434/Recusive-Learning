from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import ActionEvent, ClickEvent, NetworkTabEvent, RunContext, StageResult, utc_now
from .security import mask_sensitive_data, mask_sensitive_string


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(p) for p in parts if p is not None)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _short(text: Any, limit: int = 180) -> str:
    value = mask_sensitive_string(str(text or "").replace("\n", " ").strip())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


class KnowledgeGraphBuilder:
    """Local run Knowledge Graph for HIP Partner/System extraction debugging.

    It represents every run/stage/page/action/click/fill/search/network/candidate/verified/rejected
    decision as typed nodes and edges. It is intentionally just JSON/HTML/Mermaid, not an MCP server.
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: Dict[str, Dict[str, Any]] = {}

    def add_node(self, node_id: str, node_type: str, label: str, **props: Any) -> str:
        existing = self.nodes.get(node_id, {})
        merged = {**existing.get("properties", {}), **mask_sensitive_data(props)}
        self.nodes[node_id] = {"id": node_id, "type": node_type, "label": _short(label, 220), "properties": merged}
        return node_id

    def add_edge(self, source: str, target: str, relation: str, **props: Any) -> str:
        edge_id = _stable_id("edge", source, target, relation, json.dumps(mask_sensitive_data(props), sort_keys=True, default=str))
        self.edges[edge_id] = {"id": edge_id, "source": source, "target": target, "relation": relation, "properties": mask_sensitive_data(props)}
        return edge_id

    def build(
        self,
        *,
        ctx: RunContext,
        click_events: Iterable[ClickEvent],
        action_events: Iterable[ActionEvent],
        network_events: Iterable[NetworkTabEvent],
        report_paths: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        clicks = list(click_events)
        actions = list(action_events)
        networks = list(network_events)
        candidates = _read_json(ctx.run_dir / "id_candidates.json", [])
        verified = _read_json(ctx.run_dir / "verified_ids.json", [])
        rejected = _read_json(ctx.run_dir / "rejected_ids.json", [])
        suggestions = _read_json(ctx.run_dir / "recovery_suggestions.json", [])

        run_node = self.add_node(_stable_id("run", ctx.run_id), "RUN", ctx.run_id, customer=ctx.customer, started_at=ctx.started_at, status=ctx.registry.get("run_status"))
        customer_node = self.add_node(_stable_id("customer", ctx.customer), "CUSTOMER", ctx.customer)
        self.add_edge(run_node, customer_node, "RUN_FOR_CUSTOMER")

        backend_node = self.add_node(
            _stable_id("backend", ctx.registry.get("browser_backend_used", "playwright"), ctx.registry.get("requested_browser_backend")),
            "BACKEND",
            ctx.registry.get("browser_backend_used", "playwright"),
            requested=ctx.registry.get("requested_browser_backend"),
            attempted=ctx.registry.get("attempted_browser_backend"),
            actual=ctx.registry.get("browser_backend_used", "playwright"),
            fallback_backend=ctx.registry.get("fallback_backend"),
            fallback_reason=ctx.registry.get("fallback_reason"),
            note=ctx.registry.get("browser_backend_note"),
        )
        self.add_edge(run_node, backend_node, "PAGE_USED_BACKEND")

        if ctx.registry.get("input_original_json"):
            inp_node = self.add_node(_stable_id("input", ctx.registry["input_original_json"]), "INPUT_FILE", Path(ctx.registry["input_original_json"]).name, path=ctx.registry["input_original_json"])
            self.add_edge(run_node, inp_node, "USED_INPUT_FILE")
            if ctx.registry.get("enriched_input_json"):
                enr_node = self.add_node(_stable_id("enriched", ctx.registry["enriched_input_json"]), "ENRICHED_INPUT", "enriched_input.json", path=ctx.registry["enriched_input_json"], unmasked=True)
                self.add_edge(inp_node, enr_node, "INPUT_ENRICHED_TO")
            if ctx.registry.get("enriched_input_redacted_json"):
                red_node = self.add_node(_stable_id("redacted", ctx.registry["enriched_input_redacted_json"]), "REDACTED_INPUT", "enriched_input.redacted.json", path=ctx.registry["enriched_input_redacted_json"], redacted=True)
                self.add_edge(inp_node, red_node, "INPUT_REDACTED_TO")
                self.add_edge(run_node, red_node, "SECRET_MASKED_IN")

        failure_nodes: List[str] = []
        if ctx.registry.get("logical_failures"):
            for failure_text in ctx.registry.get("logical_failures") or []:
                fail_node = self.add_node(_stable_id("failure", ctx.run_id, failure_text), "FAILURE", failure_text, source="logical_failure")
                self.add_edge(run_node, fail_node, "RUN_GENERATED_FAILURE")
                failure_nodes.append(fail_node)
        stage_nodes: Dict[str, str] = {}
        for order, stage in enumerate(ctx.stage_results, start=1):
            stage_node = self._add_stage(run_node, stage, order)
            stage_nodes[stage.stage] = stage_node
            url = (stage.evidence or {}).get("snapshot_url")
            if url:
                page_node = self.add_node(_stable_id("page", url), "PAGE", url, url=url, title=(stage.evidence or {}).get("page_title"))
                self.add_edge(stage_node, page_node, "STAGE_OPENED_PAGE")
            for shot in stage.screenshots:
                shot_node = self.add_node(_stable_id("screenshot", shot), "SCREENSHOT", Path(shot).name, path=shot)
                self.add_edge(stage_node, shot_node, "STAGE_CAPTURED_SCREENSHOT")
            debug = ((stage.evidence or {}).get("debug") or {})
            vr = debug.get("verified_result") if isinstance(debug, dict) else None
            if vr and vr.get("accepted"):
                for ev in vr.get("evidence", []) or []:
                    cand_node = self._candidate_node(ev, "ID_CANDIDATE")
                    self.add_edge(stage_node, cand_node, "DOM_CONTAINED_CANDIDATE" if ev.get("source_type", "").startswith("dom") else "NETWORK_RETURNED_CANDIDATE")
            for item in stage.extracted_ids:
                id_node = self.add_node(_stable_id("extracted", item.object_type, item.object_id), "VERIFIED_ID", item.object_id, object_type=item.object_type, name=item.name, source=item.source, confidence=item.confidence, evidence=item.evidence)
                self.add_edge(stage_node, id_node, "EXTRACTED_ID", confidence=item.confidence, evidence_source=item.source)
                self.add_edge(stage_node, id_node, "CANDIDATE_VERIFIED_AS", confidence=item.confidence, evidence_source=item.source)
            if stage.errors:
                for err in stage.errors:
                    fail_node = self.add_node(_stable_id("failure", stage.stage, err), "FAILURE", err, stage=stage.stage)
                    self.add_edge(run_node, fail_node, "RUN_GENERATED_FAILURE")
                    failure_nodes.append(fail_node)

        for order, action in enumerate(actions, start=1):
            node = self._add_action(action, order)
            parent_stage = stage_nodes.get(action.stage) or stage_nodes.get(self._infer_action_stage(action)) or run_node
            self.add_edge(parent_stage, node, "STAGE_PERFORMED_ACTION", order=order, action_type=action.type, success=action.success)
            if action.type == "click":
                self.add_edge(node, node, "ACTION_CLICKED_ELEMENT")
            elif action.type in {"fill", "search"}:
                self.add_edge(node, node, "ACTION_FILLED_FIELD", value=action.value_redacted)
            if action.page_url_before:
                page_node = self.add_node(_stable_id("page", action.page_url_before), "PAGE", action.page_url_before, url=action.page_url_before)
                self.add_edge(page_node, node, "PAGE_PERFORMED_ACTION")
            for rid in action.network_events_triggered:
                net_node = self.add_node(_stable_id("network", rid), "NETWORK_REQUEST", str(rid), request_id=rid, placeholder=True)
                self.add_edge(node, net_node, "ACTION_TRIGGERED_NETWORK")

        for order, click in enumerate(clicks, start=1):
            click_node = self._add_click(click, order)
            parent_stage = stage_nodes.get((click.extra or {}).get("stage")) or stage_nodes.get(self._infer_click_stage(click)) or run_node
            self.add_edge(parent_stage, click_node, "STAGE_PERFORMED_ACTION", order=order, action_type="click", click_source=click.source)
            self.add_edge(parent_stage, click_node, "PERFORMED_CLICK", order=order, click_source=click.source)
            if click.url:
                page_node = self.add_node(_stable_id("page", click.url), "PAGE", click.url, url=click.url)
                self.add_edge(page_node, click_node, "PAGE_PERFORMED_ACTION")
            text = (click.text or click.aria_label or "").strip()
            if text or click.selector:
                elem_node = self.add_node(_stable_id("element", click.selector, click.tag, text), "UI_ELEMENT", text or click.selector or "element", selector=click.selector, tag=click.tag, role=click.role, href=click.href)
                self.add_edge(click_node, elem_node, "ACTION_CLICKED_ELEMENT")

        for order, ev in enumerate(networks, start=1):
            net_node = self._add_network_event(ev, order)
            parent_stage = stage_nodes.get(ev.stage) or run_node
            self.add_edge(parent_stage, net_node, "STAGE_OBSERVED_NETWORK", order=order, status=ev.status, resource_type=ev.resource_type)
            self.add_edge(parent_stage, net_node, "OBSERVED_NETWORK_EVENT", order=order, status=ev.status, resource_type=ev.resource_type)
            if ev.url:
                endpoint_node = self.add_node(_stable_id("endpoint", ev.method, ev.url), "ENDPOINT", f"{ev.method} {ev.url}", method=ev.method, url=ev.url, status=ev.status)
                self.add_edge(net_node, endpoint_node, "NETWORK_HIT_ENDPOINT")

        for c in candidates:
            cand_node = self._candidate_node(c, "ID_CANDIDATE")
            source_url = c.get("endpoint_url") or c.get("source_url")
            if source_url:
                endpoint_node = self.add_node(_stable_id("endpoint", c.get("endpoint_url") or "", source_url), "ENDPOINT", source_url, url=source_url)
                self.add_edge(endpoint_node, cand_node, "NETWORK_RETURNED_CANDIDATE" if c.get("source_type") == "network" else "DOM_CONTAINED_CANDIDATE")
        for v in verified:
            vid = v.get("id_value") or v.get("candidate_id") or "verified"
            v_node = self.add_node(_stable_id("verified", v.get("object_type"), vid), "VERIFIED_ID", str(vid), object_type=v.get("object_type"), confidence=v.get("confidence"), reasons=v.get("reasons"))
            self.add_edge(run_node, v_node, "CANDIDATE_VERIFIED_AS", confidence=v.get("confidence"), reasons=v.get("reasons"))
            if ctx.registry.get("enriched_input_json"):
                enr_node = self.add_node(_stable_id("enriched", ctx.registry["enriched_input_json"]), "ENRICHED_INPUT", "enriched_input.json", path=ctx.registry["enriched_input_json"], unmasked=True)
                self.add_edge(v_node, enr_node, "VERIFIED_ID_ENRICHED_INPUT")
        for r in rejected:
            rid = r.get("candidate_id") or r.get("id_value") or json.dumps(r, default=str)[:40]
            r_node = self.add_node(_stable_id("rejected", r.get("object_type"), rid, r.get("field_path")), "REJECTED_ID", str(rid), object_type=r.get("object_type"), reasons=r.get("rejection_reasons"), confidence=r.get("confidence"))
            self.add_edge(run_node, r_node, "CANDIDATE_REJECTED_BECAUSE", reasons=r.get("rejection_reasons") or r.get("reasons"))
        for s in suggestions:
            rec_node = self.add_node(_stable_id("recovery", s), "RECOVERY_SUGGESTION", s)
            if failure_nodes:
                for fail_node in failure_nodes:
                    self.add_edge(fail_node, rec_node, "FAILURE_HAS_RECOVERY")
            else:
                self.add_edge(run_node, rec_node, "FAILURE_HAS_RECOVERY")

        for name, path in (report_paths or {}).items():
            report_node = self.add_node(_stable_id("report", path), "REPORT", Path(path).name, path=path, file_type=name)
            self.add_edge(run_node, report_node, "RUN_GENERATED_REPORT", report_type=name)

        graph = {
            "schema_version": "2.0",
            "created_at": utc_now(),
            "run_id": ctx.run_id,
            "customer": ctx.customer,
            "debug_questions_supported": [
                "which page was open when the Partner ID was found",
                "which endpoint returned the System ID",
                "which click/action triggered the endpoint",
                "what search value was typed",
                "which row was clicked",
                "why an ID was accepted or rejected",
                "which backend was used",
                "what recovery is suggested",
            ],
            "summary": {"nodes": len(self.nodes), "edges": len(self.edges), "stages": len(ctx.stage_results), "clicks": len(clicks), "actions": len(actions), "network_events": len(networks), "candidates": len(candidates), "verified_ids": len(verified), "rejected_ids": len(rejected)},
            "nodes": list(self.nodes.values()),
            "edges": list(self.edges.values()),
        }
        return mask_sensitive_data(graph)

    def _add_stage(self, run_node: str, stage: StageResult, order: int) -> str:
        node = self.add_node(_stable_id("stage", stage.stage, stage.started_at), "STAGE", stage.stage, status=stage.status, message=stage.message, started_at=stage.started_at, finished_at=stage.finished_at, warnings=stage.warnings, errors=stage.errors, order=order)
        self.add_edge(run_node, node, "RUN_STARTED_STAGE", order=order, status=stage.status)
        return node

    def _add_action(self, action: ActionEvent, order: int) -> str:
        label = f"{action.type}: {action.target}"
        node_type = "SEARCH" if action.type == "search" else "FILL" if action.type == "fill" else "ACTION"
        return self.add_node(_stable_id("action", action.action_id), node_type, label, **{**_jsonable(action), "order": order})

    def _add_click(self, click: ClickEvent, order: int) -> str:
        label = click.text or click.aria_label or click.selector or click.action or f"Click {order}"
        return self.add_node(_stable_id("click", click.timestamp, click.source, click.action, click.selector, click.text), "CLICK", label, **{**_jsonable(click), "order": order})

    def _network_props(self, ev: NetworkTabEvent, order: int) -> Dict[str, Any]:
        # The full response bodies can be multi-GB across a run. Keep KG nodes useful
        # for debugging while excluding raw bodies by default. Set HIP_WRITE_FULL_KG=1
        # if a full evidence graph is explicitly required.
        props = _jsonable(ev)
        if os.getenv("HIP_WRITE_FULL_KG", "").strip().lower() not in {"1", "true", "yes"}:
            for key in ["response_body_redacted", "response_body_text_redacted", "request_post_data_redacted"]:
                value = props.get(key)
                if isinstance(value, str):
                    props[key] = value[:1200] + (f"... <truncated {len(value)-1200} chars>" if len(value) > 1200 else "")
                elif isinstance(value, (dict, list)):
                    text = json.dumps(value, ensure_ascii=False, default=str)
                    props[key] = text[:1200] + (f"... <truncated {len(text)-1200} chars>" if len(text) > 1200 else "")
            props["kg_body_mode"] = "compact_truncated"
        props["order"] = order
        return props

    def _add_network_event(self, ev: NetworkTabEvent, order: int) -> str:
        label = f"{ev.method} {ev.status or ''} {ev.url}"
        # Add generic request and response nodes so the graph has both required types.
        req_node = self.add_node(_stable_id("network", ev.request_id), "NETWORK_REQUEST", label, **self._network_props(ev, order))
        if ev.status is not None:
            resp_node = self.add_node(_stable_id("network_response", ev.request_id, ev.status), "NETWORK_RESPONSE", f"{ev.status} {ev.url}", request_id=ev.request_id, status=ev.status, url=ev.url, has_response_body=ev.response_body_redacted is not None or ev.response_body_text_redacted is not None)
            self.add_edge(req_node, resp_node, "NETWORK_REQUEST_RECEIVED_RESPONSE")
        return req_node

    def _candidate_node(self, c: Dict[str, Any], typ: str) -> str:
        cid = c.get("candidate_id") or c.get("id_value") or "unknown"
        return self.add_node(_stable_id("candidate", typ, c.get("target_type") or c.get("object_type"), cid, c.get("field_path")), typ, str(cid), **mask_sensitive_data(c))

    def _infer_click_stage(self, click: ClickEvent) -> str:
        action = (click.action or "").lower()
        url = (click.url or "").lower()
        if "partner" in action or "/partner" in url:
            return "extract_partner_id"
        if "system" in action or "/system" in url:
            return "extract_system_id"
        return ""

    def _infer_action_stage(self, action: ActionEvent) -> str:
        stage = (action.stage or "").lower()
        if "partner" in stage:
            return "extract_partner_id"
        if "system" in stage:
            return "extract_system_id"
        return action.stage


class KnowledgeGraphWriter:
    def __init__(self, run_context: RunContext):
        self.ctx = run_context
        self.graph_dir = self.ctx.run_dir / "knowledge_graph"
        self.graph_dir.mkdir(parents=True, exist_ok=True)

    def write(self, *, click_events: Iterable[ClickEvent], action_events: Iterable[ActionEvent] = (), network_events: Iterable[NetworkTabEvent], report_paths: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        clicks = list(click_events)
        actions = list(action_events)
        all_networks = list(network_events)
        if os.getenv("HIP_WRITE_FULL_KG", "").strip().lower() in {"1", "true", "yes"}:
            networks = all_networks
            kg_mode = "full"
        else:
            # Keep failures, BizLink endpoints and a small head/tail sample.
            networks = []
            seen = set()
            def add(ev: NetworkTabEvent) -> None:
                key = ev.request_id or f"{ev.method}:{ev.url}:{ev.status}"
                if key not in seen and len(networks) < 700:
                    seen.add(key); networks.append(ev)
            for ev in all_networks[:80]:
                add(ev)
            for ev in all_networks:
                url = (ev.url or "").lower()
                failed = bool(ev.error or (ev.status is not None and int(ev.status) >= 400))
                relevant = any(x in url for x in ["partner", "system", "domain", "account", "deployment-group"])
                if failed or relevant:
                    add(ev)
            for ev in all_networks[-80:]:
                add(ev)
            kg_mode = f"compact_sample_{len(networks)}_of_{len(all_networks)}"
        self.ctx.registry["knowledge_graph_mode"] = kg_mode
        graph = KnowledgeGraphBuilder().build(ctx=self.ctx, click_events=clicks, action_events=actions, network_events=networks, report_paths=report_paths)
        graph.setdefault("summary", {})["network_events_total_available"] = len(all_networks)
        graph.setdefault("summary", {})["knowledge_graph_mode"] = kg_mode
        graph_path = self.graph_dir / "flow_knowledge_graph.json"
        graph_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")

        click_sequence = [mask_sensitive_data({"order": i, **_jsonable(c)}) for i, c in enumerate(clicks, start=1)]
        action_sequence = [mask_sensitive_data({"order": i, **_jsonable(a)}) for i, a in enumerate(actions, start=1)]
        click_path = self.graph_dir / "click_sequence.json"
        action_path = self.graph_dir / "action_sequence.json"
        click_path.write_text(json.dumps(click_sequence, indent=2, ensure_ascii=False), encoding="utf-8")
        action_path.write_text(json.dumps(action_sequence, indent=2, ensure_ascii=False), encoding="utf-8")

        extra_files = {}
        for name in ["id_candidates.json", "verified_ids.json", "rejected_ids.json", "recovery_suggestions.json"]:
            src = self.ctx.run_dir / name
            dst = self.graph_dir / name
            if src.exists():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                extra_files[name.replace(".json", "_json")] = str(dst)

        mermaid = self._to_mermaid(graph)
        mermaid_path = self.graph_dir / "flow_knowledge_graph.mmd"
        mermaid_path.write_text(mermaid, encoding="utf-8")
        md_path = self.graph_dir / "flow_knowledge_graph.md"
        md_path.write_text(self._markdown(graph, click_sequence, action_sequence), encoding="utf-8")
        html_path = self.graph_dir / "flow_knowledge_graph.html"
        html_path.write_text(self._html(graph, mermaid, click_sequence, action_sequence), encoding="utf-8")

        return {
            "knowledge_graph_json": str(graph_path),
            "knowledge_graph_mermaid": str(mermaid_path),
            "knowledge_graph_markdown": str(md_path),
            "knowledge_graph_html": str(html_path),
            "click_sequence_json": str(click_path),
            "action_sequence_json": str(action_path),
            **extra_files,
        }

    def _to_mermaid(self, graph: Dict[str, Any], limit_edges: int = 220) -> str:
        lines = ["flowchart TD"]
        nodes = {n["id"]: n for n in graph.get("nodes", [])}
        def safe_node_id(node_id: str) -> str:
            return "N" + hashlib.sha1(node_id.encode("utf-8", errors="ignore")).hexdigest()[:10]
        used = set()
        for edge in graph.get("edges", [])[:limit_edges]:
            src, tgt = edge.get("source"), edge.get("target")
            if not src or not tgt: continue
            for nid in [src, tgt]:
                if nid not in used:
                    node = nodes.get(nid, {})
                    label = _short(f"{node.get('type','NODE')}: {node.get('label', nid)}", 75).replace('"', "'")
                    lines.append(f"  {safe_node_id(nid)}[\"{label}\"]")
                    used.add(nid)
            rel = _short(edge.get("relation", "RELATED_TO"), 38).replace('"', "'")
            lines.append(f"  {safe_node_id(src)} -- {rel} --> {safe_node_id(tgt)}")
        return "\n".join(lines) + "\n"

    def _markdown(self, graph: Dict[str, Any], click_sequence: List[Dict[str, Any]], action_sequence: List[Dict[str, Any]]) -> str:
        return "\n".join([
            "# HIP Flow Knowledge Graph",
            "",
            f"Run: `{graph.get('run_id')}`",
            f"Nodes: `{len(graph.get('nodes', []))}`  Edges: `{len(graph.get('edges', []))}`",
            "",
            "## Summary",
            "```json",
            json.dumps(graph.get("summary", {}), indent=2, ensure_ascii=False),
            "```",
            "",
            "## Click sequence preview",
            "```json",
            json.dumps(click_sequence[:30], indent=2, ensure_ascii=False),
            "```",
            "",
            "## Action sequence preview",
            "```json",
            json.dumps(action_sequence[:30], indent=2, ensure_ascii=False),
            "```",
        ])

    def _html(self, graph: Dict[str, Any], mermaid: str, click_sequence: List[Dict[str, Any]], action_sequence: List[Dict[str, Any]]) -> str:
        preview_nodes = graph.get("nodes", [])[:120]
        preview_edges = graph.get("edges", [])[:180]
        verified = [n for n in graph.get("nodes", []) if n.get("type") == "VERIFIED_ID"]
        rejected = [n for n in graph.get("nodes", []) if n.get("type") == "REJECTED_ID"]
        recovery = [n for n in graph.get("nodes", []) if n.get("type") == "RECOVERY_SUGGESTION"]
        return f"""<!doctype html>
<html><head><meta charset=\"utf-8\" />
<title>HIP Flow Knowledge Graph - {self.ctx.run_id}</title>
<script type=\"module\">import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs'; mermaid.initialize({{startOnLoad:true, securityLevel:'loose'}});</script>
<style>body{{font-family:Arial,sans-serif;margin:28px;color:#222}}.card{{border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0}}pre{{background:#f6f8fa;padding:12px;overflow:auto;max-height:520px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #ddd;padding:6px;vertical-align:top}}</style>
</head><body>
<h1>HIP Flow Knowledge Graph</h1>
<div class=\"card\"><b>Run:</b> {self.ctx.run_id}<br/><b>Customer:</b> {self.ctx.customer}<br/><b>Nodes:</b> {len(graph.get('nodes', []))} <b>Edges:</b> {len(graph.get('edges', []))}</div>
<h2>Flow graph</h2><div class=\"mermaid\">{mermaid}</div>
<h2>Verified IDs</h2><pre>{json.dumps(verified, indent=2, ensure_ascii=False)}</pre>
<h2>Rejected IDs</h2><pre>{json.dumps(rejected, indent=2, ensure_ascii=False)}</pre>
<h2>Recovery suggestions</h2><pre>{json.dumps(recovery, indent=2, ensure_ascii=False)}</pre>
<h2>Action sequence preview</h2><pre>{json.dumps(action_sequence[:80], indent=2, ensure_ascii=False)}</pre>
<h2>Click sequence preview</h2><pre>{json.dumps(click_sequence[:80], indent=2, ensure_ascii=False)}</pre>
<h2>Node preview</h2><pre>{json.dumps(preview_nodes, indent=2, ensure_ascii=False)}</pre>
<h2>Edge preview</h2><pre>{json.dumps(preview_edges, indent=2, ensure_ascii=False)}</pre>
</body></html>"""
