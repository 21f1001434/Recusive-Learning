import asyncio
import json
from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.chrome_devtools_mcp import ChromeDevToolsMCPBackend
from hip_id_agent.config import AppConfig
from hip_id_agent.dummy_fill_e2e import _artifact_actual_state, validate_live_input_contract
from hip_id_agent.section_judge import DualModelSectionJudge, SectionJudgePolicy


class _FakeLocator:
    def __init__(self, details):
        self.details = details
        self.first = self

    async def evaluate(self, _script):
        return self.details


def _judge():
    return DualModelSectionJudge(SectionJudgePolicy(enabled=True, require_text_model=False, require_vision_model=False))


def test_exact_value_judge_rejects_substring_and_requires_exact_row_count():
    judge = _judge()
    expected = {
        "facts": [{"field": "rule", "value": "RULE(1.0)", "aliases": ["rule"], "required": True}],
        "row_counts": {"conditions": 2},
    }
    actual = {
        "controls": [{"label": "Rule", "value": "PREFIX-RULE(1.0)-SUFFIX"}],
        "visible_text": "RULE(1.0)",
        "row_counts": {"conditions": 3},
    }
    result = judge.deterministic_judge(expected=expected, actual_state=actual, attempts=[])
    assert result["pass"] is False
    assert result["missing_values"]
    assert result["row_issues"][0]["actual"] == 3


def test_artifact_state_reads_post_fill_dom_and_latest_field_bound_attempt(tmp_path: Path):
    dom_dir = tmp_path / "dom_snapshots"
    dom_dir.mkdir()
    (dom_dir / "phase_after_dummy_fill_no_save.html").write_text(
        '<html><body><label id="lab">Map Identifier</label>'
        '<input id="map" aria-labelledby="lab" data-hip-locked-value="MAP-1">'
        '</body></html>',
        encoding="utf-8",
    )
    state = _artifact_actual_state(tmp_path, [{"field": "contivo_version", "label": "Contivo version", "selector": "#c", "value_redacted": "6.7", "filled": True}])
    values = {(c["label"], c["value"]) for c in state["controls"]}
    assert ("Map Identifier", "MAP-1") in values
    assert any(c["value"] == "6.7" and c["evidence"] == "latest_successful_post_fill_attempt" for c in state["controls"])


def test_vision_model_never_falls_back_to_text_model(monkeypatch):
    for name in ["HIP_VISION_MODEL", "AIA_VISION_MODEL", "VISION_MODEL_NAME", "VISION_MODEL"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_NAME", "gpt-oss-120b")
    judge = _judge()
    assert judge._vision_model() == ""
    preflight = judge.vision_preflight()
    assert preflight["pass"] is False
    assert "multimodal" in preflight["error"].lower()


def test_chrome_devtools_mcp_is_forced_to_same_cdp_endpoint(tmp_path: Path):
    cfg = AppConfig()
    backend = ChromeDevToolsMCPBackend.from_config(cfg, tmp_path, cdp_endpoint="http://127.0.0.1:9237")
    args = backend.client.args
    assert any("browser-url=http://127.0.0.1:9237" in str(x).lower() for x in args)
    assert any("experimentalpageidrouting" in str(x).lower() for x in args)


def test_input_contract_blocks_golden_value_drift():
    data = {
        "objects": {
            "source_transport_profile": {"profile_usage": "Sender", "deployment_group": "dce-default-sender"},
            "target_transport_profile": {"profile_usage": "Receiver", "deployment_group": "dce-shared-receiver"},
        },
        "_live_acceptance_contract": {
            "objects.source_transport_profile.deployment_group": "dce-shared-sender"
        },
    }
    result = validate_live_input_contract(data)
    assert result["pass"] is False
    assert result["issues"][0]["path"].endswith("deployment_group")


def test_pre_click_guard_blocks_final_create_and_allows_add(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path)
    with pytest.raises(RuntimeError, match="blocked mutating click"):
        asyncio.run(session._assert_safe_click(action="Create", locator=_FakeLocator({"text": "Create", "role": "button", "tag": "button"}), selector="button.create"))
    result = asyncio.run(session._assert_safe_click(action="Add", locator=_FakeLocator({"text": "+ Add", "role": "button", "tag": "button"}), selector="button.add"))
    assert result["text"] == "+ Add"
