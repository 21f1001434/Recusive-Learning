from pathlib import Path

import pytest

from hip_id_agent.dummy_fill_e2e import prepare_golden_screenshot_references
from hip_id_agent.stateful_form_runtime import compile_data_map_state_graph, _stateful_value_equal
from hip_id_agent.streamlit_dashboard import build_mission_command


def _data_map_payload():
    return {
        "objects": {
            "data_map": {
                "map_identifier": "MAP-A",
                "map_identifier_version": "1",
                "status": "Enable",
                "map_name": "Map A",
                "map_class": "Transform_A",
                "contivo_version": "6.7",
                "map_data_file": "a.jar",
            }
        }
    }


def test_data_map_identifier_version_is_verification_only():
    graph = compile_data_map_state_graph(_data_map_payload(), "data_map")
    node = next(n for n in graph["nodes"] if n["field_key"] == "map_identifier_version")
    assert node["action"] == "verify_only"
    assert node["required"] is False


def test_single_select_uses_selected_option_when_dds_input_value_is_blank():
    node = {
        "phase": "data_map",
        "field_key": "contivo_version",
        "action": "select_single",
        "expected_value": "6.7",
    }
    control = {
        "value": "",
        "selected_values": ["6.7"],
        "role": "combobox",
    }
    assert _stateful_value_equal(node, control) is True


def test_golden_reference_copy_failure_is_nonblocking_and_phase_scoped(tmp_path: Path, monkeypatch):
    src = tmp_path / "golden"
    src.mkdir()
    tp = src / "Source Transport Profile.png"
    dm = src / "Data Map.png"
    tp.write_bytes(b"not-a-real-image-but-enough-for-metadata")
    dm.write_bytes(b"other")

    import hip_id_agent.dummy_fill_e2e as e2e

    def fail_copy(*_args, **_kwargs):
        raise FileNotFoundError(2, "simulated Windows long-path copy failure")

    monkeypatch.setattr(e2e.shutil, "copy2", fail_copy)
    refs = prepare_golden_screenshot_references(
        src,
        tmp_path / "runs" / ("SECTION-SOURCE_TRANSPORT_PROFILE-" + "x" * 80),
        phases=["source_transport_profile"],
    )
    assert set(refs) == {"source_transport_profile"}
    row = refs["source_transport_profile"][0]
    assert row["file"] == "Source Transport Profile.png"
    assert row["copy_status"] == "source_fallback_after_copy_error"
    assert Path(row["path"]) == tp


def test_full_ui_mission_is_fresh_by_default_and_requests_autonomous_all_phase_profile(tmp_path: Path):
    (tmp_path / "config.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "input.json").write_text("{}", encoding="utf-8")
    (tmp_path / "golden").mkdir()
    (tmp_path / "uploads").mkdir()
    cmd = build_mission_command(
        project_root=tmp_path,
        config="config.yaml",
        input_json="input.json",
        runs_dir="runs",
        golden_screenshot_dir="golden",
        upload_assets_dir="uploads",
    )
    assert "--autonomous-mission" in cmd
    assert "--auto-resume" not in cmd
    assert "--resume-run" not in cmd

def test_full_autonomous_profile_continues_remaining_selected_no_save_phases_after_block():
    import inspect
    from hip_id_agent import cli, dummy_fill_e2e

    cli_source = inspect.getsource(cli.run_full_dummy_fill)
    flow_source = inspect.getsource(dummy_fill_e2e.FullDummyFillE2EFlow.run)
    assert "continue_after_phase_block=bool(autonomous_mission)" in cli_source
    assert "continue_remaining_selected_phases" in flow_source
    assert "final mission cannot pass while any phase is blocked" in flow_source
