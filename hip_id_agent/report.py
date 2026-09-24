from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Template

from .models import RunContext
from .security import mask_sensitive_data


def to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    return obj


def _load_json_if_exists(path: Path, limit: int | None = None) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list) and limit:
            return data[:limit]
        return data
    except Exception:
        return []


class ReportWriter:
    def __init__(self, run_context: RunContext):
        self.ctx = run_context

    def build_summary(self) -> Dict[str, Any]:
        network_path = self.ctx.run_dir / "network" / "network_tab_events.json"
        clicks_path = self.ctx.run_dir / "clicks" / "click_events.json"
        console_path = self.ctx.run_dir / "console" / "console_messages.json"
        kg_path = Path(str(self.ctx.registry.get("knowledge_graph_json", ""))) if self.ctx.registry.get("knowledge_graph_json") else None
        click_sequence_path = Path(str(self.ctx.registry.get("click_sequence_json", ""))) if self.ctx.registry.get("click_sequence_json") else None
        kg_preview = _load_json_if_exists(kg_path) if kg_path and kg_path.exists() else {}
        click_sequence_preview = _load_json_if_exists(click_sequence_path, limit=60) if click_sequence_path and click_sequence_path.exists() else []
        data = {
            "run_id": self.ctx.run_id,
            "customer": self.ctx.customer,
            "partner_query": self.ctx.partner_query,
            "system_query": self.ctx.system_query,
            "started_at": self.ctx.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "run_status": self.ctx.registry.get("run_status") or self._infer_status(),
            "browser_backend": self.ctx.registry.get("browser_backend_used", "playwright"),
            "requested_browser_backend": self.ctx.registry.get("requested_browser_backend"),
            "registry": self.ctx.registry,
            "stages": [to_jsonable(s) for s in self.ctx.stage_results],
            "input_entities_detected": self.ctx.registry.get("input_entities_detected", {}),
            "id_candidates": _load_json_if_exists(self.ctx.run_dir / "id_candidates.json", limit=120),
            "verified_ids": _load_json_if_exists(self.ctx.run_dir / "verified_ids.json", limit=80),
            "rejected_ids": _load_json_if_exists(self.ctx.run_dir / "rejected_ids.json", limit=120),
            "recovery_suggestions": _load_json_if_exists(self.ctx.run_dir / "recovery_suggestions.json"),
            "evidence_files": {
                "network_events_jsonl": str(self.ctx.run_dir / "network_events.jsonl"),
                "network_summary": str(self.ctx.run_dir / "network_summary.json"),
                "network_tab_events": str(network_path),
                "click_events": str(clicks_path),
                "action_sequence": str(self.ctx.run_dir / "action_sequence.json"),
                "console_messages": str(console_path),
                "enriched_input_redacted_json": self.ctx.registry.get("enriched_input_redacted_json"),
            },
            "network_preview": _load_json_if_exists(network_path, limit=20),
            "click_preview": _load_json_if_exists(clicks_path, limit=40),
            "console_preview": _load_json_if_exists(console_path, limit=40),
            "knowledge_graph_files": {
                "json": self.ctx.registry.get("knowledge_graph_json"),
                "html": self.ctx.registry.get("knowledge_graph_html"),
                "mermaid": self.ctx.registry.get("knowledge_graph_mermaid"),
                "click_sequence": self.ctx.registry.get("click_sequence_json"),
            },
            "knowledge_graph_summary": kg_preview.get("summary", {}) if isinstance(kg_preview, dict) else {},
            "knowledge_graph_node_preview": (kg_preview.get("nodes", [])[:30] if isinstance(kg_preview, dict) else []),
            "knowledge_graph_edge_preview": (kg_preview.get("edges", [])[:40] if isinstance(kg_preview, dict) else []),
            "click_sequence_preview": click_sequence_preview,
        }
        return mask_sensitive_data(data)

    def _infer_status(self) -> str:
        statuses = [s.status for s in self.ctx.stage_results]
        if statuses and all(x == "success" for x in statuses):
            return "success"
        # Treat partial_success as a first-class non-fatal status. This matters for
        # export-all runs where most inventory is collected, but a small number of
        # read-only fan-out requests return live gateway 500s and are written to
        # inventory/failed_requests.json for audit.
        if any(x in {"success", "partial_success"} for x in statuses):
            return "partial_success"
        return "failed" if statuses else "unknown"

    def write_all(self) -> Dict[str, str]:
        self.ctx.run_dir.mkdir(parents=True, exist_ok=True)
        summary = self.build_summary()
        paths: Dict[str, str] = {}
        json_path = self.ctx.run_dir / "final_report.json"
        json_text = json.dumps(summary, indent=2, ensure_ascii=False, default=str)
        json_path.write_text(json_text, encoding="utf-8")
        (self.ctx.run_dir / "report.json").write_text(json_text, encoding="utf-8")
        paths["json"] = str(json_path)
        paths["report_json"] = str(self.ctx.run_dir / "report.json")

        md_path = self.ctx.run_dir / "final_report.md"
        md_text = self._markdown(summary)
        md_path.write_text(md_text, encoding="utf-8")
        (self.ctx.run_dir / "report.md").write_text(md_text, encoding="utf-8")
        paths["markdown"] = str(md_path)
        paths["report_md"] = str(self.ctx.run_dir / "report.md")

        html_path = self.ctx.run_dir / "final_report.html"
        html_text = self._html(summary)
        html_path.write_text(html_text, encoding="utf-8")
        (self.ctx.run_dir / "report.html").write_text(html_text, encoding="utf-8")
        paths["html"] = str(html_path)
        paths["report_html"] = str(self.ctx.run_dir / "report.html")
        return paths

    def _markdown(self, summary: Dict[str, Any]) -> str:
        lines = [
            "# HIP Portal Partner/System ID Extraction Report",
            "",
            f"**Run ID:** {summary['run_id']}",
            f"**Customer:** {summary['customer']}",
            f"**Partner Query:** {summary['partner_query']}",
            f"**System Query:** {summary['system_query']}",
            f"**Run Status:** {summary.get('run_status')}",
            f"**Browser Backend:** {summary.get('browser_backend')} (requested: {summary.get('requested_browser_backend')})",
            f"**Started:** {summary['started_at']}",
            f"**Finished:** {summary['finished_at']}",
            "",
            "## Extracted Registry",
            "",
            "```json",
            json.dumps(summary.get("registry", {}), indent=2, ensure_ascii=False),
            "```",
            "",
            "## Evidence Files",
            "",
        ]
        for k, v in summary.get("evidence_files", {}).items():
            lines.append(f"- **{k}:** `{v}`")
        lines.extend(["", "## Stage Results", ""])
        for stage in summary.get("stages", []):
            lines.extend([
                f"### {stage.get('stage')}",
                f"- Status: **{stage.get('status')}**",
                f"- Message: {stage.get('message')}",
                f"- Started: {stage.get('started_at')}",
                f"- Finished: {stage.get('finished_at')}",
            ])
            if stage.get("extracted_ids"):
                lines.append("- Extracted IDs:")
                for item in stage["extracted_ids"]:
                    lines.append(f"  - {item.get('object_type')}: `{item.get('object_id')}` confidence={item.get('confidence')} source={item.get('source')}")
            if stage.get("warnings"):
                lines.append("- Warnings:")
                for w in stage["warnings"]:
                    lines.append(f"  - {w}")
            if stage.get("errors"):
                lines.append("- Errors:")
                for e in stage["errors"]:
                    lines.append(f"  - {e}")
            if stage.get("screenshots"):
                lines.append("- Screenshots:")
                for s in stage["screenshots"]:
                    lines.append(f"  - `{s}`")
            evidence = stage.get("evidence", {})
            if evidence:
                lines.append("- Evidence summary:")
                for k in ["snapshot_url", "page_title", "tables_detected", "controls_detected", "network_tab_events_seen", "click_events_seen"]:
                    if k in evidence:
                        lines.append(f"  - {k}: `{evidence[k]}`")
            lines.append("")
        lines.extend(["## Verified IDs", "", "```json", json.dumps(summary.get("verified_ids", []), indent=2, ensure_ascii=False), "```", "", "## Rejected IDs", "", "```json", json.dumps(summary.get("rejected_ids", [])[:80], indent=2, ensure_ascii=False), "```", "", "## Recovery Suggestions", "", "```json", json.dumps(summary.get("recovery_suggestions", []), indent=2, ensure_ascii=False), "```", "", "## Click Preview", "", "```json", json.dumps(summary.get("click_preview", [])[:10], indent=2, ensure_ascii=False), "```", "", "## Network Preview", "", "```json", json.dumps(summary.get("network_preview", [])[:5], indent=2, ensure_ascii=False), "```"])
        return "\n".join(lines)

    def _html(self, summary: Dict[str, Any]) -> str:
        template = Template("""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>HIP ID Extraction Report - {{ run_id }}</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 28px; color: #222; }
    h1 { color: #123; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 12px 0; }
    .ok { color: #087f23; font-weight: bold; }
    .warn { color: #a66b00; font-weight: bold; }
    .fail { color: #b00020; font-weight: bold; }
    code, pre { background: #f6f8fa; padding: 2px 4px; border-radius: 4px; }
    pre { padding: 12px; overflow:auto; max-height: 420px; }
    img { max-width: 900px; border: 1px solid #eee; margin: 8px 0; }
    table { border-collapse: collapse; width: 100%; }
    td, th { border: 1px solid #ddd; padding: 6px; }
  </style>
</head>
<body>
  <h1>HIP Portal Partner/System ID Extraction Report</h1>
  <div class="card">
    <p><b>Run ID:</b> {{ run_id }}</p>
    <p><b>Customer:</b> {{ customer }}</p>
    <p><b>Partner Query:</b> {{ partner_query }}</p>
    <p><b>System Query:</b> {{ system_query }}</p>
    <p><b>Run Status:</b> {{ run_status }}</p>
    <p><b>Browser Backend:</b> {{ browser_backend }} (requested: {{ requested_browser_backend }})</p>
    <p><b>Started:</b> {{ started_at }}</p>
    <p><b>Finished:</b> {{ finished_at }}</p>
  </div>
  <h2>Extracted Registry</h2>
  <pre>{{ registry_json }}</pre>
  <h2>Knowledge Graph</h2>
  <div class="card">
    <p><b>Summary:</b></p>
    <pre>{{ kg_summary_json }}</pre>
    <p><b>Files:</b></p>
    <ul>{% for k,v in knowledge_graph_files.items() %}{% if v %}<li><b>{{ k }}</b>: <code>{{ v }}</code></li>{% endif %}{% endfor %}</ul>
    <p><b>Click sequence preview:</b></p>
    <pre>{{ click_sequence_json }}</pre>
  </div>
  <h2>Evidence Files</h2>
  <ul>{% for k,v in evidence_files.items() %}<li><b>{{ k }}</b>: <code>{{ v }}</code></li>{% endfor %}</ul>
  <h2>Stages</h2>
  {% for s in stages %}
    <div class="card">
      <h3>{{ s.stage }}</h3>
      <p>Status: <span class="{{ 'ok' if s.status == 'success' else 'warn' if s.status == 'warning' else 'fail' }}">{{ s.status }}</span></p>
      <p>{{ s.message }}</p>
      {% if s.extracted_ids %}
      <h4>Extracted IDs</h4>
      <ul>{% for item in s.extracted_ids %}<li>{{ item.object_type }}: <code>{{ item.object_id }}</code> confidence={{ item.confidence }} source={{ item.source }}</li>{% endfor %}</ul>
      {% endif %}
      {% if s.warnings %}<h4>Warnings</h4><ul>{% for w in s.warnings %}<li>{{ w }}</li>{% endfor %}</ul>{% endif %}
      {% if s.errors %}<h4>Errors</h4><ul>{% for e in s.errors %}<li>{{ e }}</li>{% endfor %}</ul>{% endif %}
      {% if s.screenshots %}<h4>Screenshots</h4>{% for img in s.screenshots %}<div><code>{{ img }}</code></div>{% endfor %}{% endif %}
      <h4>Evidence</h4><pre>{{ s.evidence | tojson(indent=2) }}</pre>
    </div>
  {% endfor %}
  <h2>Verified IDs</h2><pre>{{ verified_json }}</pre>
  <h2>Rejected IDs</h2><pre>{{ rejected_json }}</pre>
  <h2>Recovery Suggestions</h2><pre>{{ recovery_json }}</pre>
  <h2>Click Preview</h2><pre>{{ click_json }}</pre>
  <h2>Network Preview</h2><pre>{{ network_json }}</pre>
</body>
</html>
""")
        render_context = {
            **summary,
            "registry_json": json.dumps(summary.get("registry", {}), indent=2, ensure_ascii=False),
            "click_json": json.dumps(summary.get("click_preview", [])[:20], indent=2, ensure_ascii=False),
            "network_json": json.dumps(summary.get("network_preview", [])[:10], indent=2, ensure_ascii=False),
            "kg_summary_json": json.dumps(summary.get("knowledge_graph_summary", {}), indent=2, ensure_ascii=False),
            "click_sequence_json": json.dumps(summary.get("click_sequence_preview", [])[:20], indent=2, ensure_ascii=False),
            "verified_json": json.dumps(summary.get("verified_ids", []), indent=2, ensure_ascii=False),
            "rejected_json": json.dumps(summary.get("rejected_ids", [])[:80], indent=2, ensure_ascii=False),
            "recovery_json": json.dumps(summary.get("recovery_suggestions", []), indent=2, ensure_ascii=False),
        }
        return template.render(**render_context)
