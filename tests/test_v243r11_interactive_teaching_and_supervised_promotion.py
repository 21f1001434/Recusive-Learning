from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.deterministic_recipe import DeterministicRecipeLibrary
from hip_id_agent.interactive_teaching import InteractiveTeachingStore


def test_interactive_teaching_compiles_semantic_path_without_values_selectors_or_coordinates(tmp_path: Path):
    store = InteractiveTeachingStore(tmp_path)
    row = store.start(run_id="run-1", phase="data_map", task="teach data map")
    sid = row["session_id"]
    store.finish(session_id=sid)
    captured = store.capture(
        session_id=sid,
        current_url="https://developer.dell.com/hybrid-integrations/securelink/datatmaps?customer=secret",
        clicks=[
            {
                "timestamp": row["started_at"],
                "url": "https://developer.dell.com/hybrid-integrations/securelink/datamaps?customer=secret",
                "text": "+ Add",
                "role": "button",
                "tag": "button",
                "selector": "#secret-css",
                "boundingBox": {"x": 10, "y": 20, "width": 30, "height": 40},
            },
            {
                "timestamp": row["started_at"],
                "url": "https://developer.dell.com/hybrid-integrations/securelink/datamaps",
                "ariaLabel": "Contivo Version",
                "role": "combobox",
                "tag": "input",
            },
        ],
    )
    assert captured["status"] == "captured"
    text = str(captured)
    assert "secret-css" not in text
    assert "boundingBox" not in text
    assert "customer=secret" not in text
    assert captured["step_count"] >= 2
    assert any(s.get("label") == "+ Add" for s in captured["semantic_steps"])


def test_interactive_teaching_promotes_only_after_exact_human_and_judge_pass(tmp_path: Path):
    store = InteractiveTeachingStore(tmp_path)
    session = store.start(run_id="run-2", phase="rule")
    store.finish(session_id=session["session_id"])
    store.capture(
        session_id=session["session_id"],
        clicks=[{"timestamp": session["started_at"], "url": "https://developer.dell.com/rules", "text": "Edit", "role": "button", "tag": "button"}],
        current_url="https://developer.dell.com/rules",
    )
    untrusted = store.validate(session_id=session["session_id"], exact_pass=False, human_pass=True, judge_pass=True)
    assert not untrusted["validated"]
    trusted = store.validate(session_id=session["session_id"], exact_pass=True, human_pass=True, judge_pass=True)
    assert trusted["validated"]


def test_supervised_exact_human_phase_is_immediately_validated_recipe(tmp_path: Path):
    cfg = SimpleNamespace(min_verified_successes=2, min_average_reward=0.90, demote_after_failures=2, min_match_score=0.72, enabled=True)
    lib = DeterministicRecipeLibrary(tmp_path, cfg)
    row = lib.record_supervised_phase_success(
        phase="data_map",
        run_id="run-3",
        semantic_steps=[
            {"type": "navigate_observed", "target_route": "https://developer.dell.com/hybrid-integrations/securelink/datamaps", "human_demonstrated": True},
            {"type": "semantic_click", "action": "click", "label": "+ Add", "role": "button", "human_demonstrated": True},
            {"type": "fill_from_input", "action": "fill", "label": "Contivo Version", "input_path": "data_map.contivo_version", "input_root": "data_map"},
        ],
        exact_verified=True,
        human_confirmed=True,
        judge_pass=True,
        golden_aligned=True,
    )
    assert row["status"] == "validated"
    assert row["supervised_promotion"] is True
    assert row["live_reproof_required"] is True
    assert row["values_stored"] is False
    assert any(step.get("label") == "+ Add" for step in row.get("steps", []))


def test_supervised_recipe_does_not_validate_without_exact_proof(tmp_path: Path):
    cfg = SimpleNamespace(min_verified_successes=2, min_average_reward=0.90, demote_after_failures=2, min_match_score=0.72, enabled=True)
    lib = DeterministicRecipeLibrary(tmp_path, cfg)
    row = lib.record_supervised_phase_success(
        phase="biz_flow", run_id="r", semantic_steps=[{"type": "semantic_click", "label": "Configure Routing"}],
        exact_verified=False, human_confirmed=True, judge_pass=True,
    )
    assert row["status"] != "validated"
