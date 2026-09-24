from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.human_phase_review import HumanPhaseReviewStore
from hip_id_agent.judge_consensus import MultiModelJudgeConsensus


def _cfg(tmp_path: Path):
    return SimpleNamespace(
        reporting=SimpleNamespace(memory_dir=str(tmp_path / "memory")),
        human_in_the_loop=SimpleNamespace(enabled=True, memory_subdir="human_teaching"),
        model_portfolio=SimpleNamespace(
            enabled=True,
            parallel_models=3,
            max_parallel_models=4,
            shadow_reward_weight=0.35,
            benchmark_low_confidence_only=True,
            fast_exploitation_single_model=False,
            min_champion_trials=3,
            min_champion_score=0.78,
            text_models=["gpt-oss-120b", "gpt-oss-20b", "mistral-small-3-1-24b-instruct-2503"],
            vision_models=[],
            embedding_models=[],
            memory_subdir="model_portfolio",
        ),
        aia=SimpleNamespace(),
    )


def test_phase_review_is_once_per_run_phase_and_human_pass_needs_exact(tmp_path: Path):
    store = HumanPhaseReviewStore(tmp_path / "reviews", SimpleNamespace(enabled=True))
    base = dict(
        run_id="RUN-1", phase="data_map", phase_display="Data Map", attempt=1,
        automated_judge={"pass": False, "status": "blocked", "deterministic_judge": {"pass": False}},
        verification={"status": "failed"}, exact_checkpoint={"pass": False}, model_consensus={}, screenshot_path="x.png",
        reason="judge blocked",
    )
    first = store.create_or_update(**base)
    second = store.create_or_update(**{**base, "attempt": 2})
    assert first["request_id"] == second["request_id"]
    resolved = store.resolve(request_id=first["request_id"], verdict="pass", note="looks okay")
    assert resolved["human_verdict"] == "pass"
    assert resolved["effective_human_verdict"] == "pass_pending_live_reproof"
    assert resolved["human_can_reconcile_model_only_disagreement"] is False


def test_phase_review_can_reconcile_model_only_block_when_exact_passed(tmp_path: Path):
    store = HumanPhaseReviewStore(tmp_path / "reviews", SimpleNamespace(enabled=True))
    req = store.create_or_update(
        run_id="RUN-2", phase="data_map", phase_display="Data Map", attempt=3,
        automated_judge={"pass": False, "status": "blocked", "deterministic_judge": {"pass": True}},
        verification={"status": "pass"}, exact_checkpoint={"pass": True}, model_consensus={}, screenshot_path="x.png",
        reason="model disagreed",
    )
    resolved = store.resolve(request_id=req["request_id"], verdict="looks_correct")
    assert resolved["effective_human_verdict"] == "pass"
    assert resolved["human_can_reconcile_model_only_disagreement"] is True


def test_multi_model_panel_rescues_only_with_exact_evidence_pass(tmp_path: Path):
    consensus = MultiModelJudgeConsensus(_cfg(tmp_path))

    class FakeRouter:
        def tournament_text(self, **kwargs):
            return {
                "used": True,
                "trace_id": "trace-1",
                "role": "judge",
                "task_key": "judge:x",
                "winner_model": "gpt-oss-120b",
                "candidate_results": [],
                "candidate_votes": [
                    {"model": "gpt-oss-120b", "ok": True, "parsed": {"pass": True, "confidence": 0.95, "needs_human": False, "evidence_conflict": False}},
                    {"model": "gpt-oss-20b", "ok": True, "parsed": {"pass": True, "confidence": 0.92, "needs_human": False, "evidence_conflict": False}},
                    {"model": "mistral-small-3-1-24b-instruct-2503", "ok": True, "parsed": {"pass": False, "confidence": 0.61, "needs_human": True, "evidence_conflict": True}},
                ],
            }

    consensus.router = FakeRouter()
    judge = {
        "pass": False,
        "status": "blocked",
        "deterministic_judge": {"pass": True},
        "text_model_judge": {"status": "ok", "pass": False},
        "vision_model_judge": {"status": "ok", "pass": True},
    }
    result = consensus.resolve(phase="data_map", automated_judge=judge, verification={"status": "pass"}, exact_checkpoint={"pass": True})
    assert result["rescued_model_only_block"] is True
    assert result["pass"] is True
    assert result["pass_votes"] == 2

    judge2 = {**judge, "deterministic_judge": {"pass": False}}
    result2 = consensus.resolve(phase="data_map", automated_judge=judge2, verification={"status": "failed"}, exact_checkpoint={"pass": False}, force=True)
    assert result2["exact_evidence_pass"] is False
    assert result2["pass"] is False
    assert result2["rescued_model_only_block"] is False


def test_r6_wires_learning_review_and_model_consensus_into_phase_loop():
    source = Path("hip_id_agent/dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert "multi_model_judge_consensus.json" in source
    assert "HUMAN_PHASE_REVIEW_PENDING" in source
    assert "pass_human_confirmed" in source
    assert "learning_review_phases" in source


def test_native_coordinator_reviews_only_families_learned_this_run():
    source = Path("hip_id_agent/native_hip_phase_mission.py").read_text(encoding="utf-8")
    assert "learning_review_phases" in source
    assert "for family in missing" in source
    assert "learning_review_phases=tuple(learning_review_phases)" in source
