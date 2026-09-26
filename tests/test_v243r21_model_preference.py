"""V243R21: the strongest available model is used; gpt-oss-20b no longer takes over.

Live report: runs used gpt-oss-20b although gpt-oss-120b (config ``aia.model``)
was available.  Three things let a weaker model take over:

* a tournament winner was chosen by the model's own ``confidence`` field, which
  is not comparable across models (gpt-oss-20b answers 0.98 where gpt-oss-120b
  answers 0.72);
* learning tournaments explored the *least-tried* models first, so the model
  with the most history -- gpt-oss-120b -- could be left out entirely;
* the resulting champion was exported as ``HIP_MODEL_ROUTER_SELECTED_TEXT``,
  which overrides ``aia.model`` for every default model call.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from hip_id_agent.aia_client import AIAClient
from hip_id_agent.config import AIAConfig, AppConfig
from hip_id_agent.model_portfolio import MODEL_CAPABILITY, OnPremModelPortfolioRouter

TEXT_MODELS = [
    "gpt-oss-120b", "gpt-oss-20b", "mistral-small-3-1-24b-instruct-2503",
    "llama-3-3-70b-instruct", "gemma-3-27b-it", "llama-3-2-3b-instruct",
]


class FakeAIA:
    """gpt-oss-20b is fast and overconfident; gpt-oss-120b is valid but modest."""

    def __init__(self, broken=()):
        self.broken = set(broken)

    def autogen_reply(self, system, task, model=None):
        if model in self.broken:
            return "I cannot answer in JSON right now."
        if model == "gpt-oss-20b":
            return '{"candidate_index":1,"confidence":0.98}'
        if model == "gpt-oss-120b":
            return '{"candidate_index":0,"confidence":0.72}'
        return '{"candidate_index":0,"confidence":0.55}'


def _cfg(**overrides):
    # The shipped defaults (ModelPortfolioConfig), with the models above.
    base = AppConfig().model_portfolio.model_dump()
    base.update(text_models=TEXT_MODELS, availability_probe_enabled=False, record_usage_ledger=False)
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clean_env():
    saved = os.environ.pop("HIP_MODEL_ROUTER_SELECTED_TEXT", None)
    yield
    os.environ.pop("HIP_MODEL_ROUTER_SELECTED_TEXT", None)
    if saved is not None:
        os.environ["HIP_MODEL_ROUTER_SELECTED_TEXT"] = saved


def _router(tmp_path, fake=None, **cfg):
    return OnPremModelPortfolioRouter(
        tmp_path / "portfolio", _cfg(**cfg), aia_config=SimpleNamespace(model="gpt-oss-120b"), client=fake or FakeAIA(),
    )


def test_the_strongest_model_wins_over_a_more_self_confident_weaker_one(tmp_path):
    trace = _router(tmp_path).tournament_text(
        system="choose", task='{"goal":"open edit"}', role="action_selection", require_keys=["candidate_index"],
    )
    assert "gpt-oss-20b" in trace["candidate_models"]
    assert trace["winner_model"] == "gpt-oss-120b"


def test_a_weaker_model_still_wins_when_the_strongest_gives_no_usable_answer(tmp_path):
    trace = _router(tmp_path, FakeAIA(broken={"gpt-oss-120b"})).tournament_text(
        system="choose", task='{"goal":"open edit"}', role="action_selection", require_keys=["candidate_index"],
    )
    assert trace["winner_model"] == "gpt-oss-20b"


def test_learning_exploration_never_leaves_out_the_strongest_model(tmp_path):
    router = _router(tmp_path)
    # gpt-oss-120b has by far the most history: exploration used to skip it.
    router._model_stats("gpt-oss-120b", "action_selection").update({"trials": 40, "successes": 30, "reward_sum": 30.0})
    picked = router.select_candidates("action_selection", task="fill Derived From", learning=True, exploration=True)
    assert picked[0] == "gpt-oss-120b"
    assert len(picked) >= 2  # challengers still take part while learning


def test_a_weaker_champion_never_replaces_the_configured_model_for_default_calls(tmp_path):
    router = _router(tmp_path)
    for model, score in (("gpt-oss-20b", 0.99), ("gpt-oss-120b", 0.70)):
        router._model_stats(model, "planning").update({"trials": 10, "successes": 10 if score > 0.9 else 6, "reward_sum": 10 * score, "quality_sum": 10 * score})
    router.dream(reason="test")
    assert os.environ.get("HIP_MODEL_ROUTER_SELECTED_TEXT") in (None, "", "gpt-oss-120b")
    client = AIAClient(AIAConfig(model="gpt-oss-120b"))
    assert client._model() == "gpt-oss-120b"
    assert router.manifest()["default_text_model"] == "gpt-oss-120b"


def test_the_configured_model_yields_only_when_it_is_proven_down(tmp_path):
    import time

    router = _router(tmp_path)
    router._model_stats("gpt-oss-20b", "planning").update({"trials": 10, "successes": 10, "reward_sum": 9.9, "quality_sum": 9.9})
    router.state["availability"] = {"gpt-oss-120b": {"available": False, "checked_epoch": time.time()}}
    router.dream(reason="test")
    assert os.environ.get("HIP_MODEL_ROUTER_SELECTED_TEXT") == "gpt-oss-20b"


def test_capability_tiers_rank_the_on_prem_catalog():
    assert MODEL_CAPABILITY["gpt-oss-120b"] > MODEL_CAPABILITY["llama-3-3-70b-instruct"] > MODEL_CAPABILITY["gpt-oss-20b"]
    assert MODEL_CAPABILITY["gpt-oss-20b"] > MODEL_CAPABILITY["llama-3-2-3b-instruct"]
    assert AppConfig().model_portfolio.prefer_strongest_model is True
