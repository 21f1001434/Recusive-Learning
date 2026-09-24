from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.doctype_kb import (
    build_doctype_api_flow_knowledge_graph,
    _write_doctype_api_flow_knowledge_graph_safe,
)
from hip_id_agent.rules_kb import build_rule_api_flow_knowledge_graph
from hip_id_agent.transport_profile_kb import build_transport_profile_api_flow_knowledge_graph
from hip_id_agent.dummy_fill_e2e import (
    phase_exact_completion_checkpoint,
    synthesize_summary_after_reporting_failure,
)
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


def _kg_payload(kind: str) -> dict:
    base = {
        "run_id": "run-reporting-regression",
        "url": "https://developer.dell.com/hybrid-integrations/securelink/test",
        "pagination_replay_audit": [{"url": "/page/1", "order": 999}],
        "detail_enrichment_audit": [{
            "document_type_name": "X",
            "rule_name": "X",
            "transport_profile_name": "X",
            "attempts": [{"url": "/detail/1", "order": 999}],
        }],
        "dummy_fill_attempts": [{"field": "name", "selector": "#name", "filled": True, "order": 999, "node_id": "audit-node", "node_type": "audit"}],
    }
    if kind == "doctype":
        base.update({
            "doctype_api_interactions": [],
            "old_doctypes_inventory": [],
            "form_controls": [],
            "required_fields": [],
            "dropdowns": [],
        })
    elif kind == "rule":
        base.update({
            "rule_api_interactions": [],
            "old_rules_inventory": [],
            "form_controls": [],
            "required_fields": [],
            "dropdowns": [],
        })
    else:
        base.update({
            "transport_profile_api_interactions": [],
            "old_transport_profiles_inventory": [],
            "form_controls": [],
            "required_fields": [],
            "dropdowns": [],
        })
    return base


@pytest.mark.parametrize(
    "builder,kind",
    [
        (build_doctype_api_flow_knowledge_graph, "doctype"),
        (build_rule_api_flow_knowledge_graph, "rule"),
        (build_transport_profile_api_flow_knowledge_graph, "transport"),
    ],
)
def test_kg_builders_merge_order_without_duplicate_keyword_crash(builder, kind):
    graph = builder(_kg_payload(kind))
    assert graph["nodes"]
    pagination = next(n for n in graph["nodes"] if n["type"] == "PAGINATION_REPLAY")
    assert pagination["properties"]["order"] == 1


def test_doctype_kg_export_failure_is_nonblocking(tmp_path: Path, monkeypatch):
    import hip_id_agent.doctype_kb as module

    def boom(*_args, **_kwargs):
        raise TypeError("synthetic serializer defect")

    monkeypatch.setattr(module, "_write_doctype_api_flow_knowledge_graph", boom)
    files = _write_doctype_api_flow_knowledge_graph_safe(tmp_path, {"run_id": "r1"})
    status_path = Path(files["doctype_api_flow_kg_export_status"])
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "warning"
    assert status["non_blocking"] is True
    assert "serializer defect" in status["error"]


def test_exact_completion_checkpoint_accepts_completed_document_type(tmp_path: Path):
    artifact = tmp_path / "doctype_kb" / "doctype_target_branch_execution.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({
        "pass": True,
        "status": "pass",
        "attempts": [
            {"field": "document_type_name", "success": True, "exact_verified": True},
            {"field": "attribute_usage", "success": True, "exact_verified": True},
        ],
    }), encoding="utf-8")
    checkpoint = phase_exact_completion_checkpoint("source_document_type", tmp_path)
    assert checkpoint["pass"] is True
    assert checkpoint["status"] == "exact_live_execution_completed"


def test_exact_completion_checkpoint_rejects_failed_live_attempt(tmp_path: Path):
    artifact = tmp_path / "rule_kb" / "rule_target_branch_execution.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({
        "pass": True,
        "status": "pass",
        "attempts": [{"field": "condition", "success": False}],
    }), encoding="utf-8")
    checkpoint = phase_exact_completion_checkpoint("rule", tmp_path)
    assert checkpoint["pass"] is False


def test_reporting_failure_summary_requests_no_replay(tmp_path: Path):
    checkpoint = {"pass": True, "authoritative_artifact": "x.json"}
    summary = synthesize_summary_after_reporting_failure(
        phase="source_document_type",
        phase_dir=tmp_path,
        error="multiple values for keyword argument 'order'",
        checkpoint=checkpoint,
    )
    assert summary["status"] == "completed_with_reporting_warning"
    assert summary["reporting_failure_recovered_without_replay"] is True
    assert summary["exact_completion_checkpoint"]["pass"] is True


class _Browser:
    def __init__(self):
        self.calls = []
        self.console_messages = []
        self.network_tab_events = []
        self.action_events = []

    async def _consolidate_session_pages(self, target_url=""):
        self.calls.append("consolidate")
        return {"pass": True}

    async def _ensure_page_observers(self):
        self.calls.append("observers")

    async def collect_dom_click_log(self):
        self.calls.append("collect")

    async def goto_base_and_complete_sso(self, target_url):
        self.calls.append("goto")

    async def _dismiss_transient_ui(self, next_phase=""):
        self.calls.append("dismiss")

    async def _observe_react_navigation_state(self, target_url):
        return {"target_url": target_url, "current_url": target_url, "target_match": True}

    async def screenshot(self, path, full_page=True):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")

    async def save_dom_snapshot(self, name):
        return {"html": f"{name}.html"}


@pytest.mark.asyncio
async def test_reporting_only_self_heal_refreshes_evidence_without_reopening(tmp_path: Path):
    cfg = AppConfig()
    cfg.aia.enabled = False
    browser = _Browser()
    controller = RuntimeSelfHealController(config=cfg, root_dir=tmp_path, browser=browser)
    decision = await controller.handle_failure(
        phase="source_document_type",
        target_url="https://developer.dell.com/hybrid-integrations/securelink/doctypes",
        attempt=1,
        message="knowledge graph export got multiple values for keyword argument 'order'",
    )
    assert decision.classification == "reporting_only_failure"
    assert decision.action == "rejudge_after_evidence_refresh"
    assert decision.action_result["phase_replay_required"] is False
    assert "goto" not in browser.calls
    assert "dismiss" not in browser.calls


def test_full_run_has_reporting_checkpoint_before_replay():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert "phase_exact_completion_checkpoint" in source
    assert "reporting_failure_recovered_without_replay.json" in source
    assert '"phase_replay_required": False' in source
