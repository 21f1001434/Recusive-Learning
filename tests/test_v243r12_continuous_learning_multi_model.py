from pathlib import Path
from types import SimpleNamespace
import asyncio

from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from hip_id_agent.continuous_learning import ContinuousPortalLearningEngine
from hip_id_agent.model_portfolio import OnPremModelPortfolioRouter
from hip_id_agent.models import ActionEvent
from hip_id_agent.autowebglm_bridge import AutoWebGLMRecoveryBridge


class FakeAIA:
    def __init__(self, unavailable=()):
        self.calls = []
        self.unavailable = set(unavailable)

    def autogen_reply(self, system, task, model=None):
        self.calls.append(model)
        if model in self.unavailable:
            raise RuntimeError(f"{model} unavailable")
        if "availability probe" in system.lower():
            return '{"ok":true,"model":"%s"}' % model
        idx = 1 if model == "gpt-oss-20b" else 0
        return '{"candidate_index":%d,"confidence":0.91,"reason":"ok"}' % idx


def _portfolio_cfg(**overrides):
    base = dict(
        enabled=True,
        memory_subdir="model_portfolio",
        on_prem_only=True,
        parallel_models=3,
        max_parallel_models=6,
        learning_parallel_models=4,
        complex_task_parallel_models=4,
        min_distinct_models_during_learning=2,
        force_multi_model_during_learning=True,
        force_multi_model_for_complex_tasks=True,
        fast_exploitation_single_model=True,
        disable_single_model_collapse_during_learning=True,
        shadow_reward_weight=0.35,
        availability_probe_enabled=True,
        availability_probe_on_task_start=False,
        availability_probe_ttl_seconds=1800,
        availability_probe_parallelism=4,
        record_usage_ledger=True,
        min_champion_trials=3,
        min_champion_score=0.78,
        text_models=[
            "gpt-oss-120b",
            "gpt-oss-20b",
            "mistral-small-3-1-24b-instruct-2503",
            "llama-3-3-70b-instruct",
        ],
        vision_models=["gemma-3-27b-it", "pixtral-12b-2409", "florence-2-large-ft"],
        embedding_models=["nomic-embed-vision-v1-5"],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_r12_learning_never_collapses_to_single_champion(tmp_path):
    router = OnPremModelPortfolioRouter(tmp_path / "portfolio", _portfolio_cfg(), aia_config=SimpleNamespace(enabled=True), client=FakeAIA())
    # Seed an apparently dominant champion. Learning must still use challengers.
    stats = router._model_stats("gpt-oss-120b", "planning")
    stats.update({"trials": 20, "successes": 20, "reward_sum": 19.5, "quality_sum": 19.0, "latency_ms_sum": 1000})
    router.state["role_champions"]["planning"] = "gpt-oss-120b"
    candidates = router.select_candidates("planning", task="learn Data Map portal", learning=True)
    assert len(candidates) >= 2
    assert "gpt-oss-120b" in candidates


def test_r12_availability_probe_and_learning_tournament_use_multiple_real_models(tmp_path):
    fake = FakeAIA(unavailable={"mistral-small-3-1-24b-instruct-2503"})
    router = OnPremModelPortfolioRouter(tmp_path / "portfolio", _portfolio_cfg(), aia_config=SimpleNamespace(enabled=True), client=fake)
    probe = router.probe_text_models(force=True)
    available = router.available_models("text", role="planning")
    assert probe["status"] in {"probed", "partial"}
    assert "gpt-oss-120b" in available
    assert "gpt-oss-20b" in available
    assert "mistral-small-3-1-24b-instruct-2503" not in available

    trace = router.tournament_text(
        system="Choose a safe semantic portal action and return JSON.",
        task='{"goal":"learn Document Type edit flow"}',
        role="planning",
        require_keys=["candidate_index"],
        learning=True,
        force_multi_model=True,
    )
    assert trace["used"] is True
    assert trace["distinct_models_used"] >= 2
    assert len(set(trace["candidate_models"])) >= 2
    assert (tmp_path / "portfolio" / "model_usage.jsonl").is_file()
    manifest = router.manifest()
    assert len(manifest["recent_distinct_models"]) >= 2


def test_r12_fill_and_navigation_become_value_free_persistent_portal_experience(tmp_path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.replay_policy.memory_subdir = "replay"
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / "capability_graph")
    engine = ContinuousPortalLearningEngine(cfg, capability_graph=graph)

    browser = SimpleNamespace(action_events=[
        ActionEvent(
            action_id="act-1", type="navigate", target="Data Maps", page_url_before="https://hip/home", page_url_after="https://hip/datamaps", success=True, stage="data_map",
            execution_provenance={"semantic_control_id": "nav:data_maps", "actual_executor": "playwright", "semantic_effect_pass": True},
        ),
        ActionEvent(
            action_id="act-2", type="click", target="+ Add", page_url_before="https://hip/datamaps", page_url_after="https://hip/datamaps", success=True, stage="data_map",
            execution_provenance={"semantic_control_id": "action:add", "actual_executor": "playwright", "semantic_effect_pass": True},
        ),
        ActionEvent(
            action_id="act-3", type="fill", target="Map Name", value_redacted="CUSTOMER_MAP_VALUE", value_hash="secret-hash", page_url_before="https://hip/datamaps", page_url_after="https://hip/datamaps", success=True, stage="data_map",
            execution_provenance={"semantic_control_id": "field:map_name", "actual_executor": "playwright", "semantic_effect_pass": True},
        ),
    ])
    result = engine.learn_phase(
        browser=browser,
        phase="data_map",
        run_id="run-r12",
        task="fill the Data Map",
        exact_verified=True,
        judge_pass=True,
        human_pass=True,
        input_root="data_map",
        page_families=["data_maps"],
    )
    assert result["trusted_promotion"] is True
    assert result["experience_count"] == 3
    raw = (Path(cfg.reporting.memory_dir) / "continuous_learning" / "interaction_experiences.jsonl").read_text(encoding="utf-8")
    assert "CUSTOMER_MAP_VALUE" not in raw
    assert "secret-hash" not in raw
    assert "field:map_name" in raw
    cap_text = graph.path.read_text(encoding="utf-8")
    assert "field:map_name" in cap_text
    assert "CUSTOMER_MAP_VALUE" not in cap_text


def test_r12_autowebglm_bridge_routes_complex_learning_through_portfolio(tmp_path):
    cfg = AppConfig()
    cfg.aia.enabled = True
    bridge = AutoWebGLMRecoveryBridge(cfg.autowebglm, aia_config=cfg.aia, app_config=cfg)

    class FakePortfolio:
        def __init__(self):
            self.kwargs = None
        def tournament_text(self, **kwargs):
            self.kwargs = kwargs
            return {
                "used": True,
                "winner_model": "gpt-oss-20b",
                "parsed": {"action": "click(2)", "confidence": 0.95, "reason": "edit matches intent"},
                "candidate_models": ["gpt-oss-120b", "gpt-oss-20b"],
            }

    fake = FakePortfolio()
    bridge.model_portfolio = fake
    observation = {
        "available": True,
        "task": "learn and edit a complex HIP Rule",
        "simplified_html": '<html><e id="1" role="button" label="Open"></e><e id="2" role="button" label="Edit"></e></html>',
        "action_space": ["click(id)"],
        "expected_intent": {"action": "click", "selector_present": True, "semantic_label": "Edit"},
    }
    decision = asyncio.run(bridge.propose(
        page=None,
        task="learn and edit a complex HIP Rule",
        observation=observation,
        expected_intent={"action": "click", "selector_present": True, "semantic_label": "Edit"},
        learning=True,
        complex_task=True,
    ))
    assert decision["parsed"]["action"] == "click"
    assert fake.kwargs["learning"] is True
    assert fake.kwargs["complex_task"] is True
    assert fake.kwargs["force_multi_model"] is True
    assert decision["model_routing"]["winner_model"] == "gpt-oss-20b"


def test_r12_defaults_enable_continuous_learning_and_multi_model_learning():
    cfg = AppConfig()
    assert cfg.continuous_learning.enabled is True
    assert cfg.model_portfolio.force_multi_model_during_learning is True
    assert cfg.model_portfolio.force_multi_model_for_complex_tasks is True
    assert cfg.model_portfolio.learning_parallel_models >= 2
    assert cfg.model_portfolio.min_distinct_models_during_learning >= 2
    assert cfg.human_in_the_loop.multi_model_judge_force_during_learning is True


def test_r12_strict_promotion_requires_explicit_human_pass(tmp_path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / "capability_graph")
    engine = ContinuousPortalLearningEngine(cfg, capability_graph=graph)
    browser = SimpleNamespace(action_events=[
        ActionEvent(
            action_id="act-human-gate", type="navigate", target="Rules",
            page_url_before="https://hip/home", page_url_after="https://hip/rules",
            success=True, stage="rules",
            execution_provenance={"semantic_control_id": "nav:rules", "semantic_effect_pass": True},
        )
    ])
    result = engine.learn_phase(
        browser=browser, phase="rules", run_id="run-human-gate", task="learn rules",
        exact_verified=True, judge_pass=True,
        # human_pass intentionally omitted: UNKNOWN must not equal PASS.
    )
    assert result["promotion_requires_human"] is True
    assert result["human_review_supplied"] is False
    assert result["trusted_promotion"] is False
    caps = list(graph.data["capabilities"].values())
    assert caps and all(not bool(x.get("verified")) for x in caps)


def test_r12_failed_action_is_negative_evidence_not_trusted_route(tmp_path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / "capability_graph")
    engine = ContinuousPortalLearningEngine(cfg, capability_graph=graph)
    browser = SimpleNamespace(action_events=[
        ActionEvent(
            action_id="ok-1", type="navigate", target="Rules", success=True, stage="rules",
            page_url_before="https://hip/home", page_url_after="https://hip/rules",
            execution_provenance={"semantic_control_id": "nav:rules", "semantic_effect_pass": True},
        ),
        ActionEvent(
            action_id="bad-1", type="click", target="Wrong +", success=False, stage="rules",
            page_url_before="https://hip/rules", page_url_after="https://hip/rules", error="wrong control",
            execution_provenance={"semantic_control_id": "action:wrong_add", "semantic_effect_pass": False},
        ),
        ActionEvent(
            action_id="ok-2", type="navigate", target="Rule Details", success=True, stage="rules",
            page_url_before="https://hip/rules", page_url_after="https://hip/rules/1",
            execution_provenance={"semantic_control_id": "nav:rule_details", "semantic_effect_pass": True},
        ),
    ])
    result = engine.learn_phase(
        browser=browser, phase="rules", run_id="run-negative", task="learn rules",
        exact_verified=True, judge_pass=True, human_pass=True,
    )
    assert result["trusted_promotion"] is True
    assert result["negative_evidence_count"] == 1
    assert result["verified_route_actions"] == 2
    wrong = next(x for x in graph.data["capabilities"].values() if x.get("label") == "action:wrong_add")
    assert wrong["trust"] == "negative_evidence"
    assert wrong["verified"] is False
    wrong_id = wrong["capability_id"]
    assert all(r.get("source") != wrong_id and r.get("target") != wrong_id for r in graph.data["relations"])


def test_r12_continuous_learning_switches_are_runtime_effective(tmp_path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.continuous_learning.learn_from_click = False
    cfg.continuous_learning.learn_from_search = False
    cfg.continuous_learning.learn_failed_actions_as_negative_evidence = False
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / "capability_graph")
    engine = ContinuousPortalLearningEngine(cfg, capability_graph=graph)
    browser = SimpleNamespace(action_events=[
        ActionEvent(action_id="1", type="click", target="+ Add", success=True, stage="rules",
                    execution_provenance={"semantic_control_id": "action:add", "semantic_effect_pass": True}),
        ActionEvent(action_id="2", type="search", target="Search", success=True, stage="rules",
                    execution_provenance={"semantic_control_id": "field:search", "semantic_effect_pass": True}),
        ActionEvent(action_id="3", type="fill", target="Name", success=False, stage="rules",
                    execution_provenance={"semantic_control_id": "field:name", "semantic_effect_pass": False}),
        ActionEvent(action_id="4", type="navigate", target="Rules", success=True, stage="rules",
                    execution_provenance={"semantic_control_id": "nav:rules", "semantic_effect_pass": True}),
    ])
    result = engine.learn_phase(
        browser=browser, phase="rules", run_id="run-switches", task="learn rules",
        exact_verified=True, judge_pass=True, human_pass=True,
    )
    assert result["experience_count"] == 1
    assert result["skipped_by_policy"] == 3
    assert result["negative_evidence_count"] == 0


def test_r12_autowebglm_downstream_reward_updates_exact_tournament_trace():
    cfg = AppConfig()
    cfg.aia.enabled = True
    bridge = AutoWebGLMRecoveryBridge(cfg.autowebglm, aia_config=cfg.aia, app_config=cfg)

    class FakePortfolio:
        def __init__(self):
            self.recorded = None
        def record_downstream_outcome(self, **kwargs):
            self.recorded = kwargs
            return {"role_champions": {"action_selection": "gpt-oss-20b"}}

    fake = FakePortfolio()
    bridge.model_portfolio = fake
    trace = {
        "used": True,
        "role": "action_selection",
        "winner_model": "gpt-oss-20b",
        "candidate_results": [{"model": "gpt-oss-20b", "quality": 0.9}],
    }
    result = bridge.record_downstream_outcome(
        decision={"model_routing": trace}, success=True, reward=1.0
    )
    assert result["updated"] is True
    assert fake.recorded["trace"] is trace
    assert fake.recorded["success"] is True
    assert fake.recorded["reward"] == 1.0
    assert fake.recorded["role"] == "action_selection"


def test_r12_learning_receipt_distinguishes_candidate_from_trusted_promotion(tmp_path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / "capability_graph")
    engine = ContinuousPortalLearningEngine(cfg, capability_graph=graph)
    browser = SimpleNamespace(action_events=[
        ActionEvent(
            action_id="candidate-1", type="navigate", target="Data Maps", success=True, stage="data_map",
            page_url_before="https://hip/home", page_url_after="https://hip/datamaps",
            execution_provenance={"semantic_control_id": "nav:data_maps", "semantic_effect_pass": True},
        )
    ])
    candidate = engine.learn_phase(
        browser=browser, phase="data_map", run_id="candidate-run", task="learn data maps",
        exact_verified=True, judge_pass=True, human_pass=None,
    )
    assert candidate["status"] == "candidate_learned"
    assert candidate["trusted_promotion"] is False

    trusted = engine.learn_phase(
        browser=browser, phase="data_map", run_id="trusted-run", task="learn data maps",
        exact_verified=True, judge_pass=True, human_pass=True,
    )
    assert trusted["status"] == "trusted_promoted"
    assert trusted["trusted_promotion"] is True
