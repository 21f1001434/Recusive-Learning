from __future__ import annotations

import json
from pathlib import Path

import pytest

from hip_id_agent.action_model import HIPActionModel
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from hip_id_agent.flow_pattern_memory import FlowPatternMemory
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph


class _Body:
    async def inner_text(self, timeout=0):
        return "Developer SecureLink Document Type Create Document Type"


class _Page:
    url = "https://developer.dell.com/hybrid-integrations/securelink/doctypes"
    def locator(self, selector):
        return _Body()


class _Browser:
    def __init__(self):
        self.page = _Page(); self.console_messages=[]; self.network_tab_events=[]; self.action_events=[]
    async def _observe_react_navigation_state(self, target_url): return {"target_match": True, "target_usable": True, "same_actual_surface": True, "logged_in": True}
    async def screenshot(self, path, full_page=True): Path(path).write_bytes(b"png"); return str(path)
    async def save_dom_snapshot(self, name): return {"html": name + ".html"}
    async def _ensure_active_page(self, target_url=""): return self.page
    async def _react_ensure_target_surface(self, target_url, max_steps=4): return {"pass": True}
    async def _consolidate_session_pages(self, target_url=""): return {"pass": True}
    async def _verify_dual_mcp_same_surface(self, *args, **kwargs): return {"pass": True}
    async def wait_for_blocking_overlays_gone(self, timeout_ms=0): return True
    async def _dismiss_transient_ui(self, next_phase=""): return {"pass": True}
    async def _ensure_page_observers(self): return None
    async def goto_base_and_complete_sso(self, target_url): return None


@pytest.mark.asyncio
async def test_until_complete_cannot_repeat_same_document_type_failure_forever(tmp_path: Path):
    cfg = AppConfig(); cfg.aia.enabled = False
    cfg.runtime_self_heal.until_complete = True
    cfg.runtime_self_heal.max_no_progress_repeats = 3
    controller = RuntimeSelfHealController(config=cfg, root_dir=tmp_path, browser=_Browser(), until_complete=True)
    rows=[]
    for attempt in range(1, 5):
        rows.append(await controller.handle_failure(
            phase="source_document_type", target_url=_Page.url, attempt=attempt,
            message="required semantic control not found for data_format_type",
        ))
    assert [x.retry for x in rows] == [True, True, True, False]
    assert rows[-1].action == "stop_fail_closed"
    assert "NO_PROGRESS_STALL_GUARD" in rows[-1].reason
    assert controller.summary()["max_phase_wall_seconds"] >= 60


def test_canonical_kb_bootstraps_nonblank_capabilities_and_api_contracts(tmp_path: Path):
    graph = HIPCapabilityGraph(tmp_path / "brain")
    kb = Path(__file__).resolve().parents[1] / "knowledge_base" / "HIP_Unified_Deep_KB.json"
    result = graph.bootstrap_from_unified_kb(kb)
    assert result["status"] == "seeded"
    assert result["capabilities_seeded"] > 20
    assert result["apis_seeded"] > 5
    caps = graph.query_capabilities(page_family="document_types")
    assert caps
    assert any(x.get("label") == "Name" for x in caps)
    assert all(x.get("knowledge_source") == "canonical_kb" for x in caps)
    apis = list(graph.data["api_contracts"].values())
    assert any("document-type/api/document-type" in x.get("endpoint", "") for x in apis)
    assert any(x.get("trust") == "canonical_contract" for x in apis)
    second = graph.bootstrap_from_unified_kb(kb)
    assert second["status"] == "already_seeded"


def _successful_execution(graph):
    return {
        "pass": True, "status": "pass", "final_form_state_model": {"one_to_one_pass": True},
        "attempts": [
            {
                "node_id": n["node_id"], "field": n["field_key"], "section": n.get("section"),
                "row_kind": n.get("row_kind"), "row_index": n.get("row_index"), "success": True,
                "binding_diagnostics": {"selected_identity": f"semantic::{n['field_key']}"},
                "transaction_proof": {"protected_state_changes": []},
            }
            for n in graph.get("nodes", [])
        ],
    }


def test_source_document_type_validated_memory_replays_target_without_values(tmp_path: Path):
    payload = json.loads((Path(__file__).resolve().parents[1] / "examples" / "uhaul_poasn_full_dummy_input.json").read_text())
    source = compile_phase_state_graph(payload, "source_document_type")
    target = compile_phase_state_graph(payload, "target_document_type")
    memory = FlowPatternMemory(tmp_path / "flow", minimum_similarity=0.70)
    promoted = memory.promote(graph=source, execution=_successful_execution(source), phase="source_document_type", run_id="run-1", judge_pass=True)
    assert promoted["trust"] == "validated"
    applied = memory.apply_to_graph(target, phase="target_document_type")
    assert applied["validated_replay"] is True
    assert applied["memory_replay_profile"]["source_phase"] == "source_document_type"
    assert applied["memory_replay_profile"]["values_reused"] is False
    assert any(n.get("validated_memory_binding_identity") for n in applied["nodes"])


def test_document_type_kb_skips_new_discovery_when_validated_sibling_memory_exists():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "doctype_kb.py").read_text()
    assert "skipped_validated_same_family_replay" in source
    assert "flow_memory.apply_to_graph(state_graph, phase=phase_name)" in source
    assert "no new dropdown/structure discovery is allowed" in source
    assert "skipped_canonical_deterministic_first" in source
    assert "recovery_only_after_proven_deterministic_failure" in source


def test_agentq_reward_suppresses_repeated_losing_action():
    model = HIPActionModel()
    matches = [
        {"selected_recovery": "execute_bound_action", "reward": -0.7, "success": False}
        for _ in range(3)
    ]
    plan = model.plan(
        node={"node_id":"n1", "field_key":"data_format_type", "action":"select_single", "depends_on":[]},
        representation={}, binding={"resolved": True, "best_score": 100, "score_margin": 50, "control":{"interactable":True}},
        surface_gate={"pass":True}, memory_matches=matches, interaction_state={},
    )
    execute = next(x for x in plan["candidate_actions"] if x["action"] == "execute_bound_action")
    assert execute["learned_loser"] is True
    assert execute["allowed"] is False
    assert plan["selected_action"] != "execute_bound_action"
    assert plan["negative_action_suppression"] is True


def test_supplied_web_agent_pattern_is_reused_safely_as_stuck_probe_not_second_browser():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "runtime_self_heal.py").read_text()
    assert "hip.stuck-state-probe.v1" in source
    assert "existing_authenticated_page_dom_plus_screenshot" in source
    assert "value_free" in source

def test_agentq_remote_advice_cannot_override_local_reward_gate():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "agentq_runtime.py").read_text()
    assert "accepted_with_local_reward_gate" in source
    assert "rejected_by_local_reward_or_safe_action_gate" in source
    assert "local_candidate.get(\"allowed\"" in source

def test_failed_live_attempts_still_surface_candidate_capabilities_and_api_learning():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "dummy_fill_e2e.py").read_text()
    assert 'knowledge_source="runtime_plan"' in source
    assert 'trust="candidate"' in source
    assert 'knowledge_source="live_network"' in source
    assert 'trust="observed_candidate" if not success else "observed_success"' in source
    assert "capability_graph.save()" in source

def test_javascript_learning_tabs_show_seed_and_live_memory_counts():
    app = (Path(__file__).resolve().parents[1] / "webui" / "app.js").read_text()
    html = (Path(__file__).resolve().parents[1] / "webui" / "index.html").read_text()
    assert "capabilitySummary" in app and "capabilitySummary" in html
    assert "apiSummary" in app and "apiSummary" in html
    assert "validated_live" in app
    assert "canonical contracts are available immediately" in app
