from pathlib import Path

from hip_id_agent.config import AppConfig, ReplayPolicyConfig
from hip_id_agent.replay_policy import ReplayPolicyEngine, replay_policy_engine_from_config


def _fill(pass_: bool = True, leaves: int = 4, mapped: int = 4, cycles: int = 1):
    return {
        "type": "fill_from_input",
        "pass": pass_,
        "input_leaf_count": leaves,
        "mapped_input_leaf_count": mapped,
        "unresolved_input_leaves": [] if mapped == leaves else ["$.objects.rule.conditions[1].value"],
        "verification": "100_percent_runtime_input_exact_readback" if pass_ and mapped == leaves else "incomplete",
        "cycles": [{} for _ in range(cycles)],
        "input_root": "$.objects.rule",
        "risk": "draft",
    }


def test_successful_replay_world_promotes_exploitation(tmp_path: Path):
    cfg = ReplayPolicyConfig(min_replay_support=2, exploitation_min_confidence=0.70, exploitation_min_success_rate=0.80)
    engine = ReplayPolicyEngine(tmp_path, cfg)
    for i in range(2):
        ep = engine.record_episode(
            task="edit rule and fill from input json then save",
            actions=["edit", "save"], target_area="Rules", input_root="$.objects.rule",
            steps=[{"type": "navigate", "pass": True}, _fill(), {"type": "semantic_action", "action": "save", "risk": "mutation", "pass": True}],
            success=True, run_id=f"run-{i}", mutation_required=True, mutation_verified=True,
            input_paths=["$.objects.rule.conditions[0].value", "$.objects.rule.conditions[1].value"],
        )
        assert ep["score"] > 0.8
    dream = engine.dream(reason="test")
    assert dream["policy_updates"] == 1
    decision = engine.decide(task="edit rule and fill from input json then save", actions=["edit", "save"], target_area="Rules", input_root="$.objects.rule")
    assert decision["mode"] == "exploitation"
    assert decision["workflow"]
    assert decision["live_reproof_required"] is True


def test_incomplete_fill_pushes_policy_away_from_exploitation(tmp_path: Path):
    cfg = ReplayPolicyConfig(min_replay_support=2, exploitation_min_confidence=0.85, exploitation_min_success_rate=0.90)
    engine = ReplayPolicyEngine(tmp_path, cfg)
    engine.record_episode(task="fill rule", actions=["fill"], target_area="Rules", input_root="$.objects.rule", steps=[_fill(False, 5, 3, 3)], success=False, run_id="bad-1", blocked=True)
    engine.record_episode(task="fill rule", actions=["fill"], target_area="Rules", input_root="$.objects.rule", steps=[_fill(True, 5, 5, 2)], success=True, run_id="good-1")
    engine.dream(reason="test")
    decision = engine.decide(task="fill rule", actions=["fill"], target_area="Rules", input_root="$.objects.rule")
    assert decision["mode"] in {"exploration", "hybrid"}


def test_policy_cache_never_persists_input_values_or_selectors(tmp_path: Path):
    engine = ReplayPolicyEngine(tmp_path, ReplayPolicyConfig(min_replay_support=1, exploitation_min_confidence=0.1, exploitation_min_success_rate=0.1))
    engine.record_episode(
        task="edit ACME rule", actions=["edit"], target_area="Rules", input_root="$.objects.rule",
        steps=[{"type": "search", "value": "SECRET-CUSTOMER", "pass": True}, _fill()], success=True, run_id="r1",
    )
    engine.dream(reason="test")
    text=(tmp_path/"replay_episodes.jsonl").read_text()+ (tmp_path/"policy_cache.json").read_text()
    assert "SECRET-CUSTOMER" not in text
    assert "selector" not in text.lower() or '"selectors_stored": false' in text.lower()


def test_action_policy_switches_from_explore_to_exploit(tmp_path: Path):
    engine = ReplayPolicyEngine(tmp_path, ReplayPolicyConfig(min_action_replay_support=2, action_exploitation_threshold=0.60))
    matches = [
        {"selected_recovery": "execute_bound_action", "success": True, "reward": 1.2},
        {"selected_recovery": "execute_bound_action", "success": True, "reward": 1.0},
        {"selected_recovery": "wait_for_rerender", "success": False, "reward": -0.4},
    ]
    hint = engine.action_policy(memory_matches=matches, node_signature="rule|0|value")
    assert hint["mode"] == "exploitation"
    assert hint["preferred_actions"][0] == "execute_bound_action"


def test_engine_from_app_config_uses_disk_cache(tmp_path: Path):
    cfg=AppConfig()
    cfg.reporting.memory_dir=str(tmp_path)
    cfg.brain.directory="brain"
    cfg.replay_policy.memory_subdir="dream_cache"
    engine=replay_policy_engine_from_config(cfg)
    assert engine.root == tmp_path / "brain" / "dream_cache"
    assert engine.manifest()["values_stored"] is False
