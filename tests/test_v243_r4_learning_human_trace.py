from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.config import AppConfig
from hip_id_agent.deterministic_recipe import DeterministicRecipeLibrary
from hip_id_agent.human_teaching import HumanTeachingStore
from hip_id_agent.trace_self_repair import TraceSelfRepairEngine


def test_human_teaching_roundtrip_is_value_free(tmp_path: Path):
    cfg = SimpleNamespace(enabled=True, min_task_similarity=0.2)
    store = HumanTeachingStore(tmp_path / "human", cfg)
    req = store.create_request(
        run_id="run1", task="fill source document type", input_root="$.objects.source",
        unresolved_input_paths=["$.objects.source.documentType"],
        controls=[{"semantic_control_id":"sc-1","label":"Document Type","section":"Source","role":"combobox","name":"documentType"}],
        reason="ambiguous mapping",
    )
    assert req["status"] == "needs_assistance"
    teaching = store.submit(
        request_id=req["request_id"], input_path="$.objects.source.documentType",
        semantic_control_id="sc-1", verified_by_human=True,
    )
    assert teaching["control"]["label"] == "Document Type"
    bp = store.blueprint(task="fill source document type", input_root="$.objects.source")
    assert bp["field_count"] == 1
    raw = (tmp_path / "human" / "teachings.jsonl").read_text(encoding="utf-8")
    assert "customer" not in raw.lower()
    assert '"values_stored": false' in raw.lower()


def test_deterministic_recipe_promotes_after_verified_successes(tmp_path: Path):
    cfg = SimpleNamespace(enabled=True, min_verified_successes=2, min_average_reward=0.9, min_match_score=0.5, demote_after_failures=2)
    lib = DeterministicRecipeLibrary(tmp_path / "recipes", cfg)
    kwargs = dict(
        task="fill source document type", actions=["edit"], target_area="Document Types", input_root="$.objects.source",
        steps=[{"type":"navigate","risk":"read"},{"type":"fill_from_input","input_root":"$.objects.source","risk":"draft"}],
        form_blueprints=[{"input_root":"$.objects.source","fields":[{"input_path":"$.objects.source.documentType","semantic_locator":{"labels":["Document Type"]}}]}],
        reward=1.0,
    )
    first = lib.record_success(run_id="r1", **kwargs)
    assert first["status"] == "learning"
    second = lib.record_success(run_id="r2", **kwargs)
    assert second["status"] == "validated"
    match = lib.match(task="fill source document type", actions=["edit"], target_area="Document Types", input_root="$.objects.source")
    assert match["active"] is True
    assert match["recipe"]["values_stored"] is False


class _FakePortfolio:
    def tournament_text(self, **kwargs):
        return {
            "used": True,
            "winner_model": "gpt-oss-120b",
            "role": "recovery",
            "task_key": "recovery:test",
            "parsed": {
                "repair_strategy": "rebind_controls",
                "confidence": 0.93,
                "human_required": False,
                "root_cause": "stale semantic binding",
                "repair_reason": "re-capture controls and bind by live semantics",
            },
            "candidate_results": [
                {"model":"gpt-oss-120b","ok":True,"quality":0.95,"latency_ms":10},
                {"model":"mistral-small-3-1-24b-instruct-2503","ok":True,"quality":0.88,"latency_ms":9},
            ],
        }


def test_trace_self_repair_reads_masked_trace_and_selects_bounded_strategy(tmp_path: Path):
    run = tmp_path / "run"; run.mkdir()
    (run / "mission_trace.json").write_text('{"phase":"document_type","error":"control missing"}', encoding="utf-8")
    cfg = SimpleNamespace(enabled=True, max_trace_chars=10000, auto_repair_min_confidence=0.72)
    engine = TraceSelfRepairEngine(tmp_path / "repair", cfg, model_portfolio=_FakePortfolio())
    out = engine.analyze(run_dir=run, task="fill document type", error="control missing", failed_step={"type":"fill_from_input"})
    assert out["repair_strategy"] == "rebind_controls"
    assert out["human_required"] is False
    assert out["confidence"] > 0.9
    assert out["source_code_self_modification"] is False


def test_new_configs_are_enabled_by_default():
    cfg = AppConfig()
    assert cfg.trace_self_repair.enabled is True
    assert cfg.deterministic_recipe.enabled is True
    assert cfg.human_in_the_loop.enabled is True
    assert cfg.recursive_self_improvement.allow_source_code_self_modification is False
