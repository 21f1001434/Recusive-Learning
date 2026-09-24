"""V243R13: the learning / recursive-self-improvement loop works end to end.

Each test drives the real engines through their persisted files, the way live
runs do: learn -> write to disk -> reload in a new instance -> exploit.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.autonomous_form_runtime import autonomous_target_execution, execute_autonomous_phase_goal
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from hip_id_agent.continuous_learning import continuous_learning_from_config
from hip_id_agent.flow_pattern_memory import FlowPatternMemory
from hip_id_agent.mission_learning import close_mission_learning_loop
from hip_id_agent.model_portfolio import model_portfolio_from_config
from hip_id_agent.portal_brain import PortalBrain
from hip_id_agent.recursive_self_improvement import recursive_improvement_from_config
from hip_id_agent.replay_policy import replay_policy_engine_from_config
from hip_id_agent.safe_io import safe_write_json
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph

URL = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"
TASK = "Learn and complete HIP Data Map from input"


def _cfg(tmp_path: Path) -> AppConfig:
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.reporting.runs_dir = str(tmp_path / "runs")
    return cfg


def _event(i: int, typ: str, label: str, ok: bool = True) -> dict:
    return {
        "action_id": f"a{i}", "type": typ, "target": label, "page_url_before": URL,
        "page_url_after": URL + ("?add" if typ == "click" else ""), "success": ok, "stage": "data_map",
        "backend": "pyautogui-mcp",
        "execution_provenance": {
            "semantic_control_id": f"SC-{label.upper().replace(' ', '-')}", "semantic_effect_pass": ok,
            "exact_value_commit_verified": ok and typ == "fill", "actual_executor": "pyautogui-mcp",
        },
    }


class _Browser:
    action_events = [
        _event(1, "navigate", "Data Maps"), _event(2, "click", "Add"), _event(3, "fill", "Map Identifier"),
        _event(4, "fill", "Map Name"), _event(5, "click", "Wrong button", ok=False),
    ]


def _learn(cfg: AppConfig, run_id: str) -> dict:
    graph = HIPCapabilityGraph(PortalBrain.from_config(cfg).root)
    engine = continuous_learning_from_config(cfg, capability_graph=graph, replay_policy=replay_policy_engine_from_config(cfg))
    return engine.learn_phase(
        browser=_Browser(), phase="data_map", run_id=run_id, task=TASK, exact_verified=True, judge_pass=True,
        human_pass=True, input_root="data_map", page_families=["data_map"],
    )


def test_continuous_learning_promotes_and_next_run_exploits(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert _learn(cfg, "RUN-1")["status"] == "trusted_promoted"
    assert _learn(cfg, "RUN-2")["status"] == "trusted_promoted"

    graph = HIPCapabilityGraph(PortalBrain.from_config(cfg).root)
    caps = graph.query_capabilities(page_family="data_maps")
    trusted = [c for c in caps if int(c.get("success_observations") or 0) > 0]
    assert {"SC-ADD", "SC-MAP-IDENTIFIER"} <= {c["label"] for c in trusted}
    assert "SC-WRONG-BUTTON" not in {c["label"] for c in trusted}  # failures stay negative evidence

    replay = replay_policy_engine_from_config(cfg)
    replay.dream(reason="test")
    decision = replay.decide(task=TASK, actions=["navigate", "click", "fill", "fill"], target_area="data_maps",
                             input_root="data_map", page_families=["data_maps"])
    assert decision.get("mode") == "exploitation", decision


def test_mission_rsi_cycle_improves_models_and_persists(tmp_path: Path):
    cfg = _cfg(tmp_path)
    portfolio = model_portfolio_from_config(cfg)
    models = portfolio.configured_models("text")[:3]
    trace = {
        "role": "judge", "task_key": "judge:hip phase", "winner_model": models[0],
        "candidate_results": [{"model": m, "ok": True, "quality": 0.9 if i == 0 else 0.4, "latency_ms": 900, "proposal": {"p": i}}
                              for i, m in enumerate(models)],
    }
    for _ in range(4):  # what the judge panel records during a mission
        portfolio.record_downstream_outcome(trace=trace, success=True, reward=1.0)

    replay = replay_policy_engine_from_config(cfg)
    replay.record_episode(task="fill all HIP phases from input json", actions=["fill", "verify"], target_area="all_phases",
                          steps=[{"type": "fill_from_input", "pass": True}], success=True, run_id="R1")
    first = close_mission_learning_loop(cfg, replay_policy=replay, reward=0.9, success=True)
    assert first["status"] == "complete" and first["cycle_count"] >= 1
    assert first["role_champions"].get("judge") == models[0]
    assert first["state"]["best_reward"] == pytest.approx(0.9)

    second = close_mission_learning_loop(cfg, replay_policy=replay_policy_engine_from_config(cfg), reward=0.95, success=True)
    assert second["state"]["cycle"] > first["state"]["cycle"]
    assert second["state"]["best_reward"] == pytest.approx(0.95)
    reloaded = recursive_improvement_from_config(
        cfg, replay_policy=replay_policy_engine_from_config(cfg), model_portfolio=model_portfolio_from_config(cfg)
    ).manifest()
    assert reloaded["cycle"] == second["state"]["cycle"] and reloaded["code_self_modification"] is False


def test_mission_runs_rsi_after_every_mission():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert "close_mission_learning_loop(" in source
    assert "recursive_self_improvement.json" in source


def _chromium_path() -> str | None:
    for candidate in (os.environ.get("HIP_TEST_CHROMIUM"), "/opt/pw-browsers/chromium", "/usr/bin/chromium"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def test_flow_pattern_memory_learned_on_run_one_drives_run_two(tmp_path: Path):
    html = (Path(__file__).parent / "fixtures" / "create_map_duplicate_identifier.html").read_text(encoding="utf-8")
    html = html.replace("__VERSION_ATTR__", 'placeholder="1"')
    payload = {"objects": {"data_map": {
        "map_identifier": "DELLCoXMLASNXX08C_U-HAUL", "map_identifier_version": "1", "status": "Enable",
        "map_name": "DELLCoXMLASNXX08C", "map_class": "Transform_DELLCoXMLASNXX08C",
    }}}
    memory = FlowPatternMemory.from_config(tmp_path / "flow_patterns", AppConfig())

    class _Session:
        flow_pattern_memory = memory

    async def run(name: str) -> dict:
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=_chromium_path())
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.set_content(html)
            page._hip_browser_session = _Session()
            try:
                result = await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(payload, "data_map"), phase="data_map",
                    input_data=payload, config=None, output_dir=tmp_path / name, max_cycles=2,
                )
                return autonomous_target_execution(result)
            finally:
                await browser.close()

    first = asyncio.run(run("run1"))
    assert first["pass"] is True
    assert first["execution_profile"]["validated_flow_pattern_memory"] is False
    phase_dir = tmp_path / "phase_run1"
    safe_write_json(phase_dir / "datamap_target_branch_execution.json", first)
    promotion = memory.promote_from_phase_dir(  # exactly what the mission does after a judged phase
        phase="data_map", phase_dir=phase_dir, input_payload=payload, run_id="RUN-1",
        judge_pass=True, graph_builder=compile_phase_state_graph,
    )
    assert promotion.get("trust") == "validated", promotion

    second = asyncio.run(run("run2"))
    assert second["pass"] is True
    assert second["execution_profile"]["validated_flow_pattern_memory"] is True
    assert second["flow_pattern_memory_match"]["status"] == "validated_match"
