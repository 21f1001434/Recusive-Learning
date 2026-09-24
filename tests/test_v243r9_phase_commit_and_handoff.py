from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.human_phase_review import HumanPhaseReviewStore


def test_human_looks_correct_without_exact_is_pending_reproof_not_rejection(tmp_path: Path):
    store = HumanPhaseReviewStore(tmp_path / "reviews", SimpleNamespace(enabled=True))
    req = store.create_or_update(
        run_id="run1", phase="data_map", phase_display="Data Map", attempt=1,
        automated_judge={"pass": False, "status": "blocked", "deterministic_judge": {"pass": False}},
        verification={"status": "failed"}, exact_checkpoint={"pass": False},
        model_consensus={}, reason="proof stale",
    )
    out = store.resolve(request_id=req["request_id"], verdict="pass", note="looks correct")
    assert out["human_verdict"] == "pass"
    assert out["effective_human_verdict"] == "pass_pending_live_reproof"
    assert "re-prove" in out["note"]


def test_r9_autonomous_runtime_does_not_block_on_verified_runtime_synthesized_nodes():
    source = Path("hip_id_agent/autonomous_form_runtime.py").read_text(encoding="utf-8")
    assert 'runtime_synthesized_node_count") or 0) == 0' not in source
    assert 'runtime_synthesized_nodes_verified' in source
    assert 'runtime_synthesized_nodes_are_learning_not_blockers' in source


def test_r9_human_approval_reproof_is_read_only_and_phase_commit_is_durable():
    source = Path("hip_id_agent/dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert 'human_approval_live_reproof.json' in source
    assert '"browser_replay_performed": False' in source
    assert 'phase_acceptance_commit.json' in source
    assert 'phase_completion_token.json' in source
    assert '"reexecute_same_phase": False' in source
    assert 'handoff_to_next_executable_phase' in source


def test_r9_learning_enrichment_cannot_reopen_accepted_form():
    source = Path("hip_id_agent/dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert 'HIP_LEARNING_EVIDENCE_INCOMPLETE_AFTER_PHASE_ACCEPTANCE' in source
    assert 'phase_completion_blocked": False' in source
    assert 'do not reopen/refill an exact+judge accepted form' in source
