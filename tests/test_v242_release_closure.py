from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.model_portfolio import OnPremModelPortfolioRouter
from hip_id_agent.recursive_self_improvement import RecursiveSelfImprovementEngine


class FakeAIA:
    def autogen_reply(self, system, task, model=None):
        if model == "gpt-oss-20b":
            return '{"candidate_index":1,"confidence":0.99}'
        return '{"candidate_index":0,"confidence":0.70}'


def cfg(**overrides):
    base = dict(
        enabled=True,
        memory_subdir="model_portfolio",
        on_prem_only=True,
        parallel_models=3,
        max_parallel_models=4,
        shadow_reward_weight=0.35,
        benchmark_low_confidence_only=True,
        fast_exploitation_single_model=True,
        min_champion_trials=3,
        min_champion_score=0.78,
        # Pure downstream-evidence mode (see test_v243r21_model_preference.py for
        # the default strongest-model preference).
        prefer_strongest_model=False,
        text_models=[
            "gpt-oss-120b",
            "gpt-oss-20b",
            "mistral-small-3-1-24b-instruct-2503",
            "llama-3-3-70b-instruct",
            "gemma-3-27b-it",
            "llama-3-2-3b-instruct",
        ],
        vision_models=["gemma-3-27b-it", "pixtral-12b-2409", "florence-2-large-ft"],
        embedding_models=["nomic-embed-vision-v1-5"],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_v242_role_capability_filtering(tmp_path):
    router = OnPremModelPortfolioRouter(tmp_path / "p", cfg(), aia_config=SimpleNamespace(), client=FakeAIA())
    planning = router.configured_models("text", role="planning")
    action = router.configured_models("text", role="action_selection")
    judge = router.configured_models("text", role="judge")
    assert "llama-3-2-3b-instruct" not in planning
    assert "llama-3-2-3b-instruct" in action
    assert "llama-3-2-3b-instruct" not in judge
    assert "gpt-oss-120b" in planning and "gpt-oss-120b" in judge


def test_v242_proposal_winner_is_not_durable_champion(tmp_path):
    router = OnPremModelPortfolioRouter(tmp_path / "p", cfg(), aia_config=SimpleNamespace(), client=FakeAIA())
    trace = router.tournament_text(
        system="choose", task='{"goal":"edit"}', role="action_selection",
        require_keys=["candidate_index"], exploration=True,
    )
    assert trace["winner_model"] == "gpt-oss-20b"
    assert router.state.get("role_champions") == {}
    assert router.state.get("task_champions") == {}


def test_v242_task_champion_requires_downstream_evidence(tmp_path):
    router = OnPremModelPortfolioRouter(tmp_path / "p", cfg(), aia_config=SimpleNamespace(), client=FakeAIA())
    task = '{"goal":"edit"}'
    for i in range(3):
        trace = router.tournament_text(
            system="choose", task=task, role="action_selection",
            require_keys=["candidate_index"], exploration=(i == 0),
        )
        assert trace["winner_model"] == "gpt-oss-20b"
        router.record_downstream_outcome(trace=trace, success=True, reward=0.98)
        if i < 2:
            assert trace["task_key"] not in router.state.get("task_champions", {})
    assert router.state["task_champions"][trace["task_key"]] == "gpt-oss-20b"
    assert router.state["role_champions"]["action_selection"] == "gpt-oss-20b"
    ranked = router.rank("action_selection", task=task)
    assert ranked[0]["model"] == "gpt-oss-20b"
    assert ranked[0]["task_trials"] == 3


def test_v242_v1_state_migration_discards_unproven_champions(tmp_path):
    root = tmp_path / "p"; root.mkdir()
    (root / "model_portfolio.json").write_text(
        '{"schema_version":"hip.onprem-model-portfolio.v1","models":{"gpt-oss-20b":{"roles":{},"total_trials":2}},"role_champions":{"planning":"gpt-oss-20b"},"task_champions":{"x":"gpt-oss-20b"},"cycle":4}',
        encoding="utf-8",
    )
    router = OnPremModelPortfolioRouter(root, cfg(), aia_config=SimpleNamespace(), client=FakeAIA())
    assert router.state["schema_version"] == "hip.onprem-model-portfolio.v2"
    assert router.state["role_champions"] == {}
    assert router.state["task_champions"] == {}
    assert router.state["models"]["gpt-oss-20b"]["total_trials"] == 2


class FakeDream:
    def __init__(self): self.calls = 0
    def dream(self, reason=""):
        self.calls += 1
        return {"policy_updates": 0, "champion_changes": {}}


class FakeSkillLibrary:
    def __init__(self): self.saved = 0
    def save(self): self.saved += 1
    def manifest(self): return {"skill_count": 2}
    def record_outcome(self, *a, **k): raise AssertionError("must not double-count")


def test_v242_recursive_flags_are_honored_without_skill_double_count(tmp_path):
    replay = FakeDream(); portfolio = FakeDream(); skills = FakeSkillLibrary()
    rcfg = SimpleNamespace(
        enabled=True, max_recursive_cycles=2, minimum_improvement=0.01,
        update_replay_policy=False, update_model_portfolio=False,
        update_skill_confidence=True,
    )
    engine = RecursiveSelfImprovementEngine(tmp_path / "r", rcfg, replay_policy=replay, model_portfolio=portfolio, skill_library=skills)
    out = engine.improve(
        run_reward=0.9, success=True,
        skill_feedback={"skill_id":"skill-x", "outcome_already_recorded":True},
    )
    assert replay.calls == 0
    assert portfolio.calls == 0
    assert skills.saved == 1
    assert out["skill_review"]["performed"] is False
    assert out["state"]["last_update_flags"] == {
        "replay_policy": False, "model_portfolio": False, "skill_confidence": True,
    }


def test_v242_version_and_packaged_import_contract():
    import hip_id_agent
    from hip_id_agent.mlflow_async import AsyncMLflowTracker
    from hip_id_agent.model_portfolio import OnPremModelPortfolioRouter
    from hip_id_agent.recursive_self_improvement import RecursiveSelfImprovementEngine
    from hip_id_agent.replay_policy import ReplayPolicyEngine
    from hip_id_agent.skill_induction import SkillInductionEngine
    from hip_id_agent.universal_portal_operator import UniversalPortalTaskPlanner

    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")
    assert all(x is not None for x in [
        AsyncMLflowTracker, OnPremModelPortfolioRouter, RecursiveSelfImprovementEngine,
        ReplayPolicyEngine, SkillInductionEngine, UniversalPortalTaskPlanner,
    ])
