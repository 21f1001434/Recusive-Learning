from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.autowebglm_bridge import AutoWebGLMRecoveryBridge
from hip_id_agent.config import load_config
from hip_id_agent.dds_control_driver import select_dds_combobox, set_text_control, select_radio_value
from hip_id_agent.browser_session import BrowserSession


class FakeLocator:
    def __init__(self, awg_id: str = "7"):
        self._id = awg_id
        self.first = self

    async def count(self):
        return 1

    async def get_attribute(self, name: str):
        if name == "data-hip-autowebglm-id":
            return self._id
        return None


class FakePrimaryPage:
    def __init__(self):
        self.url = "https://hip.example/document-types"
        self._context = SimpleNamespace(pages=[self])
        self._loc = FakeLocator("7")

    def context(self):
        return self._context

    async def title(self):
        return "HIP Document Type"

    def locator(self, _selector):
        return self._loc

    async def evaluate(self, _script):
        return {
            "html": '<html><e id="7" role="combobox" label="Derived From"></e></html>',
            "position": {"x": 0, "y": 10, "maxX": 0, "maxY": 1000, "viewportW": 1440, "viewportH": 900},
            "title": "HIP Document Type",
            "url": self.url,
            "elementCount": 1,
        }


def _cfg(**overrides):
    values = dict(
        enabled=True,
        primary_framework=True,
        recovery_only=False,
        deterministic_tool_fallback=True,
        require_intent_alignment=True,
        max_primary_decision_seconds=2,
        allowed_actions=["click", "hover", "select", "type_string", "scroll_page", "go", "jump_to", "switch_tab", "user_input", "finish"],
        native_model_command=[],
        use_existing_dell_aia=False,
        max_html_chars=70000,
        max_history_actions=40,
        max_tabs=12,
        max_prompt_chars=110000,
        timeout_seconds=2,
        require_agentq_reward_gate=True,
        block_mutation_labels=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_default_config_promotes_autowebglm_to_primary_framework():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    assert cfg.autowebglm.enabled is True
    assert cfg.autowebglm.primary_framework is True
    assert cfg.autowebglm.recovery_only is False
    assert cfg.autowebglm.deterministic_tool_fallback is True
    assert cfg.autowebglm.require_intent_alignment is True


def test_primary_framework_status_is_not_recovery_only():
    status = AutoWebGLMRecoveryBridge(_cfg()).status()
    assert status["primary_framework"] is True
    assert status["recovery_only"] is False
    assert status["framework_role"] == "primary_browser_decision_layer"
    assert status["deterministic_drivers_role"] == "verified_tool_adapters"


def test_model_unavailable_still_emits_vetted_intent_in_autowebglm_protocol():
    bridge = AutoWebGLMRecoveryBridge(_cfg())
    result = asyncio.run(
        bridge.primary_decide(
            page=FakePrimaryPage(),
            task="Fill Document Type Derived From",
            action="select",
            selector="#derived-from",
            label="Derived From",
            value="ELEMENT_IN_PAYLOAD",
            history=[],
        )
    )
    assert result["framework"] == "autowebglm_primary"
    assert result["status"] == "deterministic_protocol_fallback"
    assert result["aligned"] is True
    assert result["parsed"]["action"] == "select"
    assert result["parsed"]["args"][0] == "7"
    assert result["parsed"]["args"][1] == "ELEMENT_IN_PAYLOAD"


def test_misaligned_model_action_is_rejected_then_vetted_autowebglm_fallback_is_used(monkeypatch):
    bridge = AutoWebGLMRecoveryBridge(_cfg(use_existing_dell_aia=True))

    async def bad_propose(**_kwargs):
        return {
            "status": "candidate",
            "parsed": {"valid": True, "action": "click", "args": ["99"], "raw": 'click("99")'},
            "command": 'click("99")',
            "safe": True,
        }

    monkeypatch.setattr(bridge, "propose", bad_propose)
    result = asyncio.run(
        bridge.primary_decide(
            page=FakePrimaryPage(),
            task="Select exact DDS option",
            action="select",
            selector="#derived-from",
            label="Derived From",
            value="TRANSACTION_ROOT_ELEMENT",
            history=[],
        )
    )
    assert result["status"] == "deterministic_protocol_fallback"
    assert result["parsed"]["action"] == "select"
    assert result["parsed"]["args"][0] == "7"
    assert result["parsed"]["args"][1] == "TRANSACTION_ROOT_ELEMENT"


def test_sensitive_values_are_not_sent_to_model(monkeypatch):
    bridge = AutoWebGLMRecoveryBridge(_cfg(use_existing_dell_aia=True))

    async def should_not_be_called(**_kwargs):
        raise AssertionError("model must not receive sensitive values")

    monkeypatch.setattr(bridge, "propose", should_not_be_called)
    result = asyncio.run(
        bridge.primary_decide(
            page=FakePrimaryPage(),
            task="Fill password",
            action="fill",
            selector="#password",
            label="Password",
            value="top-secret-value",
            history=[],
        )
    )
    assert result["status"] == "deterministic_protocol_fallback"
    assert result["source"] == "sensitive_value_not_sent_to_model"


def test_shared_browser_and_dds_actions_are_gated_by_autowebglm_primary_layer():
    click_source = inspect.getsource(BrowserSession.click_and_wait)
    fill_source = inspect.getsource(BrowserSession.fill_and_log)
    combo_source = inspect.getsource(select_dds_combobox)
    text_source = inspect.getsource(set_text_control)
    radio_source = inspect.getsource(select_radio_value)
    assert "_autowebglm_primary_decision" in click_source
    assert "_autowebglm_primary_decision" in fill_source
    assert "_autowebglm_primary_gate" in combo_source
    assert "_autowebglm_primary_gate" in text_source
    assert "_autowebglm_primary_gate" in radio_source
    # The model is the policy layer; deterministic/MCP drivers remain downstream tools.
    assert click_source.index("_autowebglm_primary_decision") < click_source.index("playwright_mcp_backend.click")
    assert fill_source.index("_autowebglm_primary_decision") < fill_source.index("playwright_mcp_backend.fill")
