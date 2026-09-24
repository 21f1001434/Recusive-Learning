import asyncio
from pathlib import Path

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.knowledge_graph import KnowledgeGraphWriter
from hip_id_agent.models import RunContext, StageResult, utc_now

REQUIRED = [
    "exception.txt", "current_url.txt", "last_screenshot.png", "last_dom_snapshot.html", "last_dom_text.txt",
    "last_20_actions.json", "last_50_network_events.json", "console_logs.jsonl", "id_candidates.json",
    "rejected_candidates.json", "recovery_suggestions.json", "partial_knowledge_graph.json", "partial_knowledge_graph.html",
]


def test_forced_exception_failure_bundle_contains_every_required_file(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path)
    paths = asyncio.run(session.write_failure_bundle(RuntimeError("boom")))
    for name in REQUIRED:
        assert (tmp_path / "failure_bundle" / name).exists(), name
        assert name in paths


def test_failed_logical_run_kg_has_failure_and_recovery_edges(tmp_path: Path):
    ctx = RunContext("RUNFAIL", "UHAL", "UHAL", "UHAL-SFTP", tmp_path, tmp_path / "screenshots")
    ctx.registry["run_status"] = "failed"
    ctx.registry["logical_failures"] = ["Partner ID is required but was not verified/found"]
    (tmp_path / "recovery_suggestions.json").write_text('["Open details page and verify typed partnerId"]', encoding="utf-8")
    ctx.stage_results.append(StageResult("extract_partner_id", "failed", "Could not verify partner ID", utc_now(), utc_now(), errors=["Could not verify partner ID"]))
    paths = KnowledgeGraphWriter(ctx).write(click_events=[], action_events=[], network_events=[], report_paths={"html": str(tmp_path / "report.html")})
    graph = (Path(paths["knowledge_graph_json"]).read_text(encoding="utf-8"))
    assert "FAILURE" in graph
    assert "RUN_GENERATED_FAILURE" in graph
    assert "FAILURE_HAS_RECOVERY" in graph
    assert "RUN_GENERATED_REPORT" in graph
