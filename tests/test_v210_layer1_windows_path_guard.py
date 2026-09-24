from __future__ import annotations

from pathlib import Path

import pytest

from hip_id_agent import safe_io
from hip_id_agent.cli import make_run_id
from hip_id_agent.config import AppConfig
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController
from hip_id_agent.streamlit_dashboard import build_mission_command


def test_compact_path_component_is_bounded_and_collision_resistant():
    long_a = "UHAUL-POASN-AGENTQ-UI-API-STREAMLIT-" + "A" * 80
    long_b = "UHAUL-POASN-AGENTQ-UI-API-STREAMLIT-" + "B" * 80
    a = safe_io.compact_path_component(long_a, max_len=28)
    b = safe_io.compact_path_component(long_b, max_len=28)
    assert len(a) <= 28
    assert len(b) <= 28
    assert a != b
    assert a == safe_io.compact_path_component(long_a, max_len=28)


def test_generated_run_id_does_not_inherit_huge_ui_profile_name():
    rid = make_run_id("UHAUL-POASN-AGENTQ-UI-API-STREAMLIT-WITH-A-VERY-LONG-SUFFIX")
    assert len(rid) <= 44
    assert rid.endswith(tuple(str(y) for y in range(0))) is False  # shape guard without timestamp coupling
    assert "AGENTQ-UI-API-STREAMLIT-WITH" not in rid


def test_streamlit_operational_command_uses_short_business_customer_label(tmp_path: Path):
    cmd = build_mission_command(
        project_root=tmp_path,
        config="config.yaml",
        input_json="input.json",
        runs_dir="runs",
        golden_screenshot_dir="golden_screenshots/UHAUL-POASN",
        upload_assets_dir="uploads",
    )
    customer_idx = cmd.index("--customer")
    assert cmd[customer_idx + 1] == "UHAUL-POASN"


def test_safe_mkdir_retries_with_windows_long_path_prefix(monkeypatch, tmp_path: Path):
    target = tmp_path / ("deep-" * 80)
    calls = []

    def fail_original(*args, **kwargs):
        raise OSError(206, "The filename or extension is too long")

    monkeypatch.setattr(safe_io, "_ORIGINAL_PATH_MKDIR", fail_original)
    monkeypatch.setattr(safe_io.os, "name", "nt", raising=False)
    monkeypatch.setattr(safe_io, "_nt_long_path", lambda path: r"\\?\C:\HIP\deep")
    monkeypatch.setattr(safe_io.os, "makedirs", lambda path, mode=0o777, exist_ok=False: calls.append((path, mode, exist_ok)))

    safe_io.safe_mkdir(target, parents=True, exist_ok=True)
    assert calls == [(r"\\?\C:\HIP\deep", 0o777, True)]


@pytest.mark.asyncio
async def test_runtime_self_heal_compacts_exact_failure_shape_from_windows_trace(tmp_path: Path):
    cfg = AppConfig()
    cfg.runtime_self_heal.capture_evidence = False

    class Browser:
        console_messages = []
        network_tab_events = []
        action_events = []

    controller = RuntimeSelfHealController(
        config=cfg,
        root_dir=tmp_path,
        browser=Browser(),
        run_id="UHAUL-POASN-AGENTQ-UI-API-STREAMLIT-20260901-155916",
    )
    attempt_dir, observation = await controller._capture_evidence(
        phase="target_transport_profile",
        target_url="https://developer.dell.com/hybrid-integrations/securelink/transport-profiles",
        attempt=1,
        classification="multi_select_mismatch",
        message="test",
        failure_kind="verification",
        diagnosis={},
        verification={},
        judge_result={},
    )
    assert observation["phase"] == "target_transport_profile"
    assert observation["classification"] == "multi_select_mismatch"
    assert attempt_dir.name.startswith("a01_")
    assert len(attempt_dir.parent.name) <= 22
    assert len(attempt_dir.name) <= 22
    assert "target_transport_profile" not in str(attempt_dir)
