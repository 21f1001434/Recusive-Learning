from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import hip_id_agent.datamap_kb as datamap
import hip_id_agent.rules_kb as rules
import hip_id_agent.transport_profile_kb as tp
import hip_id_agent.bizflow_kb as bizflow
import hip_id_agent.stateful_form_runtime as runtime
import hip_id_agent.dummy_fill_e2e as e2e
import hip_id_agent.mission_trace as mission_trace
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


def _simple_graph() -> dict:
    return {
        "graph_id": "g1",
        "strategy": "target-branch-first-then-safe-exploration",
        "nodes": [
            {
                "node_id": "data_map.create_map.map_identifier",
                "phase": "data_map",
                "section": "Create Map",
                "field_key": "map_identifier",
                "action": "fill_text",
                "expected_value": "MAP_1",
                "input_path": "data_map.map_identifier",
                "semantic_locator": {
                    "labels": ["Map Identifier"],
                    "names": ["mapIdentifier"],
                    "placeholders": [],
                    "roles": [],
                    "section_aliases": ["Map Reference"],
                },
                "required": True,
                "depends_on": [],
                "row_kind": "",
                "row_index": None,
            }
        ],
        "dependency_edges": [],
        "repeatable_rows": {},
    }


class _FakePage:
    async def evaluate(self, *_args, **_kwargs):
        return {"event_seq": 0, "mutation_seq": 0}

    async def wait_for_timeout(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_strict_execution_fails_before_judge_when_no_controls(monkeypatch):
    async def no_controls(_page, _phase):
        return []

    async def surface_ok(_page, _phase):
        return {"fatal": [], "pass": True}

    async def agentq_start(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(runtime, "capture_stateful_controls", no_controls)
    monkeypatch.setattr(runtime, "assert_active_surface", surface_ok)
    monkeypatch.setattr(runtime, "_agentq_begin_phase", agentq_start)

    with pytest.raises(RuntimeError, match="HIP_FORM_CONTROLS_NOT_DISCOVERED"):
        await runtime.execute_phase_state_graph(
            _FakePage(), _simple_graph(), phase="data_map", strict_live_execution=True
        )


@pytest.mark.asyncio
async def test_strict_execution_rejects_unbound_listing_control(monkeypatch):
    irrelevant = {
        "index": 0,
        "selector": "input#search",
        "section": "Data Maps",
        "label": "Search",
        "name": "search",
        "placeholder": "Search",
        "semantic_key": "search",
        "type": "text",
        "role": "",
        "value": "",
        "disabled": False,
        "readonly": False,
        "interactable": True,
    }

    async def controls(_page, _phase):
        return [dict(irrelevant)]

    async def surface_ok(_page, _phase):
        return {"fatal": [], "pass": True}

    async def agentq_start(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(runtime, "capture_stateful_controls", controls)
    monkeypatch.setattr(runtime, "assert_active_surface", surface_ok)
    monkeypatch.setattr(runtime, "_agentq_begin_phase", agentq_start)

    with pytest.raises(RuntimeError, match="HIP_FORM_CONTROLS_NOT_BOUND"):
        await runtime.execute_phase_state_graph(
            _FakePage(), _simple_graph(), phase="data_map", strict_live_execution=True
        )


def test_phase_modules_do_not_gate_authoritative_execution_on_legacy_controls():
    for module in (datamap, rules, tp):
        source = inspect.getsource(module)
        assert "if self.fill_dummy and controls:" not in source
        assert "strict_live_execution=True" in source
        assert "capture_stateful_controls" in source


def test_bizflow_uses_strict_section_execution_and_combined_stage_audit():
    source = inspect.getsource(bizflow)
    assert "strict_live_execution=True" in source
    assert "combined_execution_stage_audit" in source
    assert '"fields_filled_or_verified"' in source
    assert '"exact_execution_verified"' in source


def test_stateful_collector_has_playwright_page_fallback():
    source = inspect.getsource(runtime.capture_stateful_controls)
    assert "page_playwright_fallback" in source
    assert "await page.locator(selector).evaluate_all(script)" in source


def test_pre_judge_gate_requires_exact_execution_checkpoint():
    source = inspect.getsource(e2e.FullDummyFillE2EFlow.run)
    gate = source.index("pre_judge_exact_execution_gate")
    judge = source.index("judge_artifact_section", gate)
    assert gate < judge
    assert "HIP_PHASE_EXACT_EXECUTION_NOT_COMPLETED" in source
    assert '"judge_invoked": False' in source


def test_checkpoint_respects_executor_declared_blocking_failures(tmp_path: Path):
    phase_dir = tmp_path / "phase"
    artifact = phase_dir / "datamap_kb" / "datamap_target_branch_execution.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({
        "pass": True,
        "status": "pass",
        "strict_live_execution": True,
        "execution_stage_audit": {
            "fields_filled_or_verified": True,
            "exact_execution_verified": True,
            "authoritative_execution_verified": True,
        },
        "attempts": [
            {"field": "optional_child", "success": False, "optional": True},
            {"field": "map_identifier", "success": True, "exact_verified": True, "authoritative_execution": True, "executor": "playwright-mcp-fallback"},
        ],
        "failed_attempts": [],
    }), encoding="utf-8")
    checkpoint = e2e.phase_exact_completion_checkpoint("data_map", phase_dir)
    assert checkpoint["pass"] is True
    inspected = checkpoint["inspected"][-1]
    assert inspected["failed_attempt_count"] == 0
    assert inspected["strict_stage_ok"] is True


@pytest.mark.parametrize("message", [
    "HIP_FORM_CONTROLS_NOT_DISCOVERED: no controls",
    "HIP_FORM_CONTROLS_NOT_BOUND: no semantic root",
    "HIP_PHASE_EXACT_EXECUTION_NOT_COMPLETED: no transaction",
    "HIP_PHASE_EXACT_EXECUTION_NOT_VERIFIED: no exact proof",
])
def test_new_execution_failures_are_recoverable_from_earliest_surface(message: str):
    assert RuntimeSelfHealController.classify_failure(message) == "active_surface_lost"


def test_mission_trace_exposes_execution_stage_audit():
    source = inspect.getsource(mission_trace.MissionTraceLedger.refresh_phase_artifacts)
    assert "live_execution_stage_audit" in source
    assert "controls discovered" in source
    assert "controls bound" in source
    assert "fields filled/verified" in source
    assert "exact execution verified" in source


def test_v225_release_version():
    import hip_id_agent
    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")
