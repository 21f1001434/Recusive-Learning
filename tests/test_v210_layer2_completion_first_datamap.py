from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.autowebglm_bridge import AutoWebGLMRecoveryBridge
from hip_id_agent.streamlit_dashboard import build_mission_command


class FakePage:
    url = "https://developer.dell.com/hybrid-integrations/securelink/maps"

    def context(self):
        return SimpleNamespace(pages=[self])

    async def title(self):
        return "HIP Data Maps"

    async def evaluate(self, _script):
        return {
            "html": '<html><e id="1" role="button" label="Expand"></e><e id="2" role="button" label="Cancel"></e></html>',
            "position": {"x": 0, "y": 0, "maxX": 0, "maxY": 800, "viewportW": 1400, "viewportH": 900},
            "title": "HIP Data Maps",
            "url": self.url,
            "elementCount": 2,
        }


def _cfg():
    return SimpleNamespace(
        enabled=True,
        primary_framework=True,
        recovery_only=False,
        deterministic_tool_fallback=True,
        require_intent_alignment=True,
        max_primary_decision_seconds=5,
        allowed_actions=["click", "hover", "select", "type_string", "scroll_page", "go", "jump_to", "switch_tab", "finish"],
        native_model_command=[],
        use_existing_dell_aia=True,
        max_html_chars=70000,
        max_history_actions=40,
        max_tabs=12,
        max_prompt_chars=110000,
        timeout_seconds=5,
        require_agentq_reward_gate=True,
        block_mutation_labels=True,
    )


class MustNotBeCalledAIA:
    def json_decision(self, *_args, **_kwargs):
        raise AssertionError("planner must not be called without a vetted expected_intent")


def test_recovery_without_expected_intent_is_observation_only_even_when_aia_exists():
    bridge = AutoWebGLMRecoveryBridge(_cfg())
    bridge.aia = MustNotBeCalledAIA()
    result = asyncio.run(bridge.propose(page=FakePage(), task="Recover Data Maps without repeating no-progress actions", history=[]))
    assert result["status"] == "observation_only"
    assert result["no_vetted_intent"] is True
    assert result["action_generation_allowed"] is False
    assert result["observation"]["available"] is True
    assert "must not invent a click" in result["reason"]


def test_operational_mission_is_completion_first_not_exhaustive_branch_learning(tmp_path: Path):
    cmd = build_mission_command(
        project_root=tmp_path,
        config="config.yaml",
        input_json="input.json",
        runs_dir="runs",
        golden_screenshot_dir="golden_screenshots/UHAUL-POASN",
        upload_assets_dir="uploads",
    )
    assert "--autonomous-mission" in cmd
    assert "--no-exploration-agent" in cmd
    assert "--observe-current-branch-only" in cmd
    assert "--only-explore-unknown-parent-branches" in cmd
    assert "--bounded-runtime-self-heal" in cmd
    assert cmd[cmd.index("--runtime-self-heal-max-attempts") + 1] == "5"
    assert "--runtime-self-heal-until-complete" not in cmd
    assert "--explore-parent-branches" not in cmd
    assert "--write-heavy-evidence" not in cmd


def test_cli_autonomous_profile_no_longer_forces_unbounded_or_api_capture_gate():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    block = source[source.index("if autonomous_mission:"):source.index("if all_phases_until_complete:")]
    assert "runtime_self_heal_until_complete = True" in block
    assert "all_phases_until_complete = False" in block
    assert "require_api_capture = True" not in block
    assert "write_heavy_evidence = True" not in block
    fusion = source[source.index("if agentq_crawler_fusion:"):source.index("if api_mode == \"write\"")]
    assert "require_api_capture = True" not in fusion


def test_runtime_recovery_context_calls_autowebglm_as_observer_without_intent():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "browser_session.py").read_text(encoding="utf-8")
    start = source.index("async def browser_intelligence_recovery_context")
    end = source.index("return safe_payload", start)
    block = source[start:end]
    assert "if include_autowebglm_proposal and expected_intent" in block
    assert "observation_only_no_vetted_intent" in block
    assert "action_generation_allowed" in block
