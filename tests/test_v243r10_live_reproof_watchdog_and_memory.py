from pathlib import Path

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.dummy_fill_e2e import phase_exact_completion_checkpoint
from hip_id_agent.human_teaching import human_teaching_from_config
from hip_id_agent.phase_live_reproof import live_read_only_phase_reproof
from hip_id_agent.phase_progress import PhaseNoProgressError, run_with_progress_watchdog
from hip_id_agent.section_judge import DualModelSectionJudge, SectionJudgePolicy


class _FilePage:
    async def evaluate(self, _script, *args):
        return [{"files": ["map.jar"], "value": r"C:\\fakepath\\map.jar"}]


@pytest.mark.asyncio
async def test_live_read_only_reproof_proves_stable_datamap_without_replay(monkeypatch):
    controls = [
        {"label": "Map Identifier", "value": "MAP-1", "section": "Create Map", "row_kind": "", "row_index": None, "aria_invalid": ""},
        {"label": "Map Name", "value": "NAME", "section": "Create Map", "row_kind": "", "row_index": None, "aria_invalid": ""},
        {"label": "Map Class", "value": "Contivo", "section": "Create Map", "row_kind": "", "row_index": None, "aria_invalid": ""},
        {"label": "Contivo Version", "value": "6.7", "section": "Create Map", "row_kind": "", "row_index": None, "aria_invalid": ""},
    ]

    async def fake_capture(_page, _phase):
        return controls

    monkeypatch.setattr("hip_id_agent.phase_live_reproof.capture_stateful_controls", fake_capture)
    payload = {"objects": {"data_map": {
        "map_identifier": "MAP-1",
        "map_name": "NAME",
        "map_class": "Contivo",
        "contivo_version": "6.7",
        "map_data_file": r"C:\\assets\\map.jar",
    }}}
    judge = DualModelSectionJudge(SectionJudgePolicy(enabled=True, require_text_model=False, require_vision_model=False, max_repairs=0, fail_closed=True))
    result = await live_read_only_phase_reproof(page=_FilePage(), phase="data_map", phase_input=payload, judge=judge)
    assert result["pass"] is True
    assert result["deterministic_pass"] is True
    assert result["required_upload_proof"]["pass"] is True
    assert result["browser_replay_performed"] is False
    assert result["form_mutated"] is False
    assert result["values_stored"] is False


def test_exact_checkpoint_accepts_value_free_live_read_only_reproof(tmp_path: Path):
    (tmp_path / "phase_live_read_only_reproof.json").write_text(
        '{"pass":true,"deterministic_pass":true,"read_only":true,"source":"current_live_browser",'
        '"missing_fields":[],"row_issue_fields":[],"invalid_fields":[],"required_upload_proof":{"pass":true},'
        '"values_stored":false,"selectors_stored":false,"coordinates_stored":false}',
        encoding="utf-8",
    )
    checkpoint = phase_exact_completion_checkpoint("data_map", tmp_path)
    assert checkpoint["pass"] is True
    assert checkpoint["status"] == "exact_live_state_reproved_read_only"
    assert checkpoint["read_only_live_reproof"] is True


@pytest.mark.asyncio
async def test_watchdog_uses_async_terminal_reproof_before_declaring_no_progress(tmp_path: Path):
    async def operation():
        import asyncio
        await asyncio.sleep(5)

    async def marker():
        return {"signature": "stable", "route": "/hip/datamaps"}

    calls = {"n": 0}

    async def checkpoint():
        calls["n"] += 1
        return {"pass": True, "status": "exact_live_state_reproved_read_only"}

    with pytest.raises(PhaseNoProgressError) as caught:
        await run_with_progress_watchdog(
            operation(),
            phase="data_map",
            marker_provider=marker,
            checkpoint_provider=checkpoint,
            no_progress_seconds=0.03,
            poll_seconds=0.01,
            evidence_path=tmp_path / "watchdog.json",
        )
    assert calls["n"] >= 1
    assert caught.value.code == "HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL"
    assert caught.value.payload["exact_completion_checkpoint_pass"] is True


def test_learning_memory_is_persistent_and_not_run_local(tmp_path: Path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "persistent_memory")
    store = human_teaching_from_config(cfg)
    assert str(store.root).startswith(str(tmp_path / "persistent_memory"))
    assert "human_teaching" in str(store.root)
    assert "runs" not in str(store.root.relative_to(tmp_path))


def test_r10_new_attempt_invalidates_stale_live_reproof_and_writes_memory_receipt():
    source = Path('hip_id_agent/dummy_fill_e2e.py').read_text(encoding='utf-8')
    assert 'stale_live_reproof = phase_dir / "phase_live_read_only_reproof.json"' in source
    assert 'stale_live_reproof.unlink()' in source
    assert 'phase_learning_memory_receipt.json' in source
    assert 'customer_values_persisted": False' in source
    assert 'validated_phase_learning_recorded' in source
