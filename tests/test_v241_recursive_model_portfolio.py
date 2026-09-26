from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.model_portfolio import (
    ON_PREM_MODEL_CATALOG,
    OnPremModelPortfolioRouter,
)
from hip_id_agent.recursive_self_improvement import RecursiveSelfImprovementEngine


class FakeAIA:
    def autogen_reply(self, system, task, model=None):
        if model == "gpt-oss-20b":
            return '{"candidate_index":1,"confidence":0.98}'
        if model == "gpt-oss-120b":
            return '{"candidate_index":0,"confidence":0.72}'
        return '{"candidate_index":0,"confidence":0.55}'


def portfolio_cfg():
    return SimpleNamespace(
        enabled=True,
        memory_subdir="model_portfolio",
        on_prem_only=True,
        parallel_models=3,
        max_parallel_models=4,
        shadow_reward_weight=0.35,
        # Pure downstream-evidence mode: these tests exercise the reward and
        # champion bookkeeping, not the V243R21 strongest-model preference.
        prefer_strongest_model=False,
        text_models=[
            "gpt-oss-120b",
            "gpt-oss-20b",
            "mistral-small-3-1-24b-instruct-2503",
            "gemini-2.5-flash",  # must be rejected: cloud model is not catalogued
        ],
        vision_models=["gemma-3-27b-it", "pixtral-12b-2409", "florence-2-large-ft", "gemini-2.5-flash"],
        embedding_models=["nomic-embed-vision-v1-5"],
    )


def test_v241_catalog_is_on_prem_only():
    assert "gpt-oss-120b" in ON_PREM_MODEL_CATALOG
    assert "gemma-3-27b-it" in ON_PREM_MODEL_CATALOG
    assert "nomic-embed-vision-v1-5" in ON_PREM_MODEL_CATALOG
    assert "gemini-2.5-flash" not in ON_PREM_MODEL_CATALOG
    assert "claude-opus-4-6" not in ON_PREM_MODEL_CATALOG


def test_v241_parallel_tournament_and_downstream_reward(tmp_path):
    router = OnPremModelPortfolioRouter(tmp_path / "portfolio", portfolio_cfg(), aia_config=SimpleNamespace(), client=FakeAIA())
    assert "gemini-2.5-flash" not in router.configured_models("text")
    assert "gemini-2.5-flash" not in router.configured_models("vision")

    trace = router.tournament_text(
        system="choose",
        task='{"goal":"open edit","candidates":["Open","Edit"]}',
        role="action_selection",
        require_keys=["candidate_index"],
        exploration=True,
    )
    assert trace["used"] is True
    assert len(trace["candidate_results"]) == 3
    assert trace["winner_model"] == "gpt-oss-20b"
    assert trace["parsed"]["candidate_index"] == 1
    assert all(row["model"] in ON_PREM_MODEL_CATALOG for row in trace["candidate_results"])

    manifest = router.record_downstream_outcome(trace=trace, success=True, reward=0.96)
    ranked = manifest["rankings"]["action_selection"]
    assert ranked[0]["model"] == "gpt-oss-20b"
    assert ranked[0]["stats"]["successes"] == 1
    raw = (tmp_path / "portfolio" / "model_trials.jsonl").read_text(encoding="utf-8")
    assert "open edit" not in raw.lower()  # raw task values are not persisted


class FakeReplay:
    def __init__(self):
        self.calls = 0

    def dream(self, reason=""):
        self.calls += 1
        return {"policy_updates": 1 if self.calls == 1 else 0}


class FakePortfolio:
    def __init__(self):
        self.calls = 0

    def dream(self, reason=""):
        self.calls += 1
        return {"champion_changes": {"planning": {"from": "a", "to": "b"}} if self.calls == 1 else {}}


def test_v241_recursive_improvement_is_bounded_and_does_not_rewrite_code(tmp_path):
    cfg = SimpleNamespace(enabled=True, max_recursive_cycles=4, minimum_improvement=0.01)
    engine = RecursiveSelfImprovementEngine(
        tmp_path / "rsi", cfg, replay_policy=FakeReplay(), model_portfolio=FakePortfolio(), skill_library=None
    )
    result = engine.improve(run_reward=0.93, success=True)
    assert result["status"] == "complete"
    assert 1 <= len(result["cycles"]) <= 4
    assert result["state"]["best_reward"] == 0.93
    assert result["state"]["code_self_modification"] is False
    assert result["live_page_still_authoritative"] is True


def test_v241_shipped_configs_and_version():
    import hip_id_agent
    from hip_id_agent.config import load_config

    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")
    for name in ("config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"):
        cfg = load_config(name)
        assert cfg.model_portfolio.enabled is True
        assert cfg.model_portfolio.on_prem_only is True
        assert cfg.recursive_self_improvement.enabled is True
        assert cfg.recursive_self_improvement.allow_source_code_self_modification is False
        assert "gpt-oss-120b" in cfg.model_portfolio.text_models
        assert "gemma-3-27b-it" in cfg.model_portfolio.vision_models
