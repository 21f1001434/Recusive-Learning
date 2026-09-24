import json
from pathlib import Path

from hip_id_agent.knowledge_graph import KnowledgeGraphWriter
from hip_id_agent.models import ActionEvent, NetworkTabEvent, RunContext


def test_kg_contains_backend_report_action_network_and_verified_edges(tmp_path: Path):
    ctx = RunContext("RUNKG", "UHAL", "UHAL", "UHAL-SFTP", tmp_path, tmp_path / "screenshots")
    ctx.registry.update({
        "run_status": "success",
        "requested_browser_backend": "auto",
        "browser_backend_used": "playwright",
        "fallback_reason": "MCP unavailable",
        "enriched_input_json": str(tmp_path / "enriched_input.json"),
    })
    (tmp_path / "verified_ids.json").write_text(json.dumps([{"object_type": "partner", "id_value": "10483", "confidence": 0.95, "reasons": ["typed field"]}]), encoding="utf-8")
    (tmp_path / "rejected_ids.json").write_text(json.dumps([{"object_type": "partner", "candidate_id": "99999", "rejection_reasons": ["generic unrelated id"]}]), encoding="utf-8")
    (tmp_path / "id_candidates.json").write_text("[]", encoding="utf-8")
    (tmp_path / "recovery_suggestions.json").write_text("[]", encoding="utf-8")
    actions = [ActionEvent("act-1", "search", "detected_search_box", value_redacted="UHAL", network_events_triggered=["req1"], stage="extract_partner_id")]
    nets = [NetworkTabEvent("req1", "https://developer.dell.com/api/partners/search", "GET", status=200, stage="extract_partner_id")]
    paths = KnowledgeGraphWriter(ctx).write(click_events=[], action_events=actions, network_events=nets, report_paths={"json": str(tmp_path / "report.json")})
    graph = json.loads(Path(paths["knowledge_graph_json"]).read_text(encoding="utf-8"))
    rels = {e["relation"] for e in graph["edges"]}
    types = {n["type"] for n in graph["nodes"]}
    assert "BACKEND" in types
    assert "REPORT" in types
    assert "REJECTED_ID" in types
    assert "ACTION_TRIGGERED_NETWORK" in rels
    assert "RUN_GENERATED_REPORT" in rels
    assert "VERIFIED_ID_ENRICHED_INPUT" in rels
    assert "CANDIDATE_REJECTED_BECAUSE" in rels
