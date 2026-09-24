from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
from hip_id_agent.config import load_config
from hip_id_agent.dummy_fill_e2e import _strip_ephemeral_replay_locators
from hip_id_agent.live_readiness import build_live_readiness_report
from hip_id_agent.live_runtime_certification import runtime_environment_fingerprint
from hip_id_agent.streamlit_dashboard import (
    build_mission_command,
    build_phase_subset_mission_command,
    build_section_mission_command,
)


@pytest.mark.asyncio
async def test_strict_autonomous_completion_fails_closed_when_execution_proof_is_missing(monkeypatch):
    import hip_id_agent.autonomous_form_runtime as afr

    async def surface(*args, **kwargs):
        return {"fatal": False, "pass": True}

    async def controls(*args, **kwargs):
        return []

    async def executor(page, graph, **kwargs):
        # Regression case: an inner executor says pass but supplies no physical/
        # authoritative execution-stage proof. Strict live completion must reject it.
        return {"pass": True, "attempts": []}

    monkeypatch.setattr(afr, "assert_active_surface", surface)
    monkeypatch.setattr(afr, "capture_stateful_controls", controls)

    result = await execute_autonomous_phase_goal(
        page=object(),
        graph={"nodes": [], "dependency_edges": []},
        phase="data_map",
        input_data={},
        config=SimpleNamespace(
            autonomous_form=SimpleNamespace(
                no_progress_cycle_limit=1,
                use_autowebglm_live_observation=False,
                use_dell_aia_binding_advisor_on_ambiguity=False,
            )
        ),
        max_cycles=1,
        strict_live_execution=True,
        executor=executor,
    )
    assert result["pass"] is False
    assert result["status"] == "failed_closed"


@pytest.mark.asyncio
async def test_non_strict_offline_autonomous_completion_can_accept_pass_without_live_proof(monkeypatch):
    import hip_id_agent.autonomous_form_runtime as afr

    async def surface(*args, **kwargs):
        return {"fatal": False, "pass": True}

    async def controls(*args, **kwargs):
        return []

    async def executor(page, graph, **kwargs):
        return {"pass": True, "attempts": []}

    monkeypatch.setattr(afr, "assert_active_surface", surface)
    monkeypatch.setattr(afr, "capture_stateful_controls", controls)

    result = await execute_autonomous_phase_goal(
        page=object(), graph={"nodes": [], "dependency_edges": []},
        phase="data_map", input_data={},
        config=SimpleNamespace(autonomous_form=SimpleNamespace(
            no_progress_cycle_limit=1,
            use_autowebglm_live_observation=False,
            use_dell_aia_binding_advisor_on_ambiguity=False,
        )),
        max_cycles=1, strict_live_execution=False, executor=executor,
    )
    assert result["pass"] is True


def test_semantic_replay_strips_all_ephemeral_selector_coordinate_evidence():
    raw = {
        "selector": "#angular-generated-123",
        "selector_current_generation": "[data-id='abc']",
        "xpath": "//div[7]/button",
        "bounding_box": {"x": 10, "y": 20, "width": 100, "height": 30},
        "screen_x": 900,
        "screen_y": 400,
        "nested": [
            {"label": "Map Name", "role": "textbox", "x_ratio": 0.7, "y_ratio": 0.2},
            {"business_goal": "set map name", "section": "Create Map"},
        ],
    }
    cleaned = _strip_ephemeral_replay_locators(raw)
    rendered = repr(cleaned).lower()
    assert "selector" not in rendered
    assert "xpath" not in rendered
    assert "screen_x" not in rendered
    assert "x_ratio" not in rendered
    assert cleaned["nested"][0]["label"] == "Map Name"
    assert cleaned["nested"][1]["business_goal"] == "set map name"


def _readiness(cfg):
    return build_live_readiness_report(
        fingerprint="v232",
        static_preflight={"pass": True, "skill_vetting": {"pass": True}, "autogen": {"pass": True}},
        browser_probe={"pass": True, "selected": "python-playwright"},
        mcp_probe={
            "playwright_mcp": {"available": False, "error": "not installed"},
            "chrome_devtools_mcp": {"available": False, "error": "not installed"},
            "hip_intelligence_mcp": {"available": False, "error": "not installed"},
            "pyautogui_mcp": {"available": False, "error": "not available"},
        },
        text_probe={"pass": True},
        vision_probe={"pass": True},
        path_probe={"pass": True},
        process_state={"running": False},
        config=cfg,
        runtime_certificate_probe={"pass": True, "status": "valid"},
    )


def test_normal_runtime_is_true_hybrid_quorum_not_all_mcp_or_nothing():
    cfg = load_config("config.yaml")
    assert cfg.mcp.strict_runtime_required is False
    assert cfg.mcp.hip_intelligence_mcp_required is False
    assert cfg.semantic_understanding.strict_external_evidence is False
    report = _readiness(cfg)
    assert report["pass"] is True
    assert report["decision"] == "GO"
    assert report["runtime_mode"] == "adaptive_hybrid"
    failed = {row["id"]: row for row in report["checks"] if not row["pass"]}
    assert failed["playwright_mcp_live"]["severity"] == "warning"
    assert failed["chrome_devtools_mcp_live"]["severity"] == "warning"
    assert failed["hip_intelligence_mcp_live"]["severity"] == "warning"
    quorum = next(row for row in report["checks"] if row["id"] == "hybrid_executor_quorum")
    assert quorum["pass"] is True
    assert quorum["evidence"]["python_playwright"] is True


def test_strict_windows_profile_still_requires_complete_mcp_stack():
    cfg = load_config("config.mcp-required.windows.yaml")
    assert cfg.mcp.strict_runtime_required is True
    assert cfg.mcp.hip_intelligence_mcp_required is True
    assert cfg.semantic_understanding.strict_external_evidence is True
    report = _readiness(cfg)
    assert report["pass"] is False
    assert report["decision"] == "NO_GO"
    assert report["runtime_mode"] == "strict_mcp"
    blockers = {row["id"] for row in report["checks"] if row["severity"] == "blocker" and not row["pass"]}
    assert "playwright_mcp_live" in blockers
    assert "chrome_devtools_mcp_live" in blockers
    assert "hip_intelligence_mcp_live" in blockers


def test_runtime_certificate_fingerprint_distinguishes_adaptive_and_strict_profiles():
    adaptive = load_config("config.yaml")
    strict = load_config("config.mcp-required.windows.yaml")
    assert runtime_environment_fingerprint(adaptive) != runtime_environment_fingerprint(strict)


def test_control_center_mission_builders_default_to_executor_fallback():
    common = dict(
        project_root=Path.cwd(), config="config.yaml",
        input_json="examples/uhaul_poasn_full_dummy_input.json",
        runs_dir="runs", golden_screenshot_dir="golden_screenshots/UHAUL-POASN",
        upload_assets_dir="uploads", python_executable="python",
    )
    full = build_mission_command(**common)
    section = build_section_mission_command(**common, section="data-map")
    subset = build_phase_subset_mission_command(**common, phases=["data_map", "source_document_type"])
    assert "--autonomous-mission" in full
    for command in (full, section, subset):
        # Section/subset runs cannot use --autonomous-mission because that switch
        # intentionally owns all seven phases; config.yaml still enables the same
        # shared autonomous form runtime for the selected phase(s).
        assert "--allow-executor-fallback" in command
        assert "--require-mcp" not in command


def test_v232_version_is_promoted():
    import hip_id_agent
    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")
