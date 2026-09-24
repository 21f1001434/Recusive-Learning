from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

import hip_id_agent.browser_session as browser_session_module
from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.certified_future_task_agent import CertifiedHIPFutureTaskExecutor
from hip_id_agent.change_governance import ChangeAuditLedger, HIPChangeGovernance
from hip_id_agent.config import AppConfig
from hip_id_agent.models import NetworkTabEvent
from hip_id_agent.mutation_outcome import classify_mutation_outcome


def test_dispatch_causality_fence_excludes_pre_dispatch_background_write():
    browser = BrowserSession.__new__(BrowserSession)
    browser.network_tab_events = [
        NetworkTabEvent(request_id="pre", url="https://developer.dell.com/hybrid-integrations/bizlink/flow/audit", method="POST", status=200, page_context="https://developer.dell.com/hybrid-integrations/bizlink/flow", stage="future"),
        NetworkTabEvent(request_id="post", url="https://developer.dell.com/hybrid-integrations/bizlink/flow/deploy", method="POST", status=201, page_context="https://developer.dell.com/hybrid-integrations/bizlink/flow", stage="future"),
    ]
    browser._cdp_requests = {}
    rows = CertifiedHIPFutureTaskExecutor._network_rows_since(
        browser, 0, capability_id="cap-deploy",
        dispatch={
            "dispatch_attempted": True,
            "network_request_ids_at_dispatch": ["pre"],
            "dispatch_page_url": "https://developer.dell.com/hybrid-integrations/bizlink/flow",
            "dispatch_stage": "future",
        },
    )
    assert [r["request_id"] for r in rows] == ["post"]
    assert rows[0]["caused_by_dispatch"] is True


def test_mutation_classifier_ignores_explicitly_uncorrelated_write():
    outcome = classify_mutation_outcome(
        dispatch={"dispatch_attempted": True, "dispatch_count": 1},
        network_rows=[
            {"method": "POST", "status": 201, "url": "/background", "caused_by_dispatch": False},
        ],
        ui_signal={}, structural_change=False,
        action_error="click timed out",
    )
    assert outcome["classification"] == "dispatch_attempted_no_write_response_observed"
    assert outcome["successful_2xx_write_response_count"] == 0
    assert outcome["uncorrelated_write_request_count"] == 1


def test_mutation_quarantine_retained_until_authoritative_resolution():
    browser = BrowserSession.__new__(BrowserSession)
    browser._mutation_quarantine = {
        "active": True, "classification": "awaiting_reconciliation",
        "action_hash": "abc", "armed_at": "now", "dispatch_count": 1,
    }
    retained = browser.resolve_mutation_dispatch_guard({"classification": "write_request_in_flight_or_response_lost"})
    assert retained["retained"] is True
    with pytest.raises(RuntimeError, match="HIP_MUTATION_QUARANTINE_ACTIVE"):
        browser._assert_mutation_dispatch_guard_clear()
    cleared = browser.resolve_mutation_dispatch_guard({"classification": "committed_verified"})
    assert cleared["cleared"] is True
    browser._assert_mutation_dispatch_guard_clear()


def test_public_click_path_serializes_and_checks_mutation_quarantine():
    import inspect
    source = inspect.getsource(BrowserSession.click_and_wait)
    assert "await mutation_lock.acquire()" in source
    assert "_assert_mutation_dispatch_guard_clear" in source
    assert source.index("_assert_mutation_dispatch_guard_clear") < source.index("_mark_click_dispatch")


@pytest.mark.asyncio
async def test_nested_surface_chain_is_invalidated_when_spa_route_changes(monkeypatch):
    browser = BrowserSession.__new__(BrowserSession)
    browser.page = types.SimpleNamespace(url="https://developer.dell.com/hybrid-integrations/bizlink/system")
    browser._semantic_surface_chain = [{
        "selector": "#dialog", "id": "dialog", "kind": "dialog", "role": "dialog",
        "chain_depth": 1, "page_url": "https://developer.dell.com/hybrid-integrations/bizlink/partner",
    }]
    called = []
    async def fake_refresh(page, proof):
        called.append(True)
        return {"rebound": True, "selector": "#dialog", "surface_proof": proof}
    monkeypatch.setattr(browser_session_module, "refresh_affordance_surface_lease", fake_refresh)
    active = await browser._active_semantic_surface_lease()
    assert active["active"] is False
    assert active["route_invalidated"] is True
    assert browser._semantic_surface_chain == []
    assert called == []


def test_cross_run_unresolved_execution_is_quarantined_until_terminal_resolution(tmp_path: Path):
    ledger = ChangeAuditLedger(tmp_path / "ledger.jsonl")
    key = "same-change"
    started = ledger.append("change_execution_started", {"idempotency_key": key})
    hazard = ledger.unresolved_mutation(key, window_hours=24)
    assert hazard and hazard["event_hash"] == started["event_hash"]

    ledger.append("change_failed", {
        "idempotency_key": key,
        "post_status": "mutation_rejected_verified",
        "manual_review_required": False,
    })
    assert ledger.unresolved_mutation(key, window_hours=24) is None
    assert ledger.status()["chain_valid"] is True


def test_governance_force_repeat_does_not_bypass_unresolved_execution(tmp_path: Path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.brain.directory = "brain"
    graph = HIPCapabilityGraph(tmp_path / "graph")
    gov = HIPChangeGovernance(cfg, graph)
    plan = {
        "pass": True,
        "mutation_required": True,
        "families": ["flow"],
        "subtasks": [{
            "page_family": "flow",
            "certified_family_ready": True,
            "entity": "example",
            "steps": [{"type": "click", "action": "Deploy", "risk": "mutation"}],
        }],
    }
    first = gov.preview(task="Deploy flow example", plan=plan, operator_role="admin", force_repeat_mutation=False)
    key = first["idempotency"]["idempotency_key"]
    gov.ledger.append("change_execution_started", {"idempotency_key": key})
    second = gov.preview(task="Deploy flow example", plan=plan, operator_role="admin", force_repeat_mutation=True)
    assert second["pass"] is False
    assert second["idempotency"]["unresolved_prior_execution"] is True
    assert second["idempotency"]["unresolved_replay_blocked"] is True


def test_runtime_self_heal_never_replays_certified_mutation_outcome():
    from hip_id_agent.runtime_self_heal import RuntimeSelfHealController
    assert RuntimeSelfHealController.classify_failure(
        "Mutation outcome rejected_verified; automatic retry prohibited"
    ) == "unsafe_or_mutating"
    assert RuntimeSelfHealController.classify_failure(
        "HIP_MUTATION_QUARANTINE_ACTIVE: prior write is unresolved"
    ) == "unsafe_or_mutating"


def _ledger_process_writer(path: str, prefix: str, count: int) -> None:
    ledger = ChangeAuditLedger(path)
    for i in range(count):
        ledger.append("parallel_test", {"idempotency_key": f"{prefix}-{i}", "worker": prefix})


def test_governance_ledger_parallel_process_appends_keep_one_valid_hash_chain(tmp_path: Path):
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    ledger_path = tmp_path / "parallel-ledger.jsonl"
    processes = [
        ctx.Process(target=_ledger_process_writer, args=(str(ledger_path), f"w{i}", 6))
        for i in range(3)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(10)
        assert p.exitcode == 0
    status = ChangeAuditLedger(ledger_path).status(limit=100)
    assert status["event_count"] == 18
    assert status["chain_valid"] is True


def test_final_mutation_context_barrier_precedes_physical_dispatch_marker():
    import inspect
    source = inspect.getsource(BrowserSession.click_and_wait)
    planning = source.index("_autowebglm_primary_decision")
    final_barrier = source.index("HIP_MUTATION_PREDISPATCH_AUTH_DRIFT")
    first_dispatch = source.index("_mark_click_dispatch")
    assert planning < final_barrier < first_dispatch
    assert "HIP_MUTATION_PREDISPATCH_ROUTE_DRIFT" in source
    assert "HIP_MUTATION_PREDISPATCH_LOCATOR_DRIFT" in source
