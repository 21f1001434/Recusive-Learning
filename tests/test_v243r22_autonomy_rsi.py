"""V243R22: the agent heals itself while it progresses; learning happens every phase.

Live report (Document Type): rows 1-3 were filled, row 3's Usage was half done,
rows 4-5 were empty, and the phase asked for human review after attempt 1 --
"Automated: BLOCKED, deterministic: not proven".  The dashboard showed Model
Champion gpt-oss-20b, Recursive Improvement "Cycle 0" and 0 induced skills.

Root causes:

* every vetted field action (the executor had already bound the exact control)
  waited for a 4-model AutoWebGLM vote, up to 12 s each; the ~100 actions of a
  five-row Document Type overran the 20-minute phase wall budget;
* that budget was a hard ``asyncio.wait_for``: it cancelled the attempt in the
  middle of row 3, and the stall guard then handed the phase to a human although
  every field so far had been verified;
* the model champion came from tournaments won on each model's own confidence,
  where a model proposing the *same* action as the winner got only a 35% shadow
  credit, so gpt-oss-120b could never catch up;
* recursive self-improvement ran only once a whole mission finished, which a
  mission held at Document Type never did.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.autowebglm_bridge import AutoWebGLMRecoveryBridge
from hip_id_agent.config import AppConfig
from hip_id_agent.model_portfolio import CREDIT_RULE, SCHEMA, OnPremModelPortfolioRouter
from hip_id_agent.phase_progress import run_with_progress_budget
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


# ------------------------------------------------------------ progress budget
class _Healer:
    def __init__(self, left=6, seconds=0.3):
        self.left, self.seconds, self.granted = left, seconds, []

    def extend(self, units):
        if self.left <= 0:
            return {"granted": False}
        self.left -= 1
        self.granted.append(units)
        return {"granted": True, "seconds": self.seconds}


def _run_budget(work, marker, healer, budget=0.3, tmp_path=None):
    async def main():
        return await run_with_progress_budget(
            work(), phase="source_document_type", budget_seconds=budget, marker_provider=marker,
            extend=healer.extend, evidence_path=(tmp_path / "budget.json") if tmp_path else None,
        )
    return asyncio.run(main())


def test_a_slow_attempt_that_keeps_verifying_fields_is_not_cancelled_mid_form(tmp_path: Path):
    verified = {"n": 0}

    async def form():  # five rows, each slower than the whole budget allows at once
        for _ in range(5):
            await asyncio.sleep(0.2)
            verified["n"] += 1
        return "all rows filled"

    async def marker():
        return {"progress_units": verified["n"]}

    healer = _Healer()
    assert _run_budget(form, marker, healer, budget=0.3, tmp_path=tmp_path) == "all rows filled"
    assert healer.granted and verified["n"] == 5
    assert json.loads((tmp_path / "budget.json").read_text())["decision"] == "extended_for_verified_progress"


def test_an_attempt_without_new_verified_fields_is_still_stopped(tmp_path: Path):
    async def stuck():
        await asyncio.sleep(5)

    async def marker():
        return {"progress_units": 7}

    with pytest.raises(asyncio.TimeoutError):
        _run_budget(stuck, marker, _Healer(), budget=0.2, tmp_path=tmp_path)
    assert json.loads((tmp_path / "budget.json").read_text())["decision"] == "stopped_no_new_progress"


def test_progress_extensions_are_bounded(tmp_path: Path):
    ticks = {"n": 0}

    async def endless():  # always "progressing", never done
        while True:
            await asyncio.sleep(0.05)
            ticks["n"] += 1

    async def marker():
        return {"progress_units": ticks["n"]}

    healer = _Healer(left=2, seconds=0.2)
    with pytest.raises(asyncio.TimeoutError):
        _run_budget(endless, marker, healer, budget=0.2, tmp_path=tmp_path)
    assert len(healer.granted) == 2
    assert json.loads((tmp_path / "budget.json").read_text())["decision"] == "stopped_extensions_exhausted"


def test_the_healer_grants_bounded_progress_time_that_counts_in_its_wall_budget(tmp_path: Path):
    cfg = AppConfig()
    cfg.runtime_self_heal.max_progress_extensions = 2
    healer = RuntimeSelfHealController(config=cfg, root_dir=tmp_path, browser=SimpleNamespace())
    base = healer.wall_budget_seconds("source_document_type")
    first = healer.extend_for_progress("source_document_type", progress_units=9)
    assert first["granted"] and healer.wall_budget_seconds("source_document_type") == base + cfg.runtime_self_heal.progress_extension_seconds
    healer.extend_for_progress("source_document_type", progress_units=3)
    assert healer.extend_for_progress("source_document_type", progress_units=1)["granted"] is False
    healer.reset_phase_ladder("source_document_type")  # a human Resume starts afresh
    assert healer.wall_budget_seconds("source_document_type") == base


# ------------------------------------------------------ one model per vetted action
class _Portfolio:
    def __init__(self):
        self.calls = []

    def tournament_text(self, **kwargs):
        self.calls.append(kwargs)
        return {"used": True, "parsed": {"action": "click(id='5')", "confidence": 0.9}, "winner_model": "gpt-oss-120b"}


def _bridge():
    cfg = AppConfig().autowebglm
    bridge = AutoWebGLMRecoveryBridge(cfg, aia_config=AppConfig().aia)
    bridge.model_portfolio = _Portfolio()
    bridge.aia = object()
    bridge.use_existing_dell_aia = True

    async def observation(**_):
        return {"available": True, "simplified_html": "<button id=5>Open</button>"}

    bridge.build_observation = observation
    return bridge


class _Page:
    def locator(self, _selector):
        return self

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def get_attribute(self, _name):
        return "5"


def test_a_vetted_field_action_asks_one_strong_model_and_every_tenth_adds_a_challenger():
    bridge = _bridge()

    async def decide():
        for _ in range(10):
            await bridge.primary_decide(page=_Page(), task="fill", action="click", selector="#x", label="Derived From",
                                        learning=True, complex_task=True, vetted=True)
    asyncio.run(decide())
    sizes = [c.get("parallel_models") for c in bridge.model_portfolio.calls]
    assert sizes[:9] == [1] * 9 and sizes[9] == 2
    assert not any(c.get("force_multi_model") for c in bridge.model_portfolio.calls)


# ------------------------------------------------------------ fair model credit
class _FakeAIA:
    def autogen_reply(self, system, task, model=None):
        # Same decision, different self-reported confidence.
        return json.dumps({"action": "click(id='5')", "confidence": 0.99 if model == "gpt-oss-20b" else 0.70})


def _router(tmp_path: Path, **overrides):
    base = AppConfig().model_portfolio.model_dump()
    base.update(text_models=["gpt-oss-120b", "gpt-oss-20b"], availability_probe_enabled=False, record_usage_ledger=False)
    base.update(overrides)
    return OnPremModelPortfolioRouter(tmp_path / "p", SimpleNamespace(**base), aia_config=SimpleNamespace(model="gpt-oss-120b"), client=_FakeAIA())


def test_models_that_made_the_same_decision_share_its_real_outcome(tmp_path: Path):
    router = _router(tmp_path)
    trace = router.tournament_text(system="s", task="t", role="action_selection", require_keys=["action"], parallel_models=2)
    router.record_downstream_outcome(trace=trace, success=True, reward=1.0)
    stats = {m: router._model_stats(m, "action_selection") for m in ("gpt-oss-120b", "gpt-oss-20b")}
    assert stats["gpt-oss-120b"]["successes"] == stats["gpt-oss-20b"]["successes"] == 1
    assert stats["gpt-oss-120b"]["reward_sum"] == stats["gpt-oss-20b"]["reward_sum"] == 1.0


def test_champion_evidence_scored_under_the_old_rule_is_re_earned(tmp_path: Path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "model_portfolio.json").write_text(json.dumps({
        "schema_version": SCHEMA, "models": {"gpt-oss-20b": {"roles": {"planning": {"trials": 179, "successes": 170}}}},
        "role_champions": {"planning": "gpt-oss-20b"}, "task_champions": {}, "availability": {"gpt-oss-120b": {"available": True}}, "cycle": 179,
    }), encoding="utf-8")
    router = OnPremModelPortfolioRouter(root, AppConfig().model_portfolio, aia_config=SimpleNamespace(model="gpt-oss-120b"), client=_FakeAIA())
    assert router.state["credit_rule"] == CREDIT_RULE
    assert router.state["role_champions"] == {} and router.state["models"] == {}
    assert router.state["previous_role_champions"] == {"planning": "gpt-oss-20b"}
    manifest = router.manifest()
    assert manifest["default_text_model"] == "gpt-oss-120b"
    os.environ.pop("HIP_MODEL_ROUTER_SELECTED_TEXT", None)


# ------------------------------------------------ learning every phase attempt
def test_every_phase_attempt_runs_a_recursive_improvement_cycle(tmp_path: Path):
    from hip_id_agent.mission_learning import close_mission_learning_loop
    from hip_id_agent.replay_policy import replay_policy_engine_from_config

    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    replay = replay_policy_engine_from_config(cfg)
    first = close_mission_learning_loop(cfg, replay_policy=replay, reward=0.6, success=False, reason="phase_attempt:source_document_type:1:incomplete")
    second = close_mission_learning_loop(cfg, replay_policy=replay, reward=1.0, success=True, reason="phase_attempt:source_document_type:2:pass")
    assert first["cycle_count"] >= 1 and second["state"]["cycle"] > first["state"]["cycle"]
    assert second["state"]["best_reward"] == 1.0  # the dashboard's "best" now moves during a mission
    os.environ.pop("HIP_MODEL_ROUTER_SELECTED_TEXT", None)


def test_the_mission_loop_earns_time_for_progress_and_learns_after_each_attempt():
    import inspect

    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow

    source = inspect.getsource(FullDummyFillE2EFlow.run)
    assert "run_with_progress_budget(" in source and "timeout=remaining_phase_seconds" not in source
    extend = source.index("extend_for_progress(")
    hold = source.index("HIP_PHASE_WALLCLOCK_STALL_GUARD")
    assert extend < hold  # progress is credited before a human is ever asked
    finish = source.index("async def _learning_finish(")
    assert source.index("close_mission_learning_loop(", finish) < source.index("async def _execute_phase_once(")
